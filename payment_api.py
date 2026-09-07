from __future__ import annotations

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
# NAija Pocket Business Center
# COMPLETE PAYMENT / DOWNLOAD API
# ============================================================

APP_VERSION = "payment-download-v9-exact-document"

PAYMENT_DB_PATH = os.getenv(
    "PAYMENT_DB_PATH",
    "payment_gateway.db",
)

MAIN_DB_PATH = os.getenv(
    "MAIN_DB_PATH",
    "naija_pocket_business.db",
)

DOWNLOAD_DIR = Path(
    os.getenv("DOWNLOAD_DIR", "downloads")
)

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DOCUMENT_API_BASE_URL = (
    os.getenv("DOCUMENT_API_BASE_URL")
    or os.getenv("OLD_API_BASE_URL")
    or ""
).rstrip("/")

INTERNAL_API_KEY = (
    os.getenv("INTERNAL_API_KEY")
    or ""
)

BACK_OFFICE_KEY = (
    os.getenv("BACK_OFFICE_ADMIN_KEY")
    or os.getenv("BACK_OFFICE_KEY")
    or os.getenv("ADMIN_KEY")
    or ""
)

DEFAULT_CURRENCY = "NGN"
DEFAULT_PAYMENT_METHOD = "bank_transfer"


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


