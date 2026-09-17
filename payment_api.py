"""
Naija Pocket Business Center
Complete payment, saved-document and delivery API.

CANONICAL RULE:
- Document saved once as canonical file
- All downloads return that exact file
- No regeneration during download
"""

from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
import json
import os
import re
import secrets
import sqlite3
import urllib.parse
import zipfile
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


APP_VERSION = "payment-product-canonical-v18-review-compatible"

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = "NPBC-2026"
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "").strip()
BACK_OFFICE_DOWNLOAD_TOKEN_MINUTES = 60


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
# GENERAL HELPERS
# ============================================================

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

    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip(".")

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

    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip(".")

    return name[:120] or default


# ============================================================
# MARKDOWN CLEANER
# ============================================================

def _strip_md(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    # Code blocks.
    text = re.sub(
        r"```.*?```",
        "",
        text,
        flags=re.DOTALL,
    )

    # Inline backticks.
    text = text.replace("`", "")

    # Headings.
    text = re.sub(
        r"^\s{0,3}#{1,6}\s+",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Horizontal rules.
    text = re.sub(
        r"^\s*[-*_]{3,}\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Blockquotes.
    text = re.sub(
        r"^\s*>\s*",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Bold.
    text = re.sub(
        r"\*\*(.*?)\*\*",
        r"\1",
        text,
    )

    text = re.sub(
        r"__(.*?)__",
        r"\1",
        text,
    )

    # Italic.
    text = re.sub(
        r"\*(.*?)\*",
        r"\1",
        text,
    )

    text = re.sub(
        r"_(.*?)_",
        r"\1",
        text,
    )

    # Markdown links.
    text = re.sub(
        r"\[(.*?)\]\(.*?\)",
        r"\1",
        text,
    )

    # Unordered list markers.
    text = re.sub(
        r"^\s*[-*+]\s+",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Ordered list markers.
    text = re.sub(
        r"^\s*\d+\.\s+",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Table pipes.
    text = text.replace("|", " ")

    # Excess spaces.
    text = re.sub(
        r" {2,}",
        " ",
        text,
    )

    return text.strip()


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

        return [
            json.dumps(
                value,
                ensure_ascii=False,
            )
        ]

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


def clean_saved_document_payload(payload: dict) -> dict:
    normalized = normalize_payload(
        dict(payload or {})
    )

    cleaned_pages = []

    for page in normalized.get("pages") or []:
        page_lines = []

        for raw_line in str(page).splitlines():
            cleaned_line = _strip_md(raw_line)

            if not cleaned_line:
                continue

            if cleaned_line in {
                "---",
                "***",
                "___",
            }:
                continue

            if re.match(
                r"^[\-\:\s]+$",
                cleaned_line,
            ):
                continue

            page_lines.append(cleaned_line)

        if page_lines:
            cleaned_pages.append(
                "\n".join(page_lines)
            )

    text = _strip_md(
        normalized.get("document_text") or ""
    )

    if not cleaned_pages and text:
        cleaned_pages = [text]

    if cleaned_pages:
        text = "\n\n".join(cleaned_pages)

    normalized["pages"] = cleaned_pages
    normalized["document_text"] = text
    normalized["page_count"] = len(cleaned_pages)

    return normalized


# ============================================================
# DATABASES
# ============================================================

def init_databases() -> None:
    connection = db(PRODUCT_DB_PATH)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS document_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL UNIQUE,
            service TEXT NOT NULL,
            document_title TEXT NOT NULL,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            customer_id TEXT,
            amount TEXT,
            currency TEXT DEFAULT 'NGN',
            job_id TEXT,
            document_payload TEXT,
            document_text TEXT,
            document_filename TEXT,
            document_version TEXT,
            document_pages INTEGER DEFAULT 0,
            document_saved_path TEXT,
            document_saved_at TEXT,
            download_unlocked INTEGER DEFAULT 0,
            download_count INTEGER DEFAULT 0,
            downloaded_at TEXT,
            activated_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL,
            service TEXT,
            document_title TEXT,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            recipient TEXT,
            detail TEXT,
            created_at TEXT NOT NULL,
            details TEXT
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS back_office_download_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            business_key TEXT NOT NULL,
            service TEXT NOT NULL,
            document_title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            download_count INTEGER DEFAULT 0,
            last_downloaded_at TEXT
        )
        """
    )

    connection.commit()
    connection.close()

    connection = db(PAYMENT_DB_PATH)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_key TEXT NOT NULL UNIQUE,
            service TEXT NOT NULL,
            document_title TEXT NOT NULL,
            customer_name TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            customer_id TEXT,
            amount TEXT,
            currency TEXT DEFAULT 'NGN',
            payment_method TEXT,
            payment_status TEXT DEFAULT 'pending',
            document_version TEXT,
            document_filename TEXT,
            document_pages INTEGER DEFAULT 0,
            document_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reported_at TEXT,
            payment_reported_at TEXT,
            verified_at TEXT,
            payment_completed_at TEXT,
            completed_at TEXT,
            rejected_at TEXT,
            notes TEXT
        )
        """
    )

    connection.commit()
    connection.close()


@app.on_event("startup")
def startup() -> None:
    init_databases()


# ============================================================
# PRODUCT / PAYMENT LOOKUPS
# ============================================================

def get_product(
    service: str,
    title: str,
) -> Optional[dict]:
    connection = db(PRODUCT_DB_PATH)

    row = connection.execute(
        """
        SELECT *
        FROM document_products
        WHERE business_key=?
        """,
        (
            business_key(
                service,
                title,
            ),
        ),
    ).fetchone()

    connection.close()

    return as_dict(row)


def get_product_by_business_key(
    business_key_value: str,
) -> Optional[dict]:
    connection = db(PRODUCT_DB_PATH)

    row = connection.execute(
        """
        SELECT *
        FROM document_products
        WHERE business_key=?
        """,
        (clean(business_key_value),),
    ).fetchone()

    connection.close()

    return as_dict(row)


def get_payment(
    business_key_value: str,
) -> Optional[dict]:
    connection = db(PAYMENT_DB_PATH)

    row = connection.execute(
        """
        SELECT *
        FROM payment_orders
        WHERE business_key=?
        """,
        (clean(business_key_value),),
    ).fetchone()

    connection.close()

    return as_dict(row)


def existing_saved_file(
    product: Optional[dict],
) -> Optional[Path]:
    if not product:
        return None

    raw = clean(
        product.get("document_saved_path")
    )

    if not raw:
        return None

    path = Path(raw)

    if not path.is_absolute():
        path = BASE_DIR / path

    if path.exists() and path.is_file():
        return path

    return None


# ============================================================
# DOCX CREATION
# ============================================================

def _docx_p(text: str) -> str:
    raw = str(text).replace(
        "\r",
        "",
    ).strip()

    if not raw:
        return ""

    runs = [
        '<w:r><w:t xml:space="preserve">'
        f"{xml_escape(raw)}"
        "</w:t></w:r>"
    ]

    return (
        "<w:p>"
        + "".join(runs)
        + "</w:p>"
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

    payload_clean = clean_saved_document_payload(
        {"pages": page_list}
    )

    pages = payload_clean.get("pages") or []

    if not pages:
        pages = [
            clean(title) or "Document"
        ]

    body = []

    for index, page in enumerate(pages):
        if index:
            body.append(
                '<w:p><w:r><w:br w:type="page"/>'
                "</w:r></w:p>"
            )

        for line in str(page).splitlines():
            if line.strip():
                body.append(
                    _docx_p(line)
                )

    if not body:
        body.append(
            _docx_p(
                clean(title) or "Document"
            )
        )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
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
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" '
        'standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(
        path,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "[Content_Types].xml",
            content_types,
        )
        archive.writestr(
            "_rels/.rels",
            root_rels,
        )
        archive.writestr(
            "word/document.xml",
            document_xml,
        )

    return path


# ============================================================
# CANONICAL DOCUMENT
# ============================================================

def save_exact_snapshot(
    service: str,
    title: str,
    payload: dict,
    existing_product: Optional[dict] = None,
) -> Path:

    # Preserve an already-established canonical file.
    existing_file = existing_saved_file(
        existing_product or get_product(
            service,
            title,
        )
    )

    if existing_file is not None:
        return existing_file

    # Preserve legacy existing product file.
    service_clean = clean(service)
    title_clean = clean(title)

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service_clean)
        / safe_folder(title_clean)
    )

    if folder.exists():
        candidates = [
            item
            for item in folder.iterdir()
            if item.is_file()
            and item.suffix.lower()
            in {".docx", ".pdf"}
        ]

        if candidates:
            candidates.sort(
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )

            return candidates[0]

    normalized = clean_saved_document_payload(
        payload
    )

    if (
        not normalized["pages"]
        and not normalized["document_text"]
    ):
        raise HTTPException(
            status_code=400,
            detail="SAVED_DOCUMENT_CONTENT_MISSING",
        )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = safe_filename(
        normalized.get("filename")
        or title_clean
    )

    if filename.lower().endswith(".pdf"):
        filename = (
            Path(filename).stem
            + ".docx"
        )

    target = folder / filename

    if target.exists() and target.is_file():
        return target

    return make_docx(
        target,
        title_clean,
        normalized["pages"]
        or [normalized["document_text"]],
    )


def store_saved_path(
    business_key_value: str,
    saved_path: Path,
) -> None:
    timestamp = now_iso()

    with db(PRODUCT_DB_PATH) as connection:
        connection.execute(
            """
            UPDATE document_products
            SET document_saved_path=?,
                document_saved_at=COALESCE(
                    document_saved_at,
                    ?
                ),
                updated_at=?
            WHERE business_key=?
            """,
            (
                str(saved_path),
                timestamp,
                timestamp,
                clean(business_key_value),
            ),
        )


def extract_document_text(
    payload: dict,
) -> str:
    return clean_saved_document_payload(
        payload
    ).get(
        "document_text",
        "",
    )


# ============================================================
# PRODUCT UPSERT
# ============================================================

def upsert_product(
    *,
    service: str,
    title: str,
    payload: dict,
    customer_name: str = "",
    customer_email: str = "",
    customer_phone: str = "",
    amount: str = "",
    currency: str = "NGN",
    job_id: str = "",
) -> dict:

    service_clean = clean(service)
    title_clean = clean(title)

    if not service_clean:
        raise HTTPException(
            status_code=400,
            detail="SERVICE_REQUIRED",
        )

    if not title_clean:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_TITLE_REQUIRED",
        )

    business_key_value = business_key(
        service_clean,
        title_clean,
    )

    normalized = clean_saved_document_payload(
        payload
    )

    with db(PRODUCT_DB_PATH) as connection:
        existing = connection.execute(
            """
            SELECT *
            FROM document_products
            WHERE business_key=?
            LIMIT 1
            """,
            (business_key_value,),
        ).fetchone()

        timestamp = now_iso()

        if existing:
            connection.execute(
                """
                UPDATE document_products
                SET
                    customer_name=
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE customer_name
                        END,

                    customer_email=
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE customer_email
                        END,

                    customer_phone=
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE customer_phone
                        END,

                    amount=
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE amount
                        END,

                    currency=
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE currency
                        END,

                    job_id=
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE job_id
                        END,

                    document_payload=
                        CASE
                            WHEN document_saved_path IS NULL
                              OR document_saved_path=''
                            THEN ?
                            ELSE document_payload
                        END,

                    document_text=
                        CASE
                            WHEN document_saved_path IS NULL
                              OR document_saved_path=''
                            THEN ?
                            ELSE document_text
                        END,

                    updated_at=?

                WHERE business_key=?
                """,
                (
                    clean(customer_name),
                    clean(customer_name),

                    clean(customer_email),
                    clean(customer_email),

                    clean(customer_phone),
                    clean(customer_phone),

                    clean(amount),
                    clean(amount),

                    clean(currency),
                    clean(currency),

                    clean(job_id),
                    clean(job_id),

                    to_json(normalized),
                    extract_document_text(normalized),

                    timestamp,
                    business_key_value,
                ),
            )

        else:
            connection.execute(
                """
                INSERT INTO document_products (
                    business_key,
                    service,
                    document_title,
                    customer_name,
                    customer_email,
                    customer_phone,
                    amount,
                    currency,
                    job_id,
                    document_payload,
                    document_text,
                    document_saved_path,
                    document_saved_at,
                    download_unlocked,
                    download_count,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,'',
                    NULL,0,0,?,?
                )
                """,
                (
                    business_key_value,
                    service_clean,
                    title_clean,
                    clean(customer_name),
                    clean(customer_email),
                    clean(customer_phone),
                    clean(amount),
                    clean(currency) or "NGN",
                    clean(job_id),
                    to_json(normalized),
                    extract_document_text(normalized),
                    timestamp,
                    timestamp,
                ),
            )

    product = get_product(
        service_clean,
        title_clean,
    )

    if not product:
        raise HTTPException(
            status_code=500,
            detail="PRODUCT_SAVE_FAILED",
        )

    return product


# ============================================================
# PAYMENT HELPERS
# ============================================================

def ensure_payment_record(
    product: dict,
) -> dict:

    business_key_value = clean(
        product.get("business_key")
    )

    if not business_key_value:
        raise HTTPException(
            status_code=400,
            detail="BUSINESS_KEY_REQUIRED",
        )

    existing = get_payment(
        business_key_value
    )

    if existing:
        return existing

    timestamp = now_iso()

    with db(PAYMENT_DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO payment_orders (
                business_key,
                service,
                document_title,
                customer_name,
                customer_email,
                customer_phone,
                amount,
                currency,
                document_text,
                payment_status,
                created_at,
                updated_at
            )
            VALUES (
                ?,?,?,?,?,?,?,?,?, 'pending',?,?
            )
            """,
            (
                business_key_value,
                clean(product.get("service")),
                clean(product.get("document_title")),
                clean(product.get("customer_name")),
                clean(product.get("customer_email")),
                clean(product.get("customer_phone")),
                clean(product.get("amount")),
                clean(product.get("currency") or "NGN"),
                clean(product.get("document_text")),
                timestamp,
                timestamp,
            ),
        )

    payment = get_payment(
        business_key_value
    )

    if not payment:
        raise HTTPException(
            status_code=500,
            detail="PAYMENT_RECORD_SAVE_FAILED",
        )

    return payment


def update_payment(
    business_key_value: str,
    *,
    payment_status: Optional[str] = None,
    reported_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> Optional[dict]:

    updates = []
    values = []

    if payment_status is not None:
        updates.append(
            "payment_status=?"
        )
        values.append(
            clean(payment_status)
        )

    if reported_at is not None:
        updates.append(
            "payment_reported_at=?"
        )
        values.append(
            clean(reported_at)
        )

    if completed_at is not None:
        updates.append(
            "payment_completed_at=?"
        )
        values.append(
            clean(completed_at)
        )

    if not updates:
        return get_payment(
            business_key_value
        )

    updates.append(
        "updated_at=?"
    )

    values.append(
        now_iso()
    )

    values.append(
        clean(business_key_value)
    )

    with db(PAYMENT_DB_PATH) as connection:
        connection.execute(
            f"""
            UPDATE payment_orders
            SET {', '.join(updates)}
            WHERE business_key=?
            """,
            tuple(values),
        )

    return get_payment(
        business_key_value
    )


# ============================================================
# SAVED DOCUMENT REPAIR / MIGRATION
# ============================================================

def repair_saved_snapshot(
    product: dict,
) -> Optional[Path]:

    saved = existing_saved_file(
        product
    )

    if saved:
        return saved

    service = clean(
        product.get("service")
    )

    title = clean(
        product.get("document_title")
    )

    if not service or not title:
        return None

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    if not folder.exists():
        return None

    candidates = [
        item
        for item in folder.iterdir()
        if item.is_file()
        and item.suffix.lower()
        in {".docx", ".pdf"}
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    selected = candidates[0]

    store_saved_path(
        clean(product.get("business_key")),
        selected,
    )

    return selected


def activate_download(
    business_key_value: str,
) -> Optional[dict]:

    business_key_value = clean(
        business_key_value
    )

    if not business_key_value:
        return None

    timestamp = now_iso()

    with db(PRODUCT_DB_PATH) as connection:
        connection.execute(
            """
            UPDATE document_products
            SET
                download_unlocked=1,
                activated_at=?,
                updated_at=?
            WHERE business_key=?
            """,
            (
                timestamp,
                timestamp,
                business_key_value,
            ),
        )

    return get_product_by_business_key(
        business_key_value
    )


# ============================================================
# PUBLIC PRODUCT
# ============================================================

def public_product(
    product: Optional[dict],
) -> Optional[dict]:

    if not product:
        return None

    return {
        "business_key": clean(
            product.get("business_key")
        ),
        "service": clean(
            product.get("service")
        ),
        "document_title": clean(
            product.get("document_title")
        ),
        "customer_name": clean(
            product.get("customer_name")
        ),
        "customer_email": clean(
            product.get("customer_email")
        ),
        "customer_phone": clean(
            product.get("customer_phone")
        ),
        "amount": clean(
            product.get("amount")
        ),
        "currency": clean(
            product.get("currency") or "NGN"
        ),
        "job_id": clean(
            product.get("job_id")
        ),
        "document_saved": bool(
            clean(
                product.get(
                    "document_saved_path"
                )
            )
        ),
        "document_saved_path": clean(
            product.get(
                "document_saved_path"
            )
        ),
        "document_saved_at": clean(
            product.get(
                "document_saved_at"
            )
        ),
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
        "download_count": int(
            product.get(
                "download_count"
            ) or 0
        ),
        "downloaded_at": clean(
            product.get(
                "downloaded_at"
            )
        ),
        "activated_at": clean(
            product.get(
                "activated_at"
            )
        ),
        "created_at": clean(
            product.get("created_at")
        ),
        "updated_at": clean(
            product.get("updated_at")
        ),
    }


# ============================================================
# URL / DELIVERY HELPERS
# ============================================================

def api_base(
    request: Optional[Request] = None,
) -> str:

    if PUBLIC_API_BASE_URL:
        return PUBLIC_API_BASE_URL.rstrip("/")

    if request is not None:
        return str(
            request.base_url
        ).rstrip("/")

    return ""


def download_url(
    service: str,
    title: str,
    request: Optional[Request] = None,
) -> str:

    base = api_base(request)

    query = urllib.parse.urlencode(
        {
            "service": clean(service),
            "title": clean(title),
        }
    )

    return (
        f"{base}/api/download?{query}"
    )


def delivery_channels(
    product: dict,
    request: Optional[Request] = None,
) -> list[dict]:

    service = clean(
        product.get("service")
    )

    title = clean(
        product.get("document_title")
    )

    return [
        {
            "channel": "phone",
            "label": "Download to Phone",
            "available": bool(
                product.get(
                    "download_unlocked"
                )
            ),
            "url": download_url(
                service,
                title,
                request,
            ),
        },
        {
            "channel": "whatsapp",
            "label": "WhatsApp",
            "available": True,
            "url": "",
        },
        {
            "channel": "email",
            "label": "Email",
            "available": True,
            "url": "",
        },
        {
            "channel": "telegram",
            "label": "Telegram",
            "available": True,
            "url": "",
        },
        {
            "channel": "google_drive",
            "label": "Google Drive",
            "available": True,
            "url": "",
        },
    ]


def create_back_office_download_token(
    product: dict,
) -> str:

    token = secrets.token_urlsafe(32)

    created = datetime.now(
        timezone.utc
    )

    expires = (
        created
        + timedelta(
            minutes=BACK_OFFICE_DOWNLOAD_TOKEN_MINUTES
        )
    )

    with db(PRODUCT_DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO back_office_download_tokens (
                token,
                business_key,
                service,
                document_title,
                created_at,
                expires_at,
                download_count
            )
            VALUES (?,?,?,?,?,?,0)
            """,
            (
                token,
                clean(
                    product.get(
                        "business_key"
                    )
                ),
                clean(
                    product.get("service")
                ),
                clean(
                    product.get(
                        "document_title"
                    )
                ),
                created.isoformat(),
                expires.isoformat(),
            ),
        )

    return token


def validate_back_office_download_token(
    token: str,
) -> Optional[dict]:

    token_clean = clean(token)

    if not token_clean:
        return None

    with db(PRODUCT_DB_PATH) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM back_office_download_tokens
            WHERE token=?
            LIMIT 1
            """,
            (token_clean,),
        ).fetchone()

    if not row:
        return None

    record = dict(row)

    try:
        expires = datetime.fromisoformat(
            clean(
                record.get(
                    "expires_at"
                )
            )
        )

        if expires.tzinfo is None:
            expires = expires.replace(
                tzinfo=timezone.utc
            )

        if (
            datetime.now(timezone.utc)
            > expires
        ):
            return None

    except Exception:
        return None

    return record


def record_back_office_token_download(
    token: str,
) -> None:

    with db(PRODUCT_DB_PATH) as connection:
        connection.execute(
            """
            UPDATE back_office_download_tokens
            SET
                download_count=
                    download_count+1,
                last_downloaded_at=?
            WHERE token=?
            """,
            (
                now_iso(),
                clean(token),
            ),
        )


def back_office_delivery_channels(
    product: dict,
    request: Optional[Request] = None,
) -> list[dict]:

    token = create_back_office_download_token(
        product
    )

    base = api_base(request)

    phone_url = (
        f"{base}/api/back-office/delivery-file"
        f"?token="
        f"{urllib.parse.quote(token)}"
    )

    return [
        {
            "channel": "phone",
            "label": "Download to Phone",
            "available": True,
            "url": phone_url,
        },
        {
            "channel": "whatsapp",
            "label": "WhatsApp",
            "available": True,
            "url": "",
        },
        {
            "channel": "email",
            "label": "Email",
            "available": True,
            "url": "",
        },
        {
            "channel": "telegram",
            "label": "Telegram",
            "available": True,
            "url": "",
        },
        {
            "channel": "google_drive",
            "label": "Google Drive",
            "available": True,
            "url": "",
        },
    ]


def select_channel(
    channels: list[dict],
    requested: str,
) -> Optional[dict]:

    requested_clean = clean(
        requested
    ).lower()

    for channel in channels:
        if (
            clean(
                channel.get("channel")
            ).lower()
            == requested_clean
        ):
            return channel

    return None


def log_delivery(
    *,
    business_key_value: str,
    channel: str,
    status: str,
    recipient: str = "",
    detail: str = "",
) -> None:

    with db(PRODUCT_DB_PATH) as connection:
        connection.execute(
            """
            INSERT INTO delivery_events (
                business_key,
                channel,
                status,
                recipient,
                detail,
                created_at
            )
            VALUES (?,?,?,?,?,?)
            """,
            (
                clean(business_key_value),
                clean(channel),
                clean(status),
                clean(recipient),
                clean(detail),
                now_iso(),
            ),
        )


def require_back_office(
    admin_key: Optional[str],
) -> None:

    if clean(admin_key) != BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(
            status_code=401,
            detail="BACK_OFFICE_UNAUTHORIZED",
        )


# ============================================================
# REQUEST MODELS
# ============================================================

class PaymentCreateRequest(BaseModel):
    """
    Compatibility model.

    Review remains unchanged.

    The endpoint also accepts additional fields because the
    existing Review page may send document/service information
    under different names.
    """

    service: Optional[str] = ""
    document_title: Optional[str] = ""
    document_payload: Any = None

    customer_name: str = ""
    customer_email: str = ""
    customer_phone: str = ""
    amount: str = ""
    currency: str = "NGN"
    job_id: str = ""

    class Config:
        extra = "allow"


class PaymentReportRequest(BaseModel):
    service: str
    document_title: str
    customer_name: str = ""
    customer_email: str = ""
    customer_phone: str = ""
    amount: str = ""
    currency: str = "NGN"
    reported_at: Optional[str] = None


class PaymentCompleteRequest(BaseModel):
    service: str
    document_title: str


class BackOfficeActivateRequest(BaseModel):
    service: str
    document_title: str


class BackOfficeRejectRequest(BaseModel):
    service: str
    document_title: str
    reason: str = ""


class DeliveryRequest(BaseModel):
    service: str
    document_title: str
    channel: str
    recipient_email: str = ""
    recipient_phone: str = ""
    recipient: str = ""


# ============================================================
# MAKE PAYMENT
# ============================================================

@app.post("/api/payment/create")
async def create_payment(
    request: Request,
):
    """
    MAKE PAYMENT.

    Review is not changed.

    This endpoint accepts the existing Review request and
    normalizes its fields internally.

    Behavior:
    - Saves the reviewed document if not already saved.
    - Creates/prepares payment record.
    - Does NOT verify payment.
    - Does NOT unlock customer download.
    - Does NOT regenerate an existing canonical document.
    """

    try:
        incoming = await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="INVALID_PAYMENT_REQUEST",
        )

    if not isinstance(incoming, dict):
        raise HTTPException(
            status_code=400,
            detail="INVALID_PAYMENT_REQUEST",
        )

    # --------------------------------------------------------
    # SERVICE
    # --------------------------------------------------------

    service = first(
        incoming.get("service"),
        incoming.get("service_name"),
        incoming.get("serviceName"),
    )

    if not service:
        product_data = incoming.get(
            "product"
        )

        if isinstance(
            product_data,
            dict,
        ):
            service = first(
                product_data.get("service"),
                product_data.get("service_name"),
                product_data.get("serviceName"),
            )

    # --------------------------------------------------------
    # DOCUMENT TITLE
    # --------------------------------------------------------

    document_title = first(
        incoming.get("document_title"),
        incoming.get("documentTitle"),
        incoming.get("title"),
        incoming.get("document_title_text"),
    )

    if not document_title:
        document_data = incoming.get(
            "document"
        )

        if isinstance(
            document_data,
            dict,
        ):
            document_title = first(
                document_data.get(
                    "document_title"
                ),
                document_data.get(
                    "documentTitle"
                ),
                document_data.get(
                    "title"
                ),
            )

    if not document_title:
        product_data = incoming.get(
            "product"
        )

        if isinstance(
            product_data,
            dict,
        ):
            document_title = first(
                product_data.get(
                    "document_title"
                ),
                product_data.get(
                    "documentTitle"
                ),
                product_data.get(
                    "title"
                ),
            )

    # --------------------------------------------------------
    # DOCUMENT PAYLOAD
    # --------------------------------------------------------

    document_payload = incoming.get(
        "document_payload"
    )

    if document_payload is None:
        document_payload = incoming.get(
            "documentPayload"
        )

    if document_payload is None:
        document_payload = incoming.get(
            "document"
        )

    if document_payload is None:
        document_payload = incoming.get(
            "payload"
        )

    if document_payload is None:
        document_payload = incoming.get(
            "document_data"
        )

    if document_payload is None:
        document_payload = incoming.get(
            "documentData"
        )

    # --------------------------------------------------------
    # DIRECT DOCUMENT FIELDS
    # --------------------------------------------------------

    if document_payload is None:

        possible_fields = (
            "pages",
            "page_text",
            "document_pages",
            "document_text",
            "documentText",
            "text",
            "content",
            "filename",
            "document_filename",
            "documentFilename",
        )

        extracted = {}

        for field in possible_fields:
            if field in incoming:
                extracted[field] = incoming.get(
                    field
                )

        if extracted:
            document_payload = extracted

    # --------------------------------------------------------
    # JSON-STRING DOCUMENT PAYLOAD
    # --------------------------------------------------------

    if isinstance(
        document_payload,
        str,
    ):

        parsed_payload = from_json(
            document_payload
        )

        if isinstance(
            parsed_payload,
            dict,
        ):
            document_payload = parsed_payload

        elif isinstance(
            parsed_payload,
            list,
        ):
            document_payload = {
                "pages": parsed_payload
            }

        else:
            document_payload = {
                "document_text": clean(
                    document_payload
                )
            }

    # --------------------------------------------------------
    # LIST DOCUMENT PAYLOAD
    # --------------------------------------------------------

    if isinstance(
        document_payload,
        list,
    ):
        document_payload = {
            "pages": document_payload
        }

    if not isinstance(
        document_payload,
        dict,
    ):
        document_payload = {}

    # --------------------------------------------------------
    # CUSTOMER INFORMATION
    # --------------------------------------------------------

    customer_name = first(
        incoming.get("customer_name"),
        incoming.get("customerName"),
    )

    customer_email = first(
        incoming.get("customer_email"),
        incoming.get("customerEmail"),
        incoming.get("email"),
    )

    customer_phone = first(
        incoming.get("customer_phone"),
        incoming.get("customerPhone"),
        incoming.get("phone"),
    )

    amount = first(
        incoming.get("amount"),
        incoming.get("total"),
        incoming.get("price"),
    )

    currency = first(
        incoming.get("currency"),
        incoming.get("payment_currency"),
    ) or "NGN"

    job_id = first(
        incoming.get("job_id"),
        incoming.get("jobId"),
    )

    # --------------------------------------------------------
    # REQUIRED PAYMENT PRODUCT DATA
    # --------------------------------------------------------

    if not service:
        raise HTTPException(
            status_code=400,
            detail="SERVICE_REQUIRED",
        )

    if not document_title:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_TITLE_REQUIRED",
        )

    if not document_payload:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_PAYLOAD_REQUIRED",
        )

    # --------------------------------------------------------
    # EXISTING PRODUCT/PAYMENT FLOW
    # --------------------------------------------------------

    product = upsert_product(
        service=service,
        title=document_title,
        payload=document_payload,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        amount=amount,
        currency=currency,
        job_id=job_id,
    )

    saved_path = repair_saved_snapshot(
        product
    )

    if saved_path is None:

        saved_path = save_exact_snapshot(
            service,
            document_title,
            document_payload,
            product,
        )

        store_saved_path(
            clean(
                product.get(
                    "business_key"
                )
            ),
            saved_path,
        )

        product = get_product(
            service,
            document_title,
        )

    if product is None:
        raise HTTPException(
            status_code=500,
            detail="PRODUCT_NOT_FOUND_AFTER_SAVE",
        )

    payment = ensure_payment_record(
        product
    )

    return {
        "ok": True,
        "message": "PAYMENT_PREPARED",
        "product": public_product(product),
        "payment": payment,
        "payment_verified": False,
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


# ============================================================
# I HAVE MADE PAYMENT
# ============================================================

@app.post("/api/payment/report")
def report_payment(
    body: PaymentReportRequest,
):
    """
    I HAVE MADE PAYMENT.

    Reports only.
    Does NOT unlock customer download.
    """

    product = get_product(
        body.service,
        body.document_title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    payment = get_payment(
        clean(
            product.get(
                "business_key"
            )
        )
    )

    if not payment:
        payment = ensure_payment_record(
            product
        )

    payment = update_payment(
        clean(
            product.get(
                "business_key"
            )
        ),
        payment_status="reported",
        reported_at=(
            clean(body.reported_at)
            or now_iso()
        ),
    )

    return {
        "ok": True,
        "message": "PAYMENT_REPORTED",
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


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
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    payment = get_payment(
        clean(
            product.get(
                "business_key"
            )
        )
    )

    return {
        "ok": True,
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


@app.post("/api/payment/complete")
def complete_payment(
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

    business_key_value = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        business_key_value
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        business_key_value,
        payment_status="completed",
        completed_at=now_iso(),
    )

    return {
        "ok": True,
        "message": "PAYMENT_COMPLETED",
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


# ============================================================
# CUSTOMER CARE
# ============================================================

@app.get("/api/customer-care/payments")
def customer_care_payments():

    with db(PAYMENT_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY created_at DESC
            """
        ).fetchall()

    return {
        "ok": True,
        "payments": [
            dict(row)
            for row in rows
        ],
    }


