"""
Naija Pocket Business Center
Complete payment, saved-document and delivery API.

CANONICAL RULE:
- Document saved once as canonical file.
- All customer downloads return that exact file.
- No regeneration during download.

CUSTOMER DOWNLOAD:
- Customer can download the already-saved reviewed document.
- Download is served directly by FastAPI FileResponse.
- No email delivery.
- No document regeneration during download.
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
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


APP_VERSION = "payment-product-canonical-v20-direct-download"

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = "NPBC-2026"

PUBLIC_API_BASE_URL = os.getenv(
    "PUBLIC_API_BASE_URL",
    "",
).strip()

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
    if value is None:
        return ""
    return str(value).strip()


def key_part(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        clean(value).casefold(),
    )


def business_key(
    service: Any,
    title: Any,
) -> str:
    return (
        f"{key_part(service)}::"
        f"{key_part(title)}"
    )


def first(*values: Any) -> str:
    for value in values:
        if clean(value):
            return clean(value)
    return ""


def db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        str(path),
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    return connection


def as_dict(
    row: Optional[sqlite3.Row],
) -> Optional[dict]:
    if row is None:
        return None
    return dict(row)


def to_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
    )


def from_json(
    value: Any,
    default: Any = None,
) -> Any:
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

    name = Path(
        clean(value) or default
    ).name

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

    if not name.lower().endswith(
        (".docx", ".pdf")
    ):
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
# DOCUMENT HELPERS
# ============================================================

def _strip_md(text: str) -> str:
    if not text:
        return ""

    text = str(text)

    text = re.sub(
        r"```.*?```",
        "",
        text,
        flags=re.DOTALL,
    )

    text = text.replace("`", "")

    text = re.sub(
        r"^\s{0,3}#{1,6}\s+",
        "",
        text,
        flags=re.MULTILINE,
    )

    text = re.sub(
        r"^\s*[-*_]{3,}\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    text = re.sub(
        r"^\s*>\s*",
        "",
        text,
        flags=re.MULTILINE,
    )

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

    text = re.sub(
        r"\[(.*?)\]\(.*?\)",
        r"\1",
        text,
    )

    text = re.sub(
        r"^\s*[-*+]\s+",
        "",
        text,
        flags=re.MULTILINE,
    )

    text = re.sub(
        r"^\s*\d+\.\s+",
        "",
        text,
        flags=re.MULTILINE,
    )

    text = text.replace(
        "|",
        " ",
    )

    text = re.sub(
        r" {2,}",
        " ",
        text,
    )

    return text.strip()


def normalize_pages(
    value: Any,
) -> list[str]:

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
                return normalize_pages(
                    value[key]
                )

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

    if clean(value):
        return [clean(value)]

    return []


def normalize_payload(
    value: Any,
) -> dict:

    if isinstance(value, dict):
        payload = value
    else:
        payload = {
            "document_text": clean(value)
        }

    raw_pages = (
        payload.get("pages")
        if payload.get("pages") is not None
        else payload.get("document_pages")
    )

    page_list = normalize_pages(
        raw_pages
    )

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


def clean_saved_document_payload(
    payload: dict,
) -> dict:

    normalized = normalize_payload(
        dict(payload or {})
    )

    cleaned_pages = []

    for page in (
        normalized.get("pages")
        or []
    ):

        page_lines = []

        for raw_line in str(page).splitlines():

            cleaned_line = _strip_md(
                raw_line
            )

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

            page_lines.append(
                cleaned_line
            )

        if page_lines:
            cleaned_pages.append(
                "\n".join(page_lines)
            )

    text = _strip_md(
        normalized.get(
            "document_text"
        )
        or ""
    )

    if not cleaned_pages and text:
        cleaned_pages = [text]

    if cleaned_pages:
        text = "\n\n".join(
            cleaned_pages
        )

    normalized["pages"] = cleaned_pages
    normalized["document_text"] = text
    normalized["page_count"] = len(
        cleaned_pages
    )

    return normalized


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_databases() -> None:

    with db(PRODUCT_DB_PATH) as connection:

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

    with db(PAYMENT_DB_PATH) as connection:

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

    with db(PRODUCT_DB_PATH) as connection:

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

    return as_dict(row)


def get_product_by_business_key(
    value: str,
) -> Optional[dict]:

    with db(PRODUCT_DB_PATH) as connection:

        row = connection.execute(
            """
            SELECT *
            FROM document_products
            WHERE business_key=?
            """,
            (clean(value),),
        ).fetchone()

    return as_dict(row)


def get_payment(
    value: str,
) -> Optional[dict]:

    with db(PAYMENT_DB_PATH) as connection:

        row = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE business_key=?
            """,
            (clean(value),),
        ).fetchone()

    return as_dict(row)


