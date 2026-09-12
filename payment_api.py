from __future__ import annotations

import json
import os
import re
import smtplib
import sqlite3
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
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
# DOCUMENT-FIRST PAYMENT + CUSTOMER CARE DELIVERY
#
# CORE BUSINESS RULE
#
# The finished document is the product.
#
# Payment verification is a business-control step.
#
# Delivery is the objective.
#
# The exact finished document is saved once and reused for:
#
#   CUSTOMER DOWNLOAD
#   CUSTOMER CARE DOWNLOAD
#   EMAIL DELIVERY
#   WHATSAPP DELIVERY
#
# NO endpoint regenerates an already-saved document.
#
# Protected files are NOT involved:
#
#   workspace.html
#   review.html
#   database.py
#
# ============================================================


APP_VERSION = (
    "payment-delivery-v5-document-first-customer-care"
)

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# DATABASE
# ============================================================

_raw_db_path = os.getenv(
    "PAYMENT_DB_PATH",
    str(BASE_DIR / "payment_gateway.db"),
).strip()

DB_PATH = Path(
    _raw_db_path
).expanduser()

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
# OPTIONAL CUSTOMER CARE EMAIL
#
# These are optional. Download/unlock does NOT depend on them.
#
# SMTP_HOST
# SMTP_PORT
# SMTP_USERNAME
# SMTP_PASSWORD
# SMTP_FROM
# SMTP_USE_TLS
# ============================================================

SMTP_HOST = (
    os.getenv(
        "SMTP_HOST",
        "",
    )
    .strip()
)

try:
    SMTP_PORT = int(
        os.getenv(
            "SMTP_PORT",
            "587",
        )
    )
except Exception:
    SMTP_PORT = 587

SMTP_USERNAME = (
    os.getenv(
        "SMTP_USERNAME",
        "",
    )
    .strip()
)

SMTP_PASSWORD = (
    os.getenv(
        "SMTP_PASSWORD",
        "",
    )
    .strip()
)

SMTP_FROM = (
    os.getenv(
        "SMTP_FROM",
        SMTP_USERNAME,
    )
    .strip()
)

