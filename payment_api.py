from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
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
# DOCUMENT-FIRST PAYMENT + BACK OFFICE
#
# BUSINESS WORKFLOW
#
# REVIEW PAGE
#      ↓
# MAKE PAYMENT
#      ↓
# SAVE EXACT REVIEWED DOCUMENT
#      ↓
# PAYMENT DETAILS / PAYMENT
#      ↓
# CUSTOMER SERVICE VERIFIES
#      ↓
# ACTIVATE DOWNLOAD
#      ↓
# SAME SAVED DOCUMENT
#
# IMPORTANT
#
# The customer/product workflow is based on the actual business
# document context:
#
#       service + document title
#
# There is NO customer-facing Job ID, Payment ID or Product ID
# workflow.
#
# The saved document is authoritative.
#
# Once the reviewed document has been saved:
#
#       NEVER regenerate it after payment.
#       NEVER replace it with a later version.
#       NEVER create another product merely for payment.
# ============================================================


APP_VERSION = "payment-document-first-v1"

BASE_DIR = Path(__file__).resolve().parent


# ============================================================
# DATABASE
# ============================================================

_raw_db_path = os.getenv(
    "PAYMENT_DB_PATH",
    str(BASE_DIR / "payment_gateway.db"),
).strip()

DB_PATH = Path(_raw_db_path).expanduser()

if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

DB_PATH = DB_PATH.resolve()


# ============================================================
# DOWNLOAD STORAGE
# ============================================================

_raw_download_dir = os.getenv(
    "DOWNLOAD_DIR",
    str(BASE_DIR / "downloads"),
).strip()

DOWNLOAD_DIR = Path(_raw_download_dir).expanduser()

if not DOWNLOAD_DIR.is_absolute():
    DOWNLOAD_DIR = BASE_DIR / DOWNLOAD_DIR

DOWNLOAD_DIR = DOWNLOAD_DIR.resolve()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# OPTIONAL EXISTING DOCUMENT API BRIDGE
#
# This is ONLY a fallback when the current payment request does
# not contain the reviewed document itself.
#
# Preferred path:
#
# Review page
#     →
# payment request containing exact reviewed document
#     →
# snapshot saved locally
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
# BACK OFFICE
# ============================================================

BACK_OFFICE_ADMIN_KEY = (
    os.getenv("BACK_OFFICE_ADMIN_KEY", "")
    .strip()
)


# ============================================================
# OPTIONAL DELIVERY WEBHOOKS
# ============================================================

DELIVERY_EMAIL_WEBHOOK = (
    os.getenv("DELIVERY_EMAIL_WEBHOOK", "")
    .strip()
)

DELIVERY_WHATSAPP_WEBHOOK = (
    os.getenv("DELIVERY_WHATSAPP_WEBHOOK", "")
    .strip()
)

DELIVERY_TELEGRAM_WEBHOOK = (
    os.getenv("DELIVERY_TELEGRAM_WEBHOOK", "")
    .strip()
)

