from __future__ import annotations

import hmac
import io
import json
import os
import sqlite3
import urllib.error
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
# Naija Pocket Business Center
# payment_api.py
#
# COMPLETE REPLACEMENT
#
# This file handles:
#   - Payment creation
#   - Customer payment reporting
#   - Payment status
#   - Customer Care verification
#   - Verified document download
#   - Back Office job synchronization
#
# IMPORTANT:
#   The document/intelligence API remains separate.
#   This file does NOT generate documents.
#
# BACK OFFICE FIX:
#   A reported payment is now exposed through:
#
#       GET /api/back-office/jobs
#
#   as:
#
#       Payment Reported — Awaiting Verification
#
#   It does NOT unlock download.
#   Customer Care verification is still required.
# ============================================================


APP_VERSION = "payment-download-v2-backoffice-sync"

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


# ============================================================
# EXISTING DOCUMENT API BRIDGE
# ============================================================

OLD_API_BASE_URL = (
    os.getenv("OLD_API_BASE_URL", "")
    .strip()
    .rstrip("/")
)

INTERNAL_API_KEY = (
    os.getenv("INTERNAL_API_KEY", "")
    .strip()
)


# ============================================================
# BACK OFFICE OPERATOR KEY
# ============================================================

BACK_OFFICE_KEY = (
    os.getenv("BACK_OFFICE_KEY", "").strip()
    or os.getenv("BACK_OFFICE_OPERATOR_KEY", "").strip()
    or os.getenv("OPERATOR_KEY", "").strip()
    or os.getenv("ADMIN_KEY", "").strip()
    or INTERNAL_API_KEY
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


def normalize_status(status: Any) -> str:
    return (
        clean(status)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def payment_is_reported(status: str) -> bool:
    return normalize_status(status) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def payment_is_verified(status: str) -> bool:
    return normalize_status(status) in {
        "verified",
        "completed",
        "complete",
        "paid",
    }


def payment_is_pending(status: str) -> bool:
    return normalize_status(status) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


# ============================================================
# SQLITE PAYMENT STORE
# ============================================================

def connect_db() -> sqlite3.Connection:

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(DB_PATH)
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db() -> None:

    with connect_db() as conn:

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

        # ----------------------------------------------------
        # Back Office activation fields.
        #
        # Added with ALTER TABLE so an existing
        # payment_gateway.db is preserved.
        # ----------------------------------------------------

        existing_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        if "back_office_activated" not in existing_columns:

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN back_office_activated
                INTEGER NOT NULL DEFAULT 0
                """
            )

        if "back_office_activated_at" not in existing_columns:

            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN back_office_activated_at TEXT
                """
            )

        conn.commit()


def row_to_dict(
    row: sqlite3.Row | None,
) -> dict[str, Any] | None:

    if row is None:
        return None

    return dict(row)


def get_payment(
    payment_id: str,
) -> dict[str, Any] | None:

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            """,
            (payment_id,),
        ).fetchone()

    return row_to_dict(row)


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:

    with connect_db() as conn:

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

    return row_to_dict(row)


def get_payments_for_job(
    job_id: str,
) -> list[dict[str, Any]]:

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (job_id,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def list_pending_payments() -> list[dict[str, Any]]:

    with connect_db() as conn:

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

    with connect_db() as conn:

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

    if status is not None:

        fields.append(
            "payment_status = ?"
        )

        values.append(status)

    if payment_reference is not None:

        fields.append(
            "payment_reference = ?"
        )

        values.append(
            payment_reference
        )

    if customer_note is not None:

        fields.append(
            "customer_note = ?"
        )

        values.append(
            customer_note
        )

    if admin_note is not None:

        fields.append(
            "admin_note = ?"
        )

        values.append(
            admin_note
        )

    if verified_at is not None:

        fields.append(
            "verified_at = ?"
        )

        values.append(
            verified_at
        )

    if reported_at is not None:

        fields.append(
            "reported_at = ?"
        )

        values.append(
            reported_at
        )

    fields.append(
        "updated_at = ?"
    )

    values.append(
        now_iso()
    )

    values.append(
        payment_id
    )

    with connect_db() as conn:

        conn.execute(
            f"""
            UPDATE payment_orders
            SET {', '.join(fields)}
            WHERE payment_id = ?
            """,
            tuple(values),
        )

        conn.commit()

    return get_payment(payment_id)


def increment_download(
    payment_id: str,
) -> dict[str, Any] | None:

    timestamp = now_iso()

    with connect_db() as conn:

        conn.execute(
            """
            UPDATE payment_orders

            SET download_count =
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

        conn.commit()

    return get_payment(payment_id)


# ============================================================
# OLD / EXISTING API BRIDGE
# ============================================================

def old_api_configured() -> bool:
    return bool(
        OLD_API_BASE_URL
    )


def old_api_request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = 25,
) -> Any:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured"
        )

    clean_path = (
        "/" + path.lstrip("/")
    )

    url = (
        OLD_API_BASE_URL
        + clean_path
    )

    if query:

        parts: list[str] = []

        from urllib.parse import quote

        for key, value in query.items():

            if value is None or value == "":
                continue

            parts.append(
                f"{quote(str(key))}="
                f"{quote(str(value))}"
            )

        if parts:

            url += "?" + "&".join(parts)

    headers = {
        "Accept": "application/json",
        "User-Agent":
            "NaijaPocketPaymentAPI/2.0",
    }

    if INTERNAL_API_KEY:

        headers[
            "X-Internal-API-Key"
        ] = INTERNAL_API_KEY

    data: bytes | None = None

    if body is not None:

        data = json.dumps(
            body,
            ensure_ascii=False,
        ).encode("utf-8")

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
            timeout=timeout,
        ) as response:

            raw = response.read()

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            if (
                "json"
                in content_type.lower()
            ):

                return json.loads(
                    raw.decode("utf-8")
                )

            try:

                return json.loads(
                    raw.decode("utf-8")
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
                raw.decode("utf-8")
            )

        except Exception:

            detail = raw.decode(
                "utf-8",
                errors="replace",
            )

        raise RuntimeError(
            f"Existing API returned HTTP "
            f"{exc.code}: {detail}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach existing API: {exc}"
        ) from exc


