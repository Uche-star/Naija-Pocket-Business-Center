"""
Naija Pocket Business Center - payment, saved-document and delivery API.

CANONICAL RULE:
- Document is saved once as the canonical file.
- All downloads return that exact saved file.
- Download never regenerates the document.
"""

from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

import json
import os
import re
import sqlite3
import urllib.parse
import zipfile
import smtplib

from email.message import EmailMessage
from xml.sax.saxutils import escape as xml_escape
from html import escape as html_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel


# ============================================================
# CONFIGURATION
# ============================================================

APP_VERSION = "payment-product-first-v12-canonical-document-attachment"

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = "NPBC-2026"

PUBLIC_API_BASE_URL = os.getenv(
    "PUBLIC_API_BASE_URL",
    ""
).strip()


# ============================================================
# FASTAPI
# ============================================================

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


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def key_part(value: Any) -> str:
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def business_key(service: Any, title: Any) -> str:
    return f"{key_part(service)}::{key_part(title)}"


def first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def as_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


def to_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        return "{}"


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
    name = clean(value)

    if not name:
        name = default

    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" ._")

    if not name:
        name = default

    lower = name.lower()

    if not lower.endswith(".docx") and not lower.endswith(".pdf"):
        name += ".docx"

    return name


def safe_folder(
    value: Any,
    default: str = "document",
) -> str:
    name = clean(value)

    if not name:
        name = default

    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = name.strip(" ._")

    return name or default


# ============================================================
# DOCUMENT PAYLOAD NORMALIZATION
# ============================================================

def normalize_pages(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        parsed = from_json(value, None)

        if parsed is not None:
            value = parsed
        else:
            text = value.strip()

            if not text:
                return []

            return [text]

    if isinstance(value, dict):
        if "pages" in value:
            return normalize_pages(value.get("pages"))

        if "page_text" in value:
            return normalize_pages(value.get("page_text"))

        if "text" in value:
            return [clean(value.get("text"))]

        if "content" in value:
            return [clean(value.get("content"))]

        return [clean(value)]

    if isinstance(value, (tuple, list)):
        pages: list[str] = []

        for item in value:
            if isinstance(item, dict):
                page = first(
                    item.get("text"),
                    item.get("content"),
                    item.get("page_text"),
                )
                if page is not None:
                    pages.append(clean(page))
                else:
                    pages.append(clean(item))
            else:
                pages.append(clean(item))

        return [page for page in pages if page]

    return [clean(value)]


def normalize_payload(value: Any) -> dict:
    if isinstance(value, dict):
        payload = dict(value)
    else:
        payload = {}

    pages = normalize_pages(
        first(
            payload.get("pages"),
            payload.get("document_pages"),
            payload.get("page_text"),
        )
    )

    document_text = clean(
        first(
            payload.get("document_text"),
            payload.get("documentText"),
            payload.get("text"),
            payload.get("content"),
        )
    )

    if not document_text and pages:
        document_text = "\n\n".join(pages)

    filename = safe_filename(
        first(
            payload.get("filename"),
            payload.get("document_filename"),
        ),
        "document.docx",
    )

    document_version = clean(
        payload.get("document_version")
    )

    try:
        page_count = int(
            payload.get("page_count")
            or len(pages)
            or 1
        )
    except Exception:
        page_count = len(pages) or 1

    if page_count < 1 and document_text:
        page_count = 1

    return {
        "pages": pages,
        "document_text": document_text,
        "filename": filename,
        "document_version": document_version,
        "page_count": page_count,
    }


def _format_saved_text(text: str) -> str:
    text = clean(text)

    if not text:
        return ""

    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*---+\s*$", "", text)
    text = re.sub(r"`{1,3}", "", text)

    markers = [
        "Your Complete Document",
        "MAKE PAYMENT",
        "APPLY CORRECTION",
        "CUSTOMER CARE",
        "BACK OFFICE",
        "DOWNLOAD MY DOCUMENT",
        "Previous",
        "Next",
    ]

    for marker in markers:
        text = text.replace(marker, "")

    text = re.sub(r"(?m)^\s*GO\s*$", "", text)
    text = re.sub(r"(?m)^\s*\d+\s+PAGES?\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def clean_saved_document_payload(payload: Any) -> dict:
    normalized = normalize_payload(payload)

    pages = [
        _format_saved_text(page)
        for page in normalized["pages"]
    ]

    pages = [
        page for page in pages
        if page
    ]

    document_text = _format_saved_text(
        normalized["document_text"]
    )

    if not document_text and pages:
        document_text = "\n\n".join(pages)

    page_count = len(pages)

    if page_count < 1 and document_text:
        page_count = 1

    return {
        "pages": pages,
        "document_text": document_text,
        "filename": normalized["filename"],
        "document_version": normalized["document_version"],
        "page_count": page_count,
    }


# ============================================================
# DOCX CREATION
# ============================================================

def _docx_p(text: str) -> str:
    escaped = xml_escape(
        clean(text),
        entities={
            "'": "&apos;",
            '"': "&quot;",
        },
    )

    escaped = escaped.replace("\n", "</w:t><w:br/><w:t>")

    return (
        '<w:p>'
        '<w:r>'
        f'<w:t xml:space="preserve">{escaped}</w:t>'
        '</w:r>'
        '</w:p>'
    )


def make_docx(
    path: Path,
    title: str,
    page_list: list[str],
) -> Path:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not page_list:
        page_list = [""]

    paragraphs: list[str] = []

    for index, page in enumerate(page_list):
        paragraphs.append(_docx_p(page))

        if index < len(page_list) - 1:
            paragraphs.append(
                '<w:p>'
                '<w:r>'
                '<w:br w:type="page"/>'
                '</w:r>'
                '</w:p>'
            )

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
{''.join(paragraphs)}
<w:sectPr>
<w:pgSz w:w="12240" w:h="15840"/>
<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
</w:sectPr>
</w:body>
</w:document>
"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
Target="word/document.xml"/>
</Relationships>
"""

    document_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>
"""

    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:

        archive.writestr(
            "[Content_Types].xml",
            content_types,
        )

        archive.writestr(
            "_rels/.rels",
            relationships,
        )

        archive.writestr(
            "word/document.xml",
            document_xml,
        )

        archive.writestr(
            "word/_rels/document.xml.rels",
            document_relationships,
        )

    return path


# ============================================================
# DATABASE HELPERS
# ============================================================

def ensure_column(
    path: Path,
    table: str,
    column: str,
    definition: str,
) -> None:

    connection = db(path)

    try:
        columns = connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()

        existing = {
            row["name"]
            for row in columns
        }

        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} "
                f"ADD COLUMN {column} {definition}"
            )

            connection.commit()

    finally:
        connection.close()