DELIVERY_GOOGLE_DRIVE_WEBHOOK = (
    os.getenv("DELIVERY_GOOGLE_DRIVE_WEBHOOK", "")
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


def normalize_status(value: Any) -> str:
    return (
        clean(value)
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


def payment_is_reported(status: Any) -> bool:
    return normalize_status(status) in {
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


def payment_is_verified(status: Any) -> bool:
    return normalize_status(status) in {
        "verified",
        "completed",
        "complete",
        "paid",
    }


def payment_is_pending(status: Any) -> bool:
    return normalize_status(status) in {
        "pending",
        "created",
        "initiated",
        "reported",
        "verification_pending",
        "awaiting_verification",
    }


# ============================================================
# REQUEST BODY
# ============================================================

async def read_json_body(
    request: Request,
) -> dict[str, Any]:
    try:
        value = await request.json()

        if isinstance(value, dict):
            return value

    except Exception:
        pass

    return {}


def nested_value(
    body: dict[str, Any],
    *keys: str,
) -> Any:

    for key in keys:
        value = body.get(key)

        if value not in (None, ""):
            return value

    for container_name in (
        "payment",
        "document",
        "review",
        "business",
        "order",
        "data",
    ):
        container = body.get(container_name)

        if isinstance(container, dict):
            for key in keys:
                value = container.get(key)

                if value not in (None, ""):
                    return value

    return None


# ============================================================
# BUSINESS CONTEXT
#
# SERVICE + TITLE is the product/business identity.
# ============================================================

def body_service(
    body: dict[str, Any],
) -> str:

    return clean(
        nested_value(
            body,
            "service",
            "service_name",
            "serviceName",
        )
    )


def body_title(
    body: dict[str, Any],
) -> str:

    return clean(
        nested_value(
            body,
            "title",
            "document_title",
            "documentTitle",
            "work_title",
            "workTitle",
            "name",
        )
    )


def body_customer(
    body: dict[str, Any],
) -> str:

    return clean(
        nested_value(
            body,
            "customer",
            "customer_name",
            "customerName",
            "customer_id",
            "customerId",
        )
    )


def body_amount(
    body: dict[str, Any],
) -> float:

    value = nested_value(
        body,
        "amount",
        "total_amount",
        "totalAmount",
        "price",
    )

    return money(value)


def body_payment_method(
    body: dict[str, Any],
) -> str:

    return (
        clean(
            nested_value(
                body,
                "payment_method",
                "paymentMethod",
                "method",
            )
        )
        or DEFAULT_PAYMENT_METHOD
    )


def body_note(
    body: dict[str, Any],
) -> str:

    return clean(
        nested_value(
            body,
            "note",
            "message",
            "customer_note",
            "customerNote",
        )
    )


def body_reference(
    body: dict[str, Any],
) -> str:

    return clean(
        nested_value(
            body,
            "payment_reference",
            "paymentReference",
            "reference",
        )
    )


def body_version(
    body: dict[str, Any],
) -> str:

    return clean(
        nested_value(
            body,
            "version_id",
            "versionId",
            "document_version",
            "documentVersion",
            "version",
        )
    )


# ============================================================
# BUSINESS KEY
#
# Deliberately based on:
#
#       service + document title
# ============================================================

def normalize_business_part(
    value: Any,
) -> str:

    return " ".join(
        clean(value).lower().split()
    )


def business_key(
    service: Any,
    title: Any,
) -> str:

    service_part = normalize_business_part(
        service
    )

    title_part = normalize_business_part(
        title
    )

    if not service_part and not title_part:
        return ""

    return f"{service_part}::{title_part}"


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

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    return conn


def init_db() -> None:

    with connect_db() as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                business_key TEXT NOT NULL UNIQUE,
                service TEXT,
                document_title TEXT,
                customer_name TEXT,
                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'NGN',
                payment_method TEXT NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'pending',
                payment_reference TEXT,
                customer_note TEXT,
                admin_note TEXT,
                document_version TEXT,
                document_filename TEXT,
                document_payload TEXT,
                document_saved_path TEXT,
                document_saved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reported_at TEXT,
                verified_at TEXT,
                downloaded_at TEXT,
                download_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(payment_orders)"
            ).fetchall()
        }

        additions = {
            "business_key": "TEXT",
            "service": "TEXT",
            "document_title": "TEXT",
            "customer_name": "TEXT",
            "amount": "REAL NOT NULL DEFAULT 0",
            "currency": "TEXT",
            "payment_method": "TEXT",
            "payment_status": "TEXT",
            "payment_reference": "TEXT",
            "customer_note": "TEXT",
            "admin_note": "TEXT",
            "document_version": "TEXT",
            "document_filename": "TEXT",
            "document_payload": "TEXT",
            "document_saved_path": "TEXT",
            "document_saved_at": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
            "reported_at": "TEXT",
            "verified_at": "TEXT",
            "downloaded_at": "TEXT",
            "download_count": "INTEGER NOT NULL DEFAULT 0",
        }

        for name, definition in additions.items():

            if name not in columns:

                try:
                    conn.execute(
                        f"""
                        ALTER TABLE payment_orders
                        ADD COLUMN {name} {definition}
                        """
                    )

                except sqlite3.OperationalError:
                    pass

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_business_key
            ON payment_orders(business_key)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_payment_orders_status
            ON payment_orders(payment_status)
            """
        )

        conn.commit()


# ============================================================
# DATABASE DIAGNOSTICS
# ============================================================

def database_file_info() -> dict[str, Any]:

    info: dict[str, Any] = {
        "path": str(DB_PATH),
        "exists": DB_PATH.is_file(),
        "absolute": DB_PATH.is_absolute(),
        "size": 0,
        "modified_at": None,
    }

    try:

        if DB_PATH.is_file():

            stat = DB_PATH.stat()

            info["size"] = stat.st_size

            info["modified_at"] = (
                datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                ).isoformat()
            )

    except Exception:
        pass

    return info


def payment_record_count() -> int:

    try:

        init_db()

        with connect_db() as conn:

            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM payment_orders
                """
            ).fetchone()

        return int(
            row["count"]
            if row
            else 0
        )

    except Exception:
        return 0


# ============================================================
# PAYMENT RECORD LOOKUP
# ============================================================

