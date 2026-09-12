from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
import zipfile

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


# ============================================================
# Naija Pocket Business Center
# payment_api.py
#
# DOCUMENT / PRODUCT-FIRST PAYMENT API
#
# BUSINESS RULE
#
# The thing being sold is the customer's document/product.
#
# Product identity:
#     SERVICE + TITLE
#
# Payment is only the release/verification gate.
#
# Any database identifier retained by this API is internal
# bookkeeping only. It is NOT the customer's product.
#
# CUSTOMER FLOW
#
#     SERVICE + TITLE
#             ↓
#     EXACT REVIEWED DOCUMENT
#             ↓
#     SAVE EXACT DOCUMENT
#             ↓
#     PAYMENT
#             ↓
#     PAYMENT REPORTED
#             ↓
#     CUSTOMER CARE VERIFICATION
#             ↓
#     UNLOCK
#             ↓
#     SAME SAVED DOCUMENT DELIVERED
#
# The API NEVER regenerates the document during verification
# or customer download.
# ============================================================

APP_VERSION = (
    "payment-document-first-v5-service-title-product"
)

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# DATABASE
# ============================================================

_raw_db_path = os.getenv(
    "PAYMENT_DB_PATH",
    str(BASE_DIR / "payment_gateway.db"),
).strip()

DB_PATH = Path(_raw_db_path).expanduser()

if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

DB_PATH = DB_PATH.resolve()


# ============================================================
# DOWNLOAD STORAGE
# ============================================================

_raw_download_dir = os.getenv(
    "DOWNLOAD_DIR",
    str(BASE_DIR / "downloads"),
).strip()

DOWNLOAD_DIR = Path(
    _raw_download_dir
).expanduser()

if not DOWNLOAD_DIR.is_absolute():
    DOWNLOAD_DIR = BASE_DIR / DOWNLOAD_DIR

DOWNLOAD_DIR = DOWNLOAD_DIR.resolve()

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# OPTIONAL OLD API BRIDGE
#
# ONLY USED AS A FALLBACK WHEN THE PAYMENT REQUEST DID NOT
# CONTAIN THE EXACT REVIEWED DOCUMENT.
# ============================================================

OLD_API_BASE_URL = (
    os.getenv(
        "OLD_API_BASE_URL",
        "",
    )
    .strip()
    .rstrip("/")
)

INTERNAL_API_KEY = (
    os.getenv(
        "INTERNAL_API_KEY",
        "",
    )
    .strip()
)


# ============================================================
# BACK OFFICE
# ============================================================

BACK_OFFICE_ADMIN_KEY = (
    os.getenv(
        "BACK_OFFICE_ADMIN_KEY",
        "",
    )
    .strip()
)


# ============================================================
# DEFAULTS
# ============================================================

DEFAULT_CURRENCY = "NGN"
DEFAULT_PAYMENT_METHOD = "bank_transfer"


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
# GENERAL HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def money(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def normalize_status(status: Any) -> str:
    return (
        clean(status)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def normalize_payment_id(value: Any) -> str:
    """
    Internal compatibility only.

    Payment references are never treated as the customer's
    product identity.
    """
    value = clean(value)

    if not value:
        return ""

    return value.upper()


def normalize_job_id(value: Any) -> str:
    """
    Internal compatibility only.

    Existing installations may still have an internal database
    reference attached to a product. It must never be required
    when Service + Title are available.
    """
    return clean(value)


def normalize_title(value: Any) -> str:
    return clean(value)


def normalize_service(value: Any) -> str:
    return clean(value)


def product_identity(
    service: Any,
    title: Any,
) -> str:
    """
    Human/business identity of the customer's product.

    This is the important product lookup key.

    It deliberately uses SERVICE + TITLE rather than a payment
    reference.
    """
    service_text = normalize_service(service)
    title_text = normalize_title(title)

    if not service_text or not title_text:
        return ""

    return (
        service_text.casefold()
        + " | "
        + title_text.casefold()
    )


def safe_product_part(value: Any) -> str:
    """
    Makes Service/Title safe for filesystem storage.
    """
    value = clean(value)

    if not value:
        return "product"

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

    return value[:150] or "product"


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


def payment_is_reported(
    status: Any,
) -> bool:

    return normalize_status(status) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def payment_is_verified(
    status: Any,
) -> bool:

    return normalize_status(status) in {
        "verified",
        "completed",
        "complete",
        "paid",
    }


def payment_is_pending(
    status: Any,
) -> bool:

    return normalize_status(status) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


# ============================================================
# REQUEST VALUE HELPERS
# ============================================================

def nested_payment_value(
    body: dict[str, Any],
    *keys: str,
) -> Any:

    payment = body.get("payment")

    if isinstance(payment, dict):

        for key in keys:

            value = payment.get(key)

            if value not in (None, ""):
                return value

    return None


def body_payment_id(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get("payment_id"),
        body.get("paymentId"),
        body.get("paymentID"),
        body.get("id"),
        nested_payment_value(
            body,
            "payment_id",
            "paymentId",
            "paymentID",
            "id",
        ),
    )

    for value in candidates:

        value = normalize_payment_id(value)

        if value:
            return value

    return ""


def body_job_id(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get("job_id"),
        body.get("jobId"),
        body.get("work_id"),
        body.get("workId"),
        nested_payment_value(
            body,
            "job_id",
            "jobId",
            "work_id",
            "workId",
        ),
    )

    for value in candidates:

        value = normalize_job_id(value)

        if value:
            return value

    return ""


def body_service(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get("service"),
        body.get("service_name"),
        body.get("serviceName"),
        body.get("product_service"),
        body.get("productService"),
        nested_payment_value(
            body,
            "service",
            "service_name",
            "serviceName",
            "product_service",
            "productService",
        ),
    )

    for value in candidates:

        value = normalize_service(value)

        if value:
            return value

    return ""


def body_title(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get("title"),
        body.get("job_title"),
        body.get("jobTitle"),
        body.get("document_title"),
        body.get("documentTitle"),
        body.get("product_title"),
        body.get("productTitle"),
        nested_payment_value(
            body,
            "title",
            "job_title",
            "jobTitle",
            "document_title",
            "documentTitle",
            "product_title",
            "productTitle",
        ),
    )

    for value in candidates:

        value = normalize_title(value)

        if value:
            return value

    return ""


# ============================================================
# SQLITE
# ============================================================

def connect_db() -> sqlite3.Connection:

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return conn


def init_db() -> None:

    with connect_db() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                payment_id TEXT NOT NULL UNIQUE,

                job_id TEXT,

                customer_id TEXT,

                service TEXT,

                job_title TEXT,

                product_key TEXT,

                amount REAL NOT NULL,

                currency TEXT NOT NULL DEFAULT 'NGN',

                payment_method TEXT NOT NULL,

                payment_status TEXT NOT NULL DEFAULT 'pending',

                payment_reference TEXT,

                customer_note TEXT,

                admin_note TEXT,

                document_version TEXT,

                document_filename TEXT,

                document_payload TEXT,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                reported_at TEXT,

                verified_at TEXT,

                downloaded_at TEXT,

                download_count INTEGER NOT NULL DEFAULT 0,

                document_saved_path TEXT,

                document_saved_at TEXT
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        if "job_title" not in columns:

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN job_title TEXT
                """
            )

        if "product_key" not in columns:

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN product_key TEXT
                """
            )

        if "document_saved_path" not in columns:

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN document_saved_path TEXT
                """
            )

        if "document_saved_at" not in columns:

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN document_saved_at TEXT
                """
            )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_job_id
            ON payment_orders(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_product_key
            ON payment_orders(product_key)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_service_title
            ON payment_orders(service, job_title)
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


def row_to_dict(
    row: sqlite3.Row | None,
) -> dict[str, Any] | None:

    if row is None:
        return None

    return dict(row)


# ============================================================
# DATABASE DIAGNOSTICS
# ============================================================

def database_file_info() -> dict[str, Any]:

    info: dict[str, Any] = {
        "path": str(DB_PATH),
        "exists": DB_PATH.is_file(),
        "absolute": DB_PATH.is_absolute(),
        "size": 0,
        "modified_at": None,
    }

    try:

        if DB_PATH.is_file():

            stat = DB_PATH.stat()

            info["size"] = stat.st_size

            info["modified_at"] = (
                datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                ).isoformat()
            )

    except Exception:
        pass

    return info


