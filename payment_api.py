"""
Naija Pocket Business Center (NPBC)
Payment API and Gmail SMTP Document Delivery

Email delivery:
- Uses Gmail SMTP through email_delivery.py.
- Does not require payment confirmation before sending an email.
- Attaches the exact existing saved document.
- Does not regenerate documents during delivery.
- Identifies documents by service and document title.

Required Render environment variables:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

SMTP:
    smtp.gmail.com
    Port 587
    STARTTLS
"""

import os
import re
import json
import time
import uuid
import sqlite3
import secrets
import inspect
import logging

from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from fastapi import (
    FastAPI,
    HTTPException,
    Header,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_DB = BASE_DIR / "product_delivery.db"
PAYMENT_DB = BASE_DIR / "payment_gateway.db"

BACK_OFFICE_ADMIN_KEY = os.getenv(
    "BACK_OFFICE_ADMIN_KEY",
    "NPBC-2026",
)

PUBLIC_API_BASE_URL = os.getenv(
    "PUBLIC_API_BASE_URL",
    "https://naija-pocket-business-center.onrender.com",
).rstrip("/")

APP_VERSION = "npbc-email-delivery-priority-v20"

TOKEN_TTL_SECONDS = 60 * 60

SUPPORTED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".xls",
    ".pptx",
    ".txt",
    ".csv",
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npbc.payment_api")


# ============================================================
# EMAIL MODULE
# ============================================================

try:
    import email_delivery
except Exception as exc:
    email_delivery = None
    logger.exception("Could not import email_delivery.py: %s", exc)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center API",
    version=APP_VERSION,
    description=(
        "NPBC document delivery, Gmail SMTP email, "
        "saved document access and payment management."
    ),
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

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str) -> str:
    """
    Convert a service or title into a safe folder name.
    """
    value = str(value or "").strip()

    value = re.sub(r"[\\/]+", " ", value)
    value = re.sub(r"[^A-Za-z0-9 ._()&'-]", "", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip(" ._-") or "Untitled"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def normalize_email(value: Any) -> str:
    return normalize_text(value).lower()


def database_connection(database_path: Path):
    connection = sqlite3.connect(
        str(database_path),
        timeout=30,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set:
    if not table_exists(connection, table_name):
        return set()

    rows = connection.execute(
        f'PRAGMA table_info("{table_name}")'
    ).fetchall()

    return {row["name"] for row in rows}


def row_to_dict(row):
    if row is None:
        return None

    return dict(row)


def initialize_databases():
    """
    Create the basic tables if they do not exist.

    Existing tables are not dropped or reset.
    """

    with database_connection(PRODUCT_DB) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                document_title TEXT NOT NULL,
                document_saved_path TEXT,
                customer_email TEXT,
                customer_name TEXT,
                download_unlocked INTEGER DEFAULT 0,
                download_count INTEGER DEFAULT 0,
                activated_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(service, document_title)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT,
                document_title TEXT,
                recipient_email TEXT,
                delivery_status TEXT,
                message TEXT,
                created_at TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS back_office_download_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE,
                service TEXT,
                document_title TEXT,
                file_path TEXT,
                expires_at REAL,
                created_at TEXT
            )
            """
        )

        connection.commit()

    with database_connection(PAYMENT_DB) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_reference TEXT UNIQUE,
                service TEXT,
                document_title TEXT,
                customer_email TEXT,
                amount REAL DEFAULT 0,
                payment_status TEXT DEFAULT 'pending',
                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        connection.commit()


try:
    initialize_databases()
except Exception:
    logger.exception("Database initialization encountered an error.")


# ============================================================
# REQUEST MODELS
# ============================================================

class EmailDeliveryRequest(BaseModel):
    recipient_email: EmailStr
    service: str = Field(..., min_length=1, max_length=200)
    document_title: str = Field(..., min_length=1, max_length=500)
    customer_name: Optional[str] = None


class ProductRequest(BaseModel):
    service: str = Field(..., min_length=1, max_length=200)
    document_title: str = Field(..., min_length=1, max_length=500)
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
    amount: Optional[float] = 0


class PaymentStatusRequest(BaseModel):
    order_reference: str
    payment_status: str


class DownloadTokenRequest(BaseModel):
    service: str
    document_title: str


class BackOfficeLoginRequest(BaseModel):
    admin_key: str


# ============================================================
# SAVED DOCUMENT DISCOVERY
# ============================================================

def _path_is_inside_download_directory(path: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = DOWNLOAD_DIR.resolve()

        return (
            resolved_path == resolved_root
            or resolved_root in resolved_path.parents
        )
    except Exception:
        return False


def _valid_document_path(value: Any) -> Optional[Path]:
    if not value:
        return None

    try:
        path = Path(str(value))

        if not path.is_absolute():
            path = BASE_DIR / path

        path = path.resolve()

        if not path.is_file():
            return None

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None

        return path

    except Exception:
        return None


def _product_record(
    service: str,
    document_title: str,
) -> Optional[dict]:
    """
    Find the existing product record using service and title.
    """

    try:
        with database_connection(PRODUCT_DB) as connection:
            if not table_exists(connection, "document_products"):
                return None

            columns = table_columns(
                connection,
                "document_products",
            )

            if not {"service", "document_title"}.issubset(columns):
                return None

            row = connection.execute(
                """
                SELECT *
                FROM document_products
                WHERE LOWER(TRIM(service)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(document_title)) = LOWER(TRIM(?))
                ORDER BY id DESC
                LIMIT 1
                """,
                (service, document_title),
            ).fetchone()

            return row_to_dict(row)

    except Exception:
        logger.exception("Could not find product record.")
        return None


def _saved_path_from_record(record: Optional[dict]) -> Optional[Path]:
    if not record:
        return None

    possible_fields = (
        "document_saved_path",
        "saved_path",
        "file_path",
        "document_path",
        "prepared_file_path",
        "output_path",
    )

    for field in possible_fields:
        path = _valid_document_path(record.get(field))

        if path:
            return path

    return None


def find_saved_document(
    service: str,
    document_title: str,
) -> Optional[Path]:
    """
    Find the exact existing saved document.

    Search order:
    1. Saved path in the product database.
    2. Canonical downloads/service/title directory.
    3. Existing files elsewhere inside downloads.

    This function does not generate or modify documents.
    """

    service = normalize_text(service)
    document_title = normalize_text(document_title)

    if not service or not document_title:
        return None

    record = _product_record(service, document_title)

    recorded_path = _saved_path_from_record(record)

    if recorded_path:
        return recorded_path

    service_directory = safe_name(service)
    title_directory = safe_name(document_title)

    canonical_directory = (
        DOWNLOAD_DIR / service_directory / title_directory
    )

    if canonical_directory.is_dir():
        candidates = []

        try:
            for path in canonical_directory.iterdir():
                if (
                    path.is_file()
                    and path.suffix.lower() in SUPPORTED_EXTENSIONS
                ):
                    candidates.append(path)
        except OSError:
            logger.exception("Could not inspect canonical directory.")

        if candidates:
            candidates.sort(
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )

            return candidates[0].resolve()

    if not DOWNLOAD_DIR.exists():
        return None

    matching_candidates = []

    expected_service = service.casefold()
    expected_title = document_title.casefold()

    try:
        for path in DOWNLOAD_DIR.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            if not _path_is_inside_download_directory(path):
                continue

            path_parts = [
                part.casefold()
                for part in path.parts
            ]

            filename = path.stem.casefold()

            title_match = (
                filename == expected_title
                or safe_name(filename).casefold()
                == safe_name(expected_title).casefold()
            )

            service_match = expected_service in path_parts

            if title_match and service_match:
                matching_candidates.append(path)

    except OSError:
        logger.exception("Error searching saved documents.")

    if matching_candidates:
        matching_candidates.sort(
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )

        return matching_candidates[0].resolve()

    return None


# ============================================================
# PRODUCT RECORD MANAGEMENT
# ============================================================

def register_saved_document(
    service: str,
    document_title: str,
    file_path: Path,
    customer_email: Optional[str] = None,
    customer_name: Optional[str] = None,
):
    """
    Register an already-existing document.

    The document itself is never regenerated.
    """

    if not file_path or not file_path.is_file():
        raise FileNotFoundError("Saved document does not exist.")

    service = normalize_text(service)
    document_title = normalize_text(document_title)

    with database_connection(PRODUCT_DB) as connection:
        columns = table_columns(
            connection,
            "document_products",
        )

        if not columns:
            raise RuntimeError(
                "The document_products table is unavailable."
            )

        existing = connection.execute(
            """
            SELECT id
            FROM document_products
            WHERE LOWER(TRIM(service)) = LOWER(TRIM(?))
              AND LOWER(TRIM(document_title)) = LOWER(TRIM(?))
            ORDER BY id DESC
            LIMIT 1
            """,
            (service, document_title),
        ).fetchone()

        now = utc_now()

        values = {
            "service": service,
            "document_title": document_title,
            "document_saved_path": str(file_path.resolve()),
            "customer_email": normalize_email(customer_email),
            "customer_name": normalize_text(customer_name),
            "updated_at": now,
        }

        if existing:
            update_fields = {
                key: value
                for key, value in values.items()
                if key in columns and key != "service"
                and key != "document_title"
            }

            if update_fields:
                assignments = ", ".join(
                    f'"{key}" = ?'
                    for key in update_fields
                )

                connection.execute(
                    f"""
                    UPDATE document_products
                    SET {assignments}
                    WHERE id = ?
                    """,
                    list(update_fields.values()) + [existing["id"]],
                )

        else:
            if "created_at" in columns:
                values["created_at"] = now

            insert_values = {
                key: value
                for key, value in values.items()
                if key in columns
            }

            if insert_values:
                fields = ", ".join(
                    f'"{key}"'
                    for key in insert_values
                )

                placeholders = ", ".join(
                    "?"
                    for _ in insert_values
                )

                connection.execute(
                    f"""
                    INSERT INTO document_products ({fields})
                    VALUES ({placeholders})
                    """,
                    list(insert_values.values()),
                )

        connection.commit()


# ============================================================
# GMAIL SMTP DELIVERY
# ============================================================

def _email_configuration_status() -> dict:
    if email_delivery is None:
        return {
            "configured": False,
            "message": (
                "email_delivery.py could not be imported."
            ),
        }

    status_function = getattr(
        email_delivery,
        "configuration_status",
        None,
    )

    if not callable(status_function):
        configured = bool(
            os.getenv("GMAIL_ADDRESS")
            and os.getenv("GMAIL_APP_PASSWORD")
        )

        return {
            "configured": configured,
            "message": (
                "Gmail credentials are configured."
                if configured
                else "Gmail credentials are missing."
            ),
        }

    try:
        result = status_function()

        if isinstance(result, dict):
            return result

        return {
            "configured": bool(result),
            "message": str(result),
        }

    except Exception as exc:
        logger.exception("Email configuration check failed.")

        return {
            "configured": False,
            "message": str(exc),
        }


def _invoke_email_function(
    recipient_email: str,
    file_path: Path,
    service: str,
    document_title: str,
    customer_name: Optional[str],
):
    """
    Call the installed email_delivery.py function while
    accommodating supported parameter naming variations.
    """

    if email_delivery is None:
        raise RuntimeError(
            "The email_delivery.py module could not be imported."
        )

    function = getattr(
        email_delivery,
        "send_document_email",
        None,
    )

    if not callable(function):
        raise RuntimeError(
            "email_delivery.py must provide send_document_email()."
        )

    signature = inspect.signature(function)
    parameters = signature.parameters

    values = {
        "recipient_email": recipient_email,
        "customer_email": recipient_email,
        "email": recipient_email,
        "to_email": recipient_email,
        "to": recipient_email,
        "recipient": recipient_email,

        "file_path": str(file_path),
        "document_path": str(file_path),
        "saved_path": str(file_path),
        "attachment_path": str(file_path),
        "file": str(file_path),
        "document_file": str(file_path),

        "service": service,
        "service_name": service,

        "document_title": document_title,
        "title": document_title,
        "filename": file_path.name,
        "file_name": file_path.name,

        "customer_name": customer_name or "",
        "name": customer_name or "",
    }

    kwargs = {}

    has_var_keyword = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    for name, parameter in parameters.items():
        if name in values:
            kwargs[name] = values[name]

    if has_var_keyword:
        for name in (
            "recipient_email",
            "file_path",
            "service",
            "document_title",
            "customer_name",
        ):
            kwargs.setdefault(name, values[name])

    required_unknown = [
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and name not in kwargs
    ]

    if required_unknown:
        raise RuntimeError(
            "Unsupported required email_delivery.py parameters: "
            + ", ".join(required_unknown)
        )

    result = function(**kwargs)

    if inspect.isawaitable(result):
        raise RuntimeError(
            "send_document_email() must be a synchronous function."
        )

    if isinstance(result, dict):
        if result.get("success") is False:
            raise RuntimeError(
                result.get("message")
                or result.get("error")
                or "Gmail delivery failed."
            )

        if result.get("ok") is False:
            raise RuntimeError(
                result.get("message")
                or result.get("error")
                or "Gmail delivery failed."
            )

    elif result is False:
        raise RuntimeError("Gmail delivery failed.")

    return result


def send_existing_document_email(
    recipient_email: str,
    service: str,
    document_title: str,
    customer_name: Optional[str] = None,
) -> dict:
    """
    Send an existing saved document by email.

    Payment status is intentionally not checked here.
    """

    recipient_email = normalize_email(recipient_email)
    service = normalize_text(service)
    document_title = normalize_text(document_title)

    if not recipient_email:
        raise ValueError("Customer email address is required.")

    if not service:
        raise ValueError("Service name is required.")

    if not document_title:
        raise ValueError("Document title is required.")

    configuration = _email_configuration_status()

    if not configuration.get("configured", False):
        raise RuntimeError(
            "Gmail SMTP is not configured. Check the "
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment "
            "variables in Render."
        )

    file_path = find_saved_document(
        service,
        document_title,
    )

    if not file_path:
        raise FileNotFoundError(
            "The saved document could not be found for "
            f"service '{service}' and title '{document_title}'."
        )

    # Register the existing file without regenerating it.
    register_saved_document(
        service=service,
        document_title=document_title,
        file_path=file_path,
        customer_email=recipient_email,
        customer_name=customer_name,
    )

    result = _invoke_email_function(
        recipient_email=recipient_email,
        file_path=file_path,
        service=service,
        document_title=document_title,
        customer_name=customer_name,
    )

    message = (
        result.get("message", "Email sent successfully.")
        if isinstance(result, dict)
        else "Email sent successfully."
    )

    try:
        with database_connection(PRODUCT_DB) as connection:
            connection.execute(
                """
                INSERT INTO delivery_events (
                    service,
                    document_title,
                    recipient_email,
                    delivery_status,
                    message,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    service,
                    document_title,
                    recipient_email,
                    "sent",
                    str(message),
                    utc_now(),
                ),
            )

            connection.commit()

    except Exception:
        logger.exception(
            "Email was sent, but delivery history could not be saved."
        )

    return {
        "success": True,
        "message": "Document emailed successfully.",
        "service": service,
        "document_title": document_title,
        "recipient_email": recipient_email,
        "filename": file_path.name,
    }


