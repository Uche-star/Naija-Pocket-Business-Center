from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


# ============================================================
# NAIJA POCKET BUSINESS CENTER
# payment_api.py
#
# PRODUCT-FIRST DOCUMENT DELIVERY
#
# PRIMARY PRODUCT IDENTITY:
#     SERVICE + DOCUMENT TITLE
#
# IMPORTANT:
#     The finished document exists independently of payment.
#
# PRODUCT FLOW:
#
# Review
#   ↓
# Exact reviewed document saved
#   ↓
# Product is complete
#   ↓
# Delivery is ready
#   ↓
# Download remains locked
#   ↓
# Customer Care may activate download
#   ↓
# Customer receives EXACT saved document
#
# PAYMENT:
#     Administrative information only.
#     Payment does NOT create the product.
#     Payment does NOT save the product.
#     Payment does NOT control document creation.
#     Payment does NOT control document storage.
#     Payment does NOT regenerate the document.
#
# ONLY DOWNLOAD ACTIVATION controls customer download.
# ============================================================


APP_VERSION = "payment-product-first-v4"

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# SEPARATE PRODUCT/DOCUMENT DATABASE
#
# The product must survive independently of payment records.
# ============================================================

PRODUCT_DB_PATH = Path(
    os.getenv(
        "PRODUCT_DB_PATH",
        str(BASE_DIR / "product_delivery.db"),
    )
)


# ============================================================
# PAYMENT DATABASE
#
# Payment is secondary administrative information.
# ============================================================

PAYMENT_DB_PATH = Path(
    os.getenv(
        "PAYMENT_DB_PATH",
        str(BASE_DIR / "payment_gateway.db"),
    )
)


DOWNLOAD_DIR = Path(
    os.getenv(
        "DOWNLOAD_DIR",
        str(BASE_DIR / "downloads"),
    )
)

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


DEFAULT_CURRENCY = "NGN"
DEFAULT_PAYMENT_METHOD = "bank_transfer"


BACK_OFFICE_ADMIN_KEY = os.getenv(
    "BACK_OFFICE_ADMIN_KEY",
    "",
).strip()


DELIVERY_EMAIL_WEBHOOK = os.getenv(
    "DELIVERY_EMAIL_WEBHOOK",
    "",
).strip()


DELIVERY_WHATSAPP_WEBHOOK = os.getenv(
    "DELIVERY_WHATSAPP_WEBHOOK",
    "",
).strip()


DELIVERY_TELEGRAM_WEBHOOK = os.getenv(
    "DELIVERY_TELEGRAM_WEBHOOK",
    "",
).strip()


DELIVERY_GOOGLE_DRIVE_WEBHOOK = os.getenv(
    "DELIVERY_GOOGLE_DRIVE_WEBHOOK",
    "",
).strip()


