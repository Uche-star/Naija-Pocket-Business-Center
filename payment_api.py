"""Naija Pocket Business Center - complete payment, saved-document and delivery API."""

from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
import json
import os
import re
import base64
import hashlib
import hmac
import sqlite3
import urllib.parse
import zipfile
import smtplib
from email.message import EmailMessage
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


APP_VERSION = "payment-product-first-v12-exact-approved-document-back-office-delivery"

BASE_DIR = Path(__file__).resolve().parent


def _load_local_env() -> None:
    """
    Load simple KEY=VALUE settings from a local .env file.

    Existing system environment variables are never overwritten.
    No external dotenv package is required.
    """
    env_path = BASE_DIR / ".env"

    if not env_path.exists() or not env_path.is_file():
        return

    try:
        for raw_line in env_path.read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()

            if (
                not line
                or line.startswith("#")
                or "=" not in line
            ):
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]

            if not os.getenv(key):
                os.environ[key] = value

    except Exception:
        pass


_load_local_env()

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = "NPBC-2026"
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "").strip()

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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def html_escape(value: Any, quote: bool = False) -> str:
    text = clean(value)
    text = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    if quote:
        text = text.replace('"', "&quot;").replace("'", "&#x27;")

    return text


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

    name = re.sub(r"\s+", " ", name).strip(" .") or default

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

    filename = safe_filename(
        first(
            payload.get("filename"),
            payload.get("document_filename"),
            payload.get("documentFilename"),
        )
        or "document.docx"
    )

    return {
        "pages": page_list,
        "document_text": text,
        "filename": filename,
        "document_version": first(
            payload.get("document_version"),
            payload.get("documentVersion"),
            payload.get("version"),
        ),
        "page_count": len(page_list),
    }


def _customer_display_payload(payload: dict) -> dict:
    """Return the stored document payload without changing its text."""
    return normalize_payload(dict(payload or {}))


def back_office_product(product: Optional[dict]) -> dict:
    """Full Back Office representation, including the complete saved document."""
    result = public_product(product)

    if not product:
        result.update(
            {
                "pages": [],
                "document_text": "",
                "page_count": 0,
            }
        )
        return result

    payload = _customer_display_payload(
        from_json(product.get("document_payload"), {})
    )

    result.update(
        {
            "pages": payload["pages"],
            "document_text": payload["document_text"],
            "page_count": payload["page_count"],
            "document_ready_for_back_office": bool(
                existing_saved_file(product)
            ),
        }
    )

    return result


