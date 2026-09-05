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


APP_VERSION = "payment-download-v3"

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

DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

OLD_API_BASE_URL = os.getenv(
    "OLD_API_BASE_URL",
    "",
).strip().rstrip("/")

INTERNAL_API_KEY = os.getenv(
    "INTERNAL_API_KEY",
    "",
).strip()

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
# BASIC HELPERS
# ============================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_money(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def normalized_status(value: Any) -> str:
    return (
        clean(value)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def is_verified(value: Any) -> bool:
    return normalized_status(value) in {
        "verified",
        "paid",
        "complete",
        "completed",
    }


def is_pending(value: Any) -> bool:
    return normalized_status(value) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def api_error(
    code: str,
    message: str,
    http_status: int = 400,
    **extra: Any,
):
    response = {
        "ok": False,
        "success": False,
        "error": code,
        "message": message,
    }

    response.update(extra)

    return JSONResponse(
        response,
        status_code=http_status,
    )


# ============================================================
# DATABASE
# ============================================================

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        str(DB_PATH)
    )

    connection.row_factory = sqlite3.Row

    return connection


def initialize_database() -> None:
    with get_db() as connection:

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
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_job
            ON payment_orders(job_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        connection.commit()


def row_to_dict(
    row: sqlite3.Row | None,
) -> dict[str, Any] | None:

    if row is None:
        return None

    return dict(row)


def get_payment_by_id(
    payment_id: str,
) -> dict[str, Any] | None:

    with get_db() as connection:

        row = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            """,
            (payment_id,),
        ).fetchone()

    return row_to_dict(row)


def get_latest_payment(
    job_id: str,
) -> dict[str, Any] | None:

    with get_db() as connection:

        row = connection.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    return row_to_dict(row)


def get_customer_care_queue() -> list[dict[str, Any]]:

    with get_db() as connection:

        rows = connection.execute(
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


def insert_payment(
    *,
    payment_id: str,
    job_id: str,
    customer_id: str,
    service: str,
    amount: float,
    payment_method: str,
    document_version: str,
    document_filename: str,
    document_payload: dict[str, Any],
) -> dict[str, Any]:

    timestamp = now()

    with get_db() as connection:

        connection.execute(
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
                payment_id,
                job_id,
                customer_id,
                service,
                amount,
                "NGN",
                payment_method,
                document_version,
                document_filename,
                json.dumps(
                    document_payload,
                    ensure_ascii=False,
                ),
                timestamp,
                timestamp,
                timestamp,
            ),
        )

        connection.commit()

    return (
        get_payment_by_id(
            payment_id
        )
        or {}
    )


def update_payment(
    payment_id: str,
    **changes: Any,
) -> dict[str, Any] | None:

    allowed_fields = {
        "payment_status",
        "payment_reference",
        "customer_note",
        "admin_note",
        "reported_at",
        "verified_at",
    }

    assignments: list[str] = []
    values: list[Any] = []

    for field in allowed_fields:

        if field in changes:
            assignments.append(
                f"{field} = ?"
            )

            values.append(
                changes[field]
            )

    assignments.append(
        "updated_at = ?"
    )

    values.append(
        now()
    )

    values.append(
        payment_id
    )

    with get_db() as connection:

        connection.execute(
            f"""
            UPDATE payment_orders
            SET {", ".join(assignments)}
            WHERE payment_id = ?
            """,
            values,
        )

        connection.commit()

    return get_payment_by_id(
        payment_id
    )


def record_download(
    payment_id: str,
) -> None:

    timestamp = now()

    with get_db() as connection:

        connection.execute(
            """
            UPDATE payment_orders
            SET
                download_count =
                    download_count + 1,
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
# OLD API COMMUNICATION
# ============================================================

def call_old_api(
    endpoint: str,
    job_id: str,
) -> Any:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured."
        )

    endpoint = "/" + endpoint.lstrip("/")

    url = (
        OLD_API_BASE_URL
        + endpoint
        + "?"
        + urllib.parse.urlencode(
            {
                "job_id": job_id,
            }
        )
    )

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "NaijaPocketPaymentAPI/3.0"
        ),
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

    try:

        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

            if not raw:
                return {}

            return json.loads(raw)

    except urllib.error.HTTPError as exc:

        raw = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Old API returned HTTP "
            f"{exc.code}: {raw[:500]}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            "Could not connect to old API: "
            f"{exc}"
        ) from exc


