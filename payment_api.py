"""
Naija Pocket Business Center
Payment, saved-document and delivery API.

CANONICAL DOCUMENT RULE
-----------------------
The reviewed document is saved once as the canonical document file.

After that:
- Back Office reads the canonical saved file.
- Back Office downloads the canonical saved file.
- Customer downloads the canonical saved file.
- Delivery uses the canonical saved file.
- Payment verification never rebuilds the document.
- Activation never rebuilds the document.
- Delivery never rebuilds the document.
- document_payload is retained for compatibility/history only.
- Existing canonical files are never silently overwritten.
"""

from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
from email.message import EmailMessage

import json
import os
import re
import sqlite3
import smtplib
import urllib.parse
import zipfile

from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


# ============================================================
# APPLICATION
# ============================================================

APP_VERSION = "payment-product-first-v14-canonical-document-only"

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
PRODUCT_DB_PATH = BASE_DIR / "product_delivery.db"
PAYMENT_DB_PATH = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = os.getenv(
    "BACK_OFFICE_ADMIN_KEY",
    "NPBC-2026",
).strip()

PUBLIC_API_BASE_URL = os.getenv(
    "PUBLIC_API_BASE_URL",
    "",
).strip()

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
# BASIC HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def key_part(value: Any) -> str:
    value = clean(value).lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "unknown"


def business_key(
    customer_id: Any,
    product_id: Any,
    order_id: Any = "",
) -> str:
    return (
        f"{key_part(customer_id)}:"
        f"{key_part(product_id)}:"
        f"{key_part(order_id)}"
    )


def first(data: dict, *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in data:
            value = data.get(key)
            if value not in (None, ""):
                return value
    return default


def db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        str(path),
        timeout=30,
        check_same_thread=False,
    )

    connection.row_factory = sqlite3.Row

    return connection


def as_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None

    return dict(row)


def to_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=str,
    )


def from_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(value)
    except Exception:
        return default


def safe_filename(value: str, fallback: str = "document") -> str:
    value = clean(value)

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        value,
    )

    value = re.sub(r"\s+", " ", value).strip()

    value = value.rstrip(".")

    return value or fallback


def safe_folder(value: str) -> str:
    return safe_filename(
        value,
        fallback="service",
    )


# ============================================================
# DATABASE INITIALISATION
# ============================================================

def initialise_product_database() -> None:
    connection = db(PRODUCT_DB_PATH)

    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                product_id TEXT,
                customer_id TEXT,

                document_title TEXT,
                service_name TEXT,

                customer_name TEXT,
                customer_email TEXT,
                customer_phone TEXT,

                document_payload TEXT,
                document_saved_path TEXT,
                document_saved_at TEXT,

                amount INTEGER DEFAULT 0,
                currency TEXT DEFAULT 'NGN',

                payment_status TEXT DEFAULT 'NOT_REPORTED',
                download_unlocked INTEGER DEFAULT 0,

                created_at TEXT,
                updated_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_document_products_business
            ON document_products(customer_id, product_id);

            CREATE TABLE IF NOT EXISTS delivery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                customer_id TEXT,
                product_id TEXT,
                order_id TEXT,

                channel TEXT,
                status TEXT,

                recipient TEXT,
                message TEXT,

                file_path TEXT,

                created_at TEXT
            );
            """
        )

        connection.commit()

    finally:
        connection.close()


def initialise_payment_database() -> None:
    connection = db(PAYMENT_DB_PATH)

    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                order_id TEXT UNIQUE,

                customer_id TEXT,
                product_id TEXT,

                customer_name TEXT,
                customer_email TEXT,
                customer_phone TEXT,

                document_title TEXT,
                service_name TEXT,

                amount INTEGER DEFAULT 0,
                currency TEXT DEFAULT 'NGN',

                payment_status TEXT DEFAULT 'PENDING',

                document_text TEXT,

                reference TEXT,
                payment_method TEXT,

                reported_at TEXT,
                verified_at TEXT,
                completed_at TEXT,

                created_at TEXT,
                updated_at TEXT
            );
            """
        )

        connection.commit()

    finally:
        connection.close()


initialise_product_database()
initialise_payment_database()


# ============================================================
# PAYLOAD NORMALISATION
# ============================================================

def normalize_pages(payload: Any) -> list:
    if payload is None:
        return []

    if isinstance(payload, dict):
        if "pages" in payload:
            payload = payload.get("pages")

        elif "content" in payload:
            payload = [payload.get("content")]

        else:
            payload = [payload]

    if isinstance(payload, str):
        return [payload]

    if not isinstance(payload, list):
        return [str(payload)]

    pages = []

    for item in payload:
        if item is None:
            continue

        if isinstance(item, dict):
            text = first(
                item,
                "text",
                "content",
                "body",
                "page_text",
                default="",
            )

            if text:
                pages.append(str(text))

        else:
            text = str(item)

            if text.strip():
                pages.append(text)

    return pages


def normalize_payload(payload: Any) -> dict:
    if payload is None:
        return {
            "pages": [],
        }

    if isinstance(payload, str):
        return {
            "pages": [payload],
        }

    if isinstance(payload, list):
        return {
            "pages": normalize_pages(payload),
        }

    if isinstance(payload, dict):
        result = dict(payload)

        result["pages"] = normalize_pages(
            result.get("pages", result.get("content", []))
        )

        return result

    return {
        "pages": [str(payload)],
    }


def _format_saved_text(text: str) -> str:
    """
    Compatibility text cleaner used only when creating the
    original canonical DOCX.

    It does NOT run during payment verification,
    activation, Back Office viewing, or delivery.
    """

    text = clean(text)

    if not text:
        return ""

    return text


def clean_saved_document_payload(payload: Any) -> dict:
    payload = normalize_payload(payload)

    pages = []

    for page in payload.get("pages", []):
        page = _format_saved_text(str(page))

        if page:
            pages.append(page)

    payload["pages"] = pages

    return payload


# ============================================================
# DOCX CREATION
# ============================================================

def _docx_p(text: str) -> str:
    text = xml_escape(clean(text))

    return (
        "<w:p>"
        "<w:r>"
        f"<w:t xml:space=\"preserve\">{text}</w:t>"
        "</w:r>"
        "</w:p>"
    )


