from __future__ import annotations

import asyncio
import io
import os
import re
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ada_response import (
    AdaResponse,
    get_ada_model,
    is_configured,
    normalize_document_pages,
    document_text_to_pages,
)


# ============================================================
# CONFIGURATION
# ============================================================

DEBUG = os.getenv(
    "ADA_DEBUG_ERRORS",
    "true",
).lower() in {"1", "true", "yes", "on"}

MAX_UPLOAD = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024),
    )
)

BASE = Path(__file__).resolve().parent


# ============================================================
# RUNTIME STATE
# ============================================================

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}
_review_tasks: dict[str, asyncio.Task] = {}
_correction_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center",
    version="review-intelligence-v8",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# FILE HELPERS
# ============================================================

def find_file(name: str):
    candidates = [
        BASE / name,
        BASE / "app" / name,
        BASE / "static" / name,
        BASE / "public" / name,
        BASE / "assets" / name,
    ]

    for path in candidates:
        if path.is_file():
            return path

    return None


# ============================================================
# GENERAL HELPERS
# ============================================================

def key(customer_id, job_id):
    customer = str(customer_id or "anonymous").strip() or "anonymous"
    job = str(job_id or "default").strip() or "default"
    return f"{customer}:{job}"


def session(customer_id, job_id, service=None):
    k = key(customer_id, job_id)

    ada = _sessions.get(k)

    if ada is None:
        ada = AdaResponse(service=service)
        _sessions[k] = ada

    elif service:
        ada.set_service(service)

    return ada


def err(
    stage,
    exception,
    status=500,
    code="APPLICATION_ERROR",
):
    print(
        f"[{stage}] "
        f"{type(exception).__name__}: "
        f"{exception}"
    )

    traceback.print_exc()

    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": code,
            "error_type": type(exception).__name__,
            "error_message": (
                str(exception)
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


def ev(value):
    return str(value or "").strip().lower()


# ============================================================
# REQUEST CONTEXT
# ============================================================

class Chat(BaseModel):
    message: str = ""
    service: str | None = None
    event: str | None = None
    customer_id: str | None = None
    job_id: str | None = None
    client_request_id: str | None = None

    activate_intelligence: bool = True

    context: str | None = None
    form_data: dict[str, Any] | None = None

    guidance_only: bool = False
    create_work: bool = False

    document_pages: list[Any] | None = None
    document_text: str | None = None


class Correction(BaseModel):
    job_id: str
    instruction: str


class Approval(BaseModel):
    job_id: str
    version_id: str


def form_request(request: Chat):
    parts = []

    if request.service:
        parts.append(
            "SELECTED SERVICE:\n"
            + request.service.strip()
        )

    if request.form_data:
        information = []

        for key_name, value in request.form_data.items():
            if str(value or "").strip():
                label = (
                    str(key_name)
                    .replace("_", " ")
                    .title()
                )

                information.append(
                    f"{label}: {value}"
                )

        if information:
            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(information)
            )

    if request.context and request.context.strip():
        parts.append(
            "ADDITIONAL CONTEXT:\n"
            + request.context.strip()
        )

    if request.message.strip():
        parts.append(
            "CUSTOMER REQUEST:\n"
            + request.message.strip()
        )

    return "\n\n".join(parts).strip()


def ctx(request: Chat):
    parts = []

    if request.context and request.context.strip():
        parts.append(request.context.strip())

    if request.customer_id:
        parts.append(
            "CUSTOMER ID:\n"
            + request.customer_id
        )

    if request.client_request_id:
        parts.append(
            "CLIENT REQUEST ID:\n"
            + request.client_request_id
        )

    return "\n\n".join(parts) or None


# ============================================================
# DOCUMENT PAGE NORMALIZATION
# ============================================================

def stored(pages):
    output = []

    normalized = normalize_document_pages(
        pages or []
    )

    for position, page in enumerate(
        normalized,
        1,
    ):
        if not isinstance(page, dict):
            continue

        page_number = int(
            page.get(
                "page_number",
                position,
            )
            or position
        )

        content = str(
            page.get(
                "content",
                "",
            )
            or ""
        )

        output.append(
            {
                **page,
                "page_number": page_number,
                "position": position,
                "content": content,
            }
        )

    return output


# ============================================================
# GENERATED DOCUMENT PAGINATION
# ============================================================

PAGE_MARKER_PATTERNS = [
    re.compile(
        r"(?im)^\s*(?:---\s*)?PAGE\s+(\d+)\s*(?:---\s*)?$"
    ),
    re.compile(
        r"(?im)^\s*\[\s*PAGE\s+(\d+)\s*\]\s*$"
    ),
    re.compile(
        r"(?im)^\s*#{0,6}\s*PAGE\s+(\d+)\s*$"
    ),
]