def find_document_payload(
    value: Any,
) -> dict[str, Any] | None:

    if not isinstance(
        value,
        dict,
    ):
        return None

    for key in (
        "data",
        "job",
        "result",
        "document",
        "review",
    ):

        nested = value.get(key)

        if isinstance(
            nested,
            dict,
        ):

            result = find_document_payload(
                nested
            )

            if result:
                return result

    document_keys = {
        "job_id",
        "pages",
        "document_pages",
        "review_pages",
        "document_text",
        "text",
        "status",
        "review_finished",
        "version_id",
    }

    if any(
        key in value
        for key in document_keys
    ):
        return value

    return None


def normalize_document_pages(
    value: Any,
) -> list[str]:

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
        dict,
    ):

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or value.get("page_text")
        )

        text = clean(text)

        return (
            [text]
            if text
            else []
        )

    if isinstance(
        value,
        (list, tuple),
    ):

        pages: list[str] = []

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
                )

            else:
                text = item

            text = clean(text)

            if text:
                pages.append(text)

        return pages

    return []


def extract_document(
    job_id: str,
) -> dict[str, Any]:

    errors: list[str] = []

    for endpoint in (
        "/api/review/pages",
        "/api/review",
    ):

        try:

            response = call_old_api(
                endpoint,
                job_id,
            )

            payload = (
                find_document_payload(
                    response
                )
            )

            if not payload:
                errors.append(
                    f"{endpoint}: no document payload"
                )
                continue

            pages = normalize_document_pages(
                payload.get("pages")
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
                or payload.get("text")
                or payload.get("content")
            )

            if not pages and document_text:
                pages = [
                    document_text
                ]

            if not pages:

                errors.append(
                    f"{endpoint}: no pages"
                )

                continue

            version_id = clean(
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

            if not version_id:

                fingerprint = json.dumps(
                    {
                        "job_id": job_id,
                        "pages": pages,
                        "text": document_text,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode(
                    "utf-8"
                )

                version_id = hashlib.sha256(
                    fingerprint
                ).hexdigest()[:24]

            billing = payload.get(
                "billing"
            )

            if not isinstance(
                billing,
                dict,
            ):
                billing = {}

            amount = as_money(
                payload.get(
                    "amount"
                )
            )

            if amount <= 0:
                amount = as_money(
                    payload.get(
                        "total_amount"
                    )
                )

            if amount <= 0:
                amount = as_money(
                    payload.get(
                        "price"
                    )
                )

            if amount <= 0:
                amount = as_money(
                    billing.get(
                        "amount"
                    )
                )

            if amount <= 0:
                amount = as_money(
                    billing.get(
                        "total"
                    )
                )

            filename = clean(
                payload.get(
                    "filename"
                )
                or payload.get(
                    "document_filename"
                )
            )

            if not filename:
                filename = (
                    f"naija_pocket_"
                    f"{job_id}.docx"
                )

            if not filename.lower().endswith(
                ".docx"
            ):
                filename += ".docx"

            current_status = (
                normalized_status(
                    payload.get(
                        "status"
                    )
                )
            )

            review_finished = bool(
                payload.get(
                    "review_finished"
                )
                or payload.get(
                    "review_complete"
                )
                or current_status
                in {
                    "review_complete",
                    "ready",
                    "completed",
                }
            )

            return {
                "job_id": job_id,
                "pages": pages,
                "document_text": document_text,
                "version_id": version_id,
                "amount": amount,
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
                "status": current_status,
                "review_finished": review_finished,
                "billing": billing,
                "raw": payload,
                "source_endpoint": endpoint,
            }

        except Exception as exc:

            errors.append(
                f"{endpoint}: {exc}"
            )

    raise RuntimeError(
        "Unable to retrieve the document "
        "from the old API. "
        + " | ".join(errors)
    )


def current_document(
    job_id: str,
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:

    try:

        return (
            extract_document(
                job_id
            ),
            None,
        )

    except Exception as exc:

        return (
            None,
            str(exc),
        )


def versions_match(
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


def has_document(
    document: dict[str, Any],
) -> bool:

    return bool(
        normalize_document_pages(
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


def snapshot_document(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id": document[
            "job_id"
        ],
        "pages": document[
            "pages"
        ],
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


# ============================================================
# PUBLIC PAYMENT FORMAT
# ============================================================

def public_payment(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    payment_verified = is_verified(
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
        "amount": as_money(
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
        "paid": payment_verified,
        "payment_verified": payment_verified,
        "download_unlocked": payment_verified,
    }


# ============================================================
# DOCX CREATION
# ============================================================

def make_docx_paragraph(
    text: str,
) -> str:

    pieces: list[str] = []

    lines = (
        str(text)
        .splitlines()
    )

    if not lines:
        lines = [""]

    for index, line in enumerate(
        lines
    ):

        if index:
            pieces.append(
                "<w:br/>"
            )

        pieces.append(
            "<w:r>"
            "<w:rPr>"
            '<w:sz w:val="24"/>'
            "</w:rPr>"
            f'<w:t xml:space="preserve">'
            f'{escape(str(line))}'
            f"</w:t>"
            "</w:r>"
        )

    return (
        "<w:p>"
        + "".join(pieces)
        + "</w:p>"
    )


def create_docx(
    pages: list[str],
    filename: str,
) -> Path:

    safe_filename = Path(
        filename
    ).name

    if not safe_filename.lower().endswith(
        ".docx"
    ):
        safe_filename += ".docx"

    output = (
        DOWNLOAD_DIR
        / (
            uuid.uuid4().hex
            + "_"
            + safe_filename
        )
    )

    body: list[str] = []

    for index, page in enumerate(
        pages
    ):

        if index:
            body.append(
                "<w:p>"
                "<w:r>"
                '<w:br w:type="page"/>'
                "</w:r>"
                "</w:p>"
            )

        body.append(
            make_docx_paragraph(
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
        + "".join(body)
        +
        "<w:sectPr>"
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar '
        'w:top="1134" '
        'w:right="1134" '
        'w:bottom="1134" '
        'w:left="1134"/>'
        "</w:sectPr>"
        "</w:body>"
        "</w:document>"
    )

    styles_xml = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:styles '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:docDefaults>"
        "<w:rPrDefault>"
        "<w:rPr>"
        '<w:rFonts '
        'w:ascii="Arial" '
        'w:hAnsi="Arial"/>'
        '<w:sz w:val="24"/>'
        "</w:rPr>"
        "</w:rPrDefault>"
        "</w:docDefaults>"
        "</w:styles>"
    )

    content_types = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/'
        'package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" '
        'ContentType="application/xml"/>'
        '<Override '
        'PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override '
        'PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )

    root_relationships = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship '
        'Id="rId1" '
        'Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    document_relationships = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship '
        'Id="rId1" '
        'Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
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
            root_relationships,
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
            document_relationships,
        )

    return output


# ============================================================
# REQUEST BODY
# ============================================================

async def read_json_body(
    request: Request,
) -> dict[str, Any]:

    try:

        value = await request.json()

        if isinstance(
            value,
            dict,
        ):
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
async def health():

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
# DIAGNOSTIC
# ============================================================

@app.get(
    "/api/payment/diagnostic"
)
async def payment_diagnostic():

    result: dict[str, Any] = {
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
        "database_exists": DB_PATH.exists(),
        "old_api": {
            "configured": bool(
                OLD_API_BASE_URL
            ),
            "reachable": False,
            "message": "",
        },
    }

    if not OLD_API_BASE_URL:

        result["old_api"]["message"] = (
            "OLD_API_BASE_URL is not configured."
        )

        return result

    try:

        parsed = urllib.parse.urlparse(
            OLD_API_BASE_URL
        )

        if not parsed.scheme:
            raise RuntimeError(
                "OLD_API_BASE_URL has no URL scheme."
            )

        if not parsed.netloc:
            raise RuntimeError(
                "OLD_API_BASE_URL has no hostname."
            )

        request = urllib.request.Request(
            OLD_API_BASE_URL
            + "/health",
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "NaijaPocketPaymentDiagnostic/3.0"
                ),
            },
            method="GET",
        )

        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace",
            )

            result["old_api"][
                "reachable"
            ] = True

            result["old_api"][
                "http_status"
            ] = response.status

            try:
                result["old_api"][
                    "response"
                ] = json.loads(raw)
            except Exception:
                result["old_api"][
                    "response"
                ] = raw[:1000]

            result["old_api"][
                "message"
            ] = (
                "Old API health endpoint "
                "is reachable."
            )

    except Exception as exc:

        result["old_api"][
            "message"
        ] = str(exc)

    return result


# ============================================================
# CREATE PAYMENT
# ============================================================

@app.post(
    "/api/payment/create"
)
async def create_payment(
    request: Request,
    job_id: str | None = None,
    amount: float | None = None,
    payment_method: str | None = None,
    customer_id: str | None = None,
    service: str | None = None,
):

    body = await read_json_body(
        request
    )

    job_id = clean(
        job_id
        or body.get("job_id")
    )

    if not job_id:

        return api_error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
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

    payment_method = clean(
        payment_method
        or body.get(
            "payment_method"
        )
    ) or DEFAULT_PAYMENT_METHOD

    if amount is None:
        amount = body.get(
            "amount"
        )

    document, lookup_error = (
        current_document(
            job_id
        )
    )

    if lookup_error or not document:

        return api_error(
            "DOCUMENT_LOOKUP_FAILED",
            (
                "The existing document "
                "could not be retrieved."
            ),
            502,
            detail=lookup_error,
        )

    if not has_document(
        document
    ):

        return api_error(
            "DOCUMENT_EMPTY",
            (
                "There is no completed "
                "document available "
                "for payment."
            ),
            409,
        )

    if (
        not document.get(
            "review_finished"
        )
    ):

        return api_error(
            "DOCUMENT_NOT_READY",
            (
                "The document is not "
                "ready for payment yet."
            ),
            409,
            status=document.get(
                "status"
            ),
        )

    final_amount = (
        as_money(
            document.get(
                "amount"
            )
        )
        or as_money(
            amount
        )
    )

    if final_amount <= 0:

        return api_error(
            "AMOUNT_NOT_AVAILABLE",
            (
                "No payment amount "
                "was supplied."
            ),
            409,
        )

    existing = get_latest_payment(
        job_id
    )

    if (
        existing
        and versions_match(
            existing,
            document,
        )
        and normalized_status(
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

        payment_verified = is_verified(
            existing.get(
                "payment_status"
            )
        )

        return {
            "ok": True,
            "success": True,
            "message": (
                "Existing payment record "
                "returned."
            ),
            "payment": public_payment(
                existing
            ),
            "payment_id": existing[
                "payment_id"
            ],
            "amount": as_money(
                existing[
                    "amount"
                ]
            ),
            "currency": "NGN",
            "payment_status": existing[
                "payment_status"
            ],
            "payment_pending": (
                not payment_verified
            ),
            "status": (
                existing[
                    "payment_status"
                ]
            ),
            "paid": payment_verified,
            "payment_verified": (
                payment_verified
            ),
            "download_unlocked": (
                payment_verified
            ),
            "version_id": document[
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
                "customer_id",
                "",
            )
        ),
        service=(
            service
            or document.get(
                "service",
                "",
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
        document_payload=snapshot_document(
            document
        ),
    )

    return {
        "ok": True,
        "success": True,
        "message": (
            "Payment created. "
            "Customer Care can now "
            "verify the payment."
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
        "version_id": document[
            "version_id"
        ],
    }


# ============================================================
# REPORT PAYMENT
# ============================================================

@app.post(
    "/api/payment/report"
)
async def report_payment(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    body = await read_json_body(
        request
    )

    payment_id = clean(
        payment_id
        or body.get(
            "payment_id"
        )
    )

    job_id = clean(
        job_id
        or body.get(
            "job_id"
        )
    )

    payment_reference = clean(
        payment_reference
        or body.get(
            "payment_reference"
        )
        or body.get(
            "reference"
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
    )

    payment = (
        get_payment_by_id(
            payment_id
        )
        if payment_id
        else (
            get_latest_payment(
                job_id
            )
            if job_id
            else None
        )
    )

    if not payment:

        return api_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    document, lookup_error = (
        current_document(
            payment["job_id"]
        )
    )

    if lookup_error or not document:

        return api_error(
            "DOCUMENT_LOOKUP_FAILED",
            (
                "The current document "
                "could not be checked."
            ),
            502,
            detail=lookup_error,
        )

    if not versions_match(
        payment,
        document,
    ):

        return api_error(
            "PAYMENT_DOCUMENT_CHANGED",
            (
                "The document changed "
                "after this payment was "
                "created. A new payment "
                "is required."
            ),
            409,
        )

    if is_verified(
        payment[
            "payment_status"
        ]
    ):

        return {
            "ok": True,
            "success": True,
            "message": (
                "Payment is already verified."
            ),
            "payment": public_payment(
                payment
            ),
            "paid": True,
            "payment_verified": True,
            "download_unlocked": True,
        }

    payment = update_payment(
        payment[
            "payment_id"
        ],
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
            "Customer Care verification "
            "is still required."
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

@app.get(
    "/api/payment/status"
)
async def payment_status(
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
):

    payment = (
        get_payment_by_id(
            clean(payment_id)
        )
        if clean(payment_id)
        else (
            get_latest_payment(
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

    if (
        document
        and not versions_match(
            payment,
            document,
        )
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
            "amount": as_money(
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

    payment_verified = is_verified(
        payment[
            "payment_status"
        ]
    )

    billing = ""

    if document:

        billing = document.get(
            "billing",
            "",
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
        "amount": as_money(
            payment[
                "amount"
            ]
        ),
        "billing": billing,
        "payment_status": payment[
            "payment_status"
        ],
        "payment_pending": (
            is_pending(
                payment[
                    "payment_status"
                ]
            )
            and not payment_verified
        ),
        "status": (
            "pending"
            if (
                is_pending(
                    payment[
                        "payment_status"
                    ]
                )
                and not payment_verified
            )
            else payment[
                "payment_status"
            ]
        ),
        "paid": payment_verified,
        "payment_verified": payment_verified,
        "download_unlocked": payment_verified,
        "document_check": (
            "unavailable"
            if lookup_error
            else "current"
        ),
        "version_id": (
            document.get(
                "version_id"
            )
            if document
            else payment.get(
                "document_version"
            )
        ),
    }


# ============================================================
# PAYMENT COMPLETE COMPATIBILITY
# ============================================================

@app.post(
    "/api/payment/complete"
)
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
# CUSTOMER CARE QUEUE
# ============================================================

@app.get(
    "/api/customer-care/payments"
)
async def customer_care_payments():

    payments = get_customer_care_queue()

    return {
        "ok": True,
        "success": True,
        "count": len(
            payments
        ),
        "payments": [
            public_payment(
                payment
            )
            for payment in payments
        ],
    }


# ============================================================
# CUSTOMER CARE VERIFICATION
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

    body = await read_json_body(
        request
    )

    payment_id = clean(
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

    note = clean(
        note
        or body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
    )

    if not payment_id:

        return api_error(
            "PAYMENT_ID_REQUIRED",
            "payment_id is required.",
        )

    payment = get_payment_by_id(
        payment_id
    )

    if not payment:

        return api_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    if not verified:

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
                "Payment marked as rejected."
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

        return api_error(
            "DOCUMENT_LOOKUP_FAILED",
            (
                "The current document "
                "could not be checked."
            ),
            502,
            detail=lookup_error,
        )

    if not versions_match(
        payment,
        document,
    ):

        return api_error(
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
            "Download is now unlocked."
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

@app.get(
    "/api/download"
)
async def download_document(
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
):

    payment = (
        get_payment_by_id(
            clean(payment_id)
        )
        if clean(payment_id)
        else (
            get_latest_payment(
                clean(job_id)
            )
            if clean(job_id)
            else None
        )
    )

    if not payment:

        return api_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    if not is_verified(
        payment[
            "payment_status"
        ]
    ):

        return api_error(
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

        return api_error(
            "DOCUMENT_LOOKUP_FAILED",
            (
                "The current document "
                "could not be retrieved."
            ),
            502,
            detail=lookup_error,
        )

    if not versions_match(
        payment,
        document,
    ):

        return api_error(
            "DOCUMENT_CHANGED",
            (
                "The document changed "
                "after payment verification. "
                "This payment cannot unlock "
                "the changed document."
            ),
            409,
        )

    pages = normalize_document_pages(
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
            pages = [
                text
            ]

    if not pages:

        return api_error(
            "DOCUMENT_EMPTY",
            (
                "There is no document "
                "available for download."
            ),
            409,
        )

    try:

        output = create_docx(
            pages,
            document[
                "filename"
            ],
        )

    except Exception as exc:

        return api_error(
            "DOWNLOAD_BUILD_FAILED",
            (
                "The document could "
                "not be prepared "
                "for download."
            ),
            500,
            detail=str(exc),
        )

    record_download(
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
            document[
                "filename"
            ]
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

@app.on_event(
    "startup"
)
async def startup():

    initialize_database()

    print(
        "[PAYMENT API] "
        f"Started {APP_VERSION}"
    )

    print(
        "[PAYMENT API] "
        "Old API configured="
        f"{bool(OLD_API_BASE_URL)}"
    )

    print(
        "[PAYMENT API] "
        f"Database={DB_PATH}"
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