SMTP_USE_TLS = (
    os.getenv(
        "SMTP_USE_TLS",
        "true",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)


# ============================================================
# OPTIONAL WHATSAPP CLOUD API
#
# WHATSAPP_ACCESS_TOKEN
# WHATSAPP_PHONE_NUMBER_ID
#
# WhatsApp delivery requires a configured WhatsApp provider.
# The document remains available for normal download even when
# WhatsApp is not configured.
# ============================================================

WHATSAPP_ACCESS_TOKEN = (
    os.getenv(
        "WHATSAPP_ACCESS_TOKEN",
        "",
    )
    .strip()
)

WHATSAPP_PHONE_NUMBER_ID = (
    os.getenv(
        "WHATSAPP_PHONE_NUMBER_ID",
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
    title=(
        "Naija Pocket Business Center "
        "Payment and Customer Care API"
    ),
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


def normalize_status(
    status: Any,
) -> str:
    return (
        clean(status)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def normalize_payment_id(
    value: Any,
) -> str:
    value = clean(value)

    if not value:
        return ""

    return value.upper()


def normalize_job_id(
    value: Any,
) -> str:
    return clean(value)


def normalize_version(
    value: Any,
) -> str:
    return clean(value)


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


# ============================================================
# PAYMENT STATUS
# ============================================================

def payment_is_reported(
    status: Any,
) -> bool:

    return normalize_status(
        status
    ) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def payment_is_verified(
    status: Any,
) -> bool:

    return normalize_status(
        status
    ) in {
        "verified",
        "completed",
        "complete",
        "paid",
    }


def payment_is_pending(
    status: Any,
) -> bool:

    return normalize_status(
        status
    ) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


# ============================================================
# DELIVERY STATUS
# ============================================================

def delivery_is_verified(
    delivery: dict[str, Any] | None,
) -> bool:

    if not delivery:
        return False

    return clean(
        delivery.get(
            "verification_status"
        )
    ).lower() == "verified"


def delivery_is_unlocked(
    delivery: dict[str, Any] | None,
) -> bool:

    if not delivery:
        return False

    return clean(
        delivery.get(
            "release_status"
        )
    ).lower() == "unlocked"


# ============================================================
# REQUEST VALUE HELPERS
# ============================================================

def nested_payment_value(
    body: dict[str, Any],
    *keys: str,
) -> Any:

    payment = body.get(
        "payment"
    )

    if isinstance(
        payment,
        dict,
    ):

        for key in keys:

            value = payment.get(
                key
            )

            if value not in (
                None,
                "",
            ):
                return value

    return None


def body_payment_id(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get(
            "payment_id"
        ),
        body.get(
            "paymentId"
        ),
        body.get(
            "paymentID"
        ),
        body.get(
            "id"
        ),
        nested_payment_value(
            body,
            "payment_id",
            "paymentId",
            "paymentID",
            "id",
        ),
    )

    for value in candidates:

        value = normalize_payment_id(
            value
        )

        if value:
            return value

    return ""


def body_job_id(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get(
            "job_id"
        ),
        body.get(
            "jobId"
        ),
        body.get(
            "work_id"
        ),
        body.get(
            "workId"
        ),
        body.get(
            "document_id"
        ),
        body.get(
            "documentId"
        ),
        nested_payment_value(
            body,
            "job_id",
            "jobId",
            "work_id",
            "workId",
            "document_id",
            "documentId",
        ),
    )

    for value in candidates:

        value = normalize_job_id(
            value
        )

        if value:
            return value

    return ""


def body_version_id(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get(
            "version_id"
        ),
        body.get(
            "versionId"
        ),
        body.get(
            "document_version"
        ),
        body.get(
            "version"
        ),
    )

    for value in candidates:

        value = normalize_version(
            value
        )

        if value:
            return value

    return ""


def body_customer_email(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get(
            "customer_email"
        ),
        body.get(
            "customerEmail"
        ),
        body.get(
            "email"
        ),
    )

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


def body_customer_phone(
    body: dict[str, Any],
) -> str:

    candidates = (
        body.get(
            "customer_phone"
        ),
        body.get(
            "customerPhone"
        ),
        body.get(
            "phone"
        ),
        body.get(
            "phone_number"
        ),
        body.get(
            "phoneNumber"
        ),
        body.get(
            "whatsapp"
        ),
        body.get(
            "whatsapp_number"
        ),
        body.get(
            "whatsappNumber"
        ),
    )

    for value in candidates:

        value = clean(value)

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

        # ----------------------------------------------------
        # EXISTING PAYMENT TABLE
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                payment_id TEXT NOT NULL UNIQUE,

                job_id TEXT NOT NULL,

                customer_id TEXT,

                service TEXT,

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

        # ----------------------------------------------------
        # DOCUMENT-FIRST DELIVERY TABLE
        #
        # This is the important architectural addition.
        #
        # A finished document exists independently of a
        # payment lookup.
        # ----------------------------------------------------

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                job_id TEXT NOT NULL,

                version_id TEXT NOT NULL,

                customer_id TEXT,

                customer_email TEXT,

                customer_phone TEXT,

                service TEXT,

                amount REAL NOT NULL DEFAULT 0,

                currency TEXT NOT NULL DEFAULT 'NGN',

                document_filename TEXT NOT NULL,

                document_saved_path TEXT NOT NULL,

                document_saved_at TEXT NOT NULL,

                document_payload TEXT,

                payment_id TEXT,

                verification_status TEXT
                    NOT NULL DEFAULT 'pending',

                release_status TEXT
                    NOT NULL DEFAULT 'locked',

                verification_note TEXT,

                verified_at TEXT,

                unlocked_at TEXT,

                download_count INTEGER
                    NOT NULL DEFAULT 0,

                last_downloaded_at TEXT,

                last_delivery_channel TEXT,

                last_delivery_at TEXT,

                email_sent_at TEXT,

                whatsapp_sent_at TEXT,

                created_at TEXT NOT NULL,

                updated_at TEXT NOT NULL,

                UNIQUE (
                    job_id,
                    version_id
                )
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_document_deliveries_job
            ON document_deliveries(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_document_deliveries_status
            ON document_deliveries(
                verification_status,
                release_status
            )
            """
        )

        # ----------------------------------------------------
        # EXISTING INDEXES
        # ----------------------------------------------------

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
            idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        # ----------------------------------------------------
        # SAFE MIGRATION OF EXISTING PAYMENT TABLE
        # ----------------------------------------------------

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        if (
            "document_saved_path"
            not in columns
        ):

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN document_saved_path TEXT
                """
            )

        if (
            "document_saved_at"
            not in columns
        ):

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN document_saved_at TEXT
                """
            )

        conn.commit()


# ============================================================
# ROW HELPERS
# ============================================================

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

    info = {
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


def document_record_count() -> int:

    try:

        init_db()

        with connect_db() as conn:

            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM document_deliveries
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
                WHERE payment_id
                    COLLATE NOCASE = ?
                LIMIT 1
                """,
                (
                    payment_id,
                ),
            ).fetchone()

    return row_to_dict(row)


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:

    job_id = normalize_job_id(
        job_id
    )

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


def get_payment_for_job_version(
    job_id: str,
    version_id: str,
) -> dict[str, Any] | None:

    job_id = normalize_job_id(
        job_id
    )

    version_id = normalize_version(
        version_id
    )

    if not job_id or not version_id:
        return None

    init_db()

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
              AND document_version = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                job_id,
                version_id,
            ),
        ).fetchone()

    return row_to_dict(row)


def get_payments_for_job(
    job_id: str,
) -> list[dict[str, Any]]:

    job_id = normalize_job_id(
        job_id
    )

    if not job_id:
        return []

    init_db()

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (
                job_id,
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def list_pending_payments() -> list[
    dict[str, Any]
]:

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


def list_all_payments() -> list[
    dict[str, Any]
]:

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
# DOCUMENT-FIRST READERS
# ============================================================

def get_document_delivery(
    job_id: str,
    version_id: str = "",
) -> dict[str, Any] | None:

    job_id = normalize_job_id(
        job_id
    )

    version_id = normalize_version(
        version_id
    )

    if not job_id:
        return None

    init_db()

    with connect_db() as conn:

        if version_id:

            row = conn.execute(
                """
                SELECT *
                FROM document_deliveries
                WHERE job_id = ?
                  AND version_id = ?
                LIMIT 1
                """,
                (
                    job_id,
                    version_id,
                ),
            ).fetchone()

        else:

            row = conn.execute(
                """
                SELECT *
                FROM document_deliveries
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    job_id,
                ),
            ).fetchone()

    return row_to_dict(row)


def get_document_by_payment_id(
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
            FROM document_deliveries
            WHERE payment_id
                COLLATE NOCASE = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                payment_id,
            ),
        ).fetchone()

    return row_to_dict(row)


def list_document_deliveries() -> list[
    dict[str, Any]
]:

    init_db()

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM document_deliveries
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def list_pending_documents() -> list[
    dict[str, Any]
]:

    init_db()

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM document_deliveries
            WHERE release_status != 'unlocked'
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# PAYMENT WRITE
# ============================================================

def create_payment_record(
    *,
    payment_id: str,
    job_id: str,
    customer_id: str,
    service: str,
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

    if not payment_id:
        raise RuntimeError(
            "Cannot create payment without payment ID."
        )

    if not job_id:
        raise RuntimeError(
            "Cannot create payment without job ID."
        )

    timestamp = now_iso()

    payload_json = json.dumps(
        document_payload,
        ensure_ascii=False,
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
                    job_id,
                    customer_id,
                    service,
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
                    "SQLite INSERT completed without "
                    "an immediately readable payment row."
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
            "payment ID was inserted but could not "
            "be read back after SQLite commit."
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
            WHERE payment_id
                COLLATE NOCASE = ?
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
            WHERE payment_id
                COLLATE NOCASE = ?
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
            WHERE payment_id
                COLLATE NOCASE = ?
            """,
            (
                payment_id,
            ),
        )

        conn.commit()


# ============================================================
# DOCUMENT-FIRST DATABASE WRITE
# ============================================================

def upsert_document_delivery(
    document: dict[str, Any],
    saved_path: Path,
    *,
    payment_id: str = "",
    customer_email: str = "",
    customer_phone: str = "",
) -> dict[str, Any]:

    job_id = normalize_job_id(
        document.get(
            "job_id"
        )
    )

    version_id = normalize_version(
        document.get(
            "version_id"
        )
        or document.get(
            "document_version"
        )
    )

    if not job_id:
        raise RuntimeError(
            "DOCUMENT_JOB_ID_REQUIRED"
        )

    if not version_id:
        raise RuntimeError(
            "DOCUMENT_VERSION_REQUIRED"
        )

    filename = (
        clean(
            document.get(
                "filename"
            )
        )
        or clean(
            document.get(
                "document_filename"
            )
        )
        or f"naija_pocket_{job_id}.docx"
    )

    payload = snapshot_payload(
        document
    )

    timestamp = now_iso()

    existing = get_document_delivery(
        job_id,
        version_id,
    )

    if existing:

        # ----------------------------------------------------
        # CRITICAL:
        #
        # NEVER replace an existing saved document with a new
        # generated file.
        #
        # We only update metadata.
        # ----------------------------------------------------

        existing_path = clean(
            existing.get(
                "document_saved_path"
            )
        )

        if existing_path:

            actual = Path(
                existing_path
            )

            if (
                actual.is_file()
                and actual.stat().st_size > 0
            ):
                saved_path = actual

        with connect_db() as conn:

            conn.execute(
                """
                UPDATE document_deliveries
                SET
                    customer_id = COALESCE(
                        NULLIF(?, ''),
                        customer_id
                    ),
                    customer_email = COALESCE(
                        NULLIF(?, ''),
                        customer_email
                    ),
                    customer_phone = COALESCE(
                        NULLIF(?, ''),
                        customer_phone
                    ),
                    service = COALESCE(
                        NULLIF(?, ''),
                        service
                    ),
                    amount = CASE
                        WHEN ? > 0
                        THEN ?
                        ELSE amount
                    END,
                    payment_id = COALESCE(
                        NULLIF(?, ''),
                        payment_id
                    ),
                    updated_at = ?
                WHERE job_id = ?
                  AND version_id = ?
                """,
                (
                    clean(
                        document.get(
                            "customer_id"
                        )
                    ),
                    customer_email,
                    customer_phone,
                    clean(
                        document.get(
                            "service"
                        )
                    ),
                    money(
                        document.get(
                            "amount"
                        )
                    ),
                    money(
                        document.get(
                            "amount"
                        )
                    ),
                    normalize_payment_id(
                        payment_id
                    ),
                    timestamp,
                    job_id,
                    version_id,
                ),
            )

            conn.commit()

        refreshed = get_document_delivery(
            job_id,
            version_id,
        )

        if not refreshed:
            raise RuntimeError(
                "DOCUMENT_DELIVERY_RECORD_MISSING"
            )

        return refreshed

    with connect_db() as conn:

        conn.execute(
            """
            INSERT INTO document_deliveries (
                job_id,
                version_id,
                customer_id,
                customer_email,
                customer_phone,
                service,
                amount,
                currency,
                document_filename,
                document_saved_path,
                document_saved_at,
                document_payload,
                payment_id,
                verification_status,
                release_status,
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
                ?,
                ?,
                ?,
                ?,
                'pending',
                'locked',
                ?,
                ?
            )
            """,
            (
                job_id,
                version_id,
                clean(
                    document.get(
                        "customer_id"
                    )
                ),
                customer_email,
                customer_phone,
                clean(
                    document.get(
                        "service"
                    )
                ),
                money(
                    document.get(
                        "amount"
                    )
                ),
                DEFAULT_CURRENCY,
                filename,
                str(saved_path),
                timestamp,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
                normalize_payment_id(
                    payment_id
                ),
                timestamp,
                timestamp,
            ),
        )

        conn.commit()

    result = get_document_delivery(
        job_id,
        version_id,
    )

    if not result:
        raise RuntimeError(
            "DOCUMENT_DELIVERY_PERSISTENCE_FAILED"
        )

    return result


def update_document_delivery(
    job_id: str,
    version_id: str,
    *,
    payment_id: str | None = None,
    verification_status: str | None = None,
    release_status: str | None = None,
    verification_note: str | None = None,
    verified_at: str | None = None,
    unlocked_at: str | None = None,
    customer_email: str | None = None,
    customer_phone: str | None = None,
    last_delivery_channel: str | None = None,
    last_delivery_at: str | None = None,
    email_sent_at: str | None = None,
    whatsapp_sent_at: str | None = None,
) -> dict[str, Any] | None:

    fields: list[str] = []
    values: list[Any] = []

    if payment_id is not None:

        fields.append(
            "payment_id = ?"
        )

        values.append(
            normalize_payment_id(
                payment_id
            )
        )

    if verification_status is not None:

        fields.append(
            "verification_status = ?"
        )

        values.append(
            verification_status
        )

    if release_status is not None:

        fields.append(
            "release_status = ?"
        )

        values.append(
            release_status
        )

    if verification_note is not None:

        fields.append(
            "verification_note = ?"
        )

        values.append(
            verification_note
        )

    if verified_at is not None:

        fields.append(
            "verified_at = ?"
        )

        values.append(
            verified_at
        )

    if unlocked_at is not None:

        fields.append(
            "unlocked_at = ?"
        )

        values.append(
            unlocked_at
        )

    if customer_email is not None:

        fields.append(
            "customer_email = ?"
        )

        values.append(
            customer_email
        )

    if customer_phone is not None:

        fields.append(
            "customer_phone = ?"
        )

        values.append(
            customer_phone
        )

    if last_delivery_channel is not None:

        fields.append(
            "last_delivery_channel = ?"
        )

        values.append(
            last_delivery_channel
        )

    if last_delivery_at is not None:

        fields.append(
            "last_delivery_at = ?"
        )

        values.append(
            last_delivery_at
        )

    if email_sent_at is not None:

        fields.append(
            "email_sent_at = ?"
        )

        values.append(
            email_sent_at
        )

    if whatsapp_sent_at is not None:

        fields.append(
            "whatsapp_sent_at = ?"
        )

        values.append(
            whatsapp_sent_at
        )

    if not fields:
        return get_document_delivery(
            job_id,
            version_id,
        )

    fields.append(
        "updated_at = ?"
    )

    values.append(
        now_iso()
    )

    values.extend(
        [
            normalize_job_id(job_id),
            normalize_version(version_id),
        ]
    )

    with connect_db() as conn:

        conn.execute(
            f"""
            UPDATE document_deliveries
            SET {', '.join(fields)}
            WHERE job_id = ?
              AND version_id = ?
            """,
            tuple(values),
        )

        conn.commit()

    return get_document_delivery(
        job_id,
        version_id,
    )


# ============================================================
# DOCUMENT NORMALIZATION
# ============================================================

def normalize_pages(
    value: Any,
) -> list[str]:

    if value is None:
        return []

    if isinstance(
        value,
        str,
    ):

        text = value.strip()

        return (
            [text]
            if text
            else []
        )

    if isinstance(
        value,
        (list, tuple),
    ):

        result: list[str] = []

        for item in value:

            if isinstance(
                item,
                dict,
            ):

                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("body")
                    or item.get("page_text")
                    or ""
                )

                text = str(
                    text
                ).strip()

                if text:
                    result.append(
                        text
                    )

            else:

                text = str(
                    item
                ).strip()

                if text:
                    result.append(
                        text
                    )

        return result

    if isinstance(
        value,
        dict,
    ):

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or value.get("page_text")
            or ""
        )

        text = str(
            text
        ).strip()

        return (
            [text]
            if text
            else []
        )

    text = str(
        value
    ).strip()

    return (
        [text]
        if text
        else []
    )


