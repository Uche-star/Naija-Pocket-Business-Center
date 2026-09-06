from __future__ import annotations

import io
import json
import os
import sqlite3
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
# PAYMENT / DOWNLOAD / BACK-OFFICE BRIDGE
#
# IMPORTANT:
# - This file does NOT replace the document API.
# - This file does NOT modify workspace.html.
# - This file does NOT modify review.html.
# - This file does NOT modify database.py.
#
# Main responsibilities:
#   1. Create payment records.
#   2. Report customer payment.
#   3. Allow Customer Care to verify/reject payment.
#   4. Synchronize payment/job information with the main DB.
#   5. Expose back-office jobs.
#   6. Protect document download until verification.
#   7. Preserve document-version protection.
# ============================================================


APP_VERSION = "payment-download-v7-business-db-sync"

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

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CURRENCY = "NGN"
DEFAULT_PAYMENT_METHOD = "bank_transfer"

# Kept for compatibility with installations that still configure
# the existing document API as a document source.
OLD_API_BASE_URL = (
    os.getenv("OLD_API_BASE_URL", "").strip().rstrip("/")
)

INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()


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


def payment_is_reported(status: Any) -> bool:
    return normalize_status(status) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def json_response_error(
    code: str,
    message: str,
    status_code: int = 400,
    **extra: Any,
) -> JSONResponse:
    payload = {
        "ok": False,
        "success": False,
        "error": code,
        "message": message,
    }
    payload.update(extra)
    return JSONResponse(
        payload,
        status_code=status_code,
    )


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


def payment_row_to_dict(
    row: sqlite3.Row | None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def get_payment(
    payment_id: str,
) -> dict[str, Any] | None:
    if not payment_id:
        return None

    with connect_payment_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            LIMIT 1
            """,
            (payment_id,),
        ).fetchone()

    return payment_row_to_dict(row)


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:
    if not job_id:
        return None

    with connect_payment_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    return payment_row_to_dict(row)


def get_all_gateway_payments() -> list[dict[str, Any]]:
    with connect_payment_db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_pending_gateway_payments() -> list[dict[str, Any]]:
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

    return [dict(row) for row in rows]


def create_gateway_payment(
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
                ?, ?, ?, ?, ?, ?, ?, 'pending',
                ?, ?, ?, ?, ?
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


def update_gateway_payment(
    payment_id: str,
    *,
    status: str | None = None,
    payment_reference: str | None = None,
    customer_note: str | None = None,
    admin_note: str | None = None,
    reported_at: str | None = None,
    verified_at: str | None = None,
) -> dict[str, Any] | None:

    current = get_payment(payment_id)

    if not current:
        return None

    fields: list[str] = []
    values: list[Any] = []

    if status is not None:
        fields.append("payment_status = ?")
        values.append(status)

    if payment_reference is not None:
        fields.append("payment_reference = ?")
        values.append(payment_reference)

    if customer_note is not None:
        fields.append("customer_note = ?")
        values.append(customer_note)

    if admin_note is not None:
        fields.append("admin_note = ?")
        values.append(admin_note)

    if reported_at is not None:
        fields.append("reported_at = ?")
        values.append(reported_at)

    if verified_at is not None:
        fields.append("verified_at = ?")
        values.append(verified_at)

    fields.append("updated_at = ?")
    values.append(now_iso())

    values.append(payment_id)

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
# MAIN BUSINESS DATABASE
# ============================================================

def connect_business_db() -> sqlite3.Connection:
    MAIN_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(MAIN_DB_PATH)
    )
    conn.row_factory = sqlite3.Row
    return conn


def business_table_columns(
    table_name: str,
) -> set[str]:

    try:
        with connect_business_db() as conn:
            rows = conn.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()

        return {
            clean(row["name"])
            for row in rows
        }

    except Exception as exc:
        print(
            f"[BUSINESS DB] Could not inspect "
            f"{table_name}: {exc}"
        )
        return set()


def business_db_available() -> bool:
    return MAIN_DB_PATH.exists()


# ============================================================
# IMPORT DATABASE MODULE WHEN AVAILABLE
# ============================================================

try:
    import database as business_database
except Exception as exc:
    business_database = None
    print(
        f"[BUSINESS DB] database.py import unavailable: {exc}"
    )


def call_database_function(
    function_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:

    if business_database is None:
        return None

    function = getattr(
        business_database,
        function_name,
        None,
    )

    if not callable(function):
        return None

    try:
        return function(
            *args,
            **kwargs,
        )
    except TypeError:
        try:
            return function(*args)
        except Exception as exc:
            print(
                f"[BUSINESS DB] {function_name} failed: {exc}"
            )
            return None
    except Exception as exc:
        print(
            f"[BUSINESS DB] {function_name} failed: {exc}"
        )
        return None


# ============================================================
# MAIN DB JOB LOOKUP
# ============================================================

def get_business_job(
    job_id: str,
) -> dict[str, Any] | None:

    job_id = clean(job_id)

    if not job_id:
        return None

    result = call_database_function(
        "get_job",
        job_id,
    )

    if isinstance(result, dict):
        return dict(result)

    if result is not None:
        try:
            return dict(result)
        except Exception:
            pass

    if not business_db_available():
        return None

    columns = business_table_columns("jobs")

    if "id" not in columns:
        return None

    try:
        with connect_business_db() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? LIMIT 1",
                (job_id,),
            ).fetchone()

        return dict(row) if row else None

    except Exception as exc:
        print(
            f"[BUSINESS DB] get job failed: {exc}"
        )
        return None


# ============================================================
# MAIN DB PAYMENT LOOKUP
# ============================================================

def get_business_payment(
    job_id: str,
) -> dict[str, Any] | None:

    result = call_database_function(
        "get_latest_payment",
        job_id,
    )

    if isinstance(result, dict):
        return dict(result)

    if result is not None:
        try:
            return dict(result)
        except Exception:
            pass

    if not business_db_available():
        return None

    columns = business_table_columns("payments")

    if "job_id" not in columns:
        return None

    try:
        with connect_business_db() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM payments
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()

        return dict(row) if row else None

    except Exception as exc:
        print(
            f"[BUSINESS DB] payment lookup failed: {exc}"
        )
        return None


# ============================================================
# CREATE BUSINESS JOB
# ============================================================

def ensure_business_job(
    *,
    job_id: str,
    customer_id: str,
    service: str,
    amount: float,
    customer_name: str = "",
    phone: str = "",
    description: str = "",
    customer_request: str = "",
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any] | None:

    existing = get_business_job(job_id)

    if existing:
        return existing

    # First try database.py if it provides create_job.
    creator = (
        getattr(
            business_database,
            "create_job",
            None,
        )
        if business_database is not None
        else None
    )

    if callable(creator):
        attempts = [
            {
                "job_id": job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone,
                "service_type": service,
                "description": description,
                "customer_request": customer_request,
                "status": "payment_pending",
                "amount": amount,
                "currency": currency,
            },
            {
                "id": job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone,
                "service_type": service,
                "description": description,
                "customer_request": customer_request,
                "status": "payment_pending",
                "amount": amount,
                "currency": currency,
            },
        ]

        for kwargs in attempts:
            try:
                creator(**kwargs)
                found = get_business_job(job_id)

                if found:
                    return found

            except Exception as exc:
                print(
                    "[BUSINESS DB] create_job "
                    f"attempt failed: {exc}"
                )

    # Direct SQL fallback.
    if not business_db_available():
        print(
            "[BUSINESS DB] Main database does not exist."
        )
        return None

    columns = business_table_columns("jobs")

    if not columns:
        return None

    values: dict[str, Any] = {}

    candidate_values = {
        "id": job_id,
        "job_id": job_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "phone": phone,
        "service_type": service,
        "service": service,
        "description": description,
        "customer_request": customer_request,
        "status": "payment_pending",
        "amount": amount,
        "currency": currency,
        "work_reference": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    for column, value in candidate_values.items():
        if column in columns:
            values[column] = value

    # Do not invent a row if the essential identity columns
    # are absent.
    identity_column = (
        "id"
        if "id" in columns
        else (
            "job_id"
            if "job_id" in columns
            else None
        )
    )

    if not identity_column:
        print(
            "[BUSINESS DB] jobs table has no "
            "id/job_id column."
        )
        return None

    try:
        names = list(values.keys())
        placeholders = ", ".join(
            "?" for _ in names
        )

        with connect_business_db() as conn:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO jobs
                ({", ".join(names)})
                VALUES ({placeholders})
                """,
                tuple(values[name] for name in names),
            )
            conn.commit()

        return get_business_job(job_id)

    except Exception as exc:
        print(
            f"[BUSINESS DB] Direct job creation failed: {exc}"
        )
        return None