def make_docx(
    output_path: Path,
    title: str,
    pages: list,
) -> Path:
    """
    Creates the original canonical DOCX only when no canonical
    document already exists.

    Existing canonical files are NEVER overwritten.
    """

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    body_parts = []

    for page in pages:
        page = clean(page)

        if not page:
            continue

        lines = page.splitlines()

        for line in lines:
            body_parts.append(
                _docx_p(line)
            )

    if not body_parts:
        body_parts.append(
            _docx_p(title or "Document")
        )

    body = "".join(body_parts)

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body>
        {body}
        <w:sectPr>
            <w:pgSz w:w="11906" w:h="16838"/>
            <w:pgMar
                w:top="1440"
                w:right="1440"
                w:bottom="1440"
                w:left="1440"/>
        </w:sectPr>
    </w:body>
</w:document>
"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels"
        ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml"
        ContentType="application/xml"/>
    <Default Extension="docx"
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document"/>
    <Override PartName="/word/document.xml"
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships
    xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship
        Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        Target="word/document.xml"/>
</Relationships>
"""

    document_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships
    xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>
"""

    with zipfile.ZipFile(
        output_path,
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
            "word/_rels/document.xml.rels",
            document_relationships,
        )

        archive.writestr(
            "word/document.xml",
            document_xml,
        )

    return output_path


# ============================================================
# PRODUCT HELPERS
# ============================================================

def get_product(
    customer_id: str,
    product_id: str,
) -> Optional[dict]:

    connection = db(PRODUCT_DB_PATH)

    try:
        row = connection.execute(
            """
            SELECT *
            FROM document_products
            WHERE customer_id = ?
              AND product_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                customer_id,
                product_id,
            ),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


def get_product_by_order(
    order_id: str,
) -> Optional[dict]:

    connection = db(PRODUCT_DB_PATH)

    try:
        row = connection.execute(
            """
            SELECT *
            FROM document_products
            WHERE product_id = ?
               OR customer_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                order_id,
                order_id,
            ),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


# ============================================================
# CANONICAL FILE RESOLUTION
# ============================================================