def split_explicit_pages(text: str):
    """
    Detect page markers produced by the intelligence layer.

    Supported examples:

        PAGE 1
        PAGE 2

        [PAGE 1]
        [PAGE 2]

        --- PAGE 1 ---
        --- PAGE 2 ---
    """

    for pattern in PAGE_MARKER_PATTERNS:
        matches = list(pattern.finditer(text))

        if len(matches) >= 2:
            pages = []

            for index, match in enumerate(matches):
                start = match.end()

                if index + 1 < len(matches):
                    end = matches[index + 1].start()
                else:
                    end = len(text)

                content = text[start:end].strip()

                if content:
                    pages.append(
                        {
                            "page_number": len(pages) + 1,
                            "content": content,
                        }
                    )

            if pages:
                return pages

    return None


def split_by_sections(text: str):
    """
    Split a generated document into meaningful sections.

    This is deliberately NOT keyword intelligence.

    It only converts an already-generated document into
    reviewable pages so the review system does not treat
    an entire multi-page document as one page.
    """

    text = str(text or "").strip()

    if not text:
        return []

    # First honour explicit page markers.
    explicit = split_explicit_pages(text)

    if explicit:
        return explicit

    # Preserve markdown/table structure while splitting.
    blocks = re.split(
        r"\n\s*\n+",
        text,
    )

    blocks = [
        block.strip()
        for block in blocks
        if block.strip()
    ]

    if not blocks:
        return [
            {
                "page_number": 1,
                "content": text,
            }
        ]

    # Approximate page size.
    #
    # The actual final DOCX/PDF renderer may use a different
    # physical page size, but this prevents the review system
    # from incorrectly reporting one page for a substantial
    # generated document.
    TARGET_CHARS = 2800
    HARD_LIMIT = 3600

    pages = []
    current = []

    current_length = 0

    def flush():
        nonlocal current
        nonlocal current_length

        if not current:
            return

        content = "\n\n".join(current).strip()

        if content:
            pages.append(
                {
                    "page_number": len(pages) + 1,
                    "content": content,
                }
            )

        current = []
        current_length = 0

    for block in blocks:
        block_length = len(block)

        # A very large individual block must be split safely.
        if block_length > HARD_LIMIT:
            flush()

            paragraphs = re.split(
                r"(?<=[.!?])\s+",
                block,
            )

            chunk = []

            chunk_length = 0

            for sentence in paragraphs:
                sentence = sentence.strip()

                if not sentence:
                    continue

                if (
                    chunk
                    and chunk_length + len(sentence) > TARGET_CHARS
                ):
                    pages.append(
                        {
                            "page_number": len(pages) + 1,
                            "content": " ".join(chunk).strip(),
                        }
                    )

                    chunk = []
                    chunk_length = 0

                chunk.append(sentence)
                chunk_length += len(sentence) + 1

            if chunk:
                pages.append(
                    {
                        "page_number": len(pages) + 1,
                        "content": " ".join(chunk).strip(),
                    }
                )

            continue

        # Start a new page when the current page is sufficiently
        # full and the next block would push it too far.
        if (
            current
            and current_length >= TARGET_CHARS
        ):
            flush()

        elif (
            current
            and current_length + block_length > HARD_LIMIT
        ):
            flush()

        current.append(block)
        current_length += block_length + 2

    flush()

    return pages


def text_to_review_pages(text: str):
    """
    Convert generated text into actual review pages.

    The old implementation passed the entire generated response
    through document_text_to_pages(), which could produce one
    giant page.

    This function guarantees that substantial generated work is
    represented as multiple reviewable pages.
    """

    text = str(text or "").strip()

    if not text:
        return []

    pages = split_by_sections(text)

    if not pages:
        return []

    return stored(pages)


# ============================================================
# JOB STATE
# ============================================================

def job_response(job):
    pages = stored(
        job.get(
            "document_pages",
            [],
        )
    )

    job["document_pages"] = pages

    total = len(pages)

    progress = job.get(
        "progress",
        {},
    )

    return {
        "success": True,
        "job_id": job["job_id"],
        "customer_id": job.get("customer_id"),
        "service": job.get("service"),
        "status": job.get("status"),
        "current_version": job.get(
            "current_version",
            1,
        ),
        "version_id": job.get(
            "version_id"
        ),

        "review_started": job.get(
            "review_started",
            False,
        ),

        "review_finished": job.get(
            "review_finished",
            False,
        ),

        "approved": job.get(
            "approved",
            False,
        ),

        "paid": job.get(
            "paid",
            False,
        ),

        "progress": {
            "completed": int(
                progress.get(
                    "completed",
                    0,
                )
            ),
            "total": total,
        },

        "total_pages": total,

        "document_pages": pages,
        "pages": pages,

        "review_pages": job.get(
            "review_pages",
            [],
        ),

        "assembled_review": job.get(
            "assembled_review",
            "",
        ),

        "error": job.get(
            "review_error"
        ),

        "review_url":
            f"/review.html?job_id={job['job_id']}",
    }