# ============================================================
# CREATE / UPDATE BUSINESS PAYMENT
# ============================================================

def ensure_business_payment(
    *,
    job_id: str,
    amount: float,
    payment_method: str,
    payment_status: str,
    payment_reference: str = "",
    payment_id: str = "",
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any] | None:

    existing = get_business_payment(job_id)

    if existing:
        existing_id = (
            existing.get("id")
            or existing.get("payment_id")
        )

        updater = (
            getattr(
                business_database,
                "update_payment_status",
                None,
            )
            if business_database is not None
            else None
        )

        if callable(updater):
            try:
                updater(
                    existing_id,
                    payment_status,
                )
            except Exception as exc:
                print(
                    "[BUSINESS DB] update payment status "
                    f"failed: {exc}"
                )

        return get_business_payment(job_id) or existing

    creator = (
        getattr(
            business_database,
            "create_payment",
            None,
        )
        if business_database is not None
        else None
    )

    if callable(creator):
        try:
            creator(
                job_id,
                amount,
                payment_method,
            )

            created = get_business_payment(job_id)

            if created:
                if payment_status != "pending":
                    existing_id = (
                        created.get("id")
                        or created.get("payment_id")
                    )

                    updater = getattr(
                        business_database,
                        "update_payment_status",
                        None,
                    )

                    if callable(updater):
                        try:
                            updater(
                                existing_id,
                                payment_status,
                            )
                        except Exception as exc:
                            print(
                                "[BUSINESS DB] status "
                                f"update failed: {exc}"
                            )

                return (
                    get_business_payment(job_id)
                    or created
                )

        except Exception as exc:
            print(
                "[BUSINESS DB] create_payment failed: "
                f"{exc}"
            )

    if not business_db_available():
        return None

    columns = business_table_columns("payments")

    if not columns:
        return None

    values: dict[str, Any] = {}

    candidate_values = {
        "job_id": job_id,
        "amount": amount,
        "currency": currency,
        "payment_method": payment_method,
        "payment_status": payment_status,
        "payment_reference": payment_reference,
        "payment_date": now_iso(),
        "updated_at": now_iso(),
    }

    if payment_id:
        candidate_values["payment_id"] = payment_id

    for column, value in candidate_values.items():
        if column in columns:
            values[column] = value

    if "job_id" not in values:
        return None

    try:
        names = list(values.keys())
        placeholders = ", ".join(
            "?" for _ in names
        )

        with connect_business_db() as conn:
            conn.execute(
                f"""
                INSERT INTO payments
                ({", ".join(names)})
                VALUES ({placeholders})
                """,
                tuple(values[name] for name in names),
            )
            conn.commit()

        return get_business_payment(job_id)

    except Exception as exc:
        print(
            f"[BUSINESS DB] Direct payment creation failed: {exc}"
        )
        return None