app = FastAPI(
    title="Naija Pocket Business Center Product & Payment API",
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
    return datetime.now(
        timezone.utc
    ).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def money(value: Any) -> float:
    try:
        return round(
            float(value),
            2,
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def json_response_error(
    code: str,
    message: str,
    status_code: int = 400,
    **extra: Any,
) -> JSONResponse:

    payload = {
        "ok": False,
        "error": code,
        "message": message,
    }

    payload.update(extra)

    return JSONResponse(
        payload,
        status_code=status_code,
    )


def normalize_status(
    status: Any,
) -> str:

    return (
        clean(status)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def payment_is_reported(
    status: str,
) -> bool:

    return normalize_status(
        status
    ) in {
        "reported",
        "payment_reported",
        "verification_pending",
        "awaiting_verification",
        "pending_verification",
    }


def payment_is_verified(
    status: str,
) -> bool:

    return normalize_status(
        status
    ) in {
        "verified",
        "approved",
        "paid",
        "payment_verified",
        "payment_confirmed",
        "confirmed",
        "activated",
        "completed",
        "complete",
    }


# ============================================================
# BUSINESS IDENTITY
# ============================================================

def body_service(
    body: dict[str, Any],
) -> str:

    candidates = [
        body.get("service"),
        body.get("selected_service"),
        body.get("service_name"),
        body.get("serviceName"),
    ]

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


def body_title(
    body: dict[str, Any],
) -> str:

    candidates = [
        body.get("title"),
        body.get("document_title"),
        body.get("documentTitle"),
        body.get("work_title"),
        body.get("workTitle"),
    ]

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


def body_customer(
    body: dict[str, Any],
) -> str:

    candidates = [
        body.get("customer_name"),
        body.get("customerName"),
        body.get("name"),
        body.get("customer"),
    ]

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


def body_amount(
    body: dict[str, Any],
) -> float:

    candidates = [
        body.get("amount"),
        body.get("price"),
        body.get("fee"),
        body.get("total"),
    ]

    for value in candidates:

        if value is not None and clean(value):
            return money(value)

    return 0.0


def body_payment_method(
    body: dict[str, Any],
) -> str:

    value = clean(
        body.get("payment_method")
        or body.get("paymentMethod")
        or DEFAULT_PAYMENT_METHOD
    )

    return value or DEFAULT_PAYMENT_METHOD


def body_note(
    body: dict[str, Any],
) -> str:

    candidates = [
        body.get("note"),
        body.get("customer_note"),
        body.get("customerNote"),
        body.get("message"),
    ]

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


def body_version(
    body: dict[str, Any],
) -> str:

    candidates = [
        body.get("version"),
        body.get("document_version"),
        body.get("version_id"),
        body.get("versionId"),
    ]

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


def business_key(
    service: str,
    title: str,
) -> str:

    return (
        f"{clean(service).casefold()}"
        f"::{clean(title).casefold()}"
    )


# ============================================================
# PRODUCT DATABASE
# ============================================================

def connect_product_db() -> sqlite3.Connection:

    PRODUCT_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(PRODUCT_DB_PATH)
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_product_db() -> None:

    with connect_product_db() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_products (
                business_key TEXT PRIMARY KEY,

                service TEXT NOT NULL,
                document_title TEXT NOT NULL,

                customer_name TEXT,

                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'NGN',

                document_version TEXT,
                document_filename TEXT,
                document_payload TEXT,

                document_saved_path TEXT,
                document_saved_at TEXT,

                download_unlocked INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                activated_at TEXT,

                downloaded_at TEXT,
                download_count INTEGER NOT NULL DEFAULT 0,

                customer_note TEXT,
                admin_note TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_document_products_service_title
            ON document_products(
                service,
                document_title
            )
            """
        )

        conn.commit()


# ============================================================
# PAYMENT DATABASE
# ============================================================

def connect_payment_db() -> sqlite3.Connection:

    PAYMENT_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(PAYMENT_DB_PATH)
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_payment_db() -> None:

    with connect_payment_db() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                business_key TEXT PRIMARY KEY,

                service TEXT NOT NULL,
                document_title TEXT NOT NULL,

                customer_name TEXT,

                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'NGN',

                payment_method TEXT NOT NULL
                    DEFAULT 'bank_transfer',

                payment_status TEXT NOT NULL
                    DEFAULT 'pending',

                customer_note TEXT,
                admin_note TEXT,

                document_version TEXT,
                document_filename TEXT,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                reported_at TEXT,
                verified_at TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        conn.commit()


# ============================================================
# PRODUCT LOOKUP
# ============================================================

def get_product(
    service: str,
    title: str,
) -> dict[str, Any] | None:

    service = clean(service)
    title = clean(title)

    if not service or not title:
        return None

    key = business_key(
        service,
        title,
    )

    with connect_product_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM document_products
            WHERE business_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()

    return (
        dict(row)
        if row
        else None
    )


def get_single_product() -> dict[str, Any] | None:

    with connect_product_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY created_at DESC
            """
        ).fetchall()

    if len(rows) != 1:
        return None

    return dict(
        rows[0]
    )


# ============================================================
# PAYMENT LOOKUP
# ============================================================

def get_payment(
    service: str,
    title: str,
) -> dict[str, Any] | None:

    service = clean(service)
    title = clean(title)

    if not service or not title:
        return None

    key = business_key(
        service,
        title,
    )

    try:

        with connect_payment_db() as conn:

            row = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE business_key = ?
                LIMIT 1
                """,
                (key,),
            ).fetchone()

        return (
            dict(row)
            if row
            else None
        )

    except Exception:

        # Payment failure must never prevent
        # product delivery infrastructure.

        return None


def get_active_payment_if_single() -> dict[str, Any] | None:

    try:

        with connect_payment_db() as conn:

            rows = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE payment_status IN (
                    'pending',
                    'reported',
                    'payment_reported',
                    'verification_pending',
                    'awaiting_verification',
                    'pending_verification'
                )
                ORDER BY created_at DESC
                """
            ).fetchall()

        if len(rows) != 1:
            return None

        return dict(
            rows[0]
        )

    except Exception:

        return None


# ============================================================
# PRODUCT WRITE
# ============================================================

def upsert_product(
    *,
    service: str,
    title: str,
    customer_name: str,
    amount: float,
    currency: str,
    document_version: str,
    document_filename: str,
    document_payload: dict[str, Any],
) -> dict[str, Any]:

    service = clean(service)
    title = clean(title)

    key = business_key(
        service,
        title,
    )

    timestamp = now_iso()

    with connect_product_db() as conn:

        existing = conn.execute(
            """
            SELECT *
            FROM document_products
            WHERE business_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()

        if existing:

            # Existing product remains the source of truth.
            # Never reset its download activation here.

            conn.execute(
                """
                UPDATE document_products
                SET
                    customer_name =
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE customer_name
                        END,

                    amount =
                        CASE
                            WHEN ? > 0
                            THEN ?
                            ELSE amount
                        END,

                    currency =
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE currency
                        END,

                    document_version =
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE document_version
                        END,

                    document_filename =
                        CASE
                            WHEN ? <> ''
                            THEN ?
                            ELSE document_filename
                        END,

                    document_payload =
                        CASE
                            WHEN document_saved_path IS NULL
                            THEN ?
                            ELSE document_payload
                        END,

                    updated_at = ?

                WHERE business_key = ?
                """,
                (
                    clean(customer_name),
                    clean(customer_name),

                    money(amount),
                    money(amount),

                    clean(currency),
                    clean(currency),

                    clean(document_version),
                    clean(document_version),

                    clean(document_filename),
                    clean(document_filename),

                    json.dumps(
                        document_payload,
                        ensure_ascii=False,
                    ),

                    timestamp,
                    key,
                ),
            )

        else:

            conn.execute(
                """
                INSERT INTO document_products (
                    business_key,
                    service,
                    document_title,
                    customer_name,
                    amount,
                    currency,
                    document_version,
                    document_filename,
                    document_payload,
                    document_saved_path,
                    document_saved_at,
                    download_unlocked,
                    created_at,
                    updated_at,
                    activated_at,
                    downloaded_at,
                    download_count,
                    customer_note,
                    admin_note
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    NULL, NULL,
                    0,
                    ?, ?,
                    NULL, NULL,
                    0,
                    NULL, NULL
                )
                """,
                (
                    key,
                    service,
                    title,
                    clean(customer_name),
                    money(amount),
                    clean(currency)
                    or DEFAULT_CURRENCY,

                    clean(document_version),
                    clean(document_filename),

                    json.dumps(
                        document_payload,
                        ensure_ascii=False,
                    ),

                    timestamp,
                    timestamp,
                ),
            )

        conn.commit()

    result = get_product(
        service,
        title,
    )

    if result is None:
        raise RuntimeError(
            "The finished product could not be recovered after saving."
        )

    return result


# ============================================================
# PAYMENT WRITE
#
# This is deliberately best-effort.
#
# A payment database problem MUST NOT destroy the product.
# ============================================================

def ensure_payment_record(
    product: dict[str, Any],
) -> dict[str, Any] | None:

    try:

        timestamp = now_iso()

        key = business_key(
            product["service"],
            product["document_title"],
        )

        with connect_payment_db() as conn:

            existing = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE business_key = ?
                LIMIT 1
                """,
                (key,),
            ).fetchone()

            if existing:

                return dict(
                    existing
                )

            conn.execute(
                """
                INSERT INTO payment_orders (
                    business_key,
                    service,
                    document_title,
                    customer_name,
                    amount,
                    currency,
                    payment_method,
                    payment_status,
                    customer_note,
                    admin_note,
                    document_version,
                    document_filename,
                    created_at,
                    updated_at,
                    reported_at,
                    verified_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    'pending',
                    NULL, NULL,
                    ?, ?,
                    ?, ?,
                    NULL, NULL
                )
                """,
                (
                    key,
                    product["service"],
                    product["document_title"],
                    clean(
                        product.get(
                            "customer_name"
                        )
                    ),
                    money(
                        product.get(
                            "amount"
                        )
                    ),
                    clean(
                        product.get(
                            "currency"
                        )
                    ) or DEFAULT_CURRENCY,
                    DEFAULT_PAYMENT_METHOD,

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

                    timestamp,
                    timestamp,
                ),
            )

            conn.commit()

        return get_payment(
            product["service"],
            product["document_title"],
        )

    except Exception:

        # Product remains complete even if
        # payment administration fails.

        return None


def update_payment_record(
    service: str,
    title: str,
    *,
    status: str | None = None,
    customer_note: str | None = None,
    admin_note: str | None = None,
    verified_at: str | None = None,
    reported_at: str | None = None,
) -> dict[str, Any] | None:

    current = get_payment(
        service,
        title,
    )

    if not current:
        return None

    fields: list[str] = []
    values: list[Any] = []

    if status is not None:

        fields.append(
            "payment_status = ?"
        )

        values.append(
            status
        )

    if customer_note is not None:

        fields.append(
            "customer_note = ?"
        )

        values.append(
            customer_note
        )

    if admin_note is not None:

        fields.append(
            "admin_note = ?"
        )

        values.append(
            admin_note
        )

    if verified_at is not None:

        fields.append(
            "verified_at = ?"
        )

        values.append(
            verified_at
        )

    if reported_at is not None:

        fields.append(
            "reported_at = ?"
        )

        values.append(
            reported_at
        )

    fields.append(
        "updated_at = ?"
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

    try:

        with connect_payment_db() as conn:

            conn.execute(
                f"""
                UPDATE payment_orders
                SET {", ".join(fields)}
                WHERE business_key = ?
                """,
                tuple(values),
            )

            conn.commit()

    except Exception:

        return None

    return get_payment(
        service,
        title,
    )


# ============================================================
# DOCUMENT NORMALIZATION
# ============================================================

def normalize_pages(
    pages: Any,
) -> list[str]:

    if pages is None:
        return []

    result: list[str] = []

    if isinstance(
        pages,
        str,
    ):

        text = clean(
            pages
        )

        if text:
            result.append(
                text
            )

        return result

    if isinstance(
        pages,
        dict,
    ):

        pages = [
            pages
        ]

    if not isinstance(
        pages,
        list,
    ):

        return []

    for page in pages:

        if isinstance(
            page,
            str,
        ):

            text = clean(
                page
            )

        elif isinstance(
            page,
            dict,
        ):

            text = clean(
                page.get("content")
                or page.get("text")
                or page.get("body")
                or page.get("page_text")
            )

        else:

            text = clean(
                page
            )

        if text:
            result.append(
                text
            )

    return result


def normalize_document(
    document: Any,
) -> dict[str, Any]:

    if not isinstance(
        document,
        dict,
    ):

        document = {
            "document_text": clean(
                document
            )
        }

    document_text = clean(
        document.get("document_text")
        or document.get("text")
        or document.get("content")
        or document.get("document")
    )

    pages = normalize_pages(
        document.get("pages")
        or document.get("document_pages")
        or document.get("prepared_pages")
        or document.get("content_pages")
    )

    if not pages and document_text:

        pages = [
            document_text
        ]

    if not document_text and pages:

        document_text = "\n\n".join(
            pages
        )

    return {
        "document_text": document_text,

        "pages": pages,

        "filename": clean(
            document.get("filename")
            or document.get("document_filename")
        ),

        "version": clean(
            document.get("version")
            or document.get("document_version")
        ),

        "metadata": (
            document.get("metadata")
            if isinstance(
                document.get("metadata"),
                dict,
            )
            else {}
        ),
    }


def snapshot_payload(
    document: dict[str, Any],
) -> dict[str, Any]:

    normalized = normalize_document(
        document
    )

    return {
        "document_text":
            normalized["document_text"],

        "pages":
            normalized["pages"],

        "filename":
            normalized["filename"],

        "version":
            normalized["version"],

        "metadata":
            normalized["metadata"],
    }


# ============================================================
# SAFE FILE NAMES
# ============================================================

def safe_filename(
    value: str,
    fallback: str = "document",
) -> str:

    value = clean(
        value
    )

    if not value:
        value = fallback

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    return (
        value[:180]
        or fallback
    )


def safe_folder_name(
    service: str,
    title: str,
) -> str:

    return safe_filename(
        f"{service} - {title}",
        "document",
    )


# ============================================================
# DOCX CREATION
# ============================================================

def make_docx(
    pages: list[str],
    output_path: str,
) -> str:

    output = Path(
        output_path
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship
        Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        Target="word/document.xml"/>
</Relationships>"""

    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
</Relationships>"""

    body_parts: list[str] = []

    for page_index, page in enumerate(
        pages,
        start=1,
    ):

        if page_index > 1:

            body_parts.append(
                '<w:p>'
                '<w:r>'
                '<w:br w:type="page"/>'
                '</w:r>'
                '</w:p>'
            )

        lines = page.splitlines()

        if not lines:
            lines = [""]

        for line in lines:

            body_parts.append(
                "<w:p>"
                "<w:r>"
                f'<w:t xml:space="preserve">{escape(line)}</w:t>'
                "</w:r>"
                "</w:p>"
            )

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body>
        {''.join(body_parts)}
        <w:sectPr>
            <w:pgSz w:w="12240" w:h="15840"/>
            <w:pgMar
                w:top="1440"
                w:right="1440"
                w:bottom="1440"
                w:left="1440"/>
        </w:sectPr>
    </w:body>
</w:document>"""

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
    ) as archive:

        archive.writestr(
            "[Content_Types].xml",
            content_types,
        )

        archive.writestr(
            "_rels/.rels",
            rels,
        )

        archive.writestr(
            "word/document.xml",
            document_xml,
        )

        archive.writestr(
            "word/_rels/document.xml.rels",
            document_rels,
        )

    return str(
        output
    )


# ============================================================
# EXACT PRODUCT DOCUMENT
# ============================================================

def saved_document_path(
    product: dict[str, Any],
) -> Path | None:

    raw = clean(
        product.get(
            "document_saved_path"
        )
    )

    if not raw:
        return None

    path = Path(
        raw
    )

    if not path.is_absolute():

        path = (
            BASE_DIR
            / path
        )

    return path


def saved_document_exists(
    product: dict[str, Any],
) -> bool:

    path = saved_document_path(
        product
    )

    return bool(
        path
        and path.is_file()
        and path.stat().st_size > 0
    )


def save_exact_document_snapshot(
    product: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:

    # NEVER replace an existing exact snapshot.

    if saved_document_exists(
        product
    ):

        return (
            True,
            str(
                saved_document_path(
                    product
                )
            ),
        )

    normalized = normalize_document(
        document
    )

    pages = normalized[
        "pages"
    ]

    if not pages:

        return (
            False,
            "The reviewed document contains no downloadable content.",
        )

    filename = (
        normalized["filename"]
        or clean(
            product.get(
                "document_filename"
            )
        )
        or "document"
    )

    filename = safe_filename(
        filename
    )

    if not filename.lower().endswith(
        ".docx"
    ):

        filename += ".docx"

    folder = (
        DOWNLOAD_DIR
        / safe_folder_name(
            product.get(
                "service",
                "service",
            ),
            product.get(
                "document_title",
                "document",
            ),
        )
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        folder
        / filename
    )

    try:

        make_docx(
            pages,
            str(output),
        )

        if not (
            output.is_file()
            and output.stat().st_size > 0
        ):

            return (
                False,
                "The reviewed document snapshot was not saved correctly.",
            )

        timestamp = now_iso()

        with connect_product_db() as conn:

            conn.execute(
                """
                UPDATE document_products
                SET
                    document_saved_path = ?,
                    document_saved_at = ?,
                    document_filename = ?,
                    updated_at = ?
                WHERE business_key = ?
                """,
                (
                    str(output),
                    timestamp,
                    filename,
                    timestamp,
                    business_key(
                        product["service"],
                        product["document_title"],
                    ),
                ),
            )

            conn.commit()

        return (
            True,
            str(output),
        )

    except Exception as exc:

        try:

            if output.exists():
                output.unlink()

        except Exception:
            pass

        return (
            False,
            str(exc),
        )


# ============================================================
# PRODUCT ACTIVATION
# ============================================================

def product_download_unlocked(
    product: dict[str, Any],
) -> bool:

    return bool(
        int(
            product.get(
                "download_unlocked"
            )
            or 0
        )
    )


def activate_product_download(
    service: str,
    title: str,
    admin_note: str = "",
) -> dict[str, Any] | None:

    product = get_product(
        service,
        title,
    )

    if product is None:
        return None

    timestamp = now_iso()

    with connect_product_db() as conn:

        conn.execute(
            """
            UPDATE document_products
            SET
                download_unlocked = 1,
                activated_at = ?,
                admin_note = ?,
                updated_at = ?
            WHERE business_key = ?
            """,
            (
                timestamp,
                clean(admin_note)
                or None,
                timestamp,
                business_key(
                    service,
                    title,
                ),
            ),
        )

        conn.commit()

    return get_product(
        service,
        title,
    )


# ============================================================
# PRODUCT PUBLIC RESPONSE
# ============================================================

def product_public(
    product: dict[str, Any],
) -> dict[str, Any]:

    payment = get_payment(
        product["service"],
        product["document_title"],
    )

    payment_status = (
        normalize_status(
            payment.get(
                "payment_status"
            )
        )
        if payment
        else "not_started"
    )

    return {
        "ok": True,

        "service": clean(
            product.get(
                "service"
            )
        ),

        "title": clean(
            product.get(
                "document_title"
            )
        ),

        "customer_name": clean(
            product.get(
                "customer_name"
            )
        ),

        "amount": money(
            product.get(
                "amount"
            )
        ),

        "currency": clean(
            product.get(
                "currency"
            )
        ) or DEFAULT_CURRENCY,

        "document_version": clean(
            product.get(
                "document_version"
            )
        ),

        "document_filename": clean(
            product.get(
                "document_filename"
            )
        ),

        "document_saved": saved_document_exists(
            product
        ),

        "document_saved_at": clean(
            product.get(
                "document_saved_at"
            )
        ),

        # THIS is the actual download gate.
        "download_unlocked":
            product_download_unlocked(
                product
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

        "downloaded_at": clean(
            product.get(
                "downloaded_at"
            )
        ),

        "download_count": int(
            product.get(
                "download_count"
            )
            or 0
        ),

        # Payment information is secondary.
        "payment_status":
            payment_status,

        "payment_reported":
            payment_is_reported(
                payment_status
            ),

        "payment_verified":
            payment_is_verified(
                payment_status
            ),

        "customer_note": clean(
            product.get(
                "customer_note"
            )
        ),

        "admin_note": clean(
            product.get(
                "admin_note"
            )
        ),

        "product_ready": (
            saved_document_exists(
                product
            )
        ),
    }


# ============================================================
# REQUEST DOCUMENT EXTRACTION
# ============================================================

def extract_document(
    body: dict[str, Any],
) -> dict[str, Any] | None:

    candidates = [
        body.get("document"),
        body.get("reviewed_document"),
        body.get("reviewedDocument"),
        body.get("document_data"),
        body.get("documentData"),
    ]

    for candidate in candidates:

        if isinstance(
            candidate,
            dict,
        ):

            normalized = normalize_document(
                candidate
            )

            if (
                normalized["pages"]
                or normalized["document_text"]
            ):

                return normalized

    direct = normalize_document(
        {
            "document_text": (
                body.get(
                    "document_text"
                )
                or body.get(
                    "documentText"
                )
                or body.get(
                    "text"
                )
                or body.get(
                    "content"
                )
            ),

            "pages": (
                body.get(
                    "pages"
                )
                or body.get(
                    "document_pages"
                )
                or body.get(
                    "documentPages"
                )
            ),

            "filename": (
                body.get(
                    "filename"
                )
                or body.get(
                    "document_filename"
                )
            ),

            "version": body_version(
                body
            ),
        }
    )

    if (
        direct["pages"]
        or direct["document_text"]
    ):

        return direct

    return None


# ============================================================
# PAYMENT CREATE / PREPARE
#
# IMPORTANT:
#
# Despite the endpoint name retained for compatibility,
# PRODUCT CREATION HAPPENS FIRST.
#
# Payment cannot block the product.
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: Request,
):

    try:

        body = await request.json()

    except Exception:

        return json_response_error(
            "INVALID_REQUEST",
            "The payment request could not be read.",
        )

    if not isinstance(
        body,
        dict,
    ):

        return json_response_error(
            "INVALID_REQUEST",
            "Invalid payment request.",
        )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    if not service:

        return json_response_error(
            "SERVICE_REQUIRED",
            "The service is required.",
        )

    if not title:

        return json_response_error(
            "TITLE_REQUIRED",
            "The document title is required.",
        )

    document = extract_document(
        body
    )

    if document is None:

        return json_response_error(
            "DOCUMENT_REQUIRED",
            "The exact reviewed document is required before delivery can be prepared.",
        )

    payload = snapshot_payload(
        document
    )

    # --------------------------------------------------------
    # STEP 1
    # PRODUCT IS CREATED FIRST.
    # --------------------------------------------------------

    try:

        product = upsert_product(
            service=service,
            title=title,
            customer_name=body_customer(
                body
            ),
            amount=body_amount(
                body
            ),
            currency=clean(
                body.get(
                    "currency"
                )
            ) or DEFAULT_CURRENCY,
            document_version=(
                body_version(body)
                or clean(
                    document.get(
                        "version"
                    )
                )
            ),
            document_filename=clean(
                document.get(
                    "filename"
                )
            ),
            document_payload=payload,
        )

    except Exception as exc:

        return json_response_error(
            "PRODUCT_CREATE_FAILED",
            str(exc),
            500,
        )

    # --------------------------------------------------------
    # STEP 2
    # EXACT PRODUCT DOCUMENT IS SAVED.
    # --------------------------------------------------------

    saved, result = (
        save_exact_document_snapshot(
            product,
            document,
        )
    )

    if not saved:

        return json_response_error(
            "DOCUMENT_SAVE_FAILED",
            result,
            500,
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product could not be recovered.",
            500,
        )

    # --------------------------------------------------------
    # STEP 3
    # PAYMENT IS OPTIONAL ADMINISTRATION.
    #
    # A payment DB failure DOES NOT invalidate the product.
    # --------------------------------------------------------

    ensure_payment_record(
        product
    )

    final_product = get_product(
        service,
        title,
    )

    if final_product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product could not be recovered.",
            500,
        )

    response = product_public(
        final_product
    )

    response[
        "message"
    ] = (
        "Your exact reviewed document has been "
        "saved and is ready for delivery."
    )

    return response


# ============================================================
# PAYMENT REPORT
#
# Payment reporting NEVER controls product existence.
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    request: Request,
):

    try:

        body = await request.json()

    except Exception:

        return json_response_error(
            "INVALID_REQUEST",
            "The payment report could not be read.",
        )

    if not isinstance(
        body,
        dict,
    ):

        return json_response_error(
            "INVALID_REQUEST",
            "Invalid payment report.",
        )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    product = None

    if service and title:

        product = get_product(
            service,
            title,
        )

    if product is None:

        product = get_single_product()

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished document could not be found.",
            404,
        )

    service = product[
        "service"
    ]

    title = product[
        "document_title"
    ]

    # The finished product must exist.
    if not saved_document_exists(
        product
    ):

        return json_response_error(
            "DOCUMENT_NOT_SAVED",
            "The exact finished document is not available.",
        )

    # Create payment administration only now.
    payment = ensure_payment_record(
        product
    )

    if payment is None:

        # Payment administration failed,
        # but this does NOT invalidate the product.
        return {
            **product_public(product),
            "message": (
                "The finished document is saved. "
                "Payment administration is currently unavailable."
            ),
        }

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(
        status
    ):

        response = product_public(
            product
        )

        response[
            "message"
        ] = "Payment has already been recorded."

        return response

    note = body_note(
        body
    )

    updated = update_payment_record(
        service,
        title,
        status="reported",
        customer_note=(
            note
            if note
            else None
        ),
        reported_at=now_iso(),
    )

    response = product_public(
        product
    )

    if updated:

        response[
            "message"
        ] = (
            "Payment Reported — Awaiting Verification"
        )

    else:

        response[
            "message"
        ] = (
            "Your finished document remains ready. "
            "Payment administration could not be updated."
        )

    return response


# ============================================================
# PAYMENT STATUS
#
# This endpoint reports payment information only.
# It does NOT determine product readiness.
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    service: str = "",
    title: str = "",
):

    service = clean(
        service
    )

    title = clean(
        title
    )

    if not service or not title:

        product = get_single_product()

        if product is None:

            return json_response_error(
                "SERVICE_AND_TITLE_REQUIRED",
                "Service and document title are required.",
            )

    else:

        product = get_product(
            service,
            title,
        )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "No finished document was found.",
            404,
        )

    return product_public(
        product
    )


# ============================================================
# COMPATIBILITY ALIAS
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: Request,
):

    return await payment_report(
        request
    )


# ============================================================
# CUSTOMER CARE PAYMENT LIST
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():

    try:

        with connect_payment_db() as conn:

            rows = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE payment_status IN (
                    'reported',
                    'payment_reported',
                    'verification_pending',
                    'awaiting_verification',
                    'pending_verification'
                )
                ORDER BY reported_at DESC, created_at DESC
                """
            ).fetchall()

    except Exception:

        rows = []

    payments = []

    for row in rows:

        payment = dict(
            row
        )

        product = get_product(
            payment["service"],
            payment["document_title"],
        )

        if product:

            payments.append(
                product_public(
                    product
                )
            )

    return {
        "ok": True,
        "count": len(payments),
        "payments": payments,
    }


# ============================================================
# CUSTOMER CARE PAYMENT VERIFY
#
# Verification is administrative.
# It does NOT regenerate the document.
#
# Explicit DOWNLOAD ACTIVATION remains the actual gate.
# ============================================================

@app.post("/api/customer-care/payment/verify")
async def customer_care_verify(
    request: Request,
):

    try:

        body = await request.json()

    except Exception:

        return json_response_error(
            "INVALID_REQUEST",
            "The verification request could not be read.",
        )

    if not isinstance(
        body,
        dict,
    ):

        return json_response_error(
            "INVALID_REQUEST",
            "Invalid verification request.",
        )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    if not service or not title:

        return json_response_error(
            "SERVICE_AND_TITLE_REQUIRED",
            "Service and document title are required.",
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished document was not found.",
            404,
        )

    if not saved_document_exists(
        product
    ):

        return json_response_error(
            "DOCUMENT_NOT_SAVED",
            "The exact saved document is not available.",
        )

    action = normalize_status(
        body.get(
            "action"
        )
        or body.get(
            "decision"
        )
        or "verify"
    )

    if action in {
        "reject",
        "rejected",
        "decline",
        "declined",
    }:

        ensure_payment_record(
            product
        )

        update_payment_record(
            service,
            title,
            status="rejected",
            admin_note=(
                body_note(body)
                or None
            ),
        )

        return {
            **product_public(product),
            "message": "Payment rejected.",
        }

    ensure_payment_record(
        product
    )

    update_payment_record(
        service,
        title,
        status="verified",
        admin_note=(
            body_note(body)
            or None
        ),
        verified_at=now_iso(),
    )

    # IMPORTANT:
    # Verification does not regenerate the document.
    # It also does not silently replace the product.
    #
    # Customer Care has a separate explicit activation action.

    refreshed = get_product(
        service,
        title,
    )

    return {
        **product_public(
            refreshed
            or product
        ),
        "message": (
            "Payment verified. "
            "Download remains under Customer Care activation."
        ),
    }


# ============================================================
# BACK OFFICE AUTH
# ============================================================

def check_back_office_key(
    request: Request,
) -> bool:

    if not BACK_OFFICE_ADMIN_KEY:

        return True

    supplied = clean(
        request.headers.get(
            "X-Back-Office-Key"
        )
    )

    if not supplied:

        supplied = clean(
            request.query_params.get(
                "key"
            )
        )

    return (
        supplied
        == BACK_OFFICE_ADMIN_KEY
    )


def require_back_office_key(
    request: Request,
) -> JSONResponse | None:

    if check_back_office_key(
        request
    ):

        return None

    return json_response_error(
        "BACK_OFFICE_UNAUTHORIZED",
        "Back Office access is not authorized.",
        401,
    )


# ============================================================
# BACK OFFICE LOGIN
# ============================================================

@app.post("/api/back-office/login")
async def back_office_login(
    request: Request,
):

    if check_back_office_key(
        request
    ):

        return {
            "ok": True,
            "authenticated": True,
            "message": "Back Office access granted.",
        }

    return json_response_error(
        "BACK_OFFICE_UNAUTHORIZED",
        "Invalid Back Office access key.",
        401,
    )


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
async def back_office_payments(
    request: Request,
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    try:

        with connect_payment_db() as conn:

            rows = conn.execute(
                """
                SELECT *
                FROM payment_orders
                ORDER BY updated_at DESC
                """
            ).fetchall()

    except Exception:

        rows = []

    payments = []

    for row in rows:

        payment = dict(
            row
        )

        product = get_product(
            payment["service"],
            payment["document_title"],
        )

        if product:

            payments.append(
                product_public(
                    product
                )
            )

    return {
        "ok": True,
        "count": len(payments),
        "payments": payments,
    }


# ============================================================
# BACK OFFICE JOBS
#
# Compatibility endpoint.
#
# These are PRODUCTS, not payment IDs/jobs.
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs():

    with connect_product_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM document_products
            ORDER BY updated_at DESC
            """
        ).fetchall()

    products = []

    for row in rows:

        product = dict(
            row
        )

        public = product_public(
            product
        )

        public[
            "status"
        ] = (
            "activated"
            if public[
                "download_unlocked"
            ]
            else "ready"
        )

        public[
            "approved"
        ] = True

        public[
            "activated"
        ] = public[
            "download_unlocked"
        ]

        products.append(
            public
        )

    return {
        "ok": True,
        "count": len(products),
        "jobs": products,
    }


# ============================================================
# BACK OFFICE SINGLE PRODUCT
# ============================================================

@app.get("/api/back-office/payment")
async def back_office_payment(
    request: Request,
    service: str = "",
    title: str = "",
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    product = get_product(
        clean(service),
        clean(title),
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "No finished product was found.",
            404,
        )

    return product_public(
        product
    )


# ============================================================
# DOCUMENT INFO
# ============================================================

@app.get("/api/back-office/document-info")
async def back_office_document_info(
    request: Request,
    service: str = "",
    title: str = "",
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    product = get_product(
        clean(service),
        clean(title),
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "No finished product was found.",
            404,
        )

    path = saved_document_path(
        product
    )

    return {
        "ok": True,

        "service": product[
            "service"
        ],

        "title": product[
            "document_title"
        ],

        "document_saved":
            saved_document_exists(
                product
            ),

        "document_filename":
            clean(
                product.get(
                    "document_filename"
                )
            ),

        "document_saved_at":
            clean(
                product.get(
                    "document_saved_at"
                )
            ),

        "document_exists": bool(
            path
            and path.is_file()
        ),

        "download_unlocked":
            product_download_unlocked(
                product
            ),
    }


# ============================================================
# BACK OFFICE DOCUMENT
#
# Exact saved product document.
#
# NO PAYMENT VERIFICATION REQUIRED.
# Customer Care can inspect the finished product
# regardless of payment status.
# ============================================================

@app.get("/api/back-office/document")
async def back_office_document(
    request: Request,
    service: str = "",
    title: str = "",
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    product = get_product(
        clean(service),
        clean(title),
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "No finished product was found.",
            404,
        )

    path = saved_document_path(
        product
    )

    if not (
        path
        and path.is_file()
        and path.stat().st_size > 0
    ):

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "The exact saved document is not available.",
            404,
        )

    return FileResponse(
        path=str(path),
        filename=(
            clean(
                product.get(
                    "document_filename"
                )
            )
            or path.name
        ),
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
    )


# ============================================================
# BACK OFFICE VERIFY
# ============================================================

@app.post("/api/back-office/payment/verify")
async def back_office_payment_verify(
    request: Request,
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    return await customer_care_verify(
        request
    )


# ============================================================
# ACTIVATE DOWNLOAD
#
# THIS IS THE ONLY DOWNLOAD GATE.
#
# No payment lookup is required.
# No payment status is required.
# No reference is required.
# No payment ID is required.
#
# Customer Care decides when the finished product
# becomes downloadable.
# ============================================================

@app.post("/api/back-office/activate-download")
async def activate_download(
    request: Request,
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    try:

        body = await request.json()

    except Exception:

        return json_response_error(
            "INVALID_REQUEST",
            "The activation request could not be read.",
        )

    if not isinstance(
        body,
        dict,
    ):

        return json_response_error(
            "INVALID_REQUEST",
            "Invalid activation request.",
        )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    if not service or not title:

        return json_response_error(
            "SERVICE_AND_TITLE_REQUIRED",
            "Service and document title are required.",
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product was not found.",
            404,
        )

    if not saved_document_exists(
        product
    ):

        return json_response_error(
            "DOCUMENT_NOT_SAVED",
            "The exact saved document is not available.",
        )

    activated = activate_product_download(
        service,
        title,
        body_note(body),
    )

    if activated is None:

        return json_response_error(
            "ACTIVATION_FAILED",
            "Download activation failed.",
            500,
        )

    response = product_public(
        activated
    )

    response[
        "message"
    ] = "Download unlocked."

    return response


# ============================================================
# BACK OFFICE REJECT
# ============================================================

@app.post("/api/back-office/payment/reject")
async def back_office_payment_reject(
    request: Request,
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    try:

        body = await request.json()

    except Exception:

        return json_response_error(
            "INVALID_REQUEST",
            "The rejection request could not be read.",
        )

    if not isinstance(
        body,
        dict,
    ):

        return json_response_error(
            "INVALID_REQUEST",
            "Invalid rejection request.",
        )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    if not service or not title:

        return json_response_error(
            "SERVICE_AND_TITLE_REQUIRED",
            "Service and document title are required.",
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product was not found.",
            404,
        )

    ensure_payment_record(
        product
    )

    update_payment_record(
        service,
        title,
        status="rejected",
        admin_note=(
            body_note(body)
            or None
        ),
    )

    refreshed = get_product(
        service,
        title,
    )

    response = product_public(
        refreshed
        or product
    )

    response[
        "message"
    ] = "Payment rejected."

    return response


# ============================================================
# CUSTOMER DOWNLOAD
#
# PRODUCT-FIRST:
#
# The ONLY condition here is:
#
#     download_unlocked == 1
#
# Payment status is NOT checked.
# ============================================================

@app.get("/api/download")
async def download_document(
    service: str = "",
    title: str = "",
):

    service = clean(
        service
    )

    title = clean(
        title
    )

    if not service or not title:

        return json_response_error(
            "SERVICE_AND_TITLE_REQUIRED",
            "Service and document title are required.",
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished document was not found.",
            404,
        )

    # --------------------------------------------------------
    # THE ONLY DOWNLOAD LOCK.
    # --------------------------------------------------------

    if not product_download_unlocked(
        product
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            "Download is waiting for Customer Care activation.",
            403,
        )

    path = saved_document_path(
        product
    )

    if not (
        path
        and path.is_file()
        and path.stat().st_size > 0
    ):

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "The exact saved document is not available.",
            404,
        )

    timestamp = now_iso()

    with connect_product_db() as conn:

        conn.execute(
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
                business_key(
                    service,
                    title,
                ),
            ),
        )

        conn.commit()

    return FileResponse(
        path=str(path),
        filename=(
            clean(
                product.get(
                    "document_filename"
                )
            )
            or path.name
        ),
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
    )


# ============================================================
# DELIVERY WEBHOOK
# ============================================================

def webhook_for_channel(
    channel: str,
) -> str:

    channel = normalize_status(
        channel
    )

    mapping = {
        "email":
            DELIVERY_EMAIL_WEBHOOK,

        "whatsapp":
            DELIVERY_WHATSAPP_WEBHOOK,

        "telegram":
            DELIVERY_TELEGRAM_WEBHOOK,

        "google_drive":
            DELIVERY_GOOGLE_DRIVE_WEBHOOK,

        "googledrive":
            DELIVERY_GOOGLE_DRIVE_WEBHOOK,
    }

    return mapping.get(
        channel,
        "",
    )


def send_delivery_webhook(
    webhook: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:

    if not webhook:

        return (
            False,
            "The selected delivery channel is not configured.",
        )

    data = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        webhook,
        data=data,
        headers={
            "Content-Type":
                "application/json",

            "Accept":
                "application/json",
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

            return (
                True,
                raw
                or "Delivery request accepted.",
            )

    except urllib.error.HTTPError as exc:

        return (
            False,
            f"Delivery service returned HTTP {exc.code}.",
        )

    except Exception as exc:

        return (
            False,
            str(exc),
        )


# ============================================================
# BACK OFFICE DELIVERY
#
# Customer Care can access the finished product.
#
# Direct customer delivery still respects the explicit
# Customer Care download activation.
# ============================================================

@app.post("/api/back-office/delivery")
async def back_office_delivery(
    request: Request,
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    try:

        body = await request.json()

    except Exception:

        return json_response_error(
            "INVALID_REQUEST",
            "The delivery request could not be read.",
        )

    if not isinstance(
        body,
        dict,
    ):

        return json_response_error(
            "INVALID_REQUEST",
            "Invalid delivery request.",
        )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    channel = normalize_status(
        body.get(
            "channel"
        )
    )

    recipient = clean(
        body.get(
            "recipient"
        )
        or body.get(
            "email"
        )
        or body.get(
            "phone"
        )
        or body.get(
            "whatsapp"
        )
        or body.get(
            "telegram"
        )
    )

    if not service or not title:

        return json_response_error(
            "SERVICE_AND_TITLE_REQUIRED",
            "Service and document title are required.",
        )

    if not channel:

        return json_response_error(
            "DELIVERY_CHANNEL_REQUIRED",
            "A delivery channel is required.",
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product was not found.",
            404,
        )

    path = saved_document_path(
        product
    )

    if not (
        path
        and path.is_file()
        and path.stat().st_size > 0
    ):

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "The exact saved document is not available.",
            404,
        )

    # --------------------------------------------------------
    # DIRECT CUSTOMER DOWNLOAD
    # --------------------------------------------------------

    if channel in {
        "direct",
        "phone",
        "download",
    }:

        if not product_download_unlocked(
            product
        ):

            return json_response_error(
                "DOWNLOAD_LOCKED",
                "Customer Care has not activated download.",
                403,
            )

        return {
            "ok": True,
            "channel": channel,
            "service": service,
            "title": title,
            "filename": path.name,
            "download_url": (
                "/api/download"
                "?service="
                + urllib.parse.quote(
                    service,
                    safe="",
                )
                + "&title="
                + urllib.parse.quote(
                    title,
                    safe="",
                )
            ),
            "message": (
                "The exact saved document is ready for download."
            ),
        }

    # --------------------------------------------------------
    # CUSTOMER SERVICE / MANUAL DELIVERY
    #
    # Customer Care may access the finished document
    # without payment status becoming a product blocker.
    # --------------------------------------------------------

    if channel in {
        "manual",
        "customer_service",
    }:

        return {
            "ok": True,
            "channel": channel,
            "service": service,
            "title": title,
            "filename": path.name,
            "file_endpoint": (
                "/api/back-office/delivery-file"
                "?service="
                + urllib.parse.quote(
                    service,
                    safe="",
                )
                + "&title="
                + urllib.parse.quote(
                    title,
                    safe="",
                )
            ),
            "message": (
                "The exact saved document is ready for Customer Service."
            ),
        }

    # --------------------------------------------------------
    # EXTERNAL DELIVERY
    #
    # Still respects explicit download activation.
    # --------------------------------------------------------

    if not product_download_unlocked(
        product
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            "Customer Care has not activated delivery.",
            403,
        )

    webhook = webhook_for_channel(
        channel
    )

    if not webhook:

        return json_response_error(
            "DELIVERY_CHANNEL_NOT_CONFIGURED",
            "The selected delivery channel is not configured.",
        )

    payload = {
        "service": service,
        "title": title,
        "recipient": recipient,
        "filename": path.name,
        "saved_document_path": str(path),
        "message": (
            "Deliver this exact saved document. "
            "Do not regenerate or replace it."
        ),
    }

    success, message = send_delivery_webhook(
        webhook,
        payload,
    )

    if not success:

        return json_response_error(
            "DELIVERY_FAILED",
            message,
        )

    return {
        "ok": True,
        "channel": channel,
        "service": service,
        "title": title,
        "filename": path.name,
        "message": message,
    }


# ============================================================
# EXACT SAVED FILE FOR CUSTOMER SERVICE
#
# Customer Service can retrieve the finished product
# independently of payment.
# ============================================================

@app.get("/api/back-office/delivery-file")
async def back_office_delivery_file(
    request: Request,
    service: str = "",
    title: str = "",
):

    error = require_back_office_key(
        request
    )

    if error:
        return error

    product = get_product(
        clean(service),
        clean(title),
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product was not found.",
            404,
        )

    path = saved_document_path(
        product
    )

    if not (
        path
        and path.is_file()
        and path.stat().st_size > 0
    ):

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "The exact saved document is not available.",
            404,
        )

    return FileResponse(
        path=str(path),
        filename=(
            clean(
                product.get(
                    "document_filename"
                )
            )
            or path.name
        ),
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
    )


# ============================================================
# PRODUCT STATUS
#
# Useful for delivery UI without payment dependency.
# ============================================================

@app.get("/api/product/status")
async def product_status(
    service: str = "",
    title: str = "",
):

    service = clean(
        service
    )

    title = clean(
        title
    )

    if not service or not title:

        return json_response_error(
            "SERVICE_AND_TITLE_REQUIRED",
            "Service and document title are required.",
        )

    product = get_product(
        service,
        title,
    )

    if product is None:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "The finished product was not found.",
            404,
        )

    return product_public(
        product
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "ok": True,

        "service":
            "Naija Pocket Business Center",

        "version":
            APP_VERSION,

        "product_database":
            str(PRODUCT_DB_PATH),

        "payment_database":
            str(PAYMENT_DB_PATH),

        "download_directory":
            str(DOWNLOAD_DIR),

        "identity":
            "service + document title",

        "product_first":
            True,

        "payment_required_for_product":
            False,

        "payment_required_for_document_save":
            False,

        "payment_required_for_customer_care_access":
            False,

        "payment_required_for_download":
            False,

        "customer_care_activation_required_for_download":
            True,

        "ids_as_product_identity":
            False,

        "payment_reference_as_product_identity":
            False,
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    init_product_db()

    init_payment_db()

    print(
        "Naija Pocket Business Center Product-First API started."
    )

    print(
        f"Version: {APP_VERSION}"
    )

    print(
        f"Product database: {PRODUCT_DB_PATH}"
    )

    print(
        f"Payment database: {PAYMENT_DB_PATH}"
    )

    print(
        f"Downloads: {DOWNLOAD_DIR}"
    )

    print(
        "Product identity: service + document title"
    )

    print(
        "Finished document is independent of payment."
    )

    print(
        "Only Customer Care activation controls customer download."
    )


# ============================================================
# LOCAL START
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