def document_has_content(
    document: dict[str, Any],
) -> bool:

    pages = normalize_pages(
        document.get(
            "pages"
        )
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
    job_id: str,
    *,
    version_id: str = "",
    filename: str = "",
    service: str = "",
    customer_id: str = "",
    amount: float = 0.0,
) -> dict[str, Any]:

    pages = normalize_pages(
        document.get(
            "pages"
        )
        or document.get(
            "document_pages"
        )
        or document.get(
            "review_pages"
        )
        or document.get(
            "page_texts"
        )
    )

    text = clean(
        document.get(
            "document_text"
        )
        or document.get(
            "text"
        )
        or document.get(
            "content"
        )
    )

    if not pages and text:
        pages = [text]

    final_version = clean(
        version_id
        or document.get(
            "version_id"
        )
        or document.get(
            "document_version"
        )
        or document.get(
            "version"
        )
    )

    final_filename = clean(
        filename
        or document.get(
            "filename"
        )
        or document.get(
            "document_filename"
        )
        or f"naija_pocket_{job_id}.docx"
    )

    final_filename = Path(
        final_filename
    ).name

    if not final_filename.lower().endswith(
        ".docx"
    ):
        final_filename += ".docx"

    final_service = clean(
        service
        or document.get(
            "service"
        )
    )

    final_customer = clean(
        customer_id
        or document.get(
            "customer_id"
        )
    )

    final_amount = (
        money(amount)
        or money(
            document.get(
                "amount"
            )
        )
        or money(
            document.get(
                "total_amount"
            )
        )
        or money(
            document.get(
                "price"
            )
        )
    )

    return {
        "job_id": clean(
            job_id
        ),
        "pages": pages,
        "document_text": text,
        "version_id": final_version,
        "document_version": final_version,
        "filename": final_filename,
        "document_filename": final_filename,
        "service": final_service,
        "customer_id": final_customer,
        "amount": final_amount,
        "review_finished": True,
        "status": "review_complete",
    }


def snapshot_payload(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id": document.get(
            "job_id"
        ),
        "pages": normalize_pages(
            document.get(
                "pages"
            )
        ),
        "document_text": clean(
            document.get(
                "document_text"
            )
        ),
        "version_id": clean(
            document.get(
                "version_id"
            )
        ),
        "document_version": clean(
            document.get(
                "document_version"
            )
        ),
        "service": clean(
            document.get(
                "service"
            )
        ),
        "filename": clean(
            document.get(
                "filename"
            )
        ),
        "document_filename": clean(
            document.get(
                "document_filename"
            )
        ),
        "customer_id": clean(
            document.get(
                "customer_id"
            )
        ),
        "amount": money(
            document.get(
                "amount"
            )
        ),
    }


# ============================================================
# DOCUMENT PATH HELPERS
# ============================================================

def delivery_document_path(
    delivery: dict[str, Any],
) -> Path | None:

    raw = clean(
        delivery.get(
            "document_saved_path"
        )
    )

    if not raw:
        return None

    path = Path(
        raw
    )

    if path.is_absolute():
        return path

    return BASE_DIR / path