# ============================================================
# UPDATE BUSINESS JOB STATUS
# ============================================================

def update_business_job_status(
    job_id: str,
    status: str,
) -> bool:

    updater = (
        getattr(
            business_database,
            "update_job_status",
            None,
        )
        if business_database is not None
        else None
    )

    if callable(updater):
        try:
            result = updater(
                job_id,
                status,
            )

            if result is not False:
                return True

        except Exception as exc:
            print(
                "[BUSINESS DB] update_job_status failed: "
                f"{exc}"
            )

    if not business_db_available():
        return False

    columns = business_table_columns("jobs")

    if "status" not in columns:
        return False

    id_column = (
        "id"
        if "id" in columns
        else (
            "job_id"
            if "job_id" in columns
            else None
        )
    )

    if not id_column:
        return False

    try:
        with connect_business_db() as conn:
            conn.execute(
                f"""
                UPDATE jobs
                SET status = ?
                WHERE {id_column} = ?
                """,
                (
                    status,
                    job_id,
                ),
            )
            conn.commit()

        return True

    except Exception as exc:
        print(
            f"[BUSINESS DB] status update failed: {exc}"
        )
        return False


# ============================================================
# SYNCHRONIZE PAYMENT TO BUSINESS DATABASE
# ============================================================

def synchronize_payment(
    payment: dict[str, Any],
) -> dict[str, Any]:

    job_id = clean(
        payment.get("job_id")
    )

    if not job_id:
        raise RuntimeError(
            "Payment has no job_id."
        )

    business_job = ensure_business_job(
        job_id=job_id,
        customer_id=clean(
            payment.get("customer_id")
        ),
        service=clean(
            payment.get("service")
        ),
        amount=money(
            payment.get("amount")
        ),
        customer_name=clean(
            payment.get("customer_name")
        ),
        phone=clean(
            payment.get("phone")
        ),
        description=clean(
            payment.get("description")
        ),
        customer_request=clean(
            payment.get("customer_request")
        ),
        currency=clean(
            payment.get("currency")
        ) or DEFAULT_CURRENCY,
    )

    if not business_job:
        raise RuntimeError(
            "Payment was created, but the corresponding "
            "business job could not be created or found "
            f"for job_id={job_id}."
        )

    business_payment = ensure_business_payment(
        job_id=job_id,
        amount=money(
            payment.get("amount")
        ),
        payment_method=clean(
            payment.get("payment_method")
        ) or DEFAULT_PAYMENT_METHOD,
        payment_status=clean(
            payment.get("payment_status")
        ) or "pending",
        payment_reference=clean(
            payment.get("payment_reference")
        ),
        payment_id=clean(
            payment.get("payment_id")
        ),
        currency=clean(
            payment.get("currency")
        ) or DEFAULT_CURRENCY,
    )

    status = normalize_status(
        payment.get("payment_status")
    )

    if payment_is_verified(status):
        update_business_job_status(
            job_id,
            "paid",
        )
    elif payment_is_reported(status):
        update_business_job_status(
            job_id,
            "payment_pending",
        )
    elif status == "rejected":
        update_business_job_status(
            job_id,
            "payment_rejected",
        )
    else:
        update_business_job_status(
            job_id,
            "payment_pending",
        )

    return {
        "job": get_business_job(job_id)
        or business_job,
        "payment": (
            get_business_payment(job_id)
            or business_payment
        ),
    }


# ============================================================
# PAYMENT PUBLIC RESPONSE
#
# IMPORTANT:
# The top-level "payment" value is deliberately a STRING
# payment ID for compatibility with simple payment-page
# JavaScript that may do:
#
#     element.textContent = response.payment
#
# The complete record is available as payment_record.
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    status = clean(
        payment.get("payment_status")
    )

    verified = payment_is_verified(status)

    return {
        "payment_id": clean(
            payment.get("payment_id")
        ),
        "job_id": clean(
            payment.get("job_id")
        ),
        "customer_id": clean(
            payment.get("customer_id")
        ),
        "service": clean(
            payment.get("service")
        ),
        "amount": money(
            payment.get("amount")
        ),
        "currency": clean(
            payment.get("currency")
        ) or DEFAULT_CURRENCY,
        "payment_method": clean(
            payment.get("payment_method")
        ),
        "payment_status": status,
        "payment_reference": clean(
            payment.get("payment_reference")
        ),
        "customer_note": clean(
            payment.get("customer_note")
        ),
        "admin_note": clean(
            payment.get("admin_note")
        ),
        "document_version": clean(
            payment.get("document_version")
        ),
        "document_filename": clean(
            payment.get("document_filename")
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
        "download_count": int(
            payment.get(
                "download_count",
                0,
            )
            or 0
        ),
        "paid": verified,
        "payment_verified": verified,
        "download_unlocked": verified,
    }


def payment_response(
    payment: dict[str, Any] | None,
    *,
    message: str = "",
) -> dict[str, Any]:

    public = payment_public(payment)

    payment_id = (
        public.get("payment_id")
        if public
        else ""
    )

    status = (
        public.get("payment_status")
        if public
        else "none"
    )

    verified = payment_is_verified(status)

    return {
        "ok": True,
        "success": True,

        # STRING, not object.
        "payment": payment_id,

        "payment_id": payment_id,
        "paymentId": payment_id,

        "payment_status": status,
        "status": status,

        "job_id": (
            public.get("job_id")
            if public
            else None
        ),

        "amount": (
            public.get("amount")
            if public
            else 0
        ),

        "currency": (
            public.get("currency")
            if public
            else DEFAULT_CURRENCY
        ),

        "paid": verified,
        "payment_verified": verified,
        "download_unlocked": verified,

        # Full record is available here.
        "payment_record": public,

        "message": message,
    }


# ============================================================
# DOCUMENT SNAPSHOT
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
            else:
                text = item

            text = clean(text)

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

        text = clean(text)

        return [text] if text else []

    text = clean(value)

    return [text] if text else []


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any]:

    raw = payment.get(
        "document_payload"
    ) or "{}"

    try:
        value = json.loads(raw)

        if isinstance(value, dict):
            return value

    except Exception:
        pass

    return {}