def review_pages(pages):
    output = []

    for position, page in enumerate(
        stored(pages),
        1,
    ):
        output.append(
            {
                "page_number": page.get(
                    "page_number",
                    position,
                ),
                "position": position,
                "status": "queued",
                "content": str(
                    page.get(
                        "content",
                        "",
                    )
                    or ""
                ),
                "review": "",
                "error": None,
            }
        )

    return output


def new_job(
    job_id,
    request,
    original_request,
    pages,
):
    pages = stored(pages)

    if not pages:
        raise ValueError(
            "Cannot create a job without document pages."
        )

    job = {
        "job_id": job_id,

        "customer_id":
            request.customer_id,

        "service":
            request.service,

        "original_request":
            original_request,

        "context":
            ctx(request),

        "client_request_id":
            request.client_request_id,

        "status":
            "reviewing",

        "review_started":
            True,

        "review_finished":
            False,

        "review_error":
            None,

        "progress": {
            "completed": 0,
            "total": len(pages),
        },

        "document_pages":
            pages,

        "review_pages":
            review_pages(pages),

        "assembled_review":
            "",

        "current_version":
            1,

        "version_id":
            job_id + ":1",

        "approved":
            False,

        "paid":
            False,
    }

    _jobs[job_id] = job

    return job


# ============================================================
# REVIEW CALLBACK
# ============================================================

def cb_review(job_id):
    def callback(update):
        job = _jobs.get(job_id)

        if not job:
            return

        update_type = ev(
            update.get("type")
        )

        page_number = str(
            update.get(
                "page_number",
                "",
            )
        )

        if update_type == "page_started":

            for page in job["review_pages"]:
                if (
                    str(page["page_number"])
                    == page_number
                ):
                    page["status"] = "reviewing"

        elif update_type == "page_completed":

            for page in job["review_pages"]:

                if (
                    str(page["page_number"])
                    == page_number
                ):
                    page["status"] = "reviewed"

                    page["review"] = str(
                        update.get(
                            "review",
                            "",
                        )
                        or ""
                    )

                    page["content"] = str(
                        update.get(
                            "content",
                            page["content"],
                        )
                        or ""
                    )

                    page["error"] = None

            position = update.get(
                "position"
            )

            if position is not None:
                try:
                    job["progress"]["completed"] = int(
                        position
                    )
                except Exception:
                    pass

        elif update_type == "page_error":

            for page in job["review_pages"]:

                if (
                    str(page["page_number"])
                    == page_number
                ):
                    page["status"] = "error"

                    page["error"] = str(
                        update.get(
                            "error",
                            "Page review failed.",
                        )
                    )

        elif update_type == "review_completed":

            total = len(
                job["document_pages"]
            )

            job["status"] = "review_complete"
            job["review_finished"] = True

            job["progress"] = {
                "completed": total,
                "total": total,
            }

            job["assembled_review"] = str(
                update.get(
                    "assembled_review",
                    "",
                )
                or ""
            )

    return callback


# ============================================================
# REVIEW EXECUTION
# ============================================================

async def run_review(job_id):
    job = _jobs.get(job_id)

    if not job:
        return

    try:
        ada = session(
            job.get("customer_id"),
            job_id,
            job.get("service"),
        )

        pages = stored(
            job["document_pages"]
        )

        result = await asyncio.to_thread(
            ada.review_document_pages,
            pages=pages,
            service=job.get("service"),
            context=job.get("context"),
            customer_request=job.get(
                "original_request"
            ),
            event="send_for_review",
            progress_callback=cb_review(job_id),
        )

        if not isinstance(result, dict):
            raise TypeError(
                "Invalid review result."
            )

        for reviewed_page in (
            result.get("pages", [])
            or []
        ):

            if not isinstance(
                reviewed_page,
                dict,
            ):
                continue

            reviewed_number = str(
                reviewed_page.get(
                    "page_number",
                    "",
                )
            )

            for page in job["review_pages"]:

                if (
                    str(page["page_number"])
                    == reviewed_number
                ):
                    if "review" in reviewed_page:
                        page["review"] = str(
                            reviewed_page["review"]
                            or ""
                        )

                    if "content" in reviewed_page:
                        page["content"] = str(
                            reviewed_page["content"]
                            or ""
                        )

                    page["status"] = "reviewed"

        job["assembled_review"] = str(
            result.get(
                "assembled_review",
                "",
            )
            or ""
        )

        total = len(
            job["document_pages"]
        )

        job["status"] = "review_complete"
        job["review_finished"] = True
        job["review_error"] = None

        job["progress"] = {
            "completed": total,
            "total": total,
        }

    except asyncio.CancelledError:
        raise

    except Exception as exception:

        job["status"] = "review_error"
        job["review_finished"] = True

        job["review_error"] = {
            "type":
                type(exception).__name__,
            "message":
                str(exception),
        }

        traceback.print_exc()