# ============================================================
# ROOT AND HEALTH
# ============================================================

@app.get("/")
def root():
    return {
        "success": True,
        "service": "Naija Pocket Business Center",
        "version": APP_VERSION,
        "email_delivery": True,
        "payment_confirmation_required_for_email": False,
    }


@app.get("/health")
@app.get("/api/health")
def health():
    return {
        "success": True,
        "status": "healthy",
        "version": APP_VERSION,
        "timestamp": utc_now(),
    }


@app.get("/api/status")
def api_status():
    return {
        "success": True,
        "version": APP_VERSION,
        "email": _email_configuration_status(),
    }


# ============================================================
# EMAIL DELIVERY ENDPOINTS
# ============================================================

@app.get("/api/email-delivery/status")
@app.get("/api/delivery/email/status")
def email_delivery_status():
    status = _email_configuration_status()

    return {
        "success": bool(status.get("configured", False)),
        "provider": "Gmail SMTP",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "security": "STARTTLS",
        "payment_confirmation_required": False,
        "configuration": status,
    }


@app.post("/api/delivery/email")
@app.post("/api/delivery/send-email")
@app.post("/api/email-delivery")
def deliver_document_by_email(
    payload: EmailDeliveryRequest,
):
    """
    Send an existing saved document to the customer.

    No payment confirmation is required for this endpoint.
    """

    try:
        result = send_existing_document_email(
            recipient_email=str(payload.recipient_email),
            service=payload.service,
            document_title=payload.document_title,
            customer_name=payload.customer_name,
        )

        return result

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except RuntimeError as exc:
        logger.error("Email delivery configuration/error: %s", exc)

        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )

    except Exception:
        logger.exception("Unexpected email delivery error.")

        raise HTTPException(
            status_code=500,
            detail="Email delivery failed. Check the Render logs.",
        )