def delivery_document_exists(
    delivery: dict[str, Any] | None,
) -> bool:

    if not delivery:
        return False

    path = delivery_document_path(
        delivery
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


def delivery_document_size(
    delivery: dict[str, Any] | None,
) -> int:

    path = delivery_document_path(
        delivery
    )

    if not path:
        return 0

    try:
        return path.stat().st_size
    except Exception:
        return 0


# ============================================================
# LEGACY PAYMENT DOCUMENT PATH
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

    path = Path(
        raw
    )

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

    for index, line in enumerate(
        lines
    ):

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
            paragraph_xml(
                page
            )
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
</w:styles>
"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml"
    ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.styles+xml"/>
</Types>
"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""

    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>
"""

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
# EXACT DOCUMENT SNAPSHOT
# ============================================================

def save_document_first(
    document: dict[str, Any],
) -> tuple[
    bool,
    str,
    Path | None,
]:

    job_id = normalize_job_id(
        document.get(
            "job_id"
        )
    )

    version = normalize_version(
        document.get(
            "version_id"
        )
            or document.get(
                "document_version"
            )
    )

    if not job_id:
        return (
            False,
            "Job ID is missing.",
            None,
        )

    if not version:
        return (
            False,
            "Document version is missing.",
            None,
        )

    # --------------------------------------------------------
    # If this exact job/version already exists and the file is
    # present, reuse it. NEVER regenerate it.
    # --------------------------------------------------------

    existing = get_document_delivery(
        job_id,
        version,
    )

    if existing:

        existing_path = (
            delivery_document_path(
                existing
            )
        )

        if (
            existing_path
            and existing_path.is_file()
            and existing_path.stat().st_size > 0
        ):

            return (
                True,
                str(existing_path),
                existing_path,
            )

    pages = normalize_pages(
        document.get(
            "pages"
        )
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
            None,
        )

    filename = (
        clean(
            document.get(
                "filename"
            )
        )
        or clean(
            document.get(
                "document_filename"
            )
        )
        or f"naija_pocket_{job_id}.docx"
    )

    filename = Path(
        filename
    ).name

    if not filename.lower().endswith(
        ".docx"
    ):
        filename += ".docx"

    # --------------------------------------------------------
    # The document is stored by JOB + VERSION.
    #
    # It is NOT dependent on a payment ID.
    # --------------------------------------------------------

    folder = (
        DOWNLOAD_DIR
        / "documents"
        / clean(job_id)
        / clean(version)
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
                "The exact reviewed document was not saved correctly.",
                None,
            )

        delivery = upsert_document_delivery(
            document,
            output,
        )

        if not delivery_document_exists(
            delivery
        ):

            return (
                False,
                "The document was created but could not be confirmed.",
                None,
            )

        return (
            True,
            str(output),
            output,
        )

    except Exception as exc:

        # Only remove a file created during THIS operation.
        try:

            if output.exists():
                output.unlink()

        except Exception:
            pass

        return (
            False,
            str(exc),
            None,
        )


# ============================================================
# CONNECT PAYMENT RECORD TO DOCUMENT
# ============================================================

def connect_payment_to_document(
    payment: dict[str, Any],
    delivery: dict[str, Any],
) -> dict[str, Any] | None:

    job_id = normalize_job_id(
        delivery.get(
            "job_id"
        )
    )

    version = normalize_version(
        delivery.get(
            "version_id"
        )
    )

    if not job_id or not version:
        return delivery

    return update_document_delivery(
        job_id,
        version,
        payment_id=payment.get(
            "payment_id"
        ),
    )


# ============================================================
# LEGACY PAYMENT SNAPSHOT COMPATIBILITY
# ============================================================

def save_exact_document_snapshot(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:

    job_id = normalize_job_id(
        payment.get(
            "job_id"
        )
    )

    version = normalize_version(
        payment.get(
            "document_version"
        )
    )

    if not job_id or not version:

        return (
            False,
            "Payment does not contain a usable job/version.",
        )

    delivery = get_document_delivery(
        job_id,
        version,
    )

    if delivery and delivery_document_exists(
        delivery
    ):

        path = delivery_document_path(
            delivery
        )

        return (
            True,
            str(path),
        )

    ok, message, output = (
        save_document_first(
            document
        )
    )

    if not ok or not output:
        return (
            False,
            message,
        )

    updated = upsert_document_delivery(
        document,
        output,
        payment_id=payment.get(
            "payment_id"
        ),
    )

    # --------------------------------------------------------
    # Preserve compatibility with existing payment_orders.
    # --------------------------------------------------------

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
                updated_at = ?
            WHERE payment_id
                COLLATE NOCASE = ?
            """,
            (
                str(output),
                updated.get(
                    "document_saved_at"
                )
                or now_iso(),
                Path(
                    output
                ).name,
                now_iso(),
                payment_id,
            ),
        )

        conn.commit()

    return (
        True,
        str(output),
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
            url += "?" + "&".join(
                parts
            )

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
                    or (
                        f"naija_pocket_"
                        f"{job_id}.docx"
                    )
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
# FIND DOCUMENT FROM REQUEST
#
# IMPORTANT:
#
# This resolver does NOT require payment lookup.
# ============================================================

def resolve_document_delivery(
    *,
    payment_id: str = "",
    job_id: str = "",
    version_id: str = "",
) -> dict[str, Any] | None:

    payment_id = normalize_payment_id(
        payment_id
    )

    job_id = normalize_job_id(
        job_id
    )

    version_id = normalize_version(
        version_id
    )

    delivery = None

    if payment_id:

        delivery = (
            get_document_by_payment_id(
                payment_id
            )
        )

        if delivery:
            return delivery

    if job_id and version_id:

        delivery = get_document_delivery(
            job_id,
            version_id,
        )

        if delivery:
            return delivery

    if job_id:

        delivery = get_document_delivery(
            job_id
        )

        if delivery:
            return delivery

    return None


def resolve_payment_for_delivery(
    delivery: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not delivery:
        return None

    payment_id = normalize_payment_id(
        delivery.get(
            "payment_id"
        )
    )

    if payment_id:

        payment = get_payment(
            payment_id
        )

        if payment:
            return payment

    job_id = normalize_job_id(
        delivery.get(
            "job_id"
        )
    )

    version = normalize_version(
        delivery.get(
            "version_id"
        )
    )

    if job_id and version:

        payment = (
            get_payment_for_job_version(
                job_id,
                version,
            )
        )

        if payment:
            return payment

    if job_id:

        return get_latest_payment_for_job(
            job_id
        )

    return None


# ============================================================
# PUBLIC DOCUMENT REPRESENTATION
# ============================================================

def document_public(
    delivery: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not delivery:
        return None

    exists = delivery_document_exists(
        delivery
    )

    verified = delivery_is_verified(
        delivery
    )

    unlocked = delivery_is_unlocked(
        delivery
    )

    if unlocked:

        status_label = (
            "Payment Verified — Download Unlocked"
        )

    elif verified:

        status_label = (
            "Payment Verified — Ready for Delivery"
        )

    else:

        status_label = (
            "Awaiting Payment Verification"
        )

    return {
        "job_id": delivery.get(
            "job_id"
        ),

        "job_number": delivery.get(
            "job_id"
        ),

        "work_id": delivery.get(
            "job_id"
        ),

        "document_id": delivery.get(
            "job_id"
        ),

        "version_id": delivery.get(
            "version_id"
        ),

        "versionId": delivery.get(
            "version_id"
        ),

        "document_version": delivery.get(
            "version_id"
        ),

        "customer_id": delivery.get(
            "customer_id"
        ),

        "customer_email": delivery.get(
            "customer_email"
        ),

        "customer_phone": delivery.get(
            "customer_phone"
        ),

        "service": delivery.get(
            "service"
        ),

        "amount": money(
            delivery.get(
                "amount"
            )
        ),

        "currency": delivery.get(
            "currency",
            DEFAULT_CURRENCY,
        ),

        "payment_id": delivery.get(
            "payment_id"
        ),

        "document_filename": delivery.get(
            "document_filename"
        ),

        "document_saved": exists,

        "document_snapshot_saved": exists,

        "document_saved_at": delivery.get(
            "document_saved_at"
        ),

        "document_saved_size": (
            delivery_document_size(
                delivery
            )
        ),

        "verification_status": delivery.get(
            "verification_status"
        ),

        "release_status": delivery.get(
            "release_status"
        ),

        "payment_verified": verified,

        "download_unlocked": unlocked,

        "download_locked": not unlocked,

        "verified_at": delivery.get(
            "verified_at"
        ),

        "unlocked_at": delivery.get(
            "unlocked_at"
        ),

        "download_count": delivery.get(
            "download_count",
            0,
        ),

        "last_delivery_channel": delivery.get(
            "last_delivery_channel"
        ),

        "last_delivery_at": delivery.get(
            "last_delivery_at"
        ),

        "email_sent_at": delivery.get(
            "email_sent_at"
        ),

        "whatsapp_sent_at": delivery.get(
            "whatsapp_sent_at"
        ),

        "status_label": status_label,
    }


# ============================================================
# PUBLIC PAYMENT REPRESENTATION
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

    delivery = get_document_delivery(
        normalize_job_id(
            payment.get(
                "job_id"
            )
        ),
        normalize_version(
            payment.get(
                "document_version"
            )
        ),
    )

    document_saved = (
        delivery_document_exists(
            delivery
        )
        if delivery
        else saved_document_exists(
            payment
        )
    )

    verified = (
        delivery_is_verified(
            delivery
        )
        if delivery
        else payment_is_verified(
            status
        )
    )

    unlocked = (
        delivery_is_unlocked(
            delivery
        )
        if delivery
        else payment_is_verified(
            status
        )
    )

    if payment_is_reported(
        status
    ):

        back_office_status = (
            "Payment Reported — Awaiting Verification"
        )

    elif verified and unlocked:

        back_office_status = (
            "Payment Verified — Download Unlocked"
        )

    elif verified:

        back_office_status = (
            "Payment Verified — Ready for Delivery"
        )

    else:

        back_office_status = clean(
            status
        )

    return {
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

        "service": payment.get(
            "service"
        ),

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

        "document_version": payment.get(
            "document_version"
        ),

        "version_id": payment.get(
            "document_version"
        ),

        "versionId": payment.get(
            "document_version"
        ),

        "document_filename": payment.get(
            "document_filename"
        ),

        "document_saved": document_saved,

        "document_snapshot_saved": document_saved,

        "document_saved_at": (
            delivery.get(
                "document_saved_at"
            )
            if delivery
            else payment.get(
                "document_saved_at"
            )
        ),

        "document_saved_size": (
            delivery_document_size(
                delivery
            )
            if delivery
            else saved_document_size(
                payment
            )
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

        "verified_at": (
            delivery.get(
                "verified_at"
            )
            if delivery
            else payment.get(
                "verified_at"
            )
        ),

        "downloaded_at": payment.get(
            "downloaded_at"
        ),

        "download_count": payment.get(
            "download_count",
            0,
        ),

        "reported": payment_is_reported(
            status
        ),

        "paid": verified,

        "payment_verified": verified,

        "download_unlocked": unlocked,

        "download_locked": not unlocked,

        "back_office_status": (
            back_office_status
        ),
    }


# ============================================================
# BACK OFFICE AUTH
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
            "Payment + Customer Care API"
        ),
        "version": APP_VERSION,
        "business_model": (
            "finished_document_first"
        ),
        "old_api_configured": (
            old_api_configured()
        ),
        "back_office_configured": (
            configured_admin_key()
        ),
        "email_delivery_configured": (
            bool(
                SMTP_HOST
                and SMTP_FROM
            )
        ),
        "whatsapp_delivery_configured": (
            bool(
                WHATSAPP_ACCESS_TOKEN
                and WHATSAPP_PHONE_NUMBER_ID
            )
        ),
        "database": str(DB_PATH),
        "download_dir": str(
            DOWNLOAD_DIR
        ),
        "database_info": database_file_info(),
        "payment_record_count": (
            payment_record_count()
        ),
        "document_record_count": (
            document_record_count()
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
            "finished_document_first"
        ),
        "old_api_configured": (
            old_api_configured()
        ),
        "back_office_configured": (
            configured_admin_key()
        ),
        "email_delivery_configured": (
            bool(
                SMTP_HOST
                and SMTP_FROM
            )
        ),
        "whatsapp_delivery_configured": (
            bool(
                WHATSAPP_ACCESS_TOKEN
                and WHATSAPP_PHONE_NUMBER_ID
            )
        ),
        "database": str(DB_PATH),
        "download_dir": str(
            DOWNLOAD_DIR
        ),
        "database_info": database_file_info(),
        "payment_record_count": (
            payment_record_count()
        ),
        "document_record_count": (
            document_record_count()
        ),
        "process_id": os.getpid(),
    }


# ============================================================
# CUSTOMER PAYMENT CREATE
#
# IMPORTANT CHANGE:
#
# DOCUMENT IS SAVED FIRST.
#
# Payment record creation happens after the document has its
# own job/version delivery record.
#
# Therefore a payment persistence problem no longer means that
# the finished document disappears.
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: Request,
    job_id: str | None = None,
    customer_id: str | None = None,
    service: str | None = None,
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

    service = clean(
        service
        or body.get(
            "service"
        )
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

    if not job_id:

        return json_response_error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
            400,
        )

    requested_version = (
        body_version_id(body)
    )

    supplied_pages = normalize_pages(
        body.get(
            "pages"
        )
        if body.get(
            "pages"
        ) is not None
        else body.get(
            "document_pages"
        )
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

    customer_email = (
        body_customer_email(
            body
        )
    )

    customer_phone = (
        body_customer_phone(
            body
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
    # EXACT REVIEW SNAPSHOT FROM PAYMENT HTML
    # --------------------------------------------------------

    if exact_snapshot_supplied:

        doc = normalize_document(
            {
                "pages": supplied_pages,
                "document_text": supplied_text,
                "version_id": requested_version,
                "filename": (
                    supplied_filename
                    or (
                        f"naija_pocket_"
                        f"{job_id}.docx"
                    )
                ),
                "service": service,
                "customer_id": customer_id,
                "amount": supplied_amount,
            },
            job_id,
            version_id=requested_version,
            filename=(
                supplied_filename
                or (
                    f"naija_pocket_"
                    f"{job_id}.docx"
                )
            ),
            service=service,
            customer_id=customer_id,
            amount=supplied_amount,
        )

    else:

        # ----------------------------------------------------
        # OLD API FALLBACK
        # ----------------------------------------------------

        current_error = None

        try:

            doc = fetch_current_document(
                job_id
            )

        except Exception as exc:

            current_error = str(
                exc
            )
            doc = None

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
                job_id=job_id,
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
            job_id=job_id,
        )

    version = normalize_version(
        doc.get(
            "version_id"
        )
    )

    if not version:

        return json_response_error(
            "VERSION_ID_REQUIRED",
            (
                "A document version is required "
                "before the finished document can "
                "be saved."
            ),
            409,
            job_id=job_id,
        )

    final_amount = (
        money(
            doc.get(
                "amount"
            )
        )
        or supplied_amount
    )

    final_filename = (
        clean(
            doc.get(
                "filename"
            )
        )
        or (
            f"naija_pocket_"
            f"{job_id}.docx"
        )
    )

    # --------------------------------------------------------
    # STEP 1
    #
    # SAVE THE PRODUCT FIRST.
    # --------------------------------------------------------

    existing_delivery = (
        get_document_delivery(
            job_id,
            version,
        )
    )

    if (
        existing_delivery
        and delivery_document_exists(
            existing_delivery
        )
    ):

        delivery = existing_delivery

        output = delivery_document_path(
            delivery
        )

    else:

        ok, message, output = (
            save_document_first(
                doc
            )
        )

        if not ok or not output:

            return json_response_error(
                "DOCUMENT_SNAPSHOT_FAILED",
                (
                    "The exact reviewed document "
                    "could not be saved."
                ),
                500,
                detail=message,
                job_id=job_id,
                version_id=version,
            )

        delivery = get_document_delivery(
            job_id,
            version,
        )

    if not delivery:

        return json_response_error(
            "DOCUMENT_RECORD_MISSING",
            (
                "The finished document was saved "
                "but its delivery record could not "
                "be confirmed."
            ),
            500,
            job_id=job_id,
            version_id=version,
        )

    # Save customer delivery information.
    delivery = (
        update_document_delivery(
            job_id,
            version,
            customer_email=(
                customer_email
                if customer_email
                else None
            ),
            customer_phone=(
                customer_phone
                if customer_phone
                else None
            ),
        )
        or delivery
    )

    # --------------------------------------------------------
    # STEP 2
    #
    # LOOK FOR EXISTING PAYMENT FOR SAME JOB/VERSION.
    # --------------------------------------------------------

    latest = (
        get_payment_for_job_version(
            job_id,
            version,
        )
    )

    if latest:

        # Connect existing payment to document.
        delivery = (
            update_document_delivery(
                job_id,
                version,
                payment_id=latest.get(
                    "payment_id"
                ),
            )
            or delivery
        )

        # Keep payment record path synchronized.
        if output:

            with connect_db() as conn:

                conn.execute(
                    """
                    UPDATE payment_orders
                    SET
                        document_saved_path = ?,
                        document_saved_at = ?,
                        document_filename = ?,
                        updated_at = ?
                    WHERE payment_id
                        COLLATE NOCASE = ?
                    """,
                    (
                        str(output),
                        delivery.get(
                            "document_saved_at"
                        )
                        or now_iso(),
                        Path(
                            output
                        ).name,
                        now_iso(),
                        latest.get(
                            "payment_id"
                        ),
                    ),
                )

                conn.commit()

        latest = (
            get_payment(
                latest.get(
                    "payment_id"
                )
            )
            or latest
        )

        return {
            "ok": True,
            "message": (
                "The exact finished document is "
                "already saved and the existing "
                "payment record was reused."
            ),
            "payment": payment_public(
                latest
            ),
            "payment_id": latest.get(
                "payment_id"
            ),
            "paymentId": latest.get(
                "payment_id"
            ),
            "amount": money(
                latest.get(
                    "amount"
                )
            ),
            "currency": latest.get(
                "currency",
                DEFAULT_CURRENCY,
            ),
            "payment_status": latest.get(
                "payment_status"
            ),
            "paid": (
                payment_is_verified(
                    latest.get(
                        "payment_status"
                    )
                )
            ),
            "payment_verified": (
                payment_is_verified(
                    latest.get(
                        "payment_status"
                    )
                )
            ),
            "download_unlocked": (
                delivery_is_unlocked(
                    delivery
                )
            ),
            "document_version": version,
            "version_id": version,
            "document_saved": True,
            "document_snapshot_saved": True,
            "document_delivery": document_public(
                delivery
            ),
        }

    # --------------------------------------------------------
    # STEP 3
    #
    # CREATE PAYMENT RECORD.
    #
    # If this fails, the document record remains.
    # --------------------------------------------------------

    payment_id = (
        "NPB-"
        + uuid.uuid4().hex[:12].upper()
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
        or clean(
            doc.get(
                "service"
            )
        )
    )

    payload = snapshot_payload(
        doc
    )

    payload.update(
        {
            "job_id": job_id,
            "version_id": version,
            "document_version": version,
            "customer_id": final_customer_id,
            "service": final_service,
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
            amount=final_amount,
            currency=DEFAULT_CURRENCY,
            payment_method=method,
            document_version=version,
            document_filename=final_filename,
            document_payload=payload,
        )

    except Exception as exc:

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Do NOT delete the finished document.
        #
        # The product has already been saved.
        # ----------------------------------------------------

        return json_response_error(
            "PAYMENT_RECORD_UNAVAILABLE",
            (
                "The finished document has been saved "
                "successfully, but the payment record "
                "could not be created."
            ),
            500,
            detail=str(exc),
            job_id=job_id,
            version_id=version,
            document_saved=True,
            document_ready_for_customer_care=True,
        )

    # --------------------------------------------------------
    # CONNECT PAYMENT → DOCUMENT
    # --------------------------------------------------------

    delivery = (
        update_document_delivery(
            job_id,
            version,
            payment_id=payment_id,
        )
        or delivery
    )

    # --------------------------------------------------------
    # COMPATIBILITY PATH IN payment_orders
    # --------------------------------------------------------

    if output:

        with connect_db() as conn:

            conn.execute(
                """
                UPDATE payment_orders
                SET
                    document_saved_path = ?,
                    document_saved_at = ?,
                    document_filename = ?,
                    updated_at = ?
                WHERE payment_id
                    COLLATE NOCASE = ?
                """,
                (
                    str(output),
                    delivery.get(
                        "document_saved_at"
                    )
                    or now_iso(),
                    Path(
                        output
                    ).name,
                    now_iso(),
                    payment_id,
                ),
            )

            conn.commit()

    record = (
        get_payment(
            payment_id
        )
        or record
    )

    return {
        "ok": True,
        "message": (
            "Payment record created and the exact "
            "finished document is safely saved."
        ),
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
        "document_saved_at": delivery.get(
            "document_saved_at"
        ),
        "document_delivery": document_public(
            delivery
        ),
    }


# ============================================================
# CUSTOMER PAYMENT REPORT
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
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

    version_id = normalize_version(
        body_version_id(body)
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

    # --------------------------------------------------------
    # FIRST: locate the DOCUMENT.
    #
    # This is intentionally independent of payment lookup.
    # --------------------------------------------------------

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if delivery:

        job_id = normalize_job_id(
            delivery.get(
                "job_id"
            )
        )

        version_id = normalize_version(
            delivery.get(
                "version_id"
            )
        )

    # --------------------------------------------------------
    # SECOND: locate payment.
    # --------------------------------------------------------

    payment = (
        get_payment(
            payment_id
        )
        if payment_id
        else None
    )

    if not payment and delivery:

        payment = resolve_payment_for_delivery(
            delivery
        )

    if not payment and job_id:

        payment = get_latest_payment_for_job(
            job_id
        )

    # --------------------------------------------------------
    # If payment is missing but the finished document exists,
    # Customer Care can still see the saved product.
    #
    # We do NOT pretend payment has been reported.
    # --------------------------------------------------------

    if not payment:

        if delivery:

            return {
                "ok": True,
                "message": (
                    "The finished document is saved. "
                    "No payment record was found yet. "
                    "Customer Care can review the saved "
                    "document record and verify payment "
                    "through its normal business process."
                ),
                "payment": None,
                "payment_id": None,
                "job_id": delivery.get(
                    "job_id"
                ),
                "document_version": delivery.get(
                    "version_id"
                ),
                "document_saved": True,
                "payment_verified": (
                    delivery_is_verified(
                        delivery
                    )
                ),
                "download_unlocked": (
                    delivery_is_unlocked(
                        delivery
                    )
                ),
                "document_delivery": document_public(
                    delivery
                ),
                "payment_not_found": True,
            }

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            (
                "No finished document was found "
                "for this request."
            ),
            404,
            requested_payment_id=payment_id or None,
            requested_job_id=job_id or None,
            requested_version_id=version_id or None,
        )

    # --------------------------------------------------------
    # Make sure the payment is linked to the saved document.
    # --------------------------------------------------------

    if not delivery:

        delivery = resolve_document_delivery(
            payment_id=payment.get(
                "payment_id"
            ),
            job_id=payment.get(
                "job_id"
            ),
            version_id=payment.get(
                "document_version"
            ),
        )

    if not delivery:

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The payment exists, but its exact "
                "finished document could not be found."
            ),
            409,
            payment_id=payment.get(
                "payment_id"
            ),
            job_id=payment.get(
                "job_id"
            ),
            version_id=payment.get(
                "document_version"
            ),
        )

    if not delivery_document_exists(
        delivery
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact finished document file "
                "could not be found."
            ),
            409,
            payment_id=payment.get(
                "payment_id"
            ),
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
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

        delivery = (
            update_document_delivery(
                delivery.get(
                    "job_id"
                ),
                delivery.get(
                    "version_id"
                ),
                verification_status="verified",
                release_status="unlocked",
                verified_at=(
                    delivery.get(
                        "verified_at"
                    )
                    or now_iso()
                ),
                unlocked_at=(
                    delivery.get(
                        "unlocked_at"
                    )
                    or now_iso()
                ),
                payment_id=payment.get(
                    "payment_id"
                ),
            )
            or delivery
        )

        return {
            "ok": True,
            "message": (
                "Payment has already been verified."
            ),
            "payment": payment_public(
                payment
            ),
            "payment_id": payment.get(
                "payment_id"
            ),
            "paid": True,
            "payment_verified": True,
            "download_unlocked": True,
            "document_saved": True,
            "document_delivery": document_public(
                delivery
            ),
        }

    updated = update_payment_record(
        payment.get(
            "payment_id"
        ),
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
            (
                "Payment report could not be saved."
            ),
            500,
            payment_id=payment.get(
                "payment_id"
            ),
        )

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            payment_id=updated.get(
                "payment_id"
            ),
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "Payment report received. "
            "Customer Care must verify the "
            "payment before delivery is released."
        ),
        "payment": payment_public(
            updated
        ),
        "payment_id": updated.get(
            "payment_id"
        ),
        "paymentId": updated.get(
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
        "document_delivery": document_public(
            delivery
        ),
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    payment_id: str | None = None,
    job_id: str | None = None,
    paymentId: str | None = None,
):

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    job_id = normalize_job_id(
        job_id
    )

    payment = (
        get_payment(
            payment_id
        )
        if payment_id
        else None
    )

    if not payment and job_id:

        payment = get_latest_payment_for_job(
            job_id
        )

    if not payment:

        delivery = (
            get_document_delivery(
                job_id
            )
            if job_id
            else None
        )

        if delivery:

            return {
                "ok": True,
                "payment": None,
                "payment_id": None,
                "payment_status": "none",
                "paid": delivery_is_verified(
                    delivery
                ),
                "payment_verified": (
                    delivery_is_verified(
                        delivery
                    )
                ),
                "download_unlocked": (
                    delivery_is_unlocked(
                        delivery
                    )
                ),
                "document_saved": (
                    delivery_document_exists(
                        delivery
                    )
                ),
                "document_delivery": document_public(
                    delivery
                ),
            }

        return {
            "ok": True,
            "payment": None,
            "payment_id": payment_id or None,
            "payment_status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
            "document_saved": False,
        }

    delivery = resolve_document_delivery(
        payment_id=payment.get(
            "payment_id"
        ),
        job_id=payment.get(
            "job_id"
        ),
        version_id=payment.get(
            "document_version"
        ),
    )

    verified = (
        delivery_is_verified(
            delivery
        )
        if delivery
        else payment_is_verified(
            payment.get(
                "payment_status"
            )
        )
    )

    unlocked = (
        delivery_is_unlocked(
            delivery
        )
        if delivery
        else verified
    )

    return {
        "ok": True,
        "payment": payment_public(
            payment
        ),
        "payment_id": payment.get(
            "payment_id"
        ),
        "payment_status": payment.get(
            "payment_status"
        ),
        "paid": verified,
        "payment_verified": verified,
        "download_unlocked": unlocked,
        "document_saved": (
            delivery_document_exists(
                delivery
            )
            if delivery
            else saved_document_exists(
                payment
            )
        ),
        "document_check": (
            "current_snapshot"
            if delivery
            and delivery_document_exists(
                delivery
            )
            else "missing_snapshot"
        ),
        "document_delivery": document_public(
            delivery
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
    payment_reference: str | None = None,
    note: str | None = None,
):

    return await payment_report(
        request,
        payment_id=payment_id,
        job_id=job_id,
        payment_reference=payment_reference,
        note=note,
    )


# ============================================================
# CUSTOMER CARE DOCUMENT LIST
#
# THIS IS NOW DOCUMENT-FIRST.
#
# Customer Care does not need a successful payment lookup just
# to see the finished document.
# ============================================================

@app.get(
    "/api/customer-care/documents"
)
async def customer_care_documents():

    records = list_document_deliveries()

    return {
        "ok": True,
        "count": len(records),
        "documents": [
            document_public(
                record
            )
            for record in records
        ],
    }


# ============================================================
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get(
    "/api/customer-care/payments"
)
async def customer_care_payments():

    records = list_pending_payments()

    return {
        "ok": True,
        "count": len(records),
        "payments": [
            payment_public(
                record
            )
            for record in records
        ],
    }


# ============================================================
# CUSTOMER CARE VERIFY PAYMENT
#
# Payment verification is now copied onto the document
# delivery record.
# ============================================================

@app.post(
    "/api/customer-care/payment/verify"
)
async def customer_care_verify(
    request: Request,
    payment_id: str | None = None,
    verified: bool = True,
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

    job_id = body_job_id(
        body
    )

    version_id = body_version_id(
        body
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

    # --------------------------------------------------------
    # DOCUMENT FIRST
    # --------------------------------------------------------

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    # --------------------------------------------------------
    # PAYMENT IS OPTIONAL FOR LOCATING THE SAVED DOCUMENT.
    # --------------------------------------------------------

    payment = (
        get_payment(
            payment_id
        )
        if payment_id
        else None
    )

    if not payment and delivery:

        payment = resolve_payment_for_delivery(
            delivery
        )

    if not payment and delivery:

        if not verified:

            delivery = (
                update_document_delivery(
                    delivery.get(
                        "job_id"
                    ),
                    delivery.get(
                        "version_id"
                    ),
                    verification_status="rejected",
                    verification_note=note,
                )
                or delivery
            )

            return {
                "ok": True,
                "message": (
                    "Payment marked as rejected. "
                    "Document remains locked."
                ),
                "payment": None,
                "document_delivery": document_public(
                    delivery
                ),
                "payment_verified": False,
                "download_unlocked": False,
            }

        # ----------------------------------------------------
        # No payment record, but the saved document exists.
        #
        # Customer Care can record its business verification
        # against the document itself.
        # ----------------------------------------------------

        if not delivery_document_exists(
            delivery
        ):

            return json_response_error(
                "DOCUMENT_SNAPSHOT_MISSING",
                (
                    "The finished document is not "
                    "available for delivery."
                ),
                409,
            )

        timestamp = now_iso()

        delivery = (
            update_document_delivery(
                delivery.get(
                    "job_id"
                ),
                delivery.get(
                    "version_id"
                ),
                verification_status="verified",
                release_status="unlocked",
                verification_note=note,
                verified_at=timestamp,
                unlocked_at=timestamp,
            )
            or delivery
        )

        return {
            "ok": True,
            "message": (
                "Payment verified by Customer Care. "
                "The saved document is now unlocked."
            ),
            "payment": None,
            "payment_id": None,
            "job_id": delivery.get(
                "job_id"
            ),
            "document_version": delivery.get(
                "version_id"
            ),
            "document_saved": True,
            "payment_verified": True,
            "download_unlocked": True,
            "document_delivery": document_public(
                delivery
            ),
        }

    if not payment:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            (
                "No saved finished document or "
                "payment record was found."
            ),
            404,
        )

    if not delivery:

        delivery = resolve_document_delivery(
            payment_id=payment.get(
                "payment_id"
            ),
            job_id=payment.get(
                "job_id"
            ),
            version_id=payment.get(
                "document_version"
            ),
        )

    if not delivery:

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The payment exists but the exact "
                "finished document cannot be found."
            ),
            409,
            payment_id=payment.get(
                "payment_id"
            ),
        )

    if not verified:

        updated = update_payment_record(
            payment.get(
                "payment_id"
            ),
            status="rejected",
            admin_note=(
                note
                or None
            ),
        )

        delivery = (
            update_document_delivery(
                delivery.get(
                    "job_id"
                ),
                delivery.get(
                    "version_id"
                ),
                verification_status="rejected",
                release_status="locked",
                verification_note=note,
            )
            or delivery
        )

        return {
            "ok": True,
            "message": (
                "Payment marked as rejected. "
                "Document remains locked."
            ),
            "payment": payment_public(
                updated
            ),
            "document_delivery": document_public(
                delivery
            ),
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    if not delivery_document_exists(
        delivery
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "Customer Care cannot unlock "
                "this document because the exact "
                "finished document is missing."
            ),
            409,
            payment_id=payment.get(
                "payment_id"
            ),
        )

    updated = update_payment_record(
        payment.get(
            "payment_id"
        ),
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
            (
                "Payment could not be updated, "
                "but the saved document remains "
                "available to Customer Care."
            ),
            500,
            payment_id=payment.get(
                "payment_id"
            ),
            document_saved=True,
        )

    timestamp = now_iso()

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            payment_id=updated.get(
                "payment_id"
            ),
            verification_status="verified",
            release_status="unlocked",
            verification_note=note,
            verified_at=(
                delivery.get(
                    "verified_at"
                )
                or timestamp
            ),
            unlocked_at=(
                delivery.get(
                    "unlocked_at"
                )
                or timestamp
            ),
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "Payment verified. "
            "The exact saved document is "
            "now unlocked for download and "
            "Customer Care delivery."
        ),
        "payment": payment_public(
            updated
        ),
        "paid": True,
        "payment_verified": True,
        "download_unlocked": True,
        "document_saved": True,
        "document_delivery": document_public(
            delivery
        ),
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
# BACK OFFICE DOCUMENT LIST
#
# THIS IS THE PRIMARY CUSTOMER CARE WORK QUEUE.
# ============================================================

@app.get(
    "/api/back-office/documents"
)
async def back_office_documents(
    request: Request,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    records = list_document_deliveries()

    return {
        "ok": True,
        "count": len(records),
        "documents": [
            document_public(
                record
            )
            for record in records
        ],
    }


# ============================================================
# BACK OFFICE PAYMENTS
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
# DOCUMENTS FIRST.
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

    documents = list_document_deliveries()

    jobs: list[dict[str, Any]] = []

    for delivery in documents:

        public = document_public(
            delivery
        )

        if not public:
            continue

        payment = resolve_payment_for_delivery(
            delivery
        )

        if payment:

            public[
                "payment_status"
            ] = payment.get(
                "payment_status"
            )

            public[
                "payment_reference"
            ] = payment.get(
                "payment_reference"
            )

            public[
                "payment"
            ] = payment_public(
                payment
            )

            if payment_is_reported(
                payment.get(
                    "payment_status"
                )
            ) and not delivery_is_verified(
                delivery
            ):

                public[
                    "status_label"
                ] = (
                    "Payment Reported — Awaiting Verification"
                )

        if delivery_is_unlocked(
            delivery
        ):

            public[
                "status_label"
            ] = (
                "Payment Verified — Download Unlocked"
            )

        elif delivery_is_verified(
            delivery
        ):

            public[
                "status_label"
            ] = (
                "Payment Verified — Ready for Delivery"
            )

        jobs.append(
            public
        )

    return {
        "ok": True,
        "count": len(jobs),
        "jobs": jobs,
        "payments": jobs,
    }


# ============================================================
# BACK OFFICE SINGLE PAYMENT
# ============================================================

@app.get(
    "/api/back-office/payment"
)
async def back_office_payment(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    paymentId: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    payment = None

    if payment_id:

        payment = get_payment(
            payment_id
        )

    if not payment and clean(
        job_id
    ):

        payment = get_latest_payment_for_job(
            job_id
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    return {
        "ok": True,
        "payment": payment_public(
            payment
        ),
    }


# ============================================================
# BACK OFFICE DOCUMENT INFO
#
# DOES NOT REQUIRE PAYMENT LOOKUP.
# ============================================================

@app.get(
    "/api/back-office/document-info"
)
async def back_office_document_info(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    paymentId: str | None = None,
    version_id: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id or "",
        version_id=version_id or "",
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document record not found.",
            404,
            payment_id=payment_id or None,
            job_id=job_id or None,
            version_id=version_id or None,
        )

    exists = delivery_document_exists(
        delivery
    )

    path = delivery_document_path(
        delivery
    )

    payment = resolve_payment_for_delivery(
        delivery
    )

    return {
        "ok": True,
        "payment_id": delivery.get(
            "payment_id"
        ),
        "job_id": delivery.get(
            "job_id"
        ),
        "document_version": delivery.get(
            "version_id"
        ),
        "document_filename": delivery.get(
            "document_filename"
        ),
        "document_saved": exists,
        "document_snapshot_saved": exists,
        "document_saved_at": delivery.get(
            "document_saved_at"
        ),
        "document_size": delivery_document_size(
            delivery
        ),
        "document_path": (
            str(path)
            if exists and path
            else None
        ),
        "payment_status": (
            payment.get(
                "payment_status"
            )
            if payment
            else None
        ),
        "payment_verified": delivery_is_verified(
            delivery
        ),
        "download_unlocked": delivery_is_unlocked(
            delivery
        ),
        "document_delivery": document_public(
            delivery
        ),
    }


# ============================================================
# BACK OFFICE SAVED DOCUMENT
#
# SERVES EXACT SAVED SNAPSHOT ONLY.
# ============================================================

@app.get(
    "/api/back-office/document"
)
async def back_office_document(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    paymentId: str | None = None,
    version_id: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id or "",
        version_id=version_id or "",
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document record not found.",
            404,
        )

    output = delivery_document_path(
        delivery
    )

    if (
        not output
        or not output.is_file()
        or output.stat().st_size <= 0
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact saved document "
                "snapshot is missing."
            ),
            409,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
        )

    filename = (
        clean(
            delivery.get(
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
            "X-Payment-ID": clean(
                delivery.get(
                    "payment_id"
                )
            ),
            "X-Document-Version": clean(
                delivery.get(
                    "version_id"
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

    job_id = body_job_id(
        body
    )

    version_id = body_version_id(
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

    # --------------------------------------------------------
    # DOCUMENT FIRST
    # --------------------------------------------------------

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            (
                "No saved finished document was "
                "found for this job/version."
            ),
            404,
            payment_id=payment_id or None,
            job_id=job_id or None,
            version_id=version_id or None,
        )

    if not delivery_document_exists(
        delivery
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact saved document "
                "snapshot is missing."
            ),
            409,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
        )

    payment = resolve_payment_for_delivery(
        delivery
    )

    # --------------------------------------------------------
    # If a payment exists, update it.
    # If it does not, verification can still be recorded
    # against the finished document.
    # --------------------------------------------------------

    if payment:

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
                    "This payment is marked as "
                    "rejected or cancelled."
                ),
                409,
                payment_id=payment.get(
                    "payment_id"
                ),
            )

        updated_payment = (
            update_payment_record(
                payment.get(
                    "payment_id"
                ),
                status="verified",
                admin_note=(
                    note
                    or None
                ),
                verified_at=now_iso(),
            )
        )

    else:

        updated_payment = None

    timestamp = now_iso()

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            payment_id=(
                payment.get(
                    "payment_id"
                )
                if payment
                else None
            ),
            verification_status="verified",
            release_status="unlocked",
            verification_note=note,
            verified_at=(
                delivery.get(
                    "verified_at"
                )
                or timestamp
            ),
            unlocked_at=(
                delivery.get(
                    "unlocked_at"
                )
                or timestamp
            ),
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "Payment verified. "
            "The exact saved document is "
            "now unlocked for download and "
            "Customer Care delivery."
        ),
        "payment": (
            payment_public(
                updated_payment
            )
            if updated_payment
            else None
        ),
        "payment_id": (
            delivery.get(
                "payment_id"
            )
        ),
        "job_id": delivery.get(
            "job_id"
        ),
        "document_version": delivery.get(
            "version_id"
        ),
        "document_saved": True,
        "payment_verified": True,
        "download_unlocked": True,
        "document_delivery": document_public(
            delivery
        ),
    }


# ============================================================
# BACK OFFICE ACTIVATE DOWNLOAD
#
# DOCUMENT-FIRST.
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

    version_id = body_version_id(
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

    # --------------------------------------------------------
    # DOCUMENT IS THE PRIMARY RECORD.
    # --------------------------------------------------------

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            (
                "No saved finished document was "
                "found for this job/version."
            ),
            404,
            requested_payment_id=payment_id or None,
            requested_job_id=job_id or None,
            requested_version_id=version_id or None,
        )

    if not delivery_document_exists(
        delivery
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "UNLOCK DOWNLOAD cannot continue "
                "because the exact saved document "
                "snapshot is missing."
            ),
            409,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
        )

    payment = resolve_payment_for_delivery(
        delivery
    )

    if payment:

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
                    "This payment is marked as "
                    "rejected or cancelled."
                ),
                409,
                payment_id=payment.get(
                    "payment_id"
                ),
            )

        payment = (
            update_payment_record(
                payment.get(
                    "payment_id"
                ),
                status="verified",
                admin_note=(
                    note
                    or None
                ),
                verified_at=now_iso(),
            )
            or payment
        )

    timestamp = now_iso()

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            payment_id=(
                payment.get(
                    "payment_id"
                )
                if payment
                else None
            ),
            verification_status="verified",
            release_status="unlocked",
            verification_note=note,
            verified_at=(
                delivery.get(
                    "verified_at"
                )
                or timestamp
            ),
            unlocked_at=timestamp,
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "The exact saved document is "
            "now unlocked. No document was regenerated."
        ),
        "payment": (
            payment_public(
                payment
            )
            if payment
            else None
        ),
        "payment_id": delivery.get(
            "payment_id"
        ),
        "job_id": delivery.get(
            "job_id"
        ),
        "work_id": delivery.get(
            "job_id"
        ),
        "document_id": delivery.get(
            "job_id"
        ),
        "document_version": delivery.get(
            "version_id"
        ),
        "document_saved": True,
        "payment_verified": True,
        "download_unlocked": True,
        "document_delivery": document_public(
            delivery
        ),
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

    job_id = body_job_id(
        body
    )

    version_id = body_version_id(
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

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document not found.",
            404,
        )

    payment = resolve_payment_for_delivery(
        delivery
    )

    if payment:

        if payment_is_verified(
            payment.get(
                "payment_status"
            )
        ):

            return json_response_error(
                "PAYMENT_ALREADY_UNLOCKED",
                (
                    "This payment is already verified "
                    "and download is unlocked."
                ),
                409,
                payment_id=payment.get(
                    "payment_id"
                ),
            )

        updated_payment = (
            update_payment_record(
                payment.get(
                    "payment_id"
                ),
                status="rejected",
                admin_note=(
                    note
                    or None
                ),
            )
        )

    else:

        updated_payment = None

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            verification_status="rejected",
            release_status="locked",
            verification_note=note,
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "Payment marked as rejected. "
            "Download remains locked."
        ),
        "payment": (
            payment_public(
                updated_payment
            )
            if updated_payment
            else None
        ),
        "document_delivery": document_public(
            delivery
        ),
        "payment_verified": False,
        "download_unlocked": False,
    }


