"""
ada_api.py

Naija Pocket Business Center
Customer API / Workspace Backend

IMPORTANT
---------
The SQLite database is the authoritative source for:

    jobs.id
    work_records.id
    work_records.version
    payments.id
    payment_status
    download_activated

Runtime memory is used for active document/review state only.

The API MUST NOT create a UUID job identity that is expected to
behave like the database jobs.id.

Customer workflow:

    Customer request
          ↓
    database.create_job()
          ↓
    REAL numeric jobs.id
          ↓
    Intelligence document generation
          ↓
    Review
          ↓
    database.save_customer_work()
          ↓
    Saved work_records.id/version
          ↓
    Customer approval
          ↓
    Payment
          ↓
    Back Office payment confirmation
          ↓
    Back Office download activation
          ↓
    Customer download
"""


import asyncio
import inspect
import io
import os
import re
import traceback
import uuid
import zipfile

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


from fastapi import (
    FastAPI,
    File,
    Form,
    UploadFile,
)

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import (
    FileResponse,
    JSONResponse,
)

from pydantic import BaseModel


# ============================================================
# INTELLIGENCE
# ============================================================

from ada_response import (
    AdaResponse,
    get_ada_model,
    is_configured,
)


# ============================================================
# DATABASE
# ============================================================

from database import (
    get_customer,
    get_job,
    create_job as db_create_job,
    update_job,
    update_job_status,

    create_payment,
    get_payment,
    get_latest_payment,
    update_payment_status,

    save_customer_work,
    get_work,
    get_latest_work,
    get_work_for_job,
    get_activated_work,
    activate_work_download,

    get_back_office_jobs,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEBUG = os.getenv(
    "ADA_DEBUG_ERRORS",
    "true"
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}


MAX_UPLOAD = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024)
    )
)


REVIEW_CHUNK_CHARS = int(
    os.getenv(
        "ADA_REVIEW_CHUNK_CHARS",
        "7000"
    )
)


REVIEW_MIN_CHARS = int(
    os.getenv(
        "ADA_REVIEW_MIN_CHARS",
        "2500"
    )
)


BASE = Path(
    __file__
).resolve().parent


# ============================================================
# DOCUMENT STORAGE
# ============================================================

DOCUMENT_ROOT = (
    BASE
    / "data"
    / "documents"
)

DOCUMENT_ROOT.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# ADMIN
# ============================================================

ADMIN_KEY = os.getenv(
    "NPBC_ADMIN_KEY",
    "npbc_admin_2026"
)


# ============================================================
# RUNTIME STATE
# ============================================================

_sessions: dict[
    str,
    AdaResponse
] = {}


_jobs: dict[
    int,
    dict[str, Any]
] = {}


_review_tasks: dict[
    int,
    asyncio.Task
] = {}


_correction_tasks: dict[
    int,
    asyncio.Task
] = {}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center",
    version="intelligence-first-v10"
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

def find_file(
    *names
):

    locations = [
        BASE,
        BASE / "app",
        BASE / "static",
        BASE / "public",
        BASE / "assets",
    ]

    for location in locations:

        for name in names:

            path = location / name

            if path.exists():
                return path

    return None


def event_value(
    event
):

    if event is None:
        return ""

    return str(event).strip()


def job_key(
    job_id
):

    try:

        return int(
            str(job_id).strip()
        )

    except (
        TypeError,
        ValueError
    ):

        return None


def clean_text(
    value
):

    if value is None:
        return ""

    text = str(
        value
    ).strip()

    # Remove markdown code fences around generated documents.
    text = re.sub(
        r"^```(?:markdown|md|text)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s*```$",
        "",
        text
    )

    return text.strip()


def safe_int(
    value
):

    try:

        return int(
            str(value).strip()
        )

    except (
        TypeError,
        ValueError
    ):

        return None


def application_error(
    message,
    status_code=400
):

    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": message,
        }
    )


# ============================================================
# DATABASE JOB RESOLUTION
# ============================================================

def get_database_job(
    job_id
):

    numeric_id = safe_int(
        job_id
    )

    if numeric_id is None:
        return None

    return get_job(
        numeric_id
    )


def require_database_job(
    job_id
):

    numeric_id = safe_int(
        job_id
    )

    if numeric_id is None:
        return None, application_error(
            "Invalid database job ID.",
            400
        )

    job = get_job(
        numeric_id
    )

    if job is None:
        return None, application_error(
            "Database job not found.",
            404
        )

    return numeric_id, None


# ============================================================
# DOCUMENT STORAGE
# ============================================================