def init_databases() -> None:

    connection = db(PRODUCT_DB_PATH)

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_key TEXT UNIQUE NOT NULL,
                service TEXT NOT NULL,
                document_title TEXT NOT NULL,
                customer_name TEXT DEFAULT '',
                customer_id TEXT DEFAULT '',
                customer_email TEXT DEFAULT '',
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'NGN',
                document_version TEXT DEFAULT '',
                document_filename TEXT DEFAULT '',
                document_pages INTEGER DEFAULT 0,
                document_payload TEXT DEFAULT '{}',
                document_saved_path TEXT DEFAULT '',
                document_saved_at TEXT DEFAULT '',
                download_unlocked INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                activated_at TEXT DEFAULT '',
                downloaded_at TEXT DEFAULT '',
                download_count INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_key TEXT NOT NULL,
                service TEXT NOT NULL,
                document_title TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                details TEXT DEFAULT ''
            )
            """
        )

        connection.commit()

    finally:
        connection.close()

    ensure_column(
        PRODUCT_DB_PATH,
        "document_products",
        "customer_email",
        "TEXT DEFAULT ''",
    )

    connection = db(PAYMENT_DB_PATH)

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_key TEXT UNIQUE NOT NULL,
                service TEXT NOT NULL,
                document_title TEXT NOT NULL,
                customer_name TEXT DEFAULT '',
                customer_id TEXT DEFAULT '',
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'NGN',
                payment_method TEXT DEFAULT 'bank_transfer',
                payment_status TEXT DEFAULT 'payment_ready',
                document_version TEXT DEFAULT '',
                document_filename TEXT DEFAULT '',
                document_pages INTEGER DEFAULT 0,
                document_text TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reported_at TEXT DEFAULT '',
                verified_at TEXT DEFAULT '',
                rejected_at TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            )
            """
        )

        connection.commit()

    finally:
        connection.close()

    ensure_column(
        PAYMENT_DB_PATH,
        "payment_orders",
        "customer_email",
        "TEXT DEFAULT ''",
    )


@app.on_event("startup")
def startup() -> None:
    init_databases()


# ============================================================
# PRODUCT LOOKUPS
# ============================================================

