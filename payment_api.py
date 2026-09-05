from __future__ import annotations

import hashlib
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


APP_VERSION = "payment-download-v2"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(
    os.getenv(
        "PAYMENT_DB_PATH",
        str(BASE_DIR / "payment_gateway.db"),
    )
)

DOWNLOAD_DIR = Path(
    os.getenv(
        "DOWNLOAD_DIR",
        str(BASE_DIR / "downloads"),
    )
)

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

OLD_API_BASE_URL = os.getenv(
    "OLD_API_BASE_URL",
    "",
).strip().rstrip("/")

INTERNAL_API_KEY = os.getenv(
    "INTERNAL_API_KEY",
    "",
).strip()

DEFAULT_METHOD = "bank_transfer"


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

def now() -> str:
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


def status(value: Any) -> str:
    return (
        clean(value)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def verified(value: Any) -> bool:
    return status(value) in {
        "verified",
        "paid",
        "complete",
        "completed",
    }


def pending(value: Any) -> bool:
    return status(value) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def error(
    code: str,
    message: str,
    http: int = 400,
    **extra: Any,
):
    data = {
        "ok": False,
        "success": False,
        "error": code,
        "message": message,
    }

    data.update(extra)

    return JSONResponse(
        data,
        status_code=http,
    )


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        str(DB_PATH)
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db() -> None:
    with db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE NOT NULL,
                job_id TEXT NOT NULL,
                customer_id TEXT,
                service TEXT,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'NGN',
                payment_method TEXT NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'reported',
                payment_reference TEXT,
                customer_note TEXT,
                admin_note TEXT,
                document_version TEXT NOT NULL,
                document_filename TEXT,
                document_payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reported_at TEXT,
                verified_at TEXT,
                downloaded_at TEXT,
                download_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_po_job
            ON payment_orders(job_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_po_status
            ON payment_orders(payment_status)
            """
        )

        connection.commit()


def row_to_dict(
    value: sqlite3.Row | None,
) -> dict[str, Any] | None:
    if value is None:
        return None

    return dict(value)


def get_payment(
    payment_id: str,
) -> dict[str, Any] | None:

    with db() as connection:
        value = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            """,
            (payment_id,),
        ).fetchone()

    return row_to_dict(value)


def latest_payment(
    job_id: str,
) -> dict[str, Any] | None:

    with db() as connection:
        value = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    return row_to_dict(value)


def payment_queue() -> list[dict[str, Any]]:

    with db() as connection:
        values = connection.execute(
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

    return [dict(value) for value in values]


def insert_payment(
    **data: Any,
) -> dict[str, Any]:

    timestamp = now()

    with db() as connection:
        connection.execute(
            """
            INSERT INTO payment_orders
            (
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
                updated_at,
                reported_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                'reported',
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                data["payment_id"],
                data["job_id"],
                data.get("customer_id", ""),
                data.get("service", ""),
                data["amount"],
                "NGN",
                data["payment_method"],
                data["document_version"],
                data["document_filename"],
                json.dumps(
                    data["document_payload"],
                    ensure_ascii=False,
                ),
                timestamp,
                timestamp,
                timestamp,
            ),
        )

        connection.commit()

    return get_payment(
        data["payment_id"]
    ) or {}


def update_payment(
    payment_id: str,
    **changes: Any,
) -> dict[str, Any] | None:

    existing = get_payment(payment_id)

    if not existing:
        return None

    fields: list[str] = []
    values: list[Any] = []

    allowed = (
        "payment_status",
        "payment_reference",
        "customer_note",
        "admin_note",
        "reported_at",
        "verified_at",
    )

    for field in allowed:
        if field in changes and changes[field] is not None:
            fields.append(f"{field} = ?")
            values.append(changes[field])

    fields.append("updated_at = ?")
    values.append(now())

    values.append(payment_id)

    with db() as connection:
        connection.execute(
            f"""
            UPDATE payment_orders
            SET {", ".join(fields)}
            WHERE payment_id = ?
            """,
            values,
        )

        connection.commit()

    return get_payment(payment_id)


def count_download(
    payment_id: str,
) -> None:

    timestamp = now()

    with db() as connection:
        connection.execute(
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

        connection.commit()


# ============================================================
# OLD API BRIDGE
# ============================================================

def old_api_request(
    path: str,
    job_id: str,
) -> Any:

    if not OLD_API_BASE_URL:
        raise RuntimeError(
            "OLD_API_BASE_URL is not configured"
        )

    url = (
        OLD_API_BASE_URL
        + "/"
        + path.lstrip("/")
    )

    url += "?" + urllib.parse.urlencode(
        {
            "job_id": job_id,
        }
    )

    headers = {
        "Accept": "application/json",
        "User-Agent": "NaijaPocketPaymentAPI/2.0",
    }

    if INTERNAL_API_KEY:
        headers["X-Internal-API-Key"] = (
            INTERNAL_API_KEY
        )

    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=25,
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

            return json.loads(raw)

    except urllib.error.HTTPError as exc:

        raw = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Old API HTTP {exc.code}: {raw[:500]}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach old API: {exc}"
        ) from exc


def pick_payload(
    value: Any,
) -> dict[str, Any] | None:

    if not isinstance(value, dict):
        return None

    for key in (
        "data",
        "job",
        "result",
        "document",
    ):
        nested = value.get(key)

        if isinstance(nested, dict):
            found = pick_payload(nested)

            if found:
                return found

    if any(
        key in value
        for key in (
            "job_id",
            "pages",
            "document_pages",
            "document_text",
            "review_pages",
            "status",
        )
    ):
        return value

    return None


def normalize_pages(
    value: Any,
) -> list[str]:

    if isinstance(value, str):
        text = value.strip()

        return [text] if text else []

    if isinstance(value, dict):

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or value.get("page_text")
        )

        text = clean(text)

        return [text] if text else []

    if isinstance(value, (list, tuple)):

        output: list[str] = []

        for item in value:

            if isinstance(item, dict):
                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("body")
                    or item.get("page_text")
                )
            else:
                text = item

            text = clean(text)

            if text:
                output.append(text)

        return output

    return []


def fetch_document(
    job_id: str,
) -> dict[str, Any]:

    last_error: Exception | None = None

    for endpoint in (
        "/api/review/pages",
        "/api/review",
    ):

        try:

            response = old_api_request(
                endpoint,
                job_id,
            )

            payload = pick_payload(
                response
            )

            if not payload:
                continue

            document_pages = normalize_pages(
                payload.get("pages")
                or payload.get("document_pages")
                or payload.get("review_pages")
                or payload.get("page_texts")
            )

            document_text = clean(
                payload.get("document_text")
                or payload.get("text")
                or payload.get("content")
            )

            if not document_pages and document_text:
                document_pages = [
                    document_text
                ]

            if not document_pages:
                continue

            version_id = clean(
                payload.get("version_id")
                or payload.get("document_version")
                or payload.get("version")
            )

            if not version_id:

                source = json.dumps(
                    {
                        "job": job_id,
                        "pages": document_pages,
                        "text": document_text,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()

                version_id = hashlib.sha256(
                    source
                ).hexdigest()[:24]

            billing = payload.get(
                "billing"
            )

            if not isinstance(
                billing,
                dict,
            ):
                billing = {}

            amount = money(
                payload.get("amount")
                or payload.get("total_amount")
                or payload.get("price")
                or billing.get("amount")
                or billing.get("total")
                or billing.get("total_amount")
            )

            filename = clean(
                payload.get("filename")
                or payload.get("document_filename")
            )

            if not filename:
                filename = (
                    f"naija_pocket_{job_id}.docx"
                )

            if not filename.lower().endswith(
                ".docx"
            ):
                filename += ".docx"

            current_status = status(
                payload.get("status")
            )

            ready = bool(
                payload.get("review_finished")
                or payload.get("review_complete")
                or current_status
                == "review_complete"
            )

            return {
                "job_id": job_id,
                "pages": document_pages,
                "document_text": document_text,
                "version_id": version_id,
                "amount": amount,
                "filename": filename,
                "service": clean(
                    payload.get("service")
                ),
                "customer_id": clean(
                    payload.get("customer_id")
                ),
                "status": current_status,
                "ready": ready,
                "raw": payload,
            }

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        str(last_error)
        if last_error
        else
        "Old API returned no usable document"
    )


def current_document(
    job_id: str,
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:

    try:
        return fetch_document(
            job_id
        ), None

    except Exception as exc:
        return None, str(exc)


def same_version(
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


def document_has_content(
    document: dict[str, Any],
) -> bool:

    return bool(
        normalize_pages(
            document.get("pages")
        )
        or clean(
            document.get(
                "document_text"
            )
        )
    )


def snapshot(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id": document["job_id"],
        "pages": document["pages"],
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
    }


def public_payment(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    is_verified = verified(
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
        "currency": payment.get(
            "currency",
            "NGN",
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
        "paid": is_verified,
        "payment_verified": is_verified,
        "download_unlocked": is_verified,
    }


# ============================================================
# DOCX CREATION
# ============================================================

def docx_paragraph(
    text: str,
) -> str:

    runs: list[str] = []

    for index, line in enumerate(
        str(text).splitlines() or [""]
    ):

        if index:
            runs.append(
                "<w:br/>"
            )

        escaped = escape(
            line,
            {
                '"': "&quot;",
                "'": "&apos;",
            },
        )

        runs.append(
            '<w:r>'
            '<w:rPr>'
            '<w:sz w:val="24"/>'
            '</w:rPr>'
            f'<w:t xml:space="preserve">{escaped}</w:t>'
            '</w:r>'
        )

    return (
        "<w:p>"
        + "".join(runs)
        + "</w:p>"
    )


def make_docx(
    document_pages: list[str],
    filename: str,
) -> Path:

    safe_name = Path(
        filename
    ).name

    if not safe_name.lower().endswith(
        ".docx"
    ):
        safe_name += ".docx"

    output = (
        DOWNLOAD_DIR
        / f"{uuid.uuid4().hex}_{safe_name}"
    )

    body: list[str] = []

    for index, page in enumerate(
        document_pages
    ):

        if index:
            body.append(
                '<w:p>'
                '<w:r>'
                '<w:br w:type="page"/>'
                '</w:r>'
                '</w:p>'
            )

        body.append(
            docx_paragraph(page)
        )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        + "".join(body)
        +
        '<w:sectPr>'
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar '
        'w:top="1134" '
        'w:right="1134" '
        'w:bottom="1134" '
        'w:left="1134"/>'
        '</w:sectPr>'
        '</w:body>'
        '</w:document>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults>'
        '<w:rPrDefault>'
        '<w:rPr>'
        '<w:rFonts '
        'w:ascii="Arial" '
        'w:hAnsi="Arial"/>'
        '<w:sz w:val="24"/>'
        '</w:rPr>'
        '</w:rPrDefault>'
        '</w:docDefaults>'
        '</w:styles>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types '
        'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default '
        'Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default '
        'Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override '
        'PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override '
        'PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship '
        'Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )

    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship '
        'Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>'
    )

    with zipfile.ZipFile(
        output,
        "w",
        zipfile.ZIP_DEFLATED,
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

    return output


# ============================================================
# REQUEST BODY
# ============================================================

async def body_values(
    request: Request,
) -> dict[str, Any]:

    try:
        value = await request.json()

        if isinstance(value, dict):
            return value

    except Exception:
        pass

    return {}


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {
        "ok": True,
        "success": True,
        "service": "payment_api",
        "version": APP_VERSION,
        "old_api_configured": bool(
            OLD_API_BASE_URL
        ),
        "database": str(
            DB_PATH
        ),
    }


# ============================================================
# CREATE PAYMENT
# ============================================================

@app.post("/api/payment/create")
async def create_payment(
    request: Request,
    job_id: str | None = None,
    amount: float | None = None,
    payment_method: str | None = None,
    customer_id: str | None = None,
    service: str | None = None,
):

    body = await body_values(
        request
    )

    job_id = clean(
        job_id
        or body.get("job_id")
    )

    customer_id = clean(
        customer_id
        or body.get("customer_id")
    )

    service = clean(
        service
        or body.get("service")
    )

    payment_method = clean(
        payment_method
        or body.get("payment_method")
    ) or DEFAULT_METHOD

    if amount is None:
        amount = body.get("amount")

    if not job_id:
        return error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
        )

    document, lookup_error = (
        current_document(job_id)
    )

    if lookup_error or not document:
        return error(
            "DOCUMENT_LOOKUP_FAILED",
            "The existing document service could not be reached for this job.",
            502,
            detail=lookup_error,
        )

    if (
        not document["ready"]
        or not document_has_content(
            document
        )
    ):
        return error(
            "DOCUMENT_NOT_READY",
            "The document is not ready for payment yet.",
            409,
            status=document.get(
                "status"
            ),
        )

    final_amount = (
        money(
            document.get(
                "amount"
            )
        )
        or money(amount)
    )

    if final_amount <= 0:
        return error(
            "AMOUNT_NOT_AVAILABLE",
            "The document service did not provide an amount to pay.",
            409,
        )

    existing = latest_payment(
        job_id
    )

    if (
        existing
        and same_version(
            existing,
            document,
        )
        and status(
            existing.get(
                "payment_status"
            )
        )
        in {
            "reported",
            "pending",
            "verification_pending",
            "awaiting_verification",
            "verified",
            "paid",
            "complete",
            "completed",
        }
    ):

        is_verified = verified(
            existing.get(
                "payment_status"
            )
        )

        return {
            "ok": True,
            "success": True,
            "payment": public_payment(
                existing
            ),
            "payment_id": existing[
                "payment_id"
            ],
            "amount": money(
                existing["amount"]
            ),
            "payment_status": existing[
                "payment_status"
            ],
            "payment_pending": not is_verified,
            "status": (
                existing["payment_status"]
                if is_verified
                else "pending"
            ),
            "paid": is_verified,
            "payment_verified": is_verified,
            "download_unlocked": is_verified,
            "document_version": document[
                "version_id"
            ],
        }

    payment_id = (
        "NPB-"
        + uuid.uuid4().hex[:12].upper()
    )

    payment = insert_payment(
        payment_id=payment_id,
        job_id=job_id,
        customer_id=(
            customer_id
            or document.get(
                "customer_id"
            )
        ),
        service=(
            service
            or document.get(
                "service"
            )
        ),
        amount=final_amount,
        payment_method=payment_method,
        document_version=document[
            "version_id"
        ],
        document_filename=document[
            "filename"
        ],
        document_payload=snapshot(
            document
        ),
    )

    return {
        "ok": True,
        "success": True,
        "message": (
            "Payment started and placed "
            "in the Customer Care verification "
            "queue. Complete the payment, then "
            "tap I HAVE MADE PAYMENT."
        ),
        "payment": public_payment(
            payment
        ),
        "payment_id": payment_id,
        "amount": final_amount,
        "currency": "NGN",
        "payment_status": "reported",
        "payment_pending": True,
        "status": "pending",
        "paid": False,
        "payment_verified": False,
        "download_unlocked": False,
        "document_version": document[
            "version_id"
        ],
    }


# ============================================================
# REPORT PAYMENT
# ============================================================

@app.post("/api/payment/report")
async def report_payment(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    body = await body_values(
        request
    )

    payment_id = clean(
        payment_id
        or body.get("payment_id")
    )

    job_id = clean(
        job_id
        or body.get("job_id")
    )

    payment_reference = clean(
        payment_reference
        or body.get("payment_reference")
        or body.get("reference")
    )

    note = clean(
        note
        or body.get("note")
        or body.get("message")
    )

    payment = (
        get_payment(payment_id)
        if payment_id
        else (
            latest_payment(job_id)
            if job_id
            else None
        )
    )

    if not payment:
        return error(
            "PAYMENT_NOT_FOUND",
            "No payment record was found for this request.",
            404,
        )

    document, lookup_error = (
        current_document(
            payment["job_id"]
        )
    )

    if lookup_error or not document:
        return error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be checked.",
            502,
            detail=lookup_error,
        )

    if not same_version(
        payment,
        document,
    ):
        return error(
            "PAYMENT_DOCUMENT_CHANGED",
            "The document changed after this payment was created. A new payment is required.",
            409,
        )

    if verified(
        payment.get(
            "payment_status"
        )
    ):
        return {
            "ok": True,
            "success": True,
            "message": (
                "Payment has already "
                "been verified."
            ),
            "payment": public_payment(
                payment
            ),
            "paid": True,
            "payment_verified": True,
            "download_unlocked": True,
        }

    payment = update_payment(
        payment["payment_id"],
        payment_status="reported",
        payment_reference=(
            payment_reference
            or None
        ),
        customer_note=(
            note
            or None
        ),
        reported_at=now(),
    )

    return {
        "ok": True,
        "success": True,
        "message": (
            "Payment report received. "
            "Customer Care must verify it "
            "before download is unlocked."
        ),
        "payment": public_payment(
            payment
        ),
        "paid": False,
        "payment_verified": False,
        "download_unlocked": False,
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
):

    payment = (
        get_payment(
            clean(payment_id)
        )
        if clean(payment_id)
        else (
            latest_payment(
                clean(job_id)
            )
            if clean(job_id)
            else None
        )
    )

    if not payment:
        return {
            "ok": True,
            "success": True,
            "payment": None,
            "payment_status": "none",
            "payment_pending": False,
            "status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    document, lookup_error = (
        current_document(
            payment["job_id"]
        )
    )

    if lookup_error or not document:

        is_pending = pending(
            payment[
                "payment_status"
            ]
        )

        return {
            "ok": True,
            "success": True,
            "payment": public_payment(
                payment
            ),
            "payment_id": payment[
                "payment_id"
            ],
            "amount": money(
                payment["amount"]
            ),
            "payment_status": payment[
                "payment_status"
            ],
            "payment_pending": is_pending,
            "status": (
                "pending"
                if is_pending
                and not verified(
                    payment[
                        "payment_status"
                    ]
                )
                else payment[
                    "payment_status"
                ]
            ),
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
            "document_check": "unavailable",
        }

    if not same_version(
        payment,
        document,
    ):
        return {
            "ok": True,
            "success": True,
            "payment": public_payment(
                payment
            ),
            "payment_id": payment[
                "payment_id"
            ],
            "amount": money(
                payment["amount"]
            ),
            "payment_status": (
                "invalid_for_current_document"
            ),
            "payment_pending": False,
            "status": (
                "invalid_for_current_document"
            ),
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
            "document_check": "changed",
        }

    is_verified = verified(
        payment[
            "payment_status"
        ]
    )

    billing = (
        document
        .get("raw", {})
        .get("billing", "")
    )

    return {
        "ok": True,
        "success": True,
        "payment": public_payment(
            payment
        ),
        "payment_id": payment[
            "payment_id"
        ],
        "amount": money(
            payment["amount"]
        ),
        "billing": billing,
        "payment_status": payment[
            "payment_status"
        ],
        "payment_pending": (
            pending(
                payment[
                    "payment_status"
                ]
            )
            and not is_verified
        ),
        "status": (
            "pending"
            if pending(
                payment[
                    "payment_status"
                ]
            )
            and not is_verified
            else payment[
                "payment_status"
            ]
        ),
        "paid": is_verified,
        "payment_verified": is_verified,
        "download_unlocked": is_verified,
        "document_check": "current",
        "version_id": document[
            "version_id"
        ],
    }


# ============================================================
# COMPATIBILITY ENDPOINT
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    return await report_payment(
        request,
        payment_id,
        job_id,
        payment_reference,
        note,
    )


# ============================================================
# CUSTOMER CARE PAYMENT QUEUE
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():

    records = payment_queue()

    return {
        "ok": True,
        "success": True,
        "count": len(records),
        "payments": [
            public_payment(record)
            for record in records
        ],
    }


# ============================================================
# CUSTOMER CARE VERIFICATION
# ============================================================

@app.post(
    "/api/customer-care/payment/verify"
)
async def verify_payment(
    request: Request,
    payment_id: str | None = None,
    verified_flag: bool = True,
    note: str | None = None,
):

    body = await body_values(
        request
    )

    payment_id = clean(
        payment_id
        or body.get("payment_id")
    )

    if "verified" in body:
        verified_flag = bool(
            body.get(
                "verified"
            )
        )

    note = clean(
        note
        or body.get("note")
        or body.get("admin_note")
    )

    if not payment_id:
        return error(
            "PAYMENT_ID_REQUIRED",
            "payment_id is required.",
        )

    payment = get_payment(
        payment_id
    )

    if not payment:
        return error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    if not verified_flag:

        payment = update_payment(
            payment_id,
            payment_status="rejected",
            admin_note=(
                note
                or None
            ),
        )

        return {
            "ok": True,
            "success": True,
            "message": (
                "Payment marked "
                "as rejected."
            ),
            "payment": public_payment(
                payment
            ),
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    document, lookup_error = (
        current_document(
            payment["job_id"]
        )
    )

    if lookup_error or not document:
        return error(
            "DOCUMENT_LOOKUP_FAILED",
            (
                "Customer Care cannot "
                "verify payment because "
                "the current document "
                "could not be checked."
            ),
            502,
            detail=lookup_error,
        )

    if not same_version(
        payment,
        document,
    ):
        return error(
            "PAYMENT_DOCUMENT_CHANGED",
            (
                "This payment belongs "
                "to an older document "
                "version and cannot "
                "unlock the current "
                "document."
            ),
            409,
        )

    payment = update_payment(
        payment_id,
        payment_status="verified",
        admin_note=(
            note
            or None
        ),
        verified_at=now(),
    )

    return {
        "ok": True,
        "success": True,
        "message": (
            "Payment verified. "
            "Download is now unlocked "
            "for this document version."
        ),
        "payment": public_payment(
            payment
        ),
        "paid": True,
        "payment_verified": True,
        "download_unlocked": True,
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download_document(
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
):

    payment = (
        get_payment(
            clean(payment_id)
        )
        if clean(payment_id)
        else (
            latest_payment(
                clean(job_id)
            )
            if clean(job_id)
            else None
        )
    )

    if not payment:
        return error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    if not verified(
        payment[
            "payment_status"
        ]
    ):
        return error(
            "DOWNLOAD_LOCKED",
            (
                "Download remains locked "
                "until Customer Care "
                "verifies the payment."
            ),
            403,
            payment_status=payment[
                "payment_status"
            ],
            payment_id=payment[
                "payment_id"
            ],
            download_unlocked=False,
        )

    document, lookup_error = (
        current_document(
            payment["job_id"]
        )
    )

    if lookup_error or not document:
        return error(
            "DOCUMENT_LOOKUP_FAILED",
            (
                "The current document "
                "could not be retrieved."
            ),
            502,
            detail=lookup_error,
        )

    if not same_version(
        payment,
        document,
    ):
        return error(
            "DOCUMENT_CHANGED",
            (
                "The document changed "
                "after payment verification. "
                "This payment no longer "
                "unlocks the changed document."
            ),
            409,
        )

    if not document_has_content(
        document
    ):
        return error(
            "DOCUMENT_EMPTY",
            (
                "There is no document "
                "available for download."
            ),
            409,
        )

    document_pages = normalize_pages(
        document.get(
            "pages"
        )
    )

    if not document_pages:

        document_text = clean(
            document.get(
                "document_text"
            )
        )

        if document_text:
            document_pages = [
                document_text
            ]

    try:

        output = make_docx(
            document_pages,
            document["filename"],
        )

    except Exception as exc:

        return error(
            "DOWNLOAD_BUILD_FAILED",
            (
                "The document could "
                "not be prepared "
                "for download."
            ),
            500,
            detail=str(exc),
        )

    count_download(
        payment[
            "payment_id"
        ]
    )

    return FileResponse(
        str(output),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename=Path(
            document["filename"]
        ).name,
        headers={
            "X-Payment-ID": payment[
                "payment_id"
            ],
            "X-Download-Unlocked": "true",
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup() -> None:

    init_db()

    print(
        "[PAYMENT API] "
        f"{APP_VERSION} started"
    )

    print(
        "[PAYMENT API] "
        f"Old API configured="
        f"{bool(OLD_API_BASE_URL)}"
    )

    print(
        "[PAYMENT API] "
        f"Database={DB_PATH}"
    )


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
