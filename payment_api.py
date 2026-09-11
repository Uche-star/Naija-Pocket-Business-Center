
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


# ============================================================
# PAYMENT API
# Naija Pocket Business Center
# ============================================================

APP_VERSION = (
    "payment-download-v2-exact-review-snapshot-backoffice"
)

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = BASE_DIR / "payment_gateway.db"

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OLD_API_BASE_URL = (
    os.getenv(
        "OLD_API_BASE_URL",
        ""
    )
    .strip()
    .rstrip("/")
)

INTERNAL_API_KEY = (
    os.getenv(
        "INTERNAL_API_KEY",
        ""
    )
    .strip()
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center Payment API",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# TIME / TEXT HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def safe_filename(
    filename: str,
    fallback: str = "document.txt"
) -> str:

    name = clean(filename)

    if not name:
        name = fallback

    name = Path(name).name

    allowed = (
        ".txt",
        ".doc",
        ".docx",
        ".pdf",
    )

    if not name.lower().endswith(
        allowed
    ):
        name += ".txt"

    return name


def json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False
    )


def json_loads(
    value: Any,
    fallback: Any = None
) -> Any:

    if value is None:
        return fallback

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(
            str(value)
        )
    except Exception:
        return fallback


# ============================================================
# DATABASE
# ============================================================

def get_connection() -> sqlite3.Connection:

    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db() -> None:

    conn = get_connection()

    try:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                payment_id TEXT UNIQUE,

                job_id TEXT,
                version_id TEXT,

                customer_name TEXT,
                customer_phone TEXT,

                service TEXT,

                amount REAL DEFAULT 0,

                payment_method TEXT,

                status TEXT DEFAULT 'pending',

                payment_reference TEXT,
                payment_note TEXT,

                filename TEXT,

                pages_json TEXT,
                document_text TEXT,

                document_saved_path TEXT,
                document_saved_at TEXT,

                reported_at TEXT,
                verified_at TEXT,

                rejected_at TEXT,
                rejection_note TEXT,

                download_count INTEGER DEFAULT 0,
                downloaded_at TEXT,

                created_at TEXT,
                updated_at TEXT
            )
            """
        )

        conn.commit()

    finally:
        conn.close()


init_db()


# ============================================================
# DATABASE HELPERS
# ============================================================

def row_to_dict(
    row: sqlite3.Row | None
) -> dict[str, Any] | None:

    if row is None:
        return None

    return dict(row)


def get_payment_by_id(
    payment_id: str
) -> dict[str, Any] | None:

    payment_id = clean(payment_id)

    if not payment_id:
        return None

    conn = get_connection()

    try:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            LIMIT 1
            """,
            (payment_id,)
        ).fetchone()

        return row_to_dict(row)

    finally:
        conn.close()


def get_payment_by_job_version(
    job_id: str,
    version_id: str
) -> dict[str, Any] | None:

    conn = get_connection()

    try:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
              AND version_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                clean(job_id),
                clean(version_id)
            )
        ).fetchone()

        return row_to_dict(row)

    finally:
        conn.close()


def list_all_payments() -> list[dict[str, Any]]:

    conn = get_connection()

    try:

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

    finally:
        conn.close()


def get_payments_for_job(
    job_id: str
) -> list[dict[str, Any]]:

    conn = get_connection()

    try:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (clean(job_id),)
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        conn.close()


def update_payment(
    payment_id: str,
    **fields: Any
) -> None:

    payment_id = clean(payment_id)

    if not payment_id or not fields:
        return

    fields["updated_at"] = now_iso()

    columns = []
    values = []

    for key, value in fields.items():

        columns.append(
            f"{key} = ?"
        )

        values.append(value)

    values.append(payment_id)

    sql = (
        "UPDATE payment_orders "
        "SET "
        + ", ".join(columns)
        + " WHERE payment_id = ?"
    )

    conn = get_connection()

    try:

        conn.execute(
            sql,
            values
        )

        conn.commit()

    finally:
        conn.close()


# ============================================================
# EXACT DOCUMENT SNAPSHOT
# ============================================================

def saved_document_exists(
    payment: dict[str, Any]
) -> bool:

    path = clean(
        payment.get(
            "document_saved_path"
        )
    )

    if not path:
        return False

    return Path(path).is_file()