# ============================================================
# CUSTOMER DOWNLOAD
#
# IMPORTANT:
#
# DOWNLOAD IS NOW BASED ON THE SAVED DOCUMENT RECORD.
#
# Payment lookup is attempted for compatibility, but the saved
# document record is the authoritative delivery record.
# ============================================================

@app.get(
    "/api/download"
)
async def download_document(
    payment_id: str | None = None,
    job_id: str | None = None,
    paymentId: str | None = None,
    version_id: str | None = None,
):

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    job_id = normalize_job_id(
        job_id
    )

    version_id = normalize_version(
        version_id
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            (
                "No finished document was found "
                "for this request."
            ),
            404,
            requested_payment_id=payment_id or None,
            requested_job_id=job_id or None,
            requested_version_id=version_id or None,
        )

    if not delivery_is_unlocked(
        delivery
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            (
                "Download remains locked until "
                "Customer Care verifies and "
                "releases this document."
            ),
            403,
            payment_id=delivery.get(
                "payment_id"
            ),
            job_id=delivery.get(
                "job_id"
            ),
            document_version=delivery.get(
                "version_id"
            ),
            payment_verified=delivery_is_verified(
                delivery
            ),
            download_unlocked=False,
        )

    output = delivery_document_path(
        delivery
    )

    if (
        not output
        or not output.is_file()
        or output.stat().st_size <= 0
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact saved document "
                "snapshot is missing."
            ),
            409,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
        )

    timestamp = now_iso()

    with connect_db() as conn:

        conn.execute(
            """
            UPDATE document_deliveries
            SET
                download_count =
                    download_count + 1,
                last_downloaded_at = ?,
                updated_at = ?
            WHERE job_id = ?
              AND version_id = ?
            """,
            (
                timestamp,
                timestamp,
                delivery.get(
                    "job_id"
                ),
                delivery.get(
                    "version_id"
                ),
            ),
        )

        conn.commit()

    payment = resolve_payment_for_delivery(
        delivery
    )

    if payment:

        increment_download(
            payment.get(
                "payment_id"
            )
        )

    filename = (
        clean(
            delivery.get(
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
            "X-Payment-ID": clean(
                delivery.get(
                    "payment_id"
                )
            ),
            "X-Job-ID": clean(
                delivery.get(
                    "job_id"
                )
            ),
            "X-Document-Version": clean(
                delivery.get(
                    "version_id"
                )
            ),
            "X-Download-Unlocked": "true",
            "X-Document-Snapshot": "true",
        },
    )


# ============================================================
# CUSTOMER CARE DOCUMENT FILE RESOLUTION
# ============================================================

def require_released_document(
    delivery: dict[str, Any] | None,
) -> tuple[
    bool,
    str,
    Path | None,
]:

    if not delivery:

        return (
            False,
            "Finished document not found.",
            None,
        )

    if not delivery_is_verified(
        delivery
    ):

        return (
            False,
            "Payment has not been verified.",
            None,
        )

    if not delivery_is_unlocked(
        delivery
    ):

        return (
            False,
            "Document has not been released.",
            None,
        )

    path = delivery_document_path(
        delivery
    )

    if (
        not path
        or not path.is_file()
        or path.stat().st_size <= 0
    ):

        return (
            False,
            "The exact saved document file is missing.",
            None,
        )

    return (
        True,
        "",
        path,
    )


# ============================================================
# EMAIL VALIDATION
# ============================================================

def valid_email(
    address: str,
) -> bool:

    address = clean(
        address
    )

    if not address:
        return False

    return bool(
        re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            address,
        )
    )