@app.get("/api/delivery/email")
def deliver_document_by_email_get(
    recipient_email: str = Query(...),
    service: str = Query(...),
    document_title: str = Query(...),
    customer_name: Optional[str] = Query(None),
):
    """
    GET compatibility endpoint for existing front-end callers.
    """

    try:
        return send_existing_document_email(
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            customer_name=customer_name,
        )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    except Exception:
        logger.exception("GET email delivery failed.")

        raise HTTPException(
            status_code=500,
            detail="Email delivery failed. Check the Render logs.",
        )


# ============================================================
# SAVED DOCUMENT LOOKUP
# ============================================================

@app.get("/api/document/find")
@app.get("/api/delivery/document")
def document_lookup(
    service: str = Query(...),
    title: str = Query(...),
):
    path = find_saved_document(service, title)

    if not path:
        raise HTTPException(
            status_code=404,
            detail="Saved document not found.",
        )

    return {
        "success": True,
        "service": service,
        "document_title": title,
        "filename": path.name,
        "exists": True,
    }


# ============================================================
# CUSTOMER DOCUMENT DOWNLOAD
# ============================================================

@app.get("/api/download")
def customer_download(
    service: str = Query(...),
    title: str = Query(...),
):
    """
    Return the existing saved document.

    No new document is generated here.
    """

    path = find_saved_document(service, title)

    if not path:
        raise HTTPException(
            status_code=404,
            detail="Saved document not found.",
        )

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/octet-stream",
    )