def find_saved_file_from_product(
    product: dict,
    use_path: bool = True,
) -> Optional[Path]:

    if not product:
        return None

    # --------------------------------------------------------
    # 1. Exact stored canonical path.
    # --------------------------------------------------------

    if use_path:
        saved_path = clean(
            product.get("document_saved_path")
        )

        if saved_path:
            path = Path(saved_path)

            if path.exists() and path.is_file():
                return path

    # --------------------------------------------------------
    # 2. Deterministic product folder.
    # --------------------------------------------------------

    customer_id = clean(
        product.get("customer_id")
    )

    product_id = clean(
        product.get("product_id")
    )

    title = clean(
        product.get("document_title")
    )

    if customer_id and product_id:
        folder = (
            DOWNLOAD_DIR
            / safe_folder(customer_id)
            / safe_folder(product_id)
        )

        if folder.exists():

            candidates = []

            if title:
                stem = safe_filename(
                    title,
                    fallback="document",
                )

                candidates.extend(
                    [
                        folder / f"{stem}.docx",
                        folder / f"{stem}.pdf",
                    ]
                )

            candidates.extend(
                sorted(
                    folder.glob("*.docx"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
            )

            candidates.extend(
                sorted(
                    folder.glob("*.pdf"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
            )

            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    return candidate

    # --------------------------------------------------------
    # 3. Last-resort deterministic search under downloads.
    # --------------------------------------------------------

    if customer_id and product_id and DOWNLOAD_DIR.exists():

        target_customer = safe_folder(customer_id)
        target_product = safe_folder(product_id)

        for candidate in DOWNLOAD_DIR.rglob("*"):

            if not candidate.is_file():
                continue

            if candidate.suffix.lower() not in (
                ".docx",
                ".pdf",
            ):
                continue

            parts = {
                part.lower()
                for part in candidate.parts
            }

            if (
                target_customer.lower() in parts
                and target_product.lower() in parts
            ):
                return candidate

    return None


def existing_saved_file(
    product: dict,
) -> Optional[Path]:

    return find_saved_file_from_product(
        product,
        use_path=True,
    )


# ============================================================
# CANONICAL DOCUMENT READING
# ============================================================

def read_saved_docx(path: Path) -> dict:
    """
    Reads text from the actual saved DOCX.

    This is a compatibility reader only.

    It does NOT create a new document.
    It does NOT write back to the DOCX.
    """

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

    except Exception as exc:
        raise RuntimeError(
            f"CANONICAL_DOCUMENT_READ_FAILED: {exc}"
        )

    xml = re.sub(
        r"<w:tab[^>]*/>",
        "\t",
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

    text = xml.replace(
        "&amp;",
        "&",
    ).replace(
        "&lt;",
        "<",
    ).replace(
        "&gt;",
        ">",
    ).replace(
        "&quot;",
        '"',
    ).replace(
        "&apos;",
        "'",
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return {
        "text": text.strip(),
        "pages": [
            page.strip()
            for page in re.split(
                r"\n\s*\n",
                text,
            )
            if page.strip()
        ],
    }


def read_actual_saved_document(
    product: dict,
) -> dict:

    path = existing_saved_file(product)

    if not path:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    suffix = path.suffix.lower()

    if suffix == ".docx":
        content = read_saved_docx(path)

        return {
            "path": str(path),
            "filename": path.name,
            "mime_type":
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
            "text": content["text"],
            "pages": content["pages"],
        }

    if suffix == ".pdf":
        return {
            "path": str(path),
            "filename": path.name,
            "mime_type": "application/pdf",
            "text": "",
            "pages": [],
        }

    raise HTTPException(
        status_code=415,
        detail="UNSUPPORTED_CANONICAL_DOCUMENT_TYPE",
    )


# ============================================================
# CANONICAL PATH STORAGE
# ============================================================

def store_saved_path(
    customer_id: str,
    product_id: str,
    saved_path: Path,
) -> None:

    connection = db(PRODUCT_DB_PATH)

    try:
        timestamp = now_iso()

        connection.execute(
            """
            UPDATE document_products
            SET
                document_saved_path = ?,
                document_saved_at = COALESCE(
                    document_saved_at,
                    ?
                ),
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                str(saved_path),
                timestamp,
                timestamp,
                customer_id,
                product_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()


# ============================================================
# CREATE CANONICAL DOCUMENT ONLY ONCE
# ============================================================

def save_exact_snapshot(
    product: dict,
) -> Path:

    existing = existing_saved_file(product)

    if existing:
        return existing

    customer_id = clean(
        product.get("customer_id")
    )

    product_id = clean(
        product.get("product_id")
    )

    title = clean(
        product.get("document_title")
    )

    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_ID_REQUIRED",
        )

    if not product_id:
        raise HTTPException(
            status_code=400,
            detail="PRODUCT_ID_REQUIRED",
        )

    payload = from_json(
        product.get("document_payload"),
        {},
    )

    payload = clean_saved_document_payload(
        payload
    )

    pages = payload.get(
        "pages",
        [],
    )

    if not pages:
        raise HTTPException(
            status_code=422,
            detail="REVIEWED_DOCUMENT_CONTENT_MISSING",
        )

    folder = (
        DOWNLOAD_DIR
        / safe_folder(customer_id)
        / safe_folder(product_id)
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = (
        safe_filename(
            title,
            fallback="document",
        )
        + ".docx"
    )

    output_path = folder / filename

    # Never silently replace an existing document.
    if output_path.exists():
        store_saved_path(
            customer_id,
            product_id,
            output_path,
        )

        return output_path

    make_docx(
        output_path,
        title,
        pages,
    )

    store_saved_path(
        customer_id,
        product_id,
        output_path,
    )

    return output_path


# ============================================================
# CANONICAL DOCUMENT REPAIR
# ============================================================

def repair_saved_snapshot(
    product: dict,
) -> Path:

    """
    IMPORTANT:

    This function does NOT rebuild a document.

    It only attempts to discover an already existing canonical
    file and records its path if the database path is stale.
    """

    path = existing_saved_file(product)

    if not path:
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    current_path = clean(
        product.get("document_saved_path")
    )

    if current_path != str(path):

        store_saved_path(
            clean(product.get("customer_id")),
            clean(product.get("product_id")),
            path,
        )

    return path


def extract_document_text(
    product: dict,
) -> str:

    document = read_actual_saved_document(
        product
    )

    return document.get(
        "text",
        "",
    )


# ============================================================
# PRODUCT UPSERT
# ============================================================

def upsert_product(
    data: dict,
) -> dict:

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
            "id",
        )
    )

    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_ID_REQUIRED",
        )

    if not product_id:
        raise HTTPException(
            status_code=400,
            detail="PRODUCT_ID_REQUIRED",
        )

    existing = get_product(
        customer_id,
        product_id,
    )

    timestamp = now_iso()

    title = clean(
        first(
            data,
            "document_title",
            "documentTitle",
            "title",
            default=(
                existing.get("document_title")
                if existing
                else ""
            ),
        )
    )

    service_name = clean(
        first(
            data,
            "service_name",
            "serviceName",
            "service",
            default=(
                existing.get("service_name")
                if existing
                else ""
            ),
        )
    )

    customer_name = clean(
        first(
            data,
            "customer_name",
            "customerName",
            "name",
            default=(
                existing.get("customer_name")
                if existing
                else ""
            ),
        )
    )

    customer_email = clean(
        first(
            data,
            "customer_email",
            "customerEmail",
            "email",
            default=(
                existing.get("customer_email")
                if existing
                else ""
            ),
        )
    )

    customer_phone = clean(
        first(
            data,
            "customer_phone",
            "customerPhone",
            "phone",
            default=(
                existing.get("customer_phone")
                if existing
                else ""
            ),
        )
    )

    amount_value = first(
        data,
        "amount",
        "price",
        default=(
            existing.get("amount")
            if existing
            else 0
        ),
    )

    try:
        amount = int(float(amount_value or 0))
    except Exception:
        amount = 0

    currency = clean(
        first(
            data,
            "currency",
            default=(
                existing.get("currency")
                if existing
                else "NGN"
            ),
        )
    ) or "NGN"

    incoming_payload = data.get(
        "document_payload",
        data.get("documentPayload"),
    )

    if incoming_payload is not None:
        document_payload = to_json(
            normalize_payload(
                incoming_payload
            )
        )
    elif existing:
        document_payload = existing.get(
            "document_payload"
        )
    else:
        document_payload = to_json(
            {"pages": []}
        )

    saved_path = (
        existing.get("document_saved_path")
        if existing
        else ""
    )

    saved_at = (
        existing.get("document_saved_at")
        if existing
        else ""
    )

    payment_status = (
        existing.get("payment_status")
        if existing
        else "NOT_REPORTED"
    )

    download_unlocked = (
        int(
            existing.get(
                "download_unlocked",
                0,
            )
        )
        if existing
        else 0
    )

    connection = db(PRODUCT_DB_PATH)

    try:
        connection.execute(
            """
            INSERT INTO document_products (
                product_id,
                customer_id,
                document_title,
                service_name,
                customer_name,
                customer_email,
                customer_phone,
                document_payload,
                document_saved_path,
                document_saved_at,
                amount,
                currency,
                payment_status,
                download_unlocked,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(customer_id, product_id)
            DO UPDATE SET
                document_title = excluded.document_title,
                service_name = excluded.service_name,
                customer_name = excluded.customer_name,
                customer_email = excluded.customer_email,
                customer_phone = excluded.customer_phone,

                document_payload =
                    CASE
                        WHEN document_products.document_saved_path
                             IS NOT NULL
                             AND document_products.document_saved_path != ''
                        THEN document_products.document_payload
                        ELSE excluded.document_payload
                    END,

                document_saved_path =
                    CASE
                        WHEN document_products.document_saved_path
                             IS NOT NULL
                             AND document_products.document_saved_path != ''
                        THEN document_products.document_saved_path
                        ELSE excluded.document_saved_path
                    END,

                document_saved_at =
                    CASE
                        WHEN document_products.document_saved_at
                             IS NOT NULL
                             AND document_products.document_saved_at != ''
                        THEN document_products.document_saved_at
                        ELSE excluded.document_saved_at
                    END,

                amount = excluded.amount,
                currency = excluded.currency,

                payment_status =
                    document_products.payment_status,

                download_unlocked =
                    document_products.download_unlocked,

                updated_at = excluded.updated_at
            """,
            (
                product_id,
                customer_id,
                title,
                service_name,
                customer_name,
                customer_email,
                customer_phone,
                document_payload,
                saved_path,
                saved_at,
                amount,
                currency,
                payment_status,
                download_unlocked,
                existing.get("created_at", timestamp)
                if existing
                else timestamp,
                timestamp,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    result = get_product(
        customer_id,
        product_id,
    )

    if not result:
        raise HTTPException(
            status_code=500,
            detail="PRODUCT_SAVE_FAILED",
        )

    return result


# ============================================================
# PAYMENT RECORD
# ============================================================

def ensure_payment_record(
    product: dict,
    order_id: str,
) -> dict:

    canonical = repair_saved_snapshot(
        product
    )

    document_text = extract_document_text(
        product
    )

    connection = db(PAYMENT_DB_PATH)

    try:
        timestamp = now_iso()

        connection.execute(
            """
            INSERT INTO payment_orders (
                order_id,
                customer_id,
                product_id,
                customer_name,
                customer_email,
                customer_phone,
                document_title,
                service_name,
                amount,
                currency,
                payment_status,
                document_text,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(order_id)
            DO UPDATE SET
                customer_id = excluded.customer_id,
                product_id = excluded.product_id,
                customer_name = excluded.customer_name,
                customer_email = excluded.customer_email,
                customer_phone = excluded.customer_phone,
                document_title = excluded.document_title,
                service_name = excluded.service_name,
                amount = excluded.amount,
                currency = excluded.currency,
                updated_at = excluded.updated_at
            """,
            (
                order_id,
                product.get("customer_id"),
                product.get("product_id"),
                product.get("customer_name"),
                product.get("customer_email"),
                product.get("customer_phone"),
                product.get("document_title"),
                product.get("service_name"),
                int(product.get("amount") or 0),
                product.get("currency") or "NGN",
                "PENDING",
                document_text,
                timestamp,
                timestamp,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE order_id = ?
            LIMIT 1
            """,
            (order_id,),
        ).fetchone()

        return as_dict(row)

    finally:
        connection.close()


# ============================================================
# RESPONSE HELPERS
# ============================================================

def product_response(
    product: dict,
) -> dict:

    path = existing_saved_file(product)

    return {
        "ok": True,
        "product_id": product.get("product_id"),
        "customer_id": product.get("customer_id"),
        "document_title": product.get("document_title"),
        "service_name": product.get("service_name"),
        "customer_name": product.get("customer_name"),
        "customer_email": product.get("customer_email"),
        "customer_phone": product.get("customer_phone"),
        "amount": product.get("amount"),
        "currency": product.get("currency"),
        "payment_status": product.get("payment_status"),
        "download_unlocked": bool(
            product.get("download_unlocked")
        ),
        "document_saved": bool(path),
        "document_saved_path": (
            str(path)
            if path
            else ""
        ),
        "document_filename": (
            path.name
            if path
            else ""
        ),
        "created_at": product.get("created_at"),
        "updated_at": product.get("updated_at"),
    }


# ============================================================
# PUBLIC PRODUCT
# ============================================================

@app.get("/api/product")
def public_product(
    customer_id: str,
    product_id: str,
):
    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    path = existing_saved_file(product)

    return {
        "ok": True,
        "product_id": product.get("product_id"),
        "customer_id": product.get("customer_id"),

        # Document title is deliberately preserved.
        "document_title": product.get(
            "document_title"
        ),

        "service_name": product.get(
            "service_name"
        ),

        "customer_name": product.get(
            "customer_name"
        ),

        "customer_email": product.get(
            "customer_email"
        ),

        "customer_phone": product.get(
            "customer_phone"
        ),

        "amount": product.get("amount"),
        "currency": product.get("currency"),

        "payment_status": product.get(
            "payment_status"
        ),

        "download_unlocked": bool(
            product.get("download_unlocked")
        ),

        "document_saved": bool(path),

        "document_filename": (
            path.name
            if path
            else ""
        ),

        "document_saved_path": (
            str(path)
            if path
            else ""
        ),
    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: Request,
):
    data = await request.json()

    product = upsert_product(
        data
    )

    # The only place where the canonical file may
    # be created if it does not already exist.
    canonical = save_exact_snapshot(
        product
    )

    product = get_product(
        clean(product.get("customer_id")),
        clean(product.get("product_id")),
    )

    if not product:
        raise HTTPException(
            status_code=500,
            detail="PRODUCT_NOT_FOUND_AFTER_SAVE",
        )

    order_id = clean(
        first(
            data,
            "order_id",
            "orderId",
            default="",
        )
    )

    if not order_id:
        order_id = (
            "NPBC-"
            + key_part(
                product.get("customer_id")
            )
            + "-"
            + key_part(
                product.get("product_id")
            )
            + "-"
            + datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%d%H%M%S%f"
            )
        )

    payment = ensure_payment_record(
        product,
        order_id,
    )

    return {
        "ok": True,
        "message": "PAYMENT_ORDER_CREATED",
        "order_id": order_id,
        "product_id": product.get("product_id"),
        "customer_id": product.get("customer_id"),

        "document_title": product.get(
            "document_title"
        ),

        "service_name": product.get(
            "service_name"
        ),

        "amount": product.get(
            "amount"
        ),

        "currency": product.get(
            "currency"
        ),

        "document_saved": True,
        "document_filename": canonical.name,
        "document_saved_path": str(canonical),

        "payment_status": payment.get(
            "payment_status",
            "PENDING",
        ),
    }


# ============================================================
# PAYMENT REPORT
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    request: Request,
):
    data = await request.json()

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    order_id = clean(
        first(
            data,
            "order_id",
            "orderId",
        )
    )

    reference = clean(
        first(
            data,
            "reference",
            "payment_reference",
            "transaction_reference",
        )
    )

    payment_method = clean(
        first(
            data,
            "payment_method",
            "paymentMethod",
            default="",
        )
    )

    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_ID_REQUIRED",
        )

    if not product_id:
        raise HTTPException(
            status_code=400,
            detail="PRODUCT_ID_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = repair_saved_snapshot(
        product
    )

    if not order_id:
        order_id = (
            "NPBC-"
            + key_part(customer_id)
            + "-"
            + key_part(product_id)
        )

    ensure_payment_record(
        product,
        order_id,
    )

    timestamp = now_iso()

    connection = db(PAYMENT_DB_PATH)

    try:
        connection.execute(
            """
            UPDATE payment_orders
            SET
                payment_status = 'REPORTED',
                reference = ?,
                payment_method = ?,
                reported_at = ?,
                updated_at = ?
            WHERE order_id = ?
            """,
            (
                reference,
                payment_method,
                timestamp,
                timestamp,
                order_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    product_connection = db(
        PRODUCT_DB_PATH
    )

    try:
        product_connection.execute(
            """
            UPDATE document_products
            SET
                payment_status = 'REPORTED',
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                timestamp,
                customer_id,
                product_id,
            ),
        )

        product_connection.commit()

    finally:
        product_connection.close()

    return {
        "ok": True,
        "message": "PAYMENT_REPORTED",
        "order_id": order_id,
        "payment_status": "REPORTED",
        "document_saved": True,
        "document_filename": canonical.name,
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
def payment_status(
    customer_id: str,
    product_id: str,
):
    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    path = existing_saved_file(product)

    return {
        "ok": True,
        "customer_id": customer_id,
        "product_id": product_id,

        "document_title": product.get(
            "document_title"
        ),

        "payment_status": product.get(
            "payment_status"
        ),

        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),

        "document_saved": bool(path),

        "document_filename": (
            path.name
            if path
            else ""
        ),
    }


# ============================================================
# PAYMENT COMPLETE
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: Request,
):
    data = await request.json()

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    order_id = clean(
        first(
            data,
            "order_id",
            "orderId",
        )
    )

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = repair_saved_snapshot(
        product
    )

    if not order_id:
        order_id = (
            "NPBC-"
            + key_part(customer_id)
            + "-"
            + key_part(product_id)
        )

    ensure_payment_record(
        product,
        order_id,
    )

    timestamp = now_iso()

    connection = db(
        PAYMENT_DB_PATH
    )

    try:
        connection.execute(
            """
            UPDATE payment_orders
            SET
                payment_status = 'COMPLETED',
                completed_at = ?,
                updated_at = ?
            WHERE order_id = ?
            """,
            (
                timestamp,
                timestamp,
                order_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    product_connection = db(
        PRODUCT_DB_PATH
    )

    try:
        product_connection.execute(
            """
            UPDATE document_products
            SET
                payment_status = 'COMPLETED',
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                timestamp,
                customer_id,
                product_id,
            ),
        )

        product_connection.commit()

    finally:
        product_connection.close()

    return {
        "ok": True,
        "message": "PAYMENT_COMPLETED",
        "order_id": order_id,
        "payment_status": "COMPLETED",

        "document_title": product.get(
            "document_title"
        ),

        "document_saved": True,
        "document_filename": canonical.name,

        # Payment completion does NOT automatically
        # unlock the customer download.
        "download_unlocked": bool(
            product.get(
                "download_unlocked"
            )
        ),
    }


# ============================================================
# BACK OFFICE AUTH
# ============================================================

def require_back_office(
    admin_key: str = "",
) -> None:

    if clean(admin_key) != BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(
            status_code=401,
            detail="BACK_OFFICE_UNAUTHORIZED",
        )


# ============================================================
# BACK OFFICE LOGIN
# ============================================================

@app.post("/api/back-office/login")
async def back_office_login(
    request: Request,
):
    data = await request.json()

    supplied = clean(
        first(
            data,
            "admin_key",
            "adminKey",
            "key",
        )
    )

    if supplied != BACK_OFFICE_ADMIN_KEY:
        raise HTTPException(
            status_code=401,
            detail="INVALID_BACK_OFFICE_KEY",
        )

    return {
        "ok": True,
        "authenticated": True,
        "app_version": APP_VERSION,
    }


# ============================================================
# BACK OFFICE PRODUCT
# ============================================================

@app.get("/api/back-office/product")
def back_office_product(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = repair_saved_snapshot(
        product
    )

    # Read the actual canonical file.
    # This is never reconstructed from document_payload.
    actual = read_actual_saved_document(
        product
    )

    result = product_response(
        product
    )

    result.update(
        {
            "canonical_document": True,
            "canonical_document_path":
                str(canonical),
            "canonical_document_filename":
                canonical.name,
            "canonical_document_mime_type":
                actual.get("mime_type"),
            "canonical_document_url":
                "/api/back-office/document-file"
                + "?"
                + urllib.parse.urlencode(
                    {
                        "customer_id":
                            customer_id,
                        "product_id":
                            product_id,
                        "admin_key":
                            admin_key,
                    }
                ),

            # Kept for compatibility with the
            # existing Back Office, but this is
            # extracted from the actual saved file.
            "document_text":
                actual.get("text", ""),
            "document_pages":
                actual.get("pages", []),
        }
    )

    return result


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
def back_office_payments(
    admin_key: str = "",
):
    require_back_office(
        admin_key
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

        records = []

        for row in rows:
            product = dict(row)

            try:
                path = repair_saved_snapshot(
                    product
                )
            except HTTPException:
                path = None

            records.append(
                {
                    **product_response(
                        product
                    ),

                    "payment_status":
                        product.get(
                            "payment_status"
                        ),

                    "download_unlocked":
                        bool(
                            product.get(
                                "download_unlocked"
                            )
                        ),

                    "document_saved":
                        bool(path),

                    "document_filename":
                        (
                            path.name
                            if path
                            else ""
                        ),
                }
            )

        return {
            "ok": True,
            "records": records,
            "count": len(records),
        }

    finally:
        connection.close()


# ============================================================
# BACK OFFICE DOCUMENT INFO
# ============================================================

@app.get("/api/back-office/document-info")
def back_office_document_info(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,

        "customer_id": customer_id,
        "product_id": product_id,

        # Preserved.
        "document_title":
            product.get(
                "document_title"
            ),

        "service_name":
            product.get(
                "service_name"
            ),

        "document_saved": True,

        "document_saved_path":
            str(canonical),

        "document_filename":
            canonical.name,

        "document_size":
            canonical.stat().st_size,

        "document_modified_at":
            datetime.fromtimestamp(
                canonical.stat().st_mtime,
                timezone.utc,
            ).isoformat(),

        "canonical_document": True,

        "canonical_document_url":
            "/api/back-office/document-file"
            + "?"
            + urllib.parse.urlencode(
                {
                    "customer_id":
                        customer_id,
                    "product_id":
                        product_id,
                    "admin_key":
                        admin_key,
                }
            ),
    }


# ============================================================
# BACK OFFICE DOCUMENT COMPATIBILITY ENDPOINTS
# ============================================================

@app.get("/api/back-office/document")
def back_office_document(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    return back_office_document_info(
        customer_id=customer_id,
        product_id=product_id,
        admin_key=admin_key,
    )


@app.get("/api/back-office/document-content")
def back_office_document_content(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    """
    Compatibility endpoint.

    It returns information about the canonical file.

    It does NOT reconstruct a document from document_payload.
    """

    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,
        "canonical_document": True,
        "document_title":
            product.get(
                "document_title"
            ),
        "filename": canonical.name,
        "path": str(canonical),
        "mime_type":
            (
                "application/pdf"
                if canonical.suffix.lower() == ".pdf"
                else
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        "document_url":
            "/api/back-office/document-file"
            + "?"
            + urllib.parse.urlencode(
                {
                    "customer_id":
                        customer_id,
                    "product_id":
                        product_id,
                    "admin_key":
                        admin_key,
                }
            ),
    }


# ============================================================
# BACK OFFICE PAYMENT DETAILS
# ============================================================

@app.get("/api/back-office/payment")
def back_office_payment(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    connection = db(
        PAYMENT_DB_PATH
    )

    try:
        row = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE customer_id = ?
              AND product_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                customer_id,
                product_id,
            ),
        ).fetchone()

        payment = as_dict(row)

    finally:
        connection.close()

    return {
        "ok": True,
        "product": product_response(
            product
        ),
        "payment": payment,
    }


# ============================================================
# BACK OFFICE PAYMENT VERIFY
# ============================================================

@app.post("/api/back-office/payment/verify")
async def back_office_payment_verify(
    request: Request,
):
    data = await request.json()

    admin_key = clean(
        first(
            data,
            "admin_key",
            "adminKey",
            "key",
        )
    )

    require_back_office(
        admin_key
    )

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    order_id = clean(
        first(
            data,
            "order_id",
            "orderId",
        )
    )

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    # Verify the existing canonical document only.
    canonical = repair_saved_snapshot(
        product
    )

    timestamp = now_iso()

    connection = db(
        PAYMENT_DB_PATH
    )

    try:

        if order_id:

            connection.execute(
                """
                UPDATE payment_orders
                SET
                    payment_status = 'VERIFIED',
                    verified_at = ?,
                    updated_at = ?
                WHERE order_id = ?
                """,
                (
                    timestamp,
                    timestamp,
                    order_id,
                ),
            )

        else:

            connection.execute(
                """
                UPDATE payment_orders
                SET
                    payment_status = 'VERIFIED',
                    verified_at = ?,
                    updated_at = ?
                WHERE customer_id = ?
                  AND product_id = ?
                """,
                (
                    timestamp,
                    timestamp,
                    customer_id,
                    product_id,
                ),
            )

        connection.commit()

    finally:
        connection.close()

    product_connection = db(
        PRODUCT_DB_PATH
    )

    try:
        product_connection.execute(
            """
            UPDATE document_products
            SET
                payment_status = 'VERIFIED',
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                timestamp,
                customer_id,
                product_id,
            ),
        )

        product_connection.commit()

    finally:
        product_connection.close()

    return {
        "ok": True,
        "message": "PAYMENT_VERIFIED",
        "payment_status": "VERIFIED",

        "document_title":
            product.get(
                "document_title"
            ),

        "canonical_document": True,

        "document_filename":
            canonical.name,

        "document_saved_path":
            str(canonical),

        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# BACK OFFICE ACTIVATE DOWNLOAD
# ============================================================

@app.post("/api/back-office/activate-download")
async def back_office_activate_download(
    request: Request,
):
    data = await request.json()

    admin_key = clean(
        first(
            data,
            "admin_key",
            "adminKey",
            "key",
        )
    )

    require_back_office(
        admin_key
    )

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    # Activation requires the exact saved file.
    canonical = repair_saved_snapshot(
        product
    )

    timestamp = now_iso()

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        connection.execute(
            """
            UPDATE document_products
            SET
                download_unlocked = 1,
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                timestamp,
                customer_id,
                product_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    return {
        "ok": True,
        "message": "CUSTOMER_DOWNLOAD_ACTIVATED",

        "customer_id":
            customer_id,

        "product_id":
            product_id,

        "document_title":
            product.get(
                "document_title"
            ),

        "download_unlocked": True,

        "canonical_document":
            True,

        "document_filename":
            canonical.name,

        "document_saved_path":
            str(canonical),
    }


# ============================================================
# BACK OFFICE PAYMENT REJECT
# ============================================================

@app.post("/api/back-office/payment/reject")
async def back_office_payment_reject(
    request: Request,
):
    data = await request.json()

    admin_key = clean(
        first(
            data,
            "admin_key",
            "adminKey",
            "key",
        )
    )

    require_back_office(
        admin_key
    )

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    reason = clean(
        first(
            data,
            "reason",
            "rejection_reason",
            default="Payment not verified.",
        )
    )

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    # Rejecting payment does not touch the document.
    timestamp = now_iso()

    connection = db(
        PAYMENT_DB_PATH
    )

    try:
        connection.execute(
            """
            UPDATE payment_orders
            SET
                payment_status = 'REJECTED',
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                timestamp,
                customer_id,
                product_id,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    product_connection = db(
        PRODUCT_DB_PATH
    )

    try:
        product_connection.execute(
            """
            UPDATE document_products
            SET
                payment_status = 'REJECTED',
                updated_at = ?
            WHERE customer_id = ?
              AND product_id = ?
            """,
            (
                timestamp,
                customer_id,
                product_id,
            ),
        )

        product_connection.commit()

    finally:
        product_connection.close()

    return {
        "ok": True,
        "message": "PAYMENT_REJECTED",
        "payment_status": "REJECTED",
        "reason": reason,

        "document_unchanged": True,

        "document_title":
            product.get(
                "document_title"
            ),
    }


# ============================================================
# CANONICAL FILE RESPONSE
# ============================================================

def canonical_file_or_404(
    product: dict,
) -> Path:

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    path = repair_saved_snapshot(
        product
    )

    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="SAVED_DOCUMENT_FILE_MISSING",
        )

    return path


def file_response(
    path: Path,
    attachment: bool = False,
):
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

    disposition = (
        "attachment"
        if attachment
        else "inline"
    )

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=path.name,
        headers={
            "Content-Disposition":
                f'{disposition}; filename="{path.name}"',
            "Cache-Control":
                "no-store, no-cache, must-revalidate",
        },
    )


# ============================================================
# BACK OFFICE DOCUMENT FILE
# ============================================================

@app.get("/api/back-office/document-file")
def back_office_document_file(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = canonical_file_or_404(
        product
    )

    # EXACT SAVED DOCUMENT.
    return file_response(
        canonical,
        attachment=False,
    )


# ============================================================
# BACK OFFICE DELIVERY FILE
# ============================================================

@app.get("/api/back-office/delivery-file")
def back_office_delivery_file(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = canonical_file_or_404(
        product
    )

    # EXACT SAVED DOCUMENT.
    return file_response(
        canonical,
        attachment=True,
    )


# ============================================================
# BACK OFFICE CANONICAL DOCUMENT
# ============================================================

@app.get("/api/back-office/canonical-document")
def back_office_canonical_document(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = canonical_file_or_404(
        product
    )

    return {
        "ok": True,
        "canonical_document": True,

        "document_title":
            product.get(
                "document_title"
            ),

        "customer_id":
            customer_id,

        "product_id":
            product_id,

        "filename":
            canonical.name,

        "path":
            str(canonical),

        "size":
            canonical.stat().st_size,

        "url":
            "/api/back-office/document-file"
            + "?"
            + urllib.parse.urlencode(
                {
                    "customer_id":
                        customer_id,
                    "product_id":
                        product_id,
                    "admin_key":
                        admin_key,
                }
            ),
    }


# ============================================================
# DELIVERY CHANNELS
# ============================================================

@app.get("/api/back-office/delivery-channels")
def back_office_delivery_channels(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = canonical_file_or_404(
        product
    )

    return {
        "ok": True,

        "document_title":
            product.get(
                "document_title"
            ),

        "filename":
            canonical.name,

        "canonical_document":
            True,

        "channels": {
            "phone": {
                "available": bool(
                    product.get(
                        "customer_phone"
                    )
                ),
                "recipient":
                    product.get(
                        "customer_phone"
                    ),
            },

            "whatsapp": {
                "available": bool(
                    product.get(
                        "customer_phone"
                    )
                ),
                "recipient":
                    product.get(
                        "customer_phone"
                    ),
            },

            "email": {
                "available": bool(
                    product.get(
                        "customer_email"
                    )
                ),
                "recipient":
                    product.get(
                        "customer_email"
                    ),
            },

            "telegram": {
                "available": False,
                "recipient": "",
            },

            "google_drive": {
                "available": False,
                "recipient": "",
            },
        },
    }


# ============================================================
# DELIVERY
# ============================================================

@app.post("/api/back-office/delivery")
async def back_office_delivery(
    request: Request,
):
    data = await request.json()

    admin_key = clean(
        first(
            data,
            "admin_key",
            "adminKey",
            "key",
        )
    )

    require_back_office(
        admin_key
    )

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    channel = clean(
        first(
            data,
            "channel",
            "delivery_channel",
        )
    ).lower()

    recipient = clean(
        first(
            data,
            "recipient",
            "recipient_email",
            "recipient_phone",
            "phone",
            "email",
        )
    )

    message = clean(
        first(
            data,
            "message",
            default="",
        )
    )

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    if not channel:
        raise HTTPException(
            status_code=400,
            detail="DELIVERY_CHANNEL_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = canonical_file_or_404(
        product
    )

    if not recipient:
        if channel in (
            "email",
            "gmail",
        ):
            recipient = clean(
                product.get(
                    "customer_email"
                )
            )

        elif channel in (
            "phone",
            "whatsapp",
        ):
            recipient = clean(
                product.get(
                    "customer_phone"
                )
            )

    if channel in (
        "email",
        "gmail",
    ) and not recipient:
        raise HTTPException(
            status_code=400,
            detail="RECIPIENT_EMAIL_REQUIRED",
        )

    if channel in (
        "phone",
        "whatsapp",
    ) and not recipient:
        raise HTTPException(
            status_code=400,
            detail="RECIPIENT_PHONE_REQUIRED",
        )

    timestamp = now_iso()

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        connection.execute(
            """
            INSERT INTO delivery_events (
                customer_id,
                product_id,
                order_id,
                channel,
                status,
                recipient,
                message,
                file_path,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                product_id,
                "",
                channel,
                "PREPARED",
                recipient,
                message,
                str(canonical),
                timestamp,
            ),
        )

        connection.commit()

    finally:
        connection.close()

    return {
        "ok": True,
        "message":
            "DELIVERY_PREPARED",

        "channel":
            channel,

        "recipient":
            recipient,

        "document_title":
            product.get(
                "document_title"
            ),

        "filename":
            canonical.name,

        "canonical_document":
            True,

        "file_url":
            "/api/back-office/delivery-file"
            + "?"
            + urllib.parse.urlencode(
                {
                    "customer_id":
                        customer_id,
                    "product_id":
                        product_id,
                    "admin_key":
                        admin_key,
                }
            ),
    }


# ============================================================
# DELIVERY HISTORY
# ============================================================

@app.get("/api/back-office/delivery-history")
def back_office_delivery_history(
    customer_id: str,
    product_id: str,
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM delivery_events
            WHERE customer_id = ?
              AND product_id = ?
            ORDER BY id DESC
            """,
            (
                customer_id,
                product_id,
            ),
        ).fetchall()

        return {
            "ok": True,
            "events": [
                dict(row)
                for row in rows
            ],
        }

    finally:
        connection.close()


# ============================================================
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/back-office/jobs")
def back_office_jobs(
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    connection = db(
        PRODUCT_DB_PATH
    )

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                customer_id,
                product_id,
                document_title,
                service_name,
                payment_status,
                download_unlocked,
                document_saved_path,
                document_saved_at,
                created_at,
                updated_at
            FROM document_products
            ORDER BY id DESC
            """
        ).fetchall()

        jobs = []

        for row in rows:
            item = dict(row)

            path = None

            try:
                path = find_saved_file_from_product(
                    item
                )
            except Exception:
                path = None

            item["document_saved"] = bool(
                path
            )

            item["document_filename"] = (
                path.name
                if path
                else ""
            )

            jobs.append(item)

        return {
            "ok": True,
            "jobs": jobs,
            "count": len(jobs),
        }

    finally:
        connection.close()


# ============================================================
# BACK OFFICE PAYMENT CHANNELS
# ============================================================

@app.get("/api/back-office/payment-channels")
def back_office_payment_channels(
    admin_key: str = "",
):
    require_back_office(
        admin_key
    )

    return {
        "ok": True,

        "channels": [
            {
                "name": "Bank Transfer",
                "enabled": True,
            },
            {
                "name": "USSD",
                "enabled": True,
            },
            {
                "name": "Cash",
                "enabled": True,
            },
        ],
    }


# ============================================================
# CUSTOMER DOWNLOAD
# ============================================================

@app.get("/api/download")
def customer_download(
    customer_id: str,
    product_id: str,
):
    product = get_product(
        customer_id,
        product_id,
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

    canonical = canonical_file_or_404(
        product
    )

    # EXACT CANONICAL DOCUMENT.
    return file_response(
        canonical,
        attachment=True,
    )


# ============================================================
# CUSTOMER DELIVERY CHANNELS
# ============================================================

@app.get("/api/delivery/channels")
def customer_delivery_channels(
    customer_id: str,
    product_id: str,
):
    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = canonical_file_or_404(
        product
    )

    return {
        "ok": True,

        "document_title":
            product.get(
                "document_title"
            ),

        "filename":
            canonical.name,

        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),

        "channels": {
            "phone": bool(
                product.get(
                    "customer_phone"
                )
            ),

            "whatsapp": bool(
                product.get(
                    "customer_phone"
                )
            ),

            "email": bool(
                product.get(
                    "customer_email"
                )
            ),

            "telegram": False,

            "google_drive": False,
        },
    }


# ============================================================
# CUSTOMER DELIVERY PREPARE
# ============================================================

@app.post("/api/delivery/prepare")
async def customer_delivery_prepare(
    request: Request,
):
    data = await request.json()

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    channel = clean(
        first(
            data,
            "channel",
            "delivery_channel",
        )
    ).lower()

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
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

    canonical = canonical_file_or_404(
        product
    )

    return {
        "ok": True,

        "channel":
            channel,

        "document_title":
            product.get(
                "document_title"
            ),

        "filename":
            canonical.name,

        "canonical_document":
            True,

        "download_url":
            "/api/download"
            + "?"
            + urllib.parse.urlencode(
                {
                    "customer_id":
                        customer_id,
                    "product_id":
                        product_id,
                }
            ),
    }


# ============================================================
# PRODUCT STATUS
# ============================================================

@app.get("/api/product/status")
def product_status(
    customer_id: str,
    product_id: str,
):
    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    path = existing_saved_file(
        product
    )

    return {
        "ok": True,

        "customer_id":
            customer_id,

        "product_id":
            product_id,

        "document_title":
            product.get(
                "document_title"
            ),

        "service_name":
            product.get(
                "service_name"
            ),

        "payment_status":
            product.get(
                "payment_status"
            ),

        "document_saved":
            bool(path),

        "document_filename":
            (
                path.name
                if path
                else ""
            ),

        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
def customer_care_payments(
    customer_id: str,
):
    connection = db(
        PAYMENT_DB_PATH
    )

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE customer_id = ?
            ORDER BY id DESC
            """,
            (customer_id,),
        ).fetchall()

        return {
            "ok": True,
            "payments": [
                dict(row)
                for row in rows
            ],
        }

    finally:
        connection.close()


# ============================================================
# CUSTOMER CARE PAYMENT VERIFY
# ============================================================

@app.post("/api/customer-care/payment/verify")
async def customer_care_payment_verify(
    request: Request,
):
    data = await request.json()

    customer_id = clean(
        first(
            data,
            "customer_id",
            "customerId",
        )
    )

    product_id = clean(
        first(
            data,
            "product_id",
            "productId",
        )
    )

    if not customer_id or not product_id:
        raise HTTPException(
            status_code=400,
            detail="CUSTOMER_AND_PRODUCT_REQUIRED",
        )

    product = get_product(
        customer_id,
        product_id,
    )

    if not product:
        raise HTTPException(
            status_code=404,
            detail="PRODUCT_NOT_FOUND",
        )

    canonical = repair_saved_snapshot(
        product
    )

    return {
        "ok": True,

        "payment_status":
            product.get(
                "payment_status"
            ),

        "document_title":
            product.get(
                "document_title"
            ),

        "document_saved":
            True,

        "document_filename":
            canonical.name,

        "download_unlocked":
            bool(
                product.get(
                    "download_unlocked"
                )
            ),
    }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "ok": True,
        "service":
            "Naija Pocket Business Center",
        "api":
            "payment and delivery",
        "version":
            APP_VERSION,
        "canonical_document_only":
            True,
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "status": "healthy",
        "version": APP_VERSION,
        "canonical_document_only": True,
    }