def get_product(
    service: str,
    title: str,
) -> Optional[dict]:

    key = business_key(service, title)

    connection = db(PRODUCT_DB_PATH)

    try:
        row = connection.execute(
            """
            SELECT *
            FROM document_products
            WHERE business_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


def get_single_product() -> Optional[dict]:

    connection = db(PRODUCT_DB_PATH)

    try:
        row = connection.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


def get_payment(
    service: str,
    title: str,
) -> Optional[dict]:

    key = business_key(service, title)

    connection = db(PAYMENT_DB_PATH)

    try:
        row = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE business_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


# ============================================================
# CANONICAL SAVED DOCUMENT
# ============================================================

def existing_saved_file(
    product: Optional[dict],
) -> Optional[Path]:

    if not product:
        return None

    explicit = clean(
        product.get("document_saved_path")
    )

    if explicit:
        path = Path(explicit)

        if path.exists() and path.is_file():
            return path

    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    if not service or not title:
        return None

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    filename = safe_filename(
        product.get("document_filename"),
        "document.docx",
    )

    candidate = folder / filename

    if candidate.exists() and candidate.is_file():
        return candidate

    if folder.exists():
        files = [
            item
            for item in folder.iterdir()
            if item.is_file()
            and item.suffix.lower() in {".docx", ".pdf"}
        ]

        if files:
            files.sort(
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            return files[0]

    return None


def find_saved_file_from_product(
    product: Optional[dict],
    use_path: bool = True,
) -> Optional[Path]:

    if not product:
        return None

    if use_path:
        path = clean(
            product.get("document_saved_path")
        )

        if path:
            candidate = Path(path)

            if candidate.exists() and candidate.is_file():
                return candidate

    return existing_saved_file(product)


def read_saved_docx(
    path: Path,
) -> dict:

    try:
        with zipfile.ZipFile(path, "r") as archive:
            xml = archive.read(
                "word/document.xml"
            ).decode(
                "utf-8",
                errors="replace",
            )
    except Exception:
        return {
            "pages": [],
            "text": "",
            "page_count": 0,
        }

    paragraphs: list[str] = []
    current: list[str] = []

    tokens = re.findall(
        r"<w:t[^>]*>(.*?)</w:t>|"
        r"<w:tab\s*/>|"
        r"<w:br[^>]*/>|"
        r"<w:br[^>]*>|"
        r"</w:p>",
        xml,
        flags=re.S,
    )

    for match in tokens:
        if match[0]:
            current.append(match[0])
        elif match[1]:
            current.append("\t")
        elif match[2] or match[3]:
            current.append("\n")
        elif match[4]:
            text = "".join(current)
            paragraphs.append(text)
            current = []

    if current:
        paragraphs.append("".join(current))

    text = "\n".join(paragraphs)

    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )

    text = _format_saved_text(text)

    pages = [
        page.strip()
        for page in re.split(
            r"\n\s*\n\s*\n+",
            text,
        )
        if page.strip()
    ]

    if not pages and text:
        pages = [text]

    return {
        "pages": pages,
        "text": text,
        "page_count": len(pages),
    }


def read_actual_saved_document(
    product: Optional[dict],
) -> dict:

    path = find_saved_file_from_product(product)

    if path and path.suffix.lower() == ".docx":
        return read_saved_docx(path)

    if product:
        payload = from_json(
            product.get("document_payload"),
            {},
        )

        normalized = clean_saved_document_payload(
            payload
        )

        return {
            "pages": normalized["pages"],
            "text": normalized["document_text"],
            "page_count": normalized["page_count"],
        }

    return {
        "pages": [],
        "text": "",
        "page_count": 0,
    }


def store_saved_path(
    service: str,
    title: str,
    path: Path,
) -> None:

    key = business_key(service, title)
    timestamp = now_iso()

    connection = db(PRODUCT_DB_PATH)

    try:
        connection.execute(
            """
            UPDATE document_products
            SET document_saved_path = ?,
                document_saved_at = ?,
                updated_at = ?
            WHERE business_key = ?
            """,
            (
                str(path),
                timestamp,
                timestamp,
                key,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def save_exact_snapshot(
    service: str,
    title: str,
    payload: Any,
    existing_product: Optional[dict] = None,
) -> Optional[Path]:

    product = (
        existing_product
        if existing_product is not None
        else get_product(service, title)
    )

    existing = find_saved_file_from_product(
        product
    )

    if existing:
        return existing

    normalized = clean_saved_document_payload(
        payload
    )

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = safe_filename(
        normalized["filename"],
        "document.docx",
    )

    if not filename.lower().endswith(".docx"):
        filename = (
            Path(filename).stem
            + ".docx"
        )

    path = folder / filename

    make_docx(
        path,
        title,
        normalized["pages"]
        or [normalized["document_text"]],
    )

    store_saved_path(
        service,
        title,
        path,
    )

    return path


def repair_saved_snapshot(
    product: Optional[dict],
) -> Optional[Path]:

    if not product:
        return None

    existing = find_saved_file_from_product(
        product
    )

    if existing:
        return existing

    payload = from_json(
        product.get("document_payload"),
        {},
    )

    if not payload:
        payment = get_payment(
            product.get("service", ""),
            product.get("document_title", ""),
        )

        if payment:
            payload = {
                "document_text": payment.get(
                    "document_text",
                    "",
                ),
                "filename": payment.get(
                    "document_filename",
                    "",
                ),
                "document_version": payment.get(
                    "document_version",
                    "",
                ),
                "page_count": payment.get(
                    "document_pages",
                    0,
                ),
            }

    return save_exact_snapshot(
        product.get("service", ""),
        product.get("document_title", ""),
        payload,
        existing_product=product,
    )


def extract_document_text(
    product: Optional[dict],
) -> str:

    actual = read_actual_saved_document(product)

    text = clean(actual.get("text"))

    if text:
        return text

    if product:
        payload = from_json(
            product.get("document_payload"),
            {},
        )

        normalized = clean_saved_document_payload(
            payload
        )

        return normalized["document_text"]

    return ""


# ============================================================
# PRODUCT CREATION / UPDATE
# ============================================================

def upsert_product(data: dict) -> dict:

    service = clean(data.get("service"))
    title = clean(data.get("document_title"))

    if not service or not title:
        raise HTTPException(
            status_code=400,
            detail="service and document_title are required",
        )

    incoming_payload = data.get(
        "document_payload"
    )

    if isinstance(incoming_payload, dict):
        payload_source = dict(incoming_payload)
    else:
        payload_source = dict(data)

    payload = normalize_payload(
        payload_source
    )

    old = get_product(
        service,
        title,
    )

    key = business_key(
        service,
        title,
    )

    timestamp = now_iso()

    try:
        amount = float(
            data.get("amount") or 0
        )
    except Exception:
        amount = 0.0

    customer_name = clean(
        data.get("customer_name")
    )

    customer_id = clean(
        data.get("customer_id")
    )

    customer_email = clean(
        data.get("customer_email")
    )

    currency = (
        clean(data.get("currency"))
        or "NGN"
    )

    if old:
        connection = db(PRODUCT_DB_PATH)

        try:
            connection.execute(
                """
                UPDATE document_products
                SET customer_name = ?,
                    customer_id = ?,
                    customer_email = ?,
                    amount = ?,
                    currency = ?,
                    document_version = ?,
                    document_filename = ?,
                    document_pages = ?,
                    document_payload = ?,
                    updated_at = ?
                WHERE business_key = ?
                """,
                (
                    customer_name,
                    customer_id,
                    customer_email,
                    amount,
                    currency,
                    payload["document_version"],
                    payload["filename"],
                    payload["page_count"],
                    to_json(payload),
                    timestamp,
                    key,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    else:
        connection = db(PRODUCT_DB_PATH)

        try:
            connection.execute(
                """
                INSERT INTO document_products (
                    business_key,
                    service,
                    document_title,
                    customer_name,
                    customer_id,
                    customer_email,
                    amount,
                    currency,
                    document_version,
                    document_filename,
                    document_pages,
                    document_payload,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    service,
                    title,
                    customer_name,
                    customer_id,
                    customer_email,
                    amount,
                    currency,
                    payload["document_version"],
                    payload["filename"],
                    payload["page_count"],
                    to_json(payload),
                    timestamp,
                    timestamp,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=500,
            detail="Unable to save product",
        )

    return product


# ============================================================
# PAYMENT RECORD
# ============================================================

def ensure_payment_record(
    product: dict,
    payment_method: str = "bank_transfer",
) -> dict:

    service = clean(
        product.get("service")
    )

    title = clean(
        product.get("document_title")
    )

    key = business_key(
        service,
        title,
    )

    timestamp = now_iso()

    document_text = extract_document_text(
        product
    )

    payment = get_payment(
        service,
        title,
    )

    if payment:
        connection = db(PAYMENT_DB_PATH)

        try:
            connection.execute(
                """
                UPDATE payment_orders
                SET customer_name = ?,
                    customer_id = ?,
                    amount = ?,
                    currency = ?,
                    payment_method = ?,
                    document_version = ?,
                    document_filename = ?,
                    document_pages = ?,
                    document_text = ?,
                    updated_at = ?
                WHERE business_key = ?
                """,
                (
                    product.get("customer_name", ""),
                    product.get("customer_id", ""),
                    product.get("amount", 0),
                    product.get("currency", "NGN"),
                    payment_method
                    or payment.get(
                        "payment_method",
                        "bank_transfer",
                    ),
                    product.get(
                        "document_version",
                        "",
                    ),
                    product.get(
                        "document_filename",
                        "",
                    ),
                    product.get(
                        "document_pages",
                        0,
                    ),
                    document_text,
                    timestamp,
                    key,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    else:
        connection = db(PAYMENT_DB_PATH)

        try:
            connection.execute(
                """
                INSERT INTO payment_orders (
                    business_key,
                    service,
                    document_title,
                    customer_name,
                    customer_id,
                    amount,
                    currency,
                    payment_method,
                    payment_status,
                    document_version,
                    document_filename,
                    document_pages,
                    document_text,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    service,
                    title,
                    product.get("customer_name", ""),
                    product.get("customer_id", ""),
                    product.get("amount", 0),
                    product.get("currency", "NGN"),
                    payment_method
                    or "bank_transfer",
                    "payment_ready",
                    product.get(
                        "document_version",
                        "",
                    ),
                    product.get(
                        "document_filename",
                        "",
                    ),
                    product.get(
                        "document_pages",
                        0,
                    ),
                    document_text,
                    timestamp,
                    timestamp,
                ),
            )

            connection.commit()

        finally:
            connection.close()

    result = get_payment(
        service,
        title,
    )

    if not result:
        raise HTTPException(
            status_code=500,
            detail="Unable to create payment record",
        )

    return result


def update_payment(
    service: str,
    title: str,
    status: Optional[str] = None,
    **fields: Any,
) -> Optional[dict]:

    key = business_key(
        service,
        title,
    )

    allowed = {
        "reported_at",
        "verified_at",
        "rejected_at",
        "notes",
        "payment_method",
    }

    updates: list[str] = []
    values: list[Any] = []

    if status is not None:
        updates.append("payment_status = ?")
        values.append(status)

    for field, value in fields.items():
        if field in allowed:
            updates.append(
                f"{field} = ?"
            )
            values.append(value)

    updates.append(
        "updated_at = ?"
    )
    values.append(now_iso())

    values.append(key)

    connection = db(PAYMENT_DB_PATH)

    try:
        connection.execute(
            f"""
            UPDATE payment_orders
            SET {", ".join(updates)}
            WHERE business_key = ?
            """,
            values,
        )

        connection.commit()

    finally:
        connection.close()

    return get_payment(
        service,
        title,
    )


# ============================================================
# PUBLIC / BACK OFFICE PRODUCT DATA
# ============================================================

def public_product(
    product: Optional[dict],
) -> Optional[dict]:

    if not product:
        return None

    saved = find_saved_file_from_product(
        product
    )

    return {
        "id": product.get("id"),
        "business_key": product.get(
            "business_key"
        ),
        "service": product.get("service"),
        "document_title": product.get(
            "document_title"
        ),
        "customer_name": product.get(
            "customer_name",
            "",
        ),
        "customer_id": product.get(
            "customer_id",
            "",
        ),
        "amount": product.get(
            "amount",
            0,
        ),
        "currency": product.get(
            "currency",
            "NGN",
        ),
        "document_version": product.get(
            "document_version",
            "",
        ),
        "document_filename": product.get(
            "document_filename",
            "",
        ),
        "document_pages": product.get(
            "document_pages",
            0,
        ),
        "document_saved": bool(saved),
        "document_saved_path": (
            str(saved)
            if saved
            else product.get(
                "document_saved_path",
                "",
            )
        ),
        "download_unlocked": bool(saved),
        "created_at": product.get(
            "created_at",
            "",
        ),
        "updated_at": product.get(
            "updated_at",
            "",
        ),
        "activated_at": product.get(
            "activated_at",
            "",
        ),
        "download_count": product.get(
            "download_count",
            0,
        ),
    }


def back_office_product(
    product: Optional[dict],
) -> Optional[dict]:

    if not product:
        return None

    repair_saved_snapshot(
        product
    )

    actual = read_actual_saved_document(
        product
    )

    result = public_product(
        product
    )

    if result is None:
        return None

    result.update(
        {
            "pages": actual.get(
                "pages",
                [],
            ),
            "text": actual.get(
                "text",
                "",
            ),
            "page_count": actual.get(
                "page_count",
                0,
            ),
            "canonical_path": (
                str(
                    find_saved_file_from_product(
                        product
                    )
                )
                if find_saved_file_from_product(
                    product
                )
                else ""
            ),
        }
    )

    return result


# ============================================================
# CUSTOMER CARE EMAIL
# ============================================================

def _smtp_configured() -> bool:
    return bool(
        os.getenv("SMTP_HOST", "").strip()
        and os.getenv("SMTP_USERNAME", "").strip()
        and os.getenv("SMTP_PASSWORD", "").strip()
    )


def _customer_care_email_html(
    title: str,
    service: str,
    download_url: str,
) -> str:

    safe_title = html_escape(
        title
    )

    safe_service = html_escape(
        service
    )

    safe_url = html_escape(
        download_url,
        quote=True,
    )

    return f"""<!DOCTYPE html>
<html><body>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#050505;padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#111;border:1px solid #d4af37;border-radius:14px;">
<tr><td align="center" style="padding:25px;border-bottom:1px solid #d4af37;">
<b style="color:#f0cf62;font-size:22px;">NAIJA POCKET</b><br>
<span style="color:#aaa;font-size:11px;">BUSINESS CENTER</span>
</td></tr>
<tr><td align="center" style="padding:30px;">
<b style="color:#f0cf62;font-size:18px;">YOUR DOCUMENT IS READY</b><br><br>
<span style="color:#ddd;font-size:14px;">Your requested document is ready for delivery.</span><br><br>
<span style="color:#999;font-size:12px;">{safe_service} — {safe_title}</span><br><br><br>
<a href="{safe_url}" style="background:#d4af37;color:#000;padding:16px 30px;text-decoration:none;font-weight:900;border-radius:7px;display:inline-block;">📱 DOWNLOAD YOUR DOCUMENT</a><br><br><br>
<span style="color:#999;font-size:12px;">Contact Customer Care if you need help.</span>
</td></tr>
<tr><td align="center" style="padding:15px;background:#080808;border-top:1px solid #333;">
<span style="color:#d4af37;font-size:11px;"><b>Fast • Convenient • Open 24/7</b></span>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _send_customer_care_email(
    recipient: str,
    title: str,
    service: str,
    download_url: str,
) -> dict:

    recipient = clean(recipient)

    if not recipient:
        return {
            "sent": False,
            "reason": "customer email is missing",
        }

    if not _smtp_configured():
        return {
            "sent": False,
            "reason": "SMTP is not configured",
        }

    smtp_host = os.getenv(
        "SMTP_HOST",
        "",
    ).strip()

    smtp_port = int(
        os.getenv(
            "SMTP_PORT",
            "465",
        )
    )

    smtp_username = os.getenv(
        "SMTP_USERNAME",
        "",
    ).strip()

    smtp_password = os.getenv(
        "SMTP_PASSWORD",
        "",
    )

    smtp_from = (
        os.getenv(
            "SMTP_FROM",
            "",
        ).strip()
        or smtp_username
    )

    smtp_use_ssl = (
        os.getenv(
            "SMTP_USE_SSL",
            "true",
        ).strip().lower()
        not in {
            "0",
            "false",
            "no",
        }
    )

    html_code = _customer_care_email_html(
        title,
        service,
        download_url,
    )

    msg = EmailMessage()

    msg["Subject"] = (
        "Your Document Is Ready"
    )

    msg["From"] = smtp_from
    msg["To"] = recipient

    # Plain-text fallback.
    msg.set_content(
        f"Your document is ready:\n"
        f"{download_url}\n\n"
        f"Contact Customer Care if you need help."
    )

    # HTML version.
    msg.add_alternative(
        html_code,
        subtype="html",
    )

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                timeout=30,
            ) as server:

                server.login(
                    smtp_username,
                    smtp_password,
                )

                server.send_message(msg)

        else:
            with smtplib.SMTP(
                smtp_host,
                smtp_port,
                timeout=30,
            ) as server:

                server.ehlo()

                server.starttls()

                server.ehlo()

                server.login(
                    smtp_username,
                    smtp_password,
                )

                server.send_message(msg)

        return {
            "sent": True,
            "recipient": recipient,
        }

    except Exception as exc:
        return {
            "sent": False,
            "reason": str(exc),
        }


def send_customer_care_email_for_product(
    product: dict,
    request: Request,
) -> dict:

    recipient = clean(
        product.get("customer_email")
    )

    if not recipient:
        return {
            "sent": False,
            "reason": "customer email is missing",
        }

    saved = find_saved_file_from_product(
        product
    )

    if not saved:
        return {
            "sent": False,
            "reason": "saved document is missing",
        }

    url = download_url(
        request,
        product.get("service", ""),
        product.get("document_title", ""),
    )

    result = _send_customer_care_email(
        recipient,
        product.get(
            "document_title",
            "",
        ),
        product.get(
            "service",
            "",
        ),
        url,
    )

    log_delivery(
        product,
        "email",
        "sent" if result.get("sent") else "failed",
        result,
    )

    return result


# ============================================================
# DELIVERY HELPERS
# ============================================================

def api_base(request: Request) -> str:
    configured = PUBLIC_API_BASE_URL

    if configured:
        return configured.rstrip("/")

    return str(
        request.base_url
    ).rstrip("/")


def download_url(
    request: Request,
    service: str,
    title: str,
) -> str:

    base = api_base(request)

    query = urllib.parse.urlencode(
        {
            "service": service,
            "title": title,
        }
    )

    return (
        f"{base}/api/download?{query}"
    )


def delivery_channels(
    request: Request,
    product: dict,
) -> dict:

    phone = clean(
        product.get("customer_id")
    )

    email = clean(
        product.get("customer_email")
    )

    url = download_url(
        request,
        product.get("service", ""),
        product.get(
            "document_title",
            "",
        ),
    )

    channels = {
        "download": {
            "url": url,
            "available": True,
        },
        "phone": {
            "value": phone,
            "available": bool(phone),
        },
        "whatsapp": {
            "available": bool(phone),
            "value": phone,
        },
        "email": {
            "value": email,
            "available": bool(email),
            "url": (
                f"mailto:{urllib.parse.quote(email)}"
                if email
                else ""
            ),
        },
        "telegram": {
            "available": False,
            "value": "",
        },
        "google_drive": {
            "available": False,
            "value": "",
        },
    }

    return channels


def back_office_delivery_channels(
    request: Request,
    product: dict,
) -> dict:

    url = (
        f"{api_base(request)}"
        f"/api/back-office/delivery-file?"
        + urllib.parse.urlencode(
            {
                "service": product.get(
                    "service",
                    "",
                ),
                "title": product.get(
                    "document_title",
                    "",
                ),
            }
        )
    )

    channels = delivery_channels(
        request,
        product,
    )

    channels["back_office_download"] = {
        "url": url,
        "requires_key": True,
        "available": True,
    }

    return channels


def select_channel(
    data: dict,
    requested: str,
) -> dict:

    requested = clean(
        requested
    ).lower()

    aliases = {
        "download": "download",
        "file": "download",
        "phone": "phone",
        "whatsapp": "whatsapp",
        "email": "email",
        "telegram": "telegram",
        "google": "google_drive",
        "google_drive": "google_drive",
    }

    channel = aliases.get(
        requested,
        requested,
    )

    return data.get(
        channel,
        {
            "available": False,
        },
    )


def log_delivery(
    product: dict,
    channel: str,
    status: str,
    details: Any = None,
) -> None:

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        connection.execute(
            """
            INSERT INTO delivery_events (
                business_key,
                service,
                document_title,
                channel,
                status,
                created_at,
                details
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product.get(
                    "business_key",
                    business_key(
                        product.get(
                            "service",
                            "",
                        ),
                        product.get(
                            "document_title",
                            "",
                        ),
                    ),
                ),
                product.get(
                    "service",
                    "",
                ),
                product.get(
                    "document_title",
                    "",
                ),
                channel,
                status,
                now_iso(),
                to_json(details)
                if not isinstance(
                    details,
                    str,
                )
                else details,
            ),
        )

        connection.commit()

    finally:
        connection.close()


def require_back_office(
    key: str,
) -> None:

    if clean(key) != BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid Back Office key",
        )


# ============================================================
# REQUEST MODELS
# ============================================================

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


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
def payment_create(
    payload: PaymentCreateRequest,
):

    service = clean(
        payload.service
    )

    title = clean(
        payload.document_title
    )

    if not service or not title:
        raise HTTPException(
            status_code=400,
            detail="Service and document title are required",
        )

    if payload.amount < 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid payment amount",
        )

    if payload.document_payload is not None:
        incoming = (
            dict(payload.document_payload)
            if isinstance(
                payload.document_payload,
                dict,
            )
            else {}
        )
    else:
        incoming = {}

    incoming.update(
        {
            "document_version": (
                payload.document_version
            ),
            "pages": (
                payload.pages
            ),
            "document_pages": (
                payload.document_pages
            ),
            "page_text": (
                payload.page_text
            ),
            "document_text": (
                payload.document_text
            ),
            "documentText": (
                payload.documentText
            ),
            "text": payload.text,
            "content": payload.content,
            "filename": payload.filename,
            "document_filename": (
                payload.document_filename
            ),
        }
    )

    normalized = normalize_payload(
        incoming
    )

    if not normalized["document_text"] and not normalized["pages"]:
        raise HTTPException(
            status_code=400,
            detail="Document content is required",
        )

    product = upsert_product(
        {
            "customer_name": (
                payload.customer_name
            ),
            "customer_id": (
                payload.customer_id
            ),
            "customer_email": (
                payload.customer_email
            ),
            "service": service,
            "document_title": title,
            "amount": payload.amount,
            "currency": payload.currency,
            "document_payload": normalized,
        }
    )

    saved = save_exact_snapshot(
        service,
        title,
        normalized,
        existing_product=product,
    )

    product = get_product(
        service,
        title,
    )

    if not saved:
        raise HTTPException(
            status_code=500,
            detail="Unable to save canonical document",
        )

    payment = ensure_payment_record(
        product,
        payload.payment_method,
    )

    return {
        "success": True,
        "message": "Payment record created successfully",
        "product": public_product(product),
        "payment": payment,
        "saved_document": {
            "saved": True,
            "filename": saved.name,
            "path": str(saved),
        },
        "download_unlocked": bool(
            product.get(
                "download_unlocked",
                0,
            )
        ),
        "delivery": delivery_channels(
            Request,
            product,
        )
        if False
        else {
            "available": True,
        },
    }


# ============================================================
# PAYMENT REPORT
# ============================================================

@app.post("/api/payment/report")
def payment_report(
    payload: PaymentReportRequest,
):

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    repair_saved_snapshot(
        product
    )

    payment = get_payment(
        payload.service,
        payload.document_title,
    )

    if not payment:
        payment = ensure_payment_record(
            product
        )

    payment = update_payment(
        payload.service,
        payload.document_title,
        status="payment_reported",
        reported_at=now_iso(),
        notes=payload.note,
    )

    return {
        "success": True,
        "message": "Payment reported successfully",
        "product": public_product(
            get_product(
                payload.service,
                payload.document_title,
            )
        ),
        "payment": payment,
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
def payment_status(
    service: str,
    title: str,
):

    product = get_product(
        service,
        title,
    )

    if not product:
        return {
            "found": False,
            "service": service,
            "document_title": title,
        }

    return {
        "found": True,
        "product": public_product(product),
        "payment": get_payment(
            service,
            title,
        ),
        "delivery": {
            "download_url": None,
            "available": True,
        },
    }


# ============================================================
# PAYMENT COMPLETE
# ============================================================

@app.post("/api/payment/complete")
def payment_complete(
    payload: PaymentCompleteRequest,
):

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    payment = update_payment(
        payload.service,
        payload.document_title,
        status="verified",
        verified_at=now_iso(),
    )

    return {
        "success": True,
        "message": "Payment completed",
        "payment": payment,
    }


# ============================================================
# BACK OFFICE LOGIN
# ============================================================

@app.post("/api/back-office/login")
def back_office_login(
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    return {
        "success": True,
        "authenticated": True,
    }


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
def back_office_payments(
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY id DESC
            """
        ).fetchall()

        products = [
            dict(row)
            for row in rows
        ]

    finally:
        connection.close()

    items = []

    for product in products:
        repaired = repair_saved_snapshot(
            product
        )

        refreshed = get_product(
            product.get(
                "service",
                "",
            ),
            product.get(
                "document_title",
                "",
            ),
        )

        if refreshed:
            product = refreshed

        items.append(
            {
                "product": back_office_product(
                    product
                ),
                "payment": get_payment(
                    product.get(
                        "service",
                        "",
                    ),
                    product.get(
                        "document_title",
                        "",
                    ),
                ),
                "delivery": {
                    "saved": bool(repaired),
                },
            }
        )

    return {
        "success": True,
        "items": items,
        "summary": {
            "total": len(items),
        },
    }


# ============================================================
# BACK OFFICE DOCUMENT INFO
# ============================================================

@app.get("/api/back-office/document-info")
def back_office_document_info(
    service: str,
    title: str,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    result = back_office_product(
        product
    )

    return {
        "success": True,
        "document": result,
    }


# ============================================================
# BACK OFFICE DOCUMENT
# ============================================================

@app.get("/api/back-office/document")
def back_office_document(
    service: str,
    title: str,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    result = back_office_product(
        product
    )

    return {
        "success": True,
        "document": result,
    }


# ============================================================
# BACK OFFICE DOCUMENT CONTENT
# ============================================================

@app.get("/api/back-office/document-content")
def back_office_document_content(
    service: str,
    title: str,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    actual = read_actual_saved_document(
        product
    )

    return {
        "success": True,
        "service": service,
        "document_title": title,
        "pages": actual.get(
            "pages",
            [],
        ),
        "text": actual.get(
            "text",
            "",
        ),
        "page_count": actual.get(
            "page_count",
            0,
        ),
    }


# ============================================================
# BACK OFFICE PAYMENT
# ============================================================

@app.get("/api/back-office/payment")
def back_office_payment(
    service: str,
    title: str,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return {
        "success": True,
        "product": back_office_product(
            product
        ),
        "payment": get_payment(
            service,
            title,
        ),
    }


# ============================================================
# VERIFY PAYMENT
# ============================================================

@app.post("/api/back-office/payment/verify")
def back_office_payment_verify(
    payload: PaymentCompleteRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    payment = update_payment(
        payload.service,
        payload.document_title,
        status="verified",
        verified_at=now_iso(),
    )

    return {
        "success": True,
        "message": "Payment verified",
        "payment": payment,
    }


# ============================================================
# ACTIVATE DOWNLOAD
# ============================================================

def activate_download(
    service: str,
    title: str,
) -> Optional[dict]:

    key = business_key(
        service,
        title,
    )

    timestamp = now_iso()

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        connection.execute(
            """
            UPDATE document_products
            SET download_unlocked = 1,
                activated_at = ?,
                updated_at = ?
            WHERE business_key = ?
            """,
            (
                timestamp,
                timestamp,
                key,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    return get_product(
        service,
        title,
    )


@app.post("/api/back-office/activate-download")
def back_office_activate_download(
    payload: BackOfficeActivateRequest,
    request: Request,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    if not payload.verified:
        raise HTTPException(
            status_code=400,
            detail="Payment must be verified before activation",
        )

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="Saved document not found",
        )

    product = activate_download(
        payload.service,
        payload.document_title,
    )

    email_result = send_customer_care_email_for_product(
        product,
        request,
    )

    return {
        "success": True,
        "message": "Download activated",
        "product": public_product(
            product
        ),
        "download": {
            "available": True,
            "url": download_url(
                request,
                payload.service,
                payload.document_title,
            ),
        },
        "customer_care_email": email_result,
    }


# ============================================================
# CUSTOMER CARE EMAIL
# ============================================================

@app.post("/api/back-office/customer-care-email")
def back_office_customer_care_email(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return send_customer_care_email_for_product(
        product,
        request,
    )


# ============================================================
# REJECT PAYMENT
# ============================================================

@app.post("/api/back-office/payment/reject")
def back_office_payment_reject(
    payload: BackOfficeRejectRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    payment = update_payment(
        payload.service,
        payload.document_title,
        status="rejected",
        rejected_at=now_iso(),
        notes=payload.reason,
    )

    return {
        "success": True,
        "message": "Payment rejected",
        "payment": payment,
    }


# ============================================================
# CANONICAL FILE
# ============================================================

def canonical_file_or_404(
    service: str,
    title: str,
) -> Path:

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    saved = find_saved_file_from_product(
        product
    )

    if not saved:
        saved = repair_saved_snapshot(
            product
        )

    if not saved or not saved.exists():
        raise HTTPException(
            status_code=404,
            detail="Saved document not found",
        )

    return saved


def file_response(
    path: Path,
) -> FileResponse:

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        media_type = "application/pdf"

    elif suffix == ".docx":
        media_type = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    else:
        media_type = (
            "application/octet-stream"
        )

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=path.name,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ============================================================
# CUSTOMER DOWNLOAD
# ============================================================

@app.get("/api/download")
def download_document(
    service: str,
    title: str,
):

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    saved = canonical_file_or_404(
        service,
        title,
    )

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        connection.execute(
            """
            UPDATE document_products
            SET download_count = COALESCE(download_count, 0) + 1,
                downloaded_at = ?,
                updated_at = ?
            WHERE business_key = ?
            """,
            (
                now_iso(),
                business_key(
                    service,
                    title,
                ),
            ),
        )

        connection.commit()

    finally:
        connection.close()

    return file_response(
        saved
    )


# ============================================================
# BACK OFFICE DELIVERY FILE
# ============================================================

@app.get("/api/back-office/delivery-file")
def back_office_delivery_file(
    service: str,
    title: str,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    saved = canonical_file_or_404(
        service,
        title,
    )

    return file_response(
        saved
    )


# ============================================================
# BACK OFFICE DELIVERY CHANNELS
# ============================================================

@app.get("/api/back-office/delivery-channels")
def back_office_delivery_channels_route(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return {
        "success": True,
        "channels": back_office_delivery_channels(
            request,
            product,
        ),
    }


# ============================================================
# BACK OFFICE DELIVERY
# ============================================================

@app.get("/api/back-office/delivery")
def back_office_delivery(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return {
        "success": True,
        "product": public_product(
            product
        ),
        "channels": back_office_delivery_channels(
            request,
            product,
        ),
    }


# ============================================================
# BACK OFFICE DELIVERY PREPARE
# ============================================================

@app.post("/api/back-office/delivery")
def back_office_delivery_post(
    payload: DeliveryRequest,
    request: Request,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    channels = back_office_delivery_channels(
        request,
        product,
    )

    selected = select_channel(
        channels,
        payload.channel,
    )

    if not selected.get(
        "available",
        False,
    ):
        raise HTTPException(
            status_code=400,
            detail="Requested delivery channel is unavailable",
        )

    log_delivery(
        product,
        payload.channel,
        "prepared",
        selected,
    )

    return {
        "success": True,
        "channel": payload.channel,
        "delivery": selected,
    }


# ============================================================
# DELIVERY HISTORY
# ============================================================

@app.get("/api/back-office/delivery-history")
def back_office_delivery_history(
    service: Optional[str] = None,
    title: Optional[str] = None,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    connection = db(
        PRODUCT_DB_PATH
    )

    try:

        if service and title:
            rows = connection.execute(
                """
                SELECT *
                FROM delivery_events
                WHERE business_key = ?
                ORDER BY id DESC
                """,
                (
                    business_key(
                        service,
                        title,
                    ),
                ),
            ).fetchall()

        else:
            rows = connection.execute(
                """
                SELECT *
                FROM delivery_events
                ORDER BY id DESC
                """
            ).fetchall()

        items = [
            dict(row)
            for row in rows
        ]

    finally:
        connection.close()

    return {
        "success": True,
        "items": items,
    }


# ============================================================
# CUSTOMER DELIVERY CHANNELS
# ============================================================

@app.get("/api/delivery/channels")
def delivery_channels_route(
    service: str,
    title: str,
    request: Request,
):

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    return {
        "success": True,
        "channels": delivery_channels(
            request,
            product,
        ),
    }


# ============================================================
# DELIVERY PREPARE
# ============================================================

@app.post("/api/delivery/prepare")
def delivery_prepare(
    payload: DeliveryRequest,
    request: Request,
):

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    channels = delivery_channels(
        request,
        product,
    )

    selected = select_channel(
        channels,
        payload.channel,
    )

    if not selected.get(
        "available",
        False,
    ):
        raise HTTPException(
            status_code=400,
            detail="Requested delivery channel is unavailable",
        )

    log_delivery(
        product,
        payload.channel,
        "prepared",
        selected,
    )

    return {
        "success": True,
        "channel": payload.channel,
        "delivery": selected,
    }


# ============================================================
# PRODUCT STATUS
# ============================================================

@app.get("/api/product/status")
def product_status(
    service: str,
    title: str,
):

    product = get_product(
        service,
        title,
    )

    if not product:
        return {
            "found": False,
            "service": service,
            "document_title": title,
        }

    return {
        "found": True,
        "product": public_product(
            product
        ),
        "payment": get_payment(
            service,
            title,
        ),
    }


# ============================================================
# BACK OFFICE JOBS COMPATIBILITY
# ============================================================

@app.get("/api/back-office/jobs")
def back_office_jobs(
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY id DESC
            """
        ).fetchall()

        items = []

        for row in rows:
            product = dict(row)

            items.append(
                {
                    "product": back_office_product(
                        product
                    ),
                    "payment": get_payment(
                        product.get(
                            "service",
                            "",
                        ),
                        product.get(
                            "document_title",
                            "",
                        ),
                    ),
                }
            )

    finally:
        connection.close()

    return {
        "success": True,
        "items": items,
    }


# ============================================================
# PAYMENT CHANNELS
# ============================================================

@app.get("/api/back-office/payment-channels")
def back_office_payment_channels(
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    return {
        "success": True,
        "channels": [
            {
                "id": "bank_transfer",
                "name": "Bank Transfer",
                "available": True,
            }
        ],
    }


# ============================================================
# CUSTOMER CARE PAYMENT COMPATIBILITY
# ============================================================

@app.get("/api/customer-care/payments")
def customer_care_payments_compatibility(
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    connection = db(
        PAYMENT_DB_PATH
    )

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

        items = [
            dict(row)
            for row in rows
        ]

    finally:
        connection.close()

    return {
        "success": True,
        "items": items,
    }


@app.post("/api/customer-care/payment/verify")
def customer_care_verify_compatibility(
    payload: PaymentCompleteRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        payload.service,
        payload.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    payment = update_payment(
        payload.service,
        payload.document_title,
        status="verified",
        verified_at=now_iso(),
    )

    return {
        "success": True,
        "message": "Payment verified",
        "payment": payment,
    }


# ============================================================
# BACK OFFICE DOCUMENT PREVIEW
# ============================================================

@app.get(
    "/api/back-office/document-preview",
    response_class=HTMLResponse,
)
def back_office_document_preview(
    service: str,
    title: str,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    actual = read_actual_saved_document(
        product
    )

    pages = actual.get(
        "pages",
        [],
    )

    page_blocks = []

    for index, page in enumerate(
        pages,
        start=1,
    ):
        safe_page = html_escape(
            page
        )

        page_blocks.append(
            f"""
            <section style="
                background:#fff;
                color:#111;
                width:min(900px,94vw);
                margin:24px auto;
                padding:50px;
                box-sizing:border-box;
                min-height:900px;
                box-shadow:0 5px 30px rgba(0,0,0,.25);
            ">
                <div style="
                    font-size:12px;
                    color:#777;
                    margin-bottom:25px;
                ">
                    PAGE {index}
                </div>

                <div style="
                    white-space:pre-wrap;
                    font-family:Georgia,serif;
                    line-height:1.65;
                    font-size:16px;
                ">
                    {safe_page}
                </div>
            </section>
            """
        )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport"
              content="width=device-width,initial-scale=1">
        <title>
            {html_escape(title)}
        </title>
    </head>

    <body style="
        margin:0;
        background:#050505;
        color:#fff;
        font-family:Arial,sans-serif;
    ">

    <header style="
        padding:20px;
        text-align:center;
        border-bottom:1px solid #d4af37;
    ">
        <strong style="
            color:#f0cf62;
            font-size:20px;
        ">
            {html_escape(title)}
        </strong>

        <div style="
            color:#999;
            font-size:12px;
            margin-top:6px;
        ">
            {html_escape(service)}
        </div>
    </header>

    {''.join(page_blocks)}

    </body>
    </html>
    """

    return HTMLResponse(
        content=html
    )