def get_payment_by_business(
    service: str,
    title: str,
) -> dict[str, Any] | None:

    key = business_key(
        service,
        title,
    )

    if not key:
        return None

    init_db()

    with connect_db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM payment_orders
            WHERE business_key = ?
            LIMIT 1
            """,
            (key,),
        ).fetchone()

    return dict(row) if row else None


def get_payment_by_context(
    body: dict[str, Any],
) -> dict[str, Any] | None:

    service = body_service(body)
    title = body_title(body)

    return get_payment_by_business(
        service,
        title,
    )


def list_all_payments() -> list[dict[str, Any]]:

    init_db()

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

    init_db()

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
    *,
    service: str = "",
    title: str = "",
    customer_name: str = "",
    amount: float = 0.0,
    version_id: str = "",
    filename: str = "",
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

    final_service = (
        clean(service)
        or clean(document.get("service"))
    )

    final_title = (
        clean(title)
        or clean(document.get("title"))
        or clean(document.get("document_title"))
        or clean(document.get("documentTitle"))
        or clean(document.get("work_title"))
        or clean(document.get("workTitle"))
    )

    final_customer = (
        clean(customer_name)
        or clean(document.get("customer_name"))
        or clean(document.get("customer"))
    )

    final_amount = (
        money(amount)
        or money(document.get("amount"))
        or money(document.get("total_amount"))
        or money(document.get("price"))
    )

    final_version = (
        clean(version_id)
        or clean(document.get("version_id"))
        or clean(document.get("document_version"))
        or clean(document.get("version"))
    )

    final_filename = (
        clean(filename)
        or clean(document.get("filename"))
        or clean(document.get("document_filename"))
        or "naija_pocket_document.docx"
    )

    final_filename = Path(
        final_filename
    ).name

    if not final_filename.lower().endswith(".docx"):
        final_filename += ".docx"

    return {
        "service": final_service,
        "title": final_title,
        "document_title": final_title,
        "customer_name": final_customer,
        "pages": pages,
        "document_text": text,
        "version_id": final_version,
        "document_version": final_version,
        "filename": final_filename,
        "document_filename": final_filename,
        "amount": final_amount,
        "review_finished": True,
        "status": "review_complete",
    }


# ============================================================
# EXACT SNAPSHOT PAYLOAD
# ============================================================

def snapshot_payload(
    document: dict[str, Any],
) -> dict[str, Any]:

    return {
        "service": clean(
            document.get("service")
        ),
        "title": clean(
            document.get("title")
            or document.get("document_title")
        ),
        "document_title": clean(
            document.get("document_title")
            or document.get("title")
        ),
        "customer_name": clean(
            document.get("customer_name")
        ),
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
        "filename": clean(
            document.get("filename")
        ),
        "document_filename": clean(
            document.get("document_filename")
        ),
        "amount": money(
            document.get("amount")
        ),
    }


def payment_document(
    payment: dict[str, Any],
) -> dict[str, Any]:

    raw = (
        payment.get("document_payload")
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

    path = saved_document_path(payment)

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

    path = saved_document_path(payment)

    if not path:
        return 0

    try:
        return path.stat().st_size
    except Exception:
        return 0


# ============================================================
# DOCX CREATION
#
# ONLY USED AT MAKE PAYMENT SNAPSHOT STAGE.
#
# NEVER called by:
#   payment/report
#   verify
#   activate
#   download
# ============================================================

def xml_escape(value: str) -> str:

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
    output_path: Path,
) -> Path:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    body_parts: list[str] = []

    for index, page in enumerate(pages):

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
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
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
</w:styles>"""

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default
    Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default
    Extension="xml"
    ContentType="application/xml"/>
  <Override
    PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override
    PartName="/word/styles.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>"""

    document_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>"""

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
# SAVE EXACT DOCUMENT
# ============================================================

def save_exact_document_snapshot(
    payment: dict[str, Any],
    document: dict[str, Any],
) -> tuple[bool, str]:

    # --------------------------------------------------------
    # NEVER overwrite an already-saved snapshot.
    # --------------------------------------------------------

    if saved_document_exists(payment):

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
            document.get("document_filename")
        )
        or "naija_pocket_document.docx"
    )

    filename = Path(filename).name

    if not filename.lower().endswith(".docx"):
        filename += ".docx"

    service = clean(
        payment.get("service")
    )

    title = clean(
        payment.get("document_title")
    )

    key = business_key(
        service,
        title,
    )

    if not key:

        return (
            False,
            "The business document context is incomplete. Service and document title are required.",
        )

    safe_name = "".join(
        character
        if character.isalnum()
        else "_"
        for character in (
            f"{service}_{title}"
        )
    ).strip("_")

    if not safe_name:
        safe_name = "naija_pocket_document"

    folder = DOWNLOAD_DIR / safe_name

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = folder / filename

    try:

        make_docx(
            pages,
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
                WHERE business_key = ?
                """,
                (
                    str(output),
                    timestamp,
                    filename,
                    timestamp,
                    key,
                ),
            )

            conn.commit()

        refreshed = get_payment_by_business(
            service,
            title,
        )

        if not refreshed:

            return (
                False,
                "The payment business record disappeared after document save.",
            )

        if not saved_document_exists(
            refreshed
        ):

            return (
                False,
                "The document file was created but could not be confirmed.",
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
# OPTIONAL OLD DOCUMENT API FALLBACK
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

            if value in (None, ""):
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
            "NaijaPocketPaymentAPI/document-first"
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
            f"Existing document API returned HTTP "
            f"{exc.code}: {detail}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"Could not reach existing document API: {exc}"
        ) from exc


def extract_document_payload(
    response: Any,
) -> dict[str, Any] | None:

    if not isinstance(response, dict):
        return None

    candidates = [response]

    for key in (
        "data",
        "document",
        "review",
        "result",
    ):

        value = response.get(key)

        if isinstance(value, dict):
            candidates.append(value)

    for candidate in candidates:

        if any(
            key in candidate
            for key in (
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
    service: str,
    title: str,
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
                    "service": service,
                    "title": title,
                },
            )

            payload = extract_document_payload(
                response
            )

            if payload:

                return normalize_document(
                    payload,
                    service=service,
                    title=title,
                )

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "The existing document service returned no usable reviewed document."
    )


# ============================================================
# PAYMENT DATABASE WRITE
# ============================================================