def start_review(job_id):
    job = _jobs.get(job_id)

    if not job:
        return False

    if not job.get("document_pages"):
        return False

    if job.get("status") != "reviewing":
        return False

    existing = _review_tasks.get(
        job_id
    )

    if existing and not existing.done():
        return False

    _review_tasks[job_id] = (
        asyncio.create_task(
            run_review(job_id)
        )
    )

    return True


# ============================================================
# UPLOAD EXTRACTION
# ============================================================

def extract(data, filename):
    suffix = Path(
        filename
    ).suffix.lower()

    if suffix in {
        ".txt",
        ".csv",
    }:
        return data.decode(
            "utf-8",
            "replace",
        )

    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(
            io.BytesIO(data)
        )

        return "\n\n".join(
            (
                page.extract_text()
                or ""
            )
            for page in reader.pages
        )

    if suffix in {
        ".docx",
        ".xlsx",
        ".pptx",
    }:

        with zipfile.ZipFile(
            io.BytesIO(data)
        ) as archive:

            names = archive.namelist()
            texts = []

            patterns = {
                "docx":
                    "word/document.xml",

                "pptx":
                    r"ppt/slides/slide\d+\.xml",

                "xlsx":
                    r"xl/worksheets/sheet\d+\.xml",
            }

            if suffix == ".docx":

                target = patterns["docx"]

                names = (
                    [target]
                    if target in names
                    else []
                )

            else:

                names = [
                    name
                    for name in names
                    if re.match(
                        patterns[
                            suffix[1:]
                        ],
                        name,
                    )
                ]

            for name in sorted(names):

                root = ET.fromstring(
                    archive.read(name)
                )

                values = [
                    element.text or ""
                    for element in root.iter()
                    if (
                        isinstance(
                            element.tag,
                            str,
                        )
                        and element.tag.rsplit(
                            "}",
                            1,
                        )[-1]
                        == "t"
                    )
                ]

                if values:
                    texts.append(
                        " ".join(values)
                    )

            return "\n\n".join(texts)

    raise RuntimeError(
        "Unsupported document type: "
        + (
            suffix
            or "unknown"
        )
    )


def upload_pages(
    filename,
    data,
):
    text = extract(
        data,
        filename,
    ).strip()

    if not text:
        raise ValueError(
            "The uploaded document contains no extractable text."
        )

    # Preserve extracted document structure where possible.
    pages = document_text_to_pages(
        text
    )

    normalized = stored(pages)

    if normalized:
        return normalized

    return text_to_review_pages(
        text
    )


# ============================================================
# GENERATED WORK EXTRACTION
# ============================================================

def generated_pages(result):
    """
    Convert any structured document returned by AdaResponse
    into normalized review pages.

    IMPORTANT:
    We first use actual page structures when supplied.

    If AdaResponse returns plain text, the text is paginated
    locally for the review system.
    """

    # --------------------------------------------------------
    # Already structured pages
    # --------------------------------------------------------

    if isinstance(result, dict):

        for key_name in (
            "pages",
            "document_pages",
            "prepared_pages",
            "content_pages",
        ):

            value = result.get(
                key_name
            )

            if isinstance(
                value,
                list,
            ):

                pages = stored(value)

                if pages:
                    return pages

    # --------------------------------------------------------
    # Plain generated document text
    # --------------------------------------------------------

    if isinstance(result, dict):

        for key_name in (
            "document_text",
            "prepared_work",
            "document",
            "content",
            "text",
            "reply",
            "response",
            "message",
        ):

            value = result.get(
                key_name
            )

            if (
                isinstance(
                    value,
                    str,
                )
                and value.strip()
            ):

                return text_to_review_pages(
                    value
                )

    # --------------------------------------------------------
    # Direct string response
    # --------------------------------------------------------

    if isinstance(
        result,
        str,
    ) and result.strip():

        return text_to_review_pages(
            result
        )

    raise ValueError(
        "AdaResponse returned no usable document work."
    )