def save_document_to_storage(
    job_id
):

    """
    Save the reviewed document to VPS storage and create the
    corresponding database work_records row.

    The database jobs.id is authoritative.

    Returns:

        {
            "work_id": ...,
            "version": ...,
            "storage_reference": ...,
        }

    or None on failure.
    """

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:
        return None

    job = _jobs.get(
        numeric_job_id
    )

    if job is None:
        return None

    document_text = clean_text(
        job.get(
            "document_text"
        )
    )

    if not document_text:
        return None

    version = safe_int(
        job.get(
            "current_version",
            1
        )
    ) or 1

    job_dir = (
        DOCUMENT_ROOT
        / str(numeric_job_id)
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    storage_path = (
        job_dir
        / f"v{version}.txt"
    )

    # --------------------------------------------------------
    # Do not create duplicate work records for the same
    # runtime version when the saved file already exists.
    # --------------------------------------------------------

    existing_work = get_work_for_job(
        numeric_job_id
    )

    for work in existing_work:

        try:

            work_version = int(
                work["version"]
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        if (
            work_version == version
            and work["storage_reference"]
            and Path(
                work["storage_reference"]
            ).resolve() == storage_path.resolve()
        ):

            job["work_id"] = int(
                work["id"]
            )

            job["work_version"] = work_version

            job["storage_reference"] = str(
                storage_path
            )

            return {
                "work_id": int(
                    work["id"]
                ),
                "version": work_version,
                "storage_reference": str(
                    storage_path
                ),
            }

    try:

        storage_path.write_text(
            document_text,
            encoding="utf-8"
        )

    except Exception as error:

        if DEBUG:

            print(
                "Document storage error:",
                error
            )

            traceback.print_exc()

        return None

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # database.save_customer_work() automatically determines
    # the next database version.
    #
    # Therefore the API does NOT force its runtime version
    # into SQLite.
    # --------------------------------------------------------

    work_id = save_customer_work(
        job_id=numeric_job_id,
        work_title=job.get(
            "work_title"
        ) or job.get(
            "service"
        ) or "Customer Work",
        work_type="document",
        storage_type="local",
        storage_reference=str(
            storage_path
        ),
        work_status="completed",
        notes=(
            "Reviewed customer document "
            "saved for approval/download."
        ),
    )

    if work_id is None:
        return None

    saved_work = get_work(
        work_id
    )

    if saved_work is None:
        return None

    database_version = safe_int(
        saved_work["version"]
    ) or 1

    job["work_id"] = int(
        work_id
    )

    job["work_version"] = database_version

    job["current_version"] = database_version

    job["version_id"] = (
        f"{numeric_job_id}:{database_version}"
    )

    job["storage_reference"] = str(
        storage_path
    )

    return {
        "work_id": int(
            work_id
        ),
        "version": database_version,
        "storage_reference": str(
            storage_path
        ),
    }


# ============================================================
# PERSISTENT RECOVERY
# ============================================================

def recover_saved_job_for_approval(
    supplied_job_id,
    supplied_version_id
):

    """
    Recover a reviewed document from the database/storage after
    runtime memory has been lost.

    Database job ID and work_records version are authoritative.
    """

    numeric_job_id = safe_int(
        supplied_job_id
    )

    if numeric_job_id is None:
        return None

    database_job = get_job(
        numeric_job_id
    )

    if database_job is None:
        return None

    requested_version = None

    if supplied_version_id:

        version_text = str(
            supplied_version_id
        ).strip()

        match = re.match(
            r"^(\d+):(\d+)$",
            version_text
        )

        if match:

            requested_job = int(
                match.group(1)
            )

            requested_version = int(
                match.group(2)
            )

            if requested_job != numeric_job_id:
                return None

    work = get_latest_work(
        numeric_job_id
    )

    if work is None:
        return None

    work_version = safe_int(
        work["version"]
    ) or 1

    if (
        requested_version is not None
        and work_version != requested_version
    ):
        return None

    storage_reference = (
        work["storage_reference"]
    )

    if not storage_reference:
        return None

    try:

        storage_path = Path(
            storage_reference
        ).resolve()

        document_root = DOCUMENT_ROOT.resolve()

        if (
            storage_path != document_root
            and document_root
            not in storage_path.parents
        ):
            return None

        if not storage_path.is_file():
            return None

        document_text = clean_text(
            storage_path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as error:

        if DEBUG:

            print(
                "Recovery error:",
                error
            )

        return None

    if not document_text:
        return None

    pages = text_to_review_pages(
        document_text
    )

    job = {
        "job_id": numeric_job_id,

        "customer_id":
            database_job["customer_id"],

        "customer_name":
            database_job["customer_name"],

        "phone":
            database_job["phone"],

        "service":
            database_job["service_type"],

        "description":
            database_job["description"],

        "customer_request":
            database_job["customer_request"],

        "amount":
            database_job["amount"],

        "currency":
            database_job["currency"],

        "work_title":
            work["work_title"],

        "document_text":
            document_text,

        "document_pages":
            pages,

        "review_pages":
            make_review_pages(
                pages
            ),

        "assembled_review":
            document_text,

        "status":
            "review_complete",

        "review_complete":
            True,

        "review_finished":
            True,

        "review_progress":
            100,

        "current_version":
            work_version,

        "version_id":
            f"{numeric_job_id}:{work_version}",

        "approved":
            False,

        "paid":
            False,

        "work_id":
            int(work["id"]),

        "work_version":
            work_version,

        "storage_reference":
            str(storage_path),

        "recovered":
            True,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "updated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    _jobs[
        numeric_job_id
    ] = job

    return job


# ============================================================
# PAGE NORMALIZATION
# ============================================================

def normalize_document_pages(
    pages
):

    if not pages:
        return []

    result = []

    if isinstance(
        pages,
        str
    ):

        text = clean_text(
            pages
        )

        if text:
            result.append(
                text
            )

        return result

    if not isinstance(
        pages,
        (list, tuple)
    ):
        return []

    for page in pages:

        if isinstance(
            page,
            str
        ):

            text = clean_text(
                page
            )

        elif isinstance(
            page,
            dict
        ):

            text = clean_text(
                page.get(
                    "content"
                )
                or page.get(
                    "text"
                )
                or page.get(
                    "body"
                )
                or ""
            )

        else:

            text = clean_text(
                getattr(
                    page,
                    "content",
                    None
                )
                or getattr(
                    page,
                    "text",
                    None
                )
                or ""
            )

        if text:
            result.append(
                text
            )

    return result


def text_to_review_pages(
    text
):

    """
    Review display chunking only.

    This does not impose a customer document page count.
    The complete document text remains authoritative.
    """

    text = clean_text(
        text
    )

    if not text:
        return []

    paragraphs = re.split(
        r"\n\s*\n",
        text
    )

    chunks = []
    current = ""

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        candidate = (
            paragraph
            if not current
            else
            current
            + "\n\n"
            + paragraph
        )

        if (
            len(candidate)
            <= REVIEW_CHUNK_CHARS
        ):

            current = candidate
            continue

        if current:

            chunks.append(
                current.strip()
            )

        # ----------------------------------------------------
        # Long paragraph / sentence fallback
        # ----------------------------------------------------

        if len(paragraph) <= REVIEW_CHUNK_CHARS:

            current = paragraph

        else:

            sentences = re.split(
                r"(?<=[.!?])\s+",
                paragraph
            )

            sentence_chunk = ""

            for sentence in sentences:

                sentence = sentence.strip()

                if not sentence:
                    continue

                candidate_sentence = (
                    sentence
                    if not sentence_chunk
                    else sentence_chunk
                    + " "
                    + sentence
                )

                if (
                    len(candidate_sentence)
                    <= REVIEW_CHUNK_CHARS
                ):

                    sentence_chunk = (
                        candidate_sentence
                    )

                else:

                    if sentence_chunk:

                        chunks.append(
                            sentence_chunk.strip()
                        )

                    sentence_chunk = sentence

            current = sentence_chunk

    if current:

        chunks.append(
            current.strip()
        )

    # --------------------------------------------------------
    # Avoid an unnecessarily tiny final review chunk.
    # --------------------------------------------------------

    if (
        len(chunks) > 1
        and len(chunks[-1]) < REVIEW_MIN_CHARS
    ):

        previous = chunks[-2]
        last = chunks[-1]

        combined = (
            previous
            + "\n\n"
            + last
        )

        if (
            len(combined)
            <= REVIEW_CHUNK_CHARS * 2
        ):

            chunks[-2] = combined
            chunks.pop()

    return [
        chunk
        for chunk in chunks
        if clean_text(chunk)
    ]


def normalize_pages_for_review(
    pages,
    document_text=None
):

    text = clean_text(
        document_text
    )

    if text:

        return text_to_review_pages(
            text
        )

    normalized = normalize_document_pages(
        pages
    )

    if not normalized:
        return []

    return text_to_review_pages(
        "\n\n".join(
            normalized
        )
    )


def normalize_pages(
    pages
):

    return normalize_document_pages(
        pages
    )


# ============================================================
# SESSION MANAGEMENT
# ============================================================

def get_session(
    customer_id,
    job_id,
    service
):

    customer_part = str(
        customer_id
        or "customer"
    )

    job_part = str(
        job_id
        or "job"
    )

    key = (
        f"{customer_part}:"
        f"{job_part}"
    )

    session = _sessions.get(
        key
    )

    if session is None:

        session = AdaResponse(
            service=service
        )

        _sessions[
            key
        ] = session

    else:

        setter = getattr(
            session,
            "set_service",
            None
        )

        if callable(setter):

            try:
                setter(service)

            except Exception:
                pass

    return session


# ============================================================
# REQUEST MODELS
# ============================================================

class Chat(BaseModel):

    message: str = ""

    service: str = ""

    event: str = ""

    customer_id: str = ""

    job_id: str = ""

    client_request_id: str = ""

    activate_intelligence: bool = True

    context: Any = None

    form_data: Any = None

    guidance_only: bool = False

    create_work: bool = False

    document_pages: Any = None

    document_text: str = ""


class Correction(BaseModel):

    job_id: str

    instruction: str


class Approval(BaseModel):

    job_id: str

    version_id: str = ""


class PaymentCreate(BaseModel):

    job_id: str

    customer_id: str = ""

    order_number: str = ""

    service: str = ""

    amount: float = 0

    payment_method: str = ""


class PaymentConfirm(BaseModel):

    payment_id: int

    admin_key: str


class DownloadActivation(BaseModel):

    work_id: int

    admin_key: str


# ============================================================
# CUSTOMER REQUEST
# ============================================================

def build_customer_request(
    request: Chat
):

    parts = []

    if request.service:

        parts.append(
            f"Selected service: {request.service}"
        )

    if request.form_data:

        parts.append(
            "Customer form information:\n"
            + str(
                request.form_data
            )
        )

    if request.context:

        parts.append(
            "Additional context:\n"
            + str(
                request.context
            )
        )

    if request.message:

        parts.append(
            "Customer request:\n"
            + request.message
        )

    return "\n\n".join(
        parts
    ).strip()


def build_context(
    request: Chat
):

    context = {}

    if request.context is not None:
        context["context"] = request.context

    if request.customer_id:
        context["customer_id"] = (
            request.customer_id
        )

    if request.client_request_id:
        context["client_request_id"] = (
            request.client_request_id
        )

    return context


# ============================================================
# INTELLIGENCE EXTRACTION
# ============================================================

_TEXT_KEYS = [
    "document_text",
    "prepared_work",
    "generated_document",
    "generated",
    "output",
    "document",
    "content",
    "text",
    "reply",
    "response",
    "message",
    "result",
    "answer",
]


_PAGE_KEYS = [
    "pages",
    "document_pages",
    "prepared_pages",
    "content_pages",
]


def _extract_from_value(
    value
):

    if value is None:
        return "", []

    if isinstance(
        value,
        str
    ):

        text = clean_text(
            value
        )

        return text, []

    if isinstance(
        value,
        (list, tuple)
    ):

        all_text = []
        all_pages = []

        for item in value:

            text, pages = (
                _extract_from_value(
                    item
                )
            )

            if text:
                all_text.append(
                    text
                )

            if pages:
                all_pages.extend(
                    pages
                )

        return (
            "\n\n".join(
                all_text
            ).strip(),
            all_pages
        )

    if isinstance(
        value,
        dict
    ):

        # ----------------------------------------------------
        # Document-specific text first.
        # ----------------------------------------------------

        for key in _TEXT_KEYS:

            if key not in value:
                continue

            candidate = value.get(
                key
            )

            if isinstance(
                candidate,
                str
            ):

                text = clean_text(
                    candidate
                )

                if text:
                    return text, []

        # ----------------------------------------------------
        # Explicit page structures next.
        # ----------------------------------------------------

        pages = []

        for key in _PAGE_KEYS:

            if key not in value:
                continue

            normalized = normalize_document_pages(
                value.get(
                    key
                )
            )

            if normalized:

                pages.extend(
                    normalized
                )

        if pages:

            return (
                "\n\n".join(
                    pages
                ),
                pages
            )

        # ----------------------------------------------------
        # Recursive fallback.
        # ----------------------------------------------------

        for candidate in value.values():

            text, nested_pages = (
                _extract_from_value(
                    candidate
                )
            )

            if text or nested_pages:

                return (
                    text,
                    nested_pages
                )

        return "", []

    # --------------------------------------------------------
    # Object response
    # --------------------------------------------------------

    for key in _TEXT_KEYS:

        if hasattr(
            value,
            key
        ):

            candidate = getattr(
                value,
                key
            )

            if isinstance(
                candidate,
                str
            ):

                text = clean_text(
                    candidate
                )

                if text:
                    return text, []

    for key in _PAGE_KEYS:

        if hasattr(
            value,
            key
        ):

            normalized = normalize_document_pages(
                getattr(
                    value,
                    key
                )
            )

            if normalized:

                return (
                    "\n\n".join(
                        normalized
                    ),
                    normalized
                )

    return "", []


def extract_complete_document(
    value
):

    text, pages = (
        _extract_from_value(
            value
        )
    )

    text = clean_text(
        text
    )

    if not text:

        if pages:

            text = clean_text(
                "\n\n".join(
                    pages
                )
            )

    if not text:
        return None

    reconstructed_pages = (
        text_to_review_pages(
            text
        )
    )

    return {
        "document_text":
            text,

        "document_pages":
            reconstructed_pages,

        "metadata":
            {},
    }


# ============================================================
# FLEXIBLE INTELLIGENCE METHOD CALL
# ============================================================

async def _call_method_flexibly(
    method,
    **kwargs
):

    signature = inspect.signature(
        method
    )

    parameters = signature.parameters

    accepts_kwargs = any(
        parameter.kind
        == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    if accepts_kwargs:

        call_kwargs = kwargs

    else:

        call_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key in parameters
        }

    result = method(
        **call_kwargs
    )

    if inspect.isawaitable(
        result
    ):

        result = await result

    return result


# ============================================================
# DOCUMENT GENERATION
# ============================================================

async def create_document_with_intelligence(
    ada,
    customer_request,
    service,
    form_data,
    context,
    event,
    message
):

    common_kwargs = {

        "customer_request":
            customer_request,

        "service":
            service,

        "form_data":
            form_data,

        "context":
            context,

        "event":
            event,

        "message":
            message,

        "original_request":
            customer_request,

        "create_work":
            True,
    }

    methods = [
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    ]

    for method_name in methods:

        method = getattr(
            ada,
            method_name,
            None
        )

        if not callable(method):
            continue

        try:

            result = (
                await _call_method_flexibly(
                    method,
                    **common_kwargs
                )
            )

            extracted = (
                extract_complete_document(
                    result
                )
            )

            if extracted:
                return extracted

        except Exception as error:

            if DEBUG:

                print(
                    f"{method_name} failed:",
                    error
                )

    # --------------------------------------------------------
    # Existing fallback response path.
    # --------------------------------------------------------

    respond = getattr(
        ada,
        "respond",
        None
    )

    if callable(respond):

        result = (
            await _call_method_flexibly(
                respond,
                message=message,
                service=service,
                event=event,
                context=context,
                customer_request=customer_request,
            )
        )

        extracted = (
            extract_complete_document(
                result
            )
        )

        if extracted:
            return extracted

    return None


# ============================================================
# REVIEW HELPERS
# ============================================================

def make_review_pages(
    pages
):

    result = []

    for index, content in enumerate(
        normalize_document_pages(
            pages
        ),
        start=1
    ):

        result.append(
            {
                "page":
                    index,

                "status":
                    "queued",

                "content":
                    content,

                "error":
                    None,
            }
        )

    return result


def synchronize_job_document(
    job
):

    document_text = clean_text(
        job.get(
            "document_text"
        )
    )

    if document_text:

        pages = text_to_review_pages(
            document_text
        )

    else:

        pages = normalize_pages(
            job.get(
                "document_pages"
            )
        )

        document_text = clean_text(
            "\n\n".join(
                pages
            )
        )

    job["document_text"] = (
        document_text
    )

    job["document_pages"] = (
        pages
    )

    job["review_pages"] = (
        make_review_pages(
            pages
        )
    )

    return job


# ============================================================
# DATABASE JOB + RUNTIME JOB CREATION
# ============================================================

def create_job(
    document_text,
    document_pages,
    service,
    customer_request,
    request: Chat
):

    """
    Creates BOTH:

        1. the persistent database jobs row
        2. the runtime document/review job

    The database integer ID becomes the runtime job ID.
    """

    # --------------------------------------------------------
    # Existing numeric job supplied by the client?
    # --------------------------------------------------------

    supplied_job_id = safe_int(
        request.job_id
    )

    database_job = None

    if supplied_job_id is not None:

        database_job = get_job(
            supplied_job_id
        )

    # --------------------------------------------------------
    # Create a real database job when one does not exist.
    # --------------------------------------------------------

    if database_job is None:

        customer_numeric_id = safe_int(
            request.customer_id
        )

        customer = None

        if customer_numeric_id is not None:

            customer = get_customer(
                customer_numeric_id
            )

        customer_name = (
            customer["customer_name"]
            if customer
            else "Customer"
        )

        phone = (
            customer["phone"]
            if customer
            else None
        )

        amount = 0

        job_id = db_create_job(
            customer_name=customer_name,
            service_type=service
            or "Business Center Service",
            phone=phone,
            description=customer_request,
            amount=amount,
            status="reviewing",
            customer_id=customer_numeric_id,
            customer_request=customer_request,
            currency="NGN",
            work_reference=None,
        )

        if job_id is None:
            return None

        supplied_job_id = int(
            job_id
        )

        database_job = get_job(
            supplied_job_id
        )

    else:

        job_id = supplied_job_id

        update_job(
            job_id,
            service_type=service
            if service
            else None,
            description=customer_request
            if customer_request
            else None,
            customer_request=customer_request
            if customer_request
            else None,
            status="reviewing",
        )

        database_job = get_job(
            job_id
        )

    if database_job is None:
        return None

    numeric_job_id = int(
        database_job["id"]
    )

    text = clean_text(
        document_text
    )

    pages = normalize_pages_for_review(
        document_pages,
        text
    )

    if not text and pages:

        text = clean_text(
            "\n\n".join(
                pages
            )
        )

    if not text:
        return None

    job = {

        "job_id":
            numeric_job_id,

        "customer_id":
            database_job["customer_id"],

        "customer_name":
            database_job["customer_name"],

        "phone":
            database_job["phone"],

        "service":
            database_job["service_type"],

        "description":
            database_job["description"],

        "customer_request":
            database_job["customer_request"],

        "amount":
            database_job["amount"],

        "currency":
            database_job["currency"],

        "work_title":
            service
            or database_job["service_type"],

        "document_text":
            text,

        "document_pages":
            pages,

        "review_pages":
            make_review_pages(
                pages
            ),

        "assembled_review":
            text,

        "status":
            "reviewing",

        "review_complete":
            False,

        "review_finished":
            False,

        "review_progress":
            0,

        "current_version":
            1,

        "version_id":
            f"{numeric_job_id}:1",

        "approved":
            False,

        "paid":
            False,

        "work_id":
            None,

        "work_version":
            None,

        "storage_reference":
            None,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "updated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    _jobs[
        numeric_job_id
    ] = job

    return job


# ============================================================
# REVIEW CALLBACK
# ============================================================

def review_callback(
    job_id,
    event
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    event_type = ""

    if isinstance(
        event,
        dict
    ):

        event_type = str(
            event.get(
                "event"
            )
            or event.get(
                "type"
            )
            or ""
        ).strip()

    if event_type == "page_started":

        page_number = safe_int(
            event.get(
                "page"
            )
        )

        if page_number:

            index = (
                page_number - 1
            )

            if (
                0
                <= index
                < len(
                    job["review_pages"]
                )
            ):

                job["review_pages"][index][
                    "status"
                ] = "reviewing"

    elif event_type == "page_completed":

        page_number = safe_int(
            event.get(
                "page"
            )
        )

        if page_number:

            index = (
                page_number - 1
            )

            if (
                0
                <= index
                < len(
                    job["review_pages"]
                )
            ):

                page = job[
                    "review_pages"
                ][index]

                page[
                    "status"
                ] = "reviewed"

                callback_content = clean_text(
                    event.get(
                        "content"
                    )
                    or ""
                )

                if callback_content:

                    page[
                        "content"
                    ] = callback_content

                job[
                    "review_progress"
                ] = int(
                    (
                        page_number
                        / max(
                            1,
                            len(
                                job[
                                    "review_pages"
                                ]
                            )
                        )
                    )
                    * 100
                )

    elif event_type == "page_error":

        page_number = safe_int(
            event.get(
                "page"
            )
        )

        if page_number:

            index = (
                page_number - 1
            )

            if (
                0
                <= index
                < len(
                    job["review_pages"]
                )
            ):

                page = job[
                    "review_pages"
                ][index]

                page[
                    "status"
                ] = "error"

                page[
                    "error"
                ] = str(
                    event.get(
                        "error"
                    )
                    or "Review error."
                )

    elif event_type == "review_completed":

        job[
            "status"
        ] = "review_complete"

        job[
            "review_complete"
        ] = True

        job[
            "review_finished"
        ] = True

        job[
            "review_progress"
        ] = 100

        job[
            "assembled_review"
        ] = clean_text(
            "\n\n".join(
                page.get(
                    "content",
                    ""
                )
                for page in job[
                    "review_pages"
                ]
            )
        )


# ============================================================
# REVIEW EXECUTION
# ============================================================

async def run_review(
    job_id
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    try:

        pages = normalize_pages(
            job.get(
                "document_pages"
            )
        )

        if not pages:

            raise ValueError(
                "No document pages available for review."
            )

        job[
            "status"
        ] = "reviewing"

        ada = get_session(
            job.get(
                "customer_id"
            ),
            job_id,
            job.get(
                "service"
            )
        )

        customer_request = (
            job.get(
                "customer_request"
            )
            or job.get(
                "description"
            )
            or job.get(
                "document_text"
            )
        )

        context = {
            "job_id":
                job_id,

            "customer_id":
                job.get(
                    "customer_id"
                ),

            "service":
                job.get(
                    "service"
                ),
        }

        async def progress_callback(
            event
        ):

            if isinstance(
                event,
                dict
            ):

                review_callback(
                    job_id,
                    event
                )

        review_method = getattr(
            ada,
            "review_document_pages",
            None
        )

        if not callable(
            review_method
        ):

            raise RuntimeError(
                "Document review method is unavailable."
            )

        result = await _call_method_flexibly(
            review_method,

            pages=pages,

            service=job.get(
                "service"
            ),

            context=context,

            customer_request=customer_request,

            event="send_for_review",

            progress_callback=progress_callback,
        )

        # ----------------------------------------------------
        # If intelligence returned reviewed pages/document,
        # preserve the complete returned document.
        # ----------------------------------------------------

        extracted = extract_complete_document(
            result
        )

        if extracted:

            returned_text = clean_text(
                extracted[
                    "document_text"
                ]
            )

            returned_pages = (
                extracted[
                    "document_pages"
                ]
            )

            if returned_text:

                job[
                    "document_text"
                ] = returned_text

                job[
                    "document_pages"
                ] = returned_pages

                job[
                    "review_pages"
                ] = make_review_pages(
                    returned_pages
                )

        # ----------------------------------------------------
        # If callback-created review pages exist, make sure
        # their complete content is preserved.
        # ----------------------------------------------------

        if job.get(
            "review_pages"
        ):

            assembled = clean_text(
                "\n\n".join(
                    page.get(
                        "content",
                        ""
                    )
                    for page in job[
                        "review_pages"
                    ]
                    if page.get(
                        "content"
                    )
                )
            )

            if assembled:

                job[
                    "assembled_review"
                ] = assembled

                # Only use assembled review as document text when
                # it contains a complete non-empty document.
                if len(
                    assembled
                ) >= REVIEW_MIN_CHARS:

                    job[
                        "document_text"
                    ] = assembled

                    job[
                        "document_pages"
                    ] = text_to_review_pages(
                        assembled
                    )

        job[
            "status"
        ] = "review_complete"

        job[
            "review_complete"
        ] = True

        job[
            "review_finished"
        ] = True

        job[
            "review_progress"
        ] = 100

        # ----------------------------------------------------
        # Persist ONLY after review is complete.
        # ----------------------------------------------------

        saved = save_document_to_storage(
            job_id
        )

        if saved:

            job[
                "work_id"
            ] = saved[
                "work_id"
            ]

            job[
                "work_version"
            ] = saved[
                "version"
            ]

            job[
                "current_version"
            ] = saved[
                "version"
            ]

            job[
                "version_id"
            ] = (
                f"{job_id}:"
                f"{saved['version']}"
            )

            job[
                "storage_reference"
            ] = saved[
                "storage_reference"
            ]

            # Database job remains synchronized.
            update_job_status(
                job_id,
                "review_complete"
            )

        else:

            job[
                "status"
            ] = "review_storage_error"

            update_job_status(
                job_id,
                "review_storage_error"
            )

    except asyncio.CancelledError:

        raise

    except Exception as error:

        job[
            "status"
        ] = "review_error"

        job[
            "review_complete"
        ] = False

        job[
            "review_finished"
        ] = False

        job[
            "review_error"
        ] = str(
            error
        )

        try:

            update_job_status(
                job_id,
                "review_error"
            )

        except Exception:
            pass

        if DEBUG:

            print(
                "Review error:",
                error
            )

            traceback.print_exc()


async def start_review(
    job_id
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return False

    if not job.get(
        "document_pages"
    ):

        return False

    if job.get(
        "status"
    ) != "reviewing":

        return False

    current = _review_tasks.get(
        job_id
    )

    if (
        current is not None
        and not current.done()
    ):

        return True

    task = asyncio.create_task(
        run_review(
            job_id
        )
    )

    _review_tasks[
        job_id
    ] = task

    return True


# ============================================================
# JOB RESPONSE
# ============================================================

def make_job_response(
    job
):

    return {
        "ok": True,

        "job_id":
            job.get(
                "job_id"
            ),

        "customer_id":
            job.get(
                "customer_id"
            ),

        "service":
            job.get(
                "service"
            ),

        "status":
            job.get(
                "status"
            ),

        "review_complete":
            job.get(
                "review_complete",
                False
            ),

        "review_finished":
            job.get(
                "review_finished",
                False
            ),

        "review_progress":
            job.get(
                "review_progress",
                0
            ),

        "document_pages":
            job.get(
                "document_pages",
                []
            ),

        "review_pages":
            job.get(
                "review_pages",
                []
            ),

        "document_text":
            job.get(
                "document_text",
                ""
            ),

        "assembled_review":
            job.get(
                "assembled_review",
                ""
            ),

        "current_version":
            job.get(
                "current_version",
                1
            ),

        "version_id":
            job.get(
                "version_id"
            ),

        "work_id":
            job.get(
                "work_id"
            ),

        "work_version":
            job.get(
                "work_version"
            ),

        "approved":
            job.get(
                "approved",
                False
            ),

        "paid":
            job.get(
                "paid",
                False
            ),

        "review_url":
            (
                "/review.html"
                f"?job_id="
                f"{job.get('job_id')}"
                f"&version_id="
                f"{job.get('version_id')}"
            ),
    }


# ============================================================
# UPLOAD EXTRACTION
# ============================================================

def extract_document(
    filename,
    data
):

    suffix = (
        Path(
            filename
        ).suffix.lower()
    )

    if suffix in {
        ".txt",
        ".csv",
        ".md",
    }:

        return clean_text(
            data.decode(
                "utf-8",
                errors="replace"
            )
        )

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    if suffix == ".pdf":

        try:

            from pypdf import PdfReader

            reader = PdfReader(
                io.BytesIO(data)
            )

            parts = []

            for page in reader.pages:

                try:

                    text = page.extract_text()

                except Exception:
                    text = ""

                if text:
                    parts.append(
                        text
                    )

            return clean_text(
                "\n\n".join(
                    parts
                )
            )

        except Exception as error:

            if DEBUG:
                print(
                    "PDF extraction error:",
                    error
                )

            return ""

    # --------------------------------------------------------
    # Office documents
    # --------------------------------------------------------

    if suffix in {
        ".docx",
        ".pptx",
        ".xlsx",
    }:

        try:

            with zipfile.ZipFile(
                io.BytesIO(data)
            ) as archive:

                names = archive.namelist()

                text_parts = []

                if suffix == ".docx":

                    targets = [
                        name
                        for name in names
                        if name.startswith(
                            "word/"
                        )
                        and name.endswith(
                            ".xml"
                        )
                    ]

                elif suffix == ".pptx":

                    targets = [
                        name
                        for name in names
                        if name.startswith(
                            "ppt/slides/"
                        )
                        and name.endswith(
                            ".xml"
                        )
                    ]

                else:

                    targets = [
                        name
                        for name in names
                        if name.startswith(
                            "xl/worksheets/"
                        )
                        and name.endswith(
                            ".xml"
                        )
                    ]

                for target in targets:

                    try:

                        root = ET.fromstring(
                            archive.read(
                                target
                            )
                        )

                        values = []

                        for element in root.iter():

                            if element.text:

                                value = (
                                    element.text.strip()
                                )

                                if value:
                                    values.append(
                                        value
                                    )

                        if values:

                            text_parts.append(
                                " ".join(
                                    values
                                )
                            )

                    except Exception:
                        continue

                return clean_text(
                    "\n\n".join(
                        text_parts
                    )
                )

        except Exception as error:

            if DEBUG:
                print(
                    "Office extraction error:",
                    error
                )

            return ""

    return ""


def uploaded_document_pages(
    filename,
    data
):

    text = extract_document(
        filename,
        data
    )

    if not text:
        return "", []

    pages = text_to_review_pages(
        text
    )

    return text, pages


# ============================================================
# ROUTES — HTML
# ============================================================

@app.get("/")
async def root():

    path = find_file(
        "index.html"
    )

    if path:

        return FileResponse(
            path
        )

    return JSONResponse(
        {
            "ok": True,
            "service":
                "Naija Pocket Business Center",
        }
    )


@app.get("/index.html")
async def index_page():

    path = find_file(
        "index.html"
    )

    if path:

        return FileResponse(
            path
        )

    return application_error(
        "index.html not found.",
        404
    )


@app.get("/conversation.html")
async def conversation_page():

    path = find_file(
        "conversation.html"
    )

    if path:

        return FileResponse(
            path
        )

    return application_error(
        "conversation.html not found.",
        404
    )


@app.get("/workspace.html")
async def workspace_page():

    path = find_file(
        "workspace.html"
    )

    if path:

        return FileResponse(
            path
        )

    return application_error(
        "workspace.html not found.",
        404
    )


@app.get("/review.html")
async def review_page():

    path = find_file(
        "review.html"
    )

    if path:

        return FileResponse(
            path
        )

    return application_error(
        "review.html not found.",
        404
    )


@app.get("/payment.html")
async def payment_page():

    path = find_file(
        "payment.html"
    )

    if path:

        return FileResponse(
            path
        )

    return application_error(
        "payment.html not found.",
        404
    )


@app.get("/download.html")
async def download_page():

    path = find_file(
        "download.html"
    )

    if path:

        return FileResponse(
            path
        )

    return application_error(
        "download.html not found.",
        404
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    configured = False

    try:

        configured = bool(
            is_configured()
        )

    except Exception:
        configured = False

    model = None

    try:

        model = get_ada_model()

    except Exception:
        model = None

    return {
        "ok": True,

        "service":
            "Naija Pocket Business Center",

        "intelligence_configured":
            configured,

        "intelligence_model":
            model,

        "architecture":
            "intelligence-first",

        "database_authoritative_job_ids":
            True,

        "complete_document_preservation":
            True,

        "customer_page_count_assumption":
            False,
    }


@app.get("/api/status")
async def status():

    return await health()


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat
):

    if not request.activate_intelligence:

        return application_error(
            "Intelligence activation is required.",
            400
        )

    try:

        configured = bool(
            is_configured()
        )

    except Exception:

        configured = False

    if not configured:

        return application_error(
            "Intelligence service is not configured.",
            503
        )

    # --------------------------------------------------------
    # Existing database job?
    # --------------------------------------------------------

    numeric_job_id = safe_int(
        request.job_id
    )

    customer_request = (
        build_customer_request(
            request
        )
    )

    context = build_context(
        request
    )

    # --------------------------------------------------------
    # If the supplied job ID is numeric, make sure it is a
    # genuine database job.
    # --------------------------------------------------------

    if numeric_job_id is not None:

        database_job = get_job(
            numeric_job_id
        )

        if database_job is None:

            return application_error(
                "Database job not found.",
                404
            )

    # --------------------------------------------------------
    # Existing runtime job or database job
    # --------------------------------------------------------

    job = (
        _jobs.get(
            numeric_job_id
        )
        if numeric_job_id is not None
        else None
    )

    ada = get_session(
        request.customer_id,
        numeric_job_id
        or request.job_id,
        request.service
    )

    # --------------------------------------------------------
    # Guidance only:
    #
    # This remains a normal single intelligence request.
    # It does NOT create a database job.
    # --------------------------------------------------------

    if request.guidance_only:

        respond = getattr(
            ada,
            "respond",
            None
        )

        if not callable(
            respond
        ):

            return application_error(
                "Intelligence response method is unavailable.",
                503
            )

        try:

            result = await _call_method_flexibly(
                respond,
                message=request.message,
                service=request.service,
                event=request.event,
                context=context,
                customer_request=customer_request,
            )

            text, _ = (
                _extract_from_value(
                    result
                )
            )

            return {
                "ok": True,
                "reply": text,
                "guidance_only": True,
            }

        except Exception as error:

            if DEBUG:
                traceback.print_exc()

            return application_error(
                str(error),
                500
            )

    # --------------------------------------------------------
    # Determine whether this request is asking for document
    # creation.
    # --------------------------------------------------------

    create_requested = (
        request.create_work
        or request.event in {
            "form_submitted_create_work",
            "create_work",
            "create_document",
            "submit_service",
            "service_submitted",
        }
    )

    # --------------------------------------------------------
    # Existing supplied document
    # --------------------------------------------------------

    supplied_text = clean_text(
        request.document_text
    )

    supplied_pages = (
        normalize_document_pages(
            request.document_pages
        )
    )

    if (
        create_requested
        and (
            supplied_text
            or supplied_pages
        )
    ):

        if not supplied_text:

            supplied_text = clean_text(
                "\n\n".join(
                    supplied_pages
                )
            )

        job = create_job(
            document_text=supplied_text,
            document_pages=supplied_pages,
            service=request.service,
            customer_request=customer_request,
            request=request,
        )

        if job is None:

            return application_error(
                "Unable to create database job.",
                500
            )

        await start_review(
            job["job_id"]
        )

        return {
            **make_job_response(
                job
            ),

            "reply":
                "Your work has been prepared and sent for review.",
        }

    # --------------------------------------------------------
    # Create document through intelligence.
    # --------------------------------------------------------

    if create_requested:

        extracted = (
            await create_document_with_intelligence(
                ada=ada,
                customer_request=customer_request,
                service=request.service,
                form_data=request.form_data,
                context=context,
                event=request.event,
                message=request.message,
            )
        )

        if not extracted:

            return application_error(
                "Intelligence returned empty document content.",
                502
            )

        job = create_job(
            document_text=extracted[
                "document_text"
            ],
            document_pages=extracted[
                "document_pages"
            ],
            service=request.service,
            customer_request=customer_request,
            request=request,
        )

        if job is None:

            return application_error(
                "Unable to create database job.",
                500
            )

        await start_review(
            job["job_id"]
        )

        return {
            **make_job_response(
                job
            ),

            "reply":
                "Your work has been prepared and sent for review.",
        }

    # --------------------------------------------------------
    # If a document was supplied without the explicit create
    # event, preserve the existing behavior and review it.
    # --------------------------------------------------------

    if (
        supplied_text
        or supplied_pages
    ):

        if job is None:

            job = create_job(
                document_text=supplied_text,
                document_pages=supplied_pages,
                service=request.service,
                customer_request=customer_request,
                request=request,
            )

        else:

            job[
                "document_text"
            ] = (
                supplied_text
                or job.get(
                    "document_text",
                    ""
                )
            )

            job[
                "document_pages"
            ] = normalize_pages_for_review(
                supplied_pages,
                job[
                    "document_text"
                ]
            )

            job[
                "status"
            ] = "reviewing"

            synchronize_job_document(
                job
            )

        if job is None:

            return application_error(
                "Unable to create database job.",
                500
            )

        await start_review(
            job["job_id"]
        )

        return make_job_response(
            job
        )

    # --------------------------------------------------------
    # Ordinary conversation.
    # --------------------------------------------------------

    respond = getattr(
        ada,
        "respond",
        None
    )

    if not callable(
        respond
    ):

        return application_error(
            "Intelligence response method is unavailable.",
            503
        )

    try:

        result = await _call_method_flexibly(
            respond,
            message=request.message,
            service=request.service,
            event=request.event,
            context=context,
            customer_request=customer_request,
        )

        text, _ = (
            _extract_from_value(
                result
            )
        )

        return {
            "ok": True,
            "reply": text,
        }

    except Exception as error:

        if DEBUG:
            traceback.print_exc()

        return application_error(
            str(error),
            500
        )


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    service: str = Form(""),
    customer_id: str = Form(""),
    job_id: str = Form(""),
):

    try:

        data = await file.read()

        if len(data) > MAX_UPLOAD:

            return application_error(
                "Uploaded file is too large.",
                413
            )

        text, pages = (
            uploaded_document_pages(
                file.filename
                or "document",
                data
            )
        )

        if not text:

            return application_error(
                "Unable to extract readable text from the uploaded document.",
                422
            )

        return {
            "ok": True,

            "filename":
                file.filename,

            "customer_id":
                customer_id,

            "job_id":
                job_id,

            "service":
                service,

            "document_text":
                text,

            "document_pages":
                pages,

            "page_count":
                len(pages),
        }

    except Exception as error:

        if DEBUG:
            traceback.print_exc()

        return application_error(
            str(error),
            500
        )


# ============================================================
# REVIEW
# ============================================================

@app.get("/api/review")
async def get_review(
    job_id: str,
    version_id: str = "",
):

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    job = _jobs.get(
        numeric_job_id
    )

    if job is None:

        job = recover_saved_job_for_approval(
            numeric_job_id,
            version_id
        )

    if job is None:

        return application_error(
            "JOB_NOT_FOUND",
            404
        )

    await start_review(
        numeric_job_id
    )

    return make_job_response(
        job
    )


@app.get("/api/review/pages")
async def get_review_pages(
    job_id: str,
    version_id: str = "",
):

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    job = _jobs.get(
        numeric_job_id
    )

    if job is None:

        job = recover_saved_job_for_approval(
            numeric_job_id,
            version_id
        )

    if job is None:

        return application_error(
            "JOB_NOT_FOUND",
            404
        )

    return {
        "ok": True,

        "job_id":
            numeric_job_id,

        "version_id":
            job.get(
                "version_id"
            ),

        "status":
            job.get(
                "status"
            ),

        "review_complete":
            job.get(
                "review_complete",
                False
            ),

        "pages":
            job.get(
                "review_pages",
                []
            ),
    }


# ============================================================
# CORRECTION
# ============================================================

async def correction_worker(
    job_id,
    instruction
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    try:

        job[
            "status"
        ] = "correcting"

        current_pages = normalize_pages(
            job.get(
                "document_pages"
            )
        )

        ada = get_session(
            job.get(
                "customer_id"
            ),
            job_id,
            job.get(
                "service"
            )
        )

        method = getattr(
            ada,
            "correct_document",
            None
        )

        if not callable(
            method
        ):

            raise RuntimeError(
                "Document correction method is unavailable."
            )

        result = await _call_method_flexibly(
            method,

            pages=current_pages,

            correction=instruction,

            service=job.get(
                "service"
            ),

            context={
                "job_id":
                    job_id,

                "customer_id":
                    job.get(
                        "customer_id"
                    ),
            },

            customer_request=job.get(
                "customer_request"
            ),

            event="correction",
        )

        extracted = extract_complete_document(
            result
        )

        if not extracted:

            raise RuntimeError(
                "Correction returned empty document content."
            )

        job[
            "document_text"
        ] = extracted[
            "document_text"
        ]

        job[
            "document_pages"
        ] = extracted[
            "document_pages"
        ]

        job[
            "review_pages"
        ] = make_review_pages(
            job[
                "document_pages"
            ]
        )

        # ----------------------------------------------------
        # Runtime version is incremented for each correction.
        # Database save_customer_work() remains authoritative
        # for the actual saved work version.
        # ----------------------------------------------------

        old_version = safe_int(
            job.get(
                "current_version",
                1
            )
        ) or 1

        job[
            "current_version"
        ] = old_version + 1

        job[
            "version_id"
        ] = (
            f"{job_id}:"
            f"{job['current_version']}"
        )

        job[
            "work_id"
        ] = None

        job[
            "work_version"
        ] = None

        job[
            "approved"
        ] = False

        job[
            "paid"
        ] = False

        job[
            "status"
        ] = "reviewing"

        job[
            "review_complete"
        ] = False

        job[
            "review_finished"
        ] = False

        update_job_status(
            job_id,
            "reviewing"
        )

        await start_review(
            job_id
        )

    except asyncio.CancelledError:

        raise

    except Exception as error:

        job[
            "status"
        ] = "correction_error"

        job[
            "correction_error"
        ] = str(
            error
        )

        try:

            update_job_status(
                job_id,
                "correction_error"
            )

        except Exception:
            pass

        if DEBUG:

            print(
                "Correction error:",
                error
            )

            traceback.print_exc()


@app.post("/api/correct")
async def correct(
    request: Correction
):

    numeric_job_id = safe_int(
        request.job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    job = _jobs.get(
        numeric_job_id
    )

    if job is None:

        job = recover_saved_job_for_approval(
            numeric_job_id,
            ""
        )

    if job is None:

        return application_error(
            "JOB_NOT_FOUND",
            404
        )

    if job.get(
        "status"
    ) == "correcting":

        return application_error(
            "A correction is already in progress.",
            409
        )

    current_task = _correction_tasks.get(
        numeric_job_id
    )

    if (
        current_task is not None
        and not current_task.done()
    ):

        current_task.cancel()

    task = asyncio.create_task(
        correction_worker(
            numeric_job_id,
            request.instruction
        )
    )

    _correction_tasks[
        numeric_job_id
    ] = task

    return {
        "ok": True,

        "job_id":
            numeric_job_id,

        "status":
            "correcting",

        "message":
            "Correction has been received.",
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: Approval
):

    supplied_job_id = safe_int(
        request.job_id
    )

    if supplied_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    supplied_version_id = (
        str(
            request.version_id
            or ""
        ).strip()
    )

    # --------------------------------------------------------
    # First use active runtime job.
    # --------------------------------------------------------

    job = _jobs.get(
        supplied_job_id
    )

    # --------------------------------------------------------
    # Recover exact saved document if runtime memory is gone.
    # --------------------------------------------------------

    if job is None:

        job = recover_saved_job_for_approval(
            supplied_job_id,
            supplied_version_id
        )

    if job is None:

        return application_error(
            "JOB_NOT_FOUND",
            404
        )

    actual_job_id = safe_int(
        job.get(
            "job_id"
        )
    )

    if actual_job_id != supplied_job_id:

        return application_error(
            "Job identity mismatch.",
            409
        )

    actual_version_id = str(
        job.get(
            "version_id"
        )
        or ""
    ).strip()

    # --------------------------------------------------------
    # If the client supplied a version, it must match exactly.
    # --------------------------------------------------------

    if (
        supplied_version_id
        and supplied_version_id
        != actual_version_id
    ):

        return application_error(
            "Document version mismatch.",
            409
        )

    if not job.get(
        "review_complete"
    ):

        return application_error(
            "Document review is not complete.",
            409
        )

    if not job.get(
        "document_pages"
    ):

        return application_error(
            "No reviewed document is available.",
            409
        )

    # --------------------------------------------------------
    # Ensure there is a persistent saved work record.
    # --------------------------------------------------------

    work_id = safe_int(
        job.get(
            "work_id"
        )
    )

    work = (
        get_work(
            work_id
        )
        if work_id is not None
        else None
    )

    if work is None:

        saved = save_document_to_storage(
            supplied_job_id
        )

        if not saved:

            return application_error(
                "Reviewed document could not be saved.",
                500
            )

        work_id = saved[
            "work_id"
        ]

        work = get_work(
            work_id
        )

    if work is None:

        return application_error(
            "Saved work record not found.",
            500
        )

    # --------------------------------------------------------
    # The database version is authoritative.
    # --------------------------------------------------------

    database_version = safe_int(
        work["version"]
    ) or 1

    actual_version_id = (
        f"{supplied_job_id}:"
        f"{database_version}"
    )

    if (
        supplied_version_id
        and supplied_version_id
        != actual_version_id
    ):

        return application_error(
            "Saved document version does not match the approval request.",
            409
        )

    job[
        "work_id"
    ] = int(
        work["id"]
    )

    job[
        "work_version"
    ] = database_version

    job[
        "current_version"
    ] = database_version

    job[
        "version_id"
    ] = actual_version_id

    job[
        "approved"
    ] = True

    job[
        "status"
    ] = "approved"

    update_job_status(
        supplied_job_id,
        "approved"
    )

    return {
        "ok": True,

        "job_id":
            supplied_job_id,

        "version_id":
            actual_version_id,

        "work_id":
            int(
                work["id"]
            ),

        "work_version":
            database_version,

        "approved":
            True,

        "status":
            "approved",

        "payment_url":
            (
                "/payment.html"
                f"?job_id="
                f"{supplied_job_id}"
                f"&version_id="
                f"{actual_version_id}"
            ),
    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def payment_create(
    request: PaymentCreate
):

    numeric_job_id = safe_int(
        request.job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    database_job = get_job(
        numeric_job_id
    )

    if database_job is None:

        return application_error(
            "Database job not found.",
            404
        )

    job = _jobs.get(
        numeric_job_id
    )

    version_id = ""

    if job:

        version_id = str(
            job.get(
                "version_id"
            )
            or ""
        )

    # --------------------------------------------------------
    # If runtime memory is gone, recover the saved work so the
    # payment page can still operate from the persistent DB.
    # --------------------------------------------------------

    if not job:

        job = recover_saved_job_for_approval(
            numeric_job_id,
            version_id
        )

    # --------------------------------------------------------
    # Only one pending payment is needed.
    # --------------------------------------------------------

    latest_payment = get_latest_payment(
        numeric_job_id
    )

    if latest_payment is not None:

        payment_status = str(
            latest_payment[
                "payment_status"
            ]
            or ""
        ).lower()

        if payment_status in {
            "pending",
            "paid",
        }:

            return {
                "ok": True,

                "job_id":
                    numeric_job_id,

                "payment_id":
                    int(
                        latest_payment["id"]
                    ),

                "amount":
                    latest_payment["amount"],

                "currency":
                    latest_payment["currency"],

                "payment_status":
                    latest_payment[
                        "payment_status"
                    ],

                "version_id":
                    version_id,

                "already_exists":
                    True,
            }

    amount = (
        request.amount
        if request.amount
        else database_job["amount"]
    )

    payment_id = create_payment(
        job_id=numeric_job_id,
        amount=amount,
        payment_method=(
            request.payment_method
            or None
        ),
        payment_status="pending",
        currency=(
            database_job["currency"]
            or "NGN"
        ),
        payment_reference=(
            request.order_number
            or None
        ),
    )

    if payment_id is None:

        return application_error(
            "Unable to create payment record.",
            500
        )

    return {
        "ok": True,

        "job_id":
            numeric_job_id,

        "payment_id":
            int(
                payment_id
            ),

        "amount":
            amount,

        "currency":
            database_job["currency"]
            or "NGN",

        "payment_status":
            "pending",

        "version_id":
            version_id,
    }


# ============================================================
# PAYMENT COMPLETE
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    request: PaymentCreate
):

    numeric_job_id = safe_int(
        request.job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    database_job = get_job(
        numeric_job_id
    )

    if database_job is None:

        return application_error(
            "Database job not found.",
            404
        )

    job = _jobs.get(
        numeric_job_id
    )

    if job is None:

        job = recover_saved_job_for_approval(
            numeric_job_id,
            ""
        )

    latest_payment = get_latest_payment(
        numeric_job_id
    )

    if latest_payment is None:

        payment_id = create_payment(
            job_id=numeric_job_id,
            amount=(
                request.amount
                or database_job["amount"]
            ),
            payment_method=(
                request.payment_method
                or None
            ),
            payment_status="pending",
            currency=(
                database_job["currency"]
                or "NGN"
            ),
            payment_reference=(
                request.order_number
                or None
            ),
        )

        if payment_id is None:

            return application_error(
                "Unable to create payment record.",
                500
            )

        latest_payment = get_payment(
            payment_id
        )

    payment_status = str(
        latest_payment[
            "payment_status"
        ]
        or ""
    ).lower()

    if payment_status == "paid":

        activated_work = (
            get_activated_work(
                numeric_job_id
            )
        )

        return {
            "ok": True,

            "job_id":
                numeric_job_id,

            "payment_id":
                int(
                    latest_payment["id"]
                ),

            "payment_status":
                "paid",

            "download_activated":
                activated_work is not None,

            "download_url":
                (
                    "/api/download"
                    f"?job_id="
                    f"{numeric_job_id}"
                    f"&version_id="
                    f"{job.get('version_id')}"
                    if activated_work
                    else None
                ),
        }

    if payment_status == "pending":

        return {
            "ok": True,

            "job_id":
                numeric_job_id,

            "payment_id":
                int(
                    latest_payment["id"]
                ),

            "payment_status":
                "pending",

            "message":
                "Payment is awaiting confirmation.",
        }

    return {
        "ok": True,

        "job_id":
            numeric_job_id,

        "payment_id":
            int(
                latest_payment["id"]
            ),

        "payment_status":
            payment_status,
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status(
    job_id: str,
    version_id: str = "",
):

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    database_job = get_job(
        numeric_job_id
    )

    if database_job is None:

        return application_error(
            "Database job not found.",
            404
        )

    job = _jobs.get(
        numeric_job_id
    )

    if job is None:

        job = recover_saved_job_for_approval(
            numeric_job_id,
            version_id
        )

    payment = get_latest_payment(
        numeric_job_id
    )

    activated_work = (
        get_activated_work(
            numeric_job_id
        )
    )

    if payment is None:

        return {
            "ok": True,

            "job_id":
                numeric_job_id,

            "payment_status":
                "none",

            "download_activated":
                activated_work is not None,

            "version_id":
                (
                    job.get(
                        "version_id"
                    )
                    if job
                    else version_id
                ),
        }

    return {
        "ok": True,

        "job_id":
            numeric_job_id,

        "payment_id":
            int(
                payment["id"]
            ),

        "payment_status":
            payment[
                "payment_status"
            ],

        "amount":
            payment[
                "amount"
            ],

        "currency":
            payment[
                "currency"
            ],

        "download_activated":
            activated_work is not None,

        "activated_work_id":
            (
                int(
                    activated_work["id"]
                )
                if activated_work
                else None
            ),

        "activated_work_version":
            (
                int(
                    activated_work["version"]
                )
                if activated_work
                else None
            ),

        "version_id":
            (
                job.get(
                    "version_id"
                )
                if job
                else version_id
            ),
    }


@app.get("/api/payment")
async def payment(
    job_id: str,
    version_id: str = "",
):

    return await payment_status(
        job_id=job_id,
        version_id=version_id,
    )


# ============================================================
# PAYMENT CONFIRMATION — BACK OFFICE
# ============================================================

@app.post("/api/payment/confirm")
async def payment_confirm(
    request: PaymentConfirm
):

    if request.admin_key != ADMIN_KEY:

        return application_error(
            "Unauthorized.",
            403
        )

    payment = get_payment(
        request.payment_id
    )

    if payment is None:

        return application_error(
            "Payment not found.",
            404
        )

    payment_id = int(
        payment["id"]
    )

    numeric_job_id = safe_int(
        payment["job_id"]
    )

    if numeric_job_id is None:

        return application_error(
            "Payment has invalid job ID.",
            500
        )

    updated = update_payment_status(
        payment_id,
        "paid"
    )

    if not updated:

        return application_error(
            "Unable to confirm payment.",
            500
        )

    update_job_status(
        numeric_job_id,
        "paid"
    )

    job = _jobs.get(
        numeric_job_id
    )

    if job:

        job[
            "paid"
        ] = True

        job[
            "status"
        ] = "paid"

    return {
        "ok": True,

        "payment_id":
            payment_id,

        "job_id":
            numeric_job_id,

        "payment_status":
            "paid",

        "download_activated":
            get_activated_work(
                numeric_job_id
            )
            is not None,
    }


# ============================================================
# BACK OFFICE JOBS
# ============================================================

@app.get("/api/back-office/jobs")
async def back_office_jobs():

    jobs = get_back_office_jobs()

    result = []

    for row in jobs:

        result.append(
            dict(
                row
            )
        )

    return {
        "ok": True,
        "jobs": result,
    }


# ============================================================
# DOWNLOAD ACTIVATION — BACK OFFICE
# ============================================================

@app.post("/api/back-office/activate-download")
async def back_office_activate_download(
    request: DownloadActivation
):

    if request.admin_key != ADMIN_KEY:

        return application_error(
            "Unauthorized.",
            403
        )

    work = get_work(
        request.work_id
    )

    if work is None:

        return application_error(
            "Work record not found.",
            404
        )

    job_id = safe_int(
        work["job_id"]
    )

    if job_id is None:

        return application_error(
            "Work record has invalid job ID.",
            500
        )

    payment = get_latest_payment(
        job_id
    )

    if payment is None:

        return application_error(
            "No payment record exists for this job.",
            409
        )

    payment_status = str(
        payment["payment_status"]
        or ""
    ).lower()

    if payment_status != "paid":

        return application_error(
            "Payment has not been confirmed.",
            409
        )

    activated = activate_work_download(
        request.work_id
    )

    if not activated:

        return application_error(
            "Unable to activate customer download.",
            500
        )

    update_job_status(
        job_id,
        "completed"
    )

    job = _jobs.get(
        job_id
    )

    if job:

        job[
            "status"
        ] = "completed"

        job[
            "paid"
        ] = True

    activated_work = get_activated_work(
        job_id
    )

    return {
        "ok": True,

        "job_id":
            job_id,

        "work_id":
            request.work_id,

        "version":
            (
                int(
                    activated_work["version"]
                )
                if activated_work
                else None
            ),

        "download_activated":
            activated_work is not None,

        "download_url":
            (
                "/api/download"
                f"?job_id={job_id}"
                f"&version_id="
                f"{job_id}:"
                f"{activated_work['version']}"
                if activated_work
                else None
            ),
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    job_id: str,
    version_id: str = "",
):

    numeric_job_id = safe_int(
        job_id
    )

    if numeric_job_id is None:

        return application_error(
            "Invalid database job ID.",
            400
        )

    database_job = get_job(
        numeric_job_id
    )

    if database_job is None:

        return application_error(
            "Database job not found.",
            404
        )

    # --------------------------------------------------------
    # Payment must be confirmed.
    # --------------------------------------------------------

    payment = get_latest_payment(
        numeric_job_id
    )

    if payment is None:

        return application_error(
            "Payment not found.",
            402
        )

    if str(
        payment["payment_status"]
        or ""
    ).lower() != "paid":

        return application_error(
            "Payment has not been confirmed.",
            402
        )

    # --------------------------------------------------------
    # Download MUST use the exact Back Office activated
    # work record.
    # --------------------------------------------------------

    activated_work = get_activated_work(
        numeric_job_id
    )

    if activated_work is None:

        return application_error(
            "Download has not been activated by Back Office.",
            403
        )

    activated_version = safe_int(
        activated_work["version"]
    ) or 1

    expected_version_id = (
        f"{numeric_job_id}:"
        f"{activated_version}"
    )

    # --------------------------------------------------------
    # If version_id was supplied, it must match the activated
    # saved work version.
    # --------------------------------------------------------

    if (
        version_id
        and str(
            version_id
        ).strip()
        != expected_version_id
    ):

        return application_error(
            "Requested document version is not the activated download version.",
            409
        )

    storage_reference = (
        activated_work[
            "storage_reference"
        ]
    )

    if not storage_reference:

        return application_error(
            "Activated work has no storage reference.",
            404
        )

    try:

        storage_path = Path(
            storage_reference
        ).resolve()

        document_root = DOCUMENT_ROOT.resolve()

        if (
            storage_path != document_root
            and document_root
            not in storage_path.parents
        ):

            return application_error(
                "Invalid document storage path.",
                403
            )

        if not storage_path.is_file():

            return application_error(
                "Document file not found.",
                404
            )

    except Exception as error:

        if DEBUG:
            traceback.print_exc()

        return application_error(
            str(error),
            500
        )

    # --------------------------------------------------------
    # Serve the exact activated saved work.
    # --------------------------------------------------------

    filename = (
        f"NPBC_Job"
        f"{numeric_job_id}"
        f"_v"
        f"{activated_version}"
        f".txt"
    )

    return FileResponse(
        path=storage_path,
        filename=filename,
        media_type="text/plain",
    )


# ============================================================
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str = "",
    job_id: str = "",
):

    key = (
        f"{customer_id or 'customer'}:"
        f"{job_id or 'job'}"
    )

    session = _sessions.get(
        key
    )

    if session is not None:

        clear_method = getattr(
            session,
            "clear_history",
            None
        )

        if callable(
            clear_method
        ):

            try:

                result = clear_method()

                if inspect.isawaitable(
                    result
                ):

                    await result

            except Exception:
                pass

    _sessions.pop(
        key,
        None
    )

    return {
        "ok": True
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print()
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER")
    print("ADA API")
    print("=" * 70)
    print(
        "Architecture:",
        "intelligence-first"
    )
    print(
        "Database:",
        "authoritative"
    )
    print(
        "Database job IDs:",
        "INTEGER"
    )
    print(
        "Complete document preservation:",
        True
    )
    print(
        "Review pagination source:",
        "complete document text"
    )
    print(
        "Customer page-count assumption:",
        False
    )
    print(
        "Document storage:",
        DOCUMENT_ROOT
    )
    print(
        "Intelligence configured:",
        is_configured()
    )
    print("=" * 70)
    print()


# ============================================================
# DIRECT RUN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
