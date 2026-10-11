"""
Naija Pocket Business Center (NPBC)
Payment, Saved-Document, Customer Care, Back Office
and Gmail SMTP Document Delivery API.

Gmail delivery is handled by the separate email_delivery.py module.

Required Render environment variables:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

SMTP:
    smtp.gmail.com
    Port 587
    STARTTLS

Canonical document identity:
    service + document title

Documents are delivered from the existing saved file.
They are never regenerated for email delivery.
"""

from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import urllib.parse

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, EmailStr

from email_delivery import (
    send_document_email,
    configuration_status,
)


APP_VERSION = "payment-product-first-v14-gmail-attachment-delivery"

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = os.getenv(
    "BACK_OFFICE_ADMIN_KEY",
    "NPBC-2026",
)

PUBLIC_API_BASE_URL = os.getenv(
    "PUBLIC_API_BASE_URL",
    "",
).strip()

DOWNLOAD_TOKEN_SECRET = os.getenv(
    "DOWNLOAD_TOKEN_SECRET",
    "NPBC-DIRECT-DOWNLOAD-2026",
).strip()

DOWNLOAD_TOKEN_TTL_SECONDS = int(
    os.getenv("DOWNLOAD_TOKEN_TTL_SECONDS", "3600")
)

app = FastAPI(
    title="Naija Pocket Business Center Payment API",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================================================================
# GENERAL HELPERS
# ================================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def key_part(value: Any) -> str:
    return re.sub(r"\s+", " ", clean(value).casefold())


def business_key(service: Any, title: Any) -> str:
    return f"{key_part(service)}::{key_part(title)}"


def first(*values: Any) -> str:
    for value in values:
        if clean(value):
            return clean(value)
    return ""


def db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def as_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value

    text = clean(value)

    if not text:
        return default

    try:
        return json.loads(text)
    except Exception:
        return default


def safe_filename(
    value: Any,
    default: str = "document.docx",
) -> str:
    name = Path(clean(value) or default).name

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        name,
    )

    name = re.sub(r"\s+", " ", name).strip(" .")

    if not name:
        name = default

    if not name.lower().endswith((".docx", ".pdf")):
        name += ".docx"

    return name


def safe_folder(
    value: Any,
    default: str = "document",
) -> str:
    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        clean(value) or default,
    )

    name = re.sub(r"\s+", " ", name).strip(" .")

    return name[:120] or default


def resolve_stored_path(value: Any) -> Optional[Path]:
    raw = clean(value)

    if not raw:
        return None

    path = Path(raw)

    if not path.is_absolute():
        path = BASE_DIR / path

    try:
        resolved = path.resolve()
        base = BASE_DIR.resolve()

        if resolved == base or base not in resolved.parents:
            return None

        if resolved.is_file():
            return resolved

    except (OSError, ValueError):
        return None

    return None


# ================================================================
# PAYLOAD NORMALIZATION
# ================================================================

def normalize_pages(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()

        if not text:
            return []

        parsed = from_json(text)

        if isinstance(parsed, (list, dict)):
            return normalize_pages(parsed)

        return [text]

    if isinstance(value, dict):
        for key in (
            "pages",
            "page_text",
            "document_pages",
            "content",
            "text",
        ):
            if key in value:
                return normalize_pages(value[key])

        return [json.dumps(value, ensure_ascii=False)]

    if isinstance(value, list):
        result = []

        for item in value:
            if isinstance(item, dict):
                text = first(
                    item.get("text"),
                    item.get("content"),
                    item.get("page_text"),
                )

                if text:
                    result.append(text)

            elif clean(item):
                result.append(clean(item))

        return result

    return [clean(value)] if clean(value) else []


def normalize_payload(value: Any) -> dict:
    payload = (
        value
        if isinstance(value, dict)
        else {"document_text": clean(value)}
    )

    raw_pages = (
        payload.get("pages")
        if payload.get("pages") is not None
        else payload.get("document_pages")
    )

    page_list = normalize_pages(raw_pages)

    text = first(
        payload.get("document_text"),
        payload.get("documentText"),
        payload.get("text"),
        payload.get("content"),
    )

    if not page_list and text:
        page_list = [text]

    if not text and page_list:
        text = "\n\n".join(page_list)

    return {
        "pages": page_list,
        "document_text": text,
        "filename": safe_filename(
            first(
                payload.get("filename"),
                payload.get("document_filename"),
                payload.get("documentFilename"),
            ) or "document.docx"
        ),
        "document_version": first(
            payload.get("document_version"),
            payload.get("documentVersion"),
            payload.get("version"),
        ),
        "page_count": len(page_list),
    }


def _format_saved_text(text: Any) -> list[str]:
    text = str(text or "").replace("**", "")

    markers = [
        "← Previous Page",
        "Next Page →",
        "## Review Your Complete Service",
        "Review Your Complete Service",
        "Review statusREADY",
        "Review status",
        "AMOUNT TO PAY",
        "💳 MAKE PAYMENT",
        "Apply Correction",
        "Payment preparation failed:",
    ]

    positions = [
        text.find(marker)
        for marker in markers
        if text.find(marker) >= 0
    ]

    if positions:
        text = text[:min(positions)]

    result = []

    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()

        if not line:
            continue

        if line in {
            "***",
            "GO",
            "READY",
            "Review status",
            "AMOUNT TO PAY",
            "💳 MAKE PAYMENT",
            "Apply Correction",
        }:
            continue

        if re.fullmatch(r"Page\s+\d+\s+of\s+\d+", line, re.I):
            continue

        result.append(line)

    return result


def clean_saved_document_payload(payload: dict) -> dict:
    payload = dict(payload or {})

    raw_pages = payload.get("pages")

    if not isinstance(raw_pages, list):
        raw_pages = [raw_pages] if raw_pages else []

    pages = []

    for page in raw_pages:
        lines = _format_saved_text(page)

        if lines:
            pages.append("\n".join(lines))

    text = _format_saved_text(
        payload.get("document_text", "")
    )

    document_text = (
        "\n\n".join(pages)
        if pages
        else "\n".join(text)
    )

    payload["pages"] = pages
    payload["document_text"] = document_text
    payload["page_count"] = len(pages) if pages else (
        1 if document_text else 0
    )
    payload["filename"] = safe_filename(
        payload.get("filename") or "document.docx"
    )

    return payload


def _docx_p(text: str) -> str:
    from xml.sax.saxutils import escape

    return (
        '<w:p><w:r><w:t xml:space="preserve">'
        + escape(text)
        + "</w:t></w:r></w:p>"
    )


def make_docx(
    path: Path,
    title: str,
    page_list: list[str],
) -> Path:
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)

    cleaned = clean_saved_document_payload({"pages": page_list})
    pages = cleaned.get("pages", [])

    if not pages:
        raise HTTPException(
            400,
            "SAVED_DOCUMENT_CONTENT_MISSING",
        )

    body = []

    for index, page in enumerate(pages):
        if index:
            body.append(
                '<w:p><w:r><w:br w:type="page"/>'
                '</w:r></w:p>'
            )

        for line in page.splitlines():
            if line.strip():
                body.append(_docx_p(line))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        '<w:body>'
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" '
        'w:bottom="1440" w:left="1440"/>'
        '</w:sectPr></w:body></w:document>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="'
        'application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )

    with zipfile.ZipFile(
        path,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document_xml)

    return path


