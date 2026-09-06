from __future__ import annotations

import json
import os
import sqlite3
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field


# ============================================================
# Naija Pocket Business Center
# PAYMENT API
#
# Version:
#     payment-download-v5-safe
#
# PURPOSE
# ------------------------------------------------------------
# 1. Keep the payment gateway/audit database working.
# 2. Synchronize payments into the main business database.
# 3. Make sure a paid/reported job exists in the main database.
# 4. Make Back Office able to see the job immediately after
#    payment is reported.
# 5. Preserve download/version protection.
# 6. Make "verified" compatible with the existing download page
#    by exposing it as "paid" through /api/payment/status.
#
# DO NOT CHANGE:
#     workspace.html
#     review.html
#     database.py
# ============================================================


APP_VERSION = "payment-download-v5-safe"

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

# The document API used by the current document/payment flow.
DOCUMENT_API_BASE_URL = os.getenv(
    "DOCUMENT_API_BASE_URL",
    os.getenv(
        "API_BASE_URL",
        "http://127.0.0.1:8000",
    ),
).rstrip("/")


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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default

    try:
        return str(value).strip()
    except Exception:
        return default


def json_response(
    data: Any,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        content=data,
        status_code=status_code,
    )


def normalize_id(value: Any) -> str:
    return safe_text(value)


def generate_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


# ============================================================
# PAYMENT DATABASE
# ============================================================

def payment_db_connection() -> sqlite3.Connection:
    PAYMENT_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(PAYMENT_DB_PATH),
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_payment_database() -> None:
    conn = payment_db_connection()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE,
                job_id TEXT NOT NULL,
                customer_id TEXT,
                customer_name TEXT,
                phone TEXT,
                service TEXT,
                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'NGN',
                payment_method TEXT,
                payment_status TEXT NOT NULL DEFAULT 'pending',
                payment_reference TEXT,
                version_id TEXT,
                document_title TEXT,
                document_text TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        # Safe migrations for installations created by earlier
        # payment API versions.
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        migrations = {
            "payment_id": "TEXT",
            "customer_id": "TEXT",
            "customer_name": "TEXT",
            "phone": "TEXT",
            "service": "TEXT",
            "currency": "TEXT",
            "payment_method": "TEXT",
            "payment_status": "TEXT",
            "payment_reference": "TEXT",
            "version_id": "TEXT",
            "document_title": "TEXT",
            "document_text": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }

        for column, column_type in migrations.items():
            if column not in columns:
                conn.execute(
                    f"""
                    ALTER TABLE payment_orders
                    ADD COLUMN {column} {column_type}
                    """
                )

        conn.commit()

    finally:
        conn.close()


def payment_row_to_dict(
    row: Optional[sqlite3.Row],
) -> Optional[dict[str, Any]]:
    if row is None:
        return None

    return dict(row)