# ============================================================
# CANONICAL FILE
# ============================================================

def existing_saved_file(
    product: Optional[dict],
) -> Optional[Path]:

    if not product:
        return None

    raw = clean(
        product.get(
            "document_saved_path"
        )
    )

    if not raw:
        return None

    path = Path(raw)

    if not path.is_absolute():
        path = BASE_DIR / path

    if path.exists() and path.is_file():
        return path

    return None


def store_saved_path(
    business_key_value: str,
    saved_path: Path,
) -> None:

    timestamp = now_iso()

    with db(PRODUCT_DB_PATH) as connection:

        connection.execute(
            """
            UPDATE document_products
            SET
                document_saved_path=?,
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


# ============================================================
# DOCX CREATION
# ============================================================

def _docx_p(
    text: str,
) -> str:

    raw = str(text).replace(
        "\r",
        "",
    ).strip()

    if not raw:
        return ""

    return (
        "<w:p>"
        '<w:r><w:t xml:space="preserve">'
        f"{xml_escape(raw)}"
        "</w:t></w:r>"
        "</w:p>"
    )


def make_docx(
    path: Path,
    title: str,
    pages: list[str],
) -> Path:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    normalized = clean_saved_document_payload(
        {
            "pages": pages
        }
    )

    pages = normalized.get(
        "pages"
    ) or []

    if not pages:
        pages = [
            clean(title)
            or "Document"
        ]

    body = []

    for index, page in enumerate(
        pages
    ):

        if index:
            body.append(
                '<w:p><w:r><w:br '
                'w:type="page"/></w:r></w:p>'
            )

        for line in str(
            page
        ).splitlines():

            if line.strip():
                body.append(
                    _docx_p(line)
                )

    if not body:
        body.append(
            _docx_p(
                clean(title)
                or "Document"
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


def save_exact_snapshot(
    service: str,
    title: str,
    payload: dict,
    existing_product: Optional[dict] = None,
) -> Path:

    existing = existing_saved_file(
        existing_product
        or get_product(
            service,
            title,
        )
    )

    if existing:
        return existing

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    if folder.exists():

        candidates = [
            item
            for item in folder.iterdir()
            if (
                item.is_file()
                and item.suffix.lower()
                in {
                    ".docx",
                    ".pdf",
                }
            )
        ]

        if candidates:

            candidates.sort(
                key=lambda item:
                item.stat().st_mtime,
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
        normalized.get(
            "filename"
        )
        or title
    )

    if filename.lower().endswith(
        ".pdf"
    ):
        filename = (
            Path(filename).stem
            + ".docx"
        )

    target = folder / filename

    if target.exists() and target.is_file():
        return target

    return make_docx(
        target,
        title,
        normalized["pages"]
        or [
            normalized[
                "document_text"
            ]
        ],
    )


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
        if (
            item.is_file()
            and item.suffix.lower()
            in {
                ".docx",
                ".pdf",
            }
        )
    ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda item:
        item.stat().st_mtime,
        reverse=True,
    )

    selected = candidates[0]

    store_saved_path(
        clean(
            product.get(
                "business_key"
            )
        ),
        selected,
    )

    return selected


# ============================================================
# PRODUCT
# ============================================================

def extract_document_text(
    payload: dict,
) -> str:

    normalized = (
        clean_saved_document_payload(
            payload
        )
    )

    return clean(
        normalized.get(
            "document_text"
        )
    )


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

    service = clean(service)
    title = clean(title)

    if not service:
        raise HTTPException(
            status_code=400,
            detail="SERVICE_REQUIRED",
        )

    if not title:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_TITLE_REQUIRED",
        )

    key = business_key(
        service,
        title,
    )

    normalized = clean_saved_document_payload(
        payload
    )

    timestamp = now_iso()

    with db(PRODUCT_DB_PATH) as connection:

        existing = connection.execute(
            """
            SELECT *
            FROM document_products
            WHERE business_key=?
            """,
            (key,),
        ).fetchone()

        if existing:

            connection.execute(
                """
                UPDATE document_products
                SET
                    customer_name=
                        CASE WHEN ? <> ''
                        THEN ? ELSE customer_name END,

                    customer_email=
                        CASE WHEN ? <> ''
                        THEN ? ELSE customer_email END,

                    customer_phone=
                        CASE WHEN ? <> ''
                        THEN ? ELSE customer_phone END,

                    amount=
                        CASE WHEN ? <> ''
                        THEN ? ELSE amount END,

                    currency=
                        CASE WHEN ? <> ''
                        THEN ? ELSE currency END,

                    job_id=
                        CASE WHEN ? <> ''
                        THEN ? ELSE job_id END,

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
                    clean(currency) or "NGN",

                    clean(job_id),
                    clean(job_id),

                    to_json(normalized),

                    extract_document_text(
                        normalized
                    ),

                    timestamp,
                    key,
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
                    ?,?,?,?,?,?,?,?,?,?,?,?,
                    NULL,
                    0,
                    0,
                    ?,
                    ?
                )
                """,
                (
                    key,
                    service,
                    title,
                    clean(customer_name),
                    clean(customer_email),
                    clean(customer_phone),
                    clean(amount),
                    clean(currency) or "NGN",
                    clean(job_id),
                    to_json(normalized),
                    extract_document_text(
                        normalized
                    ),
                    "",
                    timestamp,
                    timestamp,
                ),
            )

    product = get_product(
        service,
        title,
    )

    if not product:
        raise HTTPException(
            status_code=500,
            detail="PRODUCT_SAVE_FAILED",
        )

    return product


# ============================================================
# PAYMENT
# ============================================================

def ensure_payment_record(
    product: dict,
) -> dict:

    key = clean(
        product.get(
            "business_key"
        )
    )

    existing = get_payment(key)

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
                ?,?,?,?,?,?,?,?,?,?,
                'pending',
                ?,?
            )
            """,
            (
                key,
                clean(
                    product.get(
                        "service"
                    )
                ),
                clean(
                    product.get(
                        "document_title"
                    )
                ),
                clean(
                    product.get(
                        "customer_name"
                    )
                ),
                clean(
                    product.get(
                        "customer_email"
                    )
                ),
                clean(
                    product.get(
                        "customer_phone"
                    )
                ),
                clean(
                    product.get(
                        "amount"
                    )
                ),
                clean(
                    product.get(
                        "currency"
                    )
                ) or "NGN",
                clean(
                    product.get(
                        "document_text"
                    )
                ),
                timestamp,
                timestamp,
            ),
        )

    payment = get_payment(key)

    if not payment:
        raise HTTPException(
            status_code=500,
            detail="PAYMENT_RECORD_SAVE_FAILED",
        )

    return payment