def clean(value: Any, default: str = "") -> str:
    if value is None:
        return default

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def money(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def normalize_status(value: Any) -> str:
    return clean(value).lower().replace("-", "_").replace(" ", "_")


def payment_is_verified(status: Any) -> bool:
    return normalize_status(status) in {
        "verified",
        "approved",
        "paid",
        "payment_verified",
        "payment_confirmed",
        "confirmed",
        "activated",
        "completed",
    }


def payment_is_reported(status: Any) -> bool:
    return normalize_status(status) in {
        "reported",
        "payment_reported",
        "awaiting_verification",
        "payment_pending",
        "pending_verification",
        "verification_pending",
    }


def payment_is_pending(status: Any) -> bool:
    return normalize_status(status) in {
        "",
        "pending",
        "created",
        "initiated",
        "awaiting_payment",
        "payment_pending",
        "reported",
        "awaiting_verification",
        "pending_verification",
    }


def json_error(
    message: str,
    status_code: int = 400,
    **extra: Any,
) -> JSONResponse:
    payload = {
        "success": False,
        "error": message,
        "message": message,
    }

    payload.update(extra)

    return JSONResponse(
        status_code=status_code,
        content=payload,
    )


async def read_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()

        if isinstance(body, dict):
            return body

    except Exception:
        pass

    return {}


def get_request_value(
    body: dict[str, Any],
    query: dict[str, Any],
    *names: str,
) -> Any:
    for name in names:
        if name in body and body[name] is not None:
            return body[name]

        if name in query and query[name] is not None:
            return query[name]

    return None


# ============================================================
# PAYMENT DATABASE
# ============================================================

def connect_payment_db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        PAYMENT_DB_PATH,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_payment_db() -> None:
    conn = connect_payment_db()

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                payment_id TEXT NOT NULL UNIQUE,

                job_id TEXT NOT NULL,
                customer_id TEXT,

                service TEXT,
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'NGN',

                payment_method TEXT,
                payment_status TEXT DEFAULT 'pending',

                payment_reference TEXT,
                customer_note TEXT,
                admin_note TEXT,

                document_version TEXT,
                document_filename TEXT,

                document_payload TEXT,

                document_saved_path TEXT,
                document_saved_at TEXT,

                created_at TEXT,
                updated_at TEXT,

                reported_at TEXT,
                verified_at TEXT,

                downloaded_at TEXT,
                download_count INTEGER DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payment_orders_job
            ON payment_orders(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payment_orders_created
            ON payment_orders(created_at)
            """
        )

        conn.commit()

    finally:
        conn.close()


init_payment_db()


# ============================================================
# PAYMENT DB HELPERS
# ============================================================

def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    return dict(row)


def get_payment(payment_id: str) -> dict[str, Any] | None:
    payment_id = clean(payment_id)

    if not payment_id:
        return None

    conn = connect_payment_db()

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

        return row_dict(row)

    finally:
        conn.close()


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:
    job_id = clean(job_id)

    if not job_id:
        return None

    conn = connect_payment_db()

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

        return row_dict(row)

    finally:
        conn.close()


def list_all_gateway_payments() -> list[dict[str, Any]]:
    conn = connect_payment_db()

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


def list_pending_payments() -> list[dict[str, Any]]:
    conn = connect_payment_db()

    try:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE LOWER(payment_status) IN (
                'pending',
                'created',
                'reported',
                'awaiting_verification',
                'payment_pending',
                'pending_verification',
                'payment_reported'
            )
            ORDER BY id DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        conn.close()


def create_payment_record(
    *,
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
    payment_id = (
        "PAY-"
        + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8].upper()
    )

    created_at = now_iso()

    conn = connect_payment_db()

    try:
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                job_id,
                customer_id or None,
                service or "",
                amount,
                currency or DEFAULT_CURRENCY,
                payment_method or DEFAULT_PAYMENT_METHOD,
                "pending",
                document_version or "",
                document_filename or "",
                json.dumps(
                    document_payload,
                    ensure_ascii=False,
                ),
                created_at,
                created_at,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    return get_payment(payment_id) or {}


def update_payment_record(
    payment_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    payment_id = clean(payment_id)

    if not payment_id or not fields:
        return get_payment(payment_id)

    allowed = {
        "payment_status",
        "payment_reference",
        "customer_note",
        "admin_note",
        "document_version",
        "document_filename",
        "document_payload",
        "document_saved_path",
        "document_saved_at",
        "updated_at",
        "reported_at",
        "verified_at",
        "downloaded_at",
        "download_count",
    }

    assignments: list[str] = []
    values: list[Any] = []

    for key, value in fields.items():
        if key not in allowed:
            continue

        assignments.append(f"{key} = ?")
        values.append(value)

    if not assignments:
        return get_payment(payment_id)

    if "updated_at" not in fields:
        assignments.append("updated_at = ?")
        values.append(now_iso())

    values.append(payment_id)

    conn = connect_payment_db()

    try:
        conn.execute(
            f"""
            UPDATE payment_orders
            SET {", ".join(assignments)}
            WHERE payment_id = ?
            """,
            values,
        )

        conn.commit()

    finally:
        conn.close()

    return get_payment(payment_id)


def increment_download(payment_id: str) -> None:
    conn = connect_payment_db()

    try:
        conn.execute(
            """
            UPDATE payment_orders
            SET
                download_count =
                    COALESCE(download_count, 0) + 1,
                downloaded_at = ?,
                updated_at = ?
            WHERE payment_id = ?
            """,
            (
                now_iso(),
                now_iso(),
                payment_id,
            ),
        )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# MAIN BUSINESS DATABASE
# ============================================================

try:
    import database as business_database
except Exception:
    business_database = None


def connect_main_db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        MAIN_DB_PATH,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    return conn


def main_table_exists(table_name: str) -> bool:
    try:
        conn = connect_main_db()

        try:
            row = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                AND name = ?
                LIMIT 1
                """,
                (table_name,),
            ).fetchone()

            return row is not None

        finally:
            conn.close()

    except Exception:
        return False


def main_columns(table_name: str) -> list[str]:
    try:
        conn = connect_main_db()

        try:
            rows = conn.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()

            return [
                clean(row["name"])
                for row in rows
            ]

        finally:
            conn.close()

    except Exception:
        return []


def main_rows(
    table_name: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if not main_table_exists(table_name):
        return []

    columns = main_columns(table_name)

    if not columns:
        return []

    try:
        conn = connect_main_db()

        try:
            rows = conn.execute(
                f"""
                SELECT *
                FROM {table_name}
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            return [dict(row) for row in rows]

        finally:
            conn.close()

    except Exception:
        return []


def main_one(
    table_name: str,
    where: str,
    values: tuple[Any, ...],
) -> dict[str, Any] | None:
    if not main_table_exists(table_name):
        return None

    try:
        conn = connect_main_db()

        try:
            row = conn.execute(
                f"""
                SELECT *
                FROM {table_name}
                WHERE {where}
                LIMIT 1
                """,
                values,
            ).fetchone()

            return row_dict(row)

        finally:
            conn.close()

    except Exception:
        return None


def business_call(
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
        return function(*args, **kwargs)
    except Exception:
        return None


# ============================================================
# BUSINESS JOB LOOKUP / SYNCHRONIZATION
# ============================================================

def get_business_job(
    job_id: str,
) -> dict[str, Any] | None:
    result = business_call(
        "get_job",
        job_id,
    )

    if isinstance(result, dict):
        return result

    for table in (
        "jobs",
        "job",
        "work_records",
    ):
        columns = main_columns(table)

        if not columns:
            continue

        possible_id_columns = [
            column
            for column in (
                "id",
                "job_id",
                "jobId",
                "uuid",
            )
            if column in columns
        ]

        for column in possible_id_columns:
            result = main_one(
                table,
                f"{column} = ?",
                (job_id,),
            )

            if result:
                return result

    return None


def get_business_payment(
    job_id: str,
) -> dict[str, Any] | None:
    result = business_call(
        "get_latest_payment",
        job_id,
    )

    if isinstance(result, dict):
        return result

    result = business_call(
        "get_job_payments",
        job_id,
    )

    if isinstance(result, list) and result:
        return result[-1]

    columns = main_columns("payments")

    if columns:
        for column in (
            "job_id",
            "jobId",
        ):
            if column in columns:
                result = main_one(
                    "payments",
                    f"{column} = ?",
                    (job_id,),
                )

                if result:
                    return result

    return None


def get_main_jobs_direct() -> list[dict[str, Any]]:
    return main_rows("jobs")


def get_main_payment_rows() -> list[dict[str, Any]]:
    return main_rows("payments")


def get_main_work_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for table in (
        "work_records",
        "documents",
        "jobs",
    ):
        rows = main_rows(table)

        if rows:
            records.extend(rows)

    return records


def normalize_work_record(
    raw: dict[str, Any],
) -> dict[str, Any]:
    record = dict(raw)

    job_id = clean(
        record.get("job_id")
        or record.get("jobId")
        or record.get("id")
    )

    payload = (
        record.get("document_payload")
        or record.get("payload")
        or record.get("document")
        or record.get("content")
    )

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            pass

    record["job_id"] = job_id
    record["jobId"] = job_id

    if payload is not None:
        record["document_payload"] = payload

    return record


# ============================================================
# DOCUMENT NORMALIZATION
# ============================================================

def normalize_pages(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        result: list[str] = []

        for item in value:
            if isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("body")
                    or ""
                )

                result.append(clean(text))

            else:
                result.append(clean(item))

        return [
            page
            for page in result
            if page
        ]

    if isinstance(value, dict):
        for key in (
            "pages",
            "document_pages",
            "page_content",
        ):
            if key in value:
                return normalize_pages(value[key])

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or ""
        )

        return (
            [clean(text)]
            if clean(text)
            else []
        )

    text = clean(value)

    return [text] if text else []


def safe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value

    return value


def get_current_document_from_main_db(
    job_id: str,
) -> dict[str, Any] | None:
    job_id = clean(job_id)

    if not job_id:
        return None

    records = get_main_work_records()

    matching: list[dict[str, Any]] = []

    for raw in records:
        record = normalize_work_record(raw)

        if clean(
            record.get("job_id")
            or record.get("jobId")
            or record.get("id")
        ) == job_id:
            matching.append(record)

    if not matching:
        return None

    record = matching[-1]

    payload = safe_json(
        record.get("document_payload")
        or record.get("payload")
        or record.get("document")
    )

    pages = normalize_pages(
        record.get("pages")
        or (
            payload.get("pages")
            if isinstance(payload, dict)
            else None
        )
        or record.get("text")
        or record.get("content")
    )

    text = clean(
        record.get("text")
        or record.get("content")
        or record.get("body")
    )

    if not text and pages:
        text = "\n\n".join(pages)

    version = clean(
        record.get("document_version")
        or record.get("version")
        or record.get("revision")
        or record.get("updated_at")
        or record.get("created_at")
    )

    filename = clean(
        record.get("document_filename")
        or record.get("filename")
        or record.get("file_name")
    )

    service = clean(
        record.get("service")
        or record.get("service_name")
        or record.get("serviceName")
    )

    customer_id = clean(
        record.get("customer_id")
        or record.get("customerId")
    )

    amount = money(
        record.get("amount")
        or record.get("price")
        or record.get("total")
    )

    return {
        "job_id": job_id,
        "customer_id": customer_id,
        "service": service,
        "amount": amount,
        "currency": clean(
            record.get("currency"),
            DEFAULT_CURRENCY,
        ),
        "version": version,
        "filename": filename,
        "pages": pages,
        "text": text,
        "payload": (
            payload
            if isinstance(payload, dict)
            else {
                "pages": pages,
                "text": text,
            }
        ),
        "source": "main_database",
        "record": record,
    }


def normalize_document_payload(
    payload: Any,
    job_id: str = "",
) -> dict[str, Any] | None:
    payload = safe_json(payload)

    if payload is None:
        return None

    if isinstance(payload, str):
        text = clean(payload)

        if not text:
            return None

        return {
            "job_id": job_id,
            "pages": [text],
            "text": text,
            "payload": {
                "pages": [text],
                "text": text,
            },
            "version": "",
            "filename": "",
            "service": "",
            "customer_id": "",
            "amount": 0,
            "currency": DEFAULT_CURRENCY,
        }

    if not isinstance(payload, dict):
        return None

    pages = normalize_pages(
        payload.get("pages")
        or payload.get("document_pages")
        or payload.get("page_content")
        or payload.get("content")
        or payload.get("text")
    )

    text = clean(
        payload.get("text")
        or payload.get("content")
        or payload.get("body")
    )

    if not text and pages:
        text = "\n\n".join(pages)

    return {
        "job_id": clean(
            payload.get("job_id")
            or payload.get("jobId")
            or job_id
        ),
        "customer_id": clean(
            payload.get("customer_id")
            or payload.get("customerId")
        ),
        "service": clean(
            payload.get("service")
            or payload.get("service_name")
            or payload.get("serviceName")
        ),
        "amount": money(
            payload.get("amount")
            or payload.get("price")
            or payload.get("total")
        ),
        "currency": clean(
            payload.get("currency"),
            DEFAULT_CURRENCY,
        ),
        "version": clean(
            payload.get("document_version")
            or payload.get("version")
            or payload.get("revision")
            or payload.get("updated_at")
        ),
        "filename": clean(
            payload.get("document_filename")
            or payload.get("filename")
            or payload.get("file_name")
        ),
        "pages": pages,
        "text": text,
        "payload": payload,
    }


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any] | None:
    payload = safe_json(
        payment.get("document_payload")
    )

    return normalize_document_payload(
        payload,
        clean(payment.get("job_id")),
    )


def document_version_is_current(
    payment: dict[str, Any],
    document: dict[str, Any] | None,
) -> bool:
    if not payment or not document:
        return False

    saved_version = clean(
        payment.get("document_version")
    )

    current_version = clean(
        document.get("version")
    )

    if not saved_version or not current_version:
        return True

    return saved_version == current_version


def current_document_is_ready(
    document: dict[str, Any] | None,
) -> bool:
    if not document:
        return False

    pages = normalize_pages(
        document.get("pages")
    )

    text = clean(
        document.get("text")
    )

    return bool(pages or text)


# ============================================================
# FETCH CURRENT DOCUMENT
# ============================================================

def fetch_current_document(
    job_id: str,
) -> dict[str, Any] | None:
    # The persistent business database is the first source.
    document = get_current_document_from_main_db(job_id)

    if current_document_is_ready(document):
        return document

    # Existing payment snapshot can be used when the main
    # database is temporarily unavailable.
    payment = get_latest_payment_for_job(job_id)

    if payment:
        document = payment_document(payment)

        if current_document_is_ready(document):
            document["source"] = "payment_snapshot"
            return document

    return None


def safe_current_document(
    job_id: str,
) -> dict[str, Any] | None:
    try:
        return fetch_current_document(job_id)
    except Exception:
        return None


# ============================================================
# JOB CREATION / BUSINESS SYNCHRONIZATION
# ============================================================

def ensure_business_job(
    job_id: str,
    *,
    service: str = "",
    customer_id: str = "",
    amount: float = 0,
    status: str = "payment_pending",
) -> dict[str, Any] | None:
    existing = get_business_job(job_id)

    if existing:
        return existing

    # Use existing database module if available.
    created = business_call(
        "create_job",
        job_id,
        service,
        customer_id,
        amount,
    )

    if isinstance(created, dict):
        return created

    if not main_table_exists("jobs"):
        return None

    columns = main_columns("jobs")

    if not columns:
        return None

    values: dict[str, Any] = {}

    mappings = {
        "id": job_id,
        "job_id": job_id,
        "jobId": job_id,
        "service": service,
        "service_name": service,
        "serviceName": service,
        "customer_id": customer_id,
        "customerId": customer_id,
        "amount": amount,
        "price": amount,
        "status": status,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    for column, value in mappings.items():
        if column in columns:
            values[column] = value

    if not values:
        return None

    try:
        conn = connect_main_db()

        try:
            names = list(values.keys())

            placeholders = ", ".join(
                ["?"] * len(names)
            )

            conn.execute(
                f"""
                INSERT INTO jobs (
                    {", ".join(names)}
                )
                VALUES ({placeholders})
                """,
                [
                    values[name]
                    for name in names
                ],
            )

            conn.commit()

        finally:
            conn.close()

    except Exception:
        return None

    return get_business_job(job_id)


def update_business_job_status(
    job_id: str,
    status: str,
) -> None:
    job_id = clean(job_id)

    if not job_id:
        return

    # Prefer existing database API.
    for function_name in (
        "update_job_status",
        "set_job_status",
    ):
        function = (
            getattr(
                business_database,
                function_name,
                None,
            )
            if business_database is not None
            else None
        )

        if callable(function):
            try:
                function(job_id, status)
                return
            except Exception:
                pass

    if not main_table_exists("jobs"):
        return

    columns = main_columns("jobs")

    id_column = next(
        (
            column
            for column in (
                "job_id",
                "jobId",
                "id",
            )
            if column in columns
        ),
        None,
    )

    status_column = next(
        (
            column
            for column in (
                "status",
                "job_status",
                "jobStatus",
            )
            if column in columns
        ),
        None,
    )

    if not id_column or not status_column:
        return

    try:
        conn = connect_main_db()

        try:
            conn.execute(
                f"""
                UPDATE jobs
                SET {status_column} = ?
                WHERE {id_column} = ?
                """,
                (
                    status,
                    job_id,
                ),
            )

            if "updated_at" in columns:
                conn.execute(
                    """
                    UPDATE jobs
                    SET updated_at = ?
                    WHERE
                        """
                    + id_column
                    + " = ?",
                    (
                        now_iso(),
                        job_id,
                    ),
                )

            conn.commit()

        finally:
            conn.close()

    except Exception:
        pass


def synchronize_payment_to_business(
    payment: dict[str, Any],
) -> None:
    job_id = clean(
        payment.get("job_id")
    )

    if not job_id:
        return

    status = normalize_status(
        payment.get("payment_status")
    )

    if payment_is_verified(status):
        job_status = "payment_confirmed"
    elif payment_is_reported(status):
        job_status = "payment_pending"
    else:
        job_status = "payment_pending"

    document = payment_document(payment)

    ensure_business_job(
        job_id,
        service=clean(
            payment.get("service")
            or (
                document.get("service")
                if document
                else ""
            )
        ),
        customer_id=clean(
            payment.get("customer_id")
        ),
        amount=money(
            payment.get("amount")
        ),
        status=job_status,
    )

    update_business_job_status(
        job_id,
        job_status,
    )

    # Synchronize with the existing payments module where
    # its functions exist.
    existing = get_business_payment(job_id)

    if existing:
        payment_status = (
            "paid"
            if payment_is_verified(status)
            else "reported"
        )

        payment_id = (
            existing.get("id")
            or existing.get("payment_id")
            or existing.get("paymentId")
        )

        if payment_id is not None:
            business_call(
                "update_payment_status",
                payment_id,
                payment_status,
            )

    else:
        if status in {
            "reported",
            "awaiting_verification",
            "payment_pending",
        }:
            business_call(
                "create_payment",
                job_id,
                money(payment.get("amount")),
                clean(
                    payment.get("payment_method"),
                    DEFAULT_PAYMENT_METHOD,
                ),
            )


# ============================================================
# DOCX GENERATION
# ============================================================
# IMPORTANT:
# This function is used ONLY when the document must be saved
# as the payment snapshot.
#
# Download NEVER calls this function again for an already
# reported payment.
# ============================================================

def make_docx(
    pages: list[str],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    normalized = normalize_pages(pages)

    if not normalized:
        normalized = [""]

    body_parts: list[str] = []

    for index, page in enumerate(normalized):
        paragraphs = clean(page).splitlines()

        if not paragraphs:
            paragraphs = [""]

        for paragraph in paragraphs:
            body_parts.append(
                """
                <w:p>
                    <w:r>
                        <w:rPr>
                            <w:rFonts
                                w:ascii="Arial"
                                w:hAnsi="Arial"
                            />
                            <w:sz w:val="24"/>
                        </w:rPr>
                        <w:t xml:space="preserve">%s</w:t>
                    </w:r>
                </w:p>
                """
                % escape(paragraph)
            )

        if index < len(normalized) - 1:
            body_parts.append(
                """
                <w:p>
                    <w:r>
                        <w:br w:type="page"/>
                    </w:r>
                </w:p>
                """
            )

    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body>
        %s
        <w:sectPr>
            <w:pgSz w:w="11906" w:h="16838"/>
            <w:pgMar
                w:top="1440"
                w:right="1440"
                w:bottom="1440"
                w:left="1440"
            />
        </w:sectPr>
    </w:body>
</w:document>
""" % "".join(body_parts)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types
    xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default
        Extension="rels"
        ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default
        Extension="xml"
        ContentType="application/xml"/>
    <Override
        PartName="/word/document.xml"
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships
    xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship
        Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        Target="word/document.xml"/>
</Relationships>
"""

    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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

    return output_path


# ============================================================
# EXACT DOCUMENT SNAPSHOT
# ============================================================

def safe_filename(filename: str) -> str:
    filename = clean(filename)

    if not filename:
        filename = "Naija_Pocket_Document.docx"

    filename = Path(filename).name

    if not filename.lower().endswith(".docx"):
        filename += ".docx"

    invalid = '<>:"/\\|?*'

    for character in invalid:
        filename = filename.replace(
            character,
            "_",
        )

    return filename


def snapshot_path(
    payment_id: str,
    filename: str,
) -> Path:
    payment_folder = (
        DOWNLOAD_DIR
        / clean(payment_id)
    )

    payment_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    return payment_folder / safe_filename(
        filename
    )


def save_exact_document_snapshot(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:
    """
    Save the exact document represented by the payment snapshot.

    This happens when the customer reports payment.

    The resulting physical file is then reused for download.
    """

    pages = normalize_pages(
        document.get("pages")
    )

    if not pages:
        text = clean(
            document.get("text")
        )

        if text:
            pages = [text]

    if not pages:
        return False, "Document contains no content."

    filename = safe_filename(
        clean(
            document.get("filename")
            or payment.get("document_filename")
        )
    )

    path = snapshot_path(
        clean(payment.get("payment_id")),
        filename,
    )

    try:
        make_docx(
            pages,
            path,
        )

        if not path.exists():
            return False, "Document file was not created."

        if path.stat().st_size <= 0:
            return False, "Document file is empty."

        update_payment_record(
            clean(payment.get("payment_id")),
            document_saved_path=str(path),
            document_saved_at=now_iso(),
            document_filename=filename,
        )

        return True, str(path)

    except Exception as exc:
        return False, str(exc)


def saved_document_exists(
    payment: dict[str, Any],
) -> bool:
    path = clean(
        payment.get("document_saved_path")
    )

    if not path:
        return False

    return Path(path).is_file()


def saved_document_path(
    payment: dict[str, Any],
) -> Path | None:
    path = clean(
        payment.get("document_saved_path")
    )

    if not path:
        return None

    candidate = Path(path)

    if not candidate.is_file():
        return None

    return candidate


# ============================================================
# BACK OFFICE NORMALIZATION
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not payment:
        return None

    status = normalize_status(
        payment.get("payment_status")
    )

    verified = payment_is_verified(status)

    return {
        "id": payment.get("id"),
        "payment_id": payment.get("payment_id"),
        "paymentId": payment.get("payment_id"),
        "job_id": payment.get("job_id"),
        "jobId": payment.get("job_id"),
        "customer_id": payment.get("customer_id"),
        "customerId": payment.get("customer_id"),
        "service": payment.get("service"),
        "amount": money(payment.get("amount")),
        "currency": payment.get("currency")
        or DEFAULT_CURRENCY,
        "payment_method": payment.get("payment_method"),
        "payment_status": status or "pending",
        "paymentStatus": status or "pending",
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
        "document_saved": saved_document_exists(
            payment
        ),
        "document_saved_path": (
            payment.get("document_saved_path")
            if verified
            else None
        ),
        "created_at": payment.get("created_at"),
        "updated_at": payment.get("updated_at"),
        "reported_at": payment.get("reported_at"),
        "verified_at": payment.get("verified_at"),
        "downloaded_at": payment.get("downloaded_at"),
        "download_count": int(
            payment.get("download_count") or 0
        ),
        "paid": verified,
        "verified": verified,

        # The document is not downloadable merely because
        # payment was reported.
        "download_unlocked": verified,
        "downloadUnlocked": verified,
    }


def normalize_back_office_job(
    raw_job: dict[str, Any],
    payment: dict[str, Any] | None = None,
    work_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(raw_job or {})

    if payment:
        payment_status = normalize_status(
            payment.get("payment_status")
        )

        result["payment_id"] = payment.get(
            "payment_id"
        )

        result["paymentId"] = payment.get(
            "payment_id"
        )

        result["payment_status"] = (
            payment_status or "pending"
        )

        result["paymentStatus"] = (
            payment_status or "pending"
        )

        result["payment_verified"] = (
            payment_is_verified(payment_status)
        )

        result["paid"] = (
            payment_is_verified(payment_status)
        )

        result["download_unlocked"] = (
            payment_is_verified(payment_status)
        )

        result["downloadUnlocked"] = (
            payment_is_verified(payment_status)
        )

        result["payment"] = payment_public(
            payment
        )

        if payment_is_verified(payment_status):
            result["status"] = "payment_confirmed"

        elif payment_is_reported(payment_status):
            result["status"] = "payment_pending"

    job_id = clean(
        result.get("job_id")
        or result.get("jobId")
        or result.get("id")
    )

    result["id"] = (
        result.get("id")
        or job_id
    )

    result["job_id"] = job_id
    result["jobId"] = job_id

    if work_record:
        result["work_record"] = work_record

        result["document_version"] = (
            work_record.get("document_version")
            or work_record.get("version")
            or result.get("document_version")
        )

        result["document_filename"] = (
            work_record.get("document_filename")
            or work_record.get("filename")
            or result.get("document_filename")
        )

    return result


def get_business_job_lists() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []

    for function_name in (
        "get_back_office_jobs",
        "get_all_jobs",
    ):
        result = business_call(
            function_name
        )

        if isinstance(result, list):
            jobs.extend(
                item
                for item in result
                if isinstance(item, dict)
            )

    jobs.extend(
        get_main_jobs_direct()
    )

    return jobs


def get_back_office_jobs_combined() -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for raw in get_business_job_lists():
        job_id = clean(
            raw.get("job_id")
            or raw.get("jobId")
            or raw.get("id")
        )

        if job_id:
            merged[job_id] = dict(raw)

    # Every payment-created job must appear in Back Office.
    for payment in list_all_gateway_payments():
        job_id = clean(
            payment.get("job_id")
        )

        if not job_id:
            continue

        if job_id not in merged:
            merged[job_id] = {
                "id": job_id,
                "job_id": job_id,
                "jobId": job_id,
                "service": payment.get(
                    "service"
                ),
                "amount": payment.get(
                    "amount"
                ),
                "currency": payment.get(
                    "currency"
                ),
            }

    # Main payment records can also reveal jobs.
    for payment in get_main_payment_rows():
        job_id = clean(
            payment.get("job_id")
            or payment.get("jobId")
        )

        if job_id and job_id not in merged:
            merged[job_id] = {
                "id": job_id,
                "job_id": job_id,
                "jobId": job_id,
            }

    work_records = get_main_work_records()

    work_by_job: dict[str, dict[str, Any]] = {}

    for record in work_records:
        normalized = normalize_work_record(
            record
        )

        job_id = clean(
            normalized.get("job_id")
        )

        if job_id:
            work_by_job[job_id] = normalized

    output: list[dict[str, Any]] = []

    for job_id, raw_job in merged.items():
        gateway_payment = (
            get_latest_payment_for_job(
                job_id
            )
        )

        main_payment = (
            get_business_payment(
                job_id
            )
        )

        payment = (
            gateway_payment
            or main_payment
        )

        work_record = work_by_job.get(
            job_id
        )

        output.append(
            normalize_back_office_job(
                raw_job,
                payment,
                work_record,
            )
        )

    output.sort(
        key=lambda item: (
            clean(
                item.get("updated_at")
                or item.get("created_at")
            )
        ),
        reverse=True,
    )

    return output


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def root():
    return {
        "success": True,
        "service": "Naija Pocket Business Center Payment API",
        "version": APP_VERSION,
        "status": "online",
    }


@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "ok",
        "version": APP_VERSION,
        "payment_database": (
            Path(PAYMENT_DB_PATH).exists()
        ),
        "download_directory": str(
            DOWNLOAD_DIR
        ),
    }


@app.get("/api/health")
async def api_health():
    return await health()


@app.get("/api/payment/diagnostic")
async def payment_diagnostic():
    return {
        "success": True,
        "version": APP_VERSION,
        "payment_db": str(
            Path(PAYMENT_DB_PATH).resolve()
        ),
        "main_db": str(
            Path(MAIN_DB_PATH).resolve()
        ),
        "download_dir": str(
            DOWNLOAD_DIR.resolve()
        ),
        "download_dir_exists": (
            DOWNLOAD_DIR.exists()
        ),
        "payment_count": len(
            list_all_gateway_payments()
        ),
    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def payment_create(request: Request):
    body = await read_body(request)

    query = dict(request.query_params)

    job_id = clean(
        get_request_value(
            body,
            query,
            "job_id",
            "jobId",
            "id",
        )
    )

    if not job_id:
        return json_error(
            "job_id is required.",
            400,
        )

    customer_id = clean(
        get_request_value(
            body,
            query,
            "customer_id",
            "customerId",
        )
    )

    service = clean(
        get_request_value(
            body,
            query,
            "service",
            "service_name",
            "serviceName",
        )
    )

    payment_method = clean(
        get_request_value(
            body,
            query,
            "payment_method",
            "paymentMethod",
        ),
        DEFAULT_PAYMENT_METHOD,
    )

    document = safe_current_document(
        job_id
    )

    if not current_document_is_ready(
        document
    ):
        return json_error(
            "The document is not ready for payment.",
            409,
        )

    document_version = clean(
        document.get("version")
    )

    amount = money(
        get_request_value(
            body,
            query,
            "amount",
            "price",
            "total",
        )
    )

    if amount <= 0:
        amount = money(
            document.get("amount")
        )

    if amount <= 0:
        job = get_business_job(
            job_id
        )

        if job:
            amount = money(
                job.get("amount")
                or job.get("price")
                or job.get("total")
            )

    if not service:
        service = clean(
            document.get("service")
        )

    if not customer_id:
        customer_id = clean(
            document.get("customer_id")
        )

    existing = get_latest_payment_for_job(
        job_id
    )

    if existing:
        existing_status = normalize_status(
            existing.get("payment_status")
        )

        same_version = (
            document_version_is_current(
                existing,
                document,
            )
        )

        if same_version and existing_status in {
            "pending",
            "created",
            "reported",
            "awaiting_verification",
            "payment_pending",
            "pending_verification",
            "verified",
            "approved",
            "paid",
            "payment_confirmed",
        }:
            synchronize_payment_to_business(
                existing
            )

            return {
                "success": True,
                "payment": payment_public(
                    existing
                ),
                "payment_id": existing.get(
                    "payment_id"
                ),
                "job_id": job_id,
                "reused": True,
                "message": (
                    "Payment already exists for "
                    "this document."
                ),
            }

    payload = dict(
        document.get("payload")
        or {}
    )

    payload["job_id"] = job_id
    payload["customer_id"] = (
        customer_id
    )
    payload["service"] = service
    payload["amount"] = amount
    payload["currency"] = clean(
        document.get("currency"),
        DEFAULT_CURRENCY,
    )
    payload["version"] = (
        document_version
    )
    payload["document_version"] = (
        document_version
    )
    payload["filename"] = clean(
        document.get("filename")
    )
    payload["document_filename"] = clean(
        document.get("filename")
    )
    payload["pages"] = normalize_pages(
        document.get("pages")
    )
    payload["text"] = clean(
        document.get("text")
    )

    payment = create_payment_record(
        job_id=job_id,
        customer_id=customer_id,
        service=service,
        amount=amount,
        currency=clean(
            document.get("currency"),
            DEFAULT_CURRENCY,
        ),
        payment_method=payment_method,
        document_version=document_version,
        document_filename=clean(
            document.get("filename")
        ),
        document_payload=payload,
    )

    synchronize_payment_to_business(
        payment
    )

    payment = (
        get_payment(
            payment.get("payment_id")
        )
        or payment
    )

    return {
        "success": True,
        "payment": payment_public(
            payment
        ),
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": job_id,
        "message": (
            "Payment created. Complete your "
            "payment, then tap I HAVE MADE PAYMENT."
        ),
    }


# ============================================================
# PAYMENT REPORT
# ============================================================
# THIS IS THE IMPORTANT REDESIGNED ROUTE.
#
# The customer reports payment.
#
# 1. Verify the document has not changed.
# 2. Capture the exact current document.
# 3. Save the physical DOCX snapshot.
# 4. Mark payment as REPORTED.
# 5. Synchronize the job into Back Office.
# 6. Do NOT unlock download yet.
# ============================================================

@app.post("/api/payment/report")
async def payment_report(request: Request):
    body = await read_body(request)

    query = dict(request.query_params)

    payment_id = clean(
        get_request_value(
            body,
            query,
            "payment_id",
            "paymentId",
        )
    )

    job_id = clean(
        get_request_value(
            body,
            query,
            "job_id",
            "jobId",
        )
    )

    reference = clean(
        get_request_value(
            body,
            query,
            "payment_reference",
            "paymentReference",
            "reference",
            "transaction_reference",
            "transactionReference",
        )
    )

    customer_note = clean(
        get_request_value(
            body,
            query,
            "customer_note",
            "customerNote",
            "note",
        )
    )

    payment = None

    if payment_id:
        payment = get_payment(
            payment_id
        )

    elif job_id:
        payment = get_latest_payment_for_job(
            job_id
        )

    if not payment:
        return json_error(
            "Payment record was not found.",
            404,
        )

    payment_id = clean(
        payment.get("payment_id")
    )

    job_id = clean(
        payment.get("job_id")
    )

    current_document = safe_current_document(
        job_id
    )

    if not current_document_is_ready(
        current_document
    ):
        return json_error(
            "The current document is not available.",
            409,
        )

    if not document_version_is_current(
        payment,
        current_document,
    ):
        return json_error(
            (
                "The document has changed since "
                "the payment was created. Please "
                "create a new payment for the latest "
                "document."
            ),
            409,
            code="document_changed",
        )

    status = normalize_status(
        payment.get("payment_status")
    )

    # If already verified, never recreate or replace
    # the saved document.
    if payment_is_verified(status):
        synchronize_payment_to_business(
            payment
        )

        return {
            "success": True,
            "payment_id": payment_id,
            "job_id": job_id,
            "payment_status": status,
            "verified": True,
            "download_unlocked": True,
            "document_saved": saved_document_exists(
                payment
            ),
            "message": (
                "Payment has already been verified."
            ),
        }

    # --------------------------------------------------------
    # SAVE EXACT DOCUMENT BEFORE REPORTING PAYMENT.
    # --------------------------------------------------------

    if not saved_document_exists(payment):
        saved, saved_path = (
            save_exact_document_snapshot(
                payment,
                current_document,
            )
        )

        if not saved:
            return json_error(
                (
                    "Payment was not reported because "
                    "the exact document could not be saved."
                ),
                500,
                details=saved_path,
            )

        payment = (
            get_payment(payment_id)
            or payment
        )

    # --------------------------------------------------------
    # MARK PAYMENT AS REPORTED.
    # --------------------------------------------------------

    payment = (
        update_payment_record(
            payment_id,
            payment_status="reported",
            payment_reference=reference,
            customer_note=customer_note,
            reported_at=now_iso(),
        )
        or payment
    )

    synchronize_payment_to_business(
        payment
    )

    payment = (
        get_payment(payment_id)
        or payment
    )

    return {
        "success": True,
        "payment_id": payment_id,
        "paymentId": payment_id,
        "job_id": job_id,
        "jobId": job_id,
        "payment_status": "reported",
        "paymentStatus": "reported",
        "reported": True,
        "verified": False,
        "paid": False,

        # VERY IMPORTANT:
        # Reported payment is NOT unlocked.
        "download_unlocked": False,
        "downloadUnlocked": False,

        # But the exact document is already saved.
        "document_saved": saved_document_exists(
            payment
        ),
        "document_filename": payment.get(
            "document_filename"
        ),

        "message": (
            "Payment reported. Please wait for "
            "Customer Care to verify your payment."
        ),
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(request: Request):
    query = dict(request.query_params)

    payment_id = clean(
        get_request_value(
            {},
            query,
            "payment_id",
            "paymentId",
        )
    )

    job_id = clean(
        get_request_value(
            {},
            query,
            "job_id",
            "jobId",
        )
    )

    payment = None

    if payment_id:
        payment = get_payment(
            payment_id
        )

    elif job_id:
        payment = get_latest_payment_for_job(
            job_id
        )

    if not payment:
        return json_error(
            "Payment record was not found.",
            404,
        )

    status = normalize_status(
        payment.get("payment_status")
    )

    if not document_version_is_current(
        payment,
        safe_current_document(
            clean(payment.get("job_id"))
        ),
    ):
        return {
            "success": True,
            "payment_id": payment.get(
                "payment_id"
            ),
            "job_id": payment.get(
                "job_id"
            ),
            "payment_status": status,
            "verified": payment_is_verified(
                status
            ),
            "download_unlocked": False,
            "invalid_for_current_document": True,
            "message": (
                "This payment belongs to an older "
                "document version."
            ),
        }

    synchronize_payment_to_business(
        payment
    )

    payment = (
        get_payment(
            payment.get("payment_id")
        )
        or payment
    )

    status = normalize_status(
        payment.get("payment_status")
    )

    verified = payment_is_verified(
        status
    )

    return {
        "success": True,
        "payment": payment_public(
            payment
        ),
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": payment.get(
            "job_id"
        ),
        "payment_status": status or "pending",
        "paymentStatus": status or "pending",
        "reported": payment_is_reported(
            status
        ),
        "verified": verified,
        "paid": verified,
        "download_unlocked": verified,
        "downloadUnlocked": verified,
        "document_saved": saved_document_exists(
            payment
        ),
    }


# ============================================================
# PAYMENT COMPLETE
# ============================================================
# Compatibility endpoint.
#
# It means "I HAVE MADE PAYMENT".
# It MUST NOT verify payment.
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: Request,
):
    return await payment_report(
        request
    )


@app.post("/api/payment/confirm")
async def payment_confirm(
    request: Request,
):
    return await payment_report(
        request
    )


@app.post("/api/payment/report-payment")
async def payment_report_payment(
    request: Request,
):
    return await payment_report(
        request
    )


# ============================================================
# CUSTOMER CARE / BACK OFFICE PAYMENT LISTS
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():
    payments = list_all_gateway_payments()

    return {
        "success": True,
        "payments": [
            payment_public(payment)
            for payment in payments
        ],
        "count": len(payments),
    }


@app.get("/api/back-office/payments")
async def back_office_payments():
    payments = list_all_gateway_payments()

    return {
        "success": True,
        "payments": [
            payment_public(payment)
            for payment in payments
        ],
        "count": len(payments),
    }


@app.get("/api/customer-care/payment")
async def customer_care_payment(
    request: Request,
):
    query = dict(request.query_params)

    payment_id = clean(
        query.get("payment_id")
        or query.get("paymentId")
    )

    job_id = clean(
        query.get("job_id")
        or query.get("jobId")
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    return {
        "success": True,
        "payment": payment_public(
            payment
        ),
    }


# ============================================================
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/customer-care/jobs")
async def customer_care_jobs():
    jobs = get_back_office_jobs_combined()

    return {
        "success": True,
        "jobs": jobs,
        "items": jobs,
        "count": len(jobs),
    }


@app.get("/api/back-office/jobs")
async def back_office_jobs():
    jobs = get_back_office_jobs_combined()

    approved = 0
    activated = 0
    paid = 0
    pending = 0
    reported = 0

    for job in jobs:
        status = normalize_status(
            job.get("status")
        )

        payment_status = normalize_status(
            job.get("payment_status")
            or job.get("paymentStatus")
        )

        if status in {
            "approved",
            "review_complete",
            "payment_confirmed",
        }:
            approved += 1

        if status in {
            "activated",
            "download_unlocked",
            "payment_confirmed",
        }:
            activated += 1

        if payment_is_verified(
            payment_status
        ):
            paid += 1

        if payment_is_reported(
            payment_status
        ):
            reported += 1

        if payment_is_pending(
            payment_status
        ):
            pending += 1

    return {
        "success": True,
        "jobs": jobs,
        "items": jobs,
        "count": len(jobs),

        "approved": approved,
        "approved_count": approved,

        "activated": activated,
        "activated_count": activated,

        "paid": paid,
        "paid_count": paid,

        "reported": reported,
        "reported_count": reported,

        "pending": pending,
        "pending_count": pending,
    }


# ============================================================
# BACK OFFICE STATS
# ============================================================

@app.get("/api/back-office/stats")
async def back_office_stats():
    jobs = get_back_office_jobs_combined()

    payment_rows = list_all_gateway_payments()

    verified = sum(
        1
        for payment in payment_rows
        if payment_is_verified(
            payment.get("payment_status")
        )
    )

    reported = sum(
        1
        for payment in payment_rows
        if payment_is_reported(
            payment.get("payment_status")
        )
    )

    pending = sum(
        1
        for payment in payment_rows
        if payment_is_pending(
            payment.get("payment_status")
        )
    )

    total_amount = sum(
        money(payment.get("amount"))
        for payment in payment_rows
        if payment_is_verified(
            payment.get("payment_status")
        )
    )

    return {
        "success": True,
        "jobs": len(jobs),
        "payments": len(payment_rows),
        "verified": verified,
        "reported": reported,
        "pending": pending,
        "paid_amount": total_amount,
        "currency": DEFAULT_CURRENCY,
    }


# ============================================================
# OPERATOR AUTHORIZATION
# ============================================================

def operator_authorized(
    request: Request,
) -> bool:
    """
    Activation is an operator/back-office operation.

    If BACK_OFFICE_KEY is configured, require it.
    If no key is configured, retain compatibility with
    installations that do not yet have an admin key.
    """

    if not BACK_OFFICE_KEY:
        return True

    candidates = [
        request.headers.get(
            "x-back-office-key"
        ),
        request.headers.get(
            "x-admin-key"
        ),
        request.headers.get(
            "authorization"
        ),
    ]

    for candidate in candidates:
        value = clean(candidate)

        if value.startswith(
            "Bearer "
        ):
            value = clean(
                value[7:]
            )

        if value and value == BACK_OFFICE_KEY:
            return True

    return False


# ============================================================
# CUSTOMER CARE VERIFY / ACTIVATE
# ============================================================
# Verification changes only payment state.
#
# The document itself is NOT regenerated.
# The physical snapshot saved during payment reporting
# remains the document that will be downloaded.
# ============================================================

@app.post("/api/customer-care/payments/verify")
async def customer_care_verify(
    request: Request,
):
    if not operator_authorized(request):
        return json_error(
            "Customer Care authorization required.",
            401,
        )

    body = await read_body(request)

    query = dict(request.query_params)

    payment_id = clean(
        get_request_value(
            body,
            query,
            "payment_id",
            "paymentId",
        )
    )

    job_id = clean(
        get_request_value(
            body,
            query,
            "job_id",
            "jobId",
        )
    )

    admin_note = clean(
        get_request_value(
            body,
            query,
            "admin_note",
            "adminNote",
            "note",
        )
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    status = normalize_status(
        payment.get("payment_status")
    )

    # --------------------------------------------------------
    # CRITICAL:
    # Verification must NEVER silently create a new
    # document. If the reported document was not saved,
    # verification fails safely.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):
        return json_error(
            (
                "The payment cannot be activated because "
                "the exact document snapshot was not saved "
                "when payment was reported."
            ),
            409,
            code="document_snapshot_missing",
        )

    payment = (
        update_payment_record(
            payment.get("payment_id"),
            payment_status="verified",
            admin_note=admin_note,
            verified_at=now_iso(),
        )
        or payment
    )

    synchronize_payment_to_business(
        payment
    )

    payment = (
        get_payment(
            payment.get("payment_id")
        )
        or payment
    )

    return {
        "success": True,
        "payment": payment_public(
            payment
        ),
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": payment.get(
            "job_id"
        ),
        "payment_status": "verified",
        "verified": True,
        "paid": True,

        # The saved document is now unlocked.
        "download_unlocked": True,
        "downloadUnlocked": True,

        "document_saved": True,
        "message": (
            "Payment verified. The saved document "
            "is now unlocked for download."
        ),
    }


@app.post("/api/back-office/payments/verify")
async def back_office_verify(
    request: Request,
):
    return await customer_care_verify(
        request
    )


@app.post("/api/customer-care/payments/activate")
async def customer_care_activate(
    request: Request,
):
    return await customer_care_verify(
        request
    )


@app.post("/api/back-office/payments/activate")
async def back_office_activate(
    request: Request,
):
    return await customer_care_verify(
        request
    )


@app.post("/api/customer-care/activate")
async def customer_care_activate_short(
    request: Request,
):
    return await customer_care_verify(
        request
    )


@app.post("/api/back-office/activate")
async def back_office_activate_short(
    request: Request,
):
    return await customer_care_verify(
        request
    )


# ============================================================
# EXPLICIT UNLOCK ENDPOINT
# ============================================================

@app.post("/api/customer-care/payments/unlock")
async def customer_care_unlock(
    request: Request,
):
    if not operator_authorized(request):
        return json_error(
            "Customer Care authorization required.",
            401,
        )

    body = await read_body(request)
    query = dict(request.query_params)

    payment_id = clean(
        get_request_value(
            body,
            query,
            "payment_id",
            "paymentId",
        )
    )

    job_id = clean(
        get_request_value(
            body,
            query,
            "job_id",
            "jobId",
        )
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    if not saved_document_exists(
        payment
    ):
        return json_error(
            "Saved document snapshot not found.",
            409,
        )

    payment = (
        update_payment_record(
            payment.get("payment_id"),
            payment_status="verified",
            verified_at=(
                payment.get("verified_at")
                or now_iso()
            ),
        )
        or payment
    )

    synchronize_payment_to_business(
        payment
    )

    return {
        "success": True,
        "payment": payment_public(
            payment
        ),
        "download_unlocked": True,
        "message": (
            "Download unlocked."
        ),
    }


# ============================================================
# CUSTOMER DOWNLOAD STATUS
# ============================================================

@app.get("/api/download/status")
async def download_status(
    request: Request,
):
    query = dict(request.query_params)

    payment_id = clean(
        query.get("payment_id")
        or query.get("paymentId")
    )

    job_id = clean(
        query.get("job_id")
        or query.get("jobId")
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    status = normalize_status(
        payment.get("payment_status")
    )

    verified = payment_is_verified(
        status
    )

    saved = saved_document_exists(
        payment
    )

    return {
        "success": True,
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": payment.get(
            "job_id"
        ),
        "payment_status": status,
        "reported": payment_is_reported(
            status
        ),
        "verified": verified,
        "paid": verified,
        "document_saved": saved,

        # ONLY verification unlocks.
        "download_unlocked": (
            verified and saved
        ),
        "downloadUnlocked": (
            verified and saved
        ),

        "document_filename": payment.get(
            "document_filename"
        ),
        "download_count": int(
            payment.get("download_count") or 0
        ),
    }


# ============================================================
# CUSTOMER DOWNLOAD
# ============================================================
# IMPORTANT:
# There is NO make_docx() call here.
#
# The file was already created at payment-report time.
# This endpoint simply serves that same file after
# Customer Care verification.
# ============================================================

@app.get("/api/payment/download")
async def payment_download(
    request: Request,
):
    query = dict(request.query_params)

    payment_id = clean(
        query.get("payment_id")
        or query.get("paymentId")
    )

    job_id = clean(
        query.get("job_id")
        or query.get("jobId")
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    status = normalize_status(
        payment.get("payment_status")
    )

    if not payment_is_verified(
        status
    ):
        return json_error(
            (
                "Download is still locked. "
                "Customer Care must verify the payment first."
            ),
            403,
            payment_status=status,
            download_unlocked=False,
        )

    path = saved_document_path(
        payment
    )

    if path is None:
        return json_error(
            (
                "The saved document could not be found. "
                "The document must be recovered before download."
            ),
            404,
            code="saved_document_missing",
        )

    increment_download(
        clean(payment.get("payment_id"))
    )

    filename = safe_filename(
        clean(
            payment.get("document_filename")
            or path.name
        )
    )

    return FileResponse(
        path=str(path),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename=filename,
    )


@app.get("/api/download")
async def download_alias(
    request: Request,
):
    return await payment_download(
        request
    )


@app.get("/api/customer/download")
async def customer_download_alias(
    request: Request,
):
    return await payment_download(
        request
    )


# ============================================================
# BACK OFFICE PAYMENT DETAIL
# ============================================================

@app.get("/api/back-office/payment")
async def back_office_payment(
    request: Request,
):
    query = dict(request.query_params)

    payment_id = clean(
        query.get("payment_id")
        or query.get("paymentId")
    )

    job_id = clean(
        query.get("job_id")
        or query.get("jobId")
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    return {
        "success": True,
        "payment": payment_public(
            payment
        ),
    }


# ============================================================
# PAYMENT DELETE
# ============================================================

@app.delete("/api/payment/{payment_id}")
async def payment_delete(
    payment_id: str,
    request: Request,
):
    if not operator_authorized(request):
        return json_error(
            "Back Office authorization required.",
            401,
        )

    payment = get_payment(
        payment_id
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    conn = connect_payment_db()

    try:
        conn.execute(
            """
            DELETE FROM payment_orders
            WHERE payment_id = ?
            """,
            (payment_id,),
        )

        conn.commit()

    finally:
        conn.close()

    return {
        "success": True,
        "payment_id": payment_id,
        "deleted": True,
    }


# ============================================================
# /api/chat COMPATIBILITY
# ============================================================
#
# The workspace previously received:
#
#     POST /api/chat -> 404
#
# This route prevents this payment API deployment from
# returning a 404 for the workspace chat request.
#
# It does not put an AI/Groq implementation in workspace.html.
# It attempts to hand the request to the existing backend
# conversation functions if they are available.
# ============================================================

def call_existing_chat_backend(
    message: str,
    payload: dict[str, Any],
) -> Any:
    if business_database is None:
        return None

    candidates = (
        "chat",
        "process_chat",
        "handle_chat",
        "chat_message",
        "process_message",
        "send_message",
    )

    for function_name in candidates:
        function = getattr(
            business_database,
            function_name,
            None,
        )

        if not callable(function):
            continue

        attempts = [
            lambda: function(
                message=message,
                **payload,
            ),
            lambda: function(
                message,
            ),
            lambda: function(
                payload,
            ),
        ]

        for attempt in attempts:
            try:
                return attempt()
            except TypeError:
                continue
            except Exception:
                break

    return None


@app.post("/api/chat")
async def api_chat(
    request: Request,
):
    body = await read_body(request)

    message = clean(
        body.get("message")
        or body.get("text")
        or body.get("prompt")
    )

    result = call_existing_chat_backend(
        message,
        body,
    )

    if result is not None:
        if isinstance(result, dict):
            response = dict(result)

            if "success" not in response:
                response["success"] = True

            return response

        return {
            "success": True,
            "response": clean(result),
            "message": clean(result),
        }

    # Do not make the workspace crash with a 404.
    # The frontend receives a valid API response and can
    # continue using the existing service workflow.
    return {
        "success": True,
        "response": "",
        "message": "",
        "handled": False,
        "job_id": clean(
            body.get("job_id")
            or body.get("jobId")
        ),
        "notice": (
            "Chat backend is available through the "
            "main application workflow."
        ),
    }


# ============================================================
# GENERIC JOB DOCUMENT LOOKUP
# ============================================================

@app.get("/api/payment/document")
async def payment_document_info(
    request: Request,
):
    query = dict(request.query_params)

    payment_id = clean(
        query.get("payment_id")
        or query.get("paymentId")
    )

    job_id = clean(
        query.get("job_id")
        or query.get("jobId")
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else get_latest_payment_for_job(
            job_id
        )
    )

    if not payment:
        return json_error(
            "Payment not found.",
            404,
        )

    document = payment_document(
        payment
    )

    if not document:
        return json_error(
            "Document snapshot not found.",
            404,
        )

    return {
        "success": True,
        "payment_id": payment.get(
            "payment_id"
        ),
        "job_id": payment.get(
            "job_id"
        ),
        "document_version": payment.get(
            "document_version"
        ),
        "document_filename": payment.get(
            "document_filename"
        ),
        "document_saved": saved_document_exists(
            payment
        ),
        "pages": normalize_pages(
            document.get("pages")
        ),
        "text": clean(
            document.get("text")
        ),
    }


# ============================================================
# STARTUP DIAGNOSTICS
# ============================================================

@app.on_event("startup")
async def startup_event():
    init_payment_db()

    DOWNLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "Naija Pocket Business Center Payment API started."
    )

    print(
        f"Payment database: "
        f"{Path(PAYMENT_DB_PATH).resolve()}"
    )

    print(
        f"Main database: "
        f"{Path(MAIN_DB_PATH).resolve()}"
    )

    print(
        f"Download directory: "
        f"{DOWNLOAD_DIR.resolve()}"
    )

    print(
        f"API version: {APP_VERSION}"
    )


# ============================================================
# END OF COMPLETE payment_api.py
# ============================================================