def extract_job_payload(
    response: Any,
) -> dict[str, Any] | None:

    if not isinstance(response, dict):
        return None

    candidates: list[Any] = [
        response
    ]

    for key in (
        "data",
        "job",
        "result",
        "document",
    ):

        value = response.get(key)

        if isinstance(value, dict):

            candidates.append(
                value
            )

    for candidate in candidates:

        if any(
            key in candidate
            for key in (
                "job_id",
                "pages",
                "document_pages",
                "document_text",
                "text",
                "content",
            )
        ):

            return candidate

    return None


def normalize_pages(
    value: Any,
) -> list[str]:

    if value is None:
        return []

    if isinstance(value, str):

        return (
            [value]
            if value.strip()
            else []
        )

    if isinstance(
        value,
        (tuple, list),
    ):

        output: list[str] = []

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
                    or ""
                )

                if str(text).strip():

                    output.append(
                        str(text).strip()
                    )

            elif str(item).strip():

                output.append(
                    str(item).strip()
                )

        return output

    if isinstance(value, dict):

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or value.get("page_text")
            or ""
        )

        return (
            [str(text).strip()]
            if str(text).strip()
            else []
        )

    return (
        [str(value).strip()]
        if str(value).strip()
        else []
    )


def fetch_current_document(
    job_id: str,
) -> dict[str, Any]:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured. "
            "Set it to the base URL of the existing API."
        )

    last_error: Exception | None = None

    paths = [
        "/api/review/pages",
        "/api/review",
    ]

    for path in paths:

        try:

            response = old_api_request(
                "GET",
                path,
                query={
                    "job_id": job_id
                },
            )

            payload = extract_job_payload(
                response
            )

            if payload:

                return normalize_document_payload(
                    payload,
                    job_id,
                )

        except Exception as exc:

            last_error = exc

    if last_error:

        raise last_error

    raise RuntimeError(
        "The existing API returned no usable "
        "document for this job."
    )