def update_payment(
    key: str,
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
        return get_payment(key)

    updates.append(
        "updated_at=?"
    )

    values.append(
        now_iso()
    )

    values.append(
        clean(key)
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

    return get_payment(key)


def activate_download(
    key: str,
) -> Optional[dict]:

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
                clean(key),
            ),
        )

    return get_product_by_business_key(
        key
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
            product.get(
                "business_key"
            )
        ),
        "service": clean(
            product.get(
                "service"
            )
        ),
        "document_title": clean(
            product.get(
                "document_title"
            )
        ),
        "customer_name": clean(
            product.get(
                "customer_name"
            )
        ),
        "customer_email": clean(
            product.get(
                "customer_email"
            )
        ),
        "customer_phone": clean(
            product.get(
                "customer_phone"
            )
        ),
        "amount": clean(
            product.get(
                "amount"
            )
        ),
        "currency": clean(
            product.get(
                "currency"
            )
        ) or "NGN",
        "job_id": clean(
            product.get(
                "job_id"
            )
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
        "download_unlocked": True,
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
            product.get(
                "created_at"
            )
        ),
        "updated_at": clean(
            product.get(
                "updated_at"
            )
        ),
    }


# ============================================================
# URL / DELIVERY
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

    return [
        {
            "channel": "phone",
            "label": "Download to Phone",
            "available": True,
            "url": download_url(
                clean(
                    product.get(
                        "service"
                    )
                ),
                clean(
                    product.get(
                        "document_title"
                    )
                ),
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


def select_channel(
    channels: list[dict],
    requested: str,
) -> Optional[dict]:

    requested = clean(
        requested
    ).lower()

    for channel in channels:

        if (
            clean(
                channel.get(
                    "channel"
                )
            ).lower()
            == requested
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
                clean(
                    business_key_value
                ),
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
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def create_payment(
    request: Request,
):

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

    service = first(
        incoming.get("service"),
        incoming.get("service_name"),
        incoming.get("serviceName"),
    )

    title = first(
        incoming.get("document_title"),
        incoming.get("documentTitle"),
        incoming.get("title"),
    )

    product_data = incoming.get(
        "product"
    )

    if isinstance(
        product_data,
        dict,
    ):

        service = service or first(
            product_data.get("service"),
            product_data.get("service_name"),
            product_data.get("serviceName"),
        )

        title = title or first(
            product_data.get("document_title"),
            product_data.get("documentTitle"),
            product_data.get("title"),
        )

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

        extracted = {}

        for field in (
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
        ):

            if field in incoming:
                extracted[field] = incoming[
                    field
                ]

        if extracted:
            document_payload = extracted

    if isinstance(
        document_payload,
        str,
    ):

        parsed = from_json(
            document_payload
        )

        if isinstance(
            parsed,
            dict,
        ):

            document_payload = parsed

        elif isinstance(
            parsed,
            list,
        ):

            document_payload = {
                "pages": parsed
            }

        else:

            document_payload = {
                "document_text":
                    clean(
                        document_payload
                    )
            }

    if isinstance(
        document_payload,
        list,
    ):

        document_payload = {
            "pages":
                document_payload
        }

    if not isinstance(
        document_payload,
        dict,
    ):

        document_payload = {}

    if not service:

        raise HTTPException(
            status_code=400,
            detail="SERVICE_REQUIRED",
        )

    if not title:

        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_TITLE_REQUIRED",
        )

    if not document_payload:

        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_PAYLOAD_REQUIRED",
        )

    product = upsert_product(
        service=service,
        title=title,
        payload=document_payload,

        customer_name=first(
            incoming.get("customer_name"),
            incoming.get("customerName"),
        ),

        customer_email=first(
            incoming.get("customer_email"),
            incoming.get("customerEmail"),
            incoming.get("email"),
        ),

        customer_phone=first(
            incoming.get("customer_phone"),
            incoming.get("customerPhone"),
            incoming.get("phone"),
        ),

        amount=first(
            incoming.get("amount"),
            incoming.get("total"),
            incoming.get("price"),
        ),

        currency=first(
            incoming.get("currency"),
            incoming.get("payment_currency"),
        ) or "NGN",

        job_id=first(
            incoming.get("job_id"),
            incoming.get("jobId"),
        ),
    )

    saved = existing_saved_file(
        product
    )

    if saved is None:

        saved = save_exact_snapshot(
            service,
            title,
            document_payload,
            product,
        )

        store_saved_path(
            clean(
                product.get(
                    "business_key"
                )
            ),
            saved,
        )

        product = get_product(
            service,
            title,
        )

    payment = ensure_payment_record(
        product
    )

    return {
        "ok": True,
        "message":
            "PAYMENT_PREPARED",
        "product":
            public_product(product),
        "payment":
            payment,
        "payment_verified":
            False,
        "download_unlocked":
            True,
        "download_url":
            download_url(
                service,
                title,
                request,
            ),
    }


# ============================================================
# PAYMENT REPORT
# ============================================================

@app.post("/api/payment/report")
def report_payment(
    body: PaymentReportRequest,
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
            clean(
                body.reported_at
            )
            or now_iso()
        ),
    )

    return {
        "ok": True,
        "message":
            "PAYMENT_REPORTED",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            True,
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
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            True,
        "download_url":
            download_url(
                service,
                title,
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

    key = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        key
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        key,
        payment_status="completed",
        completed_at=now_iso(),
    )

    return {
        "ok": True,
        "message":
            "PAYMENT_COMPLETED",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            True,
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


@app.post(
    "/api/customer-care/verify-payment"
)
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

    key = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        key
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        key,
        payment_status="verified",
    )

    return {
        "ok": True,
        "message":
            "PAYMENT_VERIFIED",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            True,
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
        "authenticated":
            True,
        "message":
            "BACK_OFFICE_AUTHENTICATED",
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
            public_product(
                dict(row)
            )
            for row in rows
        ],
        "count":
            len(rows),
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

    return {
        "ok": True,
        "product":
            public_product(product),
        "document_payload":
            from_json(
                product.get(
                    "document_payload"
                )
            ),
        "document_text":
            clean(
                product.get(
                    "document_text"
                )
            ),
    }


@app.post(
    "/api/back-office/verify-payment"
)
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

    payment = get_payment(
        clean(
            product.get(
                "business_key"
            )
        )
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        clean(
            product.get(
                "business_key"
            )
        ),
        payment_status="verified",
    )

    return {
        "ok": True,
        "message":
            "PAYMENT_VERIFIED",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            True,
    }


@app.post(
    "/api/back-office/activate-download"
)
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

    saved = existing_saved_file(
        product
    )

    if not saved:

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

    return {
        "ok": True,
        "message":
            "CUSTOMER_DOWNLOAD_ACTIVATED",
        "product":
            public_product(activated),
        "saved_document":
            True,
        "download_unlocked":
            True,
        "download_url":
            download_url(
                body.service,
                body.document_title,
            ),
    }


@app.post(
    "/api/back-office/payment/reject"
)
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

    key = clean(
        product.get(
            "business_key"
        )
    )

    payment = get_payment(
        key
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="PAYMENT_NOT_FOUND",
        )

    payment = update_payment(
        key,
        payment_status="rejected",
    )

    log_delivery(
        business_key_value=key,
        channel="payment",
        status="rejected",
        detail=clean(
            body.reason
        ),
    )

    return {
        "ok": True,
        "message":
            "PAYMENT_REJECTED",
        "product":
            public_product(product),
        "payment":
            payment,
    }


# ============================================================
# FIXED DIRECT FILE DOWNLOAD
# ============================================================

def canonical_file_response(
    path: Path,
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

    suffix = path.suffix.lower()

    if suffix == ".pdf":

        media_type = (
            "application/pdf"
        )

    elif suffix == ".docx":

        media_type = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    else:

        media_type = (
            "application/octet-stream"
        )

    filename = safe_filename(
        path.name
    )

    # Explicit attachment response.
    #
    # This is deliberately supplied directly instead of
    # relying only on Starlette's automatic header handling.
    #
    # Android Chrome therefore receives:
    #
    # Content-Type: actual document type
    # Content-Disposition: attachment
    # Cache-Control: no-store
    #
    headers = {
        "Content-Disposition":
            f'attachment; filename="{filename}"',

        "Cache-Control":
            "no-store, no-cache, "
            "must-revalidate, "
            "proxy-revalidate, "
            "max-age=0",

        "Pragma":
            "no-cache",

        "Expires":
            "0",

        "X-Content-Type-Options":
            "nosniff",

        "Accept-Ranges":
            "bytes",
    }

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
        headers=headers,
    )


@app.get("/api/download")
def customer_download(
    service: str,
    title: str,
):

    service_clean = clean(
        service
    )

    title_clean = clean(
        title
    )

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

    # IMPORTANT:
    #
    # Only use the already-saved canonical document.
    #
    # There is deliberately:
    #
    # - no payment check
    # - no download_unlocked check
    # - no document generation
    # - no document regeneration
    #
    saved_path = existing_saved_file(
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
                download_count =
                    download_count + 1,
                downloaded_at = ?,
                updated_at = ?
            WHERE business_key = ?
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

    log_delivery(
        business_key_value=clean(
            product.get(
                "business_key"
            )
        ),
        channel="phone",
        status="downloaded",
    )

    # DIRECT FILE DELIVERY.
    #
    # Android Chrome receives the actual DOCX here.
    return canonical_file_response(
        saved_path
    )


# ============================================================
# CUSTOMER DELIVERY
# ============================================================

@app.get(
    "/api/delivery/channels"
)
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
        "product":
            public_product(product),
        "channels":
            delivery_channels(
                product,
                request,
            ),
    }


@app.post(
    "/api/delivery/prepare"
)
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
        "channel":
            channel.get(
                "channel"
            ),
        "label":
            channel.get(
                "label"
            ),
        "url":
            channel.get(
                "url"
            ),
    }


# ============================================================
# PRODUCT STATUS
# ============================================================

@app.get(
    "/api/product/status"
)
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

    saved = existing_saved_file(
        product
    )

    if saved is None:
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

        "product":
            public_product(product),

        "saved":
            bool(saved),

        "saved_filename":
            saved.name
            if saved
            else "",

        "payment":
            payment,

        "payment_status":
            (
                clean(
                    payment.get(
                        "payment_status"
                    )
                )
                if payment
                else "not_reported"
            ),

        "download_unlocked":
            True,

        "download_url":
            download_url(
                service,
                title,
            ),
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "ok": True,
        "service":
            "Naija Pocket Business Center",
        "version":
            APP_VERSION,
        "time":
            now_iso(),
    }


@app.get("/")
def root():

    return {
        "ok": True,
        "service":
            "Naija Pocket Business Center",
        "version":
            APP_VERSION,
        "message":
            "Payment and document delivery API is running.",
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
