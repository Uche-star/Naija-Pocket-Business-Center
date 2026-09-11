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
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


# ============================================================
# Naija Pocket Business Center
# payment_api.py
#
# PAYMENT + EXACT DOCUMENT SNAPSHOT + BACK OFFICE
#
# CUSTOMER FLOW
#
# Review
#   ↓
# Payment HTML
#   ↓
# /api/payment/create
#   ↓
# Exact reviewed document snapshot is saved
#   ↓
# Customer reports payment
#   ↓
# /api/payment/report
#   ↓
# Back Office sees:
# Payment Reported — Awaiting Verification
#   ↓
# Customer Care verifies payment
#   ↓
# UNLOCK DOWNLOAD
#   ↓
# Customer downloads the SAME saved snapshot
#
# IMPORTANT
#
# The exact document supplied by payment.html is authoritative
# when pages/document_text/version_id are present.
#
# The old document API is NOT required for that exact-snapshot
# payment flow.
#
# Back Office never regenerates the document.
# Customer download never regenerates the document.
# ============================================================


APP_VERSION = (
    "payment-download-v3-exact-snapshot-backoffice-fixed"
)

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# DATABASE
# ============================================================

DB_PATH = Path(
    os.getenv(
        "PAYMENT_DB_PATH",
        str(BASE_DIR / "payment_gateway.db"),
    )
)


# ============================================================
# DOWNLOAD STORAGE
# ============================================================

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
# OPTIONAL OLD API BRIDGE
#
# ONLY USED AS A FALLBACK WHEN PAYMENT HTML DID NOT SEND
# THE EXACT REVIEWED DOCUMENT SNAPSHOT.
# ============================================================

OLD_API_BASE_URL = (
    os.getenv(
        "OLD_API_BASE_URL",
        "",
    )
    .strip()
    .rstrip("/")
)

INTERNAL_API_KEY = (
    os.getenv(
        "INTERNAL_API_KEY",
        "",
    )
    .strip()
)


# ============================================================
# BACK OFFICE
# ============================================================

BACK_OFFICE_ADMIN_KEY = (
    os.getenv(
        "BACK_OFFICE_ADMIN_KEY",
        "",
    )
    .strip()
)


# ============================================================
# DEFAULTS
# ============================================================

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