def get_payment_order(
    payment_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    conn = payment_db_connection()

    try:
        if payment_id:
            row = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE payment_id = ?
                LIMIT 1
                """,
                (payment_id,),
            ).fetchone()

        elif job_id:
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

        else:
            return None

        return payment_row_to_dict(row)

    finally:
        conn.close()


# ============================================================
# MAIN BUSINESS DATABASE
# ============================================================

_business_database = None


def get_business_database():
    """
    Import the current database.py module.

    This intentionally uses the existing project database layer
    rather than replacing database.py.
    """
    global _business_database

    if _business_database is None:
        import database as business_database

        _business_database = business_database

    return _business_database


def business_connection() -> sqlite3.Connection:
    MAIN_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(MAIN_DB_PATH),
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def get_business_job(
    job_id: str,
) -> Optional[dict[str, Any]]:
    job_id = normalize_id(job_id)

    if not job_id:
        return None

    try:
        database = get_business_database()

        job = database.get_job(job_id)

        if job is None:
            return None

        if isinstance(job, sqlite3.Row):
            return dict(job)

        if isinstance(job, dict):
            return dict(job)

        try:
            return dict(job)
        except Exception:
            return None

    except Exception:
        # Fallback directly to the main DB.
        try:
            conn = business_connection()

            try:
                row = conn.execute(
                    """
                    SELECT *
                    FROM jobs
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()

                return (
                    dict(row)
                    if row is not None
                    else None
                )

            finally:
                conn.close()

        except Exception:
            return None


# ============================================================
# MAIN DB JOB CREATION
# ============================================================

def create_missing_business_job(
    job_id: str,
    customer_id: str = "",
    customer_name: str = "",
    phone: str = "",
    service_type: str = "",
    description: str = "",
    customer_request: str = "",
    amount: float = 0,
    currency: str = "NGN",
    work_reference: str = "",
    status: str = "payment_reported",
) -> Optional[dict[str, Any]]:
    """
    Ensures that a payment has a corresponding job in the main
    business database.

    IMPORTANT:
    If the job already exists, absolutely nothing is changed.

    This fixes the situation where:
        payment exists
        but jobs table has no matching job

    which previously caused Back Office to show zero jobs.
    """

    job_id = normalize_id(job_id)

    if not job_id:
        return None

    # --------------------------------------------------------
    # Never duplicate an existing job.
    # --------------------------------------------------------
    existing = get_business_job(job_id)

    if existing is not None:
        return existing

    customer_id = normalize_id(customer_id)
    customer_name = normalize_id(customer_name)
    phone = normalize_id(phone)

    service_type = (
        normalize_id(service_type)
        or "Document Service"
    )

    description = normalize_id(description)

    customer_request = normalize_id(
        customer_request
    )

    work_reference = normalize_id(
        work_reference
    )

    currency = (
        normalize_id(currency)
        or "NGN"
    )

    status = (
        normalize_id(status)
        or "payment_reported"
    )

    try:
        amount_value = float(amount or 0)
    except Exception:
        amount_value = 0.0

    created_at = now_iso()
    updated_at = created_at

    # --------------------------------------------------------
    # First attempt:
    # use the current database.py create_job function if its
    # signature supports the current fields.
    #
    # We inspect the signature so this file can remain compatible
    # with the current database.py without replacing it.
    # --------------------------------------------------------
    try:
        database = get_business_database()

        create_job_function = getattr(
            database,
            "create_job",
            None,
        )

        if callable(create_job_function):
            import inspect

            signature = inspect.signature(
                create_job_function
            )

            parameter_names = set(
                signature.parameters.keys()
            )

            candidate_values = {
                "job_id": job_id,
                "id": job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone,
                "service_type": service_type,
                "description": description,
                "customer_request": customer_request,
                "status": status,
                "amount": amount_value,
                "currency": currency,
                "work_reference": work_reference,
            }

            kwargs = {
                key: value
                for key, value in candidate_values.items()
                if key in parameter_names
            }

            # Only use this route if the function has enough
            # information to create the job.
            if kwargs:
                try:
                    result = create_job_function(
                        **kwargs
                    )

                    created = get_business_job(
                        job_id
                    )

                    if created is not None:
                        return created

                    if isinstance(result, dict):
                        result_copy = dict(result)

                        if normalize_id(
                            result_copy.get("id")
                        ) == job_id:
                            return result_copy

                except Exception:
                    # Fall through to the direct INSERT below.
                    pass

    except Exception:
        pass

    # --------------------------------------------------------
    # Second attempt:
    # direct INSERT into the existing jobs table.
    #
    # This is only used when database.py's create_job cannot
    # safely be called with the available information.
    # --------------------------------------------------------
    try:
        conn = business_connection()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(jobs)"
                ).fetchall()
            }

            values: dict[str, Any] = {
                "id": job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone,
                "service_type": service_type,
                "description": description,
                "customer_request": customer_request,
                "status": status,
                "amount": amount_value,
                "currency": currency,
                "work_reference": work_reference,
                "created_at": created_at,
                "updated_at": updated_at,
            }

            # Only insert columns that actually exist in the
            # current jobs table.
            insert_values = {
                key: value
                for key, value in values.items()
                if key in columns
            }

            if "id" not in insert_values:
                raise RuntimeError(
                    "jobs table does not contain id column"
                )

            names = list(
                insert_values.keys()
            )

            placeholders = ", ".join(
                "?" for _ in names
            )

            sql = f"""
                INSERT INTO jobs
                ({", ".join(names)})
                VALUES
                ({placeholders})
            """

            conn.execute(
                sql,
                [
                    insert_values[name]
                    for name in names
                ],
            )

            conn.commit()

        finally:
            conn.close()

        created = get_business_job(job_id)

        if created is not None:
            return created

    except sqlite3.IntegrityError:
        # Another request may have created it between our initial
        # check and INSERT.
        return get_business_job(job_id)

    except Exception:
        traceback.print_exc()

    return None


# ============================================================
# MAIN DB PAYMENT HELPERS
# ============================================================

