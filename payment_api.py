"""Naija Pocket Business Center - complete payment, saved-document and delivery API."""

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


# ============================================================
# CONFIGURATION
# ============================================================

APP_VERSION = "payment-product-first-v11-back-office-document-read-download"

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = "NPBC-2026"

PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "").strip()


# ============================================================
# APP
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
    ).strip(" .")

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
    ).strip(" .")

    return name[:120] or default


# ============================================================
# DOCUMENT PAYLOAD NORMALIZATION
# ============================================================

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
        else {
            "document_text": clean(value)
        }
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


# ============================================================
# CLEAN SAVED DOCUMENT CONTENT
# ============================================================

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

    found = []

    for marker in markers:

        position = text.find(marker)

        if position >= 0:
            found.append(position)

    if found:
        text = text[:min(found)]

    output = []

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

        if re.fullmatch(
            r"Page\s+\d+\s+of\s+\d+",
            line,
            re.I,
        ):
            continue

        output.append(line)

    return output


def clean_saved_document_payload(payload: dict) -> dict:

    payload = dict(payload or {})

    raw_pages = payload.get("pages")

    if not isinstance(raw_pages, list):

        raw_pages = (
            [raw_pages]
            if raw_pages
            else []
        )

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

    payload["page_count"] = (
        len(pages)
        if pages
        else (
            1
            if document_text
            else 0
        )
    )

    return payload


# ============================================================
# FILE HELPERS
# ============================================================

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