# ============================================================
# CUSTOMER CARE EMAIL DELIVERY
#
# Uses the EXACT saved document.
# ============================================================

def send_document_email(
    *,
    delivery: dict[str, Any],
    recipient: str,
    message_text: str = "",
) -> dict[str, Any]:

    recipient = clean(
        recipient
    )

    if not valid_email(
        recipient
    ):

        raise RuntimeError(
            "A valid customer email address is required."
        )

    if not SMTP_HOST or not SMTP_FROM:

        raise RuntimeError(
            (
                "EMAIL_DELIVERY_NOT_CONFIGURED: "
                "Set SMTP_HOST and SMTP_FROM "
                "in the payment service environment."
            )
        )

    path = delivery_document_path(
        delivery
    )

    if (
        not path
        or not path.is_file()
    ):

        raise RuntimeError(
            "The exact saved document file is missing."
        )

    filename = (
        clean(
            delivery.get(
                "document_filename"
            )
        )
        or path.name
    )

    message = EmailMessage()

    message[
        "Subject"
    ] = (
        "Naija Pocket Business Center "
        "— Your Finished Document"
    )

    message[
        "From"
    ] = SMTP_FROM

    message[
        "To"
    ] = recipient

    body = (
        message_text.strip()
        if message_text.strip()
        else (
            "Hello,\n\n"
            "Your finished document from "
            "Naija Pocket Business Center "
            "is attached.\n\n"
            "Thank you."
        )
    )

    message.set_content(
        body
    )

    with open(
        path,
        "rb",
    ) as file:

        data = file.read()

    message.add_attachment(
        data,
        maintype="application",
        subtype=(
            "vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        filename=filename,
    )

    if SMTP_USE_TLS:

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=30,
        ) as server:

            server.starttls()

            if (
                SMTP_USERNAME
                and SMTP_PASSWORD
            ):

                server.login(
                    SMTP_USERNAME,
                    SMTP_PASSWORD,
                )

            server.send_message(
                message
            )

    else:

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=30,
        ) as server:

            if (
                SMTP_USERNAME
                and SMTP_PASSWORD
            ):

                server.login(
                    SMTP_USERNAME,
                    SMTP_PASSWORD,
                )

            server.send_message(
                message
            )

    return {
        "ok": True,
        "channel": "email",
        "recipient": recipient,
        "filename": filename,
    }