@app.get("/api/delivery/file")
@app.get("/api/delivery-file")
def delivery_file(
    service: str = Query(...),
    title: str = Query(...),
):
    path = find_saved_document(service, title)

    if not path:
        raise HTTPException(
            status_code=404,
            detail="Saved document not found.",
        )

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/octet-stream",
    )


# ============================================================
# PRODUCT STATUS
# ============================================================

@app.get("/api/product/status")
def product_status(
    service: str = Query(...),
    title: str = Query(...),
):
    record = _product_record(service, title)
    path = find_saved_document(service, title)

    return {
        "success": True,
        "service": service,
        "document_title": title,
        "document_found": path is not None,
        "filename": path.name if path else None,
        "product_registered": record is not None,
        "email_delivery_available": bool(
            _email_configuration_status().get("configured", False)
        ),
    }


# ============================================================
# PAYMENT ORDER MANAGEMENT
# ============================================================

@app.post("/api/payment/create")
@app.post("/api/payments/create")
def create_payment_order(payload: ProductRequest):
    """
    Register a payment order.

    This endpoint is separate from email delivery.
    Email delivery does not call this endpoint.
    """

    order_reference = (
        "NPBC-" + secrets.token_hex(8).upper()
    )

    now = utc_now()

    try:
        with database_connection(PAYMENT_DB) as connection:
            connection.execute(
                """
                INSERT INTO payment_orders (
                    order_reference,
                    service,
                    document_title,
                    customer_email,
                    amount,
                    payment_status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_reference,
                    normalize_text(payload.service),
                    normalize_text(payload.document_title),
                    normalize_email(payload.customer_email),
                    float(payload.amount or 0),
                    "pending",
                    now,
                    now,
                ),
            )

            connection.commit()

        return {
            "success": True,
            "order_reference": order_reference,
            "payment_status": "pending",
        }

    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Could not create a unique payment order.",
        )

    except Exception:
        logger.exception("Payment order creation failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not create payment order.",
        )


@app.get("/api/payment/status")
@app.get("/api/payments/status")
def get_payment_status(
    order_reference: str = Query(...),
):
    try:
        with database_connection(PAYMENT_DB) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE order_reference = ?
                LIMIT 1
                """,
                (order_reference,),
            ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Payment order not found.",
            )

        return {
            "success": True,
            "payment": dict(row),
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Payment status lookup failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not retrieve payment status.",
        )