def saved_document_path(
    payment: dict[str, Any]
) -> Path | None:

    path = clean(
        payment.get(
            "document_saved_path"
        )
    )

    if not path:
        return None

    candidate = Path(path)

    if not candidate.is_absolute():
        candidate = (
            BASE_DIR / candidate
        )

    return candidate


def save_exact_document_snapshot(
    payment: dict[str, Any],
    pages: Any,
    document_text: str,
    filename: str
) -> tuple[bool, str]:

    payment_id = clean(
        payment.get(
            "payment_id"
        )
    )

    if not payment_id:
        return (
            False,
            "Missing payment ID."
        )

    # --------------------------------------------------------
    # NEVER replace an existing saved document.
    # --------------------------------------------------------

    if saved_document_exists(payment):

        existing = saved_document_path(
            payment
        )

        return (
            True,
            str(existing)
        )

    filename = safe_filename(
        filename
    )

    document_text = clean(
        document_text
    )

    if not document_text:

        return (
            False,
            "The reviewed document is empty."
        )

    try:

        file_path = (
            DOWNLOAD_DIR
            / f"{payment_id}_{filename}"
        )

        file_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        file_path.write_text(
            document_text,
            encoding="utf-8"
        )

        saved_at = now_iso()

        update_payment(
            payment_id,
            pages_json=json_dumps(
                pages
            ),
            document_text=document_text,
            filename=filename,
            document_saved_path=str(
                file_path
            ),
            document_saved_at=saved_at
        )

        return (
            True,
            str(file_path)
        )

    except Exception as exc:

        return (
            False,
            str(exc)
        )


# ============================================================
# CURRENT DOCUMENT FROM MAIN API
# ============================================================

async def get_current_document(
    job_id: str
) -> dict[str, Any] | None:

    job_id = clean(job_id)

    if not job_id:
        return None

    if not OLD_API_BASE_URL:
        return None

    headers = {}

    if INTERNAL_API_KEY:
        headers[
            "X-Internal-Key"
        ] = INTERNAL_API_KEY

    possible_paths = [
        f"/api/jobs/{job_id}",
        f"/api/job/{job_id}",
        f"/api/workspace/job/{job_id}",
    ]

    async with httpx.AsyncClient(
        timeout=30
    ) as client:

        for path in possible_paths:

            try:

                response = await client.get(
                    OLD_API_BASE_URL + path,
                    headers=headers
                )

                if response.status_code != 200:
                    continue

                data = response.json()

                if isinstance(
                    data,
                    dict
                ):

                    if isinstance(
                        data.get("job"),
                        dict
                    ):
                        return data["job"]

                    if isinstance(
                        data.get("data"),
                        dict
                    ):
                        return data["data"]

                    return data

            except Exception:
                continue

    return None


def extract_current_version(
    document: dict[str, Any] | None
) -> str:

    if not document:
        return ""

    candidates = [
        document.get(
            "version_id"
        ),
        document.get(
            "version"
        ),
        document.get(
            "document_version"
        ),
    ]

    for value in candidates:

        value = clean(value)

        if value:
            return value

    return ""


# ============================================================
# PAYMENT PUBLIC REPRESENTATION
# ============================================================