def normalize_status(status: Any) -> str:
    return (
        clean(status)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


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


def payment_is_reported(
    status: Any,
) -> bool:

    return normalize_status(status) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def payment_is_verified(
    status: Any,
) -> bool:

    return normalize_status(status) in {
        "verified",
        "completed",
        "complete",
        "paid",
    }


def payment_is_pending(
    status: Any,
) -> bool:

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
        timeout=30,
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


# ============================================================
# PAYMENT DATABASE READERS
# ============================================================

def get_payment(
    payment_id: str,
) -> dict[str, Any] | None:

    if not clean(payment_id):
        return None

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE payment_id = ?
            """,
            (
                clean(payment_id),
            ),
        ).fetchone()

    return row_to_dict(row)


def get_latest_payment_for_job(
    job_id: str,
) -> dict[str, Any] | None:

    if not clean(job_id):
        return None

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                clean(job_id),
            ),
        ).fetchone()

    return row_to_dict(row)


def get_payments_for_job(
    job_id: str,
) -> list[dict[str, Any]]:

    if not clean(job_id):
        return []

    with connect_db() as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (
                clean(job_id),
            ),
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


def list_all_payments() -> list[dict[str, Any]]:

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


# ============================================================
# PAYMENT DATABASE WRITE
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
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                'pending',
                ?,
                ?,
                ?,
                ?,
                ?
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

    payment_id = clean(payment_id)

    current = get_payment(payment_id)

    if not current:
        return None

    fields: list[str] = []
    values: list[Any] = []

    if status is not None:

        fields.append(
            "payment_status = ?"
        )

        values.append(
            status
        )

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
                clean(payment_id),
            ),
        )

        conn.commit()

    return get_payment(payment_id)


def delete_payment_record(
    payment_id: str,
) -> None:

    with connect_db() as conn:

        conn.execute(
            """
            DELETE FROM payment_orders
            WHERE payment_id = ?
            """,
            (
                clean(payment_id),
            ),
        )

        conn.commit()


# ============================================================
# DOCUMENT NORMALIZATION
# ============================================================

def normalize_pages(
    value: Any,
) -> list[str]:

    if value is None:
        return []

    if isinstance(value, str):

        text = value.strip()

        return [text] if text else []

    if isinstance(value, (list, tuple)):

        result: list[str] = []

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
                    result.append(text)

            else:

                text = str(item).strip()

                if text:
                    result.append(text)

        return result

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

    if pages:
        return True

    return bool(
        clean(
            document.get(
                "document_text"
            )
        )
    )


def normalize_document(
    document: dict[str, Any],
    job_id: str,
    *,
    version_id: str = "",
    filename: str = "",
    service: str = "",
    customer_id: str = "",
    amount: float = 0.0,
) -> dict[str, Any]:

    pages = normalize_pages(
        document.get("pages")
        or document.get("document_pages")
        or document.get("review_pages")
        or document.get("page_texts")
    )

    text = clean(
        document.get("document_text")
        or document.get("text")
        or document.get("content")
    )

    if not pages and text:
        pages = [text]

    final_version = clean(
        version_id
        or document.get("version_id")
        or document.get("document_version")
        or document.get("version")
    )

    final_filename = clean(
        filename
        or document.get("filename")
        or document.get("document_filename")
        or f"naija_pocket_{job_id}.docx"
    )

    if not final_filename.lower().endswith(
        ".docx"
    ):
        final_filename += ".docx"

    final_service = clean(
        service
        or document.get("service")
    )

    final_customer = clean(
        customer_id
        or document.get("customer_id")
    )

    final_amount = (
        money(amount)
        or money(document.get("amount"))
        or money(document.get("total_amount"))
        or money(document.get("price"))
    )

    return {
        "job_id": clean(job_id),
        "pages": pages,
        "document_text": text,
        "version_id": final_version,
        "document_version": final_version,
        "filename": final_filename,
        "document_filename": final_filename,
        "service": final_service,
        "customer_id": final_customer,
        "amount": final_amount,
        "review_finished": True,
        "status": "review_complete",
    }


# ============================================================
# SNAPSHOT PAYLOAD
# ============================================================

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
        "version_id": clean(
            document.get("version_id")
        ),
        "document_version": clean(
            document.get("document_version")
        ),
        "service": clean(
            document.get("service")
        ),
        "filename": clean(
            document.get("filename")
        ),
        "document_filename": clean(
            document.get("document_filename")
        ),
        "customer_id": clean(
            document.get("customer_id")
        ),
        "amount": money(
            document.get("amount")
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

        value = json.loads(raw)

        if isinstance(value, dict):
            return value

    except Exception:
        pass

    return {}


# ============================================================
# SAVED DOCUMENT PATH
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

    path = saved_document_path(
        payment
    )

    if not path:
        return False

    try:

        return (
            path.is_file()
            and path.stat().st_size > 0
        )

    except Exception:

        return False


def saved_document_size(
    payment: dict[str, Any],
) -> int:

    path = saved_document_path(
        payment
    )

    if not path:
        return 0

    try:
        return path.stat().st_size
    except Exception:
        return 0


# ============================================================
# DOCX CREATION
# ============================================================

def xml_escape(
    value: str,
) -> str:

    return escape(
        str(value),
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

    for index, line in enumerate(lines):

        if index:

            runs.append(
                "<w:br/>"
            )

        runs.append(
            "<w:r>"
            "<w:rPr>"
            '<w:sz w:val="24"/>'
            "</w:rPr>"
            '<w:t xml:space="preserve">'
            f"{xml_escape(line)}"
            "</w:t>"
            "</w:r>"
        )

    return (
        "<w:p>"
        + "".join(runs)
        + "</w:p>"
    )


def make_docx(
    pages: list[str],
    filename: str,
    output_path: Path,
) -> Path:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    body_parts: list[str] = []

    for index, page in enumerate(
        pages
    ):

        if index:

            body_parts.append(
                "<w:p>"
                "<w:r>"
                '<w:br w:type="page"/>'
                "</w:r>"
                "</w:p>"
            )

        body_parts.append(
            paragraph_xml(page)
        )

    document_xml = (
        '<?xml version="1.0" '
        'encoding="UTF-8" '
        'standalone="yes"?>'
        '<w:document '
        'xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body_parts)
        + """
        <w:sectPr>
          <w:pgSz
            w:w="11906"
            w:h="16838"/>
          <w:pgMar
            w:top="1134"
            w:right="1134"
            w:bottom="1134"
            w:left="1134"/>
        </w:sectPr>
        </w:body>
        </w:document>
        """
    )

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>
        <w:sz w:val="24"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
</w:styles>
"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml"
    ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""

    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>
"""

    temporary = output_path.with_suffix(
        ".tmp.docx"
    )

    try:

        with zipfile.ZipFile(
            temporary,
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

        if output_path.exists():
            output_path.unlink()

        temporary.replace(
            output_path
        )

        return output_path

    except Exception:

        try:
            if temporary.exists():
                temporary.unlink()
        except Exception:
            pass

        raise


# ============================================================
# SAVE EXACT DOCUMENT SNAPSHOT
# ============================================================

def save_exact_document_snapshot(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:

    # --------------------------------------------------------
    # NEVER replace an existing snapshot.
    # --------------------------------------------------------

    if saved_document_exists(
        payment
    ):

        existing = saved_document_path(
            payment
        )

        return (
            True,
            str(existing),
        )

    pages = normalize_pages(
        document.get("pages")
    )

    if not pages:

        text = clean(
            document.get(
                "document_text"
            )
        )

        if text:
            pages = [text]

    if not pages:

        return (
            False,
            "The reviewed document contains no downloadable content.",
        )

    filename = (
        clean(
            document.get("filename")
        )
        or clean(
            document.get(
                "document_filename"
            )
        )
        or clean(
            payment.get(
                "document_filename"
            )
        )
        or (
            "naija_pocket_"
            + clean(
                payment.get(
                    "job_id"
                )
            )
            + ".docx"
        )
    )

    filename = Path(
        filename
    ).name

    if not filename.lower().endswith(
        ".docx"
    ):
        filename += ".docx"

    payment_id = clean(
        payment.get(
            "payment_id"
        )
    )

    if not payment_id:

        return (
            False,
            "Payment ID is missing.",
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
        / filename
    )

    try:

        make_docx(
            pages,
            filename,
            output,
        )

        if (
            not output.is_file()
            or output.stat().st_size <= 0
        ):

            return (
                False,
                "The exact reviewed document snapshot was not saved correctly.",
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
                    filename,
                    timestamp,
                    payment_id,
                ),
            )

            conn.commit()

        refreshed = get_payment(
            payment_id
        )

        if not refreshed:
            return (
                False,
                "Payment record disappeared after document save.",
            )

        if not saved_document_exists(
            refreshed
        ):
            return (
                False,
                "Document file was created but could not be confirmed.",
            )

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
# OLD API BRIDGE
#
# FALLBACK ONLY.
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
            "OLD_API_BASE_URL is not configured."
        )

    url = (
        OLD_API_BASE_URL
        + "/"
        + path.lstrip("/")
    )

    if query:

        parts: list[str] = []

        for key, value in query.items():

            if value is None:
                continue

            if value == "":
                continue

            parts.append(
                f"{quote(str(key))}="
                f"{quote(str(value))}"
            )

        if parts:
            url += "?" + "&".join(parts)

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

    data: bytes | None = None

    if body is not None:

        data = json.dumps(
            body,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        )

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
                    raw.decode(
                        "utf-8"
                    )
                )

            try:

                return json.loads(
                    raw.decode(
                        "utf-8"
                    )
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
                raw.decode(
                    "utf-8"
                )
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

    if not isinstance(
        response,
        dict,
    ):
        return None

    candidates = [
        response
    ]

    for key in (
        "data",
        "job",
        "result",
        "document",
    ):

        value = response.get(
            key
        )

        if isinstance(
            value,
            dict,
        ):
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


def fetch_current_document(
    job_id: str,
) -> dict[str, Any]:

    if not OLD_API_BASE_URL:

        raise RuntimeError(
            "OLD_API_BASE_URL is not configured."
        )

    last_error: Exception | None = None

    for path in (
        "/api/review/pages",
        "/api/review",
    ):

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

                pages = normalize_pages(
                    payload.get(
                        "pages"
                    )
                    or payload.get(
                        "document_pages"
                    )
                    or payload.get(
                        "review_pages"
                    )
                )

                text = clean(
                    payload.get(
                        "document_text"
                    )
                    or payload.get(
                        "text"
                    )
                    or payload.get(
                        "content"
                    )
                )

                version = clean(
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

                filename = clean(
                    payload.get(
                        "filename"
                    )
                    or payload.get(
                        "document_filename"
                    )
                    or (
                        f"naija_pocket_"
                        f"{job_id}.docx"
                    )
                )

                if not pages and text:
                    pages = [text]

                return {
                    "job_id": job_id,
                    "pages": pages,
                    "document_text": text,
                    "version_id": version,
                    "document_version": version,
                    "filename": filename,
                    "document_filename": filename,
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
                    "amount": money(
                        payload.get(
                            "amount"
                        )
                    ),
                    "status": clean(
                        payload.get(
                            "status"
                        )
                    ),
                    "review_finished": True,
                }

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "The old API returned no usable document."
    )


# ============================================================
# PUBLIC PAYMENT REPRESENTATION
# ============================================================

def payment_public(
    payment: dict[str, Any] | None,
) -> dict[str, Any] | None:

    if not payment:
        return None

    status = payment.get(
        "payment_status",
        "",
    )

    saved = saved_document_exists(
        payment
    )

    verified = payment_is_verified(
        status
    )

    reported = payment_is_reported(
        status
    )

    if reported:

        back_office_status = (
            "Payment Reported — Awaiting Verification"
        )

    elif verified:

        back_office_status = (
            "Payment Verified — Download Unlocked"
        )

    else:

        back_office_status = clean(
            status
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
            DEFAULT_CURRENCY,
        ),

        "payment_method": payment.get(
            "payment_method"
        ),

        "payment_status": status,

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

        "document_saved": saved,

        "document_snapshot_saved": saved,

        "document_saved_at": payment.get(
            "document_saved_at"
        ),

        "document_saved_size": (
            saved_document_size(
                payment
            )
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

        "reported": reported,

        "paid": verified,

        "payment_verified": verified,

        "download_unlocked": verified,

        "download_locked": not verified,

        "back_office_status": (
            back_office_status
        ),
    }


# ============================================================
# BACK OFFICE AUTHENTICATION
#
# IMPORTANT COMPATIBILITY NOTE
#
# The current payment.html does NOT send X-Admin-Key.
# Its Back Office access is currently controlled by the
# local key in payment.html.
#
# Therefore the API accepts the existing browser flow.
#
# If payment.html later sends X-Admin-Key, this API will
# validate it.
# ============================================================

def configured_admin_key() -> bool:
    return bool(
        BACK_OFFICE_ADMIN_KEY
    )


def extract_admin_key(
    request: Request,
    body: dict[str, Any] | None = None,
) -> str:

    header_key = clean(
        request.headers.get(
            "X-Admin-Key"
        )
        or request.headers.get(
            "x-admin-key"
        )
    )

    if header_key:
        return header_key

    if isinstance(
        body,
        dict,
    ):

        return clean(
            body.get(
                "admin_key"
            )
        )

    return ""


def admin_key_valid(
    request: Request,
    body: dict[str, Any] | None = None,
) -> bool:

    if not configured_admin_key():
        return True

    supplied = extract_admin_key(
        request,
        body,
    )

    # Existing payment.html does not currently send
    # the header. The UI itself performs its local
    # Back Office key check.
    #
    # A supplied header/body key is still strictly checked.
    if supplied:
        return (
            supplied
            == BACK_OFFICE_ADMIN_KEY
        )

    return True


def require_admin(
    request: Request,
    body: dict[str, Any] | None = None,
) -> JSONResponse | None:

    if not admin_key_valid(
        request,
        body,
    ):

        return json_response_error(
            "BACK_OFFICE_UNAUTHORIZED",
            "Invalid Back Office admin key.",
            401,
        )

    return None


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
async def root() -> dict[str, Any]:

    return {
        "ok": True,
        "service": (
            "Naija Pocket Business Center "
            "Payment API"
        ),
        "version": APP_VERSION,
        "old_api_configured": (
            old_api_configured()
        ),
        "back_office_configured": (
            configured_admin_key()
        ),
        "database": str(DB_PATH),
        "download_dir": str(
            DOWNLOAD_DIR
        ),
    }


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:

    return {
        "ok": True,
        "service": "payment_api",
        "version": APP_VERSION,
        "old_api_configured": (
            old_api_configured()
        ),
        "back_office_configured": (
            configured_admin_key()
        ),
        "database": str(DB_PATH),
        "download_dir": str(
            DOWNLOAD_DIR
        ),
    }


# ============================================================
# CUSTOMER PAYMENT CREATE
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
        or body.get(
            "job_id"
        )
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

    method = (
        clean(
            payment_method
            or body.get(
                "payment_method"
            )
        )
        or DEFAULT_PAYMENT_METHOD
    )

    if amount is None:
        amount = body.get(
            "amount"
        )

    if not job_id:

        return json_response_error(
            "JOB_ID_REQUIRED",
            "job_id is required.",
            400,
        )

    # --------------------------------------------------------
    # EXACT SNAPSHOT FROM PAYMENT HTML
    #
    # This is now the preferred source.
    # --------------------------------------------------------

    requested_version = clean(
        body.get(
            "version_id"
        )
        or body.get(
            "document_version"
        )
        or body.get(
            "versionId"
        )
    )

    supplied_pages_value = body.get(
        "pages"
    )

    if supplied_pages_value is None:

        supplied_pages_value = body.get(
            "document_pages"
        )

    supplied_pages = normalize_pages(
        supplied_pages_value
    )

    supplied_text = clean(
        body.get(
            "document_text"
        )
        or body.get(
            "text"
        )
        or body.get(
            "content"
        )
    )

    supplied_filename = clean(
        body.get(
            "filename"
        )
        or body.get(
            "document_filename"
        )
    )

    supplied_service = clean(
        service
    )

    supplied_customer = clean(
        customer_id
    )

    supplied_amount = money(
        amount
    )

    exact_snapshot_supplied = bool(
        requested_version
        and (
            supplied_pages
            or supplied_text
        )
    )

    # --------------------------------------------------------
    # If payment.html supplied the exact reviewed document,
    # use it directly.
    #
    # We DO NOT call OLD_API_BASE_URL first.
    # --------------------------------------------------------

    if exact_snapshot_supplied:

        doc = normalize_document(
            {
                "pages": supplied_pages,
                "document_text": supplied_text,
                "version_id": requested_version,
                "filename": (
                    supplied_filename
                    or (
                        f"naija_pocket_"
                        f"{job_id}.docx"
                    )
                ),
                "service": supplied_service,
                "customer_id": supplied_customer,
                "amount": supplied_amount,
            },
            job_id,
            version_id=requested_version,
            filename=(
                supplied_filename
                or (
                    f"naija_pocket_"
                    f"{job_id}.docx"
                )
            ),
            service=supplied_service,
            customer_id=supplied_customer,
            amount=supplied_amount,
        )

    else:

        # ----------------------------------------------------
        # FALLBACK ONLY
        #
        # Older clients that do not send the exact snapshot
        # can still use the old API bridge.
        # ----------------------------------------------------

        current_error = None

        try:

            doc = fetch_current_document(
                job_id
            )

        except Exception as exc:

            current_error = str(exc)
            doc = None

        if doc is None:

            return json_response_error(
                "DOCUMENT_LOOKUP_FAILED",
                (
                    "The exact reviewed document "
                    "was not supplied by Payment HTML "
                    "and the existing document service "
                    "could not be reached."
                ),
                502,
                detail=current_error,
                job_id=job_id,
            )

        doc = normalize_document(
            doc,
            job_id,
            version_id=clean(
                doc.get(
                    "version_id"
                )
            ),
            filename=clean(
                doc.get(
                    "filename"
                )
            ),
            service=(
                supplied_service
                or clean(
                    doc.get(
                        "service"
                    )
                )
            ),
            customer_id=(
                supplied_customer
                or clean(
                    doc.get(
                        "customer_id"
                    )
                )
            ),
            amount=(
                supplied_amount
                or money(
                    doc.get(
                        "amount"
                    )
                )
            ),
        )

    # --------------------------------------------------------
    # CONTENT CHECK
    # --------------------------------------------------------

    if not document_has_content(
        doc
    ):

        return json_response_error(
            "DOCUMENT_EMPTY",
            (
                "The reviewed document contains "
                "no downloadable content."
            ),
            409,
            job_id=job_id,
        )

    version = clean(
        doc.get(
            "version_id"
        )
    )

    if not version:

        return json_response_error(
            "VERSION_ID_REQUIRED",
            (
                "A document version is required "
                "before payment can be created."
            ),
            409,
            job_id=job_id,
        )

    final_amount = (
        money(
            doc.get(
                "amount"
            )
        )
        or supplied_amount
    )

    if final_amount <= 0:

        return json_response_error(
            "AMOUNT_NOT_AVAILABLE",
            (
                "The amount to pay is not available."
            ),
            409,
            job_id=job_id,
        )

    final_filename = (
        clean(
            doc.get(
                "filename"
            )
        )
        or (
            f"naija_pocket_"
            f"{job_id}.docx"
        )
    )

    # --------------------------------------------------------
    # REUSE EXISTING PAYMENT FOR SAME JOB/VERSION
    # --------------------------------------------------------

    latest = get_latest_payment_for_job(
        job_id
    )

    if latest:

        latest_version = clean(
            latest.get(
                "document_version"
            )
        )

        latest_status = normalize_status(
            latest.get(
                "payment_status"
            )
        )

        reusable_statuses = {
            "pending",
            "reported",
            "verification_pending",
            "awaiting_verification",
            "verified",
            "completed",
            "complete",
            "paid",
        }

        if (
            latest_version == version
            and latest_status
            in reusable_statuses
        ):

            # Existing record but missing file:
            # repair the missing snapshot from the
            # exact document supplied by this request.
            if not saved_document_exists(
                latest
            ):

                ok, error = (
                    save_exact_document_snapshot(
                        latest,
                        doc,
                    )
                )

                if not ok:

                    return json_response_error(
                        "DOCUMENT_SNAPSHOT_FAILED",
                        (
                            "The exact reviewed "
                            "document could not be "
                            "saved."
                        ),
                        500,
                        detail=error,
                        job_id=job_id,
                        version_id=version,
                        payment_id=latest.get(
                            "payment_id"
                        ),
                    )

                latest = (
                    get_payment(
                        latest[
                            "payment_id"
                        ]
                    )
                    or latest
                )

            return {
                "ok": True,
                "message": (
                    "Existing payment and exact "
                    "reviewed document snapshot reused."
                ),
                "payment": payment_public(
                    latest
                ),
                "payment_id": latest[
                    "payment_id"
                ],
                "amount": money(
                    latest[
                        "amount"
                    ]
                ),
                "currency": latest[
                    "currency"
                ],
                "payment_status": latest[
                    "payment_status"
                ],
                "paid": payment_is_verified(
                    latest[
                        "payment_status"
                    ]
                ),
                "payment_verified": (
                    payment_is_verified(
                        latest[
                            "payment_status"
                        ]
                    )
                ),
                "download_unlocked": (
                    payment_is_verified(
                        latest[
                            "payment_status"
                        ]
                    )
                ),
                "document_version": version,
                "version_id": version,
                "document_saved": True,
                "document_snapshot_saved": True,
            }

    # --------------------------------------------------------
    # CREATE NEW PAYMENT RECORD
    # --------------------------------------------------------

    payment_id = (
        "NPB-"
        + uuid.uuid4().hex[:12].upper()
    )

    final_customer_id = (
        supplied_customer
        or clean(
            doc.get(
                "customer_id"
            )
        )
    )

    final_service = (
        supplied_service
        or clean(
            doc.get(
                "service"
            )
        )
    )

    payload = snapshot_payload(
        doc
    )

    payload.update(
        {
            "job_id": job_id,
            "version_id": version,
            "document_version": version,
            "customer_id": final_customer_id,
            "service": final_service,
            "amount": final_amount,
            "currency": DEFAULT_CURRENCY,
        }
    )

    record = create_payment_record(
        payment_id=payment_id,
        job_id=job_id,
        customer_id=final_customer_id,
        service=final_service,
        amount=final_amount,
        currency=DEFAULT_CURRENCY,
        payment_method=method,
        document_version=version,
        document_filename=final_filename,
        document_payload=payload,
    )

    # --------------------------------------------------------
    # SAVE EXACT REVIEWED DOCUMENT
    # --------------------------------------------------------

    ok, error = save_exact_document_snapshot(
        record,
        doc,
    )

    if not ok:

        delete_payment_record(
            payment_id
        )

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            (
                "The exact reviewed document "
                "could not be saved. Payment has "
                "not been started."
            ),
            500,
            detail=error,
            job_id=job_id,
            version_id=version,
        )

    record = (
        get_payment(
            payment_id
        )
        or record
    )

    # Final safety check.
    if not saved_document_exists(
        record
    ):

        delete_payment_record(
            payment_id
        )

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            (
                "The document save could not "
                "be confirmed. Payment has "
                "not been started."
            ),
            500,
            job_id=job_id,
            version_id=version,
        )

    return {
        "ok": True,
        "message": (
            "Payment created and the exact "
            "reviewed document was saved successfully."
        ),
        "payment": payment_public(
            record
        ),
        "payment_id": payment_id,
        "amount": final_amount,
        "currency": DEFAULT_CURRENCY,
        "payment_status": "pending",
        "paid": False,
        "payment_verified": False,
        "download_unlocked": False,
        "document_version": version,
        "version_id": version,
        "document_saved": True,
        "document_snapshot_saved": True,
        "document_saved_at": record.get(
            "document_saved_at"
        ),
    }


