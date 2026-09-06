from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
# PAYMENT + DOWNLOAD + BACK OFFICE DATABASE SYNC
#
# IMPORTANT:
# - Does NOT handle /api/chat.
# - Does NOT generate documents.
# - Does NOT modify workspace.html.
# - Does NOT modify review.html.
# - Keeps payment verification required before download.
#
# Main purpose of this version:
# Make the Back Office read the actual persistent business
# database and payment database instead of depending on one
# incomplete data source.
# ============================================================

APP_VERSION = "payment-download-v8-business-db-backoffice"

BASE_DIR = Path(__file__).resolve().parent

PAYMENT_DB_PATH = Path(
    os.getenv(
        "PAYMENT_DB_PATH",
        str(BASE_DIR / "payment_gateway.db"),
    )
)

MAIN_DB_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(BASE_DIR / "naija_pocket_business.db"),
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


# Existing document/review API.
# The payment API only reads from it when necessary.
DOCUMENT_API_BASE_URL = (
    os.getenv("DOCUMENT_API_BASE_URL", "").strip().rstrip("/")
    or os.getenv("OLD_API_BASE_URL", "").strip().rstrip("/")
)

INTERNAL_API_KEY = os.getenv(
    "INTERNAL_API_KEY",
    "",
).strip()


# Back-office activation key.
# GET /api/back-office/jobs does NOT require this key because
# workspace.html does not send one for that GET request.
BACK_OFFICE_KEY = (
    os.getenv("BACK_OFFICE_ADMIN_KEY", "").strip()
    or os.getenv("BACK_OFFICE_KEY", "").strip()
    or os.getenv("ADMIN_KEY", "").strip()
)


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


def normalize_status(value: Any) -> str:
    return (
        clean(value)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def payment_is_verified(status: Any) -> bool:
    return normalize_status(status) in {
        "verified",
        "completed",
        "complete",
        "paid",
        "payment_confirmed",
    }


def payment_is_reported(status: Any) -> bool:
    return normalize_status(status) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def payment_is_pending(status: Any) -> bool:
    return normalize_status(status) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def json_error(
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
# DATABASE CONNECTIONS
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


def connect_main_db() -> sqlite3.Connection:
    MAIN_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(MAIN_DB_PATH)
    )

    conn.row_factory = sqlite3.Row

    return conn


# ============================================================
# PAYMENT DATABASE INITIALIZATION
# ============================================================

def init_db() -> None:
    with connect_payment_db() as conn:

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

                download_count INTEGER NOT NULL DEFAULT 0
            )
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
            idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        conn.commit()


# ============================================================
# PAYMENT RECORD HELPERS
# ============================================================

def row_dict(
    row: sqlite3.Row | None,
) -> dict[str, Any] | None:

    if row is None:
        return None

    return dict(row)


def get_payment(
    payment_id: str,
) -> dict[str, Any] | None:

    payment_id = clean(payment_id)

    if not payment_id:
        return None

    with connect_payment_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            """,
            (payment_id,),
        ).fetchone()

    return row_dict(row)


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:

    job_id = clean(job_id)

    if not job_id:
        return None

    with connect_payment_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE CAST(job_id AS TEXT) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    return row_dict(row)


def list_all_gateway_payments() -> list[dict[str, Any]]:

    with connect_payment_db() as conn:

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


def list_pending_payments() -> list[dict[str, Any]]:

    with connect_payment_db() as conn:

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

    timestamp = now_iso()

    with connect_payment_db() as conn:

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
                str(job_id),
                customer_id,
                service,
                amount,
                currency,
                payment_method,
                document_version,
                document_filename,
                json.dumps(
                    document_payload,
                    ensure_ascii=False,
                ),
                timestamp,
                timestamp,
            ),
        )

        conn.commit()

    return get_payment(payment_id) or {}


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

    current = get_payment(payment_id)

    if not current:
        return None

    fields: list[str] = []
    values: list[Any] = []

    updates = (
        ("payment_status", status),
        ("payment_reference", payment_reference),
        ("customer_note", customer_note),
        ("admin_note", admin_note),
        ("verified_at", verified_at),
        ("reported_at", reported_at),
    )

    for field, value in updates:

        if value is not None:

            fields.append(
                f"{field} = ?"
            )

            values.append(value)

    fields.append(
        "updated_at = ?"
    )

    values.append(
        now_iso()
    )

    values.append(
        payment_id
    )

    with connect_payment_db() as conn:

        conn.execute(
            f"""
            UPDATE payment_orders
            SET {", ".join(fields)}
            WHERE payment_id = ?
            """,
            tuple(values),
        )

        conn.commit()

    return get_payment(payment_id)


def increment_download(
    payment_id: str,
) -> None:

    timestamp = now_iso()

    with connect_payment_db() as conn:

        conn.execute(
            """
            UPDATE payment_orders

            SET
                download_count = download_count + 1,
                downloaded_at = ?,
                updated_at = ?

            WHERE payment_id = ?
            """,
            (
                timestamp,
                timestamp,
                payment_id,
            ),
        )

        conn.commit()


# ============================================================
# MAIN BUSINESS DATABASE HELPERS
# ============================================================

try:
    import database as business_database
except Exception:
    business_database = None


def main_table_exists(
    table: str,
) -> bool:

    try:

        with connect_main_db() as conn:

            row = conn.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table'
                AND name = ?
                """,
                (table,),
            ).fetchone()

        return row is not None

    except Exception as exc:

        print(
            f"[PAYMENT API][MAIN DB TABLE] {exc}"
        )

        return False


def main_columns(
    table: str,
) -> list[str]:

    if not main_table_exists(table):
        return []

    try:

        with connect_main_db() as conn:

            rows = conn.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall()

        return [
            str(row["name"])
            for row in rows
        ]

    except Exception as exc:

        print(
            f"[PAYMENT API][MAIN DB COLUMNS] {exc}"
        )

        return []