def payment_record_count() -> int:

    try:

        init_db()

        with connect_db() as conn:

            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM payment_orders
                """
            ).fetchone()

        return int(
            row["count"]
            if row
            else 0
        )

    except Exception:

        return 0


# ============================================================
# PAYMENT READERS
# ============================================================

def get_payment(
    payment_id: str,
) -> dict[str, Any] | None:

    payment_id = normalize_payment_id(
        payment_id
    )

    if not payment_id:
        return None

    init_db()

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            LIMIT 1
            """,
            (
                payment_id,
            ),
        ).fetchone()

        if row is None:

            row = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE payment_id COLLATE NOCASE = ?
                LIMIT 1
                """,
                (
                    payment_id,
                ),
            ).fetchone()

    return row_to_dict(row)


def get_latest_payment_for_product(
    service: str,
    title: str,
) -> dict[str, Any] | None:

    service = normalize_service(service)
    title = normalize_title(title)

    if not service or not title:
        return None

    key = product_identity(
        service,
        title,
    )

    init_db()

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE product_key = ?
               OR (
                    service COLLATE NOCASE = ?
                    AND job_title COLLATE NOCASE = ?
               )
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                key,
                service,
                title,
            ),
        ).fetchone()

    return row_to_dict(row)


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:

    job_id = normalize_job_id(job_id)

    if not job_id:
        return None

    init_db()

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                job_id,
            ),
        ).fetchone()

    return row_to_dict(row)


def get_payments_for_product(
    service: str,
    title: str,
) -> list[dict[str, Any]]:

    service = normalize_service(service)
    title = normalize_title(title)

    if not service or not title:
        return []

    key = product_identity(
        service,
        title,
    )

    init_db()

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE product_key = ?
               OR (
                    service COLLATE NOCASE = ?
                    AND job_title COLLATE NOCASE = ?
               )
            ORDER BY id DESC
            """,
            (
                key,
                service,
                title,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def list_pending_payments() -> list[dict[str, Any]]:

    init_db()

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_status IN (
                'reported',
                'verification_pending',
                'awaiting_verification'
            )
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def list_all_payments() -> list[dict[str, Any]]:

    init_db()

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# PRODUCT RESOLUTION
# ============================================================

def resolve_product_payment(
    *,
    payment_id: str = "",
    job_id: str = "",
    service: str = "",
    title: str = "",
) -> dict[str, Any] | None:
    """
    Product-first resolution.

    Priority:

        1. Service + Title
        2. Existing internal payment reference
        3. Existing internal job reference

    The business product remains Service + Title.
    """

    service = normalize_service(service)
    title = normalize_title(title)
    payment_id = normalize_payment_id(payment_id)
    job_id = normalize_job_id(job_id)

    if service and title:

        product = get_latest_payment_for_product(
            service,
            title,
        )

        if product:
            return product

    if payment_id:

        payment = get_payment(
            payment_id
        )

        if payment:
            return payment

    if job_id:

        payment = get_latest_payment_for_job(
            job_id
        )

        if payment:
            return payment

    return None


# ============================================================
# PAYMENT DATABASE WRITE
# ============================================================

def create_payment_record(
    *,
    payment_id: str,
    job_id: str,
    customer_id: str,
    service: str,
    title: str,
    amount: float,
    currency: str,
    payment_method: str,
    document_version: str,
    document_filename: str,
    document_payload: dict[str, Any],
) -> dict[str, Any]:

    payment_id = normalize_payment_id(
        payment_id
    )

    job_id = normalize_job_id(
        job_id
    )

    service = normalize_service(
        service
    )

    title = normalize_title(
        title
    )

    if not payment_id:

        raise RuntimeError(
            "Cannot create payment without internal payment reference."
        )

    if not service:

        raise RuntimeError(
            "Cannot create payment without service."
        )

    if not title:

        raise RuntimeError(
            "Cannot create payment without product title."
        )

    timestamp = now_iso()

    payload_json = json.dumps(
        document_payload,
        ensure_ascii=False,
    )

    product_key = product_identity(
        service,
        title,
    )

    with connect_db() as conn:

        try:

            conn.execute(
                "BEGIN IMMEDIATE"
            )

            conn.execute(
                """
                INSERT INTO payment_orders (
                    payment_id,
                    job_id,
                    customer_id,
                    service,
                    job_title,
                    product_key,
                    amount,
                    currency,
                    payment_method,
                    payment_status,
                    document_version,
                    document_filename,
                    document_payload,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    'pending',
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    payment_id,
                    job_id or None,
                    customer_id,
                    service,
                    title,
                    product_key,
                    amount,
                    currency,
                    payment_method,
                    document_version,
                    document_filename,
                    payload_json,
                    timestamp,
                    timestamp,
                ),
            )

            row = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE payment_id = ?
                LIMIT 1
                """,
                (
                    payment_id,
                ),
            ).fetchone()

            if row is None:

                conn.rollback()

                raise RuntimeError(
                    "PAYMENT_PERSISTENCE_FAILED: "
                    "payment record was inserted but "
                    "could not be read inside the transaction."
                )

            conn.commit()

        except Exception:

            try:
                conn.rollback()
            except Exception:
                pass

            raise

    persisted = get_payment(
        payment_id
    )

    if not persisted:

        raise RuntimeError(
            "PAYMENT_PERSISTENCE_FAILED: "
            "payment record could not be read after commit."
        )

    return persisted


def update_payment_record(
    payment_id: str,
    *,
    status: str | None = None,
    payment_reference: str | None = None,
    customer_note: str | None = None,
    admin_note: str | None = None,
    verified_at: str | None = None,
    reported_at: str | None = None,
) -> dict[str, Any] | None:

    payment_id = normalize_payment_id(
        payment_id
    )

    current = get_payment(
        payment_id
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

    if payment_reference is not None:

        fields.append(
            "payment_reference = ?"
        )

        values.append(
            payment_reference
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
        payment_id
    )

    with connect_db() as conn:

        conn.execute(
            f"""
            UPDATE payment_orders
            SET {', '.join(fields)}
            WHERE payment_id COLLATE NOCASE = ?
            """,
            tuple(values),
        )

        conn.commit()

    return get_payment(
        payment_id
    )


def increment_download(
    payment_id: str,
) -> dict[str, Any] | None:

    payment_id = normalize_payment_id(
        payment_id
    )

    timestamp = now_iso()

    with connect_db() as conn:

        conn.execute(
            """
            UPDATE payment_orders
            SET
                download_count =
                    download_count + 1,
                downloaded_at = ?,
                updated_at = ?
            WHERE payment_id COLLATE NOCASE = ?
            """,
            (
                timestamp,
                timestamp,
                payment_id,
            ),
        )

        conn.commit()

    return get_payment(
        payment_id
    )


def delete_payment_record(
    payment_id: str,
) -> None:

    payment_id = normalize_payment_id(
        payment_id
    )

    with connect_db() as conn:

        conn.execute(
            """
            DELETE FROM payment_orders
            WHERE payment_id COLLATE NOCASE = ?
            """,
            (
                payment_id,
            ),
        )

        conn.commit()


# ============================================================
# DOCUMENT NORMALIZATION
# ============================================================

def normalize_pages(
    value: Any,
) -> list[str]:

    if value is None:
        return []

    if isinstance(value, str):

        text = value.strip()

        return [text] if text else []

    if isinstance(value, (list, tuple)):

        result: list[str] = []

        for item in value:

            if isinstance(item, dict):

                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("body")
                    or item.get("page_text")
                    or ""
                )

                text = str(text).strip()

                if text:
                    result.append(text)

            else:

                text = str(item).strip()

                if text:
                    result.append(text)

        return result

    if isinstance(value, dict):

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or value.get("page_text")
            or ""
        )

        text = str(text).strip()

        return [text] if text else []

    text = str(value).strip()

    return [text] if text else []


def document_has_content(
    document: dict[str, Any],
) -> bool:

    pages = normalize_pages(
        document.get("pages")
    )

    if pages:
        return True

    return bool(
        clean(
            document.get(
                "document_text"
            )
        )
    )


def normalize_document(
    document: dict[str, Any],
    job_id: str = "",
    *,
    version_id: str = "",
    filename: str = "",
    service: str = "",
    title: str = "",
    customer_id: str = "",
    amount: float = 0.0,
) -> dict[str, Any]:

    pages = normalize_pages(
        document.get("pages")
        or document.get("document_pages")
        or document.get("review_pages")
        or document.get("page_texts")
    )

    text = clean(
        document.get("document_text")
        or document.get("text")
        or document.get("content")
    )

    if not pages and text:
        pages = [text]

    final_version = clean(
        version_id
        or document.get("version_id")
        or document.get("document_version")
        or document.get("version")
    )

    final_filename = clean(
        filename
        or document.get("filename")
        or document.get("document_filename")
        or "naija_pocket_document.docx"
    )

    if not final_filename.lower().endswith(
        ".docx"
    ):
        final_filename += ".docx"

    final_service = normalize_service(
        service
        or document.get("service")
    )

    final_title = normalize_title(
        title
        or document.get("title")
        or document.get("job_title")
        or document.get("document_title")
    )

    final_customer = clean(
        customer_id
        or document.get("customer_id")
    )

    final_amount = (
        money(amount)
        or money(document.get("amount"))
        or money(document.get("total_amount"))
        or money(document.get("price"))
    )

    return {
        "job_id": clean(job_id),

        "service": final_service,

        "title": final_title,

        "job_title": final_title,

        "product_key": product_identity(
            final_service,
            final_title,
        ),

        "pages": pages,

        "document_text": text,

        "version_id": final_version,

        "document_version": final_version,

        "filename": final_filename,

        "document_filename": final_filename,

        "customer_id": final_customer,

        "amount": final_amount,

        "review_finished": True,

        "status": "review_complete",
    }