def find_saved_file_from_product(
    product: Optional[dict],
) -> Optional[Path]:

    saved = existing_saved_file(product)

    if saved:
        return saved

    if not product:
        return None

    service = clean(product.get("service"))
    title = clean(product.get("document_title"))

    if not service or not title:
        return None

    folder = (
        DOWNLOAD_DIR
        / safe_folder(service)
        / safe_folder(title)
    )

    if not folder.exists():
        return None

    filename = clean(
        product.get("document_filename")
    )

    if filename:

        candidate = (
            folder
            / safe_filename(filename)
        )

        if candidate.exists() and candidate.is_file():
            return candidate

        if candidate.suffix.lower() == ".pdf":

            candidate = (
                folder
                / (
                    candidate.stem
                    + ".docx"
                )
            )

            if candidate.exists() and candidate.is_file():
                return candidate

    files = sorted(
        [
            item
            for item in folder.iterdir()
            if item.is_file()
            and item.suffix.lower()
            in {".docx", ".pdf"}
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    return files[0] if files else None


# ============================================================
# READ ACTUAL SAVED DOCX
# ============================================================

def read_saved_docx(path: Path) -> dict:

    if not path.exists() or not path.is_file():
        return {
            "pages": [],
            "document_text": "",
            "page_count": 0,
        }

    if path.suffix.lower() != ".docx":
        return {
            "pages": [],
            "document_text": "",
            "page_count": 0,
        }

    try:

        with zipfile.ZipFile(
            path,
            "r",
        ) as archive:

            xml = archive.read(
                "word/document.xml"
            ).decode(
                "utf-8",
                errors="replace",
            )

    except Exception:
        return {
            "pages": [],
            "document_text": "",
            "page_count": 0,
        }

    xml = re.sub(
        r"<w:tab[^>]*/>",
        "\t",
        xml,
    )

    xml = re.sub(
        r"<w:br[^>]*/>",
        "\n",
        xml,
    )

    xml = re.sub(
        r"</w:p>",
        "\n",
        xml,
    )

    xml = re.sub(
        r"</w:tr>",
        "\n",
        xml,
    )

    xml = re.sub(
        r"<[^>]+>",
        "",
        xml,
    )

    xml = (
        xml.replace(
            "&amp;",
            "&",
        )
        .replace(
            "&lt;",
            "<",
        )
        .replace(
            "&gt;",
            ">",
        )
        .replace(
            "&quot;",
            '"',
        )
        .replace(
            "&apos;",
            "'",
        )
    )

    text = xml.replace(
        "\r",
        "",
    )

    lines = []

    for line in text.split("\n"):

        line = line.strip()

        if not line:
            continue

        if line == "***":
            continue

        lines.append(line)

    document_text = "\n".join(lines).strip()

    if not document_text:
        return {
            "pages": [],
            "document_text": "",
            "page_count": 0,
        }

    return {
        "pages": [document_text],
        "document_text": document_text,
        "page_count": 1,
    }


def read_actual_saved_document(
    product: dict,
) -> dict:

    saved = find_saved_file_from_product(
        product
    )

    if saved and saved.suffix.lower() == ".docx":

        content = read_saved_docx(saved)

        if content["document_text"]:

            return content

    payload = clean_saved_document_payload(
        from_json(
            product.get(
                "document_payload"
            ),
            {},
        )
    )

    return {
        "pages": payload["pages"],
        "document_text": payload[
            "document_text"
        ],
        "page_count": payload[
            "page_count"
        ],
    }


# ============================================================
# DOCX CREATION
# ============================================================

def _docx_p(text: str) -> str:

    return (
        '<w:p>'
        '<w:r>'
        '<w:t xml:space="preserve">'
        + xml_escape(text)
        + "</w:t>"
        "</w:r>"
        "</w:p>"
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

    cleaned = clean_saved_document_payload(
        {
            "pages": page_list
        }
    )

    pages = cleaned.get(
        "pages",
        [],
    )

    if not pages:
        raise HTTPException(
            status_code=400,
            detail="SAVED_DOCUMENT_CONTENT_MISSING",
        )

    body = []

    for index, page in enumerate(pages):

        if index:
            body.append(
                '<w:p>'
                '<w:r>'
                '<w:br w:type="page"/>'
                "</w:r>"
                "</w:p>"
            )

        for line in page.splitlines():

            if line.strip():
                body.append(
                    _docx_p(line)
                )

    document_xml = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        + "".join(body)
        +
        '<w:sectPr>'
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar '
        'w:top="1440" '
        'w:right="1440" '
        'w:bottom="1440" '
        'w:left="1440"/>'
        '</w:sectPr>'
        '</w:body>'
        '</w:document>'
    )

    content_types = (
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
        '<Types '
        'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override '
        'PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" '
        'encoding="UTF-8"?>'
        '<Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship '
        'Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
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
# DATABASE INITIALIZATION
# ============================================================

def init_databases() -> None:

    connection = db(
        PRODUCT_DB_PATH
    )

    connection.execute(
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
            details TEXT
        )
        """
    )

    connection.commit()
    connection.close()

    connection = db(
        PAYMENT_DB_PATH
    )

    connection.execute(
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

    connection.commit()
    connection.close()


@app.on_event("startup")
def startup() -> None:
    init_databases()


# ============================================================
# DATABASE LOOKUPS
# ============================================================

def get_product(
    service: str,
    title: str,
) -> Optional[dict]:

    connection = db(
        PRODUCT_DB_PATH
    )

    row = connection.execute(
        """
        SELECT *
        FROM document_products
        WHERE business_key = ?
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


def get_single_product() -> Optional[dict]:

    connection = db(
        PRODUCT_DB_PATH
    )

    row = connection.execute(
        """
        SELECT *
        FROM document_products
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()

    connection.close()

    return as_dict(row)


def get_product_or_single(
    service: str,
    title: str,
) -> Optional[dict]:

    return (
        get_product(
            service,
            title,
        )
        or get_single_product()
    )


def get_payment(
    service: str,
    title: str,
) -> Optional[dict]:

    connection = db(
        PAYMENT_DB_PATH
    )

    row = connection.execute(
        """
        SELECT *
        FROM payment_orders
        WHERE business_key = ?
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


# ============================================================
# SAVED DOCUMENT REPAIR
# ============================================================

def save_exact_snapshot(
    service: str,
    title: str,
    payload: dict,
    existing_product: Optional[dict] = None,
) -> Path:

    existing = find_saved_file_from_product(
        existing_product
    )

    if existing:
        return existing

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
        normalized["filename"]
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
        title,
        normalized["pages"]
        or [normalized["document_text"]],
    )


def store_saved_path(
    service: str,
    title: str,
    path: Path,
) -> None:

    connection = db(
        PRODUCT_DB_PATH
    )

    timestamp = now_iso()

    connection.execute(
        """
        UPDATE document_products
        SET document_saved_path = ?,
            document_saved_at = COALESCE(
                document_saved_at,
                ?
            ),
            updated_at = ?
        WHERE business_key = ?
        """,
        (
            str(path),
            timestamp,
            timestamp,
            business_key(
                service,
                title,
            ),
        ),
    )

    connection.commit()
    connection.close()


def extract_document_text(
    product: dict,
) -> str:

    actual = read_actual_saved_document(
        product
    )

    if actual["document_text"]:
        return actual["document_text"]

    return ""


def repair_saved_snapshot(
    product: dict,
) -> dict:

    service = clean(
        product.get("service")
    )

    title = clean(
        product.get("document_title")
    )

    current = (
        get_product(
            service,
            title,
        )
        or product
    )

    saved = find_saved_file_from_product(
        current
    )

    if saved:

        current_path = clean(
            current.get(
                "document_saved_path"
            )
        )

        if current_path != str(saved):

            store_saved_path(
                service,
                title,
                saved,
            )

            current = (
                get_product(
                    service,
                    title,
                )
                or current
            )

        return current

    payload = normalize_payload(
        from_json(
            current.get(
                "document_payload"
            ),
            {},
        )
    )

    if (
        not payload["pages"]
        and not payload["document_text"]
    ):

        payment = get_payment(
            service,
            title,
        )

        if payment:

            payload = normalize_payload(
                {
                    "document_text": payment.get(
                        "document_text"
                    ),
                    "filename": payment.get(
                        "document_filename"
                    ),
                    "document_version": payment.get(
                        "document_version"
                    ),
                }
            )

    if (
        not payload["pages"]
        and not payload["document_text"]
    ):

        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_CONTENT_MISSING",
        )

    path = save_exact_snapshot(
        service,
        title,
        payload,
        current,
    )

    store_saved_path(
        service,
        title,
        path,
    )

    return (
        get_product(
            service,
            title,
        )
        or current
    )


# ============================================================
# PRODUCT
# ============================================================

def upsert_product(data: dict) -> dict:

    service = clean(
        data.get("service")
    )

    title = clean(
        data.get("document_title")
    )

    if not service or not title:

        raise HTTPException(
            status_code=400,
            detail="SERVICE_AND_TITLE_REQUIRED",
        )

    raw_payload = data.get(
        "document_payload"
    )

    payload = normalize_payload(
        raw_payload
        if raw_payload is not None
        else data
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

    connection = db(
        PRODUCT_DB_PATH
    )

    if old:

        connection.execute(
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
                    WHEN COALESCE(
                        document_saved_path,
                        ''
                    )=''
                    THEN ?
                    ELSE document_payload
                END,
                updated_at=?
            WHERE business_key=?
            """,
            (
                clean(
                    data.get(
                        "customer_name"
                    )
                ),
                clean(
                    data.get(
                        "customer_id"
                    )
                ),
                float(
                    data.get("amount")
                    or 0
                ),
                clean(
                    data.get("currency")
                )
                or "NGN",
                payload[
                    "document_version"
                ],
                payload[
                    "filename"
                ],
                payload[
                    "page_count"
                ],
                to_json(payload),
                timestamp,
                key,
            ),
        )

    else:

        connection.execute(
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
            VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                service,
                title,
                clean(
                    data.get(
                        "customer_name"
                    )
                ),
                clean(
                    data.get(
                        "customer_id"
                    )
                ),
                float(
                    data.get("amount")
                    or 0
                ),
                clean(
                    data.get("currency")
                )
                or "NGN",
                payload[
                    "document_version"
                ],
                payload[
                    "filename"
                ],
                payload[
                    "page_count"
                ],
                to_json(payload),
                timestamp,
                timestamp,
            ),
        )

    connection.commit()
    connection.close()

    return (
        get_product(
            service,
            title,
        )
        or {}
    )


# ============================================================
# PAYMENT
# ============================================================

def ensure_payment_record(
    product: dict,
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

    old = get_payment(
        service,
        title,
    )

    timestamp = now_iso()

    values = (
        clean(
            product.get(
                "customer_name"
            )
        ),
        clean(
            product.get(
                "customer_id"
            )
        ),
        float(
            product.get("amount")
            or 0
        ),
        clean(
            product.get("currency")
        )
        or "NGN",
        clean(
            product.get(
                "document_version"
            )
        ),
        clean(
            product.get(
                "document_filename"
            )
        ),
        int(
            product.get(
                "document_pages"
            )
            or 0
        ),
        extract_document_text(
            product
        ),
    )

    connection = db(
        PAYMENT_DB_PATH
    )

    if old:

        connection.execute(
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
            values
            + (
                timestamp,
                key,
            ),
        )

    else:

        connection.execute(
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
            VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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

    connection.commit()
    connection.close()

    return (
        get_payment(
            service,
            title,
        )
        or {}
    )


def update_payment(
    service: str,
    title: str,
    status: Optional[str] = None,
    **fields: Any,
) -> Optional[dict]:

    assignments = []
    values = []

    if status:

        assignments.append(
            "payment_status=?"
        )

        values.append(status)

    for name, value in fields.items():

        if name in {
            "reported_at",
            "verified_at",
            "rejected_at",
            "notes",
            "payment_method",
        }:

            assignments.append(
                f"{name}=?"
            )

            values.append(value)

    assignments.append(
        "updated_at=?"
    )

    values.append(
        now_iso()
    )

    values.append(
        business_key(
            service,
            title,
        )
    )

    connection = db(
        PAYMENT_DB_PATH
    )

    connection.execute(
        f"""
        UPDATE payment_orders
        SET {', '.join(assignments)}
        WHERE business_key=?
        """,
        values,
    )

    connection.commit()
    connection.close()

    return get_payment(
        service,
        title,
    )


# ============================================================
# PUBLIC PRODUCT
# ============================================================

def public_product(
    product: Optional[dict],
) -> dict:

    if not product:

        return {
            "found": False,
            "saved_document": False,
            "download_unlocked": False,
        }

    saved = find_saved_file_from_product(
        product
    )

    payment = get_payment(
        clean(product.get("service")),
        clean(product.get("document_title")),
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
        "document_saved_path": (
            str(saved)
            if saved
            else clean(
                product.get(
                    "document_saved_path"
                )
            )
        ),
        "document_saved_at": product.get(
            "document_saved_at"
        ),
        "saved_document": bool(saved),
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
        "activated_at": product.get(
            "activated_at"
        ),
        "download_count": product.get(
            "download_count"
        )
        or 0,
        "payment_status": (
            payment.get("payment_status")
            if payment
            else "payment_ready"
        ),
        "payment_reported": bool(
            payment
            and payment.get(
                "reported_at"
            )
        ),
        "payment_verified": bool(
            payment
            and payment.get(
                "verified_at"
            )
        ),
        "payment_rejected": bool(
            payment
            and payment.get(
                "rejected_at"
            )
        ),
    }


def back_office_product(
    product: Optional[dict],
) -> dict:

    if not product:

        result = public_product(
            None
        )

        result.update(
            {
                "pages": [],
                "document_text": "",
                "page_count": 0,
                "document_ready_for_back_office": False,
            }
        )

        return result

    product = (
        repair_saved_snapshot(product)
    )

    result = public_product(
        product
    )

    document = read_actual_saved_document(
        product
    )

    result.update(
        {
            "pages": document[
                "pages"
            ],
            "document_text": document[
                "document_text"
            ],
            "page_count": document[
                "page_count"
            ],
            "document_ready_for_back_office": bool(
                find_saved_file_from_product(
                    product
                )
            ),
        }
    )

    return result


# ============================================================
# URLS
# ============================================================

def api_base(
    request: Request,
) -> str:

    return (
        PUBLIC_API_BASE_URL.rstrip("/")
        or str(
            request.base_url
        ).rstrip("/")
    )


def download_url(
    request: Request,
    service: str,
    title: str,
) -> str:

    return (
        f"{api_base(request)}"
        f"/api/download?service="
        f"{urllib.parse.quote(service)}"
        f"&title="
        f"{urllib.parse.quote(title)}"
    )


# ============================================================
# CUSTOMER DELIVERY CHANNELS
# ============================================================

def delivery_channels(
    request: Request,
    product: dict,
) -> dict:

    service = clean(
        product.get("service")
    )

    title = clean(
        product.get("document_title")
    )

    direct = download_url(
        request,
        service,
        title,
    )

    share_text = (
        f"{title} — {service}\n"
        "Naija Pocket Business Center document download:\n"
        f"{direct}"
    )

    unlocked = bool(
        product.get(
            "download_unlocked"
        )
    )

    saved = bool(
        find_saved_file_from_product(
            product
        )
    )

    return {
        "available": unlocked,
        "document_saved": saved,
        "channels": [
            {
                "id": "phone",
                "name": "Download to Phone",
                "type": "download",
                "available": unlocked,
                "url": direct,
            },
            {
                "id": "whatsapp",
                "name": "WhatsApp",
                "type": "share",
                "available": unlocked,
                "url":
                    "https://wa.me/?text="
                    + urllib.parse.quote(
                        share_text
                    ),
            },
            {
                "id": "email",
                "name": "Email",
                "type": "share",
                "available": unlocked,
                "url":
                    "mailto:?subject="
                    + urllib.parse.quote(
                        title
                        + " — Naija Pocket Business Center"
                    )
                    + "&body="
                    + urllib.parse.quote(
                        share_text
                    ),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "share",
                "available": unlocked,
                "url":
                    "https://t.me/share/url?url="
                    + urllib.parse.quote(
                        direct
                    )
                    + "&text="
                    + urllib.parse.quote(
                        title
                    ),
            },
            {
                "id": "google_drive",
                "name": "Google Drive",
                "type": "share",
                "available": unlocked,
                "url":
                    "https://drive.google.com/drive/my-drive",
                "note":
                    "Download the exact saved file first, then upload that same file to Google Drive.",
            },
        ],
    }


# ============================================================
# BACK OFFICE DELIVERY
# ============================================================

def back_office_delivery_channels(
    request: Request,
    product: dict,
) -> dict:

    service = clean(
        product.get("service")
    )

    title = clean(
        product.get("document_title")
    )

    file_url = (
        f"{api_base(request)}"
        f"/api/back-office/delivery-file?service="
        f"{urllib.parse.quote(service)}"
        f"&title="
        f"{urllib.parse.quote(title)}"
    )

    share_text = (
        f"{title} — {service}\n"
        "Naija Pocket Business Center document "
        "is ready for Back Office delivery.\n"
        f"Download the saved file: {file_url}"
    )

    saved = bool(
        find_saved_file_from_product(
            product
        )
    )

    return {
        "available": saved,
        "document_saved": saved,
        "customer_download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
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
                "url":
                    "https://wa.me/?text="
                    + urllib.parse.quote(
                        share_text
                    ),
            },
            {
                "id": "email",
                "name": "Email",
                "type": "share",
                "available": saved,
                "url":
                    "mailto:?subject="
                    + urllib.parse.quote(
                        title
                        + " — Naija Pocket Business Center"
                    )
                    + "&body="
                    + urllib.parse.quote(
                        share_text
                    ),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "share",
                "available": saved,
                "url":
                    "https://t.me/share/url?url="
                    + urllib.parse.quote(
                        file_url
                    )
                    + "&text="
                    + urllib.parse.quote(
                        title
                    ),
            },
            {
                "id": "google_drive",
                "name": "Google Drive",
                "type": "share",
                "available": saved,
                "url":
                    "https://drive.google.com/drive/my-drive",
                "note":
                    "Download the exact saved file first, then upload that same file to Google Drive.",
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
            for item in data.get(
                "channels",
                [],
            )
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

    connection = db(
        PRODUCT_DB_PATH
    )

    connection.execute(
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
                product.get(
                    "document_title"
                ),
            ),
            clean(
                product.get("service")
            ),
            clean(
                product.get(
                    "document_title"
                )
            ),
            channel,
            status,
            now_iso(),
            to_json(
                details or {}
            ),
        ),
    )

    connection.commit()
    connection.close()


# ============================================================
# BACK OFFICE AUTHENTICATION
# ============================================================

def require_back_office(
    key: str,
) -> None:

    if clean(key) != BACK_OFFICE_ADMIN_KEY:

        raise HTTPException(
            status_code=401,
            detail="INVALID_BACK_OFFICE_KEY",
        )


# ============================================================
# REQUEST MODELS
# ============================================================

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


# ============================================================
# MAKE PAYMENT
# ============================================================

@app.post("/api/payment/create")
def payment_create(
    body: PaymentCreateRequest,
    request: Request,
):

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

    incoming = from_json(
        body.document_payload,
        {},
    )

    if not isinstance(incoming, dict):
        incoming = {}

    if not incoming:

        incoming = {
            "pages":
                body.pages
                if body.pages is not None
                else body.document_pages,

            "page_text":
                body.page_text,

            "document_text":
                first(
                    body.document_text,
                    body.documentText,
                    body.text,
                    body.content,
                ),

            "filename":
                first(
                    body.filename,
                    body.document_filename,
                ),

            "document_version":
                body.document_version,
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

    payload = normalize_payload(
        incoming
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
            "customer_name":
                body.customer_name,
            "customer_id":
                body.customer_id,
            "amount":
                body.amount,
            "currency":
                body.currency or "NGN",
            "document_version":
                body.document_version,
            "document_payload":
                payload,
        }
    )

    saved = find_saved_file_from_product(
        product
    )

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

        product = (
            get_product(
                service,
                title,
            )
            or product
        )

    payment = ensure_payment_record(
        product
    )

    return {
        "ok": True,
        "message":
            "Payment prepared successfully. "
            "Your exact reviewed document is saved.",
        "product":
            public_product(product),
        "payment":
            payment,
        "saved_document":
            True,
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
        "delivery":
            delivery_channels(
                request,
                product,
            ),
    }


# ============================================================
# I HAVE MADE PAYMENT
# ============================================================

@app.post("/api/payment/report")
def payment_report(
    body: PaymentReportRequest,
):

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

    product = repair_saved_snapshot(
        product
    )

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
        "message":
            "Payment reported. "
            "Back Office will verify it.",
        "product":
            public_product(product),
        "payment":
            payment,
        "saved_document":
            True,
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

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
            "product":
                public_product(None),
            "payment": None,
        }

    product = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,
        "found": True,
        "product":
            public_product(product),
        "payment":
            get_payment(
                service,
                title,
            ),
        "delivery":
            delivery_channels(
                request,
                product,
            ),
    }


# ============================================================
# PAYMENT COMPLETE
# ============================================================

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

    product = repair_saved_snapshot(
        product
    )

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_verified",
        verified_at=now_iso(),
    )

    return {
        "ok": True,
        "message":
            "Payment marked verified.",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
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
        "ok": True,
        "authenticated": True,
        "message":
            "Back Office access granted.",
    }


# ============================================================
# BACK OFFICE PAYMENT CHANNELS
# ============================================================

def back_office_payment_channels(
    product: dict,
    payment: Optional[dict],
) -> list[dict]:

    method = clean(
        (payment or {}).get(
            "payment_method"
        )
        or "bank_transfer"
    ) or "bank_transfer"

    return [
        {
            "id": "bank_transfer",
            "name": "Bank Transfer",
            "type": "payment",
            "available": True,
            "selected":
                method == "bank_transfer",
        },
        {
            "id": "cash_manual",
            "name":
                "Cash / Manual Payment",
            "type": "payment",
            "available": True,
            "selected":
                method
                in {
                    "cash",
                    "manual",
                    "cash_manual",
                },
        },
        {
            "id": "recorded_method",
            "name":
                "Recorded Payment Method",
            "type": "payment",
            "available": True,
            "selected": True,
            "value": method,
        },
    ]


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
def back_office_payments(
    request: Request,
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

    rows = connection.execute(
        """
        SELECT *
        FROM document_products
        ORDER BY updated_at DESC, id DESC
        """
    ).fetchall()

    connection.close()

    items = []

    for row in rows:

        product = repair_saved_snapshot(
            dict(row)
        )

        payment = get_payment(
            product.get("service"),
            product.get(
                "document_title"
            ),
        )

        item = back_office_product(
            product
        )

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
            "saved":
                sum(
                    1
                    for item in items
                    if item.get(
                        "saved_document"
                    )
                ),
            "total_documents":
                len(items),
            "reported":
                sum(
                    1
                    for item in items
                    if item.get(
                        "payment_reported"
                    )
                ),
            "verified":
                sum(
                    1
                    for item in items
                    if item.get(
                        "payment_verified"
                    )
                ),
            "activated":
                sum(
                    1
                    for item in items
                    if item.get(
                        "download_unlocked"
                    )
                ),
        },
    }


# ============================================================
# BACK OFFICE JOBS
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

    rows = connection.execute(
        """
        SELECT *
        FROM document_products
        ORDER BY updated_at DESC, id DESC
        """
    ).fetchall()

    connection.close()

    return {
        "ok": True,
        "jobs": [
            dict(row)
            for row in rows
        ],
    }


# ============================================================
# BACK OFFICE PAYMENT
# ============================================================

@app.get("/api/back-office/payment")
def back_office_payment(
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    payment = get_payment(
        service,
        title,
    )

    return {
        "ok": True,
        "product":
            back_office_product(
                product
            ),
        "payment":
            payment,
        "payment_channels":
            back_office_payment_channels(
                product,
                payment,
            ),
        "delivery":
            back_office_delivery_channels(
                request,
                product,
            ),
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    saved = find_saved_file_from_product(
        product
    )

    document = read_actual_saved_document(
        product
    )

    return {
        "ok": True,
        "service":
            product["service"],
        "document_title":
            product["document_title"],
        "filename":
            product.get(
                "document_filename"
            ),
        "pages":
            document[
                "page_count"
            ],
        "saved_document":
            bool(saved),
        "saved_path":
            str(saved)
            if saved
            else "",
        "document_text":
            document[
                "document_text"
            ],
        "document_pages":
            document[
                "pages"
            ],
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    document = read_actual_saved_document(
        product
    )

    saved = find_saved_file_from_product(
        product
    )

    return {
        "ok": True,
        "service":
            product["service"],
        "document_title":
            product["document_title"],
        "filename":
            product.get(
                "document_filename"
            ),
        "pages":
            document[
                "pages"
            ],
        "page_count":
            document[
                "page_count"
            ],
        "document_text":
            document[
                "document_text"
            ],
        "saved_document":
            bool(saved),
        "saved_path":
            str(saved)
            if saved
            else "",
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    document = read_actual_saved_document(
        product
    )

    saved = find_saved_file_from_product(
        product
    )

    return {
        "ok": True,
        "service":
            product["service"],
        "document_title":
            product["document_title"],
        "filename":
            product.get(
                "document_filename"
            ),
        "pages":
            document[
                "pages"
            ],
        "page_count":
            document[
                "page_count"
            ],
        "document_text":
            document[
                "document_text"
            ],
        "saved_document":
            bool(saved),
        "saved_path":
            str(saved)
            if saved
            else "",
        "customer_download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# BACK OFFICE PAYMENT VERIFICATION
# ============================================================

@app.post("/api/back-office/payment/verify")
def back_office_payment_verify(
    body: PaymentCompleteRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
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

    product = repair_saved_snapshot(
        product
    )

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_verified",
        verified_at=now_iso(),
    )

    return {
        "ok": True,
        "message":
            "Payment verified. "
            "Activate download separately when ready.",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# BACK OFFICE PAYMENT CHANNELS
# ============================================================

@app.get("/api/back-office/payment-channels")
def back_office_payment_channels_endpoint(
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    payment = get_payment(
        service,
        title,
    )

    return {
        "ok": True,
        "service":
            product.get(
                "service"
            ),
        "document_title":
            product.get(
                "document_title"
            ),
        "payment":
            payment,
        "payment_channels":
            back_office_payment_channels(
                product,
                payment,
            ),
    }


# ============================================================
# BACK OFFICE ACTIVATE DOWNLOAD
# ============================================================

def activate_download(
    service: str,
    title: str,
) -> dict:

    connection = db(
        PRODUCT_DB_PATH
    )

    timestamp = now_iso()

    connection.execute(
        """
        UPDATE document_products
        SET download_unlocked=1,
            activated_at=?,
            updated_at=?
        WHERE business_key=?
        """,
        (
            timestamp,
            timestamp,
            business_key(
                service,
                title,
            ),
        ),
    )

    connection.commit()
    connection.close()

    return (
        get_product(
            service,
            title,
        )
        or {}
    )


@app.post("/api/back-office/activate-download")
def back_office_activate(
    body: BackOfficeActivateRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
    )

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

    product = repair_saved_snapshot(
        product
    )

    if not find_saved_file_from_product(
        product
    ):

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
        "message":
            "Download activated.",
        "product":
            public_product(product),
        "download_unlocked":
            True,
    }


# ============================================================
# BACK OFFICE REJECT PAYMENT
# ============================================================

@app.post("/api/back-office/payment/reject")
def back_office_reject(
    body: BackOfficeRejectRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
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

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_rejected",
        rejected_at=now_iso(),
        notes=body.reason,
    )

    return {
        "ok": True,
        "message":
            "Payment marked rejected.",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


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
            detail="PRODUCT_NOT_FOUND",
        )

    if not bool(
        product.get(
            "download_unlocked"
        )
    ):

        raise HTTPException(
            status_code=403,
            detail="DOWNLOAD_NOT_UNLOCKED",
        )

    product = repair_saved_snapshot(
        product
    )

    saved = find_saved_file_from_product(
        product
    )

    if not saved:

        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    connection = db(
        PRODUCT_DB_PATH
    )

    connection.execute(
        """
        UPDATE document_products
        SET download_count=
                COALESCE(
                    download_count,
                    0
                ) + 1,
            downloaded_at=?,
            updated_at=?
        WHERE business_key=?
        """,
        (
            now_iso(),
            now_iso(),
            business_key(
                service,
                title,
            ),
        ),
    )

    connection.commit()
    connection.close()

    if saved.suffix.lower() == ".docx":

        media = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    elif saved.suffix.lower() == ".pdf":

        media = "application/pdf"

    else:

        media = (
            "application/octet-stream"
        )

    return FileResponse(
        path=str(saved),
        filename=saved.name,
        media_type=media,
        content_disposition_type="attachment",
    )


# ============================================================
# CUSTOMER DELIVERY CHANNELS
# ============================================================

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

    product = repair_saved_snapshot(
        product
    )

    if not find_saved_file_from_product(
        product
    ):

        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    return {
        "ok": True,
        "product":
            public_product(product),
        **delivery_channels(
            request,
            product,
        ),
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

    product = repair_saved_snapshot(
        product
    )

    selected = select_channel(
        delivery_channels(
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

    log_delivery(
        product,
        selected["id"],
        "prepared",
        {
            "url":
                selected.get("url")
        },
    )

    return {
        "ok": True,
        "channel":
            selected,
        "product":
            public_product(product),
    }


# ============================================================
# BACK OFFICE DELIVERY CHANNELS
# ============================================================

@app.get("/api/back-office/delivery-channels")
def back_office_delivery_channels_endpoint(
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,
        "product":
            back_office_product(
                product
            ),
        "delivery":
            back_office_delivery_channels(
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
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

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
        {
            "url":
                selected.get("url")
        },
    )

    return {
        "ok": True,
        "channel":
            selected,
        "product":
            back_office_product(
                product
            ),
    }


# ============================================================
# BACK OFFICE ACTUAL FILE
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

    product = get_product(
        service,
        title,
    )

    if not product:

        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    product = repair_saved_snapshot(
        product
    )

    saved = find_saved_file_from_product(
        product
    )

    if not saved:

        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    if saved.suffix.lower() == ".docx":

        media = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    elif saved.suffix.lower() == ".pdf":

        media = "application/pdf"

    else:

        media = (
            "application/octet-stream"
        )

    return FileResponse(
        path=str(saved),
        filename=saved.name,
        media_type=media,
        content_disposition_type="attachment",
    )


# ============================================================
# DELIVERY HISTORY
# ============================================================

@app.get("/api/back-office/delivery-history")
def delivery_history(
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

    if service and title:

        rows = connection.execute(
            """
            SELECT *
            FROM delivery_events
            WHERE business_key=?
            ORDER BY created_at DESC, id DESC
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
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()

    connection.close()

    return {
        "ok": True,
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
            "product":
                public_product(None),
        }

    product = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,
        "found": True,
        "product":
            public_product(product),
        "delivery":
            delivery_channels(
                request,
                product,
            ),
    }


# ============================================================
# OLD BACK OFFICE PAYMENT LIST COMPATIBILITY
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

    rows = connection.execute(
        """
        SELECT *
        FROM payment_orders
        ORDER BY updated_at DESC, id DESC
        """
    ).fetchall()

    connection.close()

    return {
        "ok": True,
        "payments": [
            dict(row)
            for row in rows
        ],
    }


@app.post("/api/customer-care/payment/verify")
def customer_care_verify_compatibility(
    body: PaymentCompleteRequest,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
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

    product = repair_saved_snapshot(
        product
    )

    payment = update_payment(
        body.service,
        body.document_title,
        "payment_verified",
        verified_at=now_iso(),
    )

    return {
        "ok": True,
        "message":
            "Payment verified. "
            "Download still requires activation.",
        "product":
            public_product(product),
        "payment":
            payment,
        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# OLD BACK OFFICE DELIVERY POST COMPATIBILITY
# ============================================================

@app.post("/api/back-office/delivery")
def back_office_delivery_post(
    body: DeliveryRequest,
    request: Request,
    x_back_office_key: str = Header(
        default=""
    ),
):

    require_back_office(
        x_back_office_key
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

    product = repair_saved_snapshot(
        product
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

    log_delivery(
        product,
        selected["id"],
        "ready",
        {
            "url":
                selected.get("url")
        },
    )

    return {
        "ok": True,
        "status": "ready",
        "channel":
            selected,
        "product":
            back_office_product(
                product
            ),
        "message":
            "The exact saved document is ready "
            "for Back Office delivery through this channel.",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "ok": True,
        "service":
            "Naija Pocket Business Center Payment API",
        "version":
            APP_VERSION,
        "back_office_key_configured":
            True,
    }


@app.get("/")
def root():

    return {
        "ok": True,
        "service":
            "Naija Pocket Business Center Payment API",
        "version":
            APP_VERSION,
        "message":
            "Payment, saved-document, Back Office "
            "and delivery API is running.",
    }


# ============================================================
# START SERVER
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