def _public_delivery_token(service: str, title: str) -> str:
    """Create a signed customer-facing token without exposing the Back Office key."""
    payload = json.dumps(
        {
            "service": clean(service),
            "title": clean(title),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    payload_part = (
        base64.urlsafe_b64encode(payload)
        .decode("ascii")
        .rstrip("=")
    )

    signature = hmac.new(
        BACK_OFFICE_ADMIN_KEY.encode("utf-8"),
        payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()

    signature_part = (
        base64.urlsafe_b64encode(signature)
        .decode("ascii")
        .rstrip("=")
    )

    return payload_part + "." + signature_part


def _read_public_delivery_token(token: str) -> tuple[str, str]:
    try:
        payload_part, signature_part = clean(token).split(".", 1)

        expected = hmac.new(
            BACK_OFFICE_ADMIN_KEY.encode("utf-8"),
            payload_part.encode("ascii"),
            hashlib.sha256,
        ).digest()

        supplied = base64.urlsafe_b64decode(
            signature_part
            + "=" * (-len(signature_part) % 4)
        )

        if not hmac.compare_digest(supplied, expected):
            raise ValueError("bad signature")

        payload = json.loads(
            base64.urlsafe_b64decode(
                payload_part
                + "=" * (-len(payload_part) % 4)
            ).decode("utf-8")
        )

        service = clean(payload.get("service"))
        title = clean(payload.get("title"))

        if not service or not title:
            raise ValueError("missing payload")

        return service, title

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="INVALID_DELIVERY_LINK",
        )


def public_delivery_url(
    request: Request,
    service: str,
    title: str,
) -> str:
    token = _public_delivery_token(service, title)

    return (
        f"{api_base(request)}/api/delivery-file"
        f"?token={urllib.parse.quote(token)}"
    )


def back_office_delivery_channels(
    request: Request,
    product: dict,
) -> dict:
    """
    Back Office delivery choices.

    Customer-facing links never contain the Back Office key.
    """

    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    file_url = public_delivery_url(
        request,
        service,
        title,
    )

    share_text = (
        "NAIJA POCKET BUSINESS CENTER\n\n"
        "Your document is ready.\n\n"
        "Download your document through the secure delivery page."
    )

    email_url = (
        "mailto:?subject="
        + urllib.parse.quote(
            title + " — Naija Pocket Business Center"
        )
        + "&body="
        + urllib.parse.quote(
            "Your document is ready.\n\n"
            "Download the saved document:\n"
            + file_url
        )
    )

    return {
        "available": bool(existing_saved_file(product)),
        "document_saved": bool(existing_saved_file(product)),
        "customer_download_unlocked": bool(
            product.get("download_unlocked")
        ),
        "channels": [
            {
                "id": "phone",
                "name": "Download to Phone",
                "type": "download",
                "available": bool(existing_saved_file(product)),
                "url": file_url,
                "requires_back_office_key": False,
            },
            {
                "id": "whatsapp",
                "name": "WhatsApp",
                "type": "share",
                "available": bool(existing_saved_file(product)),
                "url": (
                    "https://wa.me/?text="
                    + urllib.parse.quote(share_text)
                ),
                "note": (
                    "Customer receives a secure document link "
                    "without the Back Office key."
                ),
            },
            {
                "id": "email",
                "name": "Email",
                "type": "share",
                "available": bool(existing_saved_file(product)),
                "url": email_url,
                "note": (
                    "Official branded email delivery page. "
                    "The signed download URL is hidden inside "
                    "the DOWNLOAD DOCUMENT button."
                ),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "share",
                "available": bool(existing_saved_file(product)),
                "url": (
                    "https://t.me/share/url?url="
                    + urllib.parse.quote(file_url)
                    + "&text="
                    + urllib.parse.quote(
                        "Download your document"
                    )
                ),
                "note": (
                    "Customer receives a secure document link "
                    "without the Back Office key."
                ),
            },
            {
                "id": "google_drive",
                "name": "Google Drive",
                "type": "share",
                "available": bool(existing_saved_file(product)),
                "url": (
                    "https://drive.google.com/drive/my-drive"
                ),
                "note": (
                    "Download the exact saved file first, "
                    "then upload that same file to Google Drive."
                ),
            },
        ],
    }


def init_databases() -> None:
    c = db(PRODUCT_DB_PATH)

    c.execute(
        """
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
        """
    )

    c.execute(
        """
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
        """
    )

    c.commit()
    c.close()

    c = db(PAYMENT_DB_PATH)

    c.execute(
        """
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
        """
    )

    c.commit()
    c.close()


@app.on_event("startup")
def startup() -> None:
    init_databases()


def get_product(
    service: str,
    title: str,
) -> Optional[dict]:
    c = db(PRODUCT_DB_PATH)

    row = c.execute(
        "SELECT * FROM document_products "
        "WHERE business_key = ?",
        (business_key(service, title),),
    ).fetchone()

    c.close()

    return as_dict(row)


def get_single_product() -> Optional[dict]:
    c = db(PRODUCT_DB_PATH)

    row = c.execute(
        "SELECT * FROM document_products "
        "ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()

    c.close()

    return as_dict(row)


def get_product_or_single(
    service: str,
    title: str,
) -> Optional[dict]:
    return get_product(service, title) or get_single_product()


def get_payment(
    service: str,
    title: str,
) -> Optional[dict]:
    c = db(PAYMENT_DB_PATH)

    row = c.execute(
        "SELECT * FROM payment_orders "
        "WHERE business_key = ?",
        (business_key(service, title),),
    ).fetchone()

    c.close()

    return as_dict(row)


def existing_saved_file(
    product: Optional[dict],
) -> Optional[Path]:
    if not product:
        return None

    raw = clean(product.get("document_saved_path"))

    if not raw:
        return None

    path = Path(raw)

    if not path.is_absolute():
        path = BASE_DIR / path

    return path if path.exists() and path.is_file() else None


def clean_saved_document_payload(payload: dict) -> dict:
    normalized = normalize_payload(dict(payload or {}))

    pages = normalized.get("pages") or []
    text = normalized.get("document_text") or ""

    if not pages and text:
        pages = [text]

    if not text and pages:
        text = "\n\n".join(str(x) for x in pages)

    normalized["pages"] = [str(x) for x in pages]
    normalized["document_text"] = str(text)
    normalized["page_count"] = len(normalized["pages"])

    return normalized


def _docx_p(text: str) -> str:
    """Write approved document text literally. No Markdown interpretation."""
    raw = str(text).replace("\r", "")

    return (
        '<w:p><w:r><w:t xml:space="preserve">'
        + xml_escape(raw)
        + "</w:t></w:r></w:p>"
    )


def make_docx(
    path: Path,
    title: str,
    page_list: list[str],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    pages = clean_saved_document_payload(
        {"pages": page_list}
    ).get("pages", [])

    if not pages:
        raise HTTPException(
            status_code=400,
            detail="SAVED_DOCUMENT_CONTENT_MISSING",
        )

    body = []

    for i, page in enumerate(pages):
        if i:
            body.append(
                '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            )

        body.extend(
            _docx_p(line)
            for line in str(page)
            .replace("\r", "")
            .split("\n")
        )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="'
        'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        '">'
        "<w:body>"
        + "".join(body)
        + '<w:sectPr>'
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" '
        'w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="'
        'http://schemas.openxmlformats.org/package/2006/content-types'
        '">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="'
        'http://schemas.openxmlformats.org/package/2006/relationships'
        '">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(
        path,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as z:
        z.writestr(
            "[Content_Types].xml",
            content_types,
        )
        z.writestr(
            "_rels/.rels",
            root_rels,
        )
        z.writestr(
            "word/document.xml",
            document_xml,
        )

    return path


def save_exact_snapshot(
    service: str,
    title: str,
    payload: dict,
    existing_product: Optional[dict] = None,
) -> Path:
    existing = existing_saved_file(existing_product)

    if existing:
        return existing

    normalized = clean_saved_document_payload(payload)

    if not normalized["pages"] and not normalized["document_text"]:
        raise HTTPException(
            status_code=400,
            detail="SAVED_DOCUMENT_CONTENT_MISSING",
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

    filename = safe_filename(normalized["filename"])

    if filename.lower().endswith(".pdf"):
        filename = Path(filename).stem + ".docx"

    target = folder / filename

    if target.exists() and target.is_file():
        return target

    return make_docx(
        target,
        title,
        normalized["pages"]
        or [normalized["document_text"]],
    )


def store_saved_path(
    service: str,
    title: str,
    path: Path,
) -> None:
    c = db(PRODUCT_DB_PATH)

    c.execute(
        """
        UPDATE document_products
        SET document_saved_path = ?,
            document_saved_at = COALESCE(document_saved_at, ?),
            updated_at = ?
        WHERE business_key = ?
        """,
        (
            str(path),
            now_iso(),
            now_iso(),
            business_key(service, title),
        ),
    )

    c.commit()
    c.close()


def extract_document_text(product: dict) -> str:
    payload = clean_saved_document_payload(
        from_json(product.get("document_payload"), {})
    )

    return payload["document_text"]


def upsert_product(data: dict) -> dict:
    service = clean(data.get("service"))
    title = clean(data.get("document_title"))

    if not service or not title:
        raise HTTPException(
            status_code=400,
            detail="SERVICE_AND_TITLE_REQUIRED",
        )

    raw_payload = data.get("document_payload")

    payload = normalize_payload(
        raw_payload if raw_payload is not None else data
    )

    old = get_product(service, title)
    key = business_key(service, title)
    timestamp = now_iso()

    c = db(PRODUCT_DB_PATH)

    if old:
        c.execute(
            """
            UPDATE document_products SET
                customer_name=?,
                customer_id=?,
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
            """,
            (
                clean(data.get("customer_name")),
                clean(data.get("customer_id")),
                float(data.get("amount") or 0),
                clean(data.get("currency")) or "NGN",
                payload["document_version"],
                payload["filename"],
                payload["page_count"],
                to_json(payload),
                timestamp,
                key,
            ),
        )

    else:
        c.execute(
            """
            INSERT INTO document_products
            (
                business_key,
                service,
                document_title,
                customer_name,
                customer_id,
                amount,
                currency,
                document_version,
                document_filename,
                document_pages,
                document_payload,
                created_at,
                updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                service,
                title,
                clean(data.get("customer_name")),
                clean(data.get("customer_id")),
                float(data.get("amount") or 0),
                clean(data.get("currency")) or "NGN",
                payload["document_version"],
                payload["filename"],
                payload["page_count"],
                to_json(payload),
                timestamp,
                timestamp,
            ),
        )

    c.commit()
    c.close()

    return get_product(service, title) or {}


def ensure_payment_record(product: dict) -> dict:
    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    key = business_key(service, title)
    old = get_payment(service, title)
    timestamp = now_iso()

    values = (
        clean(product.get("customer_name")),
        clean(product.get("customer_id")),
        float(product.get("amount") or 0),
        clean(product.get("currency")) or "NGN",
        clean(product.get("document_version")),
        clean(product.get("document_filename")),
        int(product.get("document_pages") or 0),
        extract_document_text(product),
    )

    c = db(PAYMENT_DB_PATH)

    if old:
        c.execute(
            """
            UPDATE payment_orders
            SET customer_name=?,
                customer_id=?,
                amount=?,
                currency=?,
                document_version=?,
                document_filename=?,
                document_pages=?,
                document_text=?,
                updated_at=?
            WHERE business_key=?
            """,
            values + (timestamp, key),
        )

    else:
        c.execute(
            """
            INSERT INTO payment_orders
            (
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
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                service,
                title,
                *values,
                "bank_transfer",
                "payment_ready",
                timestamp,
                timestamp,
            ),
        )

    c.commit()
    c.close()

    return get_payment(service, title) or {}


def update_payment(
    service: str,
    title: str,
    status: Optional[str] = None,
    **fields: Any,
) -> Optional[dict]:
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

    c = db(PAYMENT_DB_PATH)

    c.execute(
        f"""
        UPDATE payment_orders
        SET {", ".join(assignments)}
        WHERE business_key=?
        """,
        values,
    )

    c.commit()
    c.close()

    return get_payment(service, title)


def repair_saved_snapshot(product: dict) -> dict:
    current = (
        get_product(
            clean(product.get("service")),
            clean(product.get("document_title")),
        )
        or product
    )

    payload = normalize_payload(
        from_json(current.get("document_payload"), {})
    )

    if not payload["pages"] and not payload["document_text"]:
        payment = get_payment(
            clean(current.get("service")),
            clean(current.get("document_title")),
        )

        if payment:
            payload = normalize_payload(
                {
                    "document_text": payment.get("document_text"),
                    "filename": payment.get("document_filename"),
                    "document_version": payment.get(
                        "document_version"
                    ),
                }
            )

    if not payload["pages"] and not payload["document_text"]:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_CONTENT_MISSING",
        )

    existing = existing_saved_file(current)

    if existing:
        raw_text = "\n".join(
            payload.get("pages")
            or [payload.get("document_text", "")]
        )

        if (
            "**" in raw_text
            or re.search(r"(?<!\*)\*(?!\*)", raw_text)
        ):
            make_docx(
                existing,
                clean(current.get("document_title")),
                payload["pages"]
                or [payload["document_text"]],
            )

        return current

    path = save_exact_snapshot(
        clean(current.get("service")),
        clean(current.get("document_title")),
        payload,
        current,
    )

    store_saved_path(
        clean(current.get("service")),
        clean(current.get("document_title")),
        path,
    )

    return (
        get_product(
            clean(current.get("service")),
            clean(current.get("document_title")),
        )
        or current
    )


def activate_download(
    service: str,
    title: str,
) -> dict:
    c = db(PRODUCT_DB_PATH)

    c.execute(
        """
        UPDATE document_products
        SET download_unlocked=1,
            activated_at=?,
            updated_at=?
        WHERE business_key=?
        """,
        (
            now_iso(),
            now_iso(),
            business_key(service, title),
        ),
    )

    c.commit()
    c.close()

    return get_product(service, title) or {}


def public_product(product: Optional[dict]) -> dict:
    if not product:
        return {
            "found": False,
            "saved_document": False,
            "download_unlocked": False,
        }

    saved = existing_saved_file(product)

    payment = get_payment(
        clean(product.get("service")),
        clean(product.get("document_title")),
    )

    payload = clean_saved_document_payload(
        from_json(product.get("document_payload"), {})
    )

    return {
        "found": True,
        "id": product.get("id"),
        "service": product.get("service"),
        "document_title": product.get("document_title"),
        "customer_name": product.get("customer_name"),
        "customer_id": product.get("customer_id"),
        "amount": product.get("amount") or 0,
        "currency": product.get("currency") or "NGN",
        "document_version": product.get("document_version"),
        "document_filename": product.get("document_filename"),
        "document_pages": product.get("document_pages") or 0,
        "document_preview_pages": payload.get("pages", []),
        "document_preview_text": payload.get("document_text", ""),
        "document_saved_path": (
            str(saved)
            if saved
            else clean(product.get("document_saved_path"))
        ),
        "document_saved_at": product.get("document_saved_at"),
        "saved_document": bool(saved),
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
        "activated_at": product.get("activated_at"),
        "download_count": product.get("download_count") or 0,
        "payment_status": (
            payment.get("payment_status")
            if payment
            else "payment_ready"
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
        f"{api_base(request)}/api/download?service="
        f"{urllib.parse.quote(service)}"
        f"&title={urllib.parse.quote(title)}"
    )


def delivery_channels(
    request: Request,
    product: dict,
) -> dict:
    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    public_url = public_delivery_url(
        request,
        service,
        title,
    )

    email_url = (
        "mailto:?subject="
        + urllib.parse.quote(
            title + " — Naija Pocket Business Center"
        )
        + "&body="
        + urllib.parse.quote(
            "Your document is ready.\n\n"
            "Download the saved document:\n"
            + public_url
        )
    )

    share_text = (
        "NAIJA POCKET BUSINESS CENTER\n\n"
        "Your document is ready.\n\n"
        "Download your document:\n"
        + public_url
    )

    unlocked = bool(product.get("download_unlocked"))
    saved = bool(existing_saved_file(product))

    return {
        "available": unlocked,
        "document_saved": saved,
        "channels": [
            {
                "id": "phone",
                "name": "Download to Phone",
                "type": "download",
                "available": unlocked,
                "url": public_url,
                "requires_back_office_key": False,
            },
            {
                "id": "whatsapp",
                "name": "WhatsApp",
                "type": "share",
                "available": unlocked,
                "url": (
                    "https://wa.me/?text="
                    + urllib.parse.quote(share_text)
                ),
                "requires_back_office_key": False,
            },
            {
                "id": "email",
                "name": "Email",
                "type": "share",
                "available": unlocked,
                "url": email_url,
                "requires_back_office_key": False,
                "note": (
                    "Official branded email delivery page. "
                    "No raw URL is shown to the customer."
                ),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "share",
                "available": unlocked,
                "url": (
                    "https://t.me/share/url?url="
                    + urllib.parse.quote(public_url)
                    + "&text="
                    + urllib.parse.quote(
                        "NAIJA POCKET BUSINESS CENTER\n\n"
                        "Your document is ready.\n\n"
                        "Download your document."
                    )
                ),
                "requires_back_office_key": False,
            },
            {
                "id": "google_drive",
                "name": "Google Drive",
                "type": "share",
                "available": unlocked,
                "url": (
                    "https://drive.google.com/drive/my-drive"
                ),
                "requires_back_office_key": False,
                "note": (
                    "Download the exact saved file first, "
                    "then upload that same file to Google Drive."
                ),
            },
        ],
    }


def select_channel(
    data: dict,
    requested: str,
) -> Optional[dict]:
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
            item
            for item in data.get("channels", [])
            if item["id"] == wanted
        ),
        None,
    )


def log_delivery(
    product: dict,
    channel: str,
    status: str,
    details: Optional[dict] = None,
) -> None:
    c = db(PRODUCT_DB_PATH)

    c.execute(
        """
        INSERT INTO delivery_events
        (
            business_key,
            service,
            document_title,
            channel,
            status,
            created_at,
            details
        )
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            business_key(
                product.get("service"),
                product.get("document_title"),
            ),
            clean(product.get("service")),
            clean(product.get("document_title")),
            channel,
            status,
            now_iso(),
            to_json(details or {}),
        ),
    )

    c.commit()
    c.close()


def require_back_office(key: str) -> None:
    if clean(key) != BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(
            status_code=401,
            detail="INVALID_BACK_OFFICE_KEY",
        )


def _file_download_headers(filename: str) -> dict[str, str]:
    """
    Headers used when returning the exact saved document.

    The filename is taken from the actual saved file and is never
    replaced with the service name or another generated title.
    """
    safe_name = safe_filename(filename)

    encoded_name = urllib.parse.quote(
        safe_name,
        safe="",
    )

    header_name = safe_name.replace(
        '"',
        "_",
    )

    return {
        "Content-Disposition": (
            f'attachment; filename="{header_name}"; '
            f"filename*=UTF-8''{encoded_name}"
        ),
        "Cache-Control": (
            "no-store, no-cache, must-revalidate, max-age=0"
        ),
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Content-Type-Options": "nosniff",
    }


def _saved_file_response(saved: Path) -> FileResponse:
    """
    Return the exact existing saved file as a browser download.

    No document is regenerated here.
    No title is changed here.
    """

    suffix = saved.suffix.casefold()

    if suffix == ".docx":
        media = (
            "application/"
            "vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    elif suffix == ".pdf":
        media = "application/pdf"

    else:
        media = "application/octet-stream"

    return FileResponse(
        str(saved),
        filename=safe_filename(saved.name),
        media_type=media,
        headers=_file_download_headers(saved.name),
    )


class PaymentCreateRequest(BaseModel):
    customer_name: str = ""
    customer_id: str = ""
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
    recipient_email: str = ""


@app.post("/api/payment/create")
def payment_create(
    body: PaymentCreateRequest,
    request: Request,
):
    """
    MAKE PAYMENT action only:
    save document + prepare payment.

    This endpoint is deliberately separate from /api/payment/report.
    I HAVE MADE PAYMENT must never call this operation.
    """

    service = clean(body.service)
    title = clean(body.document_title)

    if not service or not title:
        raise HTTPException(
            status_code=400,
            detail="SERVICE_AND_TITLE_REQUIRED",
        )

    if float(body.amount) <= 0:
        raise HTTPException(
            status_code=400,
            detail="VALID_PAYMENT_AMOUNT_REQUIRED",
        )

    existing_product = get_product(
        service,
        title,
    )

    existing_payload = (
        normalize_payload(
            from_json(
                existing_product.get(
                    "document_payload"
                ),
                {},
            )
        )
        if existing_product
        else {}
    )

    incoming = from_json(
        body.document_payload,
        {},
    )

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
            body.pages
            if body.pages is not None
            else body.document_pages,
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
            first(
                body.filename,
                body.document_filename,
            ),
        )

        incoming.setdefault(
            "document_version",
            body.document_version,
        )

    payload = normalize_payload(incoming)

    if (
        not payload["pages"]
        and not payload["document_text"]
        and existing_product
    ):
        payload = existing_payload

    if (
        not payload["pages"]
        and not payload["document_text"]
    ):
        payment_existing = get_payment(
            service,
            title,
        )

        if payment_existing:
            payload = normalize_payload(
                {
                    "document_text": payment_existing.get(
                        "document_text"
                    ),
                    "filename": payment_existing.get(
                        "document_filename"
                    ),
                    "document_version": payment_existing.get(
                        "document_version"
                    ),
                }
            )

    if (
        not payload["pages"]
        and not payload["document_text"]
    ):
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_TEXT_REQUIRED",
        )

    product = upsert_product(
        {
            "service": service,
            "document_title": title,
            "customer_name": body.customer_name,
            "customer_id": body.customer_id,
            "amount": body.amount,
            "currency": body.currency or "NGN",
            "document_version": body.document_version,
            "document_payload": payload,
        }
    )

    saved = existing_saved_file(product)

    if not saved:
        saved = save_exact_snapshot(
            service,
            title,
            payload,
            product,
        )

        store_saved_path(
            service,
            title,
            saved,
        )

        product = get_product(
            service,
            title,
        ) or product

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
        "delivery": delivery_channels(
            request,
            product,
        ),
    }


@app.post("/api/payment/report")
def payment_report(
    body: PaymentReportRequest,
):
    """
    I HAVE MADE PAYMENT action only:
    report an already-prepared payment.

    It does NOT create payment preparation.
    It does NOT replace MAKE PAYMENT.
    """

    service = clean(body.service)
    title = clean(body.document_title)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_PREPARED",
        )

    product = repair_saved_snapshot(product)

    payment = get_payment(
        service,
        title,
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_PREPARED",
        )

    payment = update_payment(
        service,
        title,
        "payment_reported",
        reported_at=now_iso(),
        notes=body.note,
    )

    return {
        "ok": True,
        "message": (
            "Payment reported. "
            "Customer Care will verify it."
        ),
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
    product = get_product(
        service,
        title,
    )

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
        "delivery": delivery_channels(
            request,
            product,
        ),
    }


@app.post("/api/payment/complete")
def payment_complete(
    body: PaymentCompleteRequest,
):
    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

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


@app.get("/api/customer-care/payments")
def customer_care_payments(
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    c = db(PAYMENT_DB_PATH)

    rows = c.execute(
        "SELECT * FROM payment_orders "
        "ORDER BY updated_at DESC,id DESC"
    ).fetchall()

    c.close()

    return {
        "ok": True,
        "payments": [dict(row) for row in rows],
    }


@app.post("/api/customer-care/payment/verify")
def customer_care_verify(
    body: PaymentCompleteRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

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
            "Payment verified. "
            "Download still requires activation."
        ),
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


@app.post("/api/back-office/login")
def back_office_login(
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    return {
        "ok": True,
        "authenticated": True,
        "message": "Customer Care Back Office access granted.",
    }


def back_office_payment_channels(
    product: dict,
    payment: Optional[dict],
) -> list[dict]:
    method = (
        clean(
            (payment or {}).get("payment_method")
            or "bank_transfer"
        )
        or "bank_transfer"
    )

    return [
        {
            "id": "bank_transfer",
            "name": "Bank Transfer",
            "type": "payment",
            "available": True,
            "selected": method == "bank_transfer",
        },
        {
            "id": "cash_manual",
            "name": "Cash / Manual Payment",
            "type": "payment",
            "available": True,
            "selected": method
            in {"cash", "manual", "cash_manual"},
        },
        {
            "id": "recorded_method",
            "name": "Recorded Payment Method",
            "type": "payment",
            "available": True,
            "selected": True,
            "value": method,
        },
    ]


@app.get("/api/back-office/payments")
def back_office_payments(
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    c = db(PRODUCT_DB_PATH)

    rows = c.execute(
        "SELECT * FROM document_products "
        "ORDER BY updated_at DESC,id DESC"
    ).fetchall()

    c.close()

    items = []

    for row in rows:
        product = repair_saved_snapshot(dict(row))

        payment = get_payment(
            product.get("service"),
            product.get("document_title"),
        )

        item = back_office_product(product)

        item["payment"] = payment

        item["payment_channels"] = (
            back_office_payment_channels(
                product,
                payment,
            )
        )

        item["delivery"] = (
            back_office_delivery_channels(
                request,
                product,
            )
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
                1
                for x in items
                if x.get("saved_document")
            ),
            "total_documents": len(items),
            "reported": sum(
                1
                for x in items
                if x["payment_reported"]
            ),
            "verified": sum(
                1
                for x in items
                if x["payment_verified"]
            ),
            "activated": sum(
                1
                for x in items
                if x["download_unlocked"]
            ),
        },
    }


@app.get("/api/back-office/jobs")
def back_office_jobs(
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    c = db(PRODUCT_DB_PATH)

    rows = c.execute(
        "SELECT * FROM document_products "
        "ORDER BY updated_at DESC,id DESC"
    ).fetchall()

    c.close()

    return {
        "ok": True,
        "jobs": [dict(row) for row in rows],
    }


@app.get("/api/back-office/payment-channels")
def back_office_payment_channels_endpoint(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    payment = get_payment(
        service,
        title,
    )

    return {
        "ok": True,
        "service": product.get("service"),
        "document_title": product.get(
            "document_title"
        ),
        "payment": payment,
        "payment_channels": back_office_payment_channels(
            product,
            payment,
        ),
    }


@app.get("/api/back-office/payment")
def back_office_payment(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    payment = get_payment(
        service,
        title,
    )

    return {
        "ok": True,
        "product": back_office_product(product),
        "payment": payment,
        "payment_channels": back_office_payment_channels(
            product,
            payment,
        ),
        "delivery": back_office_delivery_channels(
            request,
            product,
        ),
    }


@app.get("/api/back-office/document-info")
def back_office_document_info(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    saved = existing_saved_file(product)

    payload = clean_saved_document_payload(
        from_json(product.get("document_payload"), {})
    )

    return {
        "ok": True,
        "service": product["service"],
        "document_title": product["document_title"],
        "filename": product.get("document_filename"),
        "pages": payload["page_count"],
        "saved_document": bool(saved),
        "saved_path": str(saved) if saved else "",
        "document_text": payload["document_text"],
        "document_pages": payload["pages"],
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


@app.get("/api/back-office/document")
def back_office_document(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    payload = clean_saved_document_payload(
        from_json(product.get("document_payload"), {})
    )

    return {
        "ok": True,
        "service": product["service"],
        "document_title": product["document_title"],
        "filename": product.get("document_filename"),
        "pages": payload["pages"],
        "page_count": payload["page_count"],
        "document_text": payload["document_text"],
        "saved_document": bool(
            existing_saved_file(product)
        ),
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


@app.get("/api/back-office/document-content")
def back_office_document_content(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    payload = clean_saved_document_payload(
        from_json(product.get("document_payload"), {})
    )

    return {
        "ok": True,
        "service": product["service"],
        "document_title": product["document_title"],
        "filename": product.get("document_filename"),
        "pages": payload["pages"],
        "page_count": payload["page_count"],
        "document_text": payload["document_text"],
        "saved_document": bool(
            existing_saved_file(product)
        ),
        "customer_download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


@app.post("/api/back-office/payment/verify")
def back_office_payment_verify(
    body: PaymentCompleteRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

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
            "Payment verified. "
            "Activate download separately when ready."
        ),
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
    }


@app.post("/api/back-office/activate-download")
def back_office_activate(
    body: BackOfficeActivateRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    if not body.verified:
        raise HTTPException(
            status_code=400,
            detail="VERIFICATION_REQUIRED",
        )

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    if not existing_saved_file(product):
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    product = activate_download(
        body.service,
        body.document_title,
    )

    return {
        "ok": True,
        "message": "Download activated.",
        "product": public_product(product),
        "download_unlocked": True,
    }


@app.post("/api/back-office/payment/reject")
def back_office_reject(
    body: BackOfficeRejectRequest,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

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
            detail="PRODUCT_NOT_FOUND",
        )

    if not bool(product.get("download_unlocked")):
        raise HTTPException(
            status_code=403,
            detail="DOWNLOAD_NOT_UNLOCKED",
        )

    product = repair_saved_snapshot(product)

    saved = existing_saved_file(product)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    c = db(PRODUCT_DB_PATH)

    c.execute(
        """
        UPDATE document_products
        SET download_count=COALESCE(download_count,0)+1,
            downloaded_at=?,
            updated_at=?
        WHERE business_key=?
        """,
        (
            now_iso(),
            now_iso(),
            business_key(service, title),
        ),
    )

    c.commit()
    c.close()

    return _saved_file_response(saved)


@app.get("/api/delivery/channels")
def get_delivery_channels(
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    if not existing_saved_file(product):
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

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
    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    selected = select_channel(
        delivery_channels(request, product),
        body.channel,
    )

    if not selected:
        raise HTTPException(
            status_code=404,
            detail="DELIVERY_CHANNEL_NOT_FOUND",
        )

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


@app.get("/api/back-office/delivery-channels")
def back_office_delivery_channels_endpoint(
    service: str,
    title: str,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    return {
        "ok": True,
        "product": back_office_product(product),
        "delivery": back_office_delivery_channels(
            request,
            product,
        ),
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

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    selected = select_channel(
        back_office_delivery_channels(
            request,
            product,
        ),
        channel,
    )

    if not selected:
        raise HTTPException(
            status_code=404,
            detail="DELIVERY_CHANNEL_NOT_FOUND",
        )

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


def _public_download_button_url(
    request: Request,
    token: str,
) -> str:
    return (
        f"{api_base(request)}/api/delivery-file/download"
        f"?token={urllib.parse.quote(token)}"
    )


def _smtp_configured() -> bool:
    return bool(
        clean(os.getenv("SMTP_HOST"))
        and clean(os.getenv("SMTP_USERNAME"))
        and clean(os.getenv("SMTP_PASSWORD"))
        and clean(os.getenv("SMTP_FROM"))
    )


def _simple_email_html(
    title: str,
    service: str,
    download_url: str,
) -> str:
    safe_title = html_escape(title)
    safe_service = html_escape(service)
    safe_url = html_escape(
        download_url,
        quote=True,
    )

    return (
        "<!doctype html><html><body "
        'style="margin:0;padding:24px;'
        'font-family:Arial,sans-serif;color:#222;background:#fff">'
        '<div style="max-width:560px;margin:0 auto">'
        "<h2>Your document is ready</h2>"
        f"<p><strong>Service:</strong> {safe_service}</p>"
        f"<p><strong>Document:</strong> {safe_title}</p>"
        "<p>Your exact saved document is ready for download.</p>"
        f'<p><a href="{safe_url}" '
        'style="display:inline-block;padding:12px 18px;'
        'background:#c00;color:#fff;text-decoration:none;'
        'font-weight:bold">'
        "DOWNLOAD DOCUMENT"
        "</a></p>"
        "</div></body></html>"
    )


def _send_simple_email(
    recipient: str,
    title: str,
    service: str,
    download_url: str,
    saved: Optional[Path] = None,
) -> None:
    if not _smtp_configured():
        raise HTTPException(
            status_code=503,
            detail="EMAIL_DELIVERY_NOT_CONFIGURED",
        )

    host = clean(os.getenv("SMTP_HOST"))
    port = int(os.getenv("SMTP_PORT", "587"))
    username = clean(os.getenv("SMTP_USERNAME"))
    password = clean(os.getenv("SMTP_PASSWORD"))
    sender = clean(os.getenv("SMTP_FROM"))

    use_ssl = (
        clean(
            os.getenv(
                "SMTP_USE_SSL",
                "false",
            )
        ).casefold()
        == "true"
    )

    use_tls = clean(
        os.getenv(
            "SMTP_USE_TLS",
            "true",
        )
    ).casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }

    msg = EmailMessage()

    msg["Subject"] = (
        f"{title} — Naija Pocket Business Center"
    )

    msg["From"] = sender
    msg["To"] = recipient

    msg.set_content(
        f"Your {service} document, "
        f'"{title}", is ready.\n\n'
        f"Download the exact saved document:\n"
        f"{download_url}"
    )

    msg.add_alternative(
        _simple_email_html(
            title,
            service,
            download_url,
        ),
        subtype="html",
    )

    # Attach the exact saved document.
    # No regeneration or document reconstruction happens here.
    if saved and saved.exists():
        with open(saved, "rb") as fh:
            data = fh.read()

        suffix = saved.suffix.casefold()

        if suffix == ".docx":
            msg.add_attachment(
                data,
                maintype="application",
                subtype=(
                    "vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                filename=saved.name,
            )

        elif suffix == ".pdf":
            msg.add_attachment(
                data,
                maintype="application",
                subtype="pdf",
                filename=saved.name,
            )

        else:
            msg.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=saved.name,
            )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(
                host,
                port,
                timeout=30,
            ) as server:
                server.login(
                    username,
                    password,
                )
                server.send_message(msg)

        else:
            with smtplib.SMTP(
                host,
                port,
                timeout=30,
            ) as server:
                server.ehlo()

                if use_tls:
                    server.starttls()
                    server.ehlo()

                server.login(
                    username,
                    password,
                )

                server.send_message(msg)

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"EMAIL_SEND_FAILED: {clean(exc)}",
        ) from exc


class RecipientEmailRequest(BaseModel):
    service: str
    document_title: str
    recipient_email: str


@app.post("/api/delivery/email")
def delivery_email(
    body: RecipientEmailRequest,
    request: Request,
):
    """Send the exact saved document with a direct DOWNLOAD DOCUMENT link."""

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    saved = existing_saved_file(product)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    if not bool(product.get("download_unlocked")):
        raise HTTPException(
            status_code=403,
            detail="DOWNLOAD_NOT_UNLOCKED",
        )

    recipient = clean(body.recipient_email)

    if not re.match(
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        recipient,
    ):
        raise HTTPException(
            status_code=400,
            detail="INVALID_RECIPIENT_EMAIL",
        )

    direct_url = public_delivery_url(
        request,
        body.service,
        body.document_title,
    )

    _send_simple_email(
        recipient,
        clean(product.get("document_title")),
        clean(product.get("service")),
        direct_url,
        saved,
    )

    log_delivery(
        product,
        "email",
        "sent",
        {
            "recipient": recipient,
            "download_url": direct_url,
        },
    )

    return {
        "ok": True,
        "message": (
            "The email was sent with a direct "
            "DOWNLOAD DOCUMENT link to the exact "
            "saved document."
        ),
        "recipient_email": recipient,
        "download_url": direct_url,
        "product": public_product(product),
    }


@app.get("/api/delivery-file")
def public_delivery_file(
    token: str,
):
    """Direct signed download. There is no branded/intermediate page."""

    service, title = _read_public_delivery_token(token)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    if not bool(product.get("download_unlocked")):
        raise HTTPException(
            status_code=403,
            detail="DOWNLOAD_NOT_UNLOCKED",
        )

    product = repair_saved_snapshot(product)

    saved = existing_saved_file(product)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    return _saved_file_response(saved)


@app.get("/api/back-office/delivery-file")
def back_office_delivery_file(
    service: str,
    title: str,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    saved = existing_saved_file(product)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    return _saved_file_response(saved)


@app.get("/api/back-office/delivery-history")
def delivery_history(
    service: Optional[str] = None,
    title: Optional[str] = None,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    c = db(PRODUCT_DB_PATH)

    if service and title:
        rows = c.execute(
            """
            SELECT * FROM delivery_events
            WHERE business_key=?
            ORDER BY created_at DESC,id DESC
            """,
            (business_key(service, title),),
        ).fetchall()

    else:
        rows = c.execute(
            """
            SELECT * FROM delivery_events
            ORDER BY created_at DESC,id DESC
            """
        ).fetchall()

    c.close()

    return {
        "ok": True,
        "events": [dict(row) for row in rows],
    }


@app.get("/api/product/status")
def product_status(
    service: str,
    title: str,
    request: Request,
):
    product = get_product(
        service,
        title,
    )

    if not product:
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
        "delivery": delivery_channels(
            request,
            product,
        ),
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": (
            "Naija Pocket Business Center Payment API"
        ),
        "version": APP_VERSION,
        "back_office_key_configured": True,
    }


@app.get("/")
def root():
    return {
        "ok": True,
        "service": (
            "Naija Pocket Business Center Payment API"
        ),
        "version": APP_VERSION,
        "message": (
            "Payment, saved-document, Customer Care "
            "and delivery API is running."
        ),
    }


@app.post("/api/back-office/delivery")
def back_office_delivery_post(
    body: DeliveryRequest,
    request: Request,
    x_back_office_key: str = Header(default=""),
):
    require_back_office(x_back_office_key)

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(product)

    # Back Office delivery uses the exact saved document.
    # It does not depend on customer download activation.
    saved = existing_saved_file(product)

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    selected = select_channel(
        back_office_delivery_channels(
            request,
            product,
        ),
        body.channel,
    )

    if not selected:
        raise HTTPException(
            status_code=404,
            detail="DELIVERY_CHANNEL_NOT_FOUND",
        )

    # EMAIL DELIVERY
    #
    # The Back Office supplies the customer's email address.
    # The exact saved document is attached directly.
    if selected["id"] == "email":
        recipient = clean(
            body.recipient_email
        )

        if not recipient:
            raise HTTPException(
                status_code=400,
                detail="RECIPIENT_EMAIL_REQUIRED",
            )

        if not re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            recipient,
        ):
            raise HTTPException(
                status_code=400,
                detail="INVALID_RECIPIENT_EMAIL",
            )

        direct_url = public_delivery_url(
            request,
            clean(product.get("service")),
            clean(product.get("document_title")),
        )

        _send_simple_email(
            recipient,
            clean(product.get("document_title")),
            clean(product.get("service")),
            direct_url,
            saved,
        )

        log_delivery(
            product,
            "email",
            "sent",
            {
                "recipient": recipient,
                "filename": saved.name,
                "download_url": direct_url,
            },
        )

        return {
            "ok": True,
            "status": "sent",
            "channel": selected,
            "recipient_email": recipient,
            "attachment": saved.name,
            "product": back_office_product(product),
            "message": (
                "The exact saved document has been "
                "attached and sent to the customer "
                "by email."
            ),
        }

    # ALL OTHER BACK OFFICE DELIVERY CHANNELS
    # Their existing behavior remains unchanged.
    log_delivery(
        product,
        selected["id"],
        "ready",
        {"url": selected.get("url")},
    )

    return {
        "ok": True,
        "status": "ready",
        "channel": selected,
        "product": back_office_product(product),
        "message": (
            "The exact saved document is ready for "
            "Back Office delivery through this channel."
        ),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