def normalize_document_payload(
    payload: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:

    pages = normalize_pages(
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
        payload.get("document_text")
        or payload.get("text")
        or payload.get("content")
        or payload.get("document")
    )

    if not pages and document_text:

        pages = [
            document_text
        ]

    status = clean(
        payload.get("status")
    )

    review_finished = bool(
        payload.get(
            "review_finished"
        )
        or payload.get(
            "review_complete"
        )
        or normalize_status(status)
        == "review_complete"
    )

    billing_value = payload.get(
        "billing"
    )

    amount = money(
        payload.get("amount")
        or payload.get("total_amount")
        or payload.get("price")
        or (
            billing_value.get("total")
            if isinstance(
                billing_value,
                dict,
            )
            else 0
        )
    )

    if (
        amount <= 0
        and isinstance(
            billing_value,
            dict,
        )
    ):

        amount = money(
            billing_value.get(
                "amount"
            )
            or billing_value.get(
                "total_amount"
            )
            or billing_value.get(
                "price"
            )
        )

    version = clean(
        payload.get("version_id")
        or payload.get(
            "document_version"
        )
        or payload.get("version")
    )

    if not version:

        import hashlib

        digest_source = json.dumps(
            {
                "job_id": job_id,
                "pages": pages,
                "text": document_text,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

        version = hashlib.sha256(
            digest_source
        ).hexdigest()[:24]

    filename = clean(
        payload.get("filename")
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
        "status": status,
        "review_finished": review_finished,
        "pages": pages,
        "document_text": document_text,
        "amount": amount,
        "version_id": version,
        "filename": filename,
        "service": clean(
            payload.get("service")
        ),
        "customer_id": clean(
            payload.get("customer_id")
        ),
        "raw": payload,
    }


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


# ============================================================
# DOCUMENT SNAPSHOT HELPERS
# ============================================================

def snapshot_payload(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id":
            document.get("job_id"),

        "pages":
            document.get("pages", []),

        "document_text":
            document.get(
                "document_text",
                "",
            ),

        "service":
            document.get(
                "service",
                "",
            ),

        "filename":
            document.get(
                "filename",
                "",
            ),

        "version_id":
            document.get(
                "version_id",
                "",
            ),

        "customer_id":
            document.get(
                "customer_id",
                "",
            ),
    }


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any]:

    raw = (
        payment.get(
            "document_payload"
        )
        or "{}"
    )

    try:

        data = json.loads(raw)

        if isinstance(
            data,
            dict,
        ):

            return data

    except Exception:
        pass

    return {}


def document_has_content(
    document: dict[str, Any],
) -> bool:

    pages = normalize_pages(
        document.get("pages")
    )

    return bool(
        pages
        or clean(
            document.get(
                "document_text"
            )
        )
    )


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

    if not document_has_content(
        document
    ):

        return False

    if document.get(
        "review_finished"
    ):

        return True

    return (
        normalize_status(
            document.get(
                "status"
            )
        )
        == "review_complete"
    )


# ============================================================
# DOCX CREATION
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

    for index, line in enumerate(
        lines
    ):

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
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
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
# PAYMENT RESPONSE
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    verified = payment_is_verified(
        status
    )

    reported = payment_is_reported(
        status
    )

    pending = payment_is_pending(
        status
    )

    return {
        "id":
            payment.get("id"),

        "payment_id":
            payment.get("payment_id"),

        "job_id":
            payment.get("job_id"),

        "customer_id":
            payment.get("customer_id"),

        "service":
            payment.get("service"),

        "amount":
            money(
                payment.get("amount")
            ),

        "currency":
            payment.get(
                "currency",
                DEFAULT_CURRENCY,
            ),

        "payment_method":
            payment.get(
                "payment_method"
            ),

        "payment_status":
            payment.get(
                "payment_status"
            ),

        "payment_reference":
            payment.get(
                "payment_reference"
            ),

        "customer_note":
            payment.get(
                "customer_note"
            ),

        "admin_note":
            payment.get(
                "admin_note"
            ),

        "document_version":
            payment.get(
                "document_version"
            ),

        "document_filename":
            payment.get(
                "document_filename"
            ),

        "created_at":
            payment.get(
                "created_at"
            ),

        "updated_at":
            payment.get(
                "updated_at"
            ),

        "reported_at":
            payment.get(
                "reported_at"
            ),

        "verified_at":
            payment.get(
                "verified_at"
            ),

        "downloaded_at":
            payment.get(
                "downloaded_at"
            ),

        "download_count":
            payment.get(
                "download_count",
                0,
            ),

        "paid":
            verified,

        "payment_verified":
            verified,

        "payment_pending":
            pending
            and not verified,

        "payment_reported":
            reported
            or verified,

        "download_unlocked":
            verified,

        "back_office_activated":
            bool(
                payment.get(
                    "back_office_activated"
                )
            ),

        "back_office_activated_at":
            payment.get(
                "back_office_activated_at"
            ),
    }


# ============================================================
# BACK OFFICE SYNCHRONIZATION
# ============================================================

def payment_backoffice_state(
    payment: dict[str, Any],
) -> tuple[str, str]:

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(
        status
    ):

        return (
            "Payment Verified",
            "verified",
        )

    if payment_is_reported(
        status
    ):

        return (
            "Payment Reported — Awaiting Verification",
            "payment_reported",
        )

    if payment_is_pending(
        status
    ):

        return (
            "Payment Started — Awaiting Customer Report",
            "payment_pending",
        )

    if status == "rejected":

        return (
            "Payment Rejected",
            "rejected",
        )

    return (
        "Payment Required",
        "unpaid",
    )


def build_work_record(
    payment: dict[str, Any],
) -> dict[str, Any]:

    snapshot = payment_document(
        payment
    )

    pages = normalize_pages(
        snapshot.get(
            "pages"
        )
        or snapshot.get(
            "document_pages"
        )
    )

    text = clean(
        snapshot.get(
            "document_text"
        )
        or snapshot.get(
            "text"
        )
        or ""
    )

    if not pages and text:

        pages = [text]

    label, workflow_status = (
        payment_backoffice_state(
            payment
        )
    )

    version_id = clean(
        payment.get(
            "document_version"
        )
    )

    payment_id = clean(
        payment.get(
            "payment_id"
        )
    )

    job_id = clean(
        payment.get(
            "job_id"
        )
    )

    activated = bool(
        payment.get(
            "back_office_activated"
        )
    )

    verified = payment_is_verified(
        payment.get(
            "payment_status",
            "",
        )
    )

    pending = payment_is_pending(
        payment.get(
            "payment_status",
            "",
        )
    )

    reported = payment_is_reported(
        payment.get(
            "payment_status",
            "",
        )
    )

    filename = (
        clean(
            payment.get(
                "document_filename"
            )
        )
        or f"naija_pocket_{job_id}.docx"
    )

    record: dict[str, Any] = {

        # ----------------------------------------------------
        # Main identifiers
        # ----------------------------------------------------

        "work_id":
            payment_id,

        "workId":
            payment_id,

        "job_id":
            job_id,

        "jobId":
            job_id,

        "customer_id":
            clean(
                payment.get(
                    "customer_id"
                )
            ),

        "customerId":
            clean(
                payment.get(
                    "customer_id"
                )
            ),

        # ----------------------------------------------------
        # Service
        # ----------------------------------------------------

        "service":
            clean(
                payment.get(
                    "service"
                )
            ),

        # ----------------------------------------------------
        # Payment
        # ----------------------------------------------------

        "amount":
            money(
                payment.get(
                    "amount"
                )
            ),

        "currency":
            payment.get(
                "currency"
            )
            or DEFAULT_CURRENCY,

        "payment_id":
            payment_id,

        "paymentId":
            payment_id,

        "payment_status":
            payment.get(
                "payment_status"
            ),

        "paymentStatus":
            payment.get(
                "payment_status"
            ),

        "payment_pending":
            pending
            and not verified,

        "payment_reported":
            reported
            or verified,

        "payment_verified":
            verified,

        "paid":
            verified,

        # ----------------------------------------------------
        # Human-readable Back Office state
        # ----------------------------------------------------

        "status":
            label,

        "workflow_status":
            workflow_status,

        "back_office_status":
            label,

        "backOfficeStatus":
            label,

        # ----------------------------------------------------
        # Review / approval
        # ----------------------------------------------------

        "review_finished":
            True,

        "review_complete":
            True,

        "approved":
            True,

        "current_version":
            1,

        "version_id":
            version_id,

        "document_version":
            version_id,

        # ----------------------------------------------------
        # Document
        # ----------------------------------------------------

        "filename":
            filename,

        "document_filename":
            filename,

        "document_text":
            text,

        "document_pages":
            pages,

        "pages":
            pages,

        "total_pages":
            len(pages),

        # ----------------------------------------------------
        # Activation
        # ----------------------------------------------------

        "activated":
            activated,

        "download_activated":
            activated,

        "back_office_activated":
            activated,

        "back_office_activated_at":
            payment.get(
                "back_office_activated_at"
            ),

        # ----------------------------------------------------
        # Download
        # ----------------------------------------------------

        "download_unlocked":
            verified,

        # ----------------------------------------------------
        # Dates
        # ----------------------------------------------------

        "created_at":
            payment.get(
                "created_at"
            ),

        "updated_at":
            payment.get(
                "updated_at"
            ),

        "reported_at":
            payment.get(
                "reported_at"
            ),

        "verified_at":
            payment.get(
                "verified_at"
            ),
    }

    # ========================================================
    # SAVED VERSION RECORD
    # ========================================================

    version_record = dict(
        record
    )

    version_record.update(
        {
            "id":
                payment_id,

            "work_id":
                payment_id,

            "workId":
                payment_id,

            "version_id":
                version_id,

            "versionId":
                version_id,

            "saved":
                True,

            "approved":
                True,

            "activated":
                activated,

            "download_unlocked":
                verified,

            "content":
                pages,
        }
    )

    # ========================================================
    # Multiple compatibility names.
    #
    # The unchanged Back Office can use whichever historical
    # field name it already expects.
    # ========================================================

    record[
        "work_records"
    ] = [
        version_record
    ]

    record[
        "workRecords"
    ] = [
        version_record
    ]

    record[
        "versions"
    ] = [
        version_record
    ]

    record[
        "works"
    ] = [
        version_record
    ]

    record[
        "saved_work"
    ] = [
        version_record
    ]

    record[
        "savedWork"
    ] = [
        version_record
    ]

    return record


def list_backoffice_jobs() -> list[
    dict[str, Any]
]:

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        build_work_record(
            dict(row)
        )
        for row in rows
    ]


def valid_operator_key(
    supplied: str | None,
) -> bool:

    supplied = clean(
        supplied
    )

    if not supplied:
        return False

    if BACK_OFFICE_KEY:

        return hmac.compare_digest(
            supplied,
            BACK_OFFICE_KEY,
        )

    # Compatibility fallback.
    #
    # A production deployment should define
    # BACK_OFFICE_KEY.
    #
    # This keeps the existing operator-key
    # frontend from being broken if no separate
    # key was configured in the payment service.
    return True


# ============================================================
# BACK OFFICE JOB LIST
# ============================================================

@app.get(
    "/api/back-office/jobs"
)
async def back_office_jobs(
    operator_key: str | None = None,
    admin_key: str | None = None,
):

    supplied = (
        clean(operator_key)
        or clean(admin_key)
    )

    if not valid_operator_key(
        supplied
    ):

        return json_response_error(
            "BACK_OFFICE_UNAUTHORIZED",
            "A valid operator key is required to load Back Office jobs.",
            401,
        )

    jobs = list_backoffice_jobs()

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
        if job.get(
            "activated"
        )
    )

    reported_count = sum(
        1
        for job in jobs
        if job.get(
            "payment_reported"
        )
        and not job.get(
            "payment_verified"
        )
    )

    return {

        "ok":
            True,

        "success":
            True,

        "count":
            len(jobs),

        "jobs":
            jobs,

        "items":
            jobs,

        "work_records":
            jobs,

        "workRecords":
            jobs,

        "approved":
            approved_count,

        "activated":
            activated_count,

        "payment_reported":
            reported_count,

        "awaiting_verification":
            reported_count,

        "total_jobs":
            len(jobs),

        "total_approved":
            approved_count,

        "total_activated":
            activated_count,

        "total_payment_reported":
            reported_count,
    }