def create_payment_record(
    *,
    service: str,
    title: str,
    customer_name: str,
    amount: float,
    currency: str,
    payment_method: str,
    document_version: str,
    document_filename: str,
    document_payload: dict[str, Any],
) -> dict[str, Any]:

    key = business_key(
        service,
        title,
    )

    if not key:

        raise RuntimeError(
            "A service and document title are required."
        )

    timestamp = now_iso()

    payload_json = json.dumps(
        document_payload,
        ensure_ascii=False,
    )

    with connect_db() as conn:

        try:

            conn.execute(
                "BEGIN IMMEDIATE"
            )

            conn.execute(
                """
                INSERT INTO payment_orders (
                    business_key,
                    service,
                    document_title,
                    customer_name,
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
                    key,
                    service,
                    title,
                    customer_name,
                    amount,
                    currency,
                    payment_method,
                    document_version,
                    document_filename,
                    payload_json,
                    timestamp,
                    timestamp,
                ),
            )

            row = conn.execute(
                """
                SELECT *
                FROM payment_orders
                WHERE business_key = ?
                LIMIT 1
                """,
                (key,),
            ).fetchone()

            if row is None:

                conn.rollback()

                raise RuntimeError(
                    "PAYMENT_PERSISTENCE_FAILED: "
                    "payment business record was inserted "
                    "but could not be read inside the transaction."
                )

            conn.commit()

        except Exception:

            try:
                conn.rollback()
            except Exception:
                pass

            raise

    persisted = get_payment_by_business(
        service,
        title,
    )

    if not persisted:

        raise RuntimeError(
            "PAYMENT_PERSISTENCE_FAILED: "
            "payment business record could not be read after commit."
        )

    return persisted


def update_payment_record(
    payment: dict[str, Any],
    *,
    status: str | None = None,
    payment_reference: str | None = None,
    customer_note: str | None = None,
    admin_note: str | None = None,
    verified_at: str | None = None,
    reported_at: str | None = None,
) -> dict[str, Any] | None:

    service = clean(
        payment.get("service")
    )

    title = clean(
        payment.get("document_title")
    )

    key = business_key(
        service,
        title,
    )

    if not key:
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

    values.append(key)

    with connect_db() as conn:

        conn.execute(
            f"""
            UPDATE payment_orders
            SET {', '.join(fields)}
            WHERE business_key = ?
            """,
            tuple(values),
        )

        conn.commit()

    return get_payment_by_business(
        service,
        title,
    )


def increment_download(
    payment: dict[str, Any],
) -> dict[str, Any] | None:

    service = clean(
        payment.get("service")
    )

    title = clean(
        payment.get("document_title")
    )

    key = business_key(
        service,
        title,
    )

    if not key:
        return None

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
            WHERE business_key = ?
            """,
            (
                timestamp,
                timestamp,
                key,
            ),
        )

        conn.commit()

    return get_payment_by_business(
        service,
        title,
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
        "service": payment.get(
            "service"
        ),
        "title": payment.get(
            "document_title"
        ),
        "document_title": payment.get(
            "document_title"
        ),
        "customer_name": payment.get(
            "customer_name"
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
        "document_filename": payment.get(
            "document_filename"
        ),
        "document_saved": saved,
        "document_snapshot_saved": saved,
        "document_saved_at": payment.get(
            "document_saved_at"
        ),
        "document_saved_size": (
            saved_document_size(payment)
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
        "back_office_status": back_office_status,
    }


# ============================================================
# ADMIN AUTHENTICATION
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
    )

    if header_key:
        return header_key

    if isinstance(body, dict):
        return clean(
            body.get("admin_key")
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

    return supplied == BACK_OFFICE_ADMIN_KEY


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
        "workflow": (
            "review → save exact document → "
            "payment → verify → activate → delivery"
        ),
        "business_identity": (
            "service + document title"
        ),
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
        "database_info": database_file_info(),
        "payment_record_count": (
            payment_record_count()
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
        "database_info": database_file_info(),
        "payment_record_count": (
            payment_record_count()
        ),
        "process_id": os.getpid(),
    }


# ============================================================
# CUSTOMER PAYMENT CREATE
#
# MAKE PAYMENT ACTION
#
# 1. Receive exact reviewed document.
# 2. Establish service + title business context.
# 3. Save payment record.
# 4. Save exact reviewed document.
# 5. Return payment details.
#
# Nothing after this endpoint regenerates the document.
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: Request,
):

    body = await read_json_body(request)

    service = body_service(body)
    title = body_title(body)
    customer_name = body_customer(body)
    amount = body_amount(body)
    payment_method = body_payment_method(
        body
    )
    version = body_version(body)

    if not service:

        return json_response_error(
            "SERVICE_REQUIRED",
            "The service is required.",
            400,
        )

    if not title:

        return json_response_error(
            "DOCUMENT_TITLE_REQUIRED",
            (
                "The document title is required "
                "so the reviewed business document "
                "can be identified."
            ),
            400,
        )

    # --------------------------------------------------------
    # EXACT REVIEWED DOCUMENT
    # --------------------------------------------------------

    supplied_pages = normalize_pages(
        nested_value(
            body,
            "pages",
            "document_pages",
            "review_pages",
            "page_texts",
        )
    )

    supplied_text = clean(
        nested_value(
            body,
            "document_text",
            "text",
            "content",
        )
    )

    supplied_filename = clean(
        nested_value(
            body,
            "filename",
            "document_filename",
            "documentFilename",
        )
    )

    document_object = body.get(
        "document"
    )

    if isinstance(
        document_object,
        dict,
    ):

        if not supplied_pages:

            supplied_pages = normalize_pages(
                document_object.get("pages")
                or document_object.get(
                    "document_pages"
                )
            )

        if not supplied_text:

            supplied_text = clean(
                document_object.get(
                    "document_text"
                )
                or document_object.get(
                    "text"
                )
                or document_object.get(
                    "content"
                )
            )

        if not supplied_filename:

            supplied_filename = clean(
                document_object.get(
                    "filename"
                )
                or document_object.get(
                    "document_filename"
                )
            )

    # --------------------------------------------------------
    # CURRENT REQUEST DOCUMENT IS AUTHORITATIVE
    # --------------------------------------------------------

    if supplied_pages or supplied_text:

        document = normalize_document(
            {
                "pages": supplied_pages,
                "document_text": supplied_text,
                "service": service,
                "title": title,
                "customer_name": customer_name,
                "amount": amount,
                "version_id": version,
                "filename": supplied_filename,
            },
            service=service,
            title=title,
            customer_name=customer_name,
            amount=amount,
            version_id=version,
            filename=supplied_filename,
        )

    else:

        # ----------------------------------------------------
        # Compatibility fallback.
        # ----------------------------------------------------

        try:

            document = fetch_current_document(
                service,
                title,
            )

            document = normalize_document(
                document,
                service=service,
                title=title,
                customer_name=customer_name,
                amount=amount,
                version_id=(
                    version
                    or document.get(
                        "version_id"
                    )
                ),
                filename=(
                    supplied_filename
                    or document.get(
                        "filename"
                    )
                ),
            )

        except Exception as exc:

            return json_response_error(
                "DOCUMENT_LOOKUP_FAILED",
                (
                    "The exact reviewed document "
                    "was not included in the payment "
                    "request and could not be recovered "
                    "from the existing document service."
                ),
                409,
                detail=str(exc),
                service=service,
                title=title,
            )

    # --------------------------------------------------------
    # CONTENT CHECK
    # --------------------------------------------------------

    if not document_has_content(
        document
    ):

        return json_response_error(
            "DOCUMENT_EMPTY",
            (
                "The reviewed document contains "
                "no downloadable content."
            ),
            409,
            service=service,
            title=title,
        )

    version = clean(
        document.get(
            "version_id"
        )
    )

    final_amount = (
        money(
            document.get(
                "amount"
            )
        )
        or amount
    )

    if final_amount <= 0:

        return json_response_error(
            "AMOUNT_NOT_AVAILABLE",
            (
                "The amount to pay is not available."
            ),
            409,
            service=service,
            title=title,
        )

    final_filename = (
        clean(
            document.get(
                "filename"
            )
        )
        or "naija_pocket_document.docx"
    )

    # --------------------------------------------------------
    # EXISTING BUSINESS RECORD
    #
    # Reuse same service + title.
    # Never create another business product merely
    # for payment.
    # --------------------------------------------------------

    existing = get_payment_by_business(
        service,
        title,
    )

    if existing:

        existing_status = normalize_status(
            existing.get(
                "payment_status"
            )
        )

        if existing_status in {
            "pending",
            "reported",
            "verification_pending",
            "awaiting_verification",
            "verified",
            "completed",
            "complete",
            "paid",
        }:

            # Never replace an already saved document.

            if not saved_document_exists(
                existing
            ):

                ok, error = (
                    save_exact_document_snapshot(
                        existing,
                        document,
                    )
                )

                if not ok:

                    return json_response_error(
                        "DOCUMENT_SNAPSHOT_FAILED",
                        (
                            "The exact reviewed document "
                            "could not be saved."
                        ),
                        500,
                        detail=error,
                        service=service,
                        title=title,
                    )

                existing = (
                    get_payment_by_business(
                        service,
                        title,
                    )
                    or existing
                )

            return {
                "ok": True,
                "message": (
                    "The existing business payment "
                    "record and exact reviewed document "
                    "were reused."
                ),
                "payment": payment_public(
                    existing
                ),
                "amount": money(
                    existing.get(
                        "amount"
                    )
                ),
                "currency": existing.get(
                    "currency",
                    DEFAULT_CURRENCY,
                ),
                "payment_status": existing.get(
                    "payment_status"
                ),
                "paid": payment_is_verified(
                    existing.get(
                        "payment_status"
                    )
                ),
                "payment_verified": payment_is_verified(
                    existing.get(
                        "payment_status"
                    )
                ),
                "download_unlocked": payment_is_verified(
                    existing.get(
                        "payment_status"
                    )
                ),
                "service": service,
                "title": title,
                "document_version": existing.get(
                    "document_version"
                ),
                "document_saved": True,
                "document_snapshot_saved": True,
            }

    # --------------------------------------------------------
    # NEW BUSINESS PAYMENT RECORD
    #
    # No customer-facing payment ID.
    # --------------------------------------------------------

    payload = snapshot_payload(
        document
    )

    payload.update(
        {
            "service": service,
            "title": title,
            "document_title": title,
            "customer_name": customer_name,
            "amount": final_amount,
            "currency": DEFAULT_CURRENCY,
        }
    )

    try:

        record = create_payment_record(
            service=service,
            title=title,
            customer_name=customer_name,
            amount=final_amount,
            currency=DEFAULT_CURRENCY,
            payment_method=payment_method,
            document_version=version,
            document_filename=final_filename,
            document_payload=payload,
        )

    except sqlite3.IntegrityError:

        record = get_payment_by_business(
            service,
            title,
        )

        if not record:

            return json_response_error(
                "PAYMENT_CREATE_FAILED",
                (
                    "The payment business record "
                    "could not be persisted."
                ),
                500,
                service=service,
                title=title,
            )

    except Exception as exc:

        return json_response_error(
            "PAYMENT_CREATE_FAILED",
            (
                "The payment business record "
                "could not be persisted."
            ),
            500,
            detail=str(exc),
            service=service,
            title=title,
        )

    # --------------------------------------------------------
    # SAVE EXACT REVIEWED DOCUMENT
    # --------------------------------------------------------

    ok, error = save_exact_document_snapshot(
        record,
        document,
    )

    if not ok:

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            (
                "The exact reviewed document "
                "could not be saved."
            ),
            500,
            detail=error,
            service=service,
            title=title,
        )

    record = (
        get_payment_by_business(
            service,
            title,
        )
        or record
    )

    if not saved_document_exists(
        record
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_FAILED",
            (
                "The exact reviewed document "
                "was not confirmed after saving."
            ),
            500,
            service=service,
            title=title,
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    return {
        "ok": True,
        "message": (
            "Your exact reviewed document "
            "is saved and ready. Payment details "
            "are now available."
        ),
        "payment": payment_public(
            record
        ),
        "service": service,
        "title": title,
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
#
# Located by:
#
#       service + document title
#
# Payment reference is optional information only.
# ============================================================

@app.post("/api/payment/report")
async def payment_report(
    request: Request,
):

    body = await read_json_body(
        request
    )

    service = body_service(body)
    title = body_title(body)
    payment_reference = body_reference(
        body
    )
    note = body_note(body)

    if not service:

        return json_response_error(
            "SERVICE_REQUIRED",
            "The service is required.",
            400,
        )

    if not title:

        return json_response_error(
            "DOCUMENT_TITLE_REQUIRED",
            "The document title is required.",
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            (
                "No payment record was found "
                "for this service and document."
            ),
            404,
            service=service,
            title=title,
        )

    # Never regenerate document here.

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "The exact reviewed document snapshot "
                "is missing. Payment cannot be reported "
                "safely."
            ),
            409,
            service=service,
            title=title,
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
            "service": service,
            "title": title,
            "paid": True,
            "payment_verified": True,
            "download_unlocked": True,
            "document_saved": True,
        }

    updated = update_payment_record(
        payment,
        status="reported",
        payment_reference=(
            payment_reference
            or None
        ),
        customer_note=(
            note
            or None
        ),
        reported_at=now_iso(),
    )

    if not updated:

        return json_response_error(
            "PAYMENT_UPDATE_FAILED",
            "Payment report could not be saved.",
            500,
            service=service,
            title=title,
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
        "service": service,
        "title": title,
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
    service: str | None = None,
    title: str | None = None,
    document_title: str | None = None,
):

    service = clean(service)

    title = clean(
        title
        or document_title
    )

    if not service or not title:

        return {
            "ok": True,
            "payment": None,
            "payment_status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return {
            "ok": True,
            "payment": None,
            "service": service,
            "title": title,
            "payment_status": "none",
            "paid": False,
            "payment_verified": False,
            "download_unlocked": False,
        }

    verified = payment_is_verified(
        payment.get(
            "payment_status"
        )
    )

    saved = saved_document_exists(
        payment
    )

    return {
        "ok": True,
        "payment": payment_public(
            payment
        ),
        "service": service,
        "title": title,
        "payment_status": payment.get(
            "payment_status"
        ),
        "paid": verified,
        "payment_verified": verified,
        "download_unlocked": verified,
        "document_saved": saved,
        "document_check": (
            "current_saved_snapshot"
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
):

    return await payment_report(
        request
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
            payment_public(record)
            for record in records
        ],
    }