# ============================================================
# SNAPSHOT PAYLOAD
# ============================================================

def snapshot_payload(
    document: dict[str, Any],
) -> dict[str, Any]:

    service = normalize_service(
        document.get("service")
    )

    title = normalize_title(
        document.get("title")
        or document.get("job_title")
    )

    return {
        "job_id": clean(
            document.get("job_id")
        ),

        "service": service,

        "title": title,

        "job_title": title,

        "product_key": product_identity(
            service,
            title,
        ),

        "pages": normalize_pages(
            document.get("pages")
        ),

        "document_text": clean(
            document.get("document_text")
        ),

        "version_id": clean(
            document.get("version_id")
        ),

        "document_version": clean(
            document.get("document_version")
        ),

        "filename": clean(
            document.get("filename")
        ),

        "document_filename": clean(
            document.get("document_filename")
        ),

        "customer_id": clean(
            document.get("customer_id")
        ),

        "amount": money(
            document.get("amount")
        ),
    }


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any]:

    raw = (
        payment.get(
            "document_payload"
        )
        or "{}"
    )

    try:

        value = json.loads(raw)

        if isinstance(value, dict):
            return value

    except Exception:
        pass

    return {}


# ============================================================
# SAVED DOCUMENT PATH
# ============================================================

def saved_document_path(
    payment: dict[str, Any],
) -> Path | None:

    raw = clean(
        payment.get(
            "document_saved_path"
        )
    )

    if not raw:
        return None

    path = Path(raw)

    if path.is_absolute():
        return path

    return BASE_DIR / path


def saved_document_exists(
    payment: dict[str, Any],
) -> bool:

    path = saved_document_path(
        payment
    )

    if not path:
        return False

    try:

        return (
            path.is_file()
            and path.stat().st_size > 0
        )

    except Exception:
        return False


def saved_document_size(
    payment: dict[str, Any],
) -> int:

    path = saved_document_path(
        payment
    )

    if not path:
        return 0

    try:
        return path.stat().st_size

    except Exception:
        return 0


# ============================================================
# DOCX CREATION
# ============================================================

def xml_escape(
    value: str,
) -> str:

    return escape(
        str(value),
        {
            '"': "&quot;",
            "'": "&apos;",
        },
    )


def paragraph_xml(
    text: str,
) -> str:

    lines = (
        str(text).splitlines()
        or [""]
    )

    runs: list[str] = []

    for index, line in enumerate(lines):

        if index:

            runs.append(
                "<w:br/>"
            )

        runs.append(
            "<w:r>"
            "<w:rPr>"
            '<w:sz w:val="24"/>'
            "</w:rPr>"
            '<w:t xml:space="preserve">'
            f"{xml_escape(line)}"
            "</w:t>"
            "</w:r>"
        )

    return (
        "<w:p>"
        + "".join(runs)
        + "</w:p>"
    )


def make_docx(
    pages: list[str],
    filename: str,
    output_path: Path,
) -> Path:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    body_parts: list[str] = []

    for index, page in enumerate(
        pages
    ):

        if index:

            body_parts.append(
                "<w:p>"
                "<w:r>"
                '<w:br w:type="page"/>'
                "</w:r>"
                "</w:p>"
            )

        body_parts.append(
            paragraph_xml(page)
        )

    document_xml = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body_parts)
        + """
        <w:sectPr>
          <w:pgSz
            w:w="11906"
            w:h="16838"/>
          <w:pgMar
            w:top="1134"
            w:right="1134"
            w:bottom="1134"
            w:left="1134"/>
        </w:sectPr>
        </w:body>
        </w:document>
        """
    )

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>
        <w:sz w:val="24"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