# ============================================================
# CUSTOMER CARE SEND BY EMAIL
#
# Can be called using:
#
# payment_id
# OR
# job_id + version_id
# ============================================================

@app.post(
    "/api/customer-care/send/email"
)
async def customer_care_send_email(
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

    version_id = body_version_id(
        body
    )

    recipient = (
        body_customer_email(
            body
        )
    )

    message_text = clean(
        body.get(
            "message"
        )
        or body.get(
            "note"
        )
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if not recipient and delivery:

        recipient = clean(
            delivery.get(
                "customer_email"
            )
        )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document not found.",
            404,
        )

    ok, error, path = (
        require_released_document(
            delivery
        )
    )

    if not ok:

        return json_response_error(
            "DELIVERY_NOT_RELEASED",
            error,
            403,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
        )

    try:

        result = send_document_email(
            delivery=delivery,
            recipient=recipient,
            message_text=message_text,
        )

    except Exception as exc:

        return json_response_error(
            "EMAIL_DELIVERY_FAILED",
            str(exc),
            502,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
            document_saved=True,
        )

    timestamp = now_iso()

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            customer_email=recipient,
            last_delivery_channel="email",
            last_delivery_at=timestamp,
            email_sent_at=timestamp,
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "The exact saved document was "
            "sent by email."
        ),
        "channel": "email",
        "recipient": recipient,
        "job_id": delivery.get(
            "job_id"
        ),
        "document_version": delivery.get(
            "version_id"
        ),
        "document_filename": delivery.get(
            "document_filename"
        ),
        "document_saved": True,
        "document_delivery": document_public(
            delivery
        ),
    }