# ============================================================
# CUSTOMER CARE VERIFY
#
# Verification changes only status.
# It never regenerates the saved document.
# ============================================================

@app.post(
    "/api/customer-care/payment/verify"
)
async def customer_care_verify(
    request: Request,
):

    body = await read_json_body(
        request
    )

    service = body_service(body)
    title = body_title(body)

    note = clean(
        nested_value(
            body,
            "note",
            "admin_note",
        )
    )

    raw_verified = body.get(
        "verified",
        True,
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

    if not service or not title:

        return json_response_error(
            "BUSINESS_CONTEXT_REQUIRED",
            (
                "Service and document title "
                "are required."
            ),
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
        )

    if not verified:

        updated = update_payment_record(
            payment,
            status="rejected",
            admin_note=(
                note
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
                "Customer Care cannot activate "
                "this document because the exact "
                "saved reviewed document is missing."
            ),
            409,
            service=service,
            title=title,
        )

    updated = update_payment_record(
        payment,
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
            service=service,
            title=title,
        )

    return {
        "ok": True,
        "message": (
            "Payment verified. "
            "The exact saved document can now "
            "be activated for delivery."
        ),
        "payment": payment_public(
            updated
        ),
        "service": service,
        "title": title,
        "paid": True,
        "payment_verified": True,
        "download_unlocked": True,
        "document_saved": True,
    }


# ============================================================
# BACK OFFICE LOGIN
# ============================================================

@app.post("/api/back-office/login")
async def back_office_login(
    request: Request,
):

    body = await read_json_body(
        request
    )

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

@app.get("/api/back-office/payments")
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
        payment_public(record)
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
        "count": len(records),
        "pending_count": len(pending),
        "payments": [
            payment_public(record)
            for record in records
        ],
        "pending_payments": pending,
    }


