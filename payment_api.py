"""
Naija Pocket Business Center (NPBC)
Payment API and Gmail SMTP Document Delivery

Email delivery is independent of payment confirmation.

Required Render environment variables:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD
    BACK_OFFICE_ADMIN_KEY (optional)
    PUBLIC_API_BASE_URL (optional)

Gmail SMTP:
    smtp.gmail.com
    Port 587
    STARTTLS
"""

import os
import re
import time
import secrets
import sqlite3
import logging
import inspect

from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Header,
    Query,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


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

APP_VERSION = "npbc-email-delivery-priority-v21"

SUPPORTED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".xls",
    ".pptx",
    ".txt",
    ".csv",
}

TOKEN_TTL_SECONDS = 3600

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npbc.payment_api")


# ============================================================
# EMAIL MODULE
# ============================================================

try:
    import email_delivery
except Exception:
    email_delivery = None
    logger.exception("Could not import email_delivery.py")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center API",
    version=APP_VERSION,
    description="NPBC document delivery, email and payment API.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELS
# ============================================================

class EmailDeliveryRequest(BaseModel):
    recipient_email: str = Field(..., min_length=3, max_length=320)
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
# GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_name(value):
    value = str(value or "").strip()
    value = re.sub(r"[\\/]+", " ", value)
    value = re.sub(r"[^A-Za-z0-9 ._()&'-]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._-") or "Untitled"


def normalize_text(value):
    return str(value or "").strip()


def normalize_email(value):
    return normalize_text(value).lower()


def valid_email(value):
    value = normalize_email(value)

    if len(value) > 320:
        return False

    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
        r"[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
        r"[A-Za-z0-9])?)+",
        value,
    ):
        return False

    return True