def get_business_payment_for_job(
    job_id: str,
) -> Optional[dict[str, Any]]:
    try:
        database = get_business_database()

        function = getattr(
            database,
            "get_latest_payment",
            None,
        )

        if callable(function):
            payment = function(job_id)

            if payment is None:
                return None

            if isinstance(payment, sqlite3.Row):
                return dict(payment)

            if isinstance(payment, dict):
                return dict(payment)

            try:
                return dict(payment)
            except Exception:
                return None

    except Exception:
        pass

    # Fallback.
    try:
        conn = business_connection()

        try:
            row = conn.execute(
                """
                SELECT *
                FROM payments
                WHERE job_id = ?
                ORDER BY
                    COALESCE(payment_date, updated_at) DESC,
                    id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()

            return (
                dict(row)
                if row is not None
                else None
            )

        finally:
            conn.close()

    except Exception:
        return None


def create_business_payment(
    job_id: str,
    amount: float,
    payment_method: str = "reported",
    payment_reference: str = "",
    currency: str = "NGN",
) -> Optional[dict[str, Any]]:
    job_id = normalize_id(job_id)

    if not job_id:
        return None

    existing = get_business_payment_for_job(
        job_id
    )

    if existing is not None:
        return existing

    try:
        database = get_business_database()

        function = getattr(
            database,
            "create_payment",
            None,
        )

        if callable(function):
            result = function(
                job_id,
                amount,
                payment_method,
            )

            payment = get_business_payment_for_job(
                job_id
            )

            if payment is not None:
                return payment

            if isinstance(result, dict):
                return dict(result)

    except Exception:
        pass

    # Fallback direct insert.
    try:
        conn = business_connection()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(payments)"
                ).fetchall()
            }

            payment_id = generate_id("PAY")
            payment_date = now_iso()

            values = {
                "id": payment_id,
                "job_id": job_id,
                "amount": float(amount or 0),
                "currency": currency or "NGN",
                "payment_method": (
                    payment_method or "reported"
                ),
                "payment_status": "reported",
                "payment_reference": (
                    payment_reference or payment_id
                ),
                "payment_date": payment_date,
                "updated_at": payment_date,
            }

            insert_values = {
                key: value
                for key, value in values.items()
                if key in columns
            }

            names = list(
                insert_values.keys()
            )

            placeholders = ", ".join(
                "?" for _ in names
            )

            conn.execute(
                f"""
                INSERT INTO payments
                ({", ".join(names)})
                VALUES
                ({placeholders})
                """,
                [
                    insert_values[name]
                    for name in names
                ],
            )

            conn.commit()

        finally:
            conn.close()

        return get_business_payment_for_job(
            job_id
        )

    except Exception:
        traceback.print_exc()

    return None


def update_business_payment_status(
    job_id: str,
    status: str,
    payment_reference: str = "",
) -> Optional[dict[str, Any]]:
    """
    Uses the existing database.py payment update function
    whenever possible.
    """

    payment = get_business_payment_for_job(
        job_id
    )

    if payment is None:
        return None

    payment_id = normalize_id(
        payment.get("id")
    )

    try:
        database = get_business_database()

        function = getattr(
            database,
            "update_payment_status",
            None,
        )

        if callable(function) and payment_id:
            function(
                payment_id,
                status,
            )

            return get_business_payment_for_job(
                job_id
            )

    except Exception:
        pass

    # Fallback.
    try:
        conn = business_connection()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(payments)"
                ).fetchall()
            }

            updates = []

            values = []

            if "payment_status" in columns:
                updates.append(
                    "payment_status = ?"
                )
                values.append(status)

            if (
                payment_reference
                and "payment_reference" in columns
            ):
                updates.append(
                    "payment_reference = ?"
                )
                values.append(
                    payment_reference
                )

            if "updated_at" in columns:
                updates.append(
                    "updated_at = ?"
                )
                values.append(now_iso())

            if not updates:
                return payment

            values.append(job_id)

            conn.execute(
                f"""
                UPDATE payments
                SET {", ".join(updates)}
                WHERE job_id = ?
                """,
                values,
            )

            conn.commit()

        finally:
            conn.close()

        return get_business_payment_for_job(
            job_id
        )

    except Exception:
        return payment


# ============================================================
# JOB STATUS
# ============================================================

def update_business_job_status(
    job_id: str,
    status: str,
) -> Optional[dict[str, Any]]:
    try:
        database = get_business_database()

        function = getattr(
            database,
            "update_job_status",
            None,
        )

        if callable(function):
            function(
                job_id,
                status,
            )

            return get_business_job(
                job_id
            )

    except Exception:
        pass

    # Fallback direct SQL.
    try:
        conn = business_connection()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(jobs)"
                ).fetchall()
            }

            updates = []

            values = []

            if "status" in columns:
                updates.append(
                    "status = ?"
                )
                values.append(status)

            if "updated_at" in columns:
                updates.append(
                    "updated_at = ?"
                )
                values.append(now_iso())

            if not updates:
                return get_business_job(job_id)

            values.append(job_id)

            conn.execute(
                f"""
                UPDATE jobs
                SET {", ".join(updates)}
                WHERE id = ?
                """,
                values,
            )

            conn.commit()

        finally:
            conn.close()

    except Exception:
        pass

    return get_business_job(job_id)


# ============================================================
# PAYMENT SYNC
# ============================================================

def is_verified(
    status: Any,
) -> bool:
    normalized = safe_text(
        status
    ).lower()

    return normalized in {
        "paid",
        "completed",
        "success",
        "successful",
        "verified",
        "confirmed",
    }


def sync_business_payment(
    job_id: str,
    amount: float,
    payment_method: str = "reported",
    payment_status: str = "reported",
    payment_reference: str = "",
    currency: str = "NGN",
    customer_id: str = "",
    customer_name: str = "",
    phone: str = "",
    service: str = "",
    description: str = "",
    customer_request: str = "",
    work_reference: str = "",
) -> dict[str, Any]:
    """
    Central synchronization point.

    IMPORTANT FIX:
    If payment arrives with a job_id that is absent from the
    main jobs table, create that job first.

    Existing jobs are never recreated or overwritten.
    """

    job_id = normalize_id(job_id)

    if not job_id:
        raise ValueError(
            "job_id is required"
        )

    # --------------------------------------------------------
    # 1. Ensure job exists.
    # --------------------------------------------------------
    job = get_business_job(job_id)

    if job is None:
        job = create_missing_business_job(
            job_id=job_id,
            customer_id=customer_id,
            customer_name=customer_name,
            phone=phone,
            service_type=service,
            description=description,
            customer_request=customer_request,
            amount=amount,
            currency=currency,
            work_reference=work_reference,
            status=(
                "paid"
                if is_verified(payment_status)
                else "payment_reported"
            ),
        )

    if job is None:
        raise RuntimeError(
            "Unable to create or locate business job"
        )

    # --------------------------------------------------------
    # 2. Ensure payment exists.
    # --------------------------------------------------------
    business_payment = (
        get_business_payment_for_job(
            job_id
        )
    )

    if business_payment is None:
        business_payment = (
            create_business_payment(
                job_id=job_id,
                amount=amount,
                payment_method=payment_method,
                payment_reference=payment_reference,
                currency=currency,
            )
        )

    # --------------------------------------------------------
    # 3. Update payment status.
    # --------------------------------------------------------
    if business_payment is not None:
        business_payment = (
            update_business_payment_status(
                job_id=job_id,
                status=(
                    "paid"
                    if is_verified(payment_status)
                    else "reported"
                ),
                payment_reference=payment_reference,
            )
        )

    # --------------------------------------------------------
    # 4. Update job status.
    # --------------------------------------------------------
    if is_verified(payment_status):
        job = update_business_job_status(
            job_id,
            "paid",
        )
    else:
        job = update_business_job_status(
            job_id,
            "payment_reported",
        )

    return {
        "job": job,
        "payment": business_payment,
    }


# ============================================================
# PAYMENT ORDER INSERT/UPDATE
# ============================================================

def insert_payment(
    *,
    job_id: str,
    customer_id: str = "",
    customer_name: str = "",
    phone: str = "",
    service: str = "",
    amount: float = 0,
    currency: str = "NGN",
    payment_method: str = "reported",
    payment_reference: str = "",
    version_id: str = "",
    document_title: str = "",
    document_text: str = "",
    payment_status: str = "reported",
) -> dict[str, Any]:
    job_id = normalize_id(job_id)

    if not job_id:
        raise ValueError(
            "job_id is required"
        )

    existing = get_payment_order(
        job_id=job_id
    )

    if existing is not None:
        return existing

    payment_id = generate_id(
        "PAY"
    )

    timestamp = now_iso()

    if not payment_reference:
        payment_reference = payment_id

    conn = payment_db_connection()

    try:
        conn.execute(
            """
            INSERT INTO payment_orders (
                payment_id,
                job_id,
                customer_id,
                customer_name,
                phone,
                service,
                amount,
                currency,
                payment_method,
                payment_status,
                payment_reference,
                version_id,
                document_title,
                document_text,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                job_id,
                customer_id,
                customer_name,
                phone,
                service,
                float(amount or 0),
                currency or "NGN",
                payment_method or "reported",
                payment_status or "reported",
                payment_reference,
                version_id,
                document_title,
                document_text,
                timestamp,
                timestamp,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    # --------------------------------------------------------
    # CRITICAL:
    # Synchronize into the main business DB immediately.
    # --------------------------------------------------------
    try:
        sync_business_payment(
            job_id=job_id,
            amount=float(amount or 0),
            payment_method=payment_method,
            payment_status=payment_status,
            payment_reference=payment_reference,
            currency=currency,
            customer_id=customer_id,
            customer_name=customer_name,
            phone=phone,
            service=service,
            description=document_title,
            customer_request=service,
            work_reference=version_id,
        )
    except Exception:
        traceback.print_exc()

    result = get_payment_order(
        payment_id=payment_id
    )

    if result is None:
        raise RuntimeError(
            "Payment was created but could not be retrieved"
        )

    return result


def update_payment(
    payment_id: str,
    *,
    payment_status: Optional[str] = None,
    payment_reference: Optional[str] = None,
    version_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    payment_id = normalize_id(
        payment_id
    )

    if not payment_id:
        return None

    current = get_payment_order(
        payment_id=payment_id
    )

    if current is None:
        return None

    new_status = (
        payment_status
        if payment_status is not None
        else current.get("payment_status")
    )

    new_reference = (
        payment_reference
        if payment_reference is not None
        else current.get("payment_reference")
    )

    new_version_id = (
        version_id
        if version_id is not None
        else current.get("version_id")
    )

    conn = payment_db_connection()

    try:
        conn.execute(
            """
            UPDATE payment_orders
            SET
                payment_status = ?,
                payment_reference = ?,
                version_id = ?,
                updated_at = ?
            WHERE payment_id = ?
            """,
            (
                new_status,
                new_reference,
                new_version_id,
                now_iso(),
                payment_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    # --------------------------------------------------------
    # Synchronize the changed payment to the main database.
    # --------------------------------------------------------
    try:
        sync_business_payment(
            job_id=current["job_id"],
            amount=float(
                current.get("amount") or 0
            ),
            payment_method=(
                current.get("payment_method")
                or "reported"
            ),
            payment_status=new_status,
            payment_reference=(
                new_reference or ""
            ),
            currency=(
                current.get("currency")
                or "NGN"
            ),
            customer_id=(
                current.get("customer_id")
                or ""
            ),
            customer_name=(
                current.get("customer_name")
                or ""
            ),
            phone=(
                current.get("phone")
                or ""
            ),
            service=(
                current.get("service")
                or ""
            ),
            description=(
                current.get("document_title")
                or ""
            ),
            customer_request=(
                current.get("service")
                or ""
            ),
            work_reference=(
                new_version_id or ""
            ),
        )
    except Exception:
        traceback.print_exc()

    return get_payment_order(
        payment_id=payment_id
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup() -> None:
    init_payment_database()

    print(
        f"{APP_VERSION} started"
    )

    print(
        f"Payment DB: {PAYMENT_DB_PATH}"
    )

    print(
        f"Main DB: {MAIN_DB_PATH}"
    )


# ============================================================
# BASIC ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "Naija Pocket Business Center Payment API",
        "version": APP_VERSION,
        "payment_database": str(
            PAYMENT_DB_PATH
        ),
        "main_database": str(
            MAIN_DB_PATH
        ),
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "status": "healthy",
        "version": APP_VERSION,
    }


@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "status": "healthy",
        "version": APP_VERSION,
    }


# ============================================================
# DIAGNOSTIC
# ============================================================

@app.get("/api/payment/diagnostic")
def payment_diagnostic(
    job_id: Optional[str] = Query(
        default=None
    ),
):
    result: dict[str, Any] = {
        "ok": True,
        "version": APP_VERSION,
        "payment_db": str(
            PAYMENT_DB_PATH
        ),
        "main_db": str(
            MAIN_DB_PATH
        ),
        "payment_db_exists": (
            PAYMENT_DB_PATH.exists()
        ),
        "main_db_exists": (
            MAIN_DB_PATH.exists()
        ),
    }

    if job_id:
        result["job_id"] = job_id

        result["payment_order"] = (
            get_payment_order(
                job_id=job_id
            )
        )

        result["business_job"] = (
            get_business_job(job_id)
        )

        result["business_payment"] = (
            get_business_payment_for_job(
                job_id
            )
        )

    return result


# ============================================================
# REQUEST MODELS
# ============================================================

class PaymentCreateRequest(BaseModel):
    job_id: str

    customer_id: str = ""
    customer_name: str = ""
    phone: str = ""

    service: str = ""

    amount: float = Field(
        default=0,
        ge=0,
    )

    currency: str = "NGN"

    payment_method: str = "reported"

    payment_reference: str = ""

    version_id: str = ""

    document_title: str = ""

    document_text: str = ""


class PaymentReportRequest(BaseModel):
    job_id: str

    customer_id: str = ""
    customer_name: str = ""
    phone: str = ""

    service: str = ""

    amount: float = Field(
        default=0,
        ge=0,
    )

    currency: str = "NGN"

    payment_method: str = "reported"

    payment_reference: str = ""

    version_id: str = ""

    document_title: str = ""

    document_text: str = ""


class PaymentCompleteRequest(BaseModel):
    payment_id: str = ""

    job_id: str = ""

    payment_reference: str = ""


class PaymentVerifyRequest(BaseModel):
    payment_id: str = ""

    job_id: str = ""

    payment_reference: str = ""


# ============================================================
# CREATE PAYMENT
# ============================================================

@app.post("/api/payment/create")
def create_payment(
    payload: PaymentCreateRequest,
):
    job_id = normalize_id(
        payload.job_id
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    # --------------------------------------------------------
    # If the upstream flow already created the job, use it.
    # If not, this call will create the missing job safely.
    # --------------------------------------------------------
    try:
        sync_business_payment(
            job_id=job_id,
            amount=payload.amount,
            payment_method=payload.payment_method,
            payment_status="pending",
            payment_reference=(
                payload.payment_reference
            ),
            currency=payload.currency,
            customer_id=payload.customer_id,
            customer_name=payload.customer_name,
            phone=payload.phone,
            service=payload.service,
            description=payload.document_title,
            customer_request=payload.service,
            work_reference=payload.version_id,
        )
    except Exception:
        traceback.print_exc()

    existing = get_payment_order(
        job_id=job_id
    )

    if existing is not None:
        return {
            "ok": True,
            "payment_id": existing.get(
                "payment_id"
            ),
            "job_id": job_id,
            "status": existing.get(
                "payment_status"
            ),
            "payment": existing,
        }

    # The create route prepares the payment order.
    payment = insert_payment(
        job_id=job_id,
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        phone=payload.phone,
        service=payload.service,
        amount=payload.amount,
        currency=payload.currency,
        payment_method=payload.payment_method,
        payment_reference=(
            payload.payment_reference
        ),
        version_id=payload.version_id,
        document_title=payload.document_title,
        document_text=payload.document_text,
        payment_status="reported",
    )

    return {
        "ok": True,
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": job_id,
        "status": payment.get(
            "payment_status"
        ),
        "payment": payment,
    }


# ============================================================
# REPORT PAYMENT
# ============================================================

@app.post("/api/payment/report")
def report_payment(
    payload: PaymentReportRequest,
):
    job_id = normalize_id(
        payload.job_id
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    existing = get_payment_order(
        job_id=job_id
    )

    if existing is None:
        payment = insert_payment(
            job_id=job_id,
            customer_id=payload.customer_id,
            customer_name=payload.customer_name,
            phone=payload.phone,
            service=payload.service,
            amount=payload.amount,
            currency=payload.currency,
            payment_method=(
                payload.payment_method
                or "reported"
            ),
            payment_reference=(
                payload.payment_reference
            ),
            version_id=payload.version_id,
            document_title=payload.document_title,
            document_text=payload.document_text,
            payment_status="reported",
        )

    else:
        payment = update_payment(
            existing["payment_id"],
            payment_status="reported",
            payment_reference=(
                payload.payment_reference
                or existing.get(
                    "payment_reference"
                )
            ),
            version_id=(
                payload.version_id
                or existing.get("version_id")
            ),
        )

    # --------------------------------------------------------
    # Make absolutely sure the business job exists.
    # --------------------------------------------------------
    sync_result = sync_business_payment(
        job_id=job_id,
        amount=float(
            payload.amount
            or (
                payment.get("amount")
                if payment
                else 0
            )
            or 0
        ),
        payment_method=(
            payload.payment_method
            or "reported"
        ),
        payment_status="reported",
        payment_reference=(
            payload.payment_reference
            or (
                payment.get(
                    "payment_reference"
                )
                if payment
                else ""
            )
            or ""
        ),
        currency=(
            payload.currency
            or (
                payment.get("currency")
                if payment
                else "NGN"
            )
            or "NGN"
        ),
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        phone=payload.phone,
        service=payload.service,
        description=payload.document_title,
        customer_request=payload.service,
        work_reference=payload.version_id,
    )

    payment = get_payment_order(
        job_id=job_id
    )

    return {
        "ok": True,
        "message": (
            "Payment report received. "
            "The job has been synchronized to "
            "Back Office for verification."
        ),
        "job_id": job_id,
        "payment_id": (
            payment.get("payment_id")
            if payment
            else None
        ),
        "status": (
            payment.get("payment_status")
            if payment
            else "reported"
        ),
        "job": sync_result.get(
            "job"
        ),
        "payment": payment,
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
def payment_status(
    job_id: Optional[str] = Query(
        default=None
    ),
    payment_id: Optional[str] = Query(
        default=None
    ),
):
    if not job_id and not payment_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "job_id or payment_id is required"
            ),
        )

    payment = get_payment_order(
        payment_id=payment_id,
        job_id=job_id,
    )

    if payment is None:
        return {
            "ok": True,
            "found": False,
            "payment_status": "pending",
            "status": "pending",
            "payment": None,
        }

    raw_status = safe_text(
        payment.get("payment_status")
    ).lower()

    # --------------------------------------------------------
    # IMPORTANT DOWNLOAD COMPATIBILITY FIX
    #
    # Existing download.html expects:
    #     paid
    #     completed
    #     success
    #
    # The internal verification workflow can use:
    #     verified
    #
    # Therefore expose verified as paid without destroying the
    # original internal record.
    # --------------------------------------------------------
    public_status = (
        "paid"
        if is_verified(raw_status)
        else raw_status
    )

    return {
        "ok": True,
        "found": True,
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": payment.get(
            "job_id"
        ),
        "payment_status": public_status,
        "status": public_status,
        "raw_payment_status": raw_status,
        "amount": payment.get(
            "amount"
        ),
        "currency": payment.get(
            "currency"
        ),
        "payment_reference": payment.get(
            "payment_reference"
        ),
        "payment": payment,
    }


# ============================================================
# COMPLETE PAYMENT
# ============================================================

@app.post("/api/payment/complete")
def complete_payment(
    payload: PaymentCompleteRequest,
):
    payment = None

    if payload.payment_id:
        payment = get_payment_order(
            payment_id=payload.payment_id
        )

    if payment is None and payload.job_id:
        payment = get_payment_order(
            job_id=payload.job_id
        )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    updated = update_payment(
        payment["payment_id"],
        payment_status="verified",
        payment_reference=(
            payload.payment_reference
            or payment.get(
                "payment_reference"
            )
            or ""
        ),
    )

    if updated is None:
        raise HTTPException(
            status_code=500,
            detail="Unable to complete payment",
        )

    # Make sure main DB sees paid.
    sync_result = sync_business_payment(
        job_id=updated["job_id"],
        amount=float(
            updated.get("amount") or 0
        ),
        payment_method=(
            updated.get(
                "payment_method"
            )
            or "reported"
        ),
        payment_status="verified",
        payment_reference=(
            updated.get(
                "payment_reference"
            )
            or ""
        ),
        currency=(
            updated.get("currency")
            or "NGN"
        ),
        customer_id=(
            updated.get("customer_id")
            or ""
        ),
        customer_name=(
            updated.get("customer_name")
            or ""
        ),
        phone=(
            updated.get("phone")
            or ""
        ),
        service=(
            updated.get("service")
            or ""
        ),
        description=(
            updated.get(
                "document_title"
            )
            or ""
        ),
        customer_request=(
            updated.get("service")
            or ""
        ),
        work_reference=(
            updated.get(
                "version_id"
            )
            or ""
        ),
    )

    return {
        "ok": True,
        "message": "Payment completed successfully.",
        "payment": updated,
        "job": sync_result.get(
            "job"
        ),
    }


# ============================================================
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
def customer_care_payments():
    conn = payment_db_connection()

    try:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

        return {
            "ok": True,
            "payments": [
                dict(row)
                for row in rows
            ],
        }

    finally:
        conn.close()


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
def back_office_payments():
    conn = payment_db_connection()

    try:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

        return {
            "ok": True,
            "payments": [
                dict(row)
                for row in rows
            ],
        }

    finally:
        conn.close()


# ============================================================
# VERIFY PAYMENT
# ============================================================

def verify_payment_internal(
    payment_id: str = "",
    job_id: str = "",
    payment_reference: str = "",
):
    payment = None

    if payment_id:
        payment = get_payment_order(
            payment_id=payment_id
        )

    if payment is None and job_id:
        payment = get_payment_order(
            job_id=job_id
        )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    updated = update_payment(
        payment["payment_id"],
        payment_status="verified",
        payment_reference=(
            payment_reference
            or payment.get(
                "payment_reference"
            )
            or ""
        ),
    )

    if updated is None:
        raise HTTPException(
            status_code=500,
            detail="Unable to verify payment",
        )

    # --------------------------------------------------------
    # Synchronize verified payment into main DB as PAID.
    # --------------------------------------------------------
    sync_result = sync_business_payment(
        job_id=updated["job_id"],
        amount=float(
            updated.get("amount") or 0
        ),
        payment_method=(
            updated.get(
                "payment_method"
            )
            or "reported"
        ),
        payment_status="verified",
        payment_reference=(
            updated.get(
                "payment_reference"
            )
            or ""
        ),
        currency=(
            updated.get("currency")
            or "NGN"
        ),
        customer_id=(
            updated.get("customer_id")
            or ""
        ),
        customer_name=(
            updated.get("customer_name")
            or ""
        ),
        phone=(
            updated.get("phone")
            or ""
        ),
        service=(
            updated.get("service")
            or ""
        ),
        description=(
            updated.get(
                "document_title"
            )
            or ""
        ),
        customer_request=(
            updated.get("service")
            or ""
        ),
        work_reference=(
            updated.get(
                "version_id"
            )
            or ""
        ),
    )

    return {
        "ok": True,
        "message": (
            "Payment verified successfully."
        ),
        "payment": updated,
        "job": sync_result.get(
            "job"
        ),
        "business_payment": sync_result.get(
            "payment"
        ),
    }


@app.post("/api/customer-care/payment/verify")
def customer_care_verify_payment(
    payload: PaymentVerifyRequest,
):
    return verify_payment_internal(
        payment_id=payload.payment_id,
        job_id=payload.job_id,
        payment_reference=(
            payload.payment_reference
        ),
    )


@app.post("/api/back-office/payment/verify")
def back_office_verify_payment(
    payload: PaymentVerifyRequest,
):
    return verify_payment_internal(
        payment_id=payload.payment_id,
        job_id=payload.job_id,
        payment_reference=(
            payload.payment_reference
        ),
    )


# ============================================================
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/back-office/jobs")
def back_office_jobs():
    """
    Back Office is job-driven.

    Therefore the important fix is that the payment flow now
    guarantees the corresponding job exists in the main DB.

    This endpoint still uses the existing database.py
    get_back_office_jobs() implementation whenever available.
    """

    database = get_business_database()

    jobs = []

    try:
        function = getattr(
            database,
            "get_back_office_jobs",
            None,
        )

        if callable(function):
            result = function()

            if result is None:
                result = []

            jobs = [
                dict(item)
                if not isinstance(item, dict)
                else item
                for item in result
            ]

    except Exception:
        traceback.print_exc()

    # --------------------------------------------------------
    # Enrich each job with payment gateway information.
    # --------------------------------------------------------
    enriched = []

    for job in jobs:
        item = dict(job)

        job_id = normalize_id(
            item.get("id")
            or item.get("job_id")
        )

        if job_id:
            gateway_payment = (
                get_payment_order(
                    job_id=job_id
                )
            )

            business_payment = (
                get_business_payment_for_job(
                    job_id
                )
            )

            item["payment_gateway"] = (
                gateway_payment
            )

            item["business_payment"] = (
                business_payment
            )

            if gateway_payment:
                raw_status = safe_text(
                    gateway_payment.get(
                        "payment_status"
                    )
                ).lower()

                item[
                    "payment_status"
                ] = (
                    "paid"
                    if is_verified(
                        raw_status
                    )
                    else raw_status
                )

        enriched.append(item)

    return {
        "ok": True,
        "jobs": enriched,
        "count": len(enriched),
    }


# ============================================================
# DOWNLOAD HELPERS
# ============================================================

def get_document_for_download(
    job_id: str,
    version_id: str = "",
) -> dict[str, Any]:
    """
    Obtain the document data used by the existing document flow.

    The payment API does not change document generation logic.
    """

    job_id = normalize_id(job_id)

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    # --------------------------------------------------------
    # First try existing main DB work information.
    # --------------------------------------------------------
    try:
        database = get_business_database()

        if version_id:
            get_work = getattr(
                database,
                "get_work",
                None,
            )

            if callable(get_work):
                work = get_work(
                    version_id
                )

                if work:
                    if isinstance(
                        work,
                        sqlite3.Row,
                    ):
                        work = dict(work)

                    if isinstance(
                        work,
                        dict,
                    ):
                        return {
                            "job_id": job_id,
                            "version_id": (
                                version_id
                            ),
                            "work": work,
                        }

        get_latest_work = getattr(
            database,
            "get_latest_work",
            None,
        )

        if callable(get_latest_work):
            work = get_latest_work(
                job_id
            )

            if work:
                if isinstance(
                    work,
                    sqlite3.Row,
                ):
                    work = dict(work)

                if isinstance(
                    work,
                    dict,
                ):
                    return {
                        "job_id": job_id,
                        "version_id": (
                            version_id
                            or normalize_id(
                                work.get("id")
                            )
                        ),
                        "work": work,
                    }

    except Exception:
        pass

    # --------------------------------------------------------
    # Gateway record may contain the current document text.
    # --------------------------------------------------------
    payment = get_payment_order(
        job_id=job_id
    )

    if payment:
        text = safe_text(
            payment.get(
                "document_text"
            )
        )

        if text:
            return {
                "job_id": job_id,
                "version_id": (
                    version_id
                    or safe_text(
                        payment.get(
                            "version_id"
                        )
                    )
                ),
                "work": {
                    "id": (
                        version_id
                        or safe_text(
                            payment.get(
                                "version_id"
                            )
                        )
                    ),
                    "job_id": job_id,
                    "work_title": (
                        payment.get(
                            "document_title"
                        )
                        or "Document"
                    ),
                    "document_text": text,
                },
            }

    return {
        "job_id": job_id,
        "version_id": version_id,
        "work": None,
    }


def build_docx_from_text(
    text: str,
    title: str = "Naija Pocket Business Center",
) -> bytes:
    """
    Builds a simple DOCX without changing the document
    generation flow elsewhere in the application.
    """

    try:
        from docx import Document
        from docx.shared import Pt

    except Exception as exc:
        raise RuntimeError(
            "python-docx is not installed"
        ) from exc

    document = Document()

    if title:
        paragraph = document.add_paragraph()

        run = paragraph.add_run(
            title
        )

        run.bold = True
        run.font.size = Pt(16)

    for block in text.split(
        "\n"
    ):
        paragraph = document.add_paragraph(
            block
        )

        for run in paragraph.runs:
            run.font.size = Pt(11)

    import io

    output = io.BytesIO()

    document.save(
        output
    )

    return output.getvalue()


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
def download(
    job_id: str = Query(...),
    version_id: str = Query(
        default=""
    ),
):
    job_id = normalize_id(
        job_id
    )

    version_id = normalize_id(
        version_id
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    # --------------------------------------------------------
    # PAYMENT SECURITY CHECK
    # --------------------------------------------------------
    payment = get_payment_order(
        job_id=job_id
    )

    if payment is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Payment has not been confirmed."
            ),
        )

    payment_status = safe_text(
        payment.get(
            "payment_status"
        )
    )

    if not is_verified(
        payment_status
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Payment has not been confirmed."
            ),
        )

    # --------------------------------------------------------
    # DOCUMENT/VERSION CHECK
    # --------------------------------------------------------
    document = get_document_for_download(
        job_id=job_id,
        version_id=version_id,
    )

    work = document.get(
        "work"
    )

    if work is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The document version could not be found."
            ),
        )

    # --------------------------------------------------------
    # Extract document text using the current work structure.
    # --------------------------------------------------------
    document_text = ""

    possible_text_fields = (
        "document_text",
        "content",
        "text",
        "work_content",
        "document_content",
        "body",
    )

    for field in possible_text_fields:
        value = work.get(field)

        if value:
            document_text = safe_text(
                value
            )

            if document_text:
                break

    # Some installations store content inside a JSON field.
    if not document_text:
        for field in (
            "storage_reference",
            "storage_data",
            "data",
        ):
            value = work.get(field)

            if not value:
                continue

            try:
                parsed = json.loads(
                    value
                    if isinstance(
                        value,
                        str,
                    )
                    else json.dumps(
                        value
                    )
                )

                if isinstance(
                    parsed,
                    dict,
                ):
                    for key in (
                        "text",
                        "content",
                        "document_text",
                        "body",
                    ):
                        if parsed.get(key):
                            document_text = safe_text(
                                parsed.get(key)
                            )
                            break

            except Exception:
                pass

            if document_text:
                break

    if not document_text:
        raise HTTPException(
            status_code=404,
            detail=(
                "No document content is available for download."
            ),
        )

    title = (
        safe_text(
            work.get(
                "work_title"
            )
        )
        or safe_text(
            work.get(
                "title"
            )
        )
        or safe_text(
            payment.get(
                "document_title"
            )
        )
        or "Naija Pocket Business Center Document"
    )

    # --------------------------------------------------------
    # Generate the downloadable DOCX.
    # --------------------------------------------------------
    try:
        file_bytes = build_docx_from_text(
            document_text,
            title,
        )

    except Exception as exc:
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to prepare download: {exc}"
            ),
        )

    filename = (
        f"{title[:60]}"
        .replace(
            "/",
            "-",
        )
        .replace(
            "\\",
            "-",
        )
        .replace(
            '"',
            "",
        )
        .replace(
            "'",
            "",
        )
        .strip()
        or "document"
    )

    if not filename.lower().endswith(
        ".docx"
    ):
        filename += ".docx"

    return Response(
        content=file_bytes,
        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
            "X-Payment-Status": "paid",
            "X-Job-ID": job_id,
            "X-Version-ID": version_id,
        },
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request,
    exc: Exception,
):
    traceback.print_exc()

    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": (
                "Payment API internal error"
            ),
            "detail": str(exc),
            "version": APP_VERSION,
        },
    )


# ============================================================
# LOCAL DEVELOPMENT
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
        reload=False,
    )