@app.post("/api/customer-care/verify-payment")
def customer_care_verify_payment(
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

    business_key_value = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        business_key_value
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        business_key_value,
        payment_status="verified",
    )

    return {
        "ok": True,
        "message": "PAYMENT_VERIFIED",
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


# ============================================================
# BACK OFFICE
# ============================================================

@app.post("/api/back-office/login")
def back_office_login(
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    return {
        "ok": True,
        "authenticated": True,
        "message": "BACK_OFFICE_AUTHENTICATED",
    }


@app.get("/api/back-office/payment-channels")
def back_office_payment_channels_endpoint(
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    return {
        "ok": True,
        "channels": [
            {
                "channel": "bank_transfer",
                "label": "Bank Transfer",
                "available": True,
            }
        ],
    }


@app.get("/api/back-office/product")
def back_office_product_endpoint(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    payload = from_json(
        product.get(
            "document_payload"
        )
    )

    return {
        "ok": True,
        "product": public_product(product),
        "document_payload": payload,
        "document_text": clean(
            product.get(
                "document_text"
            )
        ),
    }


@app.get("/api/back-office/products")
def back_office_products(
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    with db(PRODUCT_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY created_at DESC
            """
        ).fetchall()

    return {
        "ok": True,
        "products": [
            public_product(dict(row))
            for row in rows
        ],
        "count": len(rows),
    }


@app.get("/api/back-office/records")
def back_office_records(
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    with db(PRODUCT_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY created_at DESC
            """
        ).fetchall()

    records = []

    for row in rows:
        product = dict(row)

        business_key_value = clean(
            product.get(
                "business_key"
            )
        )

        payment = get_payment(
            business_key_value
        )

        saved = repair_saved_snapshot(
            product
        )

        records.append(
            {
                "product": public_product(
                    product
                ),
                "payment": payment,
                "saved": bool(saved),
                "payment_status": (
                    clean(
                        payment.get(
                            "payment_status"
                        )
                    )
                    if payment
                    else "not_reported"
                ),
                "download_unlocked": bool(
                    product.get(
                        "download_unlocked"
                    )
                ),
            }
        )

    return {
        "ok": True,
        "records": records,
        "count": len(records),
    }


@app.get("/api/back-office/payment")
def back_office_payment_endpoint(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    return {
        "ok": True,
        "product": public_product(product),
        "payment": get_payment(
            clean(
                product.get(
                    "business_key"
                )
            )
        ),
    }


@app.get("/api/back-office/document")
def back_office_document(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    saved = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,
        "service": clean(
            product.get("service")
        ),
        "document_title": clean(
            product.get(
                "document_title"
            )
        ),
        "saved": bool(saved),
        "saved_path": (
            str(saved)
            if saved
            else ""
        ),
        "document_payload": from_json(
            product.get(
                "document_payload"
            )
        ),
        "document_text": clean(
            product.get(
                "document_text"
            )
        ),
    }


@app.get("/api/back-office/document-file")
def back_office_document_file(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    return {
        "ok": True,
        "saved": True,
        "filename": saved.name,
        "path": str(saved),
        "size": saved.stat().st_size,
        "content_type": (
            "application/pdf"
            if saved.suffix.lower() == ".pdf"
            else
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    }


@app.post("/api/back-office/verify-payment")
def back_office_verify_payment(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    business_key_value = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        business_key_value
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        business_key_value,
        payment_status="verified",
    )

    return {
        "ok": True,
        "message": "PAYMENT_VERIFIED",
        "product": public_product(product),
        "payment": payment,
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


@app.post("/api/back-office/activate-download")
def back_office_activate_download(
    body: BackOfficeActivateRequest,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
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

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    activated = activate_download(
        clean(
            product.get(
                "business_key"
            )
        )
    )

    if not activated:
        raise HTTPException(
            status_code=500,
            detail="DOWNLOAD_ACTIVATION_FAILED",
        )

    return {
        "ok": True,
        "message": "CUSTOMER_DOWNLOAD_ACTIVATED",
        "product": public_product(
            activated
        ),
        "saved_document": True,
        "download_unlocked": True,
        "download_url": download_url(
            body.service,
            body.document_title,
        ),
    }


@app.post("/api/back-office/payment/reject")
def back_office_reject_payment(
    body: BackOfficeRejectRequest,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):
    require_back_office(
        admin_key
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

    business_key_value = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        business_key_value
    )

    if not payment:
        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        business_key_value,
        payment_status="rejected",
    )

    log_delivery(
        business_key_value=business_key_value,
        channel="payment",
        status="rejected",
        detail=clean(body.reason),
    )

    return {
        "ok": True,
        "message": "PAYMENT_REJECTED",
        "product": public_product(product),
        "payment": payment,
    }


# ============================================================
# CANONICAL ATTACHMENT RESPONSE
#
# THIS SECTION IS PRESERVED.
# ============================================================

def canonical_file_response(
    path: Path,
    attachment: bool = True,
) -> FileResponse:

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_NOT_FOUND",
        )

    media_type = (
        "application/pdf"
        if path.suffix.lower() == ".pdf"
        else
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )

    headers = {
        "Cache-Control":
            "no-store, no-cache, "
            "must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Content-Type-Options": "nosniff",
    }

    filename = safe_filename(
        path.name
    )

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
        headers=headers,
    )


# ============================================================
# CUSTOMER DOWNLOAD
#
# THIS SECTION IS PRESERVED.
# ============================================================

@app.get("/api/download")
def customer_download(
    service: str,
    title: str,
):
    """
    Customer download.

    Returns the exact canonical saved file.
    No fallback.
    No regeneration.
    """

    service_clean = clean(service)
    title_clean = clean(title)

    if not service_clean:
        raise HTTPException(
            status_code=400,
            detail="SERVICE_REQUIRED",
        )

    if not title_clean:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_TITLE_REQUIRED",
        )

    product = get_product(
        service_clean,
        title_clean,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    if not bool(
        product.get(
            "download_unlocked"
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="CUSTOMER_DOWNLOAD_LOCKED",
        )

    saved_path = repair_saved_snapshot(
        product
    )

    if saved_path is None:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    if not saved_path.exists():
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    if not saved_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_PATH_IS_NOT_FILE",
        )

    timestamp = now_iso()

    with db(PRODUCT_DB_PATH) as connection:
        connection.execute(
            """
            UPDATE document_products
            SET
                download_count=
                    download_count+1,
                downloaded_at=?,
                updated_at=?
            WHERE business_key=?
            """,
            (
                timestamp,
                timestamp,
                clean(
                    product.get(
                        "business_key"
                    )
                ),
            ),
        )

    return canonical_file_response(
        saved_path,
        attachment=True,
    )


# ============================================================
# CUSTOMER DELIVERY
# ============================================================

@app.get("/api/delivery/channels")
def customer_delivery_channels(
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

    return {
        "ok": True,
        "product": public_product(product),
        "channels": delivery_channels(
            product,
            request,
        ),
    }


@app.post("/api/delivery/prepare")
def prepare_customer_delivery(
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

    channels = delivery_channels(
        product,
        request,
    )

    channel = select_channel(
        channels,
        body.channel,
    )

    if not channel:
        raise HTTPException(
            status_code=400,
            detail="DELIVERY_CHANNEL_NOT_FOUND",
        )

    if (
        body.channel.lower() == "phone"
        and not bool(
            product.get(
                "download_unlocked"
            )
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="CUSTOMER_DOWNLOAD_LOCKED",
        )

    log_delivery(
        business_key_value=clean(
            product.get(
                "business_key"
            )
        ),
        channel=body.channel,
        status="prepared",
        recipient=clean(
            body.recipient
            or body.recipient_email
            or body.recipient_phone
        ),
    )

    return {
        "ok": True,
        "channel": channel.get(
            "channel"
        ),
        "label": channel.get(
            "label"
        ),
        "url": channel.get(
            "url"
        ),
    }


# ============================================================
# BACK OFFICE DELIVERY
# ============================================================

@app.get("/api/back-office/delivery-channels")
def get_back_office_delivery_channels(
    service: str,
    title: str,
    request: Request,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):

    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    return {
        "ok": True,
        "product": public_product(product),
        "saved_document": True,
        "channels": back_office_delivery_channels(
            product,
            request,
        ),
    }


@app.get("/api/back-office/delivery-file")
def back_office_delivery_file(
    token: str,
):

    record = validate_back_office_download_token(
        token
    )

    if not record:
        raise HTTPException(
            status_code=401,
            detail="INVALID_OR_EXPIRED_DOWNLOAD_TOKEN",
        )

    product = get_product_by_business_key(
        clean(
            record.get(
                "business_key"
            )
        )
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    record_back_office_token_download(
        token
    )

    log_delivery(
        business_key_value=clean(
            product.get(
                "business_key"
            )
        ),
        channel="back_office_phone",
        status="downloaded",
    )

    return canonical_file_response(
        saved,
        attachment=True,
    )


@app.get("/api/back-office/download")
def back_office_direct_download(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):

    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    log_delivery(
        business_key_value=clean(
            product.get(
                "business_key"
            )
        ),
        channel="back_office_download",
        status="downloaded",
    )

    return canonical_file_response(
        saved,
        attachment=True,
    )


@app.get("/api/back-office/delivery-history")
def back_office_delivery_history(
    service: str,
    title: str,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):

    require_back_office(
        admin_key
    )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    business_key_value = clean(
        product.get(
            "business_key"
        )
    )

    with db(PRODUCT_DB_PATH) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM delivery_events
            WHERE business_key=?
            ORDER BY created_at DESC
            """,
            (business_key_value,),
        ).fetchall()

    return {
        "ok": True,
        "business_key": business_key_value,
        "events": [
            dict(row)
            for row in rows
        ],
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
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    saved = repair_saved_snapshot(
        product
    )

    payment = get_payment(
        clean(
            product.get(
                "business_key"
            )
        )
    )

    return {
        "ok": True,
        "product": public_product(product),
        "saved": bool(saved),
        "saved_filename": (
            saved.name
            if saved
            else ""
        ),
        "payment": payment,
        "payment_status": (
            clean(
                payment.get(
                    "payment_status"
                )
            )
            if payment
            else "not_reported"
        ),
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
        "download_url": (
            download_url(
                service,
                title,
            )
            if product.get(
                "download_unlocked"
            )
            else ""
        ),
    }


# ============================================================
# HEALTH / ROOT
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "Naija Pocket Business Center",
        "version": APP_VERSION,
        "time": now_iso(),
    }


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "Naija Pocket Business Center",
        "version": APP_VERSION,
        "message": (
            "Payment and document delivery "
            "API is running."
        ),
    }


# ============================================================
# BACK OFFICE DELIVERY PREPARATION
# ============================================================

@app.post("/api/back-office/delivery")
def back_office_delivery(
    body: DeliveryRequest,
    request: Request,
    admin_key: Optional[str] = Header(
        default=None,
        alias="X-Back-Office-Key",
    ),
):

    require_back_office(
        admin_key
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

    saved = repair_saved_snapshot(
        product
    )

    if not saved:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_NOT_FOUND",
        )

    channels = back_office_delivery_channels(
        product,
        request,
    )

    channel = select_channel(
        channels,
        body.channel,
    )

    if not channel:
        raise HTTPException(
            status_code=400,
            detail="DELIVERY_CHANNEL_NOT_FOUND",
        )

    recipient = clean(
        body.recipient
        or body.recipient_email
        or body.recipient_phone
    )

    log_delivery(
        business_key_value=clean(
            product.get(
                "business_key"
            )
        ),
        channel=body.channel,
        status="prepared",
        recipient=recipient,
    )

    return {
        "ok": True,
        "message": "DELIVERY_PREPARED",
        "channel": channel.get(
            "channel"
        ),
        "label": channel.get(
            "label"
        ),
        "url": channel.get(
            "url"
        ),
        "recipient": recipient,
        "saved_document": True,
        "filename": saved.name,
    }


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