@app.post("/api/payment/complete")
@app.post("/api/payments/complete")
def complete_payment(
    payload: PaymentStatusRequest,
    x_back_office_key: Optional[str] = Header(None),
):
    """
    Mark a payment order complete through an authorized request.
    """

    if not secrets.compare_digest(
        str(x_back_office_key or ""),
        str(BACK_OFFICE_ADMIN_KEY),
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )

    new_status = normalize_text(payload.payment_status).lower()

    if new_status not in {
        "pending",
        "paid",
        "completed",
        "failed",
        "rejected",
        "cancelled",
    }:
        raise HTTPException(
            status_code=400,
            detail="Unsupported payment status.",
        )

    if new_status == "completed":
        new_status = "paid"

    try:
        with database_connection(PAYMENT_DB) as connection:
            cursor = connection.execute(
                """
                UPDATE payment_orders
                SET payment_status = ?,
                    updated_at = ?
                WHERE order_reference = ?
                """,
                (
                    new_status,
                    utc_now(),
                    payload.order_reference,
                ),
            )

            connection.commit()

            if cursor.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Payment order not found.",
                )

        return {
            "success": True,
            "order_reference": payload.order_reference,
            "payment_status": new_status,
        }

    except HTTPException:
        raise

    except Exception:
        logger.exception("Payment update failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not update payment status.",
        )