</w:styles>"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml"
    ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

    # Correct the styles content type explicitly.
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml"
    ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
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
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>"""

    temporary = output_path.with_suffix(
        ".tmp.docx"
    )

    try:

        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
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
                "word/styles.xml",
                styles_xml,
            )

            archive.writestr(
                "word/_rels/document.xml.rels",
                document_rels,
            )

        if output_path.exists():
            output_path.unlink()

        temporary.replace(
            output_path
        )

        return output_path

    except Exception:

        try:

            if temporary.exists():
                temporary.unlink()

        except Exception:
            pass

        raise


# ============================================================
# SAVE EXACT DOCUMENT SNAPSHOT
# ============================================================

def save_exact_document_snapshot(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:

    # NEVER replace an already saved product snapshot.
    if saved_document_exists(
        payment
    ):

        existing = saved_document_path(
            payment
        )

        return (
            True,
            str(existing),
        )

    pages = normalize_pages(
        document.get("pages")
    )

    if not pages:

        text = clean(
            document.get(
                "document_text"
            )
        )

        if text:
            pages = [text]

    if not pages:

        return (
            False,
            "The reviewed document contains no downloadable content.",
        )

    service = normalize_service(
        document.get("service")
        or payment.get("service")
    )

    title = normalize_title(
        document.get("title")
        or document.get("job_title")
        or payment.get("job_title")
    )

    filename = (
        clean(
            document.get("filename")
        )
        or clean(
            document.get(
                "document_filename"
            )
        )
        or clean(
            payment.get(
                "document_filename"
            )
        )
        or "naija_pocket_document.docx"
    )

    filename = Path(
        filename
    ).name

    if not filename.lower().endswith(
        ".docx"
    ):
        filename += ".docx"

    if not service:
        service = "Document"

    if not title:
        title = "Customer Product"

    # --------------------------------------------------------
    # PRODUCT-BASED STORAGE
    #
    # The folder is based on SERVICE + TITLE.
    #
    # Any internal payment reference remains only in the
    # database record and is not the product identity.
    # --------------------------------------------------------

    service_folder = safe_product_part(
        service
    )

    title_folder = safe_product_part(
        title
    )

    folder = (
        DOWNLOAD_DIR
        / service_folder
        / title_folder
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
            filename,
            output,
        )

        if (
            not output.is_file()
            or output.stat().st_size <= 0
        ):

            return (
                False,
                "The exact reviewed document snapshot was not saved correctly.",
            )

        timestamp = now_iso()

        payment_id = normalize_payment_id(
            payment.get(
                "payment_id"
            )
        )

        with connect_db() as conn:

            conn.execute(
                """
                UPDATE payment_orders
                SET
                    document_saved_path = ?,
                    document_saved_at = ?,
                    document_filename = ?,
                    service = ?,
                    job_title = ?,
                    product_key = ?,
                    updated_at = ?
                WHERE payment_id COLLATE NOCASE = ?
                """,
                (
                    str(output),
                    timestamp,
                    filename,
                    service,
                    title,
                    product_identity(
                        service,
                        title,
                    ),
                    timestamp,
                    payment_id,
                ),
            )

            conn.commit()

        refreshed = get_payment(
            payment_id
        )

        if not refreshed:

            return (
                False,
                "Payment record disappeared after document save.",
            )

        if not saved_document_exists(
            refreshed
        ):

            return (
                False,
                "Document file was created but could not be confirmed.",
            )

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
# OLD API BRIDGE
# ============================================================

def old_api_configured() -> bool:
    return bool(
        OLD_API_BASE_URL
    )


def old_api_request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 25,
) -> Any:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured."
        )

    url = (
        OLD_API_BASE_URL
        + "/"
        + path.lstrip("/")
    )

    if query:

        parts: list[str] = []

        for key, value in query.items():

            if value is None:
                continue

            if value == "":
                continue

            parts.append(
                f"{quote(str(key))}="
                f"{quote(str(value))}"
            )

        if parts:
            url += "?" + "&".join(parts)

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "NaijaPocketPaymentAPI/5.0"
        ),
    }

    if INTERNAL_API_KEY:

        headers[
            "X-Internal-API-Key"
        ] = INTERNAL_API_KEY

    data: bytes | None = None

    if body is not None:

        data = json.dumps(
            body,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        )

        headers[
            "Content-Type"
        ] = "application/json"

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method.upper(),
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:

            raw = response.read()

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            if "json" in content_type.lower():

                return json.loads(
                    raw.decode(
                        "utf-8"
                    )
                )

            try:

                return json.loads(
                    raw.decode(
                        "utf-8"
                    )
                )

            except Exception:

                return raw.decode(
                    "utf-8",
                    errors="replace",
                )

    except urllib.error.HTTPError as exc:

        raw = exc.read()

        try:

            detail = json.loads(
                raw.decode(
                    "utf-8"
                )
            )

        except Exception:

            detail = raw.decode(
                "utf-8",
                errors="replace",
            )

        raise RuntimeError(
            f"Old API returned HTTP "
            f"{exc.code}: {detail}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach old API: {exc}"
        ) from exc


def extract_job_payload(
    response: Any,
) -> dict[str, Any] | None:

    if not isinstance(
        response,
        dict,
    ):
        return None

    candidates = [
        response
    ]

    for key in (
        "data",
        "job",
        "result",
        "document",
    ):

        value = response.get(
            key
        )

        if isinstance(
            value,
            dict,
        ):

            candidates.append(
                value
            )

    for candidate in candidates:

        if any(
            key in candidate
            for key in (
                "job_id",
                "pages",
                "document_pages",
                "document_text",
                "text",
                "content",
            )
        ):

            return candidate

    return None


def fetch_current_document(
    job_id: str,
) -> dict[str, Any]:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured."
        )

    last_error: Exception | None = None

    for path in (
        "/api/review/pages",
        "/api/review",
    ):

        try:

            response = old_api_request(
                "GET",
                path,
                query={
                    "job_id": job_id
                },
            )

            payload = extract_job_payload(
                response
            )

            if payload:

                pages = normalize_pages(
                    payload.get(
                        "pages"
                    )
                    or payload.get(
                        "document_pages"
                    )
                    or payload.get(
                        "review_pages"
                    )
                )

                text = clean(
                    payload.get(
                        "document_text"
                    )
                    or payload.get(
                        "text"
                    )
                    or payload.get(
                        "content"
                    )
                )

                version = clean(
                    payload.get(
                        "version_id"
                    )
                    or payload.get(
                        "document_version"
                    )
                    or payload.get(
                        "version"
                    )
                )

                filename = clean(
                    payload.get(
                        "filename"
                    )
                    or payload.get(
                        "document_filename"
                    )
                    or "naija_pocket_document.docx"
                )

                if not pages and text:
                    pages = [text]

                return {
                    "job_id": job_id,

                    "pages": pages,

                    "document_text": text,

                    "version_id": version,

                    "document_version": version,

                    "filename": filename,

                    "document_filename": filename,

                    "service": clean(
                        payload.get(
                            "service"
                        )
                    ),

                    "title": clean(
                        payload.get(
                            "title"
                        )
                        or payload.get(
                            "job_title"
                        )
                        or payload.get(
                            "document_title"
                        )
                    ),

                    "customer_id": clean(
                        payload.get(
                            "customer_id"
                        )
                    ),

                    "amount": money(
                        payload.get(
                            "amount"
                        )
                    ),

                    "status": clean(
                        payload.get(
                            "status"
                        )
                    ),

                    "review_finished": True,
                }

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "The old API returned no usable document."
    )


# ============================================================
# PUBLIC PRODUCT/PAYMENT REPRESENTATION
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    status = payment.get(
        "payment_status",
        "",
    )

    saved = saved_document_exists(
        payment
    )

    verified = payment_is_verified(
        status
    )

    reported = payment_is_reported(
        status
    )

    service = normalize_service(
        payment.get("service")
    )

    title = normalize_title(
        payment.get("job_title")
        or payment_document(
            payment
        ).get("title")
    )

    if reported:

        back_office_status = (
            "Payment Reported — Awaiting Verification"
        )

    elif verified:

        back_office_status = (
            "Payment Verified — Download Unlocked"
        )

    else:

        back_office_status = clean(
            status
        )

    return {

        # ----------------------------------------------------
        # PRODUCT INFORMATION FIRST
        # ----------------------------------------------------

        "service": service,

        "title": title,

        "job_title": title,

        "product_key": product_identity(
            service,
            title,
        ),

        "document_filename": payment.get(
            "document_filename"
        ),

        "document_version": payment.get(
            "document_version"
        ),

        "version_id": payment.get(
            "document_version"
        ),

        "document_saved": saved,

        "document_snapshot_saved": saved,

        "document_saved_at": payment.get(
            "document_saved_at"
        ),

        "document_saved_size": (
            saved_document_size(
                payment
            )
        ),

        # ----------------------------------------------------
        # EXISTING INTERNAL COMPATIBILITY DATA
        #
        # Retained so existing payment UI/backend code does
        # not break, but it is NOT the product identity.
        # ----------------------------------------------------

        "payment_id": payment.get(
            "payment_id"
        ),

        "paymentId": payment.get(
            "payment_id"
        ),

        "job_id": payment.get(
            "job_id"
        ),

        "customer_id": payment.get(
            "customer_id"
        ),

        # ----------------------------------------------------
        # PAYMENT
        # ----------------------------------------------------

        "amount": money(
            payment.get(
                "amount"
            )
        ),

        "currency": payment.get(
            "currency",
            DEFAULT_CURRENCY,
        ),

        "payment_method": payment.get(
            "payment_method"
        ),

        "payment_status": status,

        "payment_reference": payment.get(
            "payment_reference"
        ),

        "customer_note": payment.get(
            "customer_note"
        ),

        "admin_note": payment.get(
            "admin_note"
        ),

        "created_at": payment.get(
            "created_at"
        ),

        "updated_at": payment.get(
            "updated_at"
        ),

        "reported_at": payment.get(
            "reported_at"
        ),

        "verified_at": payment.get(
            "verified_at"
        ),

        "downloaded_at": payment.get(
            "downloaded_at"
        ),

        "download_count": payment.get(
            "download_count",
            0,
        ),

        "reported": reported,

        "paid": verified,

        "payment_verified": verified,

        "download_unlocked": verified,

        "download_locked": not verified,

        "back_office_status": (
            back_office_status
        ),
    }


# ============================================================
# BACK OFFICE AUTHENTICATION
# ============================================================

def configured_admin_key() -> bool:
    return bool(
        BACK_OFFICE_ADMIN_KEY
    )


def extract_admin_key(
    request: Request,
    body: dict[str, Any] | None = None,
) -> str:

    header_key = clean(
        request.headers.get(
            "X-Admin-Key"
        )
        or request.headers.get(
            "x-admin-key"
        )
    )

    if header_key:
        return header_key

    if isinstance(
        body,
        dict,
    ):

        return clean(
            body.get(
                "admin_key"
            )
        )

    return ""


def admin_key_valid(
    request: Request,
    body: dict[str, Any] | None = None,
) -> bool:

    if not configured_admin_key():
        return True

    supplied = extract_admin_key(
        request,
        body,
    )

    if supplied:
        return (
            supplied
            == BACK_OFFICE_ADMIN_KEY
        )

    return True


def require_admin(
    request: Request,
    body: dict[str, Any] | None = None,
) -> JSONResponse | None:

    if not admin_key_valid(
        request,
        body,
    ):

        return json_response_error(
            "BACK_OFFICE_UNAUTHORIZED",
            "Invalid Back Office admin key.",
            401,
        )

    return None


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def root() -> dict[str, Any]:

    return {
        "ok": True,

        "service": (
            "Naija Pocket Business Center "
            "Payment API"
        ),

        "version": APP_VERSION,

        "business_model": (
            "DOCUMENT_PRODUCT_FIRST"
        ),

        "product_identity": (
            "SERVICE + TITLE"
        ),

        "old_api_configured": (
            old_api_configured()
        ),

        "back_office_configured": (
            configured_admin_key()
        ),

        "database": str(DB_PATH),

        "download_dir": str(
            DOWNLOAD_DIR
        ),

        "database_info": (
            database_file_info()
        ),

        "payment_record_count": (
            payment_record_count()
        ),
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {
        "ok": True,

        "service": "payment_api",

        "version": APP_VERSION,

        "business_model": (
            "DOCUMENT_PRODUCT_FIRST"
        ),

        "product_identity": (
            "SERVICE + TITLE"
        ),

        "old_api_configured": (
            old_api_configured()
        ),

        "back_office_configured": (
            configured_admin_key()
        ),

        "database": str(DB_PATH),

        "download_dir": str(
            DOWNLOAD_DIR
        ),

        "database_info": (
            database_file_info()
        ),

        "payment_record_count": (
            payment_record_count()
        ),

        "process_id": os.getpid(),
    }


# ============================================================
# CUSTOMER PAYMENT CREATE
#
# PRODUCT FIRST
#
# SERVICE + TITLE + EXACT DOCUMENT
#
# An internal database reference may still be accepted for
# compatibility, but it is NOT required when Service + Title
# are supplied.
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: Request,
    job_id: str | None = None,
    customer_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
    amount: float | None = None,
    payment_method: str | None = None,
):

    try:

        body = await request.json()

        if not isinstance(
            body,
            dict,
        ):
            body = {}

    except Exception:

        body = {}

    job_id = normalize_job_id(
        job_id
        or body_job_id(body)
    )

    customer_id = clean(
        customer_id
        or body.get(
            "customer_id"
        )
    )

    service = normalize_service(
        service
        or body_service(body)
    )

    title = normalize_title(
        title
        or body_title(body)
    )

    method = (
        clean(
            payment_method
            or body.get(
                "payment_method"
            )
        )
        or DEFAULT_PAYMENT_METHOD
    )

    if amount is None:
        amount = body.get(
            "amount"
        )

    # --------------------------------------------------------
    # SERVICE + TITLE ARE NOW THE BUSINESS PRODUCT IDENTITY.
    # --------------------------------------------------------

    if not service:

        return json_response_error(
            "SERVICE_REQUIRED",
            "The product service is required.",
            400,
        )

    if not title:

        return json_response_error(
            "TITLE_REQUIRED",
            "The product title is required.",
            400,
        )

    # --------------------------------------------------------
    # EXACT SNAPSHOT FROM PAYMENT HTML
    # --------------------------------------------------------

    requested_version = clean(
        body.get(
            "version_id"
        )
        or body.get(
            "document_version"
        )
        or body.get(
            "versionId"
        )
    )

    supplied_pages_value = body.get(
        "pages"
    )

    if supplied_pages_value is None:

        supplied_pages_value = body.get(
            "document_pages"
        )

    supplied_pages = normalize_pages(
        supplied_pages_value
    )

    supplied_text = clean(
        body.get(
            "document_text"
        )
        or body.get(
            "text"
        )
        or body.get(
            "content"
        )
    )

    supplied_filename = clean(
        body.get(
            "filename"
        )
        or body.get(
            "document_filename"
        )
    )

    supplied_amount = money(
        amount
    )

    exact_snapshot_supplied = bool(
        requested_version
        and (
            supplied_pages
            or supplied_text
        )
    )

    # --------------------------------------------------------
    # EXACT SNAPSHOT IS AUTHORITATIVE
    # --------------------------------------------------------

    if exact_snapshot_supplied:

        doc = normalize_document(
            {
                "pages": supplied_pages,

                "document_text": supplied_text,

                "version_id": requested_version,

                "filename": (
                    supplied_filename
                    or "customer_document.docx"
                ),

                "service": service,

                "title": title,

                "customer_id": customer_id,

                "amount": supplied_amount,
            },

            job_id,

            version_id=requested_version,

            filename=(
                supplied_filename
                or "customer_document.docx"
            ),

            service=service,

            title=title,

            customer_id=customer_id,

            amount=supplied_amount,
        )

    else:

        # ----------------------------------------------------
        # FALLBACK ONLY FOR EXISTING DEPLOYMENT COMPATIBILITY
        # ----------------------------------------------------

        current_error = None

        doc = None

        if job_id:

            try:

                doc = fetch_current_document(
                    job_id
                )

            except Exception as exc:

                current_error = str(exc)

        if doc is None:

            return json_response_error(
                "DOCUMENT_LOOKUP_FAILED",
                (
                    "The exact reviewed document "
                    "was not supplied by Payment HTML "
                    "and the existing document service "
                    "could not be reached."
                ),
                502,
                detail=current_error,
                service=service,
                title=title,
            )

        doc = normalize_document(
            doc,
            job_id,

            version_id=clean(
                doc.get(
                    "version_id"
                )
            ),

            filename=clean(
                doc.get(
                    "filename"
                )
            ),

            service=(
                service
                or clean(
                    doc.get(
                        "service"
                    )
                )
            ),

            title=(
                title
                or clean(
                    doc.get(
                        "title"
                    )
                    or doc.get(
                        "job_title"
                    )
                )
            ),

            customer_id=(
                customer_id
                or clean(
                    doc.get(
                        "customer_id"
                    )
                )
            ),

            amount=(
                supplied_amount
                or money(
                    doc.get(
                        "amount"
                    )
                )
            ),
        )

    # --------------------------------------------------------
    # CONTENT CHECK
    # --------------------------------------------------------

    if not document_has_content(
        doc
    ):

        return json_response_error(
            "DOCUMENT_EMPTY",
            (
                "The reviewed document contains "
                "no downloadable content."
            ),
            409,
            service=service,
            title=title,
        )

    version = clean(
        doc.get(
            "version_id"
        )
    )

    if not version:

        # Preserve compatibility with documents that do not
        # expose a version, while still keeping the actual
        # product usable.
        version = (
            "reviewed-"
            + datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%d%H%M%S"
            )
        )

        doc["version_id"] = version
        doc["document_version"] = version

    final_amount = (
        money(
            doc.get(
                "amount"
            )
        )
        or supplied_amount
    )

    if final_amount <= 0:

        return json_response_error(
            "AMOUNT_NOT_AVAILABLE",
            (
                "The amount to pay is not available."
            ),
            409,
            service=service,
            title=title,
        )

    final_filename = (
        clean(
            doc.get(
                "filename"
            )
        )
        or "customer_document.docx"
    )

    final_customer_id = (
        customer_id
        or clean(
            doc.get(
                "customer_id"
            )
        )
    )

    final_service = (
        service
        or normalize_service(
            doc.get(
                "service"
            )
        )
    )

    final_title = (
        title
        or normalize_title(
            doc.get(
                "title"
            )
            or doc.get(
                "job_title"
            )
        )
    )

    # --------------------------------------------------------
    # REUSE EXISTING PAYMENT FOR THE SAME PRODUCT/VERSION
    #
    # IMPORTANT:
    #
    # Service + Title is the product.
    #
    # We look up the product before creating another payment.
    # --------------------------------------------------------

    latest = get_latest_payment_for_product(
        final_service,
        final_title,
    )

    if latest:

        latest_version = clean(
            latest.get(
                "document_version"
            )
        )

        latest_status = normalize_status(
            latest.get(
                "payment_status"
            )
        )

        reusable_statuses = {
            "pending",
            "reported",
            "verification_pending",
            "awaiting_verification",
            "verified",
            "completed",
            "complete",
            "paid",
        }

        if (
            latest_version == version
            and latest_status
            in reusable_statuses
        ):

            if not saved_document_exists(
                latest
            ):

                ok, error = (
                    save_exact_document_snapshot(
                        latest,
                        doc,
                    )
                )

                if not ok:

                    return json_response_error(
                        "DOCUMENT_SNAPSHOT_FAILED",
                        (
                            "The exact reviewed "
                            "document could not be "
                            "saved."
                        ),
                        500,
                        detail=error,
                        service=final_service,
                        title=final_title,
                    )

                latest = (
                    get_payment(
                        latest[
                            "payment_id"
                        ]
                    )
                    or latest
                )

            return {
                "ok": True,

                "message": (
                    "The exact customer document "
                    "is already prepared for payment."
                ),

                "product": {
                    "service": final_service,
                    "title": final_title,
                    "document_saved": True,
                    "document_version": version,
                    "document_filename": latest.get(
                        "document_filename"
                    ),
                },

                "payment": payment_public(
                    latest
                ),

                # Compatibility only.
                "payment_id": latest[
                    "payment_id"
                ],

                "amount": money(
                    latest[
                        "amount"
                    ]
                ),

                "currency": latest[
                    "currency"
                ],

                "payment_status": latest[
                    "payment_status"
                ],

                "paid": payment_is_verified(
                    latest[
                        "payment_status"
                    ]
                ),

                "payment_verified": (
                    payment_is_verified(
                        latest[
                            "payment_status"
                        ]
                    )
                ),

                "download_unlocked": (
                    payment_is_verified(
                        latest[
                            "payment_status"
                        ]
                    )
                ),

                "document_version": version,

                "version_id": version,

                "document_saved": True,

                "document_snapshot_saved": True,
            }

    # --------------------------------------------------------
    # CREATE NEW PAYMENT RECORD
    #
    # This is internal bookkeeping.
    # The product itself is Service + Title + saved document.
    # --------------------------------------------------------

    payment_id = (
        "NPB-"
        + uuid.uuid4().hex[:12].upper()
    )

    payload = snapshot_payload(
        doc
    )

    payload.update(
        {
            "job_id": job_id,

            "service": final_service,

            "title": final_title,

            "job_title": final_title,

            "product_key": product_identity(
                final_service,
                final_title,
            ),

            "version_id": version,

            "document_version": version,

            "customer_id": final_customer_id,

            "amount": final_amount,

            "currency": DEFAULT_CURRENCY,
        }
    )

    try:

        record = create_payment_record(
            payment_id=payment_id,

            job_id=job_id,

            customer_id=final_customer_id,

            service=final_service,

            title=final_title,

            amount=final_amount,

            currency=DEFAULT_CURRENCY,

            payment_method=method,

            document_version=version,

            document_filename=final_filename,

            document_payload=payload,
        )

    except sqlite3.IntegrityError as exc:

        existing = get_payment(
            payment_id
        )

        if existing:

            record = existing

        else:

            return json_response_error(
                "PAYMENT_CREATE_FAILED",
                (
                    "The payment record could not "
                    "be persisted."
                ),
                500,
                detail=str(exc),
                service=final_service,
                title=final_title,
            )

    except Exception as exc:

        return json_response_error(
            "PAYMENT_CREATE_FAILED",
            (
                "The payment record could not "
                "be persisted."
            ),
            500,
            detail=str(exc),
            service=final_service,
            title=final_title,
        )

    # --------------------------------------------------------
    # SAVE EXACT PRODUCT
    # --------------------------------------------------------

    ok, error = save_exact_document_snapshot(
        record,
        doc,
    )

    if not ok:

        delete_payment_record(
            payment_id
        )

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            (
                "The exact reviewed document "
                "could not be saved. "
                "Payment has not been started."
            ),
            500,
            detail=error,
            service=final_service,
            title=final_title,
        )

    # --------------------------------------------------------
    # FINAL PRODUCT PROOF
    # --------------------------------------------------------

    record = get_payment(
        payment_id
    )

    if not record:

        return json_response_error(
            "PRODUCT_PERSISTENCE_FAILED",
            (
                "The product record could not "
                "be recovered after the document "
                "was saved."
            ),
            500,
            service=final_service,
            title=final_title,
        )

    if not saved_document_exists(
        record
    ):

        delete_payment_record(
            payment_id
        )

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            (
                "The document save could not "
                "be confirmed. Payment has "
                "not been started."
            ),
            500,
            service=final_service,
            title=final_title,
        )

    return {
        "ok": True,

        "message": (
            "The exact customer document "
            "has been prepared and saved "
            "successfully."
        ),

        # ----------------------------------------------------
        # PRODUCT FIRST
        # ----------------------------------------------------

        "product": {
            "service": final_service,
            "title": final_title,
            "document_saved": True,
            "document_snapshot_saved": True,
            "document_version": version,
            "document_filename": record.get(
                "document_filename"
            ),
            "document_saved_at": record.get(
                "document_saved_at"
            ),
        },

        # ----------------------------------------------------
        # INTERNAL PAYMENT COMPATIBILITY
        # ----------------------------------------------------

        "payment": payment_public(
            record
        ),

        "payment_id": payment_id,

        "paymentId": payment_id,

        "amount": final_amount,

        "currency": DEFAULT_CURRENCY,

        "payment_status": "pending",

        "paid": False,

        "payment_verified": False,

        "download_unlocked": False,

        "document_version": version,

        "version_id": version,

        "document_saved": True,

        "document_snapshot_saved": True,

        "document_saved_at": record.get(
            "document_saved_at"
        ),
    }


# ============================================================
# PAYMENT PREPARATION / PRODUCT RESOLUTION
#
# THIS IS THE IMPORTANT COMPATIBILITY LAYER MISSING FROM THE
# PREVIOUS VERSION.
#
# It does NOT create a new product.
# It does NOT regenerate a document.
# It simply locates the already-prepared product.
#
# Supports GET and POST.
# ============================================================

async def payment_prepare_common(
    request: Request,
) -> JSONResponse | dict[str, Any]:

    body: dict[str, Any] = {}

    try:

        raw_body = await request.json()

        if isinstance(
            raw_body,
            dict,
        ):

            body = raw_body

    except Exception:
        pass

    payment_id = body_payment_id(
        body
    )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    job_id = body_job_id(
        body
    )

    payment = resolve_product_payment(
        payment_id=payment_id,
        job_id=job_id,
        service=service,
        title=title,
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            (
                "The requested customer product "
                "could not be found."
            ),
            404,
            service=service or None,
            title=title or None,
        )

    product = payment_document(
        payment
    )

    resolved_service = (
        normalize_service(
            payment.get(
                "service"
            )
        )
        or service
        or normalize_service(
            product.get(
                "service"
            )
        )
    )

    resolved_title = (
        normalize_title(
            payment.get(
                "job_title"
            )
        )
        or title
        or normalize_title(
            product.get(
                "title"
            )
            or product.get(
                "job_title"
            )
        )
    )

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_NOT_READY",
            (
                "The exact customer document "
                "has not been saved yet."
            ),
            409,
            service=resolved_service,
            title=resolved_title,
        )

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    reported = payment_is_reported(
        payment.get(
            "payment_status"
        )
    )

    return {
        "ok": True,

        "product": {
            "service": resolved_service,

            "title": resolved_title,

            "product_key": product_identity(
                resolved_service,
                resolved_title,
            ),

            "document_saved": True,

            "document_snapshot_saved": True,

            "document_filename": payment.get(
                "document_filename"
            ),

            "document_version": payment.get(
                "document_version"
            ),

            "document_saved_at": payment.get(
                "document_saved_at"
            ),

            "document_size": saved_document_size(
                payment
            ),

            "ready": True,
        },

        "payment": payment_public(
            payment
        ),

        "payment_status": payment.get(
            "payment_status"
        ),

        "payment_verified": verified,

        "download_unlocked": verified,

        "payment_reported": reported,

        # Compatibility only.
        "payment_id": payment.get(
            "payment_id"
        ),

        "job_id": payment.get(
            "job_id"
        ),
    }


@app.post("/api/payment/prepare")
async def payment_prepare_post(
    request: Request,
):
    return await payment_prepare_common(
        request
    )


@app.get("/api/payment/prepare")
async def payment_prepare_get(
    request: Request,
    payment_id: str | None = None,
    paymentId: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
):

    body = {
        "payment_id": payment_id
        or paymentId
        or "",

        "job_id": job_id or "",

        "service": service or "",

        "title": title or "",
    }

    # GET compatibility is handled through the same resolver.
    class QueryRequest:

        async def json(self):
            return body

    return await payment_prepare_common(
        QueryRequest()
    )


# ============================================================
# CUSTOMER PAYMENT REPORT
#
# Product can be resolved by:
#
#     Service + Title
#
# with existing internal references retained as compatibility.
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    body: dict[str, Any] = {}

    try:

        raw_body = await request.json()

        if isinstance(
            raw_body,
            dict,
        ):

            body = raw_body

    except Exception:
        pass

    payment_id = normalize_payment_id(
        payment_id
        or body_payment_id(body)
    )

    job_id = normalize_job_id(
        job_id
        or body_job_id(body)
    )

    service = normalize_service(
        service
        or body_service(body)
    )

    title = normalize_title(
        title
        or body_title(body)
    )

    payment_reference = clean(
        payment_reference
        or body.get(
            "payment_reference"
        )
        or body.get(
            "paymentReference"
        )
        or body.get(
            "reference"
        )
        or nested_payment_value(
            body,
            "payment_reference",
            "paymentReference",
            "reference",
        )
    )

    note = clean(
        note
        or body.get(
            "note"
        )
        or body.get(
            "message"
        )
        or body.get(
            "customer_note"
        )
    )

    payment = resolve_product_payment(
        payment_id=payment_id,
        job_id=job_id,
        service=service,
        title=title,
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            (
                "No customer product was found "
                "for this payment request."
            ),
            404,
            service=service or None,
            title=title or None,
        )

    # --------------------------------------------------------
    # NEVER regenerate the product.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact reviewed document "
                "is missing. Payment cannot "
                "be reported safely."
            ),
            409,
            service=payment.get(
                "service"
            ),
            title=payment.get(
                "job_title"
            ),
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(
        status
    ):

        return {
            "ok": True,

            "message": (
                "Payment has already been verified."
            ),

            "product": {
                "service": payment.get(
                    "service"
                ),

                "title": payment.get(
                    "job_title"
                ),

                "document_saved": True,

                "document_filename": payment.get(
                    "document_filename"
                ),
            },

            "payment": payment_public(
                payment
            ),

            "paid": True,

            "payment_verified": True,

            "download_unlocked": True,
        }

    updated = update_payment_record(
        payment[
            "payment_id"
        ],

        status="reported",

        payment_reference=(
            payment_reference
            or None
        ),

        customer_note=(
            note
            or None
        ),

        reported_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Payment report could not be saved.",
            500,
        )

    return {
        "ok": True,

        "message": (
            "Payment report received. "
            "Customer Care must verify the "
            "payment before the document is released."
        ),

        "product": {
            "service": updated.get(
                "service"
            ),

            "title": updated.get(
                "job_title"
            ),

            "document_saved": True,

            "document_filename": updated.get(
                "document_filename"
            ),
        },

        "payment": payment_public(
            updated
        ),

        # Compatibility only.
        "payment_id": updated.get(
            "payment_id"
        ),

        "job_id": updated.get(
            "job_id"
        ),

        "document_version": updated.get(
            "document_version"
        ),

        "document_saved": True,

        "paid": False,

        "payment_verified": False,

        "download_unlocked": False,

        "back_office_status": (
            "Payment Reported — Awaiting Verification"
        ),
    }


# ============================================================
# CUSTOMER PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    payment_id: str | None = None,
    paymentId: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
):

    payment = resolve_product_payment(
        payment_id=normalize_payment_id(
            payment_id
            or paymentId
        ),

        job_id=normalize_job_id(
            job_id
        ),

        service=normalize_service(
            service
        ),

        title=normalize_title(
            title
        ),
    )

    if not payment:

        return {
            "ok": True,

            "product": None,

            "payment": None,

            "payment_status": "none",

            "paid": False,

            "payment_verified": False,

            "download_unlocked": False,
        }

    saved = saved_document_exists(
        payment
    )

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    return {
        "ok": True,

        "product": {
            "service": payment.get(
                "service"
            ),

            "title": payment.get(
                "job_title"
            ),

            "document_saved": saved,

            "document_filename": payment.get(
                "document_filename"
            ),

            "document_version": payment.get(
                "document_version"
            ),
        },

        "payment": payment_public(
            payment
        ),

        "payment_status": payment.get(
            "payment_status"
        ),

        "paid": verified,

        "payment_verified": verified,

        "download_unlocked": verified,

        "document_saved": saved,

        "document_check": (
            "current_snapshot"
            if saved
            else "missing_snapshot"
        ),
    }


# ============================================================
# PAYMENT COMPLETE COMPATIBILITY
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete_compat(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    return await payment_report(
        request,
        payment_id=payment_id,
        job_id=job_id,
        service=service,
        title=title,
        payment_reference=payment_reference,
        note=note,
    )


# ============================================================
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():

    records = list_pending_payments()

    return {
        "ok": True,

        "count": len(records),

        "products": [
            {
                "service": record.get(
                    "service"
                ),

                "title": record.get(
                    "job_title"
                ),

                "document_saved": saved_document_exists(
                    record
                ),

                "document_filename": record.get(
                    "document_filename"
                ),

                "payment": payment_public(
                    record
                ),
            }
            for record in records
        ],

        # Existing compatibility collection.
        "payments": [
            payment_public(
                record
            )
            for record in records
        ],
    }


# ============================================================
# CUSTOMER CARE VERIFY
# ============================================================

@app.post(
    "/api/customer-care/payment/verify"
)
async def customer_care_verify(
    request: Request,
    payment_id: str | None = None,
    verified: bool = True,
    service: str | None = None,
    title: str | None = None,
    note: str | None = None,
):

    body: dict[str, Any] = {}

    try:

        raw_body = await request.json()

        if isinstance(
            raw_body,
            dict,
        ):

            body = raw_body

    except Exception:
        pass

    payment_id = normalize_payment_id(
        payment_id
        or body_payment_id(body)
    )

    service = normalize_service(
        service
        or body_service(body)
    )

    title = normalize_title(
        title
        or body_title(body)
    )

    if "verified" in body:

        raw_verified = body.get(
            "verified"
        )

        if isinstance(
            raw_verified,
            bool,
        ):

            verified = raw_verified

        else:

            verified = (
                clean(
                    raw_verified
                ).lower()
                in {
                    "true",
                    "1",
                    "yes",
                    "verified",
                }
            )

    note = clean(
        note
        or body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
    )

    payment = resolve_product_payment(
        payment_id=payment_id,
        service=service,
        title=title,
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "Customer product not found.",
            404,
            service=service or None,
            title=title or None,
        )

    internal_payment_id = normalize_payment_id(
        payment.get(
            "payment_id"
        )
    )

    if not verified:

        updated = update_payment_record(
            internal_payment_id,

            status="rejected",

            admin_note=(
                note
                or None
            ),
        )

        return {
            "ok": True,

            "message": (
                "Payment marked as rejected."
            ),

            "product": {
                "service": updated.get(
                    "service"
                ),

                "title": updated.get(
                    "job_title"
                ),

                "document_saved": saved_document_exists(
                    updated
                ),
            },

            "payment": payment_public(
                updated
            ),

            "paid": False,

            "payment_verified": False,

            "download_unlocked": False,
        }

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "Customer Care cannot unlock "
                "this product because the exact "
                "document is missing."
            ),
            409,
            service=payment.get(
                "service"
            ),
            title=payment.get(
                "job_title"
            ),
        )

    updated = update_payment_record(
        internal_payment_id,

        status="verified",

        admin_note=(
            note
            or None
        ),

        verified_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Payment could not be verified.",
            500,
        )

    return {
        "ok": True,

        "message": (
            "Payment verified. "
            "The exact saved customer document "
            "is now unlocked for delivery."
        ),

        "product": {
            "service": updated.get(
                "service"
            ),

            "title": updated.get(
                "job_title"
            ),

            "document_saved": True,

            "document_filename": updated.get(
                "document_filename"
            ),

            "document_version": updated.get(
                "document_version"
            ),
        },

        "payment": payment_public(
            updated
        ),

        "paid": True,

        "payment_verified": True,

        "download_unlocked": True,

        "document_saved": True,
    }


# ============================================================
# BACK OFFICE LOGIN
# ============================================================

@app.post(
    "/api/back-office/login"
)
async def back_office_login(
    request: Request,
):

    try:

        body = await request.json()

        if not isinstance(
            body,
            dict,
        ):

            body = {}

    except Exception:

        body = {}

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    return {
        "ok": True,

        "authenticated": True,

        "back_office": True,

        "message": (
            "Back Office access granted."
        ),
    }


# ============================================================
# BACK OFFICE PAYMENT LIST
# ============================================================

@app.get(
    "/api/back-office/payments"
)
async def back_office_payments(
    request: Request,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    records = list_all_payments()

    pending = [
        payment_public(
            record
        )
        for record in records
        if payment_is_reported(
            record.get(
                "payment_status",
                "",
            )
        )
    ]

    return {
        "ok": True,

        "count": len(records),

        "pending_count": len(
            pending
        ),

        "products": [
            {
                "service": record.get(
                    "service"
                ),

                "title": record.get(
                    "job_title"
                ),

                "document_saved": saved_document_exists(
                    record
                ),

                "document_filename": record.get(
                    "document_filename"
                ),

                "payment_status": record.get(
                    "payment_status"
                ),
            }
            for record in records
        ],

        "payments": [
            payment_public(
                record
            )
            for record in records
        ],

        "pending_payments": pending,
    }


# ============================================================
# BACK OFFICE JOB LIST
#
# Existing endpoint retained for compatibility.
#
# The returned business object is now the PRODUCT:
# Service + Title + Document.
# ============================================================

@app.get(
    "/api/back-office/jobs"
)
async def back_office_jobs(
    request: Request,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    records = list_all_payments()

    products: list[dict[str, Any]] = []

    for payment in records:

        public = payment_public(
            payment
        )

        if not public:
            continue

        public["status_label"] = (
            "Payment Reported — Awaiting Verification"
            if payment_is_reported(
                payment.get(
                    "payment_status",
                    "",
                )
            )
            else (
                "Payment Verified — Download Unlocked"
                if payment_is_verified(
                    payment.get(
                        "payment_status",
                        "",
                    )
                )
                else clean(
                    payment.get(
                        "payment_status"
                    )
                )
            )
        )

        products.append(
            public
        )

    return {
        "ok": True,

        "count": len(
            products
        ),

        "products": products,

        # Existing UI compatibility.
        "jobs": products,

        "payments": products,
    }


# ============================================================
# BACK OFFICE SINGLE PRODUCT/PAYMENT
# ============================================================

@app.get(
    "/api/back-office/payment"
)
async def back_office_payment(
    request: Request,
    payment_id: str | None = None,
    paymentId: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = resolve_product_payment(
        payment_id=normalize_payment_id(
            payment_id
            or paymentId
        ),

        job_id=normalize_job_id(
            job_id
        ),

        service=normalize_service(
            service
        ),

        title=normalize_title(
            title
        ),
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "Customer product not found.",
            404,
        )

    return {
        "ok": True,

        "product": {
            "service": payment.get(
                "service"
            ),

            "title": payment.get(
                "job_title"
            ),

            "document_saved": saved_document_exists(
                payment
            ),

            "document_filename": payment.get(
                "document_filename"
            ),

            "document_version": payment.get(
                "document_version"
            ),
        },

        "payment": payment_public(
            payment
        ),
    }


# ============================================================
# BACK OFFICE DOCUMENT INFO
# ============================================================

@app.get(
    "/api/back-office/document-info"
)
async def back_office_document_info(
    request: Request,
    payment_id: str | None = None,
    paymentId: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = resolve_product_payment(
        payment_id=normalize_payment_id(
            payment_id
            or paymentId
        ),

        job_id=normalize_job_id(
            job_id
        ),

        service=normalize_service(
            service
        ),

        title=normalize_title(
            title
        ),
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "Customer product not found.",
            404,
        )

    exists = saved_document_exists(
        payment
    )

    path = saved_document_path(
        payment
    )

    return {
        "ok": True,

        "service": payment.get(
            "service"
        ),

        "title": payment.get(
            "job_title"
        ),

        "document_filename": payment.get(
            "document_filename"
        ),

        "document_version": payment.get(
            "document_version"
        ),

        "document_saved": exists,

        "document_snapshot_saved": exists,

        "document_saved_at": payment.get(
            "document_saved_at"
        ),

        "document_size": saved_document_size(
            payment
        ),

        "document_path": (
            str(path)
            if exists and path
            else None
        ),

        "payment_status": payment.get(
            "payment_status"
        ),

        "download_unlocked": payment_is_verified(
            payment.get(
                "payment_status",
                "",
            )
        ),

        # Internal compatibility only.
        "payment_id": payment.get(
            "payment_id"
        ),
    }


# ============================================================
# BACK OFFICE SAVED DOCUMENT
#
# SERVES EXISTING SNAPSHOT ONLY.
#
# NEVER REGENERATES.
# ============================================================

@app.get(
    "/api/back-office/document"
)
async def back_office_document(
    request: Request,
    payment_id: str | None = None,
    paymentId: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = resolve_product_payment(
        payment_id=normalize_payment_id(
            payment_id
            or paymentId
        ),

        job_id=normalize_job_id(
            job_id
        ),

        service=normalize_service(
            service
        ),

        title=normalize_title(
            title
        ),
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "Customer product not found.",
            404,
        )

    output = saved_document_path(
        payment
    )

    if (
        not output
        or not output.is_file()
        or output.stat().st_size <= 0
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact saved customer "
                "document is missing."
            ),
            409,
        )

    filename = (
        clean(
            payment.get(
                "document_filename"
            )
        )
        or output.name
    )

    return FileResponse(
        path=str(output),

        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        ),

        filename=Path(
            filename
        ).name,

        headers={
            "X-Product-Service": clean(
                payment.get(
                    "service"
                )
            ),

            "X-Product-Title": clean(
                payment.get(
                    "job_title"
                )
            ),

            "X-Document-Version": clean(
                payment.get(
                    "document_version"
                )
            ),

            "X-Document-Snapshot": "true",

            "X-Back-Office": "true",
        },
    )


# ============================================================
# BACK OFFICE VERIFY PAYMENT
# ============================================================

@app.post(
    "/api/back-office/payment/verify"
)
async def back_office_verify_payment(
    request: Request,
):

    try:

        body = await request.json()

        if not isinstance(
            body,
            dict,
        ):

            body = {}

    except Exception:

        body = {}

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    payment_id = body_payment_id(
        body
    )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    note = clean(
        body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
    )

    payment = resolve_product_payment(
        payment_id=payment_id,
        service=service,
        title=title,
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "Customer product not found.",
            404,
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if status in {
        "rejected",
        "cancelled",
        "canceled",
    }:

        return json_response_error(
            "PAYMENT_NOT_VERIFIABLE",
            (
                "This payment cannot be unlocked "
                "because it is marked as rejected "
                "or cancelled."
            ),
            409,
        )

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "UNLOCK DOWNLOAD cannot continue "
                "because the exact saved customer "
                "document is missing."
            ),
            409,
        )

    updated = update_payment_record(
        payment[
            "payment_id"
        ],

        status="verified",

        admin_note=(
            note
            or None
        ),

        verified_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Payment could not be verified.",
            500,
        )

    return {
        "ok": True,

        "message": (
            "Payment verified. "
            "UNLOCK DOWNLOAD is now active "
            "for the exact saved customer document."
        ),

        "product": {
            "service": updated.get(
                "service"
            ),

            "title": updated.get(
                "job_title"
            ),

            "document_saved": True,

            "document_filename": updated.get(
                "document_filename"
            ),

            "document_version": updated.get(
                "document_version"
            ),
        },

        "payment": payment_public(
            updated
        ),

        "payment_verified": True,

        "download_unlocked": True,

        "document_saved": True,
    }


# ============================================================
# BACK OFFICE ACTIVATE DOWNLOAD
#
# EXISTING SNAPSHOT → VERIFIED
#
# NO REGENERATION.
# ============================================================

@app.post(
    "/api/back-office/activate-download"
)
async def back_office_activate_download(
    request: Request,
):

    try:

        body = await request.json()

        if not isinstance(
            body,
            dict,
        ):

            body = {}

    except Exception:

        body = {}

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    payment_id = body_payment_id(
        body
    )

    job_id = body_job_id(
        body
    )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    note = clean(
        body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
    )

    payment = resolve_product_payment(
        payment_id=payment_id,
        job_id=job_id,
        service=service,
        title=title,
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            (
                "No customer product was found."
            ),
            404,
            service=service or None,
            title=title or None,
        )

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "UNLOCK DOWNLOAD cannot continue "
                "because the exact saved customer "
                "document is missing."
            ),
            409,
        )

    internal_payment_id = normalize_payment_id(
        payment.get(
            "payment_id"
        )
    )

    updated = update_payment_record(
        internal_payment_id,

        status="verified",

        admin_note=(
            note
            or None
        ),

        verified_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Download could not be unlocked.",
            500,
        )

    return {
        "ok": True,

        "message": (
            "Payment verified and the exact "
            "saved customer document is unlocked."
        ),

        "product": {
            "service": updated.get(
                "service"
            ),

            "title": updated.get(
                "job_title"
            ),

            "document_saved": True,

            "document_filename": updated.get(
                "document_filename"
            ),

            "document_version": updated.get(
                "document_version"
            ),
        },

        "payment": payment_public(
            updated
        ),

        "payment_verified": True,

        "download_unlocked": True,

        "document_saved": True,
    }


# ============================================================
# BACK OFFICE REJECT
# ============================================================

@app.post(
    "/api/back-office/payment/reject"
)
async def back_office_reject_payment(
    request: Request,
):

    try:

        body = await request.json()

        if not isinstance(
            body,
            dict,
        ):

            body = {}

    except Exception:

        body = {}

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    payment_id = body_payment_id(
        body
    )

    service = body_service(
        body
    )

    title = body_title(
        body
    )

    note = clean(
        body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
    )

    payment = resolve_product_payment(
        payment_id=payment_id,
        service=service,
        title=title,
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            "Customer product not found.",
            404,
        )

    if payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_response_error(
            "PAYMENT_ALREADY_UNLOCKED",
            (
                "This product has already been "
                "verified and download is unlocked."
            ),
            409,
        )

    updated = update_payment_record(
        payment[
            "payment_id"
        ],

        status="rejected",

        admin_note=(
            note
            or None
        ),
    )

    return {
        "ok": True,

        "message": (
            "Payment marked as rejected. "
            "The customer document remains locked."
        ),

        "product": {
            "service": updated.get(
                "service"
            ),

            "title": updated.get(
                "job_title"
            ),

            "document_saved": saved_document_exists(
                updated
            ),
        },

        "payment": payment_public(
            updated
        ),

        "payment_verified": False,

        "download_unlocked": False,
    }


# ============================================================
# CUSTOMER DOWNLOAD
#
# THIS IS THE PRODUCT DELIVERY POINT.
#
# The API serves the EXACT SAVED DOCUMENT.
#
# It does NOT:
#
#   - regenerate it
#   - reconstruct it
#   - create a new version
#   - use payment data as the product
#
# Payment verification only unlocks delivery.
# ============================================================

@app.get(
    "/api/download"
)
async def download_document(
    payment_id: str | None = None,
    paymentId: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
    title: str | None = None,
):

    payment = resolve_product_payment(
        payment_id=normalize_payment_id(
            payment_id
            or paymentId
        ),

        job_id=normalize_job_id(
            job_id
        ),

        service=normalize_service(
            service
        ),

        title=normalize_title(
            title
        ),
    )

    if not payment:

        return json_response_error(
            "PRODUCT_NOT_FOUND",
            (
                "The customer document "
                "could not be found."
            ),
            404,
            service=service or None,
            title=title or None,
        )

    if not payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            (
                "The customer document remains "
                "locked until payment is verified."
            ),
            403,
            payment_status=payment.get(
                "payment_status"
            ),
            download_unlocked=False,
        )

    output = saved_document_path(
        payment
    )

    if (
        not output
        or not output.is_file()
        or output.stat().st_size <= 0
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact customer document "
                "is missing. Download remains "
                "locked for safety."
            ),
            409,
            service=payment.get(
                "service"
            ),
            title=payment.get(
                "job_title"
            ),
            download_unlocked=False,
        )

    increment_download(
        payment[
            "payment_id"
        ]
    )

    filename = (
        clean(
            payment.get(
                "document_filename"
            )
        )
        or output.name
    )

    return FileResponse(
        path=str(output),

        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        ),

        filename=Path(
            filename
        ).name,

        headers={

            "X-Product-Service": clean(
                payment.get(
                    "service"
                )
            ),

            "X-Product-Title": clean(
                payment.get(
                    "job_title"
                )
            ),

            "X-Document-Version": clean(
                payment.get(
                    "document_version"
                )
            ),

            "X-Download-Unlocked": "true",

            "X-Document-Snapshot": "true",
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup() -> None:

    init_db()

    print(
        "[PAYMENT API] Startup complete."
    )

    print(
        "[PAYMENT API] Version: "
        + APP_VERSION
    )

    print(
        "[PAYMENT API] Business model: "
        "DOCUMENT_PRODUCT_FIRST"
    )

    print(
        "[PAYMENT API] Product identity: "
        "SERVICE + TITLE"
    )

    print(
        "[PAYMENT API] Database: "
        + str(DB_PATH)
    )

    print(
        "[PAYMENT API] Download directory: "
        + str(DOWNLOAD_DIR)
    )

    print(
        "[PAYMENT API] Old API configured: "
        + str(
            bool(
                OLD_API_BASE_URL
            )
        )
    )

    print(
        "[PAYMENT API] Back Office key configured: "
        + str(
            bool(
                BACK_OFFICE_ADMIN_KEY
            )
        )
    )


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "payment_api:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )
