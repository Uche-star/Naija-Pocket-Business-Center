from __future__ import annotations

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
# PURPOSE:
#   Payment + exact document snapshot + Back Office lifecycle.
#
# IMPORTANT:
#   This file does NOT generate the customer's document.
#   It receives the exact reviewed document snapshot from the
#   payment page, saves that exact snapshot, reports payment,
#   and later unlocks that SAME saved document.
#
# FLOW:
#
#   REVIEW
#      ↓
#   PAYMENT CREATE
#      ↓
#   EXACT DOCUMENT SNAPSHOT SAVED
#      ↓
#   I HAVE MADE PAYMENT
#      ↓
#   PAYMENT STATUS = reported
#      ↓
#   BACK OFFICE:
#   Payment Reported — Awaiting Verification
#      ↓
#   CUSTOMER CARE VERIFIES
#      ↓
#   UNLOCK DOWNLOAD
#      ↓
#   CUSTOMER DOWNLOADS EXISTING SAVED SNAPSHOT
#
# DOES NOT HANDLE:
#   /api/chat
#   /api/upload
#   /api/correct
#   document generation
#   review generation
# ============================================================

APP_VERSION = "payment-download-v3-exact-review-backoffice"

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

# Optional connection to the existing document API.
#
# IMPORTANT:
# Payment creation NO LONGER depends on this when the payment
# page supplies the exact reviewed document snapshot.
OLD_API_BASE_URL = os.getenv(
    "OLD_API_BASE_URL",
    "",
).strip().rstrip("/")

INTERNAL_API_KEY = os.getenv(
    "INTERNAL_API_KEY",
    "",
).strip()

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
        "payment_reported",
        "verification_pending",
        "awaiting_verification",
        "pending_verification",
        "payment_pending",
    }


def payment_is_verified(status: str) -> bool:
    return normalize_status(status) in {
        "verified",
        "approved",
        "paid",
        "payment_verified",
        "payment_confirmed",
        "confirmed",
        "activated",
        "completed",
        "complete",
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
# SQLITE
# ============================================================

def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(DB_PATH),
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
                download_count INTEGER NOT NULL DEFAULT 0,

                document_saved_path TEXT,
                document_saved_at TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payment_orders_job_id
            ON payment_orders(job_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        if "document_saved_path" not in columns:
            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN document_saved_path TEXT
                """
            )

        if "document_saved_at" not in columns:
            conn.execute(
                """
                ALTER TABLE payment_orders
                ADD COLUMN document_saved_at TEXT
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


def get_all_payments() -> list[dict[str, Any]]:

    with connect_db() as conn:

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


def list_pending_payments() -> list[dict[str, Any]]:

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_status IN (
                'reported',
                'verification_pending',
                'awaiting_verification',
                'pending_verification',
                'payment_pending'
            )
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# PAYMENT DATABASE OPERATIONS
# ============================================================

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

    if verified_at is not None:
        fields.append("verified_at = ?")
        values.append(verified_at)

    if reported_at is not None:
        fields.append("reported_at = ?")
        values.append(reported_at)

    fields.append("updated_at = ?")
    values.append(now_iso())

    values.append(payment_id)

    with connect_db() as conn:

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
) -> dict[str, Any] | None:

    timestamp = now_iso()

    with connect_db() as conn:

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

    return get_payment(payment_id)


# ============================================================
# DOCUMENT NORMALIZATION
# ============================================================

def normalize_pages(value: Any) -> list[str]:

    if value is None:
        return []

    if isinstance(value, str):

        text = value.strip()

        return [text] if text else []

    if isinstance(value, (tuple, list)):

        output: list[str] = []

        for item in value:

            if isinstance(item, dict):

                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("body")
                    or item.get("page_text")
                    or ""
                )

                text = str(text).strip()

                if text:
                    output.append(text)

            else:

                text = str(item).strip()

                if text:
                    output.append(text)

        return output

    if isinstance(value, dict):

        text = (
            value.get("text")
            or value.get("content")
            or value.get("body")
            or value.get("page_text")
            or ""
        )

        text = str(text).strip()

        return [text] if text else []

    text = str(value).strip()

    return [text] if text else []


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
        pages
        or text
    )


def snapshot_payload(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "job_id": document.get("job_id"),
        "pages": normalize_pages(
            document.get("pages")
        ),
        "document_text": clean(
            document.get("document_text")
        ),
        "service": clean(
            document.get("service")
        ),
        "filename": clean(
            document.get("filename")
        ),
        "version_id": clean(
            document.get("version_id")
        ),
        "document_version": clean(
            document.get("version_id")
        ),
        "customer_id": clean(
            document.get("customer_id")
        ),
        "amount": money(
            document.get("amount")
        ),
        "currency": DEFAULT_CURRENCY,
    }


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any]:

    raw = payment.get(
        "document_payload"
    ) or "{}"

    try:

        data = json.loads(raw)

        if isinstance(data, dict):
            return data

    except Exception:
        pass

    return {}