def db_connect(path):
    connection = sqlite3.connect(
        str(path),
        timeout=30,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def table_exists(connection, table_name):
    result = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return result is not None


def table_columns(connection, table_name):
    if not table_exists(connection, table_name):
        return set()

    return {
        row["name"]
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize_databases():
    with db_connect(PRODUCT_DB) as connection:
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

    with db_connect(PAYMENT_DB) as connection:
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
    logger.exception("Database initialization failed.")


# ============================================================
# SAVED DOCUMENT LOOKUP
# ============================================================

def valid_saved_path(value):
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


def inside_download_directory(path):
    try:
        resolved = path.resolve()
        root = DOWNLOAD_DIR.resolve()

        return resolved == root or root in resolved.parents

    except Exception:
        return False


def get_product_record(service, document_title):
    try:
        with db_connect(PRODUCT_DB) as connection:
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
                  AND LOWER(TRIM(document_title)) =
                      LOWER(TRIM(?))
                ORDER BY id DESC
                LIMIT 1
                """,
                (service, document_title),
            ).fetchone()

            return dict(row) if row else None

    except Exception:
        logger.exception("Could not read product record.")
        return None


def find_saved_document(service, document_title):
    """
    Locate the exact existing document.
    Never generate or rewrite the document.
    """

    service = normalize_text(service)
    document_title = normalize_text(document_title)

    if not service or not document_title:
        return None

    record = get_product_record(service, document_title)

    if record:
        for field in (
            "document_saved_path",
            "saved_path",
            "file_path",
            "document_path",
            "prepared_file_path",
            "output_path",
        ):
            path = valid_saved_path(record.get(field))

            if path:
                return path

    canonical_directory = (
        DOWNLOAD_DIR
        / safe_name(service)
        / safe_name(document_title)
    )

    if canonical_directory.is_dir():
        candidates = [
            path
            for path in canonical_directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

        if candidates:
            return max(
                candidates,
                key=lambda item: item.stat().st_mtime,
            ).resolve()

    if not DOWNLOAD_DIR.exists():
        return None

    expected_service = safe_name(service).casefold()
    expected_title = safe_name(document_title).casefold()

    matches = []

    try:
        for path in DOWNLOAD_DIR.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            if not inside_download_directory(path):
                continue

            parts = [
                safe_name(part).casefold()
                for part in path.parts
            ]

            title_match = (
                safe_name(path.stem).casefold()
                == expected_title
            )

            service_match = expected_service in parts

            if title_match and service_match:
                matches.append(path.resolve())

    except OSError:
        logger.exception("Document search failed.")

    if matches:
        return max(
            matches,
            key=lambda item: item.stat().st_mtime,
        )

    return None


# ============================================================
# REGISTER EXISTING DOCUMENT
# ============================================================

def register_saved_document(
    service,
    document_title,
    file_path,
    customer_email=None,
    customer_name=None,
):
    if not file_path or not file_path.is_file():
        raise FileNotFoundError("Saved document does not exist.")

    now = utc_now()

    with db_connect(PRODUCT_DB) as connection:
        columns = table_columns(
            connection,
            "document_products",
        )

        existing = connection.execute(
            """
            SELECT id
            FROM document_products
            WHERE LOWER(TRIM(service)) = LOWER(TRIM(?))
              AND LOWER(TRIM(document_title)) =
                  LOWER(TRIM(?))
            ORDER BY id DESC
            LIMIT 1
            """,
            (service, document_title),
        ).fetchone()

        values = {
            "service": service,
            "document_title": document_title,
            "document_saved_path": str(file_path.resolve()),
            "customer_email": normalize_email(customer_email),
            "customer_name": normalize_text(customer_name),
            "updated_at": now,
        }

        if existing:
            updates = {
                key: value
                for key, value in values.items()
                if key in columns
                and key not in {"service", "document_title"}
            }

            if updates:
                assignments = ", ".join(
                    f'"{key}" = ?'
                    for key in updates
                )

                connection.execute(
                    f"""
                    UPDATE document_products
                    SET {assignments}
                    WHERE id = ?
                    """,
                    list(updates.values()) + [existing["id"]],
                )

        else:
            values["created_at"] = now

            insert_values = {
                key: value
                for key, value in values.items()
                if key in columns
            }

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
# EMAIL CONFIGURATION
# ============================================================

def email_configuration_status():
    if email_delivery is None:
        return {
            "configured": False,
            "message": "Could not import email_delivery.py.",
        }

    function = getattr(
        email_delivery,
        "configuration_status",
        None,
    )

    if callable(function):
        try:
            result = function()

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

    configured = bool(
        os.getenv("GMAIL_ADDRESS")
        and os.getenv("GMAIL_APP_PASSWORD")
    )

    return {
        "configured": configured,
        "message": (
            "Gmail credentials are configured."
            if configured
            else "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD."
        ),
    }


# ============================================================
# SEND DOCUMENT EMAIL
# ============================================================

def invoke_email_sender(
    recipient_email,
    file_path,
    service,
    document_title,
    customer_name=None,
):
    if email_delivery is None:
        raise RuntimeError(
            "email_delivery.py could not be imported."
        )

    sender = getattr(
        email_delivery,
        "send_document_email",
        None,
    )

    if not callable(sender):
        raise RuntimeError(
            "email_delivery.py must define send_document_email()."
        )

    parameters = inspect.signature(sender).parameters

    available_values = {
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

    accepts_extra_keywords = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    for name in parameters:
        if name in available_values:
            kwargs[name] = available_values[name]

    if accepts_extra_keywords:
        kwargs.setdefault("recipient_email", recipient_email)
        kwargs.setdefault("file_path", str(file_path))
        kwargs.setdefault("service", service)
        kwargs.setdefault("document_title", document_title)
        kwargs.setdefault("customer_name", customer_name or "")

    missing = [
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and name not in kwargs
    ]

    if missing:
        raise RuntimeError(
            "Email sender has unsupported required parameters: "
            + ", ".join(missing)
        )

    result = sender(**kwargs)

    if inspect.isawaitable(result):
        raise RuntimeError(
            "send_document_email() must be a normal synchronous function."
        )

    if result is False:
        raise RuntimeError("Gmail delivery failed.")

    if isinstance(result, dict):
        if result.get("success") is False or result.get("ok") is False:
            raise RuntimeError(
                result.get("message")
                or result.get("error")
                or "Gmail delivery failed."
            )

    return result


def send_existing_document_email(
    recipient_email,
    service,
    document_title,
    customer_name=None,
):
    recipient_email = normalize_email(recipient_email)
    service = normalize_text(service)
    document_title = normalize_text(document_title)

    if not valid_email(recipient_email):
        raise ValueError("Enter a valid customer email address.")

    if not service:
        raise ValueError("Service name is required.")

    if not document_title:
        raise ValueError("Document title is required.")

    status = email_configuration_status()

    if not status.get("configured", False):
        raise RuntimeError(
            "Gmail SMTP is not configured. Check GMAIL_ADDRESS "
            "and GMAIL_APP_PASSWORD in Render environment variables."
        )

    file_path = find_saved_document(
        service,
        document_title,
    )

    if not file_path:
        raise FileNotFoundError(
            "Saved document not found for service "
            f"'{service}' and title '{document_title}'."
        )

    register_saved_document(
        service=service,
        document_title=document_title,
        file_path=file_path,
        customer_email=recipient_email,
        customer_name=customer_name,
    )

    result = invoke_email_sender(
        recipient_email=recipient_email,
        file_path=file_path,
        service=service,
        document_title=document_title,
        customer_name=customer_name,
    )

    message = "Document emailed successfully."

    if isinstance(result, dict):
        message = result.get("message", message)

    try:
        with db_connect(PRODUCT_DB) as connection:
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
        logger.exception("Could not record email delivery event.")

    return {
        "success": True,
        "message": message,
        "service": service,
        "document_title": document_title,
        "recipient_email": recipient_email,
        "filename": file_path.name,
    }


# ============================================================
# HEALTH AND STATUS
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
        "email": email_configuration_status(),
    }


@app.get("/api/email-delivery/status")
@app.get("/api/delivery/email/status")
def email_status():
    status = email_configuration_status()

    return {
        "success": bool(status.get("configured", False)),
        "provider": "Gmail SMTP",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "security": "STARTTLS",
        "payment_confirmation_required": False,
        "configuration": status,
    }


# ============================================================
# EMAIL DELIVERY ENDPOINTS
# ============================================================

@app.post("/api/delivery/email")
@app.post("/api/delivery/send-email")
@app.post("/api/email-delivery")
@app.post("/api/customer-care/email")
def deliver_document(payload: EmailDeliveryRequest):
    """
    Email the exact existing saved document.

    Payment confirmation is deliberately not checked.
    """

    try:
        return send_existing_document_email(
            recipient_email=payload.recipient_email,
            service=payload.service,
            document_title=payload.document_title,
            customer_name=payload.customer_name,
        )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    except RuntimeError as exc:
        logger.error("Email delivery unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))

    except Exception:
        logger.exception("Email delivery failed.")
        raise HTTPException(
            status_code=500,
            detail="Email delivery failed. Check Render logs.",
        )


@app.get("/api/delivery/email")
def deliver_document_get(
    recipient_email: str = Query(...),
    service: str = Query(...),
    document_title: str = Query(...),
    customer_name: Optional[str] = Query(None),
):
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
            detail="Email delivery failed. Check Render logs.",
        )


# ============================================================
# DOCUMENT LOOKUP AND DOWNLOAD
# ============================================================

@app.get("/api/document/find")
@app.get("/api/delivery/document")
def find_document(
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


@app.get("/api/download")
@app.get("/api/delivery/file")
@app.get("/api/delivery-file")
def download_document(
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


@app.get("/api/product/status")
def product_status(
    service: str = Query(...),
    title: str = Query(...),
):
    record = get_product_record(service, title)
    path = find_saved_document(service, title)

    return {
        "success": True,
        "service": service,
        "document_title": title,
        "document_found": path is not None,
        "filename": path.name if path else None,
        "product_registered": record is not None,
        "email_delivery_available": bool(
            email_configuration_status().get("configured", False)
        ),
    }


# ============================================================
# PAYMENT ORDER MANAGEMENT
# ============================================================

@app.post("/api/payment/create")
@app.post("/api/payments/create")
def create_payment(payload: ProductRequest):
    reference = "NPBC-" + secrets.token_hex(8).upper()
    now = utc_now()

    try:
        with db_connect(PAYMENT_DB) as connection:
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
                    reference,
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
            "order_reference": reference,
            "payment_status": "pending",
        }

    except Exception:
        logger.exception("Could not create payment order.")
        raise HTTPException(
            status_code=500,
            detail="Could not create payment order.",
        )


@app.get("/api/payment/status")
@app.get("/api/payments/status")
def payment_status(
    order_reference: str = Query(...),
):
    with db_connect(PAYMENT_DB) as connection:
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


@app.post("/api/payment/complete")
@app.post("/api/payments/complete")
def complete_payment(
    payload: PaymentStatusRequest,
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

    status = normalize_text(payload.payment_status).lower()

    if status not in {
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

    if status == "completed":
        status = "paid"

    with db_connect(PAYMENT_DB) as connection:
        cursor = connection.execute(
            """
            UPDATE payment_orders
            SET payment_status = ?, updated_at = ?
            WHERE order_reference = ?
            """,
            (
                status,
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
        "payment_status": status,
    }


@app.get("/api/payment/report")
@app.get("/api/payments/report")
def payment_report(
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    with db_connect(PAYMENT_DB) as connection:
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


# ============================================================
# BACK OFFICE
# ============================================================

def require_back_office(key):
    if not secrets.compare_digest(
        str(key or ""),
        str(BACK_OFFICE_ADMIN_KEY),
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized.",
        )


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
    }


@app.get("/api/back-office/products")
@app.get("/api/back-office/records")
def back_office_products(
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    with db_connect(PRODUCT_DB) as connection:
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


@app.get("/api/back-office/product")
def back_office_product(
    service: str = Query(...),
    title: str = Query(...),
    x_back_office_key: Optional[str] = Header(None),
):
    require_back_office(x_back_office_key)

    record = get_product_record(service, title)
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

    with db_connect(PRODUCT_DB) as connection:
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


# ============================================================
# TEMPORARY DOWNLOAD TOKENS
# ============================================================

def create_download_token(service, document_title):
    path = find_saved_document(service, document_title)

    if not path:
        raise FileNotFoundError("Saved document not found.")

    token = secrets.token_urlsafe(32)
    expires_at = time.time() + TOKEN_TTL_SECONDS

    with db_connect(PRODUCT_DB) as connection:
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
            f"{PUBLIC_API_BASE_URL}/api/download/token?token={token}"
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
        logger.exception("Could not create download token.")
        raise HTTPException(
            status_code=500,
            detail="Could not create download token.",
        )


@app.get("/api/download/token")
def download_by_token(token: str = Query(...)):
    with db_connect(PRODUCT_DB) as connection:
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

    path = valid_saved_path(record.get("file_path"))

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