# ============================================================
# CUSTOMER PAYMENT REPORT
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
        get_payment(
            payment_id
        )
        if payment_id
        else None
    )

    if not payment and job_id:

        payment = get_latest_payment_for_job(
            job_id
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            (
                "No payment record was found "
                "for this request."
            ),
            404,
        )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # Do NOT call the old API here.
    #
    # The payment record already contains the exact
    # snapshot that was created before payment reporting.
    # --------------------------------------------------------

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact reviewed document "
                "snapshot is missing. Payment "
                "cannot be reported safely."
            ),
            409,
            payment_id=payment[
                "payment_id"
            ],
            job_id=payment.get(
                "job_id"
            ),
            version_id=payment.get(
                "document_version"
            ),
        )

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if payment_is_verified(
        status
    ):

        return {
            "ok": True,
            "message": (
                "Payment has already been verified."
            ),
            "payment": payment_public(
                payment
            ),
            "paid": True,
            "payment_verified": True,
            "download_unlocked": True,
            "document_saved": True,
        }

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
            "Payment report could not be saved.",
            500,
            payment_id=payment[
                "payment_id"
            ],
        )

    return {
        "ok": True,
        "message": (
            "Payment report received. "
            "Customer Care must verify the "
            "payment before download is unlocked."
        ),
        "payment": payment_public(
            updated
        ),
        "payment_id": updated.get(
            "payment_id"
        ),
        "job_id": updated.get(
            "job_id"
        ),
        "document_version": updated.get(
            "document_version"
        ),
        "document_saved": True,
        "paid": False,
        "payment_verified": False,
        "download_unlocked": False,
        "back_office_status": (
            "Payment Reported — Awaiting Verification"
        ),
    }


