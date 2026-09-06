from __future__ import annotations

import io
import inspect
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
# NAIJA POCKET BUSINESS CENTER
# PAYMENT API
# ============================================================

APP_VERSION = "payment-download-v7-back-office-safe"

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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default

    try:
        return str(value).strip()
    except Exception:
        return default


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def as_dict(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None

    if isinstance(value, dict):
        return dict(value)

    if isinstance(value, sqlite3.Row):
        return dict(value)

    try:
        return dict(value)
    except Exception:
        return None


def verified_status(value: Any) -> bool:
    return text(value).lower() in {
        "paid",
        "completed",
        "success",
        "successful",
        "verified",
        "confirmed",
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return default


# ============================================================
# PAYMENT DATABASE
# ============================================================

def payment_connection() -> sqlite3.Connection:
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
    conn = payment_connection()

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

        existing_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        migrations = {
            "payment_id": "TEXT",
            "job_id": "TEXT",
            "customer_id": "TEXT",
            "customer_name": "TEXT",
            "phone": "TEXT",
            "service": "TEXT",
            "amount": "REAL DEFAULT 0",
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
            if column not in existing_columns:
                conn.execute(
                    f"""
                    ALTER TABLE payment_orders
                    ADD COLUMN {column} {column_type}
                    """
                )

        conn.commit()

    finally:
        conn.close()


def payment_to_dict(
    row: Optional[sqlite3.Row],
) -> Optional[dict[str, Any]]:
    if row is None:
        return None

    return dict(row)


def get_payment_by_id(
    payment_id: str,
) -> Optional[dict[str, Any]]:
    payment_id = text(payment_id)

    if not payment_id:
        return None

    conn = payment_connection()

    try:
        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            LIMIT 1
            """,
            (payment_id,),
        ).fetchone()

        return payment_to_dict(row)

    finally:
        conn.close()


def get_payment_by_job(
    job_id: str,
) -> Optional[dict[str, Any]]:
    job_id = text(job_id)

    if not job_id:
        return None

    conn = payment_connection()

    try:
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

        return payment_to_dict(row)

    finally:
        conn.close()


def get_all_gateway_payments() -> list[dict[str, Any]]:
    conn = payment_connection()

    try:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        conn.close()


# ============================================================
# MAIN BUSINESS DATABASE
# ============================================================

_business_database = None


def get_business_database():
    global _business_database

    if _business_database is None:
        import database

        _business_database = database

    return _business_database


def main_connection() -> sqlite3.Connection:
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
    job_id = text(job_id)

    if not job_id:
        return None

    try:
        database = get_business_database()

        function = getattr(
            database,
            "get_job",
            None,
        )

        if callable(function):
            result = function(job_id)
            converted = as_dict(result)

            if converted is not None:
                return converted

    except Exception:
        pass

    try:
        conn = main_connection()

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
# ENSURE BUSINESS JOB EXISTS
# ============================================================

def ensure_business_job(
    *,
    job_id: str,
    customer_id: str = "",
    customer_name: str = "",
    phone: str = "",
    service: str = "",
    description: str = "",
    customer_request: str = "",
    amount: float = 0,
    currency: str = "NGN",
    version_id: str = "",
    status: str = "payment_reported",
) -> Optional[dict[str, Any]]:

    job_id = text(job_id)

    if not job_id:
        return None

    existing = get_business_job(job_id)

    if existing is not None:
        return existing

    customer_id = text(customer_id)
    customer_name = text(customer_name)
    phone = text(phone)

    service = (
        text(service)
        or "Business Center Service"
    )

    description = text(description)

    customer_request = (
        text(customer_request)
        or service
    )

    currency = (
        text(currency)
        or "NGN"
    )

    version_id = text(version_id)

    status = (
        text(status)
        or "payment_reported"
    )

    amount_value = safe_float(amount)

    # --------------------------------------------------------
    # FIRST: use database.py create_job if possible.
    # --------------------------------------------------------

    try:
        database = get_business_database()

        create_job = getattr(
            database,
            "create_job",
            None,
        )

        if callable(create_job):
            signature = inspect.signature(
                create_job
            )

            parameter_names = set(
                signature.parameters.keys()
            )

            candidates = {
                "job_id": job_id,
                "id": job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone,
                "service_type": service,
                "service": service,
                "description": description,
                "customer_request": customer_request,
                "status": status,
                "amount": amount_value,
                "currency": currency,
                "work_reference": version_id,
            }

            kwargs = {
                key: value
                for key, value in candidates.items()
                if key in parameter_names
            }

            if kwargs:
                try:
                    create_job(**kwargs)

                    created = get_business_job(
                        job_id
                    )

                    if created is not None:
                        return created

                except Exception:
                    pass

    except Exception:
        pass

    # --------------------------------------------------------
    # SECOND: direct database fallback.
    # --------------------------------------------------------

    try:
        conn = main_connection()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(jobs)"
                ).fetchall()
            }

            values = {
                "id": job_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "phone": phone,
                "service_type": service,
                "description": description,
                "customer_request": customer_request,
                "status": status,
                "amount": amount_value,
                "currency": currency,
                "work_reference": version_id,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }

            insert_values = {
                key: value
                for key, value in values.items()
                if key in columns
            }

            if "id" not in insert_values:
                raise RuntimeError(
                    "jobs table has no id column"
                )

            names = list(
                insert_values.keys()
            )

            placeholders = ", ".join(
                ["?"] * len(names)
            )

            conn.execute(
                f"""
                INSERT INTO jobs
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

    except sqlite3.IntegrityError:
        pass

    except Exception:
        traceback.print_exc()

    return get_business_job(job_id)


# ============================================================
# BUSINESS PAYMENT
# ============================================================

def get_business_payment(
    job_id: str,
) -> Optional[dict[str, Any]]:
    job_id = text(job_id)

    if not job_id:
        return None

    try:
        database = get_business_database()

        function = getattr(
            database,
            "get_latest_payment",
            None,
        )

        if callable(function):
            result = function(job_id)
            converted = as_dict(result)

            if converted is not None:
                return converted

    except Exception:
        pass

    try:
        conn = main_connection()

        try:
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
    *,
    job_id: str,
    amount: float,
    payment_method: str,
    payment_reference: str,
    currency: str,
) -> Optional[dict[str, Any]]:

    existing = get_business_payment(
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
            function(
                job_id,
                amount,
                payment_method,
            )

            result = get_business_payment(
                job_id
            )

            if result is not None:
                return result

    except Exception:
        pass

    # --------------------------------------------------------
    # Direct fallback.
    # --------------------------------------------------------

    try:
        conn = main_connection()

        try:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(payments)"
                ).fetchall()
            }

            payment_id = make_id("PAY")
            timestamp = now_iso()

            values = {
                "id": payment_id,
                "job_id": job_id,
                "amount": safe_float(amount),
                "currency": (
                    text(currency)
                    or "NGN"
                ),
                "payment_method": (
                    text(payment_method)
                    or "reported"
                ),
                "payment_status": "reported",
                "payment_reference": (
                    text(payment_reference)
                    or payment_id
                ),
                "payment_date": timestamp,
                "updated_at": timestamp,
            }

            insert_values = {
                key: value
                for key, value in values.items()
                if key in columns
            }

            if "job_id" not in insert_values:
                raise RuntimeError(
                    "payments table has no job_id column"
                )

            names = list(
                insert_values.keys()
            )

            placeholders = ", ".join(
                ["?"] * len(names)
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

    except Exception:
        traceback.print_exc()

    return get_business_payment(
        job_id
    )


def update_business_payment(
    *,
    job_id: str,
    status: str,
    payment_reference: str = "",
) -> Optional[dict[str, Any]]:

    payment = get_business_payment(
        job_id
    )

    if payment is None:
        return None

    payment_id = text(
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

            return get_business_payment(
                job_id
            )

    except Exception:
        pass

    try:
        conn = main_connection()

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
                values.append(
                    now_iso()
                )

            if updates:
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

    except Exception:
        pass

    return get_business_payment(
        job_id
    )


# ============================================================
# BUSINESS JOB STATUS
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

            updated = get_business_job(
                job_id
            )

            if updated is not None:
                return updated

    except Exception:
        pass

    try:
        conn = main_connection()

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

            if updates:
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
# COMPLETE PAYMENT SYNCHRONIZATION
# ============================================================

def synchronize_payment(
    *,
    job_id: str,
    amount: float,
    payment_status: str,
    payment_method: str = "reported",
    payment_reference: str = "",
    currency: str = "NGN",
    customer_id: str = "",
    customer_name: str = "",
    phone: str = "",
    service: str = "",
    document_title: str = "",
    customer_request: str = "",
    version_id: str = "",
) -> dict[str, Any]:

    job_id = text(job_id)

    if not job_id:
        raise ValueError(
            "job_id is required"
        )

    final_job_status = (
        "paid"
        if verified_status(payment_status)
        else "payment_reported"
    )

    # 1. Guarantee the job exists.
    job = ensure_business_job(
        job_id=job_id,
        customer_id=customer_id,
        customer_name=customer_name,
        phone=phone,
        service=service,
        description=document_title,
        customer_request=(
            customer_request
            or service
        ),
        amount=amount,
        currency=currency,
        version_id=version_id,
        status=final_job_status,
    )

    if job is None:
        raise RuntimeError(
            "The payment was received but the "
            "business job could not be created."
        )

    # 2. Guarantee the main payment exists.
    business_payment = get_business_payment(
        job_id
    )

    if business_payment is None:
        business_payment = create_business_payment(
            job_id=job_id,
            amount=amount,
            payment_method=payment_method,
            payment_reference=payment_reference,
            currency=currency,
        )

    # 3. Synchronize status.
    if business_payment is not None:
        business_payment = update_business_payment(
            job_id=job_id,
            status=(
                "paid"
                if verified_status(payment_status)
                else "reported"
            ),
            payment_reference=payment_reference,
        )

    # 4. Synchronize job status.
    job = update_business_job_status(
        job_id,
        final_job_status,
    )

    return {
        "job": job,
        "business_payment": business_payment,
    }


# ============================================================
# PAYMENT ORDER CREATION
# ============================================================

def create_payment_order(
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
) -> dict[str, Any]:

    job_id = text(job_id)

    if not job_id:
        raise ValueError(
            "job_id is required"
        )

    existing = get_payment_by_job(
        job_id
    )

    if existing is not None:
        synchronize_payment(
            job_id=job_id,
            amount=safe_float(
                existing.get("amount")
                or amount
            ),
            payment_status=(
                existing.get("payment_status")
                or "reported"
            ),
            payment_method=(
                existing.get("payment_method")
                or payment_method
            ),
            payment_reference=(
                existing.get("payment_reference")
                or payment_reference
                or ""
            ),
            currency=(
                existing.get("currency")
                or currency
                or "NGN"
            ),
            customer_id=(
                existing.get("customer_id")
                or customer_id
                or ""
            ),
            customer_name=(
                existing.get("customer_name")
                or customer_name
                or ""
            ),
            phone=(
                existing.get("phone")
                or phone
                or ""
            ),
            service=(
                existing.get("service")
                or service
                or ""
            ),
            document_title=(
                existing.get("document_title")
                or document_title
                or ""
            ),
            customer_request=(
                existing.get("service")
                or service
                or ""
            ),
            version_id=(
                existing.get("version_id")
                or version_id
                or ""
            ),
        )

        return get_payment_by_id(
            text(existing.get("payment_id"))
        ) or existing

    payment_id = make_id("PAY")

    payment_reference = (
        text(payment_reference)
        or payment_id
    )

    timestamp = now_iso()

    conn = payment_connection()

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
                text(customer_id),
                text(customer_name),
                text(phone),
                text(service),
                safe_float(amount),
                text(currency) or "NGN",
                text(payment_method) or "reported",
                "reported",
                payment_reference,
                text(version_id),
                text(document_title),
                text(document_text),
                timestamp,
                timestamp,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    synchronize_payment(
        job_id=job_id,
        amount=safe_float(amount),
        payment_status="reported",
        payment_method=(
            payment_method or "reported"
        ),
        payment_reference=payment_reference,
        currency=currency or "NGN",
        customer_id=customer_id,
        customer_name=customer_name,
        phone=phone,
        service=(
            service
            or "Business Center Service"
        ),
        document_title=document_title,
        customer_request=service,
        version_id=version_id,
    )

    result = get_payment_by_id(
        payment_id
    )

    if result is None:
        raise RuntimeError(
            "Payment was created but could not be retrieved."
        )

    return result


# ============================================================
# PAYMENT UPDATE
# ============================================================

def update_payment_order(
    payment_id: str,
    *,
    status: Optional[str] = None,
    payment_reference: Optional[str] = None,
    version_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:

    payment_id = text(payment_id)

    if not payment_id:
        return None

    current = get_payment_by_id(
        payment_id
    )

    if current is None:
        return None

    new_status = (
        status
        if status is not None
        else current.get("payment_status")
    )

    new_reference = (
        payment_reference
        if payment_reference is not None
        else current.get("payment_reference")
    )

    new_version = (
        version_id
        if version_id is not None
        else current.get("version_id")
    )

    conn = payment_connection()

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
                new_version,
                now_iso(),
                payment_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    try:
        synchronize_payment(
            job_id=current["job_id"],
            amount=safe_float(
                current.get("amount")
            ),
            payment_status=(
                new_status or "reported"
            ),
            payment_method=(
                current.get("payment_method")
                or "reported"
            ),
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
            document_title=(
                current.get("document_title")
                or ""
            ),
            customer_request=(
                current.get("service")
                or ""
            ),
            version_id=(
                new_version or ""
            ),
        )

    except Exception:
        traceback.print_exc()

    return get_payment_by_id(
        payment_id
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():
    init_payment_database()

    print(
        f"{APP_VERSION} started"
    )

    print(
        f"Payment database: {PAYMENT_DB_PATH}"
    )

    print(
        f"Main database: {MAIN_DB_PATH}"
    )


# ============================================================
# BASIC ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "ok": True,
        "service": (
            "Naija Pocket Business Center "
            "Payment API"
        ),
        "version": APP_VERSION,
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
def diagnostic(
    job_id: Optional[str] = Query(
        default=None
    ),
):
    result = {
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
        result["payment"] = get_payment_by_job(
            job_id
        )
        result["business_job"] = get_business_job(
            job_id
        )
        result["business_payment"] = (
            get_business_payment(job_id)
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
# PAYMENT RESPONSE
# ============================================================

def payment_response(
    payment: Optional[dict[str, Any]],
    message: str = "",
) -> dict[str, Any]:

    if payment is None:
        return {
            "ok": False,
            "found": False,
            "message": (
                message
                or "Payment not found."
            ),
            "payment_id": None,
            "paymentId": None,
            "payment": None,
            "payment_record": None,
        }

    payment_id = text(
        payment.get("payment_id")
    )

    raw_status = text(
        payment.get("payment_status")
    ).lower()

    public_status = (
        "paid"
        if verified_status(raw_status)
        else raw_status
    )

    payment_copy = dict(payment)

    payment_copy["id"] = payment_id
    payment_copy["payment_id"] = payment_id
    payment_copy["paymentId"] = payment_id
    payment_copy["public_status"] = public_status

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # "payment" is deliberately the Payment ID STRING.
    #
    # The old version returned an object here. The customer
    # payment page was displaying that object as:
    #
    #     [object Object]
    #
    # The complete payment record remains available under
    # "payment_record".
    # --------------------------------------------------------

    return {
        "ok": True,
        "found": True,
        "message": message,

        "payment_id": payment_id,
        "paymentId": payment_id,

        "job_id": payment.get("job_id"),

        "status": public_status,
        "payment_status": public_status,
        "raw_payment_status": raw_status,

        "payment": payment_id,

        "payment_record": payment_copy,
    }


# ============================================================
# CREATE PAYMENT
# ============================================================

@app.post("/api/payment/create")
def payment_create(
    payload: PaymentCreateRequest,
):
    job_id = text(
        payload.job_id
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    ensure_business_job(
        job_id=job_id,
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        phone=payload.phone,
        service=payload.service,
        description=payload.document_title,
        customer_request=payload.service,
        amount=payload.amount,
        currency=payload.currency,
        version_id=payload.version_id,
        status="payment_reported",
    )

    existing = get_payment_by_job(
        job_id
    )

    if existing is not None:
        return payment_response(
            existing,
            "Payment record already exists.",
        )

    payment = create_payment_order(
        job_id=job_id,
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        phone=payload.phone,
        service=payload.service,
        amount=payload.amount,
        currency=payload.currency,
        payment_method=payload.payment_method,
        payment_reference=payload.payment_reference,
        version_id=payload.version_id,
        document_title=payload.document_title,
        document_text=payload.document_text,
    )

    return payment_response(
        payment,
        "Payment record created.",
    )


# ============================================================
# REPORT PAYMENT
# ============================================================

@app.post("/api/payment/report")
def payment_report(
    payload: PaymentReportRequest,
):
    job_id = text(
        payload.job_id
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    existing = get_payment_by_job(
        job_id
    )

    if existing is not None:
        existing_id = text(
            existing.get("payment_id")
        )

        if not existing_id:
            existing_id = make_id("PAY")

            conn = payment_connection()

            try:
                conn.execute(
                    """
                    UPDATE payment_orders
                    SET
                        payment_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        existing_id,
                        now_iso(),
                        existing["id"],
                    ),
                )

                conn.commit()

            finally:
                conn.close()

        payment = update_payment_order(
            existing_id,
            status="reported",
            payment_reference=(
                payload.payment_reference
                or existing.get(
                    "payment_reference"
                )
                or existing_id
            ),
            version_id=(
                payload.version_id
                or existing.get(
                    "version_id"
                )
                or ""
            ),
        )

    else:
        payment = create_payment_order(
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
            document_title=(
                payload.document_title
            ),
            document_text=(
                payload.document_text
            ),
        )

    if payment is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Payment report could not be created."
            ),
        )

    sync_result = synchronize_payment(
        job_id=job_id,
        amount=safe_float(
            payment.get("amount")
            or payload.amount
        ),
        payment_status="reported",
        payment_method=(
            payment.get("payment_method")
            or payload.payment_method
            or "reported"
        ),
        payment_reference=(
            payment.get("payment_reference")
            or payload.payment_reference
            or ""
        ),
        currency=(
            payment.get("currency")
            or payload.currency
            or "NGN"
        ),
        customer_id=(
            payment.get("customer_id")
            or payload.customer_id
            or ""
        ),
        customer_name=(
            payment.get("customer_name")
            or payload.customer_name
            or ""
        ),
        phone=(
            payment.get("phone")
            or payload.phone
            or ""
        ),
        service=(
            payment.get("service")
            or payload.service
            or "Business Center Service"
        ),
        document_title=(
            payment.get("document_title")
            or payload.document_title
            or ""
        ),
        customer_request=(
            payload.service
            or ""
        ),
        version_id=(
            payment.get("version_id")
            or payload.version_id
            or ""
        ),
    )

    payment = get_payment_by_job(
        job_id
    )

    if payment is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Payment was reported but the "
                "payment record could not be retrieved."
            ),
        )

    response = payment_response(
        payment,
        (
            "Payment reported. Please wait for "
            "Customer Care to verify your payment."
        ),
    )

    response["job"] = sync_result.get(
        "job"
    )

    response["business_payment"] = (
        sync_result.get(
            "business_payment"
        )
    )

    # Explicit string Payment ID.
    response["payment_id"] = text(
        payment.get("payment_id")
    )

    response["paymentId"] = text(
        payment.get("payment_id")
    )

    response["payment"] = text(
        payment.get("payment_id")
    )

    return response


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

    payment = None

    if payment_id:
        payment = get_payment_by_id(
            payment_id
        )

    if payment is None and job_id:
        payment = get_payment_by_job(
            job_id
        )

    if payment is None:
        return {
            "ok": True,
            "found": False,
            "payment_id": None,
            "paymentId": None,
            "payment_status": "pending",
            "status": "pending",
            "payment": None,
            "payment_record": None,
        }

    return payment_response(
        payment
    )


# ============================================================
# COMPLETE PAYMENT
# ============================================================

@app.post("/api/payment/complete")
def payment_complete(
    payload: PaymentCompleteRequest,
):
    payment = None

    if payload.payment_id:
        payment = get_payment_by_id(
            payload.payment_id
        )

    if payment is None and payload.job_id:
        payment = get_payment_by_job(
            payload.job_id
        )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    updated = update_payment_order(
        payment["payment_id"],
        status="verified",
        payment_reference=(
            payload.payment_reference
            or payment.get(
                "payment_reference"
            )
            or payment["payment_id"]
        ),
    )

    if updated is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to complete payment."
            ),
        )

    sync_result = synchronize_payment(
        job_id=updated["job_id"],
        amount=safe_float(
            updated.get("amount")
        ),
        payment_status="verified",
        payment_method=(
            updated.get("payment_method")
            or "reported"
        ),
        payment_reference=(
            updated.get("payment_reference")
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
        document_title=(
            updated.get("document_title")
            or ""
        ),
        customer_request=(
            updated.get("service")
            or ""
        ),
        version_id=(
            updated.get("version_id")
            or ""
        ),
    )

    response = payment_response(
        updated,
        "Payment completed successfully.",
    )

    response["job"] = sync_result.get(
        "job"
    )

    response["business_payment"] = (
        sync_result.get(
            "business_payment"
        )
    )

    return response


# ============================================================
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
def customer_care_payments():
    payments = get_all_gateway_payments()

    for item in payments:
        raw_status = text(
            item.get("payment_status")
        ).lower()

        item["public_status"] = (
            "paid"
            if verified_status(raw_status)
            else raw_status
        )

        item["payment_id"] = text(
            item.get("payment_id")
        )

        item["paymentId"] = item[
            "payment_id"
        ]

    return {
        "ok": True,
        "payments": payments,
        "count": len(payments),
    }


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
def back_office_payments():
    payments = get_all_gateway_payments()

    for item in payments:
        raw_status = text(
            item.get("payment_status")
        ).lower()

        item["public_status"] = (
            "paid"
            if verified_status(raw_status)
            else raw_status
        )

        item["payment_id"] = text(
            item.get("payment_id")
        )

        item["paymentId"] = item[
            "payment_id"
        ]

        business_job = get_business_job(
            text(item.get("job_id"))
        )

        business_payment = get_business_payment(
            text(item.get("job_id"))
        )

        item["business_job"] = business_job
        item["business_payment"] = (
            business_payment
        )

    return {
        "ok": True,
        "payments": payments,
        "count": len(payments),
    }


# ============================================================
# PAYMENT VERIFICATION
# ============================================================

def verify_payment(
    *,
    payment_id: str = "",
    job_id: str = "",
    payment_reference: str = "",
):
    payment = None

    if payment_id:
        payment = get_payment_by_id(
            payment_id
        )

    if payment is None and job_id:
        payment = get_payment_by_job(
            job_id
        )

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment not found",
        )

    actual_payment_id = text(
        payment.get("payment_id")
    )

    if not actual_payment_id:
        actual_payment_id = make_id("PAY")

        conn = payment_connection()

        try:
            conn.execute(
                """
                UPDATE payment_orders
                SET
                    payment_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    actual_payment_id,
                    now_iso(),
                    payment["id"],
                ),
            )

            conn.commit()

        finally:
            conn.close()

        payment = get_payment_by_id(
            actual_payment_id
        )

    updated = update_payment_order(
        actual_payment_id,
        status="verified",
        payment_reference=(
            payment_reference
            or payment.get(
                "payment_reference"
            )
            or actual_payment_id
        ),
    )

    if updated is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to verify payment."
            ),
        )

    sync_result = synchronize_payment(
        job_id=updated["job_id"],
        amount=safe_float(
            updated.get("amount")
        ),
        payment_status="verified",
        payment_method=(
            updated.get("payment_method")
            or "reported"
        ),
        payment_reference=(
            updated.get("payment_reference")
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
        document_title=(
            updated.get("document_title")
            or ""
        ),
        customer_request=(
            updated.get("service")
            or ""
        ),
        version_id=(
            updated.get("version_id")
            or ""
        ),
    )

    response = payment_response(
        updated,
        "Payment verified successfully.",
    )

    response["job"] = sync_result.get(
        "job"
    )

    response["business_payment"] = (
        sync_result.get(
            "business_payment"
        )
    )

    return response


@app.post("/api/customer-care/payment/verify")
def customer_care_verify(
    payload: PaymentVerifyRequest,
):
    return verify_payment(
        payment_id=payload.payment_id,
        job_id=payload.job_id,
        payment_reference=(
            payload.payment_reference
        ),
    )


@app.post("/api/back-office/payment/verify")
def back_office_verify(
    payload: PaymentVerifyRequest,
):
    return verify_payment(
        payment_id=payload.payment_id,
        job_id=payload.job_id,
        payment_reference=(
            payload.payment_reference
        ),
    )


# ============================================================
# BACK OFFICE JOBS
# ============================================================

def normalize_back_office_job(
    value: Any,
) -> Optional[dict[str, Any]]:
    item = as_dict(value)

    if item is None:
        return None

    job_id = text(
        item.get("id")
        or item.get("job_id")
    )

    if not job_id:
        return None

    item["id"] = job_id
    item["job_id"] = job_id

    return item


def build_gateway_job(
    payment: dict[str, Any],
) -> dict[str, Any]:
    job_id = text(
        payment.get("job_id")
    )

    existing = get_business_job(
        job_id
    )

    if existing is not None:
        item = dict(existing)
    else:
        item = {
            "id": job_id,
            "job_id": job_id,
            "customer_id": text(
                payment.get("customer_id")
            ),
            "customer_name": text(
                payment.get("customer_name")
            ),
            "phone": text(
                payment.get("phone")
            ),
            "service_type": (
                text(payment.get("service"))
                or "Business Center Service"
            ),
            "service": (
                text(payment.get("service"))
                or "Business Center Service"
            ),
            "description": text(
                payment.get("document_title")
            ),
            "customer_request": (
                text(payment.get("service"))
                or "Business Center Service"
            ),
            "amount": safe_float(
                payment.get("amount")
            ),
            "currency": (
                text(payment.get("currency"))
                or "NGN"
            ),
            "status": (
                "paid"
                if verified_status(
                    payment.get(
                        "payment_status"
                    )
                )
                else "payment_reported"
            ),
            "created_at": (
                payment.get("created_at")
                or now_iso()
            ),
            "updated_at": (
                payment.get("updated_at")
                or now_iso()
            ),
        }

    return item


@app.get("/api/back-office/jobs")
def back_office_jobs():
    jobs_by_id: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------
    # FIRST SOURCE: normal business Back Office jobs.
    # --------------------------------------------------------

    try:
        database = get_business_database()

        function = getattr(
            database,
            "get_back_office_jobs",
            None,
        )

        if callable(function):
            result = function()

            if result:
                for raw_item in result:
                    item = normalize_back_office_job(
                        raw_item
                    )

                    if item is not None:
                        jobs_by_id[
                            item["id"]
                        ] = item

    except Exception:
        traceback.print_exc()

    # --------------------------------------------------------
    # SECOND SOURCE: payment gateway.
    #
    # This is the critical fix.
    #
    # Every reported/verified payment has a job_id. If the
    # normal Back Office query missed that job, we add it here.
    # --------------------------------------------------------

    try:
        gateway_payments = (
            get_all_gateway_payments()
        )

        for payment in gateway_payments:
            job_id = text(
                payment.get("job_id")
            )

            if not job_id:
                continue

            if job_id not in jobs_by_id:
                jobs_by_id[job_id] = (
                    build_gateway_job(
                        payment
                    )
                )

    except Exception:
        traceback.print_exc()

    # --------------------------------------------------------
    # THIRD SOURCE: enrich every job with payment information.
    # --------------------------------------------------------

    enriched = []

    for job_id, job in jobs_by_id.items():
        item = dict(job)

        gateway_payment = (
            get_payment_by_job(job_id)
        )

        business_payment = (
            get_business_payment(job_id)
        )

        item["payment_gateway"] = (
            gateway_payment
        )

        item["business_payment"] = (
            business_payment
        )

        if gateway_payment is not None:
            payment_id = text(
                gateway_payment.get(
                    "payment_id"
                )
            )

            raw_status = text(
                gateway_payment.get(
                    "payment_status"
                )
            ).lower()

            item["payment_id"] = payment_id
            item["paymentId"] = payment_id

            item["payment_status"] = (
                "paid"
                if verified_status(
                    raw_status
                )
                else raw_status
            )

            item["payment_reference"] = (
                gateway_payment.get(
                    "payment_reference"
                )
            )

            if not item.get("amount"):
                item["amount"] = (
                    gateway_payment.get(
                        "amount"
                    )
                )

            if not item.get("currency"):
                item["currency"] = (
                    gateway_payment.get(
                        "currency"
                    )
                    or "NGN"
                )

        elif business_payment is not None:
            raw_status = text(
                business_payment.get(
                    "payment_status"
                )
            ).lower()

            item["payment_status"] = (
                "paid"
                if verified_status(
                    raw_status
                )
                else raw_status
            )

        else:
            item.setdefault(
                "payment_status",
                "pending",
            )

        enriched.append(item)

    return {
        "ok": True,
        "jobs": enriched,
        "count": len(enriched),
    }


# ============================================================
# DOWNLOAD WORK
# ============================================================

def find_work(
    job_id: str,
    version_id: str = "",
) -> Optional[dict[str, Any]]:

    database = get_business_database()

    # Exact version.
    if version_id:
        try:
            function = getattr(
                database,
                "get_work",
                None,
            )

            if callable(function):
                result = function(
                    version_id
                )

                converted = as_dict(result)

                if converted is not None:
                    return converted

        except Exception:
            pass

    # Activated work.
    try:
        function = getattr(
            database,
            "get_activated_work",
            None,
        )

        if callable(function):
            result = function(job_id)

            converted = as_dict(result)

            if converted is not None:
                return converted

    except Exception:
        pass

    # Latest work.
    try:
        function = getattr(
            database,
            "get_latest_work",
            None,
        )

        if callable(function):
            result = function(job_id)

            converted = as_dict(result)

            if converted is not None:
                return converted

    except Exception:
        pass

    # Direct fallback.
    try:
        conn = main_connection()

        try:
            if version_id:
                row = conn.execute(
                    """
                    SELECT *
                    FROM work_records
                    WHERE id = ?
                    AND job_id = ?
                    LIMIT 1
                    """,
                    (
                        version_id,
                        job_id,
                    ),
                ).fetchone()

                if row is not None:
                    return dict(row)

            row = conn.execute(
                """
                SELECT *
                FROM work_records
                WHERE job_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()

            if row is not None:
                return dict(row)

        finally:
            conn.close()

    except Exception:
        pass

    # Payment gateway document fallback.
    payment = get_payment_by_job(
        job_id
    )

    if payment:
        document_text = text(
            payment.get("document_text")
        )

        if document_text:
            return {
                "id": (
                    version_id
                    or text(
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
                "document_text": document_text,
            }

    return None


def extract_document_text(
    work: dict[str, Any],
) -> str:

    fields = (
        "document_text",
        "content",
        "text",
        "work_content",
        "document_content",
        "body",
    )

    for field in fields:
        value = work.get(field)

        if value:
            result = text(value)

            if result:
                return result

    for field in (
        "storage_reference",
        "storage_data",
        "data",
    ):
        value = work.get(field)

        if not value:
            continue

        try:
            parsed = (
                json.loads(value)
                if isinstance(value, str)
                else value
            )

            if isinstance(parsed, dict):
                for key in fields:
                    result = text(
                        parsed.get(key)
                    )

                    if result:
                        return result

        except Exception:
            pass

    return ""


def build_docx(
    title: str,
    document_text: str,
) -> bytes:

    try:
        from docx import Document
        from docx.shared import Pt

    except Exception as exc:
        raise RuntimeError(
            "python-docx is not installed."
        ) from exc

    document = Document()

    if title:
        paragraph = (
            document.add_paragraph()
        )

        run = paragraph.add_run(
            title
        )

        run.bold = True
        run.font.size = Pt(16)

    for line in document_text.split("\n"):
        paragraph = document.add_paragraph(
            line
        )

        for run in paragraph.runs:
            run.font.size = Pt(11)

    output = io.BytesIO()

    document.save(output)

    return output.getvalue()


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
def download(
    job_id: str = Query(...),
    version_id: str = Query(default=""),
):
    job_id = text(job_id)
    version_id = text(version_id)

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id is required",
        )

    # --------------------------------------------------------
    # PAYMENT SECURITY
    # --------------------------------------------------------

    payment = get_payment_by_job(
        job_id
    )

    if payment is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Payment has not been confirmed."
            ),
        )

    if not verified_status(
        payment.get("payment_status")
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Payment has not been confirmed."
            ),
        )

    # --------------------------------------------------------
    # DOCUMENT
    # --------------------------------------------------------

    work = find_work(
        job_id,
        version_id,
    )

    if work is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "The document version could not be found."
            ),
        )

    document_text = extract_document_text(
        work
    )

    if not document_text:
        raise HTTPException(
            status_code=404,
            detail=(
                "No document content is available for download."
            ),
        )

    title = (
        text(work.get("work_title"))
        or text(work.get("title"))
        or text(payment.get("document_title"))
        or "Naija Pocket Business Center Document"
    )

    try:
        file_bytes = build_docx(
            title,
            document_text,
        )

    except Exception as exc:
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to prepare download: {exc}"
            ),
        )

    safe_filename = (
        title
        .replace("/", "-")
        .replace("\\", "-")
        .replace('"', "")
        .replace("'", "")
        .strip()
    )

    if not safe_filename:
        safe_filename = "document"

    if not safe_filename.lower().endswith(
        ".docx"
    ):
        safe_filename += ".docx"

    return Response(
        content=file_bytes,
        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_filename}"'
            ),
            "X-Payment-Status": "paid",
            "X-Job-ID": job_id,
            "X-Version-ID": version_id,
        },
    )


# ============================================================
# GLOBAL ERROR HANDLER
# ============================================================

@app.exception_handler(Exception)
async def unhandled_exception(
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
        reload=False,
    )