@app.get("/api/payment/report")
@app.get("/api/payments/report")
def payment_report(
    x_back_office_key: Optional[str] = Header(None),
):
    if not secrets.compare_digest(
        str(x_back_office_key or ""),
        str(BACK_OFFICE_ADMIN_KEY),
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )

    try:
        with database_connection(PAYMENT_DB) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM payment_orders
                ORDER BY id DESC
                LIMIT 500
                """
            ).fetchall()

        return {
            "success": True,
            "count": len(rows),
            "payments": [dict(row) for row in rows],
        }

    except Exception:
        logger.exception("Payment report failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not load payment report.",
        )


# ============================================================
# BACK OFFICE AUTHENTICATION
# ============================================================

@app.post("/api/back-office/login")
def back_office_login(payload: BackOfficeLoginRequest):
    if not secrets.compare_digest(
        str(payload.admin_key),
        str(BACK_OFFICE_ADMIN_KEY),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Back Office key.",
        )

    return {
        "success": True,
        "authenticated": True,
        "message": "Back Office authentication successful.",
    }


def require_back_office(
    x_back_office_key: Optional[str],
):
    if not secrets.compare_digest(
        str(x_back_office_key or ""),
        str(BACK_OFFICE_ADMIN_KEY),
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )


# ============================================================
# BACK OFFICE PRODUCT AND DELIVERY RECORDS
# ============================================================

@app.get("/api/back-office/products")
@app.get("/api/back-office/records")
def back_office_products(
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    try:
        with database_connection(PRODUCT_DB) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM document_products
                ORDER BY id DESC
                LIMIT 500
                """
            ).fetchall()

        return {
            "success": True,
            "count": len(rows),
            "products": [dict(row) for row in rows],
        }

    except Exception:
        logger.exception("Back Office product listing failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not load product records.",
        )