# ============================================================
# CUSTOMER PAYMENT STATUS
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

        payment = get_latest_payment_for_job(
            clean(job_id)
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

    saved = saved_document_exists(
        payment
    )

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    return {
        "ok": True,
        "payment": payment_public(
            payment
        ),
        "payment_status": payment.get(
            "payment_status"
        ),
        "paid": verified,
        "payment_verified": verified,
        "download_unlocked": verified,
        "document_saved": saved,
        "document_check": (
            "current_snapshot"
            if saved
            else "missing_snapshot"
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
# CUSTOMER CARE PAYMENTS
# ============================================================

@app.get("/api/customer-care/payments")
async def customer_care_payments():

    records = list_pending_payments()

    return {
        "ok": True,
        "count": len(records),
        "payments": [
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

                raw_verified = body.get(
                    "verified"
                )

                if isinstance(
                    raw_verified,
                    bool,
                ):

                    verified = raw_verified

                else:

                    verified = (
                        clean(
                            raw_verified
                        ).lower()
                        in {
                            "true",
                            "1",
                            "yes",
                            "verified",
                        }
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
            "message": (
                "Payment marked as rejected."
            ),
            "payment": payment_public(
                updated
            ),
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "Customer Care cannot unlock "
                "this payment because the exact "
                "document snapshot is missing."
            ),
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
            "Payment could not be verified.",
            500,
            payment_id=payment_id,
        )

    return {
        "ok": True,
        "message": (
            "Payment verified. Download is "
            "now unlocked for this document version."
        ),
        "payment": payment_public(
            updated
        ),
        "paid": True,
        "payment_verified": True,
        "download_unlocked": True,
        "document_saved": True,
    }


# ============================================================
# BACK OFFICE LOGIN
# ============================================================

@app.post(
    "/api/back-office/login"
)
async def back_office_login(
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

    # If an admin key is configured and supplied,
    # validate it.
    #
    # Existing payment.html currently performs its own
    # local Back Office key gate.
    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    return {
        "ok": True,
        "authenticated": True,
        "back_office": True,
        "message": (
            "Back Office access granted."
        ),
    }


# ============================================================
# BACK OFFICE PAYMENT LIST
# ============================================================

@app.get(
    "/api/back-office/payments"
)
async def back_office_payments(
    request: Request,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    records = list_all_payments()

    pending = [
        payment_public(
            record
        )
        for record in records
        if payment_is_reported(
            record.get(
                "payment_status",
                "",
            )
        )
    ]

    return {
        "ok": True,

        "count": len(
            records
        ),

        "pending_count": len(
            pending
        ),

        "payments": [
            payment_public(
                record
            )
            for record in records
        ],

        "pending_payments": pending,
    }


# ============================================================
# BACK OFFICE JOB LIST
#
# Compatibility with existing Workspace Back Office code.
# ============================================================

@app.get(
    "/api/back-office/jobs"
)
async def back_office_jobs(
    request: Request,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    records = list_all_payments()

    jobs: list[dict[str, Any]] = []

    for payment in records:

        public = payment_public(
            payment
        )

        if not public:
            continue

        public["job_number"] = public[
            "job_id"
        ]

        public["work_id"] = public[
            "job_id"
        ]

        public["document_id"] = public[
            "job_id"
        ]

        public["status_label"] = (
            "Payment Reported — Awaiting Verification"
            if payment_is_reported(
                payment.get(
                    "payment_status",
                    "",
                )
            )
            else (
                "Payment Verified — Download Unlocked"
                if payment_is_verified(
                    payment.get(
                        "payment_status",
                        "",
                    )
                )
                else clean(
                    payment.get(
                        "payment_status"
                    )
                )
            )
        )

        jobs.append(
            public
        )

    return {
        "ok": True,
        "count": len(jobs),
        "jobs": jobs,
        "payments": jobs,
    }


# ============================================================
# BACK OFFICE SINGLE PAYMENT
# ============================================================

@app.get(
    "/api/back-office/payment"
)
async def back_office_payment(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = None

    if clean(payment_id):

        payment = get_payment(
            clean(payment_id)
        )

    if not payment and clean(job_id):

        payment = get_latest_payment_for_job(
            clean(job_id)
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    return {
        "ok": True,
        "payment": payment_public(
            payment
        ),
    }


# ============================================================
# BACK OFFICE DOCUMENT INFO
# ============================================================

@app.get(
    "/api/back-office/document-info"
)
async def back_office_document_info(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = None

    if clean(payment_id):

        payment = get_payment(
            clean(payment_id)
        )

    if not payment and clean(job_id):

        payment = get_latest_payment_for_job(
            clean(job_id)
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    exists = saved_document_exists(
        payment
    )

    path = saved_document_path(
        payment
    )

    return {
        "ok": True,

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

        "document_saved": exists,

        "document_snapshot_saved": exists,

        "document_saved_at": payment.get(
            "document_saved_at"
        ),

        "document_size": saved_document_size(
            payment
        ),

        "document_path": (
            str(path)
            if exists and path
            else None
        ),

        "payment_status": payment.get(
            "payment_status"
        ),

        "download_unlocked": payment_is_verified(
            payment.get(
                "payment_status",
                "",
            )
        ),
    }


# ============================================================
# BACK OFFICE SAVED DOCUMENT
#
# Serves the already saved file.
# NEVER regenerates.
# ============================================================

@app.get(
    "/api/back-office/document"
)
async def back_office_document(
    request: Request,
    payment_id: str | None = None,
    job_id: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    payment = None

    if clean(payment_id):

        payment = get_payment(
            clean(payment_id)
        )

    if not payment and clean(job_id):

        payment = get_latest_payment_for_job(
            clean(job_id)
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

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
            (
                "The exact saved document "
                "snapshot is missing."
            ),
            409,
            payment_id=payment.get(
                "payment_id"
            ),
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
            "X-Payment-ID": payment[
                "payment_id"
            ],

            "X-Document-Version": clean(
                payment.get(
                    "document_version"
                )
            ),

            "X-Document-Snapshot": "true",

            "X-Back-Office": "true",
        },
    )


# ============================================================
# BACK OFFICE VERIFY PAYMENT
#
# This only changes:
#
# reported → verified
#
# It never regenerates the document.
# ============================================================

@app.post(
    "/api/back-office/payment/verify"
)
async def back_office_verify_payment(
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

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    note = clean(
        body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
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

    status = normalize_status(
        payment.get(
            "payment_status"
        )
    )

    if status in {
        "rejected",
        "cancelled",
        "canceled",
    }:

        return json_response_error(
            "PAYMENT_NOT_VERIFIABLE",
            (
                "This payment cannot be unlocked "
                "because it is marked as rejected "
                "or cancelled."
            ),
            409,
            payment_id=payment_id,
        )

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "UNLOCK DOWNLOAD cannot continue "
                "because the exact saved document "
                "snapshot is missing."
            ),
            409,
            payment_id=payment_id,
            job_id=payment.get(
                "job_id"
            ),
            document_version=payment.get(
                "document_version"
            ),
            download_unlocked=False,
        )

    updated = update_payment_record(
        payment_id,
        status="verified",
        admin_note=(
            note
            or None
        ),
        verified_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Payment could not be verified.",
            500,
            payment_id=payment_id,
        )

    return {
        "ok": True,
        "message": (
            "Payment verified. "
            "UNLOCK DOWNLOAD is now active "
            "for the exact saved document."
        ),
        "payment": payment_public(
            updated
        ),
        "payment_id": payment_id,
        "job_id": updated.get(
            "job_id"
        ),
        "document_version": updated.get(
            "document_version"
        ),
        "document_saved": True,
        "payment_verified": True,
        "download_unlocked": True,
    }


# ============================================================
# BACK OFFICE ACTIVATE DOWNLOAD
#
# Existing compatibility endpoint.
#
# It performs exactly the same safe operation:
#
# existing snapshot → verified
#
# NO regeneration.
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

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

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

    note = clean(
        body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
    )

    payment = None

    if payment_id:

        payment = get_payment(
            payment_id
        )

    if not payment and job_id:

        payment = get_latest_payment_for_job(
            job_id
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "No payment record was found.",
            404,
        )

    payment_id = clean(
        payment.get(
            "payment_id"
        )
    )

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "UNLOCK DOWNLOAD cannot continue "
                "because the exact saved document "
                "snapshot is missing."
            ),
            409,
            payment_id=payment_id,
            job_id=payment.get(
                "job_id"
            ),
            document_version=payment.get(
                "document_version"
            ),
            download_unlocked=False,
        )

    updated = update_payment_record(
        payment_id,
        status="verified",
        admin_note=(
            note
            or None
        ),
        verified_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Download could not be unlocked.",
            500,
            payment_id=payment_id,
        )

    return {
        "ok": True,
        "message": (
            "Payment verified and download "
            "unlocked for the exact saved document."
        ),
        "payment": payment_public(
            updated
        ),
        "payment_id": payment_id,
        "job_id": updated.get(
            "job_id"
        ),
        "work_id": updated.get(
            "job_id"
        ),
        "document_id": updated.get(
            "job_id"
        ),
        "document_version": updated.get(
            "document_version"
        ),
        "document_saved": True,
        "payment_verified": True,
        "download_unlocked": True,
    }


# ============================================================
# BACK OFFICE REJECT
# ============================================================

@app.post(
    "/api/back-office/payment/reject"
)
async def back_office_reject_payment(
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

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    payment_id = clean(
        body.get(
            "payment_id"
        )
    )

    note = clean(
        body.get(
            "note"
        )
        or body.get(
            "admin_note"
        )
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

    if payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_response_error(
            "PAYMENT_ALREADY_UNLOCKED",
            (
                "This payment is already verified "
                "and download is unlocked."
            ),
            409,
            payment_id=payment_id,
        )

    updated = update_payment_record(
        payment_id,
        status="rejected",
        admin_note=(
            note
            or None
        ),
    )

    return {
        "ok": True,
        "message": (
            "Payment marked as rejected. "
            "Download remains locked."
        ),
        "payment": payment_public(
            updated
        ),
        "payment_verified": False,
        "download_unlocked": False,
    }


# ============================================================
# CUSTOMER DOWNLOAD
#
# Serves EXACT saved snapshot.
# NEVER regenerates.
# ============================================================

@app.get(
    "/api/download"
)
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

        payment = get_latest_payment_for_job(
            clean(job_id)
        )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
        )

    if not payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            (
                "Download remains locked until "
                "Customer Care verifies the payment."
            ),
            403,
            payment_status=payment.get(
                "payment_status"
            ),
            payment_id=payment.get(
                "payment_id"
            ),
            download_unlocked=False,
        )

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
            (
                "The paid document snapshot is missing. "
                "Download remains locked for safety."
            ),
            409,
            payment_id=payment.get(
                "payment_id"
            ),
            version_id=payment.get(
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
            "X-Payment-ID": payment[
                "payment_id"
            ],

            "X-Document-Version": clean(
                payment.get(
                    "document_version"
                )
            ),

            "X-Download-Unlocked": "true",

            "X-Document-Snapshot": "true",
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
        "[PAYMENT API] Version: "
        + APP_VERSION
    )

    print(
        "[PAYMENT API] Database: "
        + str(DB_PATH)
    )

    print(
        "[PAYMENT API] Download directory: "
        + str(DOWNLOAD_DIR)
    )

    print(
        "[PAYMENT API] Old API configured: "
        + str(
            bool(
                OLD_API_BASE_URL
            )
        )
    )

    print(
        "[PAYMENT API] Back Office key configured: "
        + str(
            bool(
                BACK_OFFICE_ADMIN_KEY
            )
        )
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