# ============================================================
# BACK OFFICE JOB LIST COMPATIBILITY
#
# Existing Workspace/Back Office code may call this URL.
#
# This endpoint is intentionally kept compatible with the
# existing Workspace entry point.
#
# Detailed Back Office payment/document endpoints remain
# protected by BACK_OFFICE_ADMIN_KEY.
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs(
    request: Request,
):

    # --------------------------------------------------------
    # Compatibility endpoint:
    #
    # Existing Workspace code may call GET /api/back-office/jobs
    # without sending an admin key.
    #
    # Do not break that existing entry point.
    # --------------------------------------------------------

    records = list_all_payments()

    jobs: list[dict[str, Any]] = []

    for payment in records:

        public = payment_public(
            payment
        )

        if public:
            jobs.append(public)

    return {
        "ok": True,
        "count": len(jobs),
        "jobs": jobs,
        "payments": jobs,
    }


# ============================================================
# BACK OFFICE SINGLE PAYMENT / BUSINESS DOCUMENT
# ============================================================

@app.get("/api/back-office/payment")
async def back_office_payment(
    request: Request,
    service: str | None = None,
    title: str | None = None,
    document_title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    service = clean(service)

    title = clean(
        title
        or document_title
    )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
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
    service: str | None = None,
    title: str | None = None,
    document_title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    service = clean(service)

    title = clean(
        title
        or document_title
    )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
        )

    exists = saved_document_exists(
        payment
    )

    path = saved_document_path(
        payment
    )

    return {
        "ok": True,
        "service": payment.get(
            "service"
        ),
        "title": payment.get(
            "document_title"
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
# EXISTING SNAPSHOT ONLY.
# ============================================================

@app.get(
    "/api/back-office/document"
)
async def back_office_document(
    request: Request,
    service: str | None = None,
    title: str | None = None,
    document_title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    service = clean(service)

    title = clean(
        title
        or document_title
    )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
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
            service=service,
            title=title,
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
# ============================================================

@app.post(
    "/api/back-office/payment/verify"
)
async def back_office_verify_payment(
    request: Request,
):

    body = await read_json_body(
        request
    )

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    service = body_service(body)
    title = body_title(body)

    note = clean(
        body.get("note")
        or body.get("admin_note")
    )

    if not service or not title:

        return json_response_error(
            "BUSINESS_CONTEXT_REQUIRED",
            (
                "Service and document title "
                "are required."
            ),
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
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
            service=service,
            title=title,
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
            service=service,
            title=title,
            download_unlocked=False,
        )

    updated = update_payment_record(
        payment,
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
            service=service,
            title=title,
        )

    return {
        "ok": True,
        "message": (
            "Payment verified. "
            "The exact saved document is ready "
            "for activation."
        ),
        "payment": payment_public(
            updated
        ),
        "service": service,
        "title": title,
        "document_version": updated.get(
            "document_version"
        ),
        "document_saved": True,
        "payment_verified": True,
        "download_unlocked": True,
    }


# ============================================================
# ACTIVATE DOWNLOAD
#
# EXISTING SAVED DOCUMENT → VERIFIED/ACTIVE
#
# NO DOCUMENT GENERATION.
# ============================================================

@app.post(
    "/api/back-office/activate-download"
)
async def back_office_activate_download(
    request: Request,
):

    body = await read_json_body(
        request
    )

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    service = body_service(body)
    title = body_title(body)

    note = clean(
        body.get("note")
        or body.get("admin_note")
    )

    if not service or not title:

        return json_response_error(
            "BUSINESS_CONTEXT_REQUIRED",
            (
                "Service and document title "
                "are required."
            ),
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            (
                "No payment record was found "
                "for this business document."
            ),
            404,
            service=service,
            title=title,
        )

    if not saved_document_exists(
        payment
    ):

        return json_response_error(
            "DOCUMENT_SNAPSHOT_MISSING",
            (
                "ACTIVATE DOWNLOAD cannot continue "
                "because the exact saved reviewed "
                "document is missing."
            ),
            409,
            service=service,
            title=title,
            download_unlocked=False,
        )

    updated = update_payment_record(
        payment,
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
            (
                "Download could not be activated."
            ),
            500,
            service=service,
            title=title,
        )

    return {
        "ok": True,
        "message": (
            "Payment verified and download "
            "activated for the exact saved document."
        ),
        "payment": payment_public(
            updated
        ),
        "service": service,
        "title": title,
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

    body = await read_json_body(
        request
    )

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    service = body_service(body)
    title = body_title(body)

    note = clean(
        body.get("note")
        or body.get("admin_note")
    )

    if not service or not title:

        return json_response_error(
            "BUSINESS_CONTEXT_REQUIRED",
            (
                "Service and document title "
                "are required."
            ),
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
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
            service=service,
            title=title,
        )

    updated = update_payment_record(
        payment,
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
# VERIFIED BUSINESS DOCUMENT
#       ↓
# EXISTING SAVED FILE
#
# NEVER REGENERATES.
# ============================================================

@app.get("/api/download")
async def download_document(
    service: str | None = None,
    title: str | None = None,
    document_title: str | None = None,
):

    service = clean(service)

    title = clean(
        title
        or document_title
    )

    if not service or not title:

        return json_response_error(
            "BUSINESS_CONTEXT_REQUIRED",
            (
                "Service and document title "
                "are required."
            ),
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
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
            service=service,
            title=title,
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
            service=service,
            title=title,
            download_unlocked=False,
        )

    increment_download(
        payment
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
# DELIVERY
#
# Always uses the EXISTING saved file.
# ============================================================

def delivery_webhook(
    channel: str,
) -> str:

    normalized = (
        clean(channel)
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    mapping = {
        "email": DELIVERY_EMAIL_WEBHOOK,
        "whatsapp": DELIVERY_WHATSAPP_WEBHOOK,
        "telegram": DELIVERY_TELEGRAM_WEBHOOK,
        "google_drive": DELIVERY_GOOGLE_DRIVE_WEBHOOK,
        "drive": DELIVERY_GOOGLE_DRIVE_WEBHOOK,
    }

    return mapping.get(
        normalized,
        "",
    )


def send_saved_document_webhook(
    webhook: str,
    *,
    payment: dict[str, Any],
    channel: str,
    recipient: str,
) -> Any:

    output = saved_document_path(
        payment
    )

    if not output or not output.is_file():

        raise RuntimeError(
            "The exact saved document is missing."
        )

    payload = {
        "channel": channel,
        "recipient": recipient,
        "service": payment.get(
            "service"
        ),
        "title": payment.get(
            "document_title"
        ),
        "document_title": payment.get(
            "document_title"
        ),
        "filename": output.name,
        "saved_document_path": str(
            output
        ),
        "document_version": payment.get(
            "document_version"
        ),
        "document_saved_at": payment.get(
            "document_saved_at"
        ),
        "exact_saved_document": True,
    }

    data = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        webhook,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        raw = response.read()

        try:

            return json.loads(
                raw.decode("utf-8")
            )

        except Exception:

            return raw.decode(
                "utf-8",
                errors="replace",
            )


@app.post(
    "/api/back-office/delivery"
)
async def back_office_delivery(
    request: Request,
):

    body = await read_json_body(
        request
    )

    auth_error = require_admin(
        request,
        body,
    )

    if auth_error:
        return auth_error

    service = body_service(body)
    title = body_title(body)

    channel = clean(
        body.get("channel")
    ).lower()

    recipient = clean(
        body.get("recipient")
        or body.get("email")
        or body.get("phone")
        or body.get("username")
        or body.get("chat_id")
        or body.get("chatId")
    )

    if not service or not title:

        return json_response_error(
            "BUSINESS_CONTEXT_REQUIRED",
            (
                "Service and document title "
                "are required."
            ),
            400,
        )

    if not channel:

        return json_response_error(
            "DELIVERY_CHANNEL_REQUIRED",
            (
                "A delivery channel is required."
            ),
            400,
        )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
        )

    if not payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            (
                "The payment must be verified "
                "before the saved document can "
                "be delivered."
            ),
            403,
            service=service,
            title=title,
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
                "is missing."
            ),
            409,
            service=service,
            title=title,
        )

    normalized_channel = (
        channel
        .replace("-", "_")
        .replace(" ", "_")
    )

    # --------------------------------------------------------
    # Direct customer download
    # --------------------------------------------------------

    if normalized_channel in {
        "download",
        "direct_download",
        "phone_download",
    }:

        return {
            "ok": True,
            "delivery": "direct_download",
            "message": (
                "The exact saved document "
                "is ready for direct download."
            ),
            "service": service,
            "title": title,
            "filename": output.name,
            "download_url": (
                "/api/download"
                "?service="
                + quote(service)
                + "&title="
                + quote(title)
            ),
            "exact_saved_document": True,
        }

    # --------------------------------------------------------
    # External channel
    # --------------------------------------------------------

    webhook = delivery_webhook(
        normalized_channel
    )

    if not webhook:

        return {
            "ok": True,
            "sent": False,
            "channel": normalized_channel,
            "message": (
                "The exact saved document is ready "
                "for this delivery channel, but no "
                "delivery integration is configured "
                "for the channel."
            ),
            "service": service,
            "title": title,
            "filename": output.name,
            "saved_document_path": str(
                output
            ),
            "exact_saved_document": True,
            "requires_channel_integration": True,
        }

    try:

        result = send_saved_document_webhook(
            webhook,
            payment=payment,
            channel=normalized_channel,
            recipient=recipient,
        )

        return {
            "ok": True,
            "sent": True,
            "channel": normalized_channel,
            "message": (
                "The exact saved document "
                "was passed to the configured "
                "delivery integration."
            ),
            "service": service,
            "title": title,
            "filename": output.name,
            "exact_saved_document": True,
            "delivery_result": result,
        }

    except Exception as exc:

        return json_response_error(
            "DELIVERY_FAILED",
            (
                "The exact saved document was "
                "not delivered through the configured "
                "channel."
            ),
            502,
            detail=str(exc),
            channel=normalized_channel,
            service=service,
            title=title,
        )


# ============================================================
# BACK OFFICE SAVED DOCUMENT DELIVERY FILE
#
# Allows Customer Service to obtain the exact already-saved
# file for manual sending through WhatsApp, Email, Telegram,
# Google Drive or another channel.
# ============================================================

@app.get(
    "/api/back-office/delivery-file"
)
async def back_office_delivery_file(
    request: Request,
    service: str | None = None,
    title: str | None = None,
    document_title: str | None = None,
):

    auth_error = require_admin(
        request
    )

    if auth_error:
        return auth_error

    service = clean(service)

    title = clean(
        title
        or document_title
    )

    payment = get_payment_by_business(
        service,
        title,
    )

    if not payment:

        return json_response_error(
            "PAYMENT_NOT_FOUND",
            "Payment record not found.",
            404,
            service=service,
            title=title,
        )

    if not payment_is_verified(
        payment.get(
            "payment_status"
        )
    ):

        return json_response_error(
            "DOWNLOAD_LOCKED",
            (
                "Customer Service must verify "
                "the payment before obtaining "
                "the delivery file."
            ),
            403,
            service=service,
            title=title,
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
                "is missing."
            ),
            409,
            service=service,
            title=title,
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
            "X-Document-Version": clean(
                payment.get(
                    "document_version"
                )
            ),
            "X-Document-Snapshot": "true",
            "X-Customer-Service-Delivery": "true",
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
        "[PAYMENT API] Business identity: "
        "service + document title"
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