# ============================================================
# DOCUMENT CREATION
# ============================================================

async def create_work(
    ada,
    request,
    customer_request,
    context,
):
    """
    Use the actual document-generation interface exposed by
    AdaResponse.

    There is deliberately NO keyword-based document generator.

    The previous compatibility call incorrectly sent:

        create_work=True

    to AdaResponse.respond().

    That caused:

        AdaResponse.respond() got an unexpected keyword argument
        'create_work'

    This replacement never sends that unsupported argument.
    """

    # --------------------------------------------------------
    # Preferred structured creation interfaces
    # --------------------------------------------------------

    for method_name in (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    ):

        function = getattr(
            ada,
            method_name,
            None,
        )

        if not callable(function):
            continue

        try:

            result = await asyncio.to_thread(
                function,
                customer_request=customer_request,
                service=request.service,
                form_data=request.form_data,
                context=context,
                event=request.event,
            )

            return generated_pages(
                result
            )

        except TypeError:

            # Some versions expose a smaller
            # method signature.

            try:

                result = await asyncio.to_thread(
                    function,
                    message=customer_request,
                    service=request.service,
                    context=context,
                    event=request.event,
                )

                return generated_pages(
                    result
                )

            except TypeError:
                continue

    # --------------------------------------------------------
    # Normal intelligence response fallback
    # --------------------------------------------------------

    respond = getattr(
        ada,
        "respond",
        None,
    )

    if not callable(respond):
        raise AttributeError(
            "AdaResponse has no document creation method "
            "and no respond() method."
        )

    # IMPORTANT:
    #
    # Do NOT send create_work=True.
    #
    # That argument does not exist in the deployed
    # AdaResponse.respond() interface.

    try:

        result = await asyncio.to_thread(
            respond,
            message=customer_request,
            service=request.service,
            event=request.event,
            context=context,
            form_data=request.form_data,
        )

    except TypeError:

        # Compatibility with deployments where
        # form_data is not accepted.

        try:

            result = await asyncio.to_thread(
                respond,
                message=customer_request,
                service=request.service,
                event=request.event,
                context=context,
            )

        except TypeError:

            # Final compatibility form for the simplest
            # respond(message, service, event, context)
            # implementation.

            result = await asyncio.to_thread(
                respond,
                message=customer_request,
                service=request.service,
                event=request.event,
                context=context,
            )

    return generated_pages(
        result
    )


# ============================================================
# HTML ROUTES
# ============================================================