# ============================================================
# BACK OFFICE ACTIVATE EXACT SAVED VERSION
# ============================================================

@app.post(
    "/api/back-office/activate-download"
)
async def back_office_activate_download(
    request: Request,
):

    try:

        body = await request.json()

    except Exception:

        body = {}

    if not isinstance(
        body,
        dict,
    ):

        body = {}

    supplied_key = clean(
        body.get(
            "admin_key"
        )
        or body.get(
            "operator_key"
        )
        or body.get(
            "operatorKey"
        )
        or body.get(
            "key"
        )
    )

    if not valid_operator_key(
        supplied_key
    ):

        return json_response_error(
            "BACK_OFFICE_UNAUTHORIZED",
            "A valid operator key is required.",
            401,
        )

    work_id = clean(
        body.get(
            "work_id"
        )
        or body.get(
            "workId"
        )
        or body.get(
            "payment_id"
        )
        or body.get(
            "paymentId"
        )
    )

    job_id = clean(
        body.get(
            "job_id"
        )
        or body.get(
            "jobId"
        )
    )

    version_id = clean(
        body.get(
            "version_id"
        )
        or body.get(
            "versionId"
        )
        or body.get(
            "document_version"
        )
    )

    payment = (
        get_payment(work_id)
        if work_id
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
            "WORK_NOT_FOUND",
            "The saved work could not be found.",
            404,
        )

    stored_version = clean(
        payment.get(
            "document_version"
        )
    )

    if (
        version_id
        and stored_version
        and version_id != stored_version
    ):

        return json_response_error(
            "VERSION_MISMATCH",
            "The requested document version does not match the saved payment version.",
            409,
        )

    timestamp = now_iso()

    with connect_db() as conn:

        conn.execute(
            """
            UPDATE payment_orders

            SET
                back_office_activated = 1,
                back_office_activated_at = ?,
                updated_at = ?

            WHERE payment_id = ?
            """,
            (
                timestamp,
                timestamp,
                payment["payment_id"],
            ),
        )

        conn.commit()

    updated = (
        get_payment(
            payment["payment_id"]
        )
        or payment
    )

    record = build_work_record(
        updated
    )

    return {

        "ok":
            True,

        "success":
            True,

        "activated":
            True,

        "download_activated":
            True,

        "work_id":
            record["work_id"],

        "job_id":
            record["job_id"],

        "version_id":
            record["version_id"],

        "work":
            record,

        "message":
            "The exact saved work version has been activated in Back Office.",
    }


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def root() -> dict[str, Any]:

    return {

        "ok":
            True,

        "success":
            True,

        "service":
            "Naija Pocket Business Center Payment API",

        "version":
            APP_VERSION,

        "old_api_configured":
            old_api_configured(),

        "back_office_sync":
            True,
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {

        "ok":
            True,

        "success":
            True,

        "service":
            "payment_api",

        "version":
            APP_VERSION,

        "old_api_configured":
            old_api_configured(),

        "back_office_sync":
            True,

        "database":
            str(DB_PATH),
    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post(
    "/api/payment/create"
)
async def payment_create(
    request: Request,
    job_id: str | None = None,
    customer_id: str | None = None,
    service: str | None = None,
    amount: float | None = None,
    payment_method: str | None = None,
):

    # --------------------------------------------------------
    # Accept query parameters AND JSON.
    # --------------------------------------------------------

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

    current, error = (
        safe_current_document(
            job_id
        )
    )

    if error:

        return json_response_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The existing document service could not be reached for this job.",
            502,
            detail=error,
        )

    assert current is not None

    if not current_document_is_ready(
        current
    ):

        return json_response_error(
            "DOCUMENT_NOT_READY",
            "The document is not ready for payment yet.",
            409,
            job_id=job_id,
            status=current.get(
                "status"
            ),
        )

    document_amount = money(
        current.get(
            "amount"
        )
    )

    requested_amount = money(
        amount
    )

    final_amount = (
        document_amount
        if document_amount > 0
        else requested_amount
    )

    if final_amount <= 0:

        return json_response_error(
            "AMOUNT_NOT_AVAILABLE",
            "The document service did not provide an amount to pay.",
            409,
            job_id=job_id,
        )

    # --------------------------------------------------------
    # Reuse the current payment for this exact document version.
    # --------------------------------------------------------

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

            public = payment_public(
                latest
            )

            return {

                "ok":
                    True,

                "success":
                    True,

                "payment":
                    public,

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
                        latest_status
                    ),

                "payment_verified":
                    payment_is_verified(
                        latest_status
                    ),

                "payment_pending":
                    payment_is_pending(
                        latest_status
                    ),

                "payment_reported":
                    payment_is_reported(
                        latest_status
                    ),

                "download_unlocked":
                    payment_is_verified(
                        latest_status
                    ),

                "document_version":
                    current.get(
                        "version_id"
                    ),
            }

    # --------------------------------------------------------
    # Create new payment.
    # --------------------------------------------------------

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
        ),

        amount=final_amount,

        currency=DEFAULT_CURRENCY,

        payment_method=method,

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

        document_payload=
            snapshot_payload(
                current
            ),
    )

    return {

        "ok":
            True,

        "success":
            True,

        "message":
            "Payment created. Complete the payment, then report it for Customer Care verification.",

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

        "payment_pending":
            True,

        "payment_reported":
            False,

        "download_unlocked":
            False,

        "document_version":
            current.get(
                "version_id"
            ),
    }