def document_version_is_current(
    payment: dict[str, Any],
    current: dict[str, Any],
) -> bool:

    stored = clean(
        payment.get("document_version")
    )

    current_version = clean(
        current.get("version_id")
    )

    return bool(
        stored
        and current_version
        and stored == current_version
    )


# ============================================================
# DOCX
# ============================================================

def xml_escape(value: str) -> str:

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

    lines = str(text).splitlines() or [""]

    runs: list[str] = []

    for index, line in enumerate(lines):

        if index:
            runs.append("<w:br/>")

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

    safe_name = Path(filename).name

    if not safe_name.lower().endswith(".docx"):
        safe_name += ".docx"

    output_path = (
        DOWNLOAD_DIR
        / f"{uuid.uuid4().hex}_{safe_name}"
    )

    body_parts: list[str] = []

    for index, page in enumerate(pages):

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
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>'''

    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
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
# SAVED DOCUMENT
# ============================================================

def saved_document_path(
    payment: dict[str, Any],
) -> Path | None:

    raw = clean(
        payment.get(
            "document_saved_path"
        )
    )

    if not raw:
        return None

    path = Path(raw)

    if path.is_absolute():
        return path

    return BASE_DIR / path


def saved_document_exists(
    payment: dict[str, Any],
) -> bool:

    path = saved_document_path(payment)

    return bool(
        path
        and path.is_file()
        and path.stat().st_size > 0
    )


def save_exact_document_snapshot(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:

    if saved_document_exists(payment):

        return (
            True,
            str(
                saved_document_path(payment)
            ),
        )

    pages = normalize_pages(
        document.get("pages")
    )

    document_text = clean(
        document.get("document_text")
    )

    if not pages and document_text:
        pages = [document_text]

    if not pages:
        return (
            False,
            "The reviewed document contains no downloadable content.",
        )

    filename = (
        clean(document.get("filename"))
        or clean(
            payment.get(
                "document_filename"
            )
        )
        or (
            f"naija_pocket_"
            f"{payment.get('job_id', 'document')}.docx"
        )
    )

    if not filename.lower().endswith(".docx"):
        filename += ".docx"

    payment_id = clean(
        payment.get("payment_id")
    )

    folder = (
        DOWNLOAD_DIR
        / payment_id
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        folder
        / Path(filename).name
    )

    try:

        generated = make_docx(
            pages,
            str(output),
        )

        generated = Path(generated)

        if generated != output:

            if output.exists():
                output.unlink()

            generated.replace(output)

        if (
            not output.is_file()
            or output.stat().st_size <= 0
        ):
            return (
                False,
                "The reviewed document snapshot was not saved correctly.",
            )

        timestamp = now_iso()

        with connect_db() as conn:

            conn.execute(
                """
                UPDATE payment_orders
                SET
                    document_saved_path = ?,
                    document_saved_at = ?,
                    document_filename = ?,
                    updated_at = ?
                WHERE payment_id = ?
                """,
                (
                    str(output),
                    timestamp,
                    Path(filename).name,
                    timestamp,
                    payment_id,
                ),
            )

            conn.commit()

        return (
            True,
            str(output),
        )

    except Exception as exc:

        try:

            if output.exists():
                output.unlink()

        except Exception:
            pass

        return (
            False,
            str(exc),
        )


# ============================================================
# OPTIONAL OLD API BRIDGE
# ============================================================

def old_api_configured() -> bool:
    return bool(OLD_API_BASE_URL)


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

    clean_path = "/" + path.lstrip("/")

    url = (
        OLD_API_BASE_URL
        + clean_path
    )

    if query:

        from urllib.parse import quote

        parts: list[str] = []

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
        "User-Agent": "NaijaPocketPaymentAPI/3.0",
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

            if "json" in content_type.lower():

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
            f"Old API returned HTTP "
            f"{exc.code}: {detail}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach old API: {exc}"
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
            candidates.append(value)

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


def normalize_document_payload(
    payload: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:

    pages = normalize_pages(
        payload.get("pages")
        or payload.get("document_pages")
        or payload.get("review_pages")
        or payload.get("page_texts")
    )

    document_text = clean(
        payload.get("document_text")
        or payload.get("text")
        or payload.get("content")
        or payload.get("document")
    )

    if not pages and document_text:
        pages = [document_text]

    status = clean(
        payload.get("status")
    )

    review_finished = bool(
        payload.get("review_finished")
        or payload.get("review_complete")
        or normalize_status(status)
        == "review_complete"
    )

    billing = payload.get("billing")

    if not isinstance(
        billing,
        dict,
    ):
        billing = {}

    amount = money(
        payload.get("amount")
        or payload.get("total_amount")
        or payload.get("price")
        or billing.get("total")
    )

    if amount <= 0:

        amount = money(
            billing.get("amount")
            or billing.get("total_amount")
            or billing.get("price")
        )

    version = clean(
        payload.get("version_id")
        or payload.get("document_version")
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
        or payload.get("document_filename")
        or f"naija_pocket_{job_id}.docx"
    )

    if not filename.lower().endswith(".docx"):
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


def fetch_current_document(
    job_id: str,
) -> dict[str, Any]:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured."
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
                    "job_id": job_id,
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
        "The existing document service returned no usable document."
    )


def safe_current_document(
    job_id: str,
) -> tuple[
    dict[str, Any] | None,
    str | None,
]:

    try:

        return (
            fetch_current_document(job_id),
            None,
        )

    except Exception as exc:

        return (
            None,
            str(exc),
        )


# ============================================================
# PUBLIC PAYMENT FORMAT
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    verified = payment_is_verified(
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
            payment.get("amount")
        ),

        "currency": payment.get(
            "currency",
            DEFAULT_CURRENCY,
        ),

        "payment_method": payment.get(
            "payment_method"
        ),

        "payment_status": payment.get(
            "payment_status"
        ),

        "status": payment.get(
            "payment_status"
        ),

        "display_status": (
            "Payment Verified — Download Unlocked"
            if verified
            else (
                "Payment Reported — Awaiting Verification"
                if reported
                else "Payment Pending"
            )
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

        "version_id": payment.get(
            "document_version"
        ),

        "versionId": payment.get(
            "document_version"
        ),

        "document_filename": payment.get(
            "document_filename"
        ),

        "document_saved": saved_document_exists(
            payment
        ),

        "document_saved_path": (
            str(
                saved_document_path(payment)
            )
            if saved_document_exists(payment)
            else None
        ),

        "document_saved_at": payment.get(
            "document_saved_at"
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

        "paid": verified,

        "payment_verified": verified,

        "download_unlocked": verified,

        "awaiting_verification": reported,

        "payment_reported": reported,
    }


# ============================================================
# BACK OFFICE
# ============================================================

def payment_display_status(
    payment: dict[str, Any],
) -> str:

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(status):

        return (
            "Payment Verified — "
            "Download Unlocked"
        )

    if payment_is_reported(status):

        return (
            "Payment Reported — "
            "Awaiting Verification"
        )

    if status in {
        "rejected",
        "declined",
        "failed",
    }:

        return "Payment Rejected"

    return "Payment Pending"


def back_office_payment_record(
    payment: dict[str, Any],
) -> dict[str, Any]:

    public = (
        payment_public(payment)
        or {}
    )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    reported = payment_is_reported(
        status
    )

    verified = payment_is_verified(
        status
    )

    public.update(
        {
            "status": payment.get(
                "payment_status"
            ),

            "display_status":
                payment_display_status(
                    payment
                ),

            "payment_reported":
                reported,

            "awaiting_verification":
                reported,

            "verified":
                verified,

            "unlocked":
                verified,

            "saved_document":
                saved_document_exists(
                    payment
                ),

            "download_ready":
                saved_document_exists(
                    payment
                ),
        }
    )

    return public


def get_back_office_jobs_combined(
) -> list[dict[str, Any]]:

    payments = get_all_payments()

    grouped: dict[
        str,
        dict[str, Any],
    ] = {}

    for payment in payments:

        job_id = clean(
            payment.get("job_id")
        )

        if not job_id:
            continue

        status = normalize_status(
            payment.get(
                "payment_status"
            )
        )

        reported = payment_is_reported(
            status
        )

        verified = payment_is_verified(
            status
        )

        item = grouped.get(job_id)

        if item is None:

            item = {
                "job_id": job_id,

                "customer_id":
                    clean(
                        payment.get(
                            "customer_id"
                        )
                    ),

                "service":
                    clean(
                        payment.get(
                            "service"
                        )
                    ),

                "amount":
                    money(
                        payment.get(
                            "amount"
                        )
                    ),

                "currency":
                    payment.get(
                        "currency",
                        DEFAULT_CURRENCY,
                    ),

                "payment_status":
                    payment.get(
                        "payment_status"
                    ),

                "status":
                    payment_display_status(
                        payment
                    ),

                "display_status":
                    payment_display_status(
                        payment
                    ),

                "payment_id":
                    payment.get(
                        "payment_id"
                    ),

                "payment_reference":
                    payment.get(
                        "payment_reference"
                    ),

                "document_version":
                    payment.get(
                        "document_version"
                    ),

                "version_id":
                    payment.get(
                        "document_version"
                    ),

                "document_saved":
                    saved_document_exists(
                        payment
                    ),

                "saved_document":
                    saved_document_exists(
                        payment
                    ),

                "download_ready":
                    saved_document_exists(
                        payment
                    ),

                "payment_reported":
                    reported,

                "awaiting_verification":
                    reported,

                "payment_verified":
                    verified,

                "download_unlocked":
                    verified,

                "created_at":
                    payment.get(
                        "created_at"
                    ),

                "reported_at":
                    payment.get(
                        "reported_at"
                    ),

                "verified_at":
                    payment.get(
                        "verified_at"
                    ),

                "document_saved_at":
                    payment.get(
                        "document_saved_at"
                    ),
            }

            grouped[job_id] = item

        else:

            # Latest payment record for this job
            # remains authoritative.
            item.update(
                {
                    "customer_id":
                        clean(
                            payment.get(
                                "customer_id"
                            )
                        )
                        or item.get(
                            "customer_id"
                        ),

                    "service":
                        clean(
                            payment.get(
                                "service"
                            )
                        )
                        or item.get(
                            "service"
                        ),

                    "amount":
                        money(
                            payment.get(
                                "amount"
                            )
                        )
                        or item.get(
                            "amount"
                        ),

                    "currency":
                        payment.get(
                            "currency",
                            DEFAULT_CURRENCY,
                        ),

                    "payment_status":
                        payment.get(
                            "payment_status"
                        ),

                    "status":
                        payment_display_status(
                            payment
                        ),

                    "display_status":
                        payment_display_status(
                            payment
                        ),

                    "payment_id":
                        payment.get(
                            "payment_id"
                        ),

                    "payment_reference":
                        payment.get(
                            "payment_reference"
                        ),

                    "document_version":
                        payment.get(
                            "document_version"
                        ),

                    "version_id":
                        payment.get(
                            "document_version"
                        ),

                    "document_saved":
                        saved_document_exists(
                            payment
                        ),

                    "saved_document":
                        saved_document_exists(
                            payment
                        ),

                    "download_ready":
                        saved_document_exists(
                            payment
                        ),

                    "payment_reported":
                        reported,

                    "awaiting_verification":
                        reported,

                    "payment_verified":
                        verified,

                    "download_unlocked":
                        verified,

                    "created_at":
                        payment.get(
                            "created_at"
                        ),

                    "reported_at":
                        payment.get(
                            "reported_at"
                        ),

                    "verified_at":
                        payment.get(
                            "verified_at"
                        ),

                    "document_saved_at":
                        payment.get(
                            "document_saved_at"
                        ),
                }
            )

    return list(
        grouped.values()
    )


def back_office_stats() -> dict[str, Any]:

    payments = get_all_payments()

    jobs = (
        get_back_office_jobs_combined()
    )

    reported = sum(
        1
        for payment in payments
        if payment_is_reported(
            payment.get(
                "payment_status",
                "",
            )
        )
    )

    verified = sum(
        1
        for payment in payments
        if payment_is_verified(
            payment.get(
                "payment_status",
                "",
            )
        )
    )

    pending = sum(
        1
        for payment in payments
        if (
            payment_is_pending(
                payment.get(
                    "payment_status",
                    "",
                )
            )
            and not payment_is_reported(
                payment.get(
                    "payment_status",
                    "",
                )
            )
        )
    )

    return {
        "ok": True,

        "total":
            len(payments),

        "total_payments":
            len(payments),

        "total_jobs":
            len(jobs),

        "pending":
            pending,

        "reported":
            reported,

        "payment_reported":
            reported,

        "awaiting_verification":
            reported,

        "verified":
            verified,

        "completed":
            verified,

        "unlocked":
            verified,

        "revenue":
            round(
                sum(
                    money(
                        payment.get(
                            "amount"
                        )
                    )
                    for payment in payments
                    if payment_is_verified(
                        payment.get(
                            "payment_status",
                            "",
                        )
                    )
                ),
                2,
            ),
    }


# ============================================================
# OPTIONAL SYNCHRONIZATION
# ============================================================

def synchronize_report_to_old_api(
    payment: dict[str, Any],
) -> dict[str, Any]:

    if not old_api_configured():

        return {
            "ok": False,
            "configured": False,
            "message":
                "OLD_API_BASE_URL is not configured",
        }

    payload = {
        "payment_id":
            payment.get(
                "payment_id"
            ),

        "job_id":
            payment.get(
                "job_id"
            ),

        "customer_id":
            payment.get(
                "customer_id"
            ),

        "service":
            payment.get(
                "service"
            ),

        "amount":
            money(
                payment.get(
                    "amount"
                )
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

        "payment_reference":
            payment.get(
                "payment_reference"
            ),

        "payment_status":
            payment.get(
                "payment_status"
            ),

        "status":
            payment_display_status(
                payment
            ),

        "reported_at":
            payment.get(
                "reported_at"
            ),

        "document_version":
            payment.get(
                "document_version"
            ),

        "version_id":
            payment.get(
                "document_version"
            ),

        "document_saved":
            saved_document_exists(
                payment
            ),
    }

    candidates = [
        "/api/payment/report-sync",
        "/api/back-office/payment-sync",
        "/api/payment/sync",
    ]

    errors: list[str] = []

    for path in candidates:

        try:

            response = old_api_request(
                "POST",
                path,
                body=payload,
            )

            return {
                "ok": True,
                "configured": True,
                "path": path,
                "response": response,
            }

        except Exception as exc:

            errors.append(
                str(exc)
            )

    return {
        "ok": False,
        "configured": True,
        "errors": errors,
    }


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def root() -> dict[str, Any]:

    return {
        "ok": True,
        "service":
            "Naija Pocket Business Center Payment API",
        "version":
            APP_VERSION,
        "old_api_configured":
            old_api_configured(),
        "payment_flow":
            "exact-review-snapshot",
        "back_office":
            True,
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {
        "ok": True,
        "service":
            "payment_api",
        "version":
            APP_VERSION,
        "old_api_configured":
            old_api_configured(),
        "database":
            str(DB_PATH),
        "download_directory":
            str(DOWNLOAD_DIR),
    }


# ============================================================
# PAYMENT CREATE
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

        if not isinstance(
            body,
            dict,
        ):
            body = {}

    except Exception:

        body = {}

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

    method = clean(
        payment_method
        or body.get("payment_method")
    ) or DEFAULT_PAYMENT_METHOD

    if amount is None:
        amount = body.get("amount")

    if not job_id:

        return json_response_error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
            400,
        )

    # --------------------------------------------------------
    # FIRST PRIORITY:
    # Use the exact reviewed snapshot supplied by the
    # payment page.
    #
    # This is the important fix.
    #
    # The payment page already has:
    #   pages
    #   document_pages
    #   document_text
    #   version_id
    #   document_version
    #
    # Therefore payment creation must NOT fail merely because
    # the separate document API cannot be contacted again.
    # --------------------------------------------------------

    requested_version = clean(
        body.get("version_id")
        or body.get("document_version")
        or body.get("versionId")
    )

    supplied_pages = body.get(
        "pages",
        body.get(
            "document_pages"
        ),
    )

    supplied_text = clean(
        body.get("document_text")
        or body.get("text")
        or body.get("content")
    )

    supplied_filename = clean(
        body.get("filename")
        or body.get("document_filename")
    )

    supplied_service = clean(
        body.get("service")
    )

    supplied_customer = clean(
        body.get("customer_id")
    )

    supplied_amount = money(
        body.get("amount")
    )

    has_exact_snapshot = bool(
        requested_version
        and (
            supplied_pages is not None
            or supplied_text
        )
    )

    # --------------------------------------------------------
    # EXACT SNAPSHOT PATH
    # --------------------------------------------------------

    if has_exact_snapshot:

        exact_pages = normalize_pages(
            supplied_pages
        )

        if (
            not exact_pages
            and supplied_text
        ):
            exact_pages = [
                supplied_text
            ]

        if not exact_pages:

            return json_response_error(
                "DOCUMENT_EMPTY",
                "The reviewed document contains no downloadable content.",
                409,
                job_id=job_id,
            )

        # Optional validation only.
        #
        # If the old API is available, check the version.
        # If it is unavailable, do NOT reject the exact Review
        # snapshot supplied by the payment page.
        current = None

        if OLD_API_BASE_URL:

            current, current_error = (
                safe_current_document(
                    job_id
                )
            )

            if current is not None:

                backend_version = clean(
                    current.get(
                        "version_id"
                    )
                )

                if (
                    backend_version
                    and requested_version
                    and backend_version
                    != requested_version
                ):

                    return json_response_error(
                        "PAYMENT_DOCUMENT_CHANGED",
                        "The reviewed document version no longer matches the current job version. Please return to Review and try again.",
                        409,
                        job_id=job_id,
                        version_id=requested_version,
                        current_version=backend_version,
                    )

        filename = (
            supplied_filename
            or clean(
                body.get(
                    "document_filename"
                )
            )
            or (
                f"naija_pocket_"
                f"{job_id}.docx"
            )
        )

        if not filename.lower().endswith(
            ".docx"
        ):
            filename += ".docx"

        final_service = (
            service
            or supplied_service
            or "Business Center Service"
        )

        final_customer = (
            customer_id
            or supplied_customer
        )

        final_amount = (
            supplied_amount
            or money(amount)
        )

        # If amount wasn't in the snapshot,
        # optionally try the existing API.
        if final_amount <= 0 and current:

            final_amount = money(
                current.get(
                    "amount"
                )
            )

        if final_amount <= 0:

            return json_response_error(
                "AMOUNT_NOT_AVAILABLE",
                "The amount to pay is not available.",
                409,
                job_id=job_id,
            )

        document = {
            "job_id": job_id,

            "pages": exact_pages,

            "document_text":
                supplied_text,

            "version_id":
                requested_version,

            "filename":
                filename,

            "service":
                final_service,

            "customer_id":
                final_customer,

            "amount":
                final_amount,

            "review_finished":
                True,

            "status":
                "review_complete",
        }

        version = requested_version

    # --------------------------------------------------------
    # FALLBACK PATH
    #
    # Used only when the payment page did NOT supply an exact
    # snapshot.
    # --------------------------------------------------------

    else:

        current, error = (
            safe_current_document(
                job_id
            )
        )

        if error or current is None:

            return json_response_error(
                "DOCUMENT_LOOKUP_FAILED",
                "The existing document service could not be reached for this job.",
                502,
                detail=error,
                job_id=job_id,
            )

        if not document_has_content(
            current
        ):

            return json_response_error(
                "DOCUMENT_EMPTY",
                "The reviewed document contains no downloadable content.",
                409,
                job_id=job_id,
            )

        ready = bool(
            current.get(
                "review_finished"
            )
            or normalize_status(
                current.get(
                    "status"
                )
            )
            == "review_complete"
        )

        if not ready:

            return json_response_error(
                "DOCUMENT_NOT_READY",
                "The document is not ready for payment yet.",
                409,
                job_id=job_id,
                status=current.get(
                    "status"
                ),
            )

        document = current

        version = clean(
            current.get(
                "version_id"
            )
        )

        if not version:

            return json_response_error(
                "VERSION_ID_REQUIRED",
                "A document version is required before payment can be created.",
                409,
                job_id=job_id,
            )

        final_amount = (
            money(
                current.get(
                    "amount"
                )
            )
            or money(amount)
        )

        if final_amount <= 0:

            return json_response_error(
                "AMOUNT_NOT_AVAILABLE",
                "The document service did not provide an amount to pay.",
                409,
                job_id=job_id,
            )

        filename = clean(
            current.get(
                "filename"
            )
        ) or (
            f"naija_pocket_"
            f"{job_id}.docx"
        )

        if not filename.lower().endswith(
            ".docx"
        ):
            filename += ".docx"

        document["filename"] = filename

    # --------------------------------------------------------
    # REUSE EXISTING PAYMENT FOR SAME DOCUMENT VERSION
    # --------------------------------------------------------

    latest = get_latest_payment_for_job(
        job_id
    )

    if (
        latest
        and clean(
            latest.get(
                "document_version"
            )
        )
        == version
        and normalize_status(
            latest.get(
                "payment_status"
            )
        )
        in {
            "pending",
            "reported",
            "verification_pending",
            "awaiting_verification",
            "pending_verification",
            "verified",
            "completed",
            "complete",
            "paid",
        }
    ):

        if not saved_document_exists(
            latest
        ):

            ok, error = (
                save_exact_document_snapshot(
                    latest,
                    document,
                )
            )

            if not ok:

                return json_response_error(
                    "DOCUMENT_SNAPSHOT_FAILED",
                    "The exact reviewed document could not be saved before payment.",
                    500,
                    detail=error,
                    job_id=job_id,
                    version_id=version,
                )

            latest = (
                get_payment(
                    latest["payment_id"]
                )
                or latest
            )

        verified = payment_is_verified(
            latest.get(
                "payment_status",
                "",
            )
        )

        return {
            "ok": True,

            "message":
                "Existing payment and exact reviewed document snapshot reused.",

            "payment":
                payment_public(
                    latest
                ),

            "payment_id":
                latest[
                    "payment_id"
                ],

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
                verified,

            "payment_verified":
                verified,

            "download_unlocked":
                verified,

            "document_version":
                version,

            "version_id":
                version,

            "document_saved":
                True,
        }

    # --------------------------------------------------------
    # CREATE NEW PAYMENT
    # --------------------------------------------------------

    payment_id = (
        f"NPB-"
        f"{uuid.uuid4().hex[:12].upper()}"
    )

    payload = snapshot_payload(
        document
    )

    payload.update(
        {
            "version_id":
                version,

            "document_version":
                version,

            "customer_id":
                customer_id
                or clean(
                    document.get(
                        "customer_id"
                    )
                ),

            "service":
                service
                or clean(
                    document.get(
                        "service"
                    )
                ),

            "amount":
                final_amount,

            "currency":
                DEFAULT_CURRENCY,
        }
    )

    record = create_payment_record(
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

        document_version=version,

        document_filename=(
            clean(
                document.get(
                    "filename"
                )
            )
            or (
                f"naija_pocket_"
                f"{job_id}.docx"
            )
        ),

        document_payload=payload,
    )

    # --------------------------------------------------------
    # SAVE THE EXACT DOCUMENT BEFORE PAYMENT IS REPORTED.
    # --------------------------------------------------------

    ok, error = (
        save_exact_document_snapshot(
            record,
            document,
        )
    )

    if not ok:

        with connect_db() as conn:

            conn.execute(
                """
                DELETE FROM payment_orders
                WHERE payment_id = ?
                """,
                (payment_id,),
            )

            conn.commit()

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            "The exact reviewed document could not be saved. Payment has not been started.",
            500,
            detail=error,
            job_id=job_id,
            version_id=version,
        )

    record = (
        get_payment(payment_id)
        or record
    )

    return {
        "ok": True,

        "message":
            "Payment created and the exact reviewed document was saved successfully.",

        "payment":
            payment_public(record),

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

        "download_unlocked":
            False,

        "document_version":
            version,

        "version_id":
            version,

        "document_saved":
            True,
    }


# ============================================================
# CUSTOMER: I HAVE MADE PAYMENT
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
            "No payment record was found for this request.",
            404,
        )

    # --------------------------------------------------------
    # IMPORTANT:
    # The exact snapshot was already saved during payment
    # creation. We do NOT regenerate it here.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            "The exact reviewed document was not saved. Payment cannot be reported until the snapshot exists.",
            409,
            payment_id=payment.get(
                "payment_id"
            ),
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    # Already verified.
    if payment_is_verified(
        status
    ):

        return {
            "ok": True,

            "message":
                "Payment has already been verified.",

            "payment":
                payment_public(
                    payment
                ),

            "back_office":
                back_office_payment_record(
                    payment
                ),

            "paid":
                True,

            "payment_verified":
                True,

            "download_unlocked":
                True,
        }

    # --------------------------------------------------------
    # THE SWITCH:
    #
    # "I HAVE MADE PAYMENT"
    # changes the database status to "reported".
    #
    # Back Office reads the SAME payment database.
    # Therefore the report immediately exists in Back Office.
    # --------------------------------------------------------

    updated = update_payment_record(
        payment[
            "payment_id"
        ],

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

    # Optional external synchronization.
    # Failure here NEVER removes the local report.
    sync = (
        synchronize_report_to_old_api(
            updated
        )
    )

    return {
        "ok": True,

        "message":
            "Payment Reported — Awaiting Verification",

        "payment":
            payment_public(
                updated
            ),

        "back_office":
            back_office_payment_record(
                updated
            ),

        "sync":
            sync,

        "paid":
            False,

        "payment_verified":
            False,

        "download_unlocked":
            False,

        "payment_reported":
            True,

        "awaiting_verification":
            True,
    }


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
            "payment": None,
            "payment_status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    verified = payment_is_verified(
        payment.get(
            "payment_status",
            "",
        )
    )

    return {
        "ok": True,

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

        "download_unlocked":
            verified,

        "document_check":
            "saved_snapshot",

        "document_saved":
            saved_document_exists(
                payment
            ),
    }


# ============================================================
# PAYMENT COMPLETE COMPATIBILITY
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete_compat(
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
# CUSTOMER CARE PAYMENT LIST
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():

    records = (
        list_pending_payments()
    )

    return {
        "ok": True,

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
# CUSTOMER CARE VERIFY
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
            "ok": True,

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

            "download_unlocked":
                False,
        }

    # --------------------------------------------------------
    # NEVER GENERATE THE DOCUMENT HERE.
    # The exact snapshot must already exist.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            "Customer Care cannot unlock this payment because the exact document snapshot is missing.",
            409,
            payment_id=payment_id,
        )

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
            "PAYMENT_UPDATE_FAILED",
            "Payment verification could not be saved.",
            500,
        )

    return {
        "ok": True,

        "message":
            "Payment verified. Download is now unlocked for this document version.",

        "payment":
            payment_public(
                updated
            ),

        "back_office":
            back_office_payment_record(
                updated
            ),

        "paid":
            True,

        "payment_verified":
            True,

        "download_unlocked":
            True,
    }


# ============================================================
# BACK OFFICE PAYMENTS
# ============================================================

@app.get("/api/back-office/payments")
@app.get("/api/customer-care/payments/all")
async def back_office_payments():

    records = get_all_payments()

    return {
        "ok": True,

        "count":
            len(records),

        "total":
            len(records),

        "payments":
            [
                back_office_payment_record(
                    payment
                )
                for payment in records
            ],

        "stats":
            back_office_stats(),
    }


# ============================================================
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/back-office/jobs")
@app.get("/api/customer-care/jobs")
async def back_office_jobs():

    jobs = (
        get_back_office_jobs_combined()
    )

    return {
        "ok": True,

        "count":
            len(jobs),

        "total":
            len(jobs),

        "jobs":
            jobs,

        "records":
            jobs,

        "stats":
            back_office_stats(),
    }


# ============================================================
# BACK OFFICE STATS
# ============================================================

@app.get("/api/back-office/stats")
@app.get("/api/customer-care/stats")
async def back_office_statistics():

    return back_office_stats()


# ============================================================
# BACK OFFICE SINGLE PAYMENT
# ============================================================

@app.get(
    "/api/back-office/payment/{payment_id}"
)
async def back_office_payment(
    payment_id: str,
):

    payment = get_payment(
        clean(payment_id)
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    return {
        "ok": True,

        "payment":
            back_office_payment_record(
                payment
            ),
    }


# ============================================================
# BACK OFFICE:
# UNLOCK DOWNLOAD
#
# This endpoint is intentionally simple.
#
# It DOES NOT generate a document.
# It DOES NOT save another document.
#
# It only changes the existing payment record from reported
# to verified, provided the exact saved document exists.
# ============================================================

@app.post(
    "/api/back-office/activate-download"
)
async def back_office_activate_download(
    request: Request,
):

    try:

        body = await request.json()

        if not isinstance(
            body,
            dict,
        ):
            body = {}

    except Exception:

        body = {}

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    job_id = clean(
        body.get(
            "job_id"
        )
    )

    work_id = clean(
        body.get(
            "work_id"
        )
    )

    document_id = clean(
        body.get(
            "document_id"
        )
    )

    action = normalize_status(
        body.get(
            "action"
        )
    )

    # --------------------------------------------------------
    # Find payment.
    # --------------------------------------------------------

    payment = None

    if payment_id:

        payment = get_payment(
            payment_id
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
            "No payment record was found for this Job ID.",
            404,
            job_id=job_id,
            payment_id=payment_id,
        )

    # --------------------------------------------------------
    # The saved exact document is mandatory.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            "The exact saved document is missing. Download cannot be unlocked.",
            409,
            payment_id=payment.get(
                "payment_id"
            ),
            job_id=payment.get(
                "job_id"
            ),
        )

    # --------------------------------------------------------
    # Unlock means VERIFY the existing payment.
    # --------------------------------------------------------

    current_status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(
        current_status
    ):

        updated = payment

    else:

        updated = update_payment_record(
            payment[
                "payment_id"
            ],

            status="verified",

            admin_note=(
                "Download unlocked by "
                "Back Office."
            ),

            verified_at=now_iso(),
        )

    if not updated:

        return json_response_error(
            "UNLOCK_FAILED",
            "The download could not be unlocked.",
            500,
        )

    return {
        "ok": True,

        "message":
            "Download unlocked successfully.",

        "action":
            action
            or "unlock_download",

        "payment":
            payment_public(
                updated
            ),

        "back_office":
            back_office_payment_record(
                updated
            ),

        "payment_id":
            updated.get(
                "payment_id"
            ),

        "job_id":
            updated.get(
                "job_id"
            ),

        "work_id":
            work_id,

        "document_id":
            document_id,

        "document_saved":
            True,

        "download_unlocked":
            True,

        "payment_verified":
            True,

        "paid":
            True,
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

    if not payment_is_verified(
        payment.get(
            "payment_status",
            "",
        )
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            "Download remains locked until Customer Care verifies the payment.",
            403,
            payment_status=
                payment.get(
                    "payment_status"
                ),
            payment_id=
                payment.get(
                    "payment_id"
                ),
            download_unlocked=False,
        )

    # --------------------------------------------------------
    # Serve the EXISTING snapshot.
    # Never regenerate it.
    # --------------------------------------------------------

    output = saved_document_path(
        payment
    )

    if (
        not output
        or not output.is_file()
        or output.stat().st_size <= 0
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            "The paid document snapshot is missing. Download remains locked for safety.",
            409,
            payment_id=
                payment.get(
                    "payment_id"
                ),
            version_id=
                payment.get(
                    "document_version"
                ),
            download_unlocked=False,
        )

    increment_download(
        payment[
            "payment_id"
        ]
    )

    filename = (
        clean(
            payment.get(
                "document_filename"
            )
        )
        or output.name
    )

    return FileResponse(
        path=str(output),

        media_type=(
            "application/vnd.openxmlformats-"
            "officedocument.wordprocessingml.document"
        ),

        filename=Path(
            filename
        ).name,

        headers={
            "X-Payment-ID":
                payment[
                    "payment_id"
                ],

            "X-Document-Version":
                clean(
                    payment.get(
                        "document_version"
                    )
                ),

            "X-Download-Unlocked":
                "true",

            "X-Document-Snapshot":
                "true",
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
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
        f"[PAYMENT API] Downloads: "
        f"{DOWNLOAD_DIR}"
    )

    print(
        "[PAYMENT API] Old API configured: "
        f"{bool(OLD_API_BASE_URL)}"
    )

    print(
        "[PAYMENT API] Exact Review snapshot "
        "payment flow: ENABLED"
    )

    print(
        "[PAYMENT API] Back Office unlock "
        "endpoint: ENABLED"
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