def payment_public(
    payment: dict[str, Any]
) -> dict[str, Any]:

    status = clean(
        payment.get(
            "status"
        )
    ).lower()

    saved = saved_document_exists(
        payment
    )

    verified = (
        status == "verified"
    )

    reported = (
        status == "reported"
        or status == "verified"
    )

    pages = json_loads(
        payment.get(
            "pages_json"
        ),
        []
    )

    return {
        "id": payment.get(
            "id"
        ),

        "payment_id": payment.get(
            "payment_id"
        ),

        "job_id": payment.get(
            "job_id"
        ),

        "version_id": payment.get(
            "version_id"
        ),

        "customer_name": payment.get(
            "customer_name"
        ),

        "customer_phone": payment.get(
            "customer_phone"
        ),

        "service": payment.get(
            "service"
        ),

        "amount": payment.get(
            "amount"
        ),

        "payment_method": payment.get(
            "payment_method"
        ),

        "status": status,

        "payment_reference": payment.get(
            "payment_reference"
        ),

        "payment_note": payment.get(
            "payment_note"
        ),

        "filename": payment.get(
            "filename"
        ),

        "pages": pages,

        "page_count": (
            len(pages)
            if isinstance(pages, list)
            else 0
        ),

        "document_saved": saved,

        "document_saved_path": (
            payment.get(
                "document_saved_path"
            )
            if saved
            else None
        ),

        "document_saved_at": payment.get(
            "document_saved_at"
        ),

        "reported_at": payment.get(
            "reported_at"
        ),

        "verified_at": payment.get(
            "verified_at"
        ),

        "rejected_at": payment.get(
            "rejected_at"
        ),

        "rejection_note": payment.get(
            "rejection_note"
        ),

        "download_count": payment.get(
            "download_count",
            0
        ),

        "downloaded_at": payment.get(
            "downloaded_at"
        ),

        "created_at": payment.get(
            "created_at"
        ),

        "updated_at": payment.get(
            "updated_at"
        ),

        "verified": verified,

        "download_unlocked": verified,

        "reported": reported
    }


# ============================================================
# BACK OFFICE SERVER AUTH
# ============================================================
#
# IMPORTANT:
#
# Back Office access is intentionally controlled by
# payment.html using its local key:
#
#     NPBC-2026
#
# The API must NOT require:
#     BACK_OFFICE_ADMIN_KEY
#     X-Admin-Key
#     Render environment variables
#
# This function remains only so the existing route structure
# can stay intact without changing the customer payment flow.
#
# ============================================================

def require_admin(
    request: Request | None = None,
    body: dict[str, Any] | None = None
) -> JSONResponse | None:

    # --------------------------------------------------------
    # No server-side Back Office authentication.
    #
    # The simple Back Office access gate is controlled by
    # payment.html.
    # --------------------------------------------------------

    return None


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def root():

    return {
        "service": "Naija Pocket Business Center Payment API",
        "version": APP_VERSION,
        "status": "ok",

        "back_office_server_auth": False,

        "message": (
            "Back Office access is controlled "
            "by Payment HTML."
        )
    }


@app.get("/health")
async def health():

    return {
        "status": "ok",
        "service": "payment_api",
        "version": APP_VERSION,
        "database": str(DB_PATH),
        "download_directory": str(
            DOWNLOAD_DIR
        ),
        "back_office_server_auth": False
    }


# ============================================================
# CREATE PAYMENT
# ============================================================