@app.get("/api/back-office/product")
def back_office_product(
    service: str = Query(...),
    title: str = Query(...),
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    record = _product_record(service, title)
    path = find_saved_document(service, title)

    if not record and not path:
        raise HTTPException(
            status_code=404,
            detail="Product or saved document not found.",
        )

    return {
        "success": True,
        "product": record,
        "document_found": path is not None,
        "filename": path.name if path else None,
    }


@app.get("/api/back-office/document-file")
@app.get("/api/back-office/delivery-file")
def back_office_document_file(
    service: str = Query(...),
    title: str = Query(...),
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    path = find_saved_document(service, title)

    if not path:
        raise HTTPException(
            status_code=404,
            detail="Saved document not found.",
        )

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/octet-stream",
    )


@app.get("/api/back-office/delivery-history")
@app.get("/api/back-office/history")
def back_office_delivery_history(
    limit: int = Query(100, ge=1, le=500),
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    try:
        with database_connection(PRODUCT_DB) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM delivery_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return {
            "success": True,
            "count": len(rows),
            "events": [dict(row) for row in rows],
        }

    except Exception:
        logger.exception("Delivery history lookup failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not load delivery history.",
        )


# ============================================================
# BACK OFFICE DOWNLOAD TOKENS
# ============================================================

def create_download_token(
    service: str,
    document_title: str,
) -> dict:
    path = find_saved_document(service, document_title)

    if not path:
        raise FileNotFoundError("Saved document not found.")

    token = secrets.token_urlsafe(32)
    expires_at = time.time() + TOKEN_TTL_SECONDS

    with database_connection(PRODUCT_DB) as connection:
        connection.execute(
            """
            INSERT INTO back_office_download_tokens (
                token,
                service,
                document_title,
                file_path,
                expires_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                service,
                document_title,
                str(path.resolve()),
                expires_at,
                utc_now(),
            ),
        )

        connection.commit()

    return {
        "success": True,
        "token": token,
        "download_url": (
            f"{PUBLIC_API_BASE_URL}/api/download/token"
            f"?token={token}"
        ),
        "expires_in_seconds": TOKEN_TTL_SECONDS,
    }


@app.post("/api/back-office/download-token")
def back_office_download_token(
    payload: DownloadTokenRequest,
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    try:
        return create_download_token(
            payload.service,
            payload.document_title,
        )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    except Exception:
        logger.exception("Download token creation failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not create download token.",
        )


@app.get("/api/download/token")
def download_by_token(
    token: str = Query(...),
):
    try:
        with database_connection(PRODUCT_DB) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM back_office_download_tokens
                WHERE token = ?
                LIMIT 1
                """,
                (token,),
            ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Download token not found.",
            )

        record = dict(row)

        if float(record["expires_at"]) < time.time():
            raise HTTPException(
                status_code=410,
                detail="Download token has expired.",
            )

        path = _valid_document_path(record.get("file_path"))

        if not path:
            path = find_saved_document(
                record["service"],
                record["document_title"],
            )

        if not path:
            raise HTTPException(
                status_code=404,
                detail="Saved document not found.",
            )

        return FileResponse(
            path=str(path),
            filename=path.name,
            media_type="application/octet-stream",
        )

    except HTTPException:
        raise

    except Exception:
        logger.exception("Token download failed.")

        raise HTTPException(
            status_code=500,
            detail="Could not deliver saved document.",
        )


# ============================================================
# CUSTOMER CARE EMAIL ALIAS
# ============================================================

@app.post("/api/customer-care/email")
def customer_care_email(
    payload: EmailDeliveryRequest,
):
    """
    Customer Care email alias.

    This endpoint sends the existing document directly.
    Payment confirmation is not required.
    """

    return deliver_document_by_email(payload)


# ============================================================
# ERROR HANDLING
# ============================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request,
    exc: HTTPException,
):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "detail": exc.detail,
            "path": request.url.path,
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled API error at %s",
        request.url.path,
    )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "detail": "An internal server error occurred.",
            "path": request.url.path,
        },
    )