# ============================================================
# WHATSAPP HELPERS
# ============================================================

def normalize_whatsapp_number(
    value: Any,
) -> str:

    raw = clean(
        value
    )

    if not raw:
        return ""

    # Keep international digits only.
    digits = re.sub(
        r"\D+",
        "",
        raw,
    )

    # Nigeria convenience:
    # 080... becomes 23480...
    if (
        digits.startswith("0")
        and len(digits) >= 10
    ):

        digits = (
            "234"
            + digits[1:]
        )

    return digits


def whatsapp_configured() -> bool:

    return bool(
        WHATSAPP_ACCESS_TOKEN
        and WHATSAPP_PHONE_NUMBER_ID
    )


def whatsapp_api_request(
    *,
    phone_number: str,
    text: str,
) -> dict[str, Any]:

    if not whatsapp_configured():

        raise RuntimeError(
            (
                "WHATSAPP_DELIVERY_NOT_CONFIGURED: "
                "Set WHATSAPP_ACCESS_TOKEN and "
                "WHATSAPP_PHONE_NUMBER_ID."
            )
        )

    number = normalize_whatsapp_number(
        phone_number
    )

    if not number:

        raise RuntimeError(
            "A customer WhatsApp number is required."
        )

    url = (
        "https://graph.facebook.com/v20.0/"
        + quote(
            WHATSAPP_PHONE_NUMBER_ID
        )
        + "/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text,
        },
    }

    data = json.dumps(
        payload
    ).encode(
        "utf-8"
    )

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": (
                "Bearer "
                + WHATSAPP_ACCESS_TOKEN
            ),
            "Content-Type": (
                "application/json"
            ),
            "Accept": (
                "application/json"
            ),
        },
        method="POST",
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            raw = response.read()

            try:

                return json.loads(
                    raw.decode(
                        "utf-8"
                    )
                )

            except Exception:

                return {
                    "raw": raw.decode(
                        "utf-8",
                        errors="replace",
                    )
                }

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
            f"WhatsApp API returned HTTP "
            f"{exc.code}: {detail}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach WhatsApp API: {exc}"
        ) from exc


# ============================================================
# CUSTOMER CARE SEND BY WHATSAPP
#
# The exact saved document is NOT regenerated.
#
# This endpoint sends a WhatsApp message containing the
# customer-facing delivery information.
#
# A WhatsApp Cloud API media-upload flow can be added when the
# provider credentials/support are enabled. For now, the
# endpoint safely confirms the document and sends the delivery
# notification/link data without pretending the binary file
# was uploaded when it was not.
# ============================================================

@app.post(
    "/api/customer-care/send/whatsapp"
)
async def customer_care_send_whatsapp(
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

    version_id = body_version_id(
        body
    )

    phone = (
        body_customer_phone(
            body
        )
    )

    message_text = clean(
        body.get(
            "message"
        )
        or body.get(
            "note"
        )
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
    )

    if not phone and delivery:

        phone = clean(
            delivery.get(
                "customer_phone"
            )
        )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document not found.",
            404,
        )

    ok, error, path = (
        require_released_document(
            delivery
        )
    )

    if not ok:

        return json_response_error(
            "DELIVERY_NOT_RELEASED",
            error,
            403,
            job_id=delivery.get(
                "job_id"
            ),
            version_id=delivery.get(
                "version_id"
            ),
        )

    normalized_phone = (
        normalize_whatsapp_number(
            phone
        )
    )

    if not normalized_phone:

        return json_response_error(
            "WHATSAPP_NUMBER_REQUIRED",
            (
                "A customer WhatsApp number "
                "is required."
            ),
            400,
        )

    filename = (
        clean(
            delivery.get(
                "document_filename"
            )
        )
        or (
            path.name
            if path
            else "finished_document.docx"
        )
    )

    if not message_text:

        message_text = (
            "Hello. Your finished document "
            "from Naija Pocket Business Center "
            "is ready. "
            f"Document: {filename}"
        )

    # --------------------------------------------------------
    # If WhatsApp is configured, send the message.
    # --------------------------------------------------------

    whatsapp_response = None

    if whatsapp_configured():

        try:

            whatsapp_response = (
                whatsapp_api_request(
                    phone_number=normalized_phone,
                    text=message_text,
                )
            )

        except Exception as exc:

            return json_response_error(
                "WHATSAPP_DELIVERY_FAILED",
                str(exc),
                502,
                job_id=delivery.get(
                    "job_id"
                ),
                version_id=delivery.get(
                    "version_id"
                ),
                document_saved=True,
            )

    else:

        # ----------------------------------------------------
        # Do not falsely claim that WhatsApp sent the file.
        #
        # The saved document remains safely available.
        # ----------------------------------------------------

        return {
            "ok": False,
            "error": (
                "WHATSAPP_DELIVERY_NOT_CONFIGURED"
            ),
            "message": (
                "The exact document is ready and "
                "released, but WhatsApp delivery is "
                "not configured on the server."
            ),
            "job_id": delivery.get(
                "job_id"
            ),
            "document_version": delivery.get(
                "version_id"
            ),
            "document_saved": True,
            "document_filename": filename,
            "customer_whatsapp": normalized_phone,
            "whatsapp_configured": False,
            "document_delivery": document_public(
                delivery
            ),
        }

    timestamp = now_iso()

    delivery = (
        update_document_delivery(
            delivery.get(
                "job_id"
            ),
            delivery.get(
                "version_id"
            ),
            customer_phone=normalized_phone,
            last_delivery_channel="whatsapp",
            last_delivery_at=timestamp,
            whatsapp_sent_at=timestamp,
        )
        or delivery
    )

    return {
        "ok": True,
        "message": (
            "The WhatsApp delivery message "
            "was sent for the exact saved document."
        ),
        "channel": "whatsapp",
        "recipient": normalized_phone,
        "job_id": delivery.get(
            "job_id"
        ),
        "document_version": delivery.get(
            "version_id"
        ),
        "document_filename": filename,
        "document_saved": True,
        "whatsapp_configured": True,
        "whatsapp_response": whatsapp_response,
        "document_delivery": document_public(
            delivery
        ),
    }


# ============================================================
# CUSTOMER CARE DELIVERY STATUS
# ============================================================

@app.get(
    "/api/customer-care/delivery"
)
async def customer_care_delivery(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
    paymentId: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id or "",
        version_id=version_id or "",
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document not found.",
            404,
        )

    payment = resolve_payment_for_delivery(
        delivery
    )

    return {
        "ok": True,
        "document": document_public(
            delivery
        ),
        "payment": (
            payment_public(
                payment
            )
            if payment
            else None
        ),
    }


# ============================================================
# CUSTOMER CARE RELEASE / UNLOCK
#
# Alias endpoint for systems that use the word RELEASE.
# ============================================================

@app.post(
    "/api/customer-care/release"
)
async def customer_care_release(
    request: Request,
):

    return await back_office_activate_download(
        request
    )


# ============================================================
# BACK OFFICE DOWNLOAD
#
# Customer Care can download the exact file after release.
# ============================================================

@app.get(
    "/api/customer-care/document"
)
async def customer_care_document(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
    paymentId: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment_id = normalize_payment_id(
        payment_id
        or paymentId
    )

    delivery = resolve_document_delivery(
        payment_id=payment_id,
        job_id=job_id or "",
        version_id=version_id or "",
    )

    if not delivery:

        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "Finished document not found.",
            404,
        )

    if not delivery_document_exists(
        delivery
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            "The exact saved document is missing.",
            409,
        )

    output = delivery_document_path(
        delivery
    )

    if not output:

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            "Saved document path is missing.",
            409,
        )

    filename = (
        clean(
            delivery.get(
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
            "X-Job-ID": clean(
                delivery.get(
                    "job_id"
                )
            ),
            "X-Document-Version": clean(
                delivery.get(
                    "version_id"
                )
            ),
            "X-Document-Snapshot": "true",
            "X-Customer-Care": "true",
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

    print(
        "[PAYMENT API] Email delivery configured: "
        + str(
            bool(
                SMTP_HOST
                and SMTP_FROM
            )
        )
    )

    print(
        "[PAYMENT API] WhatsApp delivery configured: "
        + str(
            bool(
                WHATSAPP_ACCESS_TOKEN
                and WHATSAPP_PHONE_NUMBER_ID
            )
        )
    )

    print(
        "[PAYMENT API] Payment records: "
        + str(
            payment_record_count()
        )
    )

    print(
        "[PAYMENT API] Finished document records: "
        + str(
            document_record_count()
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