@app.post("/api/payment/create")
async def create_payment(
    request: Request
):

    try:

        body = await request.json()

    except Exception:

        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "INVALID_JSON",
                "message": (
                    "Invalid payment request."
                )
            }
        )

    job_id = clean(
        body.get("job_id")
    )

    version_id = clean(
        body.get("version_id")
    )

    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="Missing job_id."
        )

    if not version_id:
        raise HTTPException(
            status_code=400,
            detail="Missing version_id."
        )

    try:

        amount = float(
            body.get(
                "amount",
                0
            )
        )

    except Exception:

        amount = 0

    payment_method = clean(
        body.get(
            "payment_method"
        )
    )

    customer_name = clean(
        body.get(
            "customer_name"
        )
    )

    customer_phone = clean(
        body.get(
            "customer_phone"
        )
    )

    service = clean(
        body.get(
            "service"
        )
    )

    filename = safe_filename(
        body.get(
            "filename",
            "document.txt"
        )
    )

    pages = body.get(
        "pages",
        []
    )

    document_text = clean(
        body.get(
            "document_text"
        )
    )

    if not document_text:

        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "DOCUMENT_EMPTY",
                "message": (
                    "The reviewed document "
                    "cannot be empty."
                )
            }
        )

    # --------------------------------------------------------
    # Check the current job/version when the main API is
    # configured.
    # --------------------------------------------------------

    current_document = (
        await get_current_document(
            job_id
        )
    )

    if current_document:

        current_version = (
            extract_current_version(
                current_document
            )
        )

        if (
            current_version
            and current_version
            != version_id
        ):

            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": (
                        "VERSION_MISMATCH"
                    ),
                    "message": (
                        "The reviewed document "
                        "version is no longer "
                        "the current version."
                    ),
                    "current_version_id":
                        current_version,
                    "requested_version_id":
                        version_id
                }
            )

    # --------------------------------------------------------
    # Reuse existing payment for the exact same
    # job/version.
    # --------------------------------------------------------

    existing = (
        get_payment_by_job_version(
            job_id,
            version_id
        )
    )

    if existing:

        if not saved_document_exists(
            existing
        ):

            saved_ok, saved_path = (
                save_exact_document_snapshot(
                    existing,
                    pages,
                    document_text,
                    filename
                )
            )

            if not saved_ok:

                return JSONResponse(
                    status_code=500,
                    content={
                        "ok": False,
                        "error": (
                            "DOCUMENT_SAVE_FAILED"
                        ),
                        "message": (
                            "The exact reviewed "
                            "document could not "
                            "be saved."
                        ),
                        "details": saved_path
                    }
                )

            existing = (
                get_payment_by_id(
                    existing[
                        "payment_id"
                    ]
                )
                or existing
            )

        return {
            "ok": True,
            "existing": True,
            "payment": payment_public(
                existing
            ),
            "payment_id": existing[
                "payment_id"
            ],
            "document_saved": True,
            "document_saved_path":
                existing.get(
                    "document_saved_path"
                )
        }

    # --------------------------------------------------------
    # Create new payment record.
    # --------------------------------------------------------

    payment_id = (
        "NPBC-"
        + uuid.uuid4().hex[:12].upper()
    )

    created_at = now_iso()

    conn = get_connection()

    try:

        conn.execute(
            """
            INSERT INTO payment_orders (
                payment_id,
                job_id,
                version_id,
                customer_name,
                customer_phone,
                service,
                amount,
                payment_method,
                status,
                filename,
                pages_json,
                document_text,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                payment_id,
                job_id,
                version_id,
                customer_name,
                customer_phone,
                service,
                amount,
                payment_method,
                "pending",
                filename,
                json_dumps(pages),
                document_text,
                created_at,
                created_at
            )
        )

        conn.commit()

    except sqlite3.IntegrityError:

        conn.close()

        existing = (
            get_payment_by_id(
                payment_id
            )
        )

        if existing:
            return {
                "ok": True,
                "existing": True,
                "payment":
                    payment_public(
                        existing
                    ),
                "payment_id":
                    existing[
                        "payment_id"
                    ],
                "document_saved":
                    saved_document_exists(
                        existing
                    )
            }

        raise

    finally:

        try:
            conn.close()
        except Exception:
            pass

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": (
                    "PAYMENT_CREATE_FAILED"
                ),
                "message": (
                    "Payment record could "
                    "not be created."
                )
            }
        )

    # --------------------------------------------------------
    # CRITICAL:
    #
    # Save the exact reviewed document immediately.
    # This is independent of payment reporting.
    # --------------------------------------------------------

    saved_ok, saved_path = (
        save_exact_document_snapshot(
            payment,
            pages,
            document_text,
            filename
        )
    )

    if not saved_ok:

        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": (
                    "DOCUMENT_SAVE_FAILED"
                ),
                "message": (
                    "Payment record was created "
                    "but the exact reviewed "
                    "document could not be saved."
                ),
                "payment_id":
                    payment_id,
                "details":
                    saved_path
            }
        )

    payment = (
        get_payment_by_id(
            payment_id
        )
        or payment
    )

    return {
        "ok": True,
        "existing": False,
        "payment":
            payment_public(
                payment
            ),
        "payment_id":
            payment_id,
        "document_saved": True,
        "document_saved_path":
            payment.get(
                "document_saved_path"
            )
    }


# ============================================================
# REPORT PAYMENT
# ============================================================

@app.post("/api/payment/report")
async def report_payment(
    request: Request
):

    try:
        body = await request.json()
    except Exception:
        body = {}

    payment_id = clean(
        body.get("payment_id")
    )

    if not payment_id:

        raise HTTPException(
            status_code=400,
            detail="Missing payment_id."
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    job_id = clean(
        payment.get(
            "job_id"
        )
    )

    version_id = clean(
        payment.get(
            "version_id"
        )
    )

    current_document = (
        await get_current_document(
            job_id
        )
    )

    if current_document:

        current_version = (
            extract_current_version(
                current_document
            )
        )

        if (
            current_version
            and current_version
            != version_id
        ):

            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": (
                        "VERSION_MISMATCH"
                    ),
                    "message": (
                        "This payment belongs "
                        "to an older document "
                        "version."
                    ),
                    "current_version_id":
                        current_version,
                    "payment_version_id":
                        version_id
                }
            )

    if not saved_document_exists(
        payment
    ):

        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": (
                    "DOCUMENT_NOT_SAVED"
                ),
                "message": (
                    "The exact reviewed "
                    "document has not been "
                    "saved yet."
                )
            }
        )

    reference = clean(
        body.get(
            "payment_reference"
        )
    )

    note = clean(
        body.get(
            "payment_note"
        )
    )

    update_payment(
        payment_id,
        status="reported",
        payment_reference=reference,
        payment_note=note,
        reported_at=now_iso()
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
    )

    return {
        "ok": True,
        "message": (
            "Payment reported — "
            "awaiting verification."
        ),
        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    payment_id: str
):

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    job_id = clean(
        payment.get(
            "job_id"
        )
    )

    version_id = clean(
        payment.get(
            "version_id"
        )
    )

    current_document = (
        await get_current_document(
            job_id
        )
    )

    current_version = ""

    if current_document:

        current_version = (
            extract_current_version(
                current_document
            )
        )

    status = clean(
        payment.get(
            "status"
        )
    ).lower()

    verified = (
        status == "verified"
    )

    return {
        "ok": True,

        "payment_id":
            payment.get(
                "payment_id"
            ),

        "job_id":
            job_id,

        "version_id":
            version_id,

        "current_version_id":
            current_version,

        "status":
            status,

        "reported":
            status in (
                "reported",
                "verified"
            ),

        "verified":
            verified,

        "download_unlocked":
            verified,

        "document_saved":
            saved_document_exists(
                payment
            ),

        "document_saved_at":
            payment.get(
                "document_saved_at"
            ),

        "filename":
            payment.get(
                "filename"
            ),

        "download_count":
            payment.get(
                "download_count",
                0
            )
    }


# ============================================================
# COMPLETE PAYMENT
# ============================================================

@app.post("/api/payment/complete")
async def complete_payment(
    request: Request
):

    try:
        body = await request.json()
    except Exception:
        body = {}

    payment_id = clean(
        body.get("payment_id")
    )

    if not payment_id:

        raise HTTPException(
            status_code=400,
            detail="Missing payment_id."
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    if not saved_document_exists(
        payment
    ):

        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": (
                    "DOCUMENT_NOT_SAVED"
                ),
                "message": (
                    "The exact document "
                    "has not been saved."
                )
            }
        )

    update_payment(
        payment_id,
        status="verified",
        verified_at=now_iso()
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
    )

    return {
        "ok": True,
        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# CUSTOMER CARE — LIST PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments(
    request: Request
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payments = list_all_payments()

    return {
        "ok": True,

        "payments": [
            payment_public(
                payment
            )
            for payment in payments
        ],

        "count": len(payments)
    }


# ============================================================
# CUSTOMER CARE — VERIFY / REJECT
# ============================================================

@app.post(
    "/api/customer-care/payment/verify"
)
async def customer_care_verify(
    request: Request
):

    try:
        body = await request.json()
    except Exception:
        body = {}

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    if not payment_id:

        raise HTTPException(
            status_code=400,
            detail="Missing payment_id."
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    verified = body.get(
        "verified",
        True
    )

    verified = bool(
        verified
    )

    if not saved_document_exists(
        payment
    ):

        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": (
                    "DOCUMENT_NOT_SAVED"
                ),
                "message": (
                    "The exact saved document "
                    "is required before "
                    "download can be activated."
                )
            }
        )

    if verified:

        update_payment(
            payment_id,
            status="verified",
            verified_at=now_iso(),
            rejection_note=None
        )

        payment = (
            get_payment_by_id(
                payment_id
            )
        )

        return {
            "ok": True,

            "message": (
                "Download activated."
            ),

            "payment":
                payment_public(
                    payment
                )
        }

    note = clean(
        body.get(
            "note"
        )
    )

    update_payment(
        payment_id,
        status="rejected",
        rejected_at=now_iso(),
        rejection_note=note
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
    )

    return {
        "ok": True,

        "message": (
            "Payment rejected."
        ),

        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# BACK OFFICE LOGIN
# ============================================================
#
# No server-side key is required.
# payment.html performs the local access-key check.
# ============================================================

@app.post("/api/back-office/login")
async def back_office_login(
    request: Request
):

    return {
        "ok": True,
        "authenticated": True,
        "server_authentication": False,
        "message": (
            "Back Office access is "
            "controlled by Payment HTML."
        )
    }


# ============================================================
# BACK OFFICE — ALL SAVED PAYMENT DOCUMENTS
# ============================================================

@app.get("/api/back-office/payments")
async def back_office_payments(
    request: Request
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payments = list_all_payments()

    records = [
        payment_public(
            payment
        )
        for payment in payments
    ]

    pending_payments = [
        record
        for record in records
        if record.get(
            "status"
        ) == "reported"
    ]

    return {
        "ok": True,

        "payments": records,

        "records": records,

        "items": records,

        "count": len(records),

        "pending_payments":
            pending_payments,

        "pending_count":
            len(
                pending_payments
            )
    }


# ============================================================
# BACK OFFICE — JOB COMPATIBILITY ENDPOINT
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs(
    request: Request
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payments = list_all_payments()

    jobs = []

    for payment in payments:

        record = payment_public(
            payment
        )

        jobs.append(
            {
                "job_id":
                    record.get(
                        "job_id"
                    ),

                "version_id":
                    record.get(
                        "version_id"
                    ),

                "payment_id":
                    record.get(
                        "payment_id"
                    ),

                "service":
                    record.get(
                        "service"
                    ),

                "amount":
                    record.get(
                        "amount"
                    ),

                "status":
                    record.get(
                        "status"
                    ),

                "document_saved":
                    record.get(
                        "document_saved"
                    ),

                "filename":
                    record.get(
                        "filename"
                    ),

                "verified":
                    record.get(
                        "verified"
                    ),

                "download_unlocked":
                    record.get(
                        "download_unlocked"
                    ),

                "created_at":
                    record.get(
                        "created_at"
                    )
            }
        )

    return {
        "ok": True,
        "jobs": jobs,
        "count": len(jobs)
    }


# ============================================================
# BACK OFFICE — SINGLE PAYMENT
# ============================================================

@app.get(
    "/api/back-office/payment"
)
async def back_office_payment(
    request: Request,
    payment_id: str
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    return {
        "ok": True,
        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# BACK OFFICE — DOCUMENT INFORMATION
# ============================================================

@app.get(
    "/api/back-office/document-info"
)
async def back_office_document_info(
    request: Request,
    payment_id: str
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    pages = json_loads(
        payment.get(
            "pages_json"
        ),
        []
    )

    saved = saved_document_exists(
        payment
    )

    path = saved_document_path(
        payment
    )

    return {
        "ok": True,

        "payment_id":
            payment.get(
                "payment_id"
            ),

        "job_id":
            payment.get(
                "job_id"
            ),

        "version_id":
            payment.get(
                "version_id"
            ),

        "filename":
            payment.get(
                "filename"
            ),

        "pages":
            pages,

        "page_count":
            len(pages)
            if isinstance(
                pages,
                list
            )
            else 0,

        "document_saved":
            saved,

        "document_saved_at":
            payment.get(
                "document_saved_at"
            ),

        "document_saved_path":
            str(path)
            if saved and path
            else None,

        "status":
            payment.get(
                "status"
            ),

        "download_unlocked":
            payment.get(
                "status"
            ) == "verified"
    }


# ============================================================
# BACK OFFICE — VIEW EXACT SAVED DOCUMENT
# ============================================================

@app.get(
    "/api/back-office/document"
)
async def back_office_document(
    request: Request,
    payment_id: str
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    path = saved_document_path(
        payment
    )

    if not path or not path.is_file():

        raise HTTPException(
            status_code=404,
            detail=(
                "Saved document not found."
            )
        )

    return FileResponse(
        path=str(path),
        filename=safe_filename(
            payment.get(
                "filename"
            )
        ),
        media_type=(
            "text/plain; charset=utf-8"
        )
    )


# ============================================================
# BACK OFFICE — VERIFY PAYMENT
# ============================================================

@app.post(
    "/api/back-office/payment/verify"
)
async def back_office_payment_verify(
    request: Request
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    try:
        body = await request.json()
    except Exception:
        body = {}

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    if not payment_id:

        raise HTTPException(
            status_code=400,
            detail="Missing payment_id."
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Activation NEVER regenerates the document.
    # It only unlocks the already-saved exact snapshot.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):

        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": (
                    "DOCUMENT_NOT_SAVED"
                ),
                "message": (
                    "No saved document exists "
                    "for this payment."
                )
            }
        )

    update_payment(
        payment_id,
        status="verified",
        verified_at=now_iso()
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
    )

    return {
        "ok": True,

        "message":
            "Download activated.",

        "document_regenerated":
            False,

        "document_re_saved":
            False,

        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# BACK OFFICE — ACTIVATE DOWNLOAD
# ============================================================

@app.post(
    "/api/back-office/activate-download"
)
async def back_office_activate_download(
    request: Request
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    try:
        body = await request.json()
    except Exception:
        body = {}

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    if not payment_id:

        raise HTTPException(
            status_code=400,
            detail="Missing payment_id."
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    if not saved_document_exists(
        payment
    ):

        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": (
                    "DOCUMENT_NOT_SAVED"
                ),
                "message": (
                    "The exact saved document "
                    "does not exist."
                )
            }
        )

    update_payment(
        payment_id,
        status="verified",
        verified_at=now_iso()
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
    )

    return {
        "ok": True,

        "message":
            "Download activated.",

        "document_regenerated":
            False,

        "document_re_saved":
            False,

        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# BACK OFFICE — REJECT PAYMENT
# ============================================================

@app.post(
    "/api/back-office/payment/reject"
)
async def back_office_payment_reject(
    request: Request
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    try:
        body = await request.json()
    except Exception:
        body = {}

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    note = clean(
        body.get(
            "note"
        )
    )

    if not payment_id:

        raise HTTPException(
            status_code=400,
            detail="Missing payment_id."
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    update_payment(
        payment_id,
        status="rejected",
        rejected_at=now_iso(),
        rejection_note=note
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
    )

    return {
        "ok": True,

        "message":
            "Payment rejected.",

        "payment":
            payment_public(
                payment
            )
    }


# ============================================================
# CUSTOMER DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download_document(
    payment_id: str
):

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        raise HTTPException(
            status_code=404,
            detail="Payment not found."
        )

    status = clean(
        payment.get(
            "status"
        )
    ).lower()

    # --------------------------------------------------------
    # CUSTOMER DOWNLOAD REMAINS LOCKED UNTIL VERIFICATION.
    # --------------------------------------------------------

    if status != "verified":

        return JSONResponse(
            status_code=403,
            content={
                "ok": False,

                "error":
                    "DOWNLOAD_LOCKED",

                "message": (
                    "Your payment has not "
                    "yet been verified. "
                    "Download will be "
                    "available after "
                    "Customer Care activates it."
                ),

                "payment_id":
                    payment_id,

                "status":
                    status
            }
        )

    path = saved_document_path(
        payment
    )

    if not path or not path.is_file():

        return JSONResponse(
            status_code=404,
            content={
                "ok": False,

                "error":
                    "DOCUMENT_NOT_FOUND",

                "message": (
                    "The exact saved document "
                    "could not be found."
                )
            }
        )

    new_count = (
        int(
            payment.get(
                "download_count",
                0
            )
            or 0
        )
        + 1
    )

    update_payment(
        payment_id,
        download_count=new_count,
        downloaded_at=now_iso()
    )

    return FileResponse(
        path=str(path),

        filename=safe_filename(
            payment.get(
                "filename"
            )
        ),

        media_type=(
            "text/plain; charset=utf-8"
        )
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    init_db()

    print(
        "[PAYMENT API] "
        f"Version: {APP_VERSION}"
    )

    print(
        "[PAYMENT API] "
        f"Database: {DB_PATH}"
    )

    print(
        "[PAYMENT API] "
        f"Downloads: {DOWNLOAD_DIR}"
    )

    print(
        "[PAYMENT API] "
        "Back Office server authentication: "
        "disabled."
    )

    print(
        "[PAYMENT API] "
        "Back Office access key is handled "
        "by Payment HTML."
    ) 