def snapshot_document(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id": document.get(
            "job_id"
        ),
        "pages": document.get(
            "pages",
            [],
        ),
        "document_text": document.get(
            "document_text",
            "",
        ),
        "service": document.get(
            "service",
            "",
        ),
        "filename": document.get(
            "filename",
            "",
        ),
        "version_id": document.get(
            "version_id",
            "",
        ),
    }


def document_has_content(
    document: dict[str, Any],
) -> bool:

    pages = normalize_pages(
        document.get("pages")
    )

    text = clean(
        document.get("document_text")
    )

    return bool(
        pages or text
    )


def document_version_is_current(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> bool:

    stored = clean(
        payment.get(
            "document_version"
        )
    )

    current = clean(
        document.get(
            "version_id"
        )
    )

    return bool(
        stored
        and current
        and stored == current
    )


# ============================================================
# DOCUMENT LOOKUP
#
# First attempt:
#   main database / work_records
#
# Compatibility fallback:
#   existing document API
# ============================================================

def get_document_from_main_db(
    job_id: str,
) -> dict[str, Any] | None:

    if not business_db_available():
        return None

    columns = business_table_columns(
        "work_records"
    )

    if not columns or "job_id" not in columns:
        return None

    try:
        with connect_business_db() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM work_records
                WHERE job_id = ?
                ORDER BY id DESC
                """,
                (job_id,),
            ).fetchall()

        if not rows:
            return None

        row = dict(rows[0])

        pages: list[str] = []

        for key in (
            "pages",
            "document_pages",
            "page_texts",
        ):
            if key in row:
                pages = normalize_pages(
                    row.get(key)
                )

                if pages:
                    break

        text = clean(
            row.get("document_text")
            or row.get("content")
            or row.get("text")
            or row.get("work_content")
        )

        if not pages and text:
            pages = [text]

        if not pages:
            return None

        job = get_business_job(job_id) or {}

        version = clean(
            row.get("version")
            or row.get("version_id")
            or row.get("work_version")
        )

        if not version:
            version = clean(
                job.get("work_reference")
            )

        filename = clean(
            row.get("filename")
            or row.get("document_filename")
        )

        if not filename:
            filename = (
                f"naija_pocket_{job_id}.docx"
            )

        return {
            "job_id": job_id,
            "pages": pages,
            "document_text": text,
            "version_id": version,
            "filename": filename,
            "service": clean(
                job.get("service_type")
                or job.get("service")
            ),
            "customer_id": clean(
                job.get("customer_id")
            ),
            "status": clean(
                row.get("work_status")
            ),
            "review_finished": True,
        }

    except Exception as exc:
        print(
            f"[DOCUMENT] Main DB lookup failed: {exc}"
        )
        return None


def fetch_document_from_existing_api(
    job_id: str,
) -> dict[str, Any] | None:

    if not OLD_API_BASE_URL:
        return None

    import urllib.error
    import urllib.parse
    import urllib.request

    paths = (
        "/api/review/pages",
        "/api/review",
    )

    for path in paths:

        try:
            query = urllib.parse.urlencode(
                {
                    "job_id": job_id
                }
            )

            url = (
                OLD_API_BASE_URL
                + path
                + "?"
                + query
            )

            headers = {
                "Accept": "application/json",
                "User-Agent":
                    "NaijaPocketPaymentAPI/2.0",
            }

            if INTERNAL_API_KEY:
                headers[
                    "X-Internal-API-Key"
                ] = INTERNAL_API_KEY

            request = urllib.request.Request(
                url,
                headers=headers,
                method="GET",
            )

            with urllib.request.urlopen(
                request,
                timeout=25,
            ) as response:

                raw = response.read()

            payload = json.loads(
                raw.decode("utf-8")
            )

            if not isinstance(
                payload,
                dict,
            ):
                continue

            candidates = [
                payload,
                payload.get("data"),
                payload.get("job"),
                payload.get("result"),
                payload.get("document"),
                payload.get("review"),
            ]

            for candidate in candidates:

                if not isinstance(
                    candidate,
                    dict,
                ):
                    continue

                pages = normalize_pages(
                    candidate.get("pages")
                    or candidate.get(
                        "document_pages"
                    )
                    or candidate.get(
                        "review_pages"
                    )
                    or candidate.get(
                        "page_texts"
                    )
                )

                text = clean(
                    candidate.get(
                        "document_text"
                    )
                    or candidate.get(
                        "text"
                    )
                    or candidate.get(
                        "content"
                    )
                    or candidate.get(
                        "document"
                    )
                )

                if not pages and text:
                    pages = [text]

                if not pages:
                    continue

                return {
                    "job_id": job_id,
                    "pages": pages,
                    "document_text": text,
                    "version_id": clean(
                        candidate.get(
                            "version_id"
                        )
                        or candidate.get(
                            "document_version"
                        )
                        or candidate.get(
                            "version"
                        )
                    ),
                    "filename": clean(
                        candidate.get(
                            "filename"
                        )
                        or candidate.get(
                            "document_filename"
                        )
                    )
                    or f"naija_pocket_{job_id}.docx",
                    "service": clean(
                        candidate.get(
                            "service"
                        )
                    ),
                    "customer_id": clean(
                        candidate.get(
                            "customer_id"
                        )
                    ),
                    "status": clean(
                        candidate.get(
                            "status"
                        )
                    ),
                    "review_finished": bool(
                        candidate.get(
                            "review_finished"
                        )
                        or candidate.get(
                            "review_complete"
                        )
                        or normalize_status(
                            candidate.get(
                                "status"
                            )
                        )
                        == "review_complete"
                    ),
                }

        except Exception as exc:
            print(
                f"[DOCUMENT] API lookup failed "
                f"for {path}: {exc}"
            )

    return None


def get_current_document(
    job_id: str,
) -> dict[str, Any] | None:

    document = get_document_from_main_db(
        job_id
    )

    if document:
        return document

    document = fetch_document_from_existing_api(
        job_id
    )

    return document


# ============================================================
# BACK OFFICE JOBS
#
# IMPORTANT:
# Do NOT rely only on database.get_back_office_jobs().
# We combine:
#   1. normal business DB jobs
#   2. database.py back-office jobs
#   3. gateway payment jobs
#
# This prevents "Jobs: 0" when a payment exists but a
# particular database helper filters the job out.
# ============================================================

def normalize_business_job(
    job: dict[str, Any],
) -> dict[str, Any]:

    job_id = clean(
        job.get("id")
        or job.get("job_id")
    )

    status = clean(
        job.get("status")
    ) or "unknown"

    payment = (
        get_business_payment(job_id)
        if job_id
        else None
    )

    gateway_payment = (
        get_latest_payment_for_job(job_id)
        if job_id
        else None
    )

    selected_payment = (
        payment
        or gateway_payment
    )

    return {
        **job,

        "id": job_id,
        "job_id": job_id,

        "customer_id": clean(
            job.get("customer_id")
        ),

        "customer_name": clean(
            job.get("customer_name")
        ),

        "phone": clean(
            job.get("phone")
        ),

        "service_type": clean(
            job.get("service_type")
            or job.get("service")
        ),

        "description": clean(
            job.get("description")
        ),

        "customer_request": clean(
            job.get("customer_request")
        ),

        "status": status,

        "amount": money(
            job.get("amount")
        ),

        "currency": clean(
            job.get("currency")
        ) or DEFAULT_CURRENCY,

        "payment_id": (
            clean(
                selected_payment.get(
                    "payment_id"
                )
            )
            if selected_payment
            else ""
        ),

        "payment_status": (
            clean(
                selected_payment.get(
                    "payment_status"
                )
            )
            if selected_payment
            else "unpaid"
        ),

        "payment_verified": (
            payment_is_verified(
                selected_payment.get(
                    "payment_status"
                )
            )
            if selected_payment
            else False
        ),

        "paid": (
            payment_is_verified(
                selected_payment.get(
                    "payment_status"
                )
            )
            if selected_payment
            else False
        ),

        "payment": payment_public(
            selected_payment
        ),
    }


def get_back_office_jobs_combined() -> list[dict[str, Any]]:

    merged: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------
    # SOURCE 1: database.py back-office helper
    # --------------------------------------------------------

    normal_jobs = call_database_function(
        "get_back_office_jobs"
    )

    if isinstance(
        normal_jobs,
        (list, tuple),
    ):
        for job in normal_jobs:
            if not isinstance(
                job,
                dict,
            ):
                try:
                    job = dict(job)
                except Exception:
                    continue

            job_id = clean(
                job.get("id")
                or job.get("job_id")
            )

            if job_id:
                merged[job_id] = (
                    normalize_business_job(job)
                )

    # --------------------------------------------------------
    # SOURCE 2: direct jobs table
    # --------------------------------------------------------

    if business_db_available():
        columns = business_table_columns(
            "jobs"
        )

        id_column = (
            "id"
            if "id" in columns
            else (
                "job_id"
                if "job_id" in columns
                else None
            )
        )

        if id_column:
            try:
                with connect_business_db() as conn:
                    rows = conn.execute(
                        f"""
                        SELECT *
                        FROM jobs
                        ORDER BY
                            COALESCE(updated_at, created_at) DESC,
                            {id_column} DESC
                        """
                    ).fetchall()

                for row in rows:
                    job = dict(row)

                    job_id = clean(
                        job.get("id")
                        or job.get("job_id")
                    )

                    if job_id:
                        if job_id in merged:
                            # Preserve the richer back-office
                            # record but refresh payment state.
                            merged[job_id] = (
                                normalize_business_job(
                                    {
                                        **job,
                                        **merged[job_id],
                                    }
                                )
                            )
                        else:
                            merged[job_id] = (
                                normalize_business_job(
                                    job
                                )
                            )

            except Exception as exc:
                print(
                    "[BACK OFFICE] Direct jobs "
                    f"lookup failed: {exc}"
                )

    # --------------------------------------------------------
    # SOURCE 3: payment gateway
    #
    # If a customer successfully reached payment and the
    # gateway contains a job that the main jobs helper did
    # not return, ensure it is visible.
    # --------------------------------------------------------

    gateway_payments = (
        get_all_gateway_payments()
    )

    for payment in gateway_payments:

        job_id = clean(
            payment.get("job_id")
        )

        if not job_id:
            continue

        if job_id not in merged:

            business_job = get_business_job(
                job_id
            )

            if business_job:
                merged[job_id] = (
                    normalize_business_job(
                        business_job
                    )
                )
                continue

            # Last-resort job-shaped record.
            # This ensures the back office can at least
            # see the paid/reported transaction.
            merged[job_id] = {
                "id": job_id,
                "job_id": job_id,
                "customer_id": clean(
                    payment.get(
                        "customer_id"
                    )
                ),
                "customer_name": "",
                "phone": "",
                "service_type": clean(
                    payment.get(
                        "service"
                    )
                ),
                "description": "",
                "customer_request": "",
                "status": (
                    "paid"
                    if payment_is_verified(
                        payment.get(
                            "payment_status"
                        )
                    )
                    else "payment_pending"
                ),
                "amount": money(
                    payment.get(
                        "amount"
                    )
                ),
                "currency": clean(
                    payment.get(
                        "currency"
                    )
                ) or DEFAULT_CURRENCY,
                "payment_id": clean(
                    payment.get(
                        "payment_id"
                    )
                ),
                "payment_status": clean(
                    payment.get(
                        "payment_status"
                    )
                ),
                "payment_verified":
                    payment_is_verified(
                        payment.get(
                            "payment_status"
                        )
                    ),
                "paid":
                    payment_is_verified(
                        payment.get(
                            "payment_status"
                        )
                    ),
                "payment":
                    payment_public(
                        payment
                    ),
                "source":
                    "payment_gateway",
            }

    return list(
        merged.values()
    )


# ============================================================
# DOCX DOWNLOAD
# ============================================================

def xml_escape(
    value: str,
) -> str:

    return escape(
        value,
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
            '<w:r>'
            '<w:rPr>'
            '<w:sz w:val="24"/>'
            '</w:rPr>'
            f'<w:t xml:space="preserve">'
            f'{xml_escape(line)}'
            f'</w:t>'
            '</w:r>'
        )

    return (
        "<w:p>"
        + "".join(runs)
        + "</w:p>"
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
                '<w:p>'
                '<w:r>'
                '<w:br w:type="page"/>'
                '</w:r>'
                '</w:p>'
            )

        body_parts.append(
            paragraph_xml(page)
        )

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {''.join(body_parts)}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>
    </w:sectPr>
  </w:body>
</w:document>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>
        <w:sz w:val="24"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
</w:styles>'''

    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

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
        "success": True,
        "service":
            "Naija Pocket Business Center Payment API",
        "version": APP_VERSION,
        "main_database":
            str(MAIN_DB_PATH),
        "payment_database":
            str(PAYMENT_DB_PATH),
        "main_database_exists":
            MAIN_DB_PATH.exists(),
        "payment_database_exists":
            PAYMENT_DB_PATH.exists(),
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {
        "ok": True,
        "success": True,
        "service": "payment_api",
        "version": APP_VERSION,
        "main_database":
            str(MAIN_DB_PATH),
        "main_database_exists":
            MAIN_DB_PATH.exists(),
        "payment_database":
            str(PAYMENT_DB_PATH),
    }


@app.get("/api/payment/diagnostic")
async def payment_diagnostic():

    jobs = get_back_office_jobs_combined()
    gateway = get_all_gateway_payments()

    return {
        "ok": True,
        "success": True,
        "version": APP_VERSION,
        "main_database":
            str(MAIN_DB_PATH),
        "main_database_exists":
            MAIN_DB_PATH.exists(),
        "gateway_payment_count":
            len(gateway),
        "back_office_job_count":
            len(jobs),
        "jobs": jobs,
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
                or body.get("job_id")
            )

            customer_id = (
                customer_id
                or body.get("customer_id")
            )

            service = (
                service
                or body.get("service")
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

    job_id = clean(job_id)
    customer_id = clean(customer_id)
    service = clean(service)

    method = (
        clean(payment_method)
        or DEFAULT_PAYMENT_METHOD
    )

    if not job_id:
        return json_response_error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
            400,
        )

    document = get_current_document(
        job_id
    )

    if not document:

        # A payment must remain tied to a real job.
        # Do not manufacture a payment without a document.
        return json_response_error(
            "DOCUMENT_NOT_FOUND",
            "The document for this job could not be found.",
            404,
            job_id=job_id,
        )

    if not document_has_content(
        document
    ):
        return json_response_error(
            "DOCUMENT_EMPTY",
            "There is no completed document available for payment.",
            409,
            job_id=job_id,
        )

    document_version = clean(
        document.get(
            "version_id"
        )
    )

    if not document_version:
        # Compatibility fallback.
        document_version = "1"

    final_amount = money(
        document.get("amount")
    )

    if final_amount <= 0:
        final_amount = money(
            amount
        )

    if final_amount <= 0:

        existing_job = get_business_job(
            job_id
        )

        if existing_job:
            final_amount = money(
                existing_job.get(
                    "amount"
                )
            )

    if final_amount <= 0:
        return json_response_error(
            "AMOUNT_NOT_AVAILABLE",
            "No payment amount is available for this job.",
            409,
            job_id=job_id,
        )

    # --------------------------------------------------------
    # CRITICAL:
    # Ensure the business job exists BEFORE payment creation.
    # --------------------------------------------------------

    business_job = ensure_business_job(
        job_id=job_id,
        customer_id=(
            customer_id
            or clean(
                document.get(
                    "customer_id"
                )
            )
        ),
        service=(
            service
            or clean(
                document.get(
                    "service"
                )
            )
            or "Business Center Service"
        ),
        amount=final_amount,
    )

    if not business_job:
        return json_response_error(
            "BUSINESS_JOB_CREATE_FAILED",
            "The payment could not be created because the business job could not be recorded in the main database.",
            500,
            job_id=job_id,
            main_database=str(
                MAIN_DB_PATH
            ),
        )

    latest = get_latest_payment_for_job(
        job_id
    )

    if latest:

        same_version = (
            clean(
                latest.get(
                    "document_version"
                )
            )
            == document_version
        )

        latest_status = normalize_status(
            latest.get(
                "payment_status"
            )
        )

        if same_version and latest_status in {
            "pending",
            "reported",
            "verification_pending",
            "awaiting_verification",
            "verified",
            "completed",
            "complete",
            "paid",
        }:

            synchronize_payment(
                latest
            )

            return payment_response(
                latest,
                message=(
                    "Existing payment record returned."
                ),
            )

    payment_id = (
        f"NPB-{uuid.uuid4().hex[:12].upper()}"
    )

    record = create_gateway_payment(
        payment_id=payment_id,
        job_id=job_id,
        customer_id=(
            customer_id
            or clean(
                document.get(
                    "customer_id"
                )
            )
        ),
        service=(
            service
            or clean(
                document.get(
                    "service"
                )
            )
            or "Business Center Service"
        ),
        amount=final_amount,
        currency=DEFAULT_CURRENCY,
        payment_method=method,
        document_version=document_version,
        document_filename=(
            clean(
                document.get(
                    "filename"
                )
            )
            or f"naija_pocket_{job_id}.docx"
        ),
        document_payload=snapshot_document(
            document
        ),
    )

    # --------------------------------------------------------
    # CRITICAL SYNCHRONIZATION
    # --------------------------------------------------------

    try:
        synchronize_payment(
            record
        )

    except Exception as exc:
        print(
            "[PAYMENT CREATE] Synchronization failed: "
            f"{exc}"
        )

        # Do NOT pretend the payment was successfully
        # synchronized with the business database.
        return json_response_error(
            "BUSINESS_SYNC_FAILED",
            "The payment record was created, but it could not be synchronized with the business back office.",
            500,
            payment_id=payment_id,
            job_id=job_id,
            detail=str(exc),
        )

    return payment_response(
        record,
        message=(
            "Payment created successfully. "
            "After making payment, tap I HAVE MADE PAYMENT."
        ),
    )


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
                or body.get(
                    "paymentId"
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
                or body.get("note")
                or body.get("message")
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
        get_payment(payment_id)
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
        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "No payment record was found.",
            404,
        )

    current_document = get_current_document(
        clean(
            payment.get("job_id")
        )
    )

    if current_document:

        if not document_version_is_current(
            payment,
            current_document,
        ):
            return json_response_error(
                "PAYMENT_DOCUMENT_CHANGED",
                "The document changed after this payment was created. A new payment record is required.",
                409,
            )

    current_status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(
        current_status
    ):

        synchronize_payment(
            payment
        )

        return payment_response(
            payment,
            message=(
                "Payment has already been verified."
            ),
        )

    updated = update_gateway_payment(
        payment["payment_id"],
        status="reported",
        payment_reference=(
            clean(
                payment_reference
            )
            or None
        ),
        customer_note=(
            clean(note)
            or None
        ),
        reported_at=now_iso(),
    )

    if not updated:
        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "The payment report could not be saved.",
            500,
        )

    # --------------------------------------------------------
    # CRITICAL:
    # Synchronize reported payment with main DB.
    # --------------------------------------------------------

    try:
        synchronize_payment(
            updated
        )

    except Exception as exc:
        print(
            "[PAYMENT REPORT] Business sync failed: "
            f"{exc}"
        )

        return json_response_error(
            "BUSINESS_SYNC_FAILED",
            "Payment was reported, but the back office synchronization failed.",
            500,
            payment_id=updated.get(
                "payment_id"
            ),
            job_id=updated.get(
                "job_id"
            ),
            detail=str(exc),
        )

    return payment_response(
        updated,
        message=(
            "Payment report received. "
            "Customer Care must verify the payment "
            "before download is unlocked."
        ),
    )


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    payment_id: str | None = None,
    job_id: str | None = None,
):

    payment = (
        get_payment(
            clean(payment_id)
        )
        if clean(payment_id)
        else None
    )

    if not payment and clean(job_id):
        payment = (
            get_latest_payment_for_job(
                clean(job_id)
            )
        )

    if not payment:
        return {
            "ok": True,
            "success": True,
            "payment": "",
            "payment_id": "",
            "payment_status": "none",
            "status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
            "payment_record": None,
        }

    current = get_current_document(
        clean(
            payment.get("job_id")
        )
    )

    if current and not document_version_is_current(
        payment,
        current,
    ):
        return {
            **payment_response(
                payment,
                message=(
                    "This payment belongs to an older document version."
                ),
            ),
            "payment_status":
                "invalid_for_current_document",
            "status":
                "invalid_for_current_document",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    synchronize_payment(
        payment
    )

    payment = (
        get_payment(
            clean(
                payment.get(
                    "payment_id"
                )
            )
        )
        or payment
    )

    return payment_response(
        payment
    )


# ============================================================
# COMPLETE COMPATIBILITY ENDPOINT
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
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
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
@app.get("/api/back-office/payments")
async def customer_care_payments():

    records = (
        get_pending_gateway_payments()
    )

    return {
        "ok": True,
        "success": True,
        "count": len(records),
        "payments": [
            payment_public(record)
            for record in records
        ],
    }


# ============================================================
# CUSTOMER CARE VERIFY
# ============================================================

@app.post("/api/customer-care/payment/verify")
@app.post("/api/back-office/payment/verify")
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
                or body.get(
                    "paymentId"
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
        return json_response_error(
            "PAYMENT_ID_REQUIRED",
            "payment_id is required.",
            400,
        )

    payment = get_payment(
        payment_id
    )

    if not payment:
        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    if not verified:

        updated = update_gateway_payment(
            payment_id,
            status="rejected",
            admin_note=(
                clean(note)
                or None
            ),
        )

        if updated:
            try:
                synchronize_payment(
                    updated
                )
            except Exception as exc:
                print(
                    "[VERIFY] Rejection sync failed: "
                    f"{exc}"
                )

        return payment_response(
            updated,
            message=(
                "Payment marked as rejected."
            ),
        )

    current = get_current_document(
        clean(
            payment.get("job_id")
        )
    )

    if not current:
        return json_response_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be checked.",
            502,
        )

    if not document_version_is_current(
        payment,
        current,
    ):
        return json_response_error(
            "PAYMENT_DOCUMENT_CHANGED",
            "This payment belongs to an older document version and cannot unlock the current document.",
            409,
        )

    updated = update_gateway_payment(
        payment_id,
        status="verified",
        admin_note=(
            clean(note)
            or None
        ),
        verified_at=now_iso(),
    )

    if not updated:
        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Payment verification could not be saved.",
            500,
        )

    # --------------------------------------------------------
    # CRITICAL:
    # Verification must reach main business database too.
    # --------------------------------------------------------

    try:
        synchronize_payment(
            updated
        )

    except Exception as exc:
        print(
            "[VERIFY] Business synchronization failed: "
            f"{exc}"
        )

        return json_response_error(
            "BUSINESS_SYNC_FAILED",
            "Payment was verified in the payment system, but the business database could not be synchronized.",
            500,
            payment_id=payment_id,
            job_id=updated.get(
                "job_id"
            ),
            detail=str(exc),
        )

    return payment_response(
        updated,
        message=(
            "Payment verified. "
            "Download is now unlocked for this document version."
        ),
    )


# ============================================================
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs():

    jobs = (
        get_back_office_jobs_combined()
    )

    return {
        "ok": True,
        "success": True,
        "count": len(jobs),
        "total": len(jobs),
        "jobs": jobs,
    }


# Additional compatibility alias.
@app.get("/api/customer-care/jobs")
async def customer_care_jobs():

    jobs = (
        get_back_office_jobs_combined()
    )

    return {
        "ok": True,
        "success": True,
        "count": len(jobs),
        "total": len(jobs),
        "jobs": jobs,
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
            clean(payment_id)
        )
        if clean(payment_id)
        else None
    )

    if not payment and clean(job_id):
        payment = (
            get_latest_payment_for_job(
                clean(job_id)
            )
        )

    if not payment:
        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    # --------------------------------------------------------
    # SECURITY:
    # Reported/pending payment CANNOT download.
    # --------------------------------------------------------

    if not payment_is_verified(
        status
    ):
        return json_response_error(
            "DOWNLOAD_LOCKED",
            "Download remains locked until Customer Care verifies the payment.",
            403,
            payment_status=status,
            payment_id=payment.get(
                "payment_id"
            ),
            download_unlocked=False,
        )

    job_id_value = clean(
        payment.get("job_id")
    )

    current = get_current_document(
        job_id_value
    )

    if not current:

        # Last-resort snapshot stored at payment creation.
        snapshot = payment_document(
            payment
        )

        if snapshot:
            current = {
                "job_id":
                    job_id_value,
                "pages":
                    snapshot.get(
                        "pages",
                        [],
                    ),
                "document_text":
                    clean(
                        snapshot.get(
                            "document_text"
                        )
                    ),
                "version_id":
                    clean(
                        snapshot.get(
                            "version_id"
                        )
                        or payment.get(
                            "document_version"
                        )
                    ),
                "filename":
                    clean(
                        snapshot.get(
                            "filename"
                        )
                    )
                    or clean(
                        payment.get(
                            "document_filename"
                        )
                    ),
            }

    if not current:
        return json_response_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be retrieved.",
            502,
        )

    # --------------------------------------------------------
    # SECURITY:
    # Payment must match the current document version.
    # --------------------------------------------------------

    if not document_version_is_current(
        payment,
        current,
    ):
        return json_response_error(
            "DOCUMENT_CHANGED",
            "The document changed after payment verification. This payment no longer unlocks the changed document.",
            409,
            download_unlocked=False,
        )

    if not document_has_content(
        current
    ):
        return json_response_error(
            "DOCUMENT_EMPTY",
            "There is no document available for download.",
            409,
        )

    pages = normalize_pages(
        current.get("pages")
    )

    if not pages:
        text = clean(
            current.get(
                "document_text"
            )
        )

        if text:
            pages = [text]

    if not pages:
        return json_response_error(
            "DOCUMENT_EMPTY",
            "There is no document content available for download.",
            409,
        )

    filename = clean(
        current.get(
            "filename"
        )
    )

    if not filename:
        filename = clean(
            payment.get(
                "document_filename"
            )
        )

    if not filename:
        filename = (
            f"naija_pocket_{job_id_value}.docx"
        )

    if not filename.lower().endswith(
        ".docx"
    ):
        filename += ".docx"

    try:
        output_path = make_docx(
            pages,
            filename,
        )

    except Exception as exc:
        return json_response_error(
            "DOWNLOAD_BUILD_FAILED",
            "The document could not be prepared for download.",
            500,
            detail=str(exc),
        )

    increment_download(
        payment["payment_id"]
    )

    return FileResponse(
        path=str(output_path),
        media_type=(
            "application/"
            "vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
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

    init_payment_db()

    print(
        "[PAYMENT API] Startup complete."
    )

    print(
        f"[PAYMENT API] Version: {APP_VERSION}"
    )

    print(
        "[PAYMENT API] Payment database: "
        f"{PAYMENT_DB_PATH}"
    )

    print(
        "[PAYMENT API] Main business database: "
        f"{MAIN_DB_PATH}"
    )

    print(
        "[PAYMENT API] Main DB exists: "
        f"{MAIN_DB_PATH.exists()}"
    )

    print(
        "[PAYMENT API] Existing document API configured: "
        f"{bool(OLD_API_BASE_URL)}"
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