def html(filename):
    path = find_file(
        filename
    )

    if not path:
        return err(
            "PAGE",
            FileNotFoundError(
                f"{filename} was not found."
            ),
            404,
            "HTML_NOT_FOUND",
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


@app.get("/")
async def root():
    return html(
        "index.html"
    )


@app.get("/index.html")
async def index():
    return html(
        "index.html"
    )


@app.get("/conversation.html")
async def conversation():
    return html(
        "conversation.html"
    )


@app.get("/workspace.html")
async def workspace():
    return html(
        "workspace.html"
    )


@app.get("/review.html")
async def review_page():
    return html(
        "review.html"
    )


@app.get("/payment.html")
async def payment_page():
    return html(
        "payment.html"
    )


@app.get("/download.html")
async def download_page():
    return html(
        "download.html"
    )


# ============================================================
# HEALTH / STATUS
# ============================================================

@app.get("/health")
async def health():
    return {
        "success": True,
        "status": "ok",
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
    }


@app.get("/api/status")
async def status():
    return {
        "success": True,
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
        "active_sessions":
            len(_sessions),
        "active_jobs":
            len(_jobs),
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):
    try:

        data = await file.read()

        if not data:
            return err(
                "UPLOAD",
                ValueError(
                    "The uploaded file is empty."
                ),
                400,
                "EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD:
            return err(
                "UPLOAD",
                ValueError(
                    "The uploaded document is too large."
                ),
                413,
                "FILE_TOO_LARGE",
            )

        pages = await asyncio.to_thread(
            upload_pages,
            file.filename or "document",
            data,
        )

        job_id_value = (
            str(job_id or "").strip()
            or str(uuid.uuid4())
        )

        return {
            "success": True,
            "filename": file.filename,
            "job_id": job_id_value,
            "customer_id": customer_id,
            "client_request_id":
                client_request_id,
            "service": service,
            "total_pages":
                len(pages),
            "document_pages":
                pages,
            "pages":
                pages,
        }

    except Exception as exception:

        return err(
            "UPLOAD",
            exception,
            400,
            "DOCUMENT_UPLOAD_ERROR",
        )


# ============================================================
# MAIN CHAT / WORK CREATION / REVIEW INTAKE
# ============================================================

@app.post("/api/chat")
async def chat(request: Chat):

    if not request.activate_intelligence:
        return err(
            "INTELLIGENCE",
            ValueError(
                "Intelligence activation is disabled."
            ),
            400,
            "INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():
        return err(
            "INTELLIGENCE",
            RuntimeError(
                "AdaResponse is not configured."
            ),
            503,
            "INTELLIGENCE_NOT_CONFIGURED",
        )

    job_id = (
        str(request.job_id or "").strip()
        or str(uuid.uuid4())
    )

    application_context = ctx(
        request
    )

    pages = stored(
        request.document_pages or []
    )

    if (
        not pages
        and request.document_text
        and request.document_text.strip()
    ):
        pages = text_to_review_pages(
            request.document_text
        )

    try:

        ada = session(
            request.customer_id,
            job_id,
            request.service,
        )

        # ====================================================
        # GUIDANCE ONLY
        # ====================================================

        if request.guidance_only:

            if not request.message.strip():
                return err(
                    "GUIDANCE",
                    ValueError(
                        "The guidance message is empty."
                    ),
                    400,
                    "EMPTY_GUIDANCE_MESSAGE",
                )

            reply = await asyncio.to_thread(
                ada.respond,
                message=request.message.strip(),
                service=request.service,
                event=request.event,
                context=application_context,
            )

            return {
                "success": True,
                "reply":
                    str(reply or "").strip(),
                "job_id":
                    job_id,
                "created_work":
                    False,
            }

        # ====================================================
        # BUILD CUSTOMER REQUEST
        # ====================================================

        customer_request = form_request(
            request
        )

        # ====================================================
        # DOCUMENT CREATION REQUEST
        # ====================================================

        create_requested = (
            request.create_work
            or ev(request.event)
            in {
                "form_submitted_create_work",
                "create_work",
                "create_document",
            }
        )

        if create_requested and not pages:

            if not customer_request:
                return err(
                    "WORK_CREATION",
                    ValueError(
                        "The customer service request contains "
                        "no usable information."
                    ),
                    400,
                    "EMPTY_WORK_REQUEST",
                )

            created_pages = await create_work(
                ada,
                request,
                customer_request,
                application_context,
            )

            if not created_pages:
                raise ValueError(
                    "No document pages were produced."
                )

            job = new_job(
                job_id,
                request,
                customer_request,
                created_pages,
            )

            started = start_review(
                job_id
            )

            output = job_response(
                job
            )

            output.update(
                {
                    "reply":
                        "Your request has been prepared "
                        "and sent into document review.",

                    "created_work":
                        True,

                    "work_created":
                        True,

                    "review_started":
                        started,
                }
            )

            return output

        # ====================================================
        # DOCUMENT INTAKE / REVIEW
        # ====================================================

        if pages:

            job = _jobs.get(
                job_id
            )

            if job is None:

                job = new_job(
                    job_id,
                    request,
                    customer_request,
                    pages,
                )

            else:

                existing_task = _review_tasks.get(
                    job_id
                )

                if (
                    existing_task
                    and not existing_task.done()
                    and job.get("status")
                    == "reviewing"
                ):
                    return job_response(
                        job
                    )

                job.update(
                    {
                        "document_pages":
                            pages,

                        "review_pages":
                            review_pages(pages),

                        "assembled_review":
                            "",

                        "status":
                            "reviewing",

                        "review_started":
                            True,

                        "review_finished":
                            False,

                        "review_error":
                            None,

                        "approved":
                            False,

                        "paid":
                            False,

                        "progress":
                            {
                                "completed": 0,
                                "total":
                                    len(pages),
                            },

                        "customer_id":
                            request.customer_id,

                        "service":
                            request.service
                            or job.get("service"),

                        "original_request":
                            customer_request,

                        "context":
                            application_context,
                    }
                )

            started = start_review(
                job_id
            )

            output = job_response(
                job
            )

            output.update(
                {
                    "reply":
                        "Your document has been received. "
                        "It is now being reviewed page by page.",

                    "created_work":
                        True,

                    "review_started":
                        started,
                }
            )

            return output

        # ====================================================
        # NORMAL CHAT
        # ====================================================

        if not request.message.strip():
            return err(
                "CHAT",
                ValueError(
                    "The chat message is empty."
                ),
                400,
                "EMPTY_MESSAGE",
            )

        reply = await asyncio.to_thread(
            ada.respond,
            message=request.message.strip(),
            service=request.service,
            event=request.event,
            context=application_context,
        )

        return {
            "success": True,
            "reply":
                str(reply or "").strip(),
            "job_id":
                job_id,
            "service":
                request.service
                or ada.service,
            "created_work":
                False,
        }

    except Exception as exception:

        return err(
            "CHAT",
            exception,
            500,
            "CHAT_ERROR",
        )


# ============================================================
# REVIEW STATUS
# ============================================================

@app.get("/api/review")
async def get_review(
    job_id: str,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return err(
            "REVIEW",
            ValueError(
                "The requested review job does not exist."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    start_review(
        job_id
    )

    return job_response(
        job
    )


# ============================================================
# REVIEW PAGES
# ============================================================

@app.get("/api/review/pages")
async def get_pages(
    job_id: str,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return err(
            "REVIEW_PAGES",
            ValueError(
                "The requested review job does not exist."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    start_review(
        job_id
    )

    document_pages = stored(
        job["document_pages"]
    )

    return {
        "success": True,
        "job_id":
            job_id,

        "current_version":
            job["current_version"],

        "version_id":
            job["version_id"],

        "status":
            job["status"],

        "total_pages":
            len(document_pages),

        "pages":
            document_pages,

        "document_pages":
            document_pages,

        "review_pages":
            job["review_pages"],

        "progress":
            job["progress"],

        "approved":
            job["approved"],

        "paid":
            job["paid"],
    }


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(
    request: Correction,
):
    job = _jobs.get(
        request.job_id
    )

    instruction = (
        request.instruction.strip()
    )

    if not job:
        return err(
            "CORRECTION",
            ValueError(
                "Job not found."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    if not instruction:
        return err(
            "CORRECTION",
            ValueError(
                "Correction instruction is empty."
            ),
            400,
            "EMPTY_CORRECTION",
        )

    if job.get("status") in {
        "reviewing",
        "correcting",
    }:
        return err(
            "CORRECTION",
            ValueError(
                "The document is still being processed."
            ),
            409,
            "DOCUMENT_STILL_PROCESSING",
        )

    if not job.get(
        "document_pages"
    ):
        return err(
            "CORRECTION",
            ValueError(
                "There is no document available for correction."
            ),
            409,
            "NO_DOCUMENT",
        )

    job["current_version"] += 1

    job["version_id"] = (
        f"{request.job_id}:"
        f"{job['current_version']}"
    )

    job.update(
        {
            "status":
                "correcting",

            "approved":
                False,

            "paid":
                False,

            "review_started":
                False,

            "review_finished":
                False,

            "review_error":
                None,

            "correction_instruction":
                instruction,

            "progress":
                {
                    "completed": 0,
                    "total":
                        len(
                            job["document_pages"]
                        ),
                },
        }
    )

    async def worker():

        try:

            ada = session(
                job.get("customer_id"),
                request.job_id,
                job.get("service"),
            )

            result = await asyncio.to_thread(
                ada.correct_document,
                document_pages=stored(
                    job["document_pages"]
                ),
                correction=instruction,
                service=job.get("service"),
                context=job.get("context"),
                progress_callback=None,
            )

            corrected_pages = generated_pages(
                result
            )

            if not corrected_pages:
                raise ValueError(
                    "Correction produced no document pages."
                )

            job["document_pages"] = (
                corrected_pages
            )

            job["review_pages"] = (
                review_pages(
                    corrected_pages
                )
            )

            job["status"] = (
                "reviewing"
            )

            job["review_started"] = (
                True
            )

            job["review_finished"] = (
                False
            )

            job["review_error"] = (
                None
            )

            job["progress"] = {
                "completed": 0,
                "total":
                    len(
                        corrected_pages
                    ),
            }

            start_review(
                request.job_id
            )

        except Exception as exception:

            job["status"] = (
                "correction_error"
            )

            job["review_error"] = {
                "type":
                    type(exception).__name__,

                "message":
                    str(exception),
            }

            traceback.print_exc()

    old_task = _correction_tasks.get(
        request.job_id
    )

    if (
        old_task
        and not old_task.done()
    ):
        old_task.cancel()

    _correction_tasks[
        request.job_id
    ] = asyncio.create_task(
        worker()
    )

    return {
        "success": True,
        "job_id":
            request.job_id,

        "status":
            "correcting",

        "version_id":
            job["version_id"],

        "current_version":
            job["current_version"],

        "message":
            "Correction has started. "
            "The corrected document will be reviewed again.",
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: Approval,
):
    job = _jobs.get(
        request.job_id
    )

    if not job:
        return err(
            "APPROVAL",
            ValueError(
                "Job not found."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    if (
        request.version_id
        != job["version_id"]
    ):
        return err(
            "APPROVAL",
            ValueError(
                "The supplied document version does not match."
            ),
            409,
            "VERSION_MISMATCH",
        )

    if job["status"] != "review_complete":
        return err(
            "APPROVAL",
            ValueError(
                "The document review is not complete."
            ),
            409,
            "REVIEW_NOT_COMPLETE",
        )

    job["approved"] = True
    job["status"] = "approved"

    return {
        "success": True,
        "job_id":
            request.job_id,

        "version_id":
            request.version_id,

        "current_version":
            job["current_version"],

        "approved":
            True,

        "status":
            "approved",

        "total_pages":
            len(
                job["document_pages"]
            ),

        "pages":
            job["document_pages"],

        "payment_url":
            (
                "/payment.html"
                f"?job_id={request.job_id}"
                f"&version_id={request.version_id}"
            ),
    }


# ============================================================
# PAYMENT COMPLETE
# ============================================================

@app.post("/api/payment/complete")
async def payment_complete(
    job_id: str,
    version_id: str,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return err(
            "PAYMENT",
            ValueError(
                "Job not found."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != job["version_id"]:
        return err(
            "PAYMENT",
            ValueError(
                "Version mismatch."
            ),
            409,
            "VERSION_MISMATCH",
        )

    if not job["approved"]:
        return err(
            "PAYMENT",
            ValueError(
                "The document must be approved before payment."
            ),
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    job["paid"] = True
    job["status"] = "paid"

    return {
        "success": True,
        "job_id":
            job_id,

        "version_id":
            version_id,

        "paid":
            True,

        "status":
            "paid",

        "total_pages":
            len(
                job["document_pages"]
            ),

        "download_url":
            (
                "/download.html"
                f"?job_id={job_id}"
                f"&version_id={version_id}"
            ),

        "api_download_url":
            (
                "/api/download"
                f"?job_id={job_id}"
                f"&version_id={version_id}"
            ),
    }


# ============================================================
# PAYMENT STATE
# ============================================================

@app.get("/api/payment")
async def payment(
    job_id: str,
    version_id: str,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return err(
            "PAYMENT_STATE",
            ValueError(
                "Job not found."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != job["version_id"]:
        return err(
            "PAYMENT_STATE",
            ValueError(
                "Version mismatch."
            ),
            409,
            "VERSION_MISMATCH",
        )

    return {
        "success": True,
        "job_id":
            job_id,

        "version_id":
            version_id,

        "status":
            job["status"],

        "approved":
            job["approved"],

        "paid":
            job["paid"],

        "total_pages":
            len(
                job["document_pages"]
            ),

        "payment_complete":
            job["paid"],
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    job_id: str,
    version_id: str,
):
    job = _jobs.get(
        job_id
    )

    if not job:
        return err(
            "DOWNLOAD",
            ValueError(
                "Job not found."
            ),
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != job["version_id"]:
        return err(
            "DOWNLOAD",
            ValueError(
                "Version mismatch."
            ),
            409,
            "VERSION_MISMATCH",
        )

    if not job["approved"]:
        return err(
            "DOWNLOAD",
            ValueError(
                "The current document version has not been approved."
            ),
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    if not job["paid"]:
        return err(
            "DOWNLOAD",
            ValueError(
                "Payment for the current document version "
                "has not been completed."
            ),
            409,
            "PAYMENT_NOT_COMPLETED",
        )

    return {
        "success": True,
        "job_id":
            job_id,

        "version_id":
            version_id,

        "status":
            "paid",

        "total_pages":
            len(
                job["document_pages"]
            ),

        "pages":
            job["document_pages"],

        "document_pages":
            job["document_pages"],

        "message":
            "The approved and paid document is ready "
            "for final document generation.",
    }


# ============================================================
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear(
    customer_id: str | None = None,
    job_id: str | None = None,
):
    ada = _sessions.get(
        key(
            customer_id,
            job_id,
        )
    )

    if ada:
        ada.clear_history()

    return {
        "success": True,
        "message":
            "Conversation cleared.",
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print("=" * 70)

    print(
        "NAIJA POCKET BUSINESS CENTER — FASTAPI"
    )

    print(
        "AdaResponse:",
        get_ada_model(),
        "configured=",
        is_configured(),
    )

    print(
        "Complete page workflow: ENABLED"
    )

    print(
        "Keyword intelligence: DISABLED"
    )

    print(
        "Generated-document pagination: ENABLED"
    )

    print(
        "Review → Correction → Approval → Payment: ENABLED"
    )

    print("=" * 70)


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "ada_api:app",
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
        reload=False,
    )