# ================================================================
# DATABASE INITIALIZATION
# ================================================================

def ensure_column(
    path: Path,
    table: str,
    column: str,
    definition: str,
) -> None:
    connection = db(path)

    try:
        columns = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }

        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )
            connection.commit()

    finally:
        connection.close()


def init_databases() -> None:
    connection = db(PRODUCT_DB_PATH)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS document_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL UNIQUE,
            service TEXT NOT NULL,
            document_title TEXT NOT NULL,
            customer_name TEXT,
            customer_id TEXT,
            amount REAL DEFAULT 0,
            currency TEXT DEFAULT 'NGN',
            document_version TEXT,
            document_filename TEXT,
            document_pages INTEGER DEFAULT 0,
            document_payload TEXT,
            document_saved_path TEXT,
            document_saved_at TEXT,
            download_unlocked INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            activated_at TEXT,
            downloaded_at TEXT,
            download_count INTEGER DEFAULT 0,
            notes TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS delivery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL,
            service TEXT NOT NULL,
            document_title TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            details TEXT
        )
    """)

    connection.commit()
    connection.close()

    connection = db(PAYMENT_DB_PATH)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS payment_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL UNIQUE,
            service TEXT NOT NULL,
            document_title TEXT NOT NULL,
            customer_name TEXT,
            customer_id TEXT,
            amount REAL DEFAULT 0,
            currency TEXT DEFAULT 'NGN',
            payment_method TEXT,
            payment_status TEXT DEFAULT 'payment_ready',
            document_version TEXT,
            document_filename TEXT,
            document_pages INTEGER DEFAULT 0,
            document_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reported_at TEXT,
            verified_at TEXT,
            rejected_at TEXT,
            notes TEXT
        )
    """)

    connection.commit()
    connection.close()

    ensure_column(
        PRODUCT_DB_PATH,
        "document_products",
        "customer_email",
        "TEXT",
    )

    ensure_column(
        PAYMENT_DB_PATH,
        "payment_orders",
        "customer_email",
        "TEXT",
    )


@app.on_event("startup")
def startup():
    init_databases()