# ============================================================
# PAYMENT REPORT
#
# THIS IS THE IMPORTANT FIX.
#
# When the customer reports payment:
#
#   payment_status = reported
#
# and the Back Office endpoint immediately sees it.
#
# It does NOT unlock download.
# ============================================================

@app.post(
    "/api/payment/report"
)
async def payment_report(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
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

            version_id = (
                version_id
                or body.get(
                    "version_id"
                )
                or body.get(
                    "document_version"
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

    version_id = clean(
        version_id
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

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "No payment record was found for this request.",
            404,
        )

    # --------------------------------------------------------
    # Validate the exact document version.
    # --------------------------------------------------------

    stored_version = clean(
        payment.get(
            "document_version"
        )
    )

    if (
        version_id
        and stored_version
        and version_id != stored_version
    ):

        return json_response_error(
            "PAYMENT_DOCUMENT_CHANGED",
            "The payment report does not match the saved document version.",
            409,
        )

    current, current_error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    if (
        current_error
        or current is None
    ):

        return json_response_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be checked before recording the payment report.",
            502,
            detail=current_error,
        )

    if not document_version_is_current(
        payment,
        current,
    ):

        return json_response_error(
            "PAYMENT_DOCUMENT_CHANGED",
            "The document was changed after this payment was created. A new payment record is required for the current document.",
            409,
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    # --------------------------------------------------------
    # Already verified.
    # --------------------------------------------------------

    if payment_is_verified(
        status
    ):

        return {

            "ok":
                True,

            "success":
                True,

            "message":
                "Payment has already been verified.",

            "payment":
                payment_public(
                    payment
                ),

            "paid":
                True,

            "payment_verified":
                True,

            "payment_pending":
                False,

            "payment_reported":
                True,

            "download_unlocked":
                True,

            "back_office_status":
                "Payment Verified",

            "back_office_visible":
                True,
        }

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT set this to paid.
    #
    # "reported" means:
    #
    # Customer says payment was made.
    #
    # Customer Care still has to verify it.
    # --------------------------------------------------------

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
            clean(note)
            or None
        ),

        reported_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_REPORT_FAILED",
            "The payment report could not be saved.",
            500,
        )

    public = payment_public(
        updated
    )

    return {

        "ok":
            True,

        "success":
            True,

        "message":
            "Payment report received. The job is now visible in Back Office as Payment Reported — Awaiting Verification. Customer Care must verify the payment before download is unlocked.",

        "payment":
            public,

        "job_id":
            updated.get(
                "job_id"
            ),

        "version_id":
            updated.get(
                "document_version"
            ),

        "payment_id":
            updated.get(
                "payment_id"
            ),

        "payment_status":
            "reported",

        "paid":
            False,

        "payment_verified":
            False,

        "payment_pending":
            True,

        "payment_reported":
            True,

        "download_unlocked":
            False,

        "back_office_status":
            "Payment Reported — Awaiting Verification",

        "back_office_visible":
            True,
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
        get_payment(
            clean(payment_id)
        )
        if clean(payment_id)
        else None
    )

    if not payment and clean(
        job_id
    ):

        payment = (
            get_latest_payment_for_job(
                clean(job_id)
            )
        )

    if not payment:

        return {

            "ok":
                True,

            "success":
                True,

            "payment":
                None,

            "payment_status":
                "none",

            "paid":
                False,

            "payment_verified":
                False,

            "payment_pending":
                False,

            "payment_reported":
                False,

            "download_unlocked":
                False,
        }

    stored_version = clean(
        payment.get(
            "document_version"
        )
    )

    if (
        version_id
        and stored_version
        and clean(version_id)
        != stored_version
    ):

        return {

            "ok":
                True,

            "success":
                True,

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

            "payment_pending":
                False,

            "payment_reported":
                False,

            "download_unlocked":
                False,

            "document_check":
                "changed",

            "message":
                "The payment belongs to a different document version.",
        }

    current, error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    if (
        error
        or current is None
    ):

        status = normalize_status(
            payment.get(
                "payment_status"
            )
        )

        verified = payment_is_verified(
            status
        )

        reported = payment_is_reported(
            status
        )

        pending = payment_is_pending(
            status
        )

        return {

            "ok":
                True,

            "success":
                True,

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

            "payment_pending":
                pending
                and not verified,

            "payment_reported":
                reported
                or verified,

            "download_unlocked":
                verified,

            "document_check":
                "unavailable",
        }

    if not document_version_is_current(
        payment,
        current,
    ):

        return {

            "ok":
                True,

            "success":
                True,

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

            "payment_pending":
                False,

            "payment_reported":
                False,

            "download_unlocked":
                False,

            "document_check":
                "changed",

            "message":
                "The document changed after payment creation. Payment cannot unlock the changed document.",
        }

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    verified = payment_is_verified(
        status
    )

    reported = payment_is_reported(
        status
    )

    pending = payment_is_pending(
        status
    )

    backoffice_label, _ = (
        payment_backoffice_state(
            payment
        )
    )

    return {

        "ok":
            True,

        "success":
            True,

        "payment":
            payment_public(
                payment
            ),

        "job_id":
            payment.get(
                "job_id"
            ),

        "version_id":
            payment.get(
                "document_version"
            ),

        "payment_id":
            payment.get(
                "payment_id"
            ),

        "payment_status":
            payment.get(
                "payment_status"
            ),

        "paid":
            verified,

        "payment_verified":
            verified,

        "payment_pending":
            pending
            and not verified,

        "payment_reported":
            reported
            or verified,

        "download_unlocked":
            verified,

        "back_office_status":
            backoffice_label,

        "back_office_visible":
            bool(
                reported
                or verified
            ),

        "document_check":
            "current",
    }


# ============================================================
# PAYMENT COMPATIBILITY ENDPOINT
#
# Older clients may call /api/payment/complete
# when they actually mean "I HAVE MADE PAYMENT".
#
# It therefore remains a REPORT operation.
#
# Customer Care verification is handled separately below.
# ============================================================

@app.post(
    "/api/payment/complete"
)
async def payment_complete_compat(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
    version_id: str | None = None,
    payment_reference: str | None = None,
    note: str | None = None,
):

    return await payment_report(
        request=request,
        payment_id=payment_id,
        job_id=job_id,
        version_id=version_id,
        payment_reference=payment_reference,
        note=note,
    )


# ============================================================
# LEGACY PAYMENT ALIAS
# ============================================================

@app.get(
    "/api/payment"
)
async def payment_state(
    job_id: str,
    version_id: str,
):

    return await payment_status(
        job_id=job_id,
        version_id=version_id,
    )


# ============================================================
# CUSTOMER CARE PAYMENT LIST
# ============================================================

@app.get(
    "/api/customer-care/payments"
)
async def customer_care_payments():

    records = (
        list_pending_payments()
    )

    return {

        "ok":
            True,

        "success":
            True,

        "count":
            len(records),

        "payments":
            [
                payment_public(
                    record
                )
                for record in records
            ],
    }


# ============================================================
# CUSTOMER CARE VERIFICATION
#
# THIS is the operation that changes:
#
#     reported
#
# into:
#
#     verified
#
# Only after this:
#
#     paid = true
#     payment_verified = true
#     download_unlocked = true
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

    # --------------------------------------------------------
    # Customer Care rejects the payment.
    # --------------------------------------------------------

    if not verified:

        updated = update_payment_record(

            payment_id,

            status="rejected",

            admin_note=(
                clean(note)
                or None
            ),
        )

        return {

            "ok":
                True,

            "success":
                True,

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

            "payment_pending":
                False,

            "payment_reported":
                False,

            "download_unlocked":
                False,

            "back_office_status":
                "Payment Rejected",
        }

    # --------------------------------------------------------
    # Verify current document version.
    # --------------------------------------------------------

    current, error = (
        safe_current_document(
            payment["job_id"]
        )
    )

    if (
        error
        or current is None
    ):

        return json_response_error(
            "DOCUMENT_LOOKUP_FAILED",
            "Customer Care verification cannot be completed because the current document could not be checked.",
            502,
            detail=error,
        )

    if not document_version_is_current(
        payment,
        current,
    ):

        return json_response_error(
            "PAYMENT_DOCUMENT_CHANGED",
            "This payment belongs to an older version of the document and cannot unlock the current document.",
            409,
        )

    # --------------------------------------------------------
    # VERIFIED.
    # --------------------------------------------------------

    updated = update_payment_record(

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
            "PAYMENT_VERIFY_FAILED",
            "Payment verification could not be saved.",
            500,
        )

    return {

        "ok":
            True,

        "success":
            True,

        "message":
            "Payment verified. Download is now unlocked for this document version.",

        "payment":
            payment_public(
                updated
            ),

        "job_id":
            updated.get(
                "job_id"
            ),

        "version_id":
            updated.get(
                "document_version"
            ),

        "payment_id":
            updated.get(
                "payment_id"
            ),

        "paid":
            True,

        "payment_verified":
            True,

        "payment_pending":
            False,

        "payment_reported":
            True,

        "download_unlocked":
            True,

        "back_office_status":
            "Payment Verified",
    }


# ============================================================
# DOWNLOAD
#
# Download is still locked unless Customer Care has verified.
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
        get_payment(
            clean(payment_id)
        )
        if clean(payment_id)
        else None
    )

    if not payment and clean(
        job_id
    ):

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

    stored_version = clean(
        payment.get(
            "document_version"
        )
    )

    if (
        version_id
        and stored_version
        and version_id != stored_version
    ):

        return json_response_error(
            "VERSION_MISMATCH",
            "The requested document version does not match the payment version.",
            409,
            download_unlocked=False,
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    # --------------------------------------------------------
    # FINAL SECURITY CHECK.
    #
    # Reported is NOT enough.
    # Pending is NOT enough.
    # Only verified/paid is enough.
    # --------------------------------------------------------

    if not payment_is_verified(
        status
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            "Download remains locked until Customer Care verifies the payment.",
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

    if (
        error
        or current is None
    ):

        return json_response_error(
            "DOCUMENT_LOOKUP_FAILED",
            "The current document could not be retrieved.",
            502,
            detail=error,
        )

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
        current.get(
            "pages"
        )
    )

    if (
        not pages
        and clean(
            current.get(
                "document_text"
            )
        )
    ):

        pages = [
            clean(
                current.get(
                    "document_text"
                )
            )
        ]

    filename = (
        clean(
            current.get(
                "filename"
            )
        )
        or clean(
            payment.get(
                "document_filename"
            )
        )
    )

    if not filename:

        filename = (
            f"naija_pocket_"
            f"{payment['job_id']}.docx"
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

        path=str(
            output_path
        ),

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
                payment[
                    "payment_id"
                ],

            "X-Download-Unlocked":
                "true",
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event(
    "startup"
)
async def startup() -> None:

    init_db()

    print(
        "[PAYMENT API] Startup complete."
    )

    print(
        f"[PAYMENT API] Version: "
        f"{APP_VERSION}"
    )

    print(
        f"[PAYMENT API] Database: "
        f"{DB_PATH}"
    )

    print(
        "[PAYMENT API] Old API configured: "
        f"{bool(OLD_API_BASE_URL)}"
    )

    print(
        "[PAYMENT API] Back Office "
        "synchronization: ENABLED"
    )


# ============================================================
# DIRECT EXECUTION
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