def main_rows(
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:

    try:

        with connect_main_db() as conn:

            rows = conn.execute(
                sql,
                params,
            ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    except Exception as exc:

        print(
            f"[PAYMENT API][MAIN DB READ] {exc}"
        )

        return []


def main_one(
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:

    try:

        with connect_main_db() as conn:

            row = conn.execute(
                sql,
                params,
            ).fetchone()

        return row_dict(row)

    except Exception as exc:

        print(
            f"[PAYMENT API][MAIN DB ONE] {exc}"
        )

        return None


def business_call(
    name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:

    if business_database is None:
        return None

    function = getattr(
        business_database,
        name,
        None,
    )

    if not callable(function):
        return None

    try:

        return function(
            *args,
            **kwargs,
        )

    except Exception as exc:

        print(
            f"[PAYMENT API][database.{name}] {exc}"
        )

        return None


def get_business_job(
    job_id: str,
) -> dict[str, Any] | None:

    job_id = clean(job_id)

    if not job_id:
        return None

    result = business_call(
        "get_job",
        job_id,
    )

    if isinstance(result, dict):
        return dict(result)

    rows = main_rows(
        """
        SELECT *
        FROM jobs
        WHERE CAST(id AS TEXT) = ?
        LIMIT 1
        """,
        (job_id,),
    )

    if rows:
        return rows[0]

    return None


def get_business_payment(
    job_id: str,
) -> dict[str, Any] | None:

    job_id = clean(job_id)

    result = business_call(
        "get_latest_payment",
        job_id,
    )

    if isinstance(result, dict):
        return dict(result)

    if not main_table_exists("payments"):
        return None

    columns = main_columns(
        "payments"
    )

    if "job_id" not in columns:
        return None

    order_column = (
        "id"
        if "id" in columns
        else "rowid"
    )

    return main_one(
        f"""
        SELECT *
        FROM payments
        WHERE CAST(job_id AS TEXT) = ?
        ORDER BY {order_column} DESC
        LIMIT 1
        """,
        (job_id,),
    )


def get_main_jobs_direct() -> list[dict[str, Any]]:

    if not main_table_exists("jobs"):
        return []

    columns = main_columns(
        "jobs"
    )

    order_parts = []

    for column in (
        "updated_at",
        "created_at",
        "id",
    ):

        if column in columns:

            order_parts.append(
                f'"{column}" DESC'
            )

    order_sql = (
        ", ".join(order_parts)
        if order_parts
        else "rowid DESC"
    )

    return main_rows(
        f"""
        SELECT *
        FROM jobs
        ORDER BY {order_sql}
        """
    )


def get_main_payment_rows() -> list[dict[str, Any]]:

    if not main_table_exists("payments"):
        return []

    columns = main_columns(
        "payments"
    )

    if "job_id" not in columns:
        return []

    order_column = (
        "id"
        if "id" in columns
        else "rowid"
    )

    return main_rows(
        f"""
        SELECT *
        FROM payments
        ORDER BY {order_column} DESC
        """
    )


# ============================================================
# WORK RECORDS
# ============================================================

def normalize_work_record(
    row: dict[str, Any],
) -> dict[str, Any]:

    work_id = row.get(
        "work_id",
        row.get("id"),
    )

    version = row.get(
        "version",
        row.get(
            "version_id",
            row.get(
                "work_version",
                1,
            ),
        ),
    )

    activated = row.get(
        "download_activated",
        row.get(
            "downloadActivated",
            row.get(
                "activated",
                False,
            ),
        ),
    )

    if isinstance(
        activated,
        str,
    ):

        activated = (
            activated.lower()
            in {
                "1",
                "true",
                "yes",
                "on",
            }
        )

    result = dict(row)

    result.update(
        {
            "work_id": work_id,
            "workId": work_id,
            "id": work_id,

            "version": version,
            "version_id": version,

            "work_status": row.get(
                "work_status",
                row.get(
                    "status",
                    "",
                ),
            ),

            "status": row.get(
                "status",
                row.get(
                    "work_status",
                    "",
                ),
            ),

            "download_activated": bool(
                activated
            ),

            "downloadActivated": bool(
                activated
            ),

            "activated": bool(
                activated
            ),
        }
    )

    return result


def get_main_work_records(
    job_id: str,
) -> list[dict[str, Any]]:

    if not main_table_exists(
        "work_records"
    ):
        return []

    columns = main_columns(
        "work_records"
    )

    if "job_id" not in columns:
        return []

    order_column = (
        "id"
        if "id" in columns
        else "rowid"
    )

    rows = main_rows(
        f"""
        SELECT *
        FROM work_records

        WHERE CAST(job_id AS TEXT) = ?

        ORDER BY {order_column} ASC
        """,
        (str(job_id),),
    )

    return [
        normalize_work_record(row)
        for row in rows
    ]


# ============================================================
# BUSINESS DATABASE JOB FALLBACKS
# ============================================================

def get_business_job_lists() -> list[dict[str, Any]]:

    output: list[dict[str, Any]] = []

    for function_name in (
        "get_back_office_jobs",
        "get_all_jobs",
    ):

        result = business_call(
            function_name
        )

        if isinstance(
            result,
            list,
        ):

            for item in result:

                if isinstance(
                    item,
                    dict,
                ):

                    output.append(
                        dict(item)
                    )

    return output


# ============================================================
# ENSURE MAIN BUSINESS JOB EXISTS
# ============================================================

def ensure_business_job(
    job_id: str,
    *,
    customer_id: str = "",
    service: str = "",
    amount: float = 0.0,
    status: str = "payment_pending",
    current: dict[str, Any] | None = None,
) -> dict[str, Any] | None:

    existing = get_business_job(
        job_id
    )

    if existing:
        return existing

    service_name = (
        service
        or "Business Center Service"
    )

    # First use the project's existing database function.
    created = business_call(
        "create_job",
        customer_id=customer_id,
        service_type=service_name,
        description=service_name,
        customer_request="",
        status=status,
        amount=amount,
        currency=DEFAULT_CURRENCY,
        work_reference=clean(
            (current or {}).get(
                "filename"
            )
        ),
    )

    if isinstance(
        created,
        dict,
    ):

        return created

    # Direct SQL fallback.
    if not main_table_exists(
        "jobs"
    ):
        return None

    columns = main_columns(
        "jobs"
    )

    candidates: dict[str, Any] = {
        "customer_id": customer_id,
        "customer_name": "",
        "phone": "",
        "service_type": service_name,
        "service": service_name,
        "job_type": service_name,
        "description": service_name,
        "customer_request": "",
        "status": status,
        "amount": amount,
        "currency": DEFAULT_CURRENCY,
        "work_reference": clean(
            (current or {}).get(
                "filename"
            )
        ),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    values: dict[str, Any] = {}

    for column in columns:

        if column in candidates:
            values[column] = candidates[column]

    # Handle additional NOT NULL columns
    # dynamically so schema differences do not
    # cause the synchronization to fail.
    try:

        with connect_main_db() as conn:

            info = conn.execute(
                'PRAGMA table_info("jobs")'
            ).fetchall()

            for row in info:

                name = row["name"]

                if name == "id":
                    continue

                if name in values:
                    continue

                if row["notnull"] != 1:
                    continue

                if row["dflt_value"] is not None:
                    continue

                lower = name.lower()

                if "customer" in lower:

                    values[name] = (
                        customer_id
                    )

                elif "status" in lower:

                    values[name] = (
                        status
                    )

                elif (
                    "amount" in lower
                    or "price" in lower
                ):

                    values[name] = (
                        amount
                    )

                elif (
                    "service" in lower
                    or "type" in lower
                    or "title" in lower
                ):

                    values[name] = (
                        service_name
                    )

                else:

                    values[name] = ""

            if not values:
                return None

            names = list(
                values.keys()
            )

            quoted_names = ",".join(
                f'"{name}"'
                for name in names
            )

            placeholders = ",".join(
                "?"
                for _ in names
            )

            cursor = conn.execute(
                f"""
                INSERT INTO jobs (
                    {quoted_names}
                )

                VALUES (
                    {placeholders}
                )
                """,
                tuple(
                    values[name]
                    for name in names
                ),
            )

            conn.commit()

            new_id = cursor.lastrowid

        return (
            get_business_job(
                str(new_id)
            )
            or {
                "id": new_id,
                "job_id": new_id,
                **values,
            }
        )

    except Exception as exc:

        print(
            "[PAYMENT API]"
            "[ensure_business_job] "
            f"{exc}"
        )

        return None


# ============================================================
# PAYMENT → BUSINESS DATABASE SYNCHRONIZATION
# ============================================================

def synchronize_payment_to_business(
    payment: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> None:

    job_id = clean(
        payment.get(
            "job_id"
        )
    )

    if not job_id:
        return

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    if verified:
        job_status = (
            "payment_confirmed"
        )
    else:
        job_status = (
            "payment_pending"
        )

    job = ensure_business_job(
        job_id,
        customer_id=clean(
            payment.get(
                "customer_id"
            )
        ),
        service=(
            clean(
                payment.get(
                    "service"
                )
            )
            or clean(
                (current or {}).get(
                    "service"
                )
            )
        ),
        amount=money(
            payment.get(
                "amount"
            )
            or (current or {}).get(
                "amount"
            )
        ),
        status=job_status,
        current=current,
    )

    if job is not None:

        business_call(
            "update_job_status",
            job_id,
            job_status,
        )

    # Synchronize payment record in the
    # main business database where the
    # existing payment functions are available.
    if business_database is not None:

        try:

            existing_payment = business_call(
                "get_latest_payment",
                job_id,
            )

            business_payment_id = None

            if isinstance(
                existing_payment,
                dict,
            ):

                business_payment_id = (
                    existing_payment.get(
                        "id"
                    )
                    or existing_payment.get(
                        "payment_id"
                    )
                )

            if not existing_payment:

                created_payment = business_call(
                    "create_payment",
                    job_id,
                    money(
                        payment.get(
                            "amount"
                        )
                    ),
                    clean(
                        payment.get(
                            "payment_method"
                        )
                    )
                    or DEFAULT_PAYMENT_METHOD,
                )

                if isinstance(
                    created_payment,
                    dict,
                ):

                    business_payment_id = (
                        created_payment.get(
                            "id"
                        )
                        or created_payment.get(
                            "payment_id"
                        )
                    )

                elif created_payment is not None:

                    business_payment_id = (
                        created_payment
                    )

            if business_payment_id is not None:

                if verified:

                    business_call(
                        "update_payment_status",
                        business_payment_id,
                        "paid",
                    )

                elif payment_is_reported(
                    payment.get(
                        "payment_status"
                    )
                ):

                    business_call(
                        "update_payment_status",
                        business_payment_id,
                        "reported",
                    )

        except Exception as exc:

            print(
                "[PAYMENT API]"
                "[business payment sync] "
                f"{exc}"
            )


# ============================================================
# DOCUMENT HELPERS
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

        return (
            [value.strip()]
            if value.strip()
            else []
        )

    if isinstance(
        value,
        dict,
    ):

        text = (
            value.get("content")
            or value.get("text")
            or value.get("body")
            or value.get("page_text")
            or ""
        )

        return (
            [str(text).strip()]
            if str(text).strip()
            else []
        )

    if isinstance(
        value,
        (list, tuple),
    ):

        output: list[str] = []

        for item in value:

            if isinstance(
                item,
                dict,
            ):

                text = (
                    item.get(
                        "content"
                    )
                    or item.get(
                        "text"
                    )
                    or item.get(
                        "body"
                    )
                    or item.get(
                        "page_text"
                    )
                    or ""
                )

            else:

                text = item

            if str(text).strip():

                output.append(
                    str(text).strip()
                )

        return output

    text = str(value).strip()

    return (
        [text]
        if text
        else []
    )


def safe_json(
    value: Any,
) -> Any:

    if value is None:
        return None

    if isinstance(
        value,
        (dict, list, int, float, bool),
    ):

        return value

    text = clean(value)

    if not text:
        return None

    try:

        return json.loads(
            text
        )

    except Exception:

        return text


def get_current_document_from_main_db(
    job_id: str,
) -> dict[str, Any] | None:

    records = get_main_work_records(
        job_id
    )

    if not records:
        return None

    work = records[-1]

    payload: dict[str, Any] = {}

    for key in (
        "document_payload",
        "payload",
        "data",
        "content",
        "document_text",
        "text",
        "work_content",
    ):

        if key not in work:
            continue

        value = safe_json(
            work.get(key)
        )

        if isinstance(
            value,
            dict,
        ):

            payload.update(
                value
            )

        elif isinstance(
            value,
            str,
        ) and value:

            payload.setdefault(
                "document_text",
                value,
            )

    pages = normalize_pages(
        payload.get("pages")
        or work.get("pages")
        or work.get("document_pages")
    )

    text = clean(
        payload.get(
            "document_text"
        )
        or work.get(
            "document_text"
        )
        or work.get(
            "content"
        )
        or work.get(
            "text"
        )
    )

    if not pages and text:
        pages = [text]

    version = clean(
        payload.get(
            "version_id"
        )
        or work.get(
            "version_id"
        )
        or work.get(
            "version"
        )
    )

    if not version:

        version = clean(
            work.get(
                "id"
            )
            or work.get(
                "work_id"
            )
        )

    filename = clean(
        payload.get(
            "filename"
        )
        or work.get(
            "filename"
        )
        or work.get(
            "storage_reference"
        )
    )

    if not filename:

        filename = (
            f"naija_pocket_{job_id}.docx"
        )

    if not filename.lower().endswith(
        ".docx"
    ):

        filename += ".docx"

    if not pages and not text:
        return None

    job = get_business_job(
        job_id
    ) or {}

    return {
        "job_id": job_id,
        "status": clean(
            work.get(
                "work_status"
            )
            or work.get(
                "status"
            )
        ),
        "review_finished": True,
        "pages": pages,
        "document_text": text,
        "amount": money(
            job.get(
                "amount"
            )
        ),
        "version_id": version,
        "filename": filename,
        "service": clean(
            job.get(
                "service_type"
            )
            or job.get(
                "service"
            )
        ),
        "customer_id": clean(
            job.get(
                "customer_id"
            )
        ),
    }


def document_api_request(
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:

    if not DOCUMENT_API_BASE_URL:

        raise RuntimeError(
            "Document API URL is not configured."
        )

    url = (
        DOCUMENT_API_BASE_URL
        + "/"
        + path.lstrip("/")
    )

    if query:

        encoded = urllib.parse.urlencode(
            {
                key: value
                for key, value in query.items()
                if value not in (
                    None,
                    "",
                )
            }
        )

        if encoded:

            url += (
                "?"
                + encoded
            )

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "NaijaPocketPaymentAPI/8"
        ),
    }

    if INTERNAL_API_KEY:

        headers[
            "X-Internal-API-Key"
        ] = INTERNAL_API_KEY

    data = None

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
            timeout=25,
        ) as response:

            raw = response.read()

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
            f"Document API returned HTTP "
            f"{exc.code}: {detail}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach document API: {exc}"
        ) from exc


def normalize_document_payload(
    payload: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:

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
        or payload.get(
            "page_texts"
        )
    )

    document_text = clean(
        payload.get(
            "document_text"
        )
        or payload.get(
            "text"
        )
        or payload.get(
            "content"
        )
        or payload.get(
            "document"
        )
    )

    if not pages and document_text:

        pages = [
            document_text
        ]

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
        or f"naija_pocket_{job_id}.docx"
    )

    if not filename.lower().endswith(
        ".docx"
    ):

        filename += ".docx"

    return {
        "job_id": job_id,
        "status": clean(
            payload.get(
                "status"
            )
        ),
        "review_finished": bool(
            payload.get(
                "review_finished"
            )
            or payload.get(
                "review_complete"
            )
            or normalize_status(
                payload.get(
                    "status"
                )
            )
            == "review_complete"
        ),
        "pages": pages,
        "document_text": document_text,
        "amount": money(
            payload.get(
                "amount"
            )
            or payload.get(
                "total_amount"
            )
            or payload.get(
                "price"
            )
        ),
        "version_id": version,
        "filename": filename,
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
        "raw": payload,
    }


def fetch_current_document(
    job_id: str,
) -> dict[str, Any]:

    # First choice: persistent business database.
    local = get_current_document_from_main_db(
        job_id
    )

    if local and local.get(
        "pages"
    ):

        return local

    last_error: Exception | None = None

    for path in (
        "/api/review/pages",
        "/api/review",
        "/api/job",
    ):

        try:

            response = document_api_request(
                "GET",
                path,
                {
                    "job_id": job_id
                },
            )

            if not isinstance(
                response,
                dict,
            ):
                continue

            candidates = [
                response
            ]

            for key in (
                "data",
                "job",
                "result",
                "document",
                "review",
            ):

                if isinstance(
                    response.get(
                        key
                    ),
                    dict,
                ):

                    candidates.append(
                        response[key]
                    )

            for candidate in candidates:

                if any(
                    key in candidate
                    for key in (
                        "pages",
                        "document_pages",
                        "document_text",
                        "text",
                        "content",
                    )
                ):

                    return normalize_document_payload(
                        candidate,
                        job_id,
                    )

        except Exception as exc:

            last_error = exc

    # Last persistent fallback:
    # the document snapshot saved with the payment.
    payment = get_latest_payment_for_job(
        job_id
    )

    if payment:

        snapshot = payment_document(
            payment
        )

        if snapshot:

            return normalize_document_payload(
                snapshot,
                job_id,
            )

    if local:
        return local

    raise RuntimeError(
        str(last_error)
        if last_error
        else (
            "No document was found "
            "for this job."
        )
    )


def safe_current_document(
    job_id: str,
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:

    try:

        return (
            fetch_current_document(
                job_id
            ),
            None,
        )

    except Exception as exc:

        return (
            None,
            str(exc),
        )


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any]:

    try:

        data = json.loads(
            payment.get(
                "document_payload"
            )
            or "{}"
        )

        if isinstance(
            data,
            dict,
        ):

            return data

    except Exception:
        pass

    return {}


def document_version_is_current(
    payment: dict[str, Any],
    current: dict[str, Any],
) -> bool:

    stored = clean(
        payment.get(
            "document_version"
        )
    )

    current_version = clean(
        current.get(
            "version_id"
        )
    )

    return bool(
        stored
        and current_version
        and stored == current_version
    )


def current_document_is_ready(
    document: dict[str, Any],
) -> bool:

    return bool(
        normalize_pages(
            document.get(
                "pages"
            )
        )
        or clean(
            document.get(
                "document_text"
            )
        )
    )


# ============================================================
# BACK OFFICE JOB MERGER
# ============================================================

def normalize_back_office_job(
    raw: dict[str, Any],
    payment: dict[str, Any] | None,
    work_records: list[dict[str, Any]],
) -> dict[str, Any]:

    job_id = raw.get(
        "job_id",
        raw.get(
            "id"
        ),
    )

    payment_status = clean(
        (payment or {}).get(
            "payment_status"
        )
        or raw.get(
            "payment_status"
        )
        or raw.get(
            "paymentStatus"
        )
    )

    status = clean(
        raw.get(
            "status"
        )
        or raw.get(
            "job_status"
        )
    )

    paid = payment_is_verified(
        payment_status
    )

    reported = payment_is_reported(
        payment_status
    )

    pending = payment_is_pending(
        payment_status
    )

    # IMPORTANT:
    # These statuses match the logic already used
    # by workspace.html.
    if paid:

        if normalize_status(
            status
        ) in {
            "",
            "review_complete",
            "approved",
            "payment_pending",
            "paid",
        }:

            status = (
                "payment_confirmed"
            )

    elif reported:

        status = (
            "payment_pending"
        )

    elif pending:

        if normalize_status(
            status
        ) in {
            "",
            "review_complete",
        }:

            status = (
                "payment_pending"
            )

    service = clean(
        raw.get(
            "service"
        )
        or raw.get(
            "service_name"
        )
        or raw.get(
            "service_type"
        )
        or (payment or {}).get(
            "service"
        )
        or "Business Center Service"
    )

    approved = (
        normalize_status(
            status
        )
        in {
            "approved",
            "payment_pending",
            "payment_confirmed",
            "completed",
            "paid",
            "delivery",
        }
        or paid
    )

    result = dict(
        raw
    )

    result.update(
        {
            "id": job_id,

            "job_id": job_id,

            "jobId": job_id,

            "status": (
                status
                or (
                    "payment_confirmed"
                    if paid
                    else "payment_pending"
                    if payment
                    else "review_complete"
                )
            ),

            "service": service,

            "service_name": service,

            "serviceName": service,

            "payment_status": payment_status,

            "paymentStatus": payment_status,

            "paid": paid,

            "payment_verified": paid,

            "download_unlocked": (
                paid
                and any(
                    bool(
                        work.get(
                            "download_activated"
                        )
                    )
                    for work in work_records
                )
            ),

            "approved": approved,

            "work_records": work_records,

            "workRecords": work_records,

            "versions": work_records,

            "works": work_records,

            "activated_work": next(
                (
                    work
                    for work in reversed(
                        work_records
                    )
                    if work.get(
                        "download_activated"
                    )
                ),
                None,
            ),

            "payment": (
                payment_public(
                    payment
                )
                if payment
                else None
            ),

            "payment_id": (
                (payment or {}).get(
                    "payment_id"
                )
                or raw.get(
                    "payment_id"
                )
            ),

            "amount": money(
                (payment or {}).get(
                    "amount"
                )
                or raw.get(
                    "amount"
                )
            ),

            "currency": (
                (payment or {}).get(
                    "currency"
                )
                or raw.get(
                    "currency"
                )
                or DEFAULT_CURRENCY
            ),

            "created_at": (
                raw.get(
                    "created_at"
                )
                or (payment or {}).get(
                    "created_at"
                )
            ),

            "updated_at": (
                raw.get(
                    "updated_at"
                )
                or (payment or {}).get(
                    "updated_at"
                )
            ),
        }
    )

    return result


def get_back_office_jobs_combined() -> list[dict[str, Any]]:

    sources: dict[
        str,
        dict[str, Any],
    ] = {}

    def add_job(
        raw: Any,
    ) -> None:

        if not isinstance(
            raw,
            dict,
        ):
            return

        job_id = clean(
            raw.get(
                "job_id",
                raw.get(
                    "id"
                ),
            )
        )

        if not job_id:
            return

        existing = sources.get(
            job_id,
            {},
        )

        merged = dict(
            existing
        )

        for key, value in raw.items():

            if value not in (
                None,
                "",
                [],
                {},
            ):

                merged[key] = value

        sources[job_id] = merged

    # 1. Existing project database functions.
    for raw in get_business_job_lists():

        add_job(
            raw
        )

    # 2. Direct persistent jobs table.
    for raw in get_main_jobs_direct():

        add_job(
            raw
        )

    # 3. Gateway payment records.
    # This guarantees that a payment-created job is visible
    # even if the business job query failed to expose it.
    gateway_payments = (
        list_all_gateway_payments()
    )

    for payment in gateway_payments:

        job_id = clean(
            payment.get(
                "job_id"
            )
        )

        if not job_id:
            continue

        if job_id not in sources:

            sources[job_id] = {
                "job_id": job_id,
                "id": job_id,
                "customer_id": payment.get(
                    "customer_id"
                ),
                "service": payment.get(
                    "service"
                ),
                "status": (
                    "payment_confirmed"
                    if payment_is_verified(
                        payment.get(
                            "payment_status"
                        )
                    )
                    else "payment_pending"
                ),
            }

    # Main database payment rows.
    main_payment_rows = (
        get_main_payment_rows()
    )

    payments_by_job: dict[
        str,
        dict[str, Any],
    ] = {}

    for payment in main_payment_rows:

        job_id = clean(
            payment.get(
                "job_id"
            )
        )

        if job_id and job_id not in payments_by_job:

            payments_by_job[
                job_id
            ] = payment

    output: list[
        dict[str, Any]
    ] = []

    for job_id, raw in sources.items():

        gateway_payment = (
            get_latest_payment_for_job(
                job_id
            )
        )

        business_payment = (
            payments_by_job.get(
                job_id
            )
        )

        payment = (
            gateway_payment
            or business_payment
        )

        if (
            gateway_payment
            and business_payment
        ):

            payment = dict(
                business_payment
            )

            payment.update(
                gateway_payment
            )

        work_records = (
            get_main_work_records(
                job_id
            )
        )

        # Compatibility fallback if the database function
        # already returned work records.
        if not work_records:

            supplied = (
                raw.get(
                    "work_records"
                )
                or raw.get(
                    "workRecords"
                )
                or raw.get(
                    "versions"
                )
                or raw.get(
                    "works"
                )
                or []
            )

            if isinstance(
                supplied,
                list,
            ):

                work_records = [
                    normalize_work_record(
                        item
                    )
                    for item in supplied
                    if isinstance(
                        item,
                        dict,
                    )
                ]

        output.append(
            normalize_back_office_job(
                raw,
                payment,
                work_records,
            )
        )

    # Newest jobs first.
    output.sort(
        key=lambda item: clean(
            item.get(
                "updated_at"
            )
            or item.get(
                "created_at"
            )
            or ""
        ),
        reverse=True,
    )

    return output


# ============================================================
# PAYMENT PUBLIC FORMAT
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    return {
        "payment_id": payment.get(
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

        "currency": (
            payment.get(
                "currency"
            )
            or DEFAULT_CURRENCY
        ),

        "payment_method": payment.get(
            "payment_method"
        ),

        "payment_status": payment.get(
            "payment_status"
        ),

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

        "document_filename": payment.get(
            "document_filename"
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

        "paid": verified,

        "payment_verified": verified,

        "download_unlocked": verified,
    }


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


def make_docx(
    pages: list[str],
    filename: str,
) -> Path:

    safe_name = Path(
        filename
    ).name

    if not safe_name.lower().endswith(
        ".docx"
    ):

        safe_name += ".docx"

    output_path = (
        DOWNLOAD_DIR
        / f"{uuid.uuid4().hex}_{safe_name}"
    )

    body_parts: list[str] = []

    for index, page in enumerate(
        pages
    ):

        if index:

            body_parts.append(
                '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            )

        runs: list[str] = []

        lines = (
            str(page)
            .splitlines()
            or [""]
        )

        for line_index, line in enumerate(
            lines
        ):

            if line_index:

                runs.append(
                    "<w:br/>"
                )

            runs.append(
                '<w:r>'
                '<w:rPr>'
                '<w:sz w:val="24"/>'
                '</w:rPr>'
                f'<w:t xml:space="preserve">'
                f'{xml_escape(line)}'
                f'</w:t>'
                '</w:r>'
            )

        body_parts.append(
            "<w:p>"
            + "".join(runs)
            + "</w:p>"
        )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        + "".join(body_parts)
        + '<w:sectPr>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" '
        'w:bottom="1134" w:left="1134"/>'
        '</w:sectPr>'
        '</w:body>'
        '</w:document>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults>'
        '<w:rPrDefault>'
        '<w:rPr>'
        '<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>'
        '<w:sz w:val="24"/>'
        '</w:rPr>'
        '</w:rPrDefault>'
        '</w:docDefaults>'
        '</w:styles>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )

    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>'
    )

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

    return output_path


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
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {
        "ok": True,
        "service": "payment_api",
        "version": APP_VERSION,
        "payment_database": str(
            PAYMENT_DB_PATH
        ),
        "main_database": str(
            MAIN_DB_PATH
        ),
        "document_api_configured": bool(
            DOCUMENT_API_BASE_URL
        ),
    }


# ============================================================
# DIAGNOSTIC
# ============================================================

@app.get("/api/payment/diagnostic")
async def payment_diagnostic() -> dict[str, Any]:

    gateway_count = len(
        list_all_gateway_payments()
    )

    main_jobs = len(
        get_main_jobs_direct()
    )

    main_work_records = 0

    if main_table_exists(
        "work_records"
    ):

        main_work_records = len(
            main_rows(
                "SELECT * FROM work_records"
            )
        )

    main_payments = len(
        get_main_payment_rows()
    )

    merged_jobs = (
        get_back_office_jobs_combined()
    )

    return {
        "ok": True,

        "version": APP_VERSION,

        "payment_database": str(
            PAYMENT_DB_PATH
        ),

        "main_database": str(
            MAIN_DB_PATH
        ),

        "gateway_payment_orders_count":
            gateway_count,

        "main_jobs_count":
            main_jobs,

        "main_work_records_count":
            main_work_records,

        "main_payments_count":
            main_payments,

        "merged_back_office_jobs_count":
            len(merged_jobs),

        "jobs":
            merged_jobs[:20],
    }


# ============================================================
# CREATE PAYMENT
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

        if isinstance(
            body,
            dict,
        ):

            job_id = (
                job_id
                or body.get(
                    "job_id"
                )
            )

            customer_id = (
                customer_id
                or body.get(
                    "customer_id"
                )
            )

            service = (
                service
                or body.get(
                    "service"
                )
            )

            payment_method = (
                payment_method
                or body.get(
                    "payment_method"
                )
            )

            if amount is None:

                amount = body.get(
                    "amount"
                )

    except Exception:
        pass

    job_id = clean(
        job_id
    )

    customer_id = clean(
        customer_id
    )

    service = clean(
        service
    )

    if not job_id:

        return json_error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
        )

    current, error = (
        safe_current_document(
            job_id
        )
    )

    if error or current is None:

        return json_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be retrieved.",
            502,
            detail=error,
        )

    if not current_document_is_ready(
        current
    ):

        return json_error(
            "DOCUMENT_NOT_READY",
            "The document is not ready for payment.",
            409,
        )

    final_amount = (
        money(
            current.get(
                "amount"
            )
        )
        or money(
            amount
        )
    )

    if final_amount <= 0:

        return json_error(
            "AMOUNT_NOT_AVAILABLE",
            "The amount to pay is not available.",
            409,
        )

    latest = (
        get_latest_payment_for_job(
            job_id
        )
    )

    if latest:

        same_version = (
            document_version_is_current(
                latest,
                current,
            )
        )

        latest_status = normalize_status(
            latest.get(
                "payment_status"
            )
        )

        if (
            same_version
            and latest_status in {
                "pending",
                "reported",
                "verification_pending",
                "awaiting_verification",
                "verified",
                "completed",
                "complete",
                "paid",
            }
        ):

            synchronize_payment_to_business(
                latest,
                current,
            )

            return {
                "ok": True,

                "payment": payment_public(
                    latest
                ),

                "payment_id":
                    latest.get(
                        "payment_id"
                    ),

                "amount":
                    money(
                        latest.get(
                            "amount"
                        )
                    ),

                "currency":
                    latest.get(
                        "currency",
                        DEFAULT_CURRENCY,
                    ),

                "payment_status":
                    latest.get(
                        "payment_status"
                    ),

                "paid":
                    payment_is_verified(
                        latest.get(
                            "payment_status"
                        )
                    ),

                "payment_verified":
                    payment_is_verified(
                        latest.get(
                            "payment_status"
                        )
                    ),

                "download_unlocked":
                    payment_is_verified(
                        latest.get(
                            "payment_status"
                        )
                    ),

                "document_version":
                    current.get(
                        "version_id"
                    ),
            }

    payment_id = (
        f"NPB-{uuid.uuid4().hex[:12].upper()}"
    )

    record = create_payment_record(
        payment_id=payment_id,

        job_id=job_id,

        customer_id=(
            customer_id
            or clean(
                current.get(
                    "customer_id"
                )
            )
        ),

        service=(
            service
            or clean(
                current.get(
                    "service"
                )
            )
            or "Business Center Service"
        ),

        amount=final_amount,

        currency=DEFAULT_CURRENCY,

        payment_method=(
            clean(
                payment_method
            )
            or DEFAULT_PAYMENT_METHOD
        ),

        document_version=clean(
            current.get(
                "version_id"
            )
        ),

        document_filename=(
            clean(
                current.get(
                    "filename"
                )
            )
            or f"naija_pocket_{job_id}.docx"
        ),

        document_payload={
            "job_id":
                job_id,

            "pages":
                current.get(
                    "pages",
                    [],
                ),

            "document_text":
                current.get(
                    "document_text",
                    "",
                ),

            "service":
                current.get(
                    "service",
                    "",
                ),

            "filename":
                current.get(
                    "filename",
                    "",
                ),
        },
    )

    # THIS IS THE IMPORTANT SYNC.
    synchronize_payment_to_business(
        record,
        current,
    )

    return {
        "ok": True,

        "message":
            "Payment created. "
            "Complete payment, then report it "
            "for verification.",

        "payment":
            payment_public(
                record
            ),

        "payment_id":
            payment_id,

        "amount":
            final_amount,

        "currency":
            DEFAULT_CURRENCY,

        "payment_status":
            "pending",

        "paid":
            False,

        "payment_verified":
            False,

        "download_unlocked":
            False,

        "document_version":
            current.get(
                "version_id"
            ),
    }


# ============================================================
# REPORT PAYMENT
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    try:

        body = await request.json()

        if isinstance(
            body,
            dict,
        ):

            payment_id = (
                payment_id
                or body.get(
                    "payment_id"
                )
            )

            job_id = (
                job_id
                or body.get(
                    "job_id"
                )
            )

            payment_reference = (
                payment_reference
                or body.get(
                    "payment_reference"
                )
                or body.get(
                    "reference"
                )
            )

            note = (
                note
                or body.get(
                    "note"
                )
                or body.get(
                    "message"
                )
            )

    except Exception:
        pass

    payment_id = clean(
        payment_id
    )

    job_id = clean(
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

        payment = (
            get_latest_payment_for_job(
                job_id
            )
        )

    if not payment:

        return json_error(
            "PAYMENT_NOT_FOUND",
            "No payment record was found.",
            404,
        )

    current, error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    if error or current is None:

        return json_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The document could not be checked.",
            502,
            detail=error,
        )

    if not document_version_is_current(
        payment,
        current,
    ):

        return json_error(
            "PAYMENT_DOCUMENT_CHANGED",
            "This payment belongs to an older document version.",
            409,
        )

    if payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        synchronize_payment_to_business(
            payment,
            current,
        )

        return {
            "ok": True,

            "message":
                "Payment has already been verified.",

            "payment":
                payment_public(
                    payment
                ),

            "payment_id":
                payment.get(
                    "payment_id"
                ),

            "job_id":
                payment.get(
                    "job_id"
                ),

            "status":
                payment.get(
                    "payment_status"
                ),

            "payment_status":
                payment.get(
                    "payment_status"
                ),

            "paid":
                True,

            "payment_verified":
                True,

            "download_unlocked":
                True,
        }

    updated = update_payment_record(
        payment["payment_id"],

        status="reported",

        payment_reference=(
            clean(
                payment_reference
            )
            or None
        ),

        customer_note=(
            clean(
                note
            )
            or None
        ),

        reported_at=now_iso(),
    )

    synchronize_payment_to_business(
        updated or payment,
        current,
    )

    return {
        "ok": True,

        "message":
            "Payment reported. "
            "Please wait for Customer Care "
            "to verify your payment.",

        "payment":
            payment_public(
                updated
            ),

        "payment_id":
            payment["payment_id"],

        "job_id":
            payment["job_id"],

        "status":
            "reported",

        "payment_status":
            "reported",

        "paid":
            False,

        "payment_verified":
            False,

        "download_unlocked":
            False,
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    job_id: str,
    payment_id: str | None = None,
):

    payment = (
        get_payment(
            clean(
                payment_id
            )
        )
        if clean(
            payment_id
        )
        else get_latest_payment_for_job(
            clean(
                job_id
            )
        )
    )

    if not payment:

        return {
            "ok": True,
            "payment": None,
            "payment_status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    current, error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    if (
        current
        and not document_version_is_current(
            payment,
            current,
        )
    ):

        return {
            "ok": True,

            "payment":
                payment_public(
                    payment
                ),

            "payment_status":
                "invalid_for_current_document",

            "paid":
                False,

            "payment_verified":
                False,

            "download_unlocked":
                False,

            "document_check":
                "changed",
        }

    synchronize_payment_to_business(
        payment,
        current,
    )

    return {
        "ok": True,

        "payment":
            payment_public(
                payment
            ),

        "payment_status":
            payment.get(
                "payment_status"
            ),

        "paid":
            verified,

        "payment_verified":
            verified,

        "download_unlocked":
            verified,

        "document_check":
            (
                "current"
                if current
                else "unavailable"
            ),

        "detail":
            error,
    }


# ============================================================
# PAYMENT COMPLETE COMPATIBILITY ROUTE
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    # "I HAVE MADE PAYMENT" reports payment.
    # It does NOT verify payment.
    return await payment_report(
        request=request,
        payment_id=payment_id,
        job_id=job_id,
        payment_reference=payment_reference,
        note=note,
    )


# ============================================================
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():

    records = (
        list_pending_payments()
    )

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


@app.get("/api/back-office/payments")
async def back_office_payments():

    records = (
        list_all_gateway_payments()
    )

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
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/customer-care/jobs")
async def customer_care_jobs():

    jobs = (
        get_back_office_jobs_combined()
    )

    return {
        "ok": True,
        "success": True,
        "count": len(jobs),
        "jobs": jobs,
    }


@app.get("/api/back-office/jobs")
async def back_office_jobs():

    # IMPORTANT:
    #
    # workspace.html calls this GET without admin_key.
    #
    # Therefore this route deliberately does NOT require
    # an operator key.
    #
    # The operator key remains required for activation.

    jobs = (
        get_back_office_jobs_combined()
    )

    approved_count = sum(
        1
        for job in jobs
        if job.get(
            "approved"
        )
    )

    activated_count = sum(
        1
        for job in jobs
        if any(
            work.get(
                "download_activated"
            )
            for work in job.get(
                "work_records",
                [],
            )
        )
    )

    paid_count = sum(
        1
        for job in jobs
        if job.get(
            "paid"
        )
    )

    pending_count = sum(
        1
        for job in jobs
        if normalize_status(
            job.get(
                "payment_status"
            )
        )
        in {
            "pending",
            "reported",
            "verification_pending",
            "awaiting_verification",
        }
    )

    return {
        "ok": True,

        "success": True,

        "count":
            len(jobs),

        "total":
            len(jobs),

        "job_count":
            len(jobs),

        "total_jobs":
            len(jobs),

        "approved_count":
            approved_count,

        "approved_jobs":
            approved_count,

        "activated_count":
            activated_count,

        "activated_jobs":
            activated_count,

        "paid_count":
            paid_count,

        "paid_jobs":
            paid_count,

        "pending_count":
            pending_count,

        "pending_jobs":
            pending_count,

        "stats": {
            "total":
                len(jobs),

            "approved":
                approved_count,

            "activated":
                activated_count,

            "paid":
                paid_count,

            "pending":
                pending_count,
        },

        "jobs":
            jobs,
    }


@app.get("/api/back-office/stats")
async def back_office_stats():

    jobs = (
        get_back_office_jobs_combined()
    )

    approved = sum(
        1
        for job in jobs
        if job.get(
            "approved"
        )
    )

    activated = sum(
        1
        for job in jobs
        if any(
            work.get(
                "download_activated"
            )
            for work in job.get(
                "work_records",
                [],
            )
        )
    )

    paid = sum(
        1
        for job in jobs
        if job.get(
            "paid"
        )
    )

    return {
        "ok": True,
        "total": len(jobs),
        "approved": approved,
        "activated": activated,
        "paid": paid,
    }


# ============================================================
# BACK OFFICE KEY
# ============================================================

def key_is_valid(
    key: str,
) -> bool:

    # If no key has been configured,
    # preserve compatibility with the existing setup.
    if not BACK_OFFICE_KEY:
        return True

    return (
        clean(key)
        == BACK_OFFICE_KEY
    )


# ============================================================
# ACTIVATE DOWNLOAD
# ============================================================

@app.post("/api/back-office/activate-download")
async def activate_download(
    request: Request,
    work_id: int | None = None,
    admin_key: str | None = None,
):

    try:

        body = await request.json()

        if isinstance(
            body,
            dict,
        ):

            if work_id is None:

                work_id = body.get(
                    "work_id"
                )

            admin_key = (
                admin_key
                or body.get(
                    "admin_key"
                )
            )

    except Exception:
        pass

    if not key_is_valid(
        clean(admin_key)
    ):

        return json_error(
            "UNAUTHORIZED",
            "Invalid back-office key.",
            401,
        )

    if work_id is None:

        return json_error(
            "WORK_ID_REQUIRED",
            "work_id is required.",
        )

    activated = False

    # Preferred project database function.
    result = business_call(
        "activate_work_download",
        int(work_id),
    )

    if result is not None:

        activated = True

    # Direct SQL fallback.
    if (
        not activated
        and main_table_exists(
            "work_records"
        )
    ):

        columns = main_columns(
            "work_records"
        )

        if "download_activated" in columns:

            try:

                with connect_main_db() as conn:

                    if "job_id" in columns:

                        row = conn.execute(
                            """
                            SELECT job_id
                            FROM work_records
                            WHERE id = ?
                            """,
                            (int(work_id),),
                        ).fetchone()

                        if row:

                            conn.execute(
                                """
                                UPDATE work_records
                                SET download_activated = 0
                                WHERE job_id = ?
                                """,
                                (
                                    row["job_id"],
                                ),
                            )

                    conn.execute(
                        """
                        UPDATE work_records

                        SET download_activated = 1

                        WHERE id = ?
                        """,
                        (
                            int(work_id),
                        ),
                    )

                    if (
                        "download_activated_at"
                        in columns
                    ):

                        conn.execute(
                            """
                            UPDATE work_records

                            SET download_activated_at = ?

                            WHERE id = ?
                            """,
                            (
                                now_iso(),
                                int(work_id),
                            ),
                        )

                    conn.commit()

                    activated = True

            except Exception as exc:

                return json_error(
                    "ACTIVATION_FAILED",
                    "Could not activate download.",
                    500,
                    detail=str(exc),
                )

    if not activated:

        return json_error(
            "WORK_NOT_FOUND",
            "Work record could not be activated.",
            404,
        )

    return {
        "ok": True,
        "success": True,

        "message":
            "Download activated for this work version.",

        "work_id":
            int(work_id),

        "download_activated":
            True,
    }


# ============================================================
# PAYMENT VERIFICATION
# ============================================================

@app.post(
    "/api/customer-care/payment/verify"
)
@app.post(
    "/api/back-office/payment/verify"
)
async def verify_payment(
    request: Request,
    payment_id: str | None = None,
    verified: bool = True,
    note: str | None = None,
):

    try:

        body = await request.json()

        if isinstance(
            body,
            dict,
        ):

            payment_id = (
                payment_id
                or body.get(
                    "payment_id"
                )
            )

            if "verified" in body:

                verified = bool(
                    body.get(
                        "verified"
                    )
                )

            note = (
                note
                or body.get(
                    "note"
                )
                or body.get(
                    "admin_note"
                )
            )

    except Exception:
        pass

    payment_id = clean(
        payment_id
    )

    if not payment_id:

        return json_error(
            "PAYMENT_ID_REQUIRED",
            "payment_id is required.",
        )

    payment = get_payment(
        payment_id
    )

    if not payment:

        return json_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    current, error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    if error or current is None:

        return json_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The document could not be checked.",
            502,
            detail=error,
        )

    if not document_version_is_current(
        payment,
        current,
    ):

        return json_error(
            "PAYMENT_DOCUMENT_CHANGED",
            "This payment belongs to an older document version.",
            409,
        )

    if not verified:

        updated = update_payment_record(
            payment_id,
            status="rejected",
            admin_note=(
                clean(note)
                or None
            ),
        )

        synchronize_payment_to_business(
            updated or payment,
            current,
        )

        return {
            "ok": True,

            "message":
                "Payment marked as rejected.",

            "payment":
                payment_public(
                    updated
                ),

            "paid":
                False,

            "payment_verified":
                False,

            "download_unlocked":
                False,
        }

    updated = update_payment_record(
        payment_id,
        status="verified",
        admin_note=(
            clean(note)
            or None
        ),
        verified_at=now_iso(),
    )

    synchronize_payment_to_business(
        updated or payment,
        current,
    )

    return {
        "ok": True,

        "message":
            "Payment verified. "
            "Download is now unlocked "
            "for this document version.",

        "payment":
            payment_public(
                updated
            ),

        "paid":
            True,

        "payment_verified":
            True,

        "download_unlocked":
            True,
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download_document(
    payment_id: str | None = None,
    job_id: str | None = None,
):

    payment = (
        get_payment(
            clean(
                payment_id
            )
        )
        if clean(
            payment_id
        )
        else None
    )

    if not payment and clean(
        job_id
    ):

        payment = (
            get_latest_payment_for_job(
                clean(
                    job_id
                )
            )
        )

    if not payment:

        return json_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    # SECURITY:
    # Customer reporting payment does NOT unlock download.
    if not payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_error(
            "DOWNLOAD_LOCKED",
            "Download remains locked until payment is verified.",
            403,

            payment_status=payment.get(
                "payment_status"
            ),

            payment_id=payment.get(
                "payment_id"
            ),

            download_unlocked=False,
        )

    current, error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    if error or current is None:

        return json_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be retrieved.",
            502,
            detail=error,
        )

    # SECURITY:
    # Payment only unlocks the exact document version
    # that was paid for.
    if not document_version_is_current(
        payment,
        current,
    ):

        return json_error(
            "DOCUMENT_CHANGED",
            "The document changed after payment verification.",
            409,
            download_unlocked=False,
        )

    pages = normalize_pages(
        current.get(
            "pages"
        )
    )

    if not pages and clean(
        current.get(
            "document_text"
        )
    ):

        pages = [
            clean(
                current.get(
                    "document_text"
                )
            )
        ]

    if not pages:

        return json_error(
            "DOCUMENT_EMPTY",
            "There is no document available for download.",
            409,
        )

    filename = clean(
        current.get(
            "filename"
        )
        or payment.get(
            "document_filename"
        )
    )

    if not filename:

        filename = (
            f"naija_pocket_"
            f"{payment['job_id']}.docx"
        )

    try:

        output_path = make_docx(
            pages,
            filename,
        )

    except Exception as exc:

        return json_error(
            "DOWNLOAD_BUILD_FAILED",
            "The document could not be prepared.",
            500,
            detail=str(exc),
        )

    increment_download(
        payment["payment_id"]
    )

    return FileResponse(
        path=str(
            output_path
        ),

        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        ),

        filename=Path(
            filename
        ).name,

        headers={
            "X-Payment-ID":
                payment["payment_id"],

            "X-Download-Unlocked":
                "true",
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
        f"[PAYMENT API] Version: {APP_VERSION}"
    )

    print(
        f"[PAYMENT API] Payment DB: "
        f"{PAYMENT_DB_PATH}"
    )

    print(
        f"[PAYMENT API] Main DB: "
        f"{MAIN_DB_PATH}"
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