def get_product(service: str, title: str) -> Optional[dict]:
    connection = db(PRODUCT_DB_PATH)

    try:
        row = connection.execute(
            "SELECT * FROM document_products WHERE business_key=?",
            (business_key(service, title),),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


def get_single_product() -> Optional[dict]:
    connection = db(PRODUCT_DB_PATH)

    try:
        row = connection.execute(
            "SELECT * FROM document_products "
            "ORDER BY updated_at DESC,id DESC LIMIT 1"
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


def get_payment(service: str, title: str) -> Optional[dict]:
    connection = db(PAYMENT_DB_PATH)

    try:
        row = connection.execute(
            "SELECT * FROM payment_orders WHERE business_key=?",
            (business_key(service, title),),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


# ================================================================
# SAVED DOCUMENT DISCOVERY
# ================================================================

def filesystem_saved_file(
    service: str,
    title: str,
    filename: str = "",
) -> Optional[Path]:
    if not clean(service) or not clean(title):
        return None

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    if not folder.is_dir():
        return None

    requested_filename = clean(filename)

    if requested_filename:
        candidate = folder / safe_filename(requested_filename)

        if (
            candidate.is_file()
            and candidate.suffix.lower() in {".docx", ".pdf"}
            and candidate.resolve().parent == folder.resolve()
        ):
            return candidate

        if candidate.suffix.lower() == ".pdf":
            alternative = folder / (candidate.stem + ".docx")

            if alternative.is_file():
                return alternative

    files = sorted(
        (
            item for item in folder.iterdir()
            if item.is_file()
            and item.suffix.lower() in {".docx", ".pdf"}
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    return files[0] if files else None


def find_saved_file_from_product(
    product: Optional[dict],
    use_path: bool = True,
) -> Optional[Path]:
    if not product:
        return None

    if use_path:
        saved = resolve_stored_path(
            product.get("document_saved_path")
        )

        if saved:
            return saved

    return filesystem_saved_file(
        clean(product.get("service")),
        clean(product.get("document_title")),
        clean(product.get("document_filename")),
    )


def existing_saved_file(
    product: Optional[dict],
) -> Optional[Path]:
    return find_saved_file_from_product(product)


def read_saved_docx(path: Path) -> dict:
    import zipfile
    from html import unescape

    if not path.is_file() or path.suffix.lower() != ".docx":
        return {
            "pages": [],
            "document_text": "",
            "page_count": 0,
        }

    try:
        with zipfile.ZipFile(path, "r") as archive:
            xml = archive.read("word/document.xml").decode(
                "utf-8",
                errors="replace",
            )

    except Exception:
        return {
            "pages": [],
            "document_text": "",
            "page_count": 0,
        }

    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"</w:tr>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)

    text = unescape(xml)

    lines = [
        item.strip()
        for item in text.replace("\r", "").split("\n")
        if item.strip() and item.strip() != "***"
    ]

    text = "\n".join(lines).strip()

    return {
        "pages": [text] if text else [],
        "document_text": text,
        "page_count": 1 if text else 0,
    }


def read_actual_saved_document(product: dict) -> dict:
    saved = find_saved_file_from_product(product)

    if saved and saved.suffix.lower() == ".docx":
        content = read_saved_docx(saved)

        if content["document_text"]:
            return content

    payload = clean_saved_document_payload(
        from_json(product.get("document_payload"), {})
    )

    return {
        "pages": payload["pages"],
        "document_text": payload["document_text"],
        "page_count": payload["page_count"],
    }


def store_saved_path(
    service: str,
    title: str,
    path: Path,
) -> None:
    connection = db(PRODUCT_DB_PATH)
    timestamp = now_iso()

    try:
        connection.execute("""
            UPDATE document_products
            SET document_saved_path=?,
                document_saved_at=COALESCE(document_saved_at,?),
                updated_at=?
            WHERE business_key=?
        """, (
            str(path),
            timestamp,
            timestamp,
            business_key(service, title),
        ))

        connection.commit()

    finally:
        connection.close()


def save_exact_snapshot(
    service: str,
    title: str,
    payload: dict,
    existing_product: Optional[dict] = None,
) -> Path:
    existing = find_saved_file_from_product(existing_product)

    if existing:
        return existing

    normalized = clean_saved_document_payload(payload)

    if not normalized["pages"] and not normalized["document_text"]:
        raise HTTPException(
            400,
            "SAVED_DOCUMENT_CONTENT_MISSING",
        )

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    folder.mkdir(parents=True, exist_ok=True)

    filename = safe_filename(normalized["filename"])

    if filename.lower().endswith(".pdf"):
        filename = Path(filename).stem + ".docx"

    target = folder / filename

    if target.is_file():
        return target

    return make_docx(
        target,
        title,
        normalized["pages"] or [normalized["document_text"]],
    )


def repair_saved_snapshot(product: dict) -> dict:
    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    current = get_product(service, title) or product

    saved = find_saved_file_from_product(current)

    if saved:
        if clean(current.get("document_saved_path")) != str(saved):
            store_saved_path(service, title, saved)
            current = get_product(service, title) or current

        return current

    payload = normalize_payload(
        from_json(current.get("document_payload"), {})
    )

    if not payload["pages"] and not payload["document_text"]:
        payment = get_payment(service, title)

        if payment:
            payload = normalize_payload({
                "document_text": payment.get("document_text"),
                "filename": payment.get("document_filename"),
                "document_version": payment.get("document_version"),
            })

    if not payload["pages"] and not payload["document_text"]:
        raise HTTPException(
            404,
            "SAVED_DOCUMENT_CONTENT_MISSING",
        )

    path = save_exact_snapshot(
        service,
        title,
        payload,
        current,
    )

    store_saved_path(service, title, path)

    return get_product(service, title) or current


def extract_document_text(product: dict) -> str:
    return read_actual_saved_document(product)["document_text"]


# ================================================================
# PRODUCT AND PAYMENT MANAGEMENT
# ================================================================

def upsert_product(data: dict) -> dict:
    service = clean(data.get("service"))
    title = clean(data.get("document_title"))

    if not service or not title:
        raise HTTPException(400, "SERVICE_AND_TITLE_REQUIRED")

    payload = normalize_payload(
        data.get("document_payload", data)
    )

    old = get_product(service, title)
    key = business_key(service, title)
    timestamp = now_iso()

    connection = db(PRODUCT_DB_PATH)

    try:
        if old:
            connection.execute("""
                UPDATE document_products
                SET customer_name=?,
                    customer_id=?,
                    customer_email=?,
                    amount=?,
                    currency=?,
                    document_version=?,
                    document_filename=?,
                    document_pages=?,
                    document_payload=CASE
                        WHEN COALESCE(document_saved_path,'')=''
                        THEN ?
                        ELSE document_payload
                    END,
                    updated_at=?
                WHERE business_key=?
            """, (
                clean(data.get("customer_name")),
                clean(data.get("customer_id")),
                clean(data.get("customer_email")),
                float(data.get("amount") or 0),
                clean(data.get("currency")) or "NGN",
                payload["document_version"],
                payload["filename"],
                payload["page_count"],
                to_json(payload),
                timestamp,
                key,
            ))

        else:
            connection.execute("""
                INSERT INTO document_products (
                    business_key, service, document_title,
                    customer_name, customer_id, customer_email,
                    amount, currency, document_version,
                    document_filename, document_pages,
                    document_payload, created_at, updated_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key,
                service,
                title,
                clean(data.get("customer_name")),
                clean(data.get("customer_id")),
                clean(data.get("customer_email")),
                float(data.get("amount") or 0),
                clean(data.get("currency")) or "NGN",
                payload["document_version"],
                payload["filename"],
                payload["page_count"],
                to_json(payload),
                timestamp,
                timestamp,
            ))

        connection.commit()

    finally:
        connection.close()

    return get_product(service, title) or {}


def ensure_payment_record(product: dict) -> dict:
    service = clean(product.get("service"))
    title = clean(product.get("document_title"))
    key = business_key(service, title)
    timestamp = now_iso()

    values = (
        clean(product.get("customer_name")),
        clean(product.get("customer_id")),
        clean(product.get("customer_email")),
        float(product.get("amount") or 0),
        clean(product.get("currency")) or "NGN",
        clean(product.get("document_version")),
        clean(product.get("document_filename")),
        int(product.get("document_pages") or 0),
        extract_document_text(product),
    )

    connection = db(PAYMENT_DB_PATH)

    try:
        old = connection.execute(
            "SELECT * FROM payment_orders WHERE business_key=?",
            (key,),
        ).fetchone()

        if old:
            connection.execute("""
                UPDATE payment_orders
                SET customer_name=?,
                    customer_id=?,
                    customer_email=?,
                    amount=?,
                    currency=?,
                    document_version=?,
                    document_filename=?,
                    document_pages=?,
                    document_text=?,
                    updated_at=?
                WHERE business_key=?
            """, values + (timestamp, key))

        else:
            connection.execute("""
                INSERT INTO payment_orders (
                    business_key, service, document_title,
                    customer_name, customer_id, customer_email,
                    amount, currency, payment_method, payment_status,
                    document_version, document_filename,
                    document_pages, document_text,
                    created_at, updated_at
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key,
                service,
                title,
                *values[:8],
                "bank_transfer",
                "payment_ready",
                values[5],
                values[6],
                values[7],
                values[8],
                timestamp,
                timestamp,
            ))

        connection.commit()

    finally:
        connection.close()

    return get_payment(service, title) or {}


def update_payment(
    service: str,
    title: str,
    status: Optional[str] = None,
    **fields: Any,
):
    assignments = []
    values = []

    if status:
        assignments.append("payment_status=?")
        values.append(status)

    for name, value in fields.items():
        if name in {
            "reported_at",
            "verified_at",
            "rejected_at",
            "notes",
            "payment_method",
        }:
            assignments.append(f"{name}=?")
            values.append(value)

    assignments.append("updated_at=?")
    values.append(now_iso())
    values.append(business_key(service, title))

    connection = db(PAYMENT_DB_PATH)

    try:
        connection.execute(
            f"UPDATE payment_orders SET {', '.join(assignments)} "
            "WHERE business_key=?",
            values,
        )

        connection.commit()

    finally:
        connection.close()

    return get_payment(service, title)


def public_product(product: Optional[dict]) -> dict:
    if not product:
        return {
            "found": False,
            "saved_document": False,
            "download_unlocked": False,
        }

    saved = find_saved_file_from_product(product)

    payment = get_payment(
        product["service"],
        product["document_title"],
    )

    return {
        "found": True,
        "id": product.get("id"),
        "service": product.get("service"),
        "document_title": product.get("document_title"),
        "customer_name": product.get("customer_name"),
        "customer_id": product.get("customer_id"),
        "customer_email": product.get("customer_email") or "",
        "amount": product.get("amount") or 0,
        "currency": product.get("currency") or "NGN",
        "document_version": product.get("document_version"),
        "document_filename": product.get("document_filename"),
        "document_pages": product.get("document_pages") or 0,
        "document_saved_path": str(saved) if saved else "",
        "document_saved_at": product.get("document_saved_at"),
        "saved_document": bool(saved),
        "download_unlocked": bool(product.get("download_unlocked")),
        "activated_at": product.get("activated_at"),
        "download_count": product.get("download_count") or 0,
        "payment_status": (
            payment.get("payment_status")
            if payment else "payment_ready"
        ),
        "payment_reported": bool(
            payment and payment.get("reported_at")
        ),
        "payment_verified": bool(
            payment and payment.get("verified_at")
        ),
        "payment_rejected": bool(
            payment and payment.get("rejected_at")
        ),
    }


def back_office_product(product: Optional[dict]) -> dict:
    if not product:
        return {
            "found": False,
            "saved_document": False,
            "download_unlocked": False,
            "pages": [],
            "document_text": "",
            "page_count": 0,
            "document_ready_for_back_office": False,
        }

    product = repair_saved_snapshot(product)
    document = read_actual_saved_document(product)
    result = public_product(product)
    saved = find_saved_file_from_product(product)

    result.update({
        "pages": document["pages"],
        "document_text": document["document_text"],
        "page_count": document["page_count"],
        "document_ready_for_back_office": bool(saved),
        "canonical_document_path": str(saved or ""),
        "canonical_document_filename": saved.name if saved else "",
    })

    return result


# ================================================================
# DELIVERY URLS AND CHANNELS
# ================================================================

def api_base(request: Request) -> str:
    return (
        PUBLIC_API_BASE_URL.rstrip("/")
        or str(request.base_url).rstrip("/")
    )


def download_url(
    request: Request,
    service: str,
    title: str,
) -> str:
    return (
        f"{api_base(request)}/api/download?"
        f"service={quote(service)}&title={quote(title)}"
    )


def delivery_channels(request: Request, product: dict) -> dict:
    service = clean(product["service"])
    title = clean(product["document_title"])
    direct = download_url(request, service, title)

    share = (
        f"{title} — {service}\n"
        "Naija Pocket Business Center document download:\n"
        f"{direct}"
    )

    saved = bool(find_saved_file_from_product(product))

    return {
        "available": saved,
        "document_saved": saved,
        "channels": [
            {
                "id": "phone",
                "name": "Download to Phone",
                "type": "download",
                "available": saved,
                "url": direct,
            },
            {
                "id": "whatsapp",
                "name": "WhatsApp",
                "type": "share",
                "available": saved,
                "url": "https://wa.me/?text=" + quote(share),
            },
            {
                "id": "email",
                "name": "Email",
                "type": "share",
                "available": saved,
                "url": (
                    "mailto:?subject="
                    + quote(title + " — Naija Pocket Business Center")
                    + "&body="
                    + quote(share)
                ),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "share",
                "available": saved,
                "url": (
                    "https://t.me/share/url?url="
                    + quote(direct)
                    + "&text="
                    + quote(title)
                ),
            },
            {
                "id": "google_drive",
                "name": "Google Drive",
                "type": "share",
                "available": saved,
                "url": "https://drive.google.com/drive/my-drive",
                "note": (
                    "Download the exact saved file first, "
                    "then upload that same file to Google Drive."
                ),
            },
        ],
    }


def back_office_delivery_channels(
    request: Request,
    product: dict,
) -> dict:
    service = clean(product["service"])
    title = clean(product["document_title"])

    file_url = (
        f"{api_base(request)}/api/back-office/delivery-file?"
        f"service={quote(service)}&title={quote(title)}"
    )

    share = (
        f"{title} — {service}\n"
        "Naija Pocket Business Center document is ready "
        "for Back Office delivery.\n"
        f"Download the saved file: {file_url}"
    )

    saved = bool(find_saved_file_from_product(product))

    return {
        "available": saved,
        "document_saved": saved,
        "customer_download_unlocked": bool(
            product.get("download_unlocked")
        ),
        "channels": [
            {
                "id": "phone",
                "name": "Download to Phone",
                "type": "download",
                "available": saved,
                "url": file_url,
                "requires_back_office_key": True,
            },
            {
                "id": "whatsapp",
                "name": "WhatsApp",
                "type": "share",
                "available": saved,
                "url": "https://wa.me/?text=" + quote(share),
            },
            {
                "id": "email",
                "name": "Email",
                "type": "share",
                "available": saved,
                "url": (
                    "mailto:?subject="
                    + quote(title + " — Naija Pocket Business Center")
                    + "&body="
                    + quote(share)
                ),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "share",
                "available": saved,
                "url": (
                    "https://t.me/share/url?url="
                    + quote(file_url)
                    + "&text="
                    + quote(title)
                ),
            },
            {
                "id": "google_drive",
                "name": "Google Drive",
                "type": "share",
                "available": saved,
                "url": "https://drive.google.com/drive/my-drive",
                "note": (
                    "Download the exact saved file first, "
                    "then upload that same file to Google Drive."
                ),
            },
        ],
    }


def select_channel(data: dict, requested: str):
    aliases = {
        "download": "phone",
        "direct": "phone",
        "phone": "phone",
        "whatsapp": "whatsapp",
        "email": "email",
        "telegram": "telegram",
        "drive": "google_drive",
        "google drive": "google_drive",
        "google_drive": "google_drive",
    }

    wanted = aliases.get(
        clean(requested).casefold(),
        clean(requested).casefold(),
    )

    return next(
        (
            channel for channel in data.get("channels", [])
            if channel["id"] == wanted
        ),
        None,
    )


def log_delivery(
    product: dict,
    channel: str,
    status: str,
    details: Optional[dict] = None,
):
    connection = db(PRODUCT_DB_PATH)

    try:
        connection.execute("""
            INSERT INTO delivery_events (
                business_key, service, document_title,
                channel, status, created_at, details
            )
            VALUES (?,?,?,?,?,?,?)
        """, (
            business_key(
                product["service"],
                product["document_title"],
            ),
            clean(product["service"]),
            clean(product["document_title"]),
            channel,
            status,
            now_iso(),
            to_json(details or {}),
        ))

        connection.commit()

    finally:
        connection.close()


def require_back_office(key: str):
    if clean(key) != BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(401, "INVALID_BACK_OFFICE_KEY")


# ================================================================
# GMAIL SMTP DOCUMENT DELIVERY
# ================================================================

def _smtp_configured() -> bool:
    try:
        status = configuration_status()

        if isinstance(status, dict):
            if "configured" in status:
                return bool(status["configured"])

            if "ok" in status:
                return bool(status["ok"])

            if "ready" in status:
                return bool(status["ready"])

    except Exception:
        pass

    return bool(
        clean(os.getenv("GMAIL_ADDRESS"))
        and clean(os.getenv("GMAIL_APP_PASSWORD"))
    )


def _send_customer_care_email(
    recipient: str,
    title: str,
    service: str,
    document_path: Path,
    customer_name: str = "Valued Customer",
) -> dict:
    if not clean(recipient):
        return {
            "ok": False,
            "message": "CUSTOMER_EMAIL_NOT_PROVIDED",
        }

    if not document_path.is_file():
        return {
            "ok": False,
            "message": "SAVED_DOCUMENT_FILE_MISSING",
        }

    if not _smtp_configured():
        return {
            "ok": False,
            "message": "EMAIL_DELIVERY_NOT_CONFIGURED",
        }

    try:
        result = send_document_email(
            recipient_email=clean(recipient),
            document_path=str(document_path),
            service=clean(service),
            document_title=clean(title),
            customer_name=clean(customer_name) or "Valued Customer",
            subject=(
                "Your document is ready — "
                "Naija Pocket Business Center"
            ),
        )

        if not isinstance(result, dict):
            return {
                "ok": False,
                "message": "INVALID_EMAIL_MODULE_RESPONSE",
            }

        if result.get("ok") is True:
            return result

        return {
            **result,
            "ok": False,
            "message": result.get(
                "message",
                "EMAIL_SEND_FAILED",
            ),
        }

    except Exception as exc:
        return {
            "ok": False,
            "message": "EMAIL_SEND_FAILED",
            "error": str(exc),
        }


def send_customer_care_email_for_product(
    product: dict,
    request: Request = None,
    recipient_email: str = "",
) -> dict:
    recipient = first(
        recipient_email,
        product.get("customer_email"),
    )

    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    if not recipient:
        return {
            "sent": False,
            "ok": False,
            "reason": "CUSTOMER_EMAIL_NOT_PROVIDED",
        }

    saved = find_saved_file_from_product(product)

    if not saved:
        return {
            "sent": False,
            "ok": False,
            "reason": "SAVED_DOCUMENT_FILE_MISSING",
            "recipient": recipient,
        }

    if not _smtp_configured():
        return {
            "sent": False,
            "ok": False,
            "reason": "EMAIL_DELIVERY_NOT_CONFIGURED",
            "recipient": recipient,
        }

    result = _send_customer_care_email(
        recipient=recipient,
        title=title,
        service=service,
        document_path=saved,
        customer_name=clean(product.get("customer_name"))
        or "Valued Customer",
    )

    if result.get("ok"):
        log_delivery(
            product,
            "email",
            "sent",
            {
                "recipient": recipient,
                "filename": saved.name,
                "message_id": result.get("message_id", ""),
                "delivery_type": "saved_document_attachment",
            },
        )

        return {
            "ok": True,
            "sent": True,
            "recipient": recipient,
            "filename": saved.name,
            "message": result.get(
                "message",
                "Document emailed successfully.",
            ),
            "message_id": result.get("message_id", ""),
        }

    log_delivery(
        product,
        "email",
        "failed",
        {
            "recipient": recipient,
            "filename": saved.name,
            "error": result.get("message", "EMAIL_SEND_FAILED"),
        },
    )

    return {
        "ok": False,
        "sent": False,
        "recipient": recipient,
        "reason": result.get("message", "EMAIL_SEND_FAILED"),
        "error": result.get("error", ""),
    }


# ================================================================
# DIRECT DOWNLOAD TOKEN
# ================================================================

def _token_encode(payload: dict) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    encoded = (
        base64.urlsafe_b64encode(raw)
        .decode("ascii")
        .rstrip("=")
    )

    signature = hmac.new(
        DOWNLOAD_TOKEN_SECRET.encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    signature_encoded = (
        base64.urlsafe_b64encode(signature)
        .decode("ascii")
        .rstrip("=")
    )

    return encoded + "." + signature_encoded


def _token_decode(token: str) -> dict:
    parts = clean(token).split(".", 1)

    if len(parts) != 2:
        raise HTTPException(400, "INVALID_DOWNLOAD_TOKEN")

    encoded, supplied_signature = parts

    expected_signature = hmac.new(
        DOWNLOAD_TOKEN_SECRET.encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    expected_encoded = (
        base64.urlsafe_b64encode(expected_signature)
        .decode("ascii")
        .rstrip("=")
    )

    if not hmac.compare_digest(
        supplied_signature,
        expected_encoded,
    ):
        raise HTTPException(401, "INVALID_DOWNLOAD_TOKEN")

    padding = "=" * (-len(encoded) % 4)

    try:
        raw = base64.urlsafe_b64decode(
            (encoded + padding).encode("ascii")
        )

        payload = json.loads(raw.decode("utf-8"))

    except Exception:
        raise HTTPException(400, "INVALID_DOWNLOAD_TOKEN")

    if not isinstance(payload, dict):
        raise HTTPException(400, "INVALID_DOWNLOAD_TOKEN")

    if int(payload.get("exp", 0)) <= int(
        datetime.now(timezone.utc).timestamp()
    ):
        raise HTTPException(410, "DOWNLOAD_TOKEN_EXPIRED")

    if not clean(payload.get("service")):
        raise HTTPException(400, "INVALID_DOWNLOAD_TOKEN")

    if not clean(payload.get("title")):
        raise HTTPException(400, "INVALID_DOWNLOAD_TOKEN")

    return payload


def create_direct_download_token(
    service: str,
    title: str,
    path: Path,
) -> str:
    expires_at = int(
        datetime.now(timezone.utc).timestamp()
    ) + DOWNLOAD_TOKEN_TTL_SECONDS

    return _token_encode({
        "service": clean(service),
        "title": clean(title),
        "filename": path.name,
        "exp": expires_at,
    })


def resolve_direct_token_file(payload: dict) -> Path:
    service = clean(payload.get("service"))
    title = clean(payload.get("title"))
    filename = clean(payload.get("filename"))

    if not service or not title:
        raise HTTPException(400, "INVALID_DOWNLOAD_TOKEN")

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    if not folder.is_dir():
        raise HTTPException(404, "SAVED_DOCUMENT_FILE_MISSING")

    requested = folder / safe_filename(filename)

    if (
        requested.is_file()
        and requested.resolve().parent == folder.resolve()
        and requested.suffix.lower() in {".docx", ".pdf"}
    ):
        return requested

    fallback = filesystem_saved_file(service, title)

    if not fallback:
        raise HTTPException(404, "SAVED_DOCUMENT_FILE_MISSING")

    return fallback


# ================================================================
# REQUEST MODELS
# ================================================================

class PaymentCreateRequest(BaseModel):
    customer_name: str = ""
    customer_id: str = ""
    customer_email: str = ""
    service: str
    document_title: str
    amount: float
    currency: str = "NGN"
    payment_method: str = "bank_transfer"
    document_version: str = ""
    pages: Any = None
    document_pages: Any = None
    page_text: Any = None
    document_text: str = ""
    documentText: str = ""
    text: str = ""
    content: str = ""
    filename: str = ""
    document_filename: str = ""
    document_payload: Any = None


class PaymentReportRequest(BaseModel):
    service: str
    document_title: str
    note: str = ""


class PaymentCompleteRequest(BaseModel):
    service: str
    document_title: str


class BackOfficeActivateRequest(BaseModel):
    service: str
    document_title: str
    verified: bool = True


class BackOfficeRejectRequest(BaseModel):
    service: str
    document_title: str
    reason: str = ""


class DeliveryRequest(BaseModel):
    service: str
    document_title: str
    channel: str


class EmailDeliveryRequest(BaseModel):
    service: str = ""
    document_title: str = ""
    title: str = ""
    recipient_email: str = ""
    customer_email: str = ""
    email: str = ""
    customer_name: str = ""


# ================================================================
# DIRECT DOWNLOAD ENDPOINTS
# ================================================================

@app.get("/api/direct-download-token")
def direct_download_token(
    service: str,
    title: str,
    request: Request,
):
    service = clean(service)
    title = clean(title)

    if not service or not title:
        raise HTTPException(400, "SERVICE_AND_TITLE_REQUIRED")

    saved = filesystem_saved_file(service, title)

    if not saved:
        product = get_product(service, title)

        if product:
            saved = find_saved_file_from_product(product)

    if not saved:
        raise HTTPException(404, "SAVED_DOCUMENT_FILE_MISSING")

    token = create_direct_download_token(service, title, saved)

    direct_url = (
        f"{api_base(request)}/api/direct-download/"
        f"{quote(token, safe='')}"
    )

    expires_at = int(
        datetime.now(timezone.utc).timestamp()
    ) + DOWNLOAD_TOKEN_TTL_SECONDS

    return {
        "ok": True,
        "available": True,
        "filename": saved.name,
        "download_url": direct_url,
        "expires_at": expires_at,
        "database_required": False,
        "canonical_document": True,
    }


@app.get("/api/direct-download/{token}")
def direct_download(token: str):
    payload = _token_decode(token)
    saved = resolve_direct_token_file(payload)
    return file_response(saved)


# ================================================================
# PAYMENT CREATION AND STATUS
# ================================================================

@app.post("/api/payment/create")
def payment_create(
    body: PaymentCreateRequest,
    request: Request,
):
    service = clean(body.service)
    title = clean(body.document_title)

    if not service or not title:
        raise HTTPException(400, "SERVICE_AND_TITLE_REQUIRED")

    if float(body.amount) <= 0:
        raise HTTPException(400, "VALID_PAYMENT_AMOUNT_REQUIRED")

    incoming = from_json(body.document_payload, {})

    if not isinstance(incoming, dict):
        incoming = {}

    if not incoming:
        incoming = {
            "pages": (
                body.pages
                if body.pages is not None
                else body.document_pages
            ),
            "page_text": body.page_text,
            "document_text": first(
                body.document_text,
                body.documentText,
                body.text,
                body.content,
            ),
            "filename": first(
                body.filename,
                body.document_filename,
            ),
            "document_version": body.document_version,
        }

    else:
        incoming.setdefault(
            "pages",
            body.pages if body.pages is not None else body.document_pages,
        )
        incoming.setdefault(
            "document_text",
            first(
                body.document_text,
                body.documentText,
                body.text,
                body.content,
            ),
        )
        incoming.setdefault(
            "filename",
            first(body.filename, body.document_filename),
        )
        incoming.setdefault(
            "document_version",
            body.document_version,
        )

    payload = normalize_payload(incoming)

    if not payload["pages"] and not payload["document_text"]:
        raise HTTPException(400, "DOCUMENT_TEXT_REQUIRED")

    product = upsert_product({
        "service": service,
        "document_title": title,
        "customer_name": body.customer_name,
        "customer_id": body.customer_id,
        "customer_email": body.customer_email,
        "amount": body.amount,
        "currency": body.currency or "NGN",
        "document_version": body.document_version,
        "document_payload": payload,
    })

    saved = find_saved_file_from_product(product)

    if not saved:
        saved = save_exact_snapshot(
            service,
            title,
            payload,
            product,
        )

        store_saved_path(service, title, saved)
        product = get_product(service, title) or product

    payment = ensure_payment_record(product)

    return {
        "ok": True,
        "message": (
            "Payment prepared successfully. "
            "Your exact reviewed document is saved."
        ),
        "product": public_product(product),
        "payment": payment,
        "saved_document": True,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
        "delivery": delivery_channels(request, product),
    }


@app.post("/api/payment/report")
def payment_report(body: PaymentReportRequest):
    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PAYMENT_NOT_PREPARED")

    product = repair_saved_snapshot(product)

    if not get_payment(body.service, body.document_title):
        raise HTTPException(404, "PAYMENT_NOT_PREPARED")

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_reported",
        reported_at=now_iso(),
        notes=body.note,
    )

    return {
        "ok": True,
        "message": "Payment reported. Back Office will verify it.",
        "product": public_product(product),
        "payment": payment,
        "saved_document": True,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


@app.get("/api/payment/status")
def payment_status(
    service: str,
    title: str,
    request: Request,
):
    product = get_product(service, title)

    if not product:
        return {
            "ok": True,
            "found": False,
            "product": public_product(None),
            "payment": None,
        }

    product = repair_saved_snapshot(product)

    return {
        "ok": True,
        "found": True,
        "product": public_product(product),
        "payment": get_payment(service, title),
        "delivery": delivery_channels(request, product),
    }


@app.post("/api/payment/complete")
def payment_complete(body: PaymentCompleteRequest):
    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_verified",
        verified_at=now_iso(),
    )

    return {
        "ok": True,
        "message": "Payment marked verified.",
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


# ================================================================
# BACK OFFICE AUTHENTICATION AND PAYMENT MANAGEMENT
# ================================================================

@app.post("/api/back-office/login")
def back_office_login(
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    return {
        "ok": True,
        "authenticated": True,
        "message": "Back Office access granted.",
    }


@app.get("/api/back-office/payments")
def back_office_payments(
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    connection = db(PRODUCT_DB_PATH)

    try:
        rows = connection.execute(
            "SELECT * FROM document_products "
            "ORDER BY updated_at DESC,id DESC"
        ).fetchall()

    finally:
        connection.close()

    items = []

    for row in rows:
        product = repair_saved_snapshot(dict(row))

        payment = get_payment(
            product["service"],
            product["document_title"],
        )

        item = back_office_product(product)
        item["payment"] = payment
        item["delivery"] = back_office_delivery_channels(
            request,
            product,
        )

        items.append(item)

    return {
        "ok": True,
        "payments": items,
        "records": items,
        "items": items,
        "documents": items,
        "summary": {
            "saved": sum(
                bool(item.get("saved_document"))
                for item in items
            ),
            "total_documents": len(items),
            "reported": sum(
                bool(item.get("payment_reported"))
                for item in items
            ),
            "verified": sum(
                bool(item.get("payment_verified"))
                for item in items
            ),
            "activated": sum(
                bool(item.get("download_unlocked"))
                for item in items
            ),
        },
    }


@app.get("/api/back-office/document-info")
def back_office_document_info(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(service, title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)
    saved = find_saved_file_from_product(product)
    document = read_actual_saved_document(product)

    return {
        "ok": True,
        "service": product["service"],
        "document_title": product["document_title"],
        "filename": saved.name if saved else product.get(
            "document_filename"
        ),
        "pages": document["page_count"],
        "saved_document": bool(saved),
        "saved_path": str(saved) if saved else "",
        "document_text": document["document_text"],
        "document_pages": document["pages"],
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
        "canonical_document_filename": saved.name if saved else "",
    }


@app.get("/api/back-office/document")
def back_office_document(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    return back_office_document_info(
        service,
        title,
        x_back_office_key,
    )


@app.get("/api/back-office/document-content")
def back_office_document_content(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    return back_office_document_info(
        service,
        title,
        x_back_office_key,
    )


@app.get("/api/back-office/payment")
def back_office_payment(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(service, title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    return {
        "ok": True,
        "product": back_office_product(product),
        "payment": get_payment(service, title),
        "delivery": back_office_delivery_channels(request, product),
    }


@app.post("/api/back-office/payment/verify")
def back_office_payment_verify(
    body: PaymentCompleteRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_verified",
        verified_at=now_iso(),
    )

    return {
        "ok": True,
        "message": (
            "Payment verified. Activate download separately when ready."
        ),
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


def activate_download(service: str, title: str) -> dict:
    connection = db(PRODUCT_DB_PATH)
    timestamp = now_iso()

    try:
        connection.execute("""
            UPDATE document_products
            SET download_unlocked=1,
                activated_at=?,
                updated_at=?
            WHERE business_key=?
        """, (
            timestamp,
            timestamp,
            business_key(service, title),
        ))

        connection.commit()

    finally:
        connection.close()

    return get_product(service, title) or {}


@app.post("/api/back-office/activate-download")
def back_office_activate(
    body: BackOfficeActivateRequest,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    if not body.verified:
        raise HTTPException(400, "VERIFICATION_REQUIRED")

    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    if not find_saved_file_from_product(product):
        raise HTTPException(404, "SAVED_DOCUMENT_FILE_MISSING")

    product = activate_download(
        body.service,
        body.document_title,
    )

    email_result = send_customer_care_email_for_product(
        product,
        request,
    )

    return {
        "ok": True,
        "message": "Download activated.",
        "product": public_product(product),
        "download_unlocked": True,
        "customer_care_email": email_result,
    }


@app.post("/api/back-office/customer-care-email")
def back_office_customer_care_email(
    body: PaymentCompleteRequest,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    if not clean(product.get("customer_email")):
        raise HTTPException(400, "CUSTOMER_EMAIL_NOT_PROVIDED")

    result = send_customer_care_email_for_product(product, request)

    if not result.get("sent"):
        raise HTTPException(
            503,
            result.get("reason", "EMAIL_SEND_FAILED"),
        )

    return {
        "ok": True,
        "message": "Customer Care email sent successfully.",
        "customer_care_email": result,
        "product": public_product(product),
    }


@app.post("/api/back-office/payment/reject")
def back_office_reject(
    body: BackOfficeRejectRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_rejected",
        rejected_at=now_iso(),
        notes=body.reason,
    )

    return {
        "ok": True,
        "message": "Payment marked rejected.",
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


# ================================================================
# CUSTOMER EMAIL DELIVERY ENDPOINT
# ================================================================

@app.post("/api/delivery/email")
@app.post("/api/delivery/send-email")
@app.post("/api/email-delivery")
def customer_email_delivery(
    body: EmailDeliveryRequest,
    request: Request,
):
    service = first(body.service)
    title = first(body.document_title, body.title)

    recipient = first(
        body.recipient_email,
        body.customer_email,
        body.email,
    )

    if not service or not title:
        raise HTTPException(
            400,
            "SERVICE_AND_DOCUMENT_TITLE_REQUIRED",
        )

    if not recipient:
        raise HTTPException(
            400,
            "RECIPIENT_EMAIL_REQUIRED",
        )

    product = get_product(service, title)

    if product:
        product = repair_saved_snapshot(product)

        if not clean(product.get("customer_email")):
            connection = db(PRODUCT_DB_PATH)

            try:
                connection.execute("""
                    UPDATE document_products
                    SET customer_email=?, updated_at=?
                    WHERE business_key=?
                """, (
                    recipient,
                    now_iso(),
                    business_key(service, title),
                ))

                connection.commit()

            finally:
                connection.close()

            product = get_product(service, title) or product

        product["customer_email"] = recipient

        if body.customer_name:
            product["customer_name"] = body.customer_name

    else:
        saved = filesystem_saved_file(service, title)

        if not saved:
            raise HTTPException(
                404,
                "SAVED_DOCUMENT_FILE_MISSING",
            )

        product = {
            "service": service,
            "document_title": title,
            "document_filename": saved.name,
            "document_saved_path": str(saved),
            "customer_email": recipient,
            "customer_name": body.customer_name or "Valued Customer",
        }

    saved = find_saved_file_from_product(product)

    if not saved:
        raise HTTPException(
            404,
            "SAVED_DOCUMENT_FILE_MISSING",
        )

    result = send_customer_care_email_for_product(
        product,
        request,
        recipient_email=recipient,
    )

    if not result.get("sent"):
        reason = result.get("reason", "EMAIL_SEND_FAILED")

        status_code = (
            503
            if reason == "EMAIL_DELIVERY_NOT_CONFIGURED"
            or reason == "EMAIL_SEND_FAILED"
            else 404
            if reason == "SAVED_DOCUMENT_FILE_MISSING"
            else 400
        )

        raise HTTPException(
            status_code,
            detail={
                "ok": False,
                "sent": False,
                "message": reason,
                "recipient": recipient,
            },
        )

    return {
        "ok": True,
        "sent": True,
        "message": "The saved document was emailed successfully.",
        "recipient": recipient,
        "service": service,
        "document_title": title,
        "filename": saved.name,
        "delivery_type": "saved_document_attachment",
        "email": result,
    }


@app.get("/api/email-delivery/status")
def email_delivery_status():
    configured = _smtp_configured()

    return {
        "ok": True,
        "configured": configured,
        "provider": "Gmail SMTP",
        "host": "smtp.gmail.com",
        "port": 587,
        "security": "STARTTLS",
    }


# ================================================================
# CUSTOMER DOWNLOAD AND BACK OFFICE FILE DELIVERY
# ================================================================

def canonical_file_or_404(
    service: str,
    title: str,
) -> tuple[dict, Path]:
    product = get_product(service, title)

    if product:
        product = repair_saved_snapshot(product)
        saved = find_saved_file_from_product(product)

        if saved:
            return product, saved

    saved = filesystem_saved_file(service, title)

    if saved:
        return {
            "service": clean(service),
            "document_title": clean(title),
            "document_filename": saved.name,
            "document_saved_path": str(saved),
            "download_unlocked": True,
        }, saved

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    raise HTTPException(404, "SAVED_DOCUMENT_FILE_MISSING")


def file_response(path: Path) -> FileResponse:
    if path.suffix.lower() == ".docx":
        media = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    elif path.suffix.lower() == ".pdf":
        media = "application/pdf"

    else:
        media = "application/octet-stream"

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type=media,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": (
                "no-store, no-cache, must-revalidate, max-age=0"
            ),
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/download")
def download_document(
    service: str,
    title: str,
):
    product, saved = canonical_file_or_404(service, title)

    if product.get("id"):
        try:
            connection = db(PRODUCT_DB_PATH)
            timestamp = now_iso()

            connection.execute("""
                UPDATE document_products
                SET download_count=COALESCE(download_count,0)+1,
                    downloaded_at=?,
                    updated_at=?
                WHERE business_key=?
            """, (
                timestamp,
                timestamp,
                business_key(service, title),
            ))

            connection.commit()
            connection.close()

        except Exception:
            try:
                connection.close()
            except Exception:
                pass

    return file_response(saved)


@app.get("/api/back-office/delivery-file")
def back_office_delivery_file(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    _, saved = canonical_file_or_404(service, title)

    return file_response(saved)


# ================================================================
# BACK OFFICE DELIVERY CHANNELS, HISTORY AND STATUS
# ================================================================

@app.get("/api/back-office/delivery-channels")
def back_office_delivery_channels_endpoint(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(service, title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    return {
        "ok": True,
        "product": back_office_product(product),
        "delivery": back_office_delivery_channels(request, product),
    }


@app.get("/api/back-office/delivery")
def back_office_delivery(
    service: str,
    title: str,
    channel: str,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(service, title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    selected = select_channel(
        back_office_delivery_channels(request, product),
        channel,
    )

    if not selected:
        raise HTTPException(404, "DELIVERY_CHANNEL_NOT_FOUND")

    log_delivery(
        product,
        selected["id"],
        "ready",
        {"url": selected.get("url")},
    )

    return {
        "ok": True,
        "channel": selected,
        "product": back_office_product(product),
    }


@app.post("/api/back-office/delivery")
def back_office_delivery_post(
    body: DeliveryRequest,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    selected = select_channel(
        back_office_delivery_channels(request, product),
        body.channel,
    )

    if not selected:
        raise HTTPException(404, "DELIVERY_CHANNEL_NOT_FOUND")

    log_delivery(
        product,
        selected["id"],
        "ready",
        {"url": selected.get("url")},
    )

    return {
        "ok": True,
        "channel": selected,
        "product": back_office_product(product),
    }


@app.get("/api/back-office/delivery-history")
def delivery_history(
    service: Optional[str] = None,
    title: Optional[str] = None,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    connection = db(PRODUCT_DB_PATH)

    try:
        if service and title:
            rows = connection.execute("""
                SELECT * FROM delivery_events
                WHERE business_key=?
                ORDER BY created_at DESC,id DESC
            """, (
                business_key(service, title),
            )).fetchall()

        else:
            rows = connection.execute("""
                SELECT * FROM delivery_events
                ORDER BY created_at DESC,id DESC
            """).fetchall()

        events = [dict(row) for row in rows]

    finally:
        connection.close()

    return {
        "ok": True,
        "events": events,
    }


@app.get("/api/delivery/channels")
def get_delivery_channels(
    service: str,
    title: str,
    request: Request,
):
    product = get_product(service, title)

    if not product:
        saved = filesystem_saved_file(service, title)

        if not saved:
            raise HTTPException(404, "PRODUCT_NOT_FOUND")

        return {
            "ok": True,
            "product": {
                "found": True,
                "service": service,
                "document_title": title,
                "document_filename": saved.name,
                "saved_document": True,
                "download_unlocked": True,
            },
            "available": True,
            "document_saved": True,
            "channels": [{
                "id": "phone",
                "name": "Download to Phone",
                "type": "download",
                "available": True,
                "url": download_url(request, service, title),
            }],
        }

    product = repair_saved_snapshot(product)

    if not find_saved_file_from_product(product):
        raise HTTPException(404, "SAVED_DOCUMENT_FILE_MISSING")

    return {
        "ok": True,
        "product": public_product(product),
        **delivery_channels(request, product),
    }


@app.post("/api/delivery/prepare")
def prepare_delivery(
    body: DeliveryRequest,
    request: Request,
):
    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    selected = select_channel(
        delivery_channels(request, product),
        body.channel,
    )

    if not selected:
        raise HTTPException(404, "DELIVERY_CHANNEL_NOT_FOUND")

    log_delivery(
        product,
        selected["id"],
        "prepared",
        {"url": selected.get("url")},
    )

    return {
        "ok": True,
        "channel": selected,
        "product": public_product(product),
    }


@app.get("/api/product/status")
def product_status(
    service: str,
    title: str,
    request: Request,
):
    product = get_product(service, title)

    if not product:
        saved = filesystem_saved_file(service, title)

        if saved:
            return {
                "ok": True,
                "found": True,
                "product": {
                    "found": True,
                    "service": service,
                    "document_title": title,
                    "document_filename": saved.name,
                    "saved_document": True,
                    "download_unlocked": True,
                },
                "delivery": {
                    "available": True,
                    "document_saved": True,
                    "channels": [{
                        "id": "phone",
                        "name": "Download to Phone",
                        "type": "download",
                        "available": True,
                        "url": download_url(request, service, title),
                    }],
                },
            }

        return {
            "ok": True,
            "found": False,
            "product": public_product(None),
        }

    product = repair_saved_snapshot(product)

    return {
        "ok": True,
        "found": True,
        "product": public_product(product),
        "delivery": delivery_channels(request, product),
    }


@app.get("/api/back-office/jobs")
def back_office_jobs(
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    connection = db(PRODUCT_DB_PATH)

    try:
        rows = connection.execute("""
            SELECT * FROM document_products
            ORDER BY updated_at DESC,id DESC
        """).fetchall()

        jobs = [dict(row) for row in rows]

    finally:
        connection.close()

    return {
        "ok": True,
        "jobs": jobs,
    }


@app.get("/api/back-office/payment-channels")
def back_office_payment_channels_endpoint(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(service, title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    return {
        "ok": True,
        "service": product["service"],
        "document_title": product["document_title"],
        "payment": get_payment(service, title),
    }


@app.get("/api/customer-care/payments")
def customer_care_payments_compatibility(
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    connection = db(PAYMENT_DB_PATH)

    try:
        rows = connection.execute("""
            SELECT * FROM payment_orders
            ORDER BY updated_at DESC,id DESC
        """).fetchall()

        payments = [dict(row) for row in rows]

    finally:
        connection.close()

    return {
        "ok": True,
        "payments": payments,
    }


@app.post("/api/customer-care/payment/verify")
def customer_care_verify_compatibility(
    body: PaymentCompleteRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(body.service, body.document_title)

    if not product:
        raise HTTPException(404, "PRODUCT_NOT_FOUND")

    product = repair_saved_snapshot(product)

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_verified",
        verified_at=now_iso(),
    )

    return {
        "ok": True,
        "message": (
            "Payment verified. Download still requires activation."
        ),
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


# ================================================================
# OPTIONAL READ-ONLY BACK OFFICE HTML PREVIEW
# ================================================================

@app.get(
    "/api/back-office/document-preview",
    response_class=HTMLResponse,
)
def back_office_document_preview(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    from html import escape

    require_back_office(x_back_office_key)

    _, saved = canonical_file_or_404(service, title)

    if saved.suffix.lower() == ".docx":
        document = read_saved_docx(saved)
        body = document["document_text"]

    else:
        body = (
            "PDF document. Use the Download to Phone button "
            "to open the exact saved PDF."
        )

    escaped_body = escape(body).replace("\n", "<br>")

    return HTMLResponse(
        "<!doctype html>"
        "<html><head>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + escape(title) + "</title>"
        "<style>body{font-family:Arial,sans-serif;"
        "white-space:normal;padding:20px;line-height:1.6}</style>"
        "</head><body>"
        + escaped_body
        + "</body></html>"
    )
