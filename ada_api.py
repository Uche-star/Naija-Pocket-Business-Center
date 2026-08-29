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

DEBUG = os.getenv("ADA_DEBUG_ERRORS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

MAX_UPLOAD = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024),
    )
)

# Maximum approximate text size for one review page.
#
# This is deliberately a logical document-page size.
# It prevents one huge generated response from becoming
# one gigantic "page" in the review system.
PAGE_TARGET_CHARS = int(
    os.getenv(
        "ADA_REVIEW_PAGE_TARGET_CHARS",
        "5500",
    )
)

MIN_PAGE_CHARS = int(
    os.getenv(
        "ADA_REVIEW_PAGE_MIN_CHARS",
        "1200",
    )
)


BASE = Path(__file__).resolve().parent

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

def ev(value: Any) -> str:
    return str(value or "").strip().lower()


def make_key(customer_id: Any, job_id: Any) -> str:
    customer = str(customer_id or "anonymous").strip() or "anonymous"
    job = str(job_id or "default").strip() or "default"
    return f"{customer}:{job}"


def get_session(
    customer_id: Any,
    job_id: Any,
    service: str | None = None,
) -> AdaResponse:

    k = make_key(customer_id, job_id)

    assistant = _sessions.get(k)

    if assistant is None:
        assistant = AdaResponse(service=service)
        _sessions[k] = assistant

    elif service:
        try:
            assistant.set_service(service)
        except Exception:
            pass

    return assistant


def err(
    stage: str,
    exception: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
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
            "error_type": (
                type(exception).__name__
                if isinstance(exception, Exception)
                else "ApplicationError"
            ),
            "error_message": (
                str(exception)
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


# ============================================================
# REQUEST MODELS
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


# ============================================================
# REQUEST CONTEXT
# ============================================================

def form_request(request: Chat) -> str:
    parts: list[str] = []

    if request.service:
        parts.append(
            "SELECTED SERVICE:\n"
            + request.service.strip()
        )

    if request.form_data:
        lines = []

        for key, value in request.form_data.items():
            if str(value or "").strip():
                label = (
                    str(key)
                    .replace("_", " ")
                    .title()
                )

                lines.append(
                    f"{label}: {value}"
                )

        if lines:
            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(lines)
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


def request_context(request: Chat) -> str | None:
    parts: list[str] = []

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

    return "\n\n".join(parts).strip() or None


# ============================================================
# DOCUMENT PAGE NORMALIZATION
# ============================================================

def clean_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_pages(pages: Any) -> list[dict[str, Any]]:
    """
    Normalize arbitrary page structures into the single page
    structure used by the complete document workflow.
    """

    if not isinstance(pages, list):
        return []

    result: list[dict[str, Any]] = []

    try:
        normalized = normalize_document_pages(pages)
    except Exception:
        normalized = pages

    for position, page in enumerate(normalized or [], 1):

        if isinstance(page, dict):

            content = clean_text(
                page.get("content")
                or page.get("text")
                or page.get("document_text")
                or ""
            )

            if not content:
                continue

            try:
                page_number = int(
                    page.get("page_number")
                    or position
                )
            except Exception:
                page_number = position

            result.append(
                {
                    **page,
                    "page_number": page_number,
                    "position": position,
                    "content": content,
                }
            )

        elif isinstance(page, str):

            content = clean_text(page)

            if content:
                result.append(
                    {
                        "page_number": position,
                        "position": position,
                        "content": content,
                    }
                )

    # Always make numbering sequential.
    for position, page in enumerate(result, 1):
        page["page_number"] = position
        page["position"] = position

    return result


def split_long_text(text: str) -> list[str]:
    """
    Split one very large text block into logical document pages.

    Priority:
      1. paragraphs
      2. sentences
      3. hard character boundary
    """

    text = clean_text(text)

    if not text:
        return []

    if len(text) <= PAGE_TARGET_CHARS:
        return [text]

    paragraphs = [
        clean_text(p)
        for p in re.split(r"\n\s*\n+", text)
        if clean_text(p)
    ]

    pages: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush():
        nonlocal current
        nonlocal current_length

        if current:
            value = "\n\n".join(current).strip()

            if value:
                pages.append(value)

        current = []
        current_length = 0

    for paragraph in paragraphs:

        # If one paragraph itself is huge, split it.
        if len(paragraph) > PAGE_TARGET_CHARS:

            flush()

            sentences = re.split(
                r"(?<=[.!?])\s+",
                paragraph,
            )

            sentence_buffer: list[str] = []
            sentence_length = 0

            for sentence in sentences:
                sentence = sentence.strip()

                if not sentence:
                    continue

                if (
                    sentence_buffer
                    and sentence_length + len(sentence) + 1
                    > PAGE_TARGET_CHARS
                ):
                    pages.append(
                        " ".join(sentence_buffer).strip()
                    )

                    sentence_buffer = []
                    sentence_length = 0

                sentence_buffer.append(sentence)
                sentence_length += len(sentence) + 1

            if sentence_buffer:
                pages.append(
                    " ".join(sentence_buffer).strip()
                )

            continue

        proposed = (
            current_length
            + len(paragraph)
            + (2 if current else 0)
        )

        if (
            current
            and proposed > PAGE_TARGET_CHARS
            and current_length >= MIN_PAGE_CHARS
        ):
            flush()

        current.append(paragraph)
        current_length += (
            len(paragraph)
            + (2 if current_length else 0)
        )

    flush()

    return [
        page
        for page in pages
        if clean_text(page)
    ]


def paginate_text(text: str) -> list[dict[str, Any]]:
    """
    Convert one generated document string into a complete
    collection of logical pages.

    Explicit page markers are respected first.
    """

    text = clean_text(text)

    if not text:
        return []

    # --------------------------------------------------------
    # Respect explicit page breaks.
    # --------------------------------------------------------

    explicit = re.split(
        r"(?:\f|"
        r"\n\s*(?:---+\s*)?"
        r"\[?\s*PAGE\s+\d+\s*(?:OF\s+\d+)?\s*\]?"
        r"\s*(?:---+)?\s*\n)",
        text,
        flags=re.IGNORECASE,
    )

    explicit = [
        clean_text(p)
        for p in explicit
        if clean_text(p)
    ]

    if len(explicit) > 1:

        pages: list[str] = []

        for part in explicit:
            pages.extend(
                split_long_text(part)
            )

    else:

        # ----------------------------------------------------
        # Respect markdown section structure where practical.
        # ----------------------------------------------------

        pages = split_long_text(text)

    result: list[dict[str, Any]] = []

    for position, content in enumerate(pages, 1):
        result.append(
            {
                "page_number": position,
                "position": position,
                "content": content,
            }
        )

    return result


def ensure_document_pages(value: Any) -> list[dict[str, Any]]:
    """
    Critical document boundary.

    Whatever AdaResponse returns, this function guarantees that
    the application receives a complete collection of pages.
    """

    # --------------------------------------------------------
    # Already structured pages.
    # --------------------------------------------------------

    if isinstance(value, list):

        pages = normalize_pages(value)

        if pages:

            # If the provider gave us one giant page, paginate it.
            if len(pages) == 1:
                content = pages[0]["content"]

                if len(content) > PAGE_TARGET_CHARS:
                    return paginate_text(content)

            return pages

    # --------------------------------------------------------
    # Structured response dictionaries.
    # --------------------------------------------------------

    if isinstance(value, dict):

        for key in (
            "pages",
            "document_pages",
            "prepared_pages",
            "content_pages",
        ):
            candidate = value.get(key)

            if isinstance(candidate, list):

                pages = normalize_pages(candidate)

                if pages:

                    if len(pages) == 1:
                        content = pages[0]["content"]

                        if len(content) > PAGE_TARGET_CHARS:
                            return paginate_text(content)

                    return pages

        for key in (
            "document_text",
            "prepared_work",
            "document",
            "content",
            "text",
            "reply",
            "response",
            "message",
        ):
            candidate = value.get(key)

            if isinstance(candidate, str):
                candidate = clean_text(candidate)

                if candidate:
                    return paginate_text(candidate)

    # --------------------------------------------------------
    # Plain string response.
    # --------------------------------------------------------

    if isinstance(value, str):

        text = clean_text(value)

        if text:
            return paginate_text(text)

    raise ValueError(
        "AdaResponse returned no usable document work."
    )


# ============================================================
# STORED PAGE REPRESENTATION
# ============================================================

def stored(pages: Any) -> list[dict[str, Any]]:
    """
    Re-number and store the complete page collection.

    No page is discarded simply because the frontend displays
    one page at a time.
    """

    pages = ensure_document_pages(pages)

    result: list[dict[str, Any]] = []

    for position, page in enumerate(pages, 1):

        content = clean_text(
            page.get("content")
        )

        if not content:
            continue

        result.append(
            {
                **page,
                "page_number": position,
                "position": position,
                "content": content,
            }
        )

    return result


# ============================================================
# REVIEW PAGE STATE
# ============================================================

def build_review_pages(
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    result = []

    for position, page in enumerate(pages, 1):

        result.append(
            {
                "page_number": position,
                "position": position,
                "status": "queued",
                "content": clean_text(
                    page.get("content")
                ),
                "review": "",
                "error": None,
            }
        )

    return result


# ============================================================
# JOB RESPONSE
# ============================================================

def job_response(job: dict[str, Any]) -> dict[str, Any]:

    pages = stored(
        job.get("document_pages", [])
    )

    job["document_pages"] = pages

    review_pages = job.get(
        "review_pages",
        [],
    )

    total = len(pages)

    completed = int(
        job.get("progress", {}).get(
            "completed",
            0,
        )
        or 0
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
            "completed": min(
                completed,
                total,
            ),
            "total": total,
        },

        "total_pages": total,

        # The complete document collection.
        "document_pages": pages,
        "pages": pages,

        # The independent review state for each page.
        "review_pages": review_pages,

        "assembled_review": job.get(
            "assembled_review",
            "",
        ),

        "error": job.get(
            "review_error"
        ),

        "review_url":
            f"/review.html?job_id="
            f"{job['job_id']}",
    }


# ============================================================
# JOB CREATION
# ============================================================

def new_job(
    job_id: str,
    request: Chat,
    original_request: str,
    pages: Any,
) -> dict[str, Any]:

    pages = stored(pages)

    if not pages:
        raise ValueError(
            "Cannot create a job without document pages."
        )

    job = {
        "job_id": job_id,

        "customer_id": request.customer_id,
        "service": request.service,

        "original_request": original_request,

        "context": request_context(request),

        "client_request_id":
            request.client_request_id,

        "status": "reviewing",

        "review_started": True,
        "review_finished": False,

        "review_error": None,

        "progress": {
            "completed": 0,
            "total": len(pages),
        },

        "document_pages": pages,

        "review_pages":
            build_review_pages(pages),

        "assembled_review": "",

        "current_version": 1,

        "version_id":
            f"{job_id}:1",

        "approved": False,
        "paid": False,
    }

    _jobs[job_id] = job

    return job


# ============================================================
# REVIEW CALLBACK
# ============================================================

def review_callback(job_id: str):

    def callback(update: dict[str, Any]):

        job = _jobs.get(job_id)

        if not job:
            return

        event_type = ev(
            update.get("type")
        )

        page_number = str(
            update.get("page_number", "")
        )

        if event_type == "page_started":

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == page_number:

                    page["status"] = "reviewing"

        elif event_type == "page_completed":

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == page_number:

                    page["status"] = "reviewed"

                    page["review"] = str(
                        update.get(
                            "review",
                            "",
                        )
                        or ""
                    )

                    page["content"] = clean_text(
                        update.get(
                            "content",
                            page["content"],
                        )
                    )

                    page["error"] = None

            try:
                position = int(
                    update.get(
                        "position",
                        0,
                    )
                    or 0
                )
            except Exception:
                position = 0

            if position:
                job["progress"]["completed"] = min(
                    position,
                    len(job["document_pages"]),
                )

        elif event_type == "page_error":

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == page_number:

                    page["status"] = "error"

                    page["error"] = str(
                        update.get(
                            "error",
                            "Page review failed.",
                        )
                    )

        elif event_type == "review_completed":

            job["status"] = "review_complete"

            job["review_started"] = True
            job["review_finished"] = True

            job["progress"] = {
                "completed":
                    len(job["document_pages"]),
                "total":
                    len(job["document_pages"]),
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
# RUN REVIEW
# ============================================================

async def run_review(job_id: str):

    job = _jobs.get(job_id)

    if not job:
        return

    try:

        assistant = get_session(
            job.get("customer_id"),
            job_id,
            job.get("service"),
        )

        pages = stored(
            job["document_pages"]
        )

        if not pages:
            raise ValueError(
                "There are no document pages available for review."
            )

        result = await asyncio.to_thread(
            assistant.review_document_pages,
            pages=pages,
            service=job.get("service"),
            context=job.get("context"),
            customer_request=job.get(
                "original_request"
            ),
            event="send_for_review",
            progress_callback=
                review_callback(job_id),
        )

        if not isinstance(result, dict):
            raise TypeError(
                "Invalid review result returned by AdaResponse."
            )

        returned_pages = result.get(
            "pages",
            [],
        )

        if isinstance(returned_pages, list):

            for reviewed_page in returned_pages:

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

                    if str(
                        page["page_number"]
                    ) == reviewed_number:

                        if "review" in reviewed_page:
                            page["review"] = str(
                                reviewed_page.get(
                                    "review",
                                    "",
                                )
                                or ""
                            )

                        if "content" in reviewed_page:
                            page["content"] = clean_text(
                                reviewed_page.get(
                                    "content",
                                    "",
                                )
                            )

                        page["status"] = "reviewed"
                        page["error"] = None

        # ----------------------------------------------------
        # Important:
        # Do not lose pages returned by the original job.
        # ----------------------------------------------------

        job["assembled_review"] = str(
            result.get(
                "assembled_review",
                "",
            )
            or ""
        )

        job["status"] = "review_complete"
        job["review_started"] = True
        job["review_finished"] = True
        job["review_error"] = None

        job["progress"] = {
            "completed":
                len(job["document_pages"]),
            "total":
                len(job["document_pages"]),
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


# ============================================================
# START REVIEW
# ============================================================

def start_review(job_id: str) -> bool:

    job = _jobs.get(job_id)

    if not job:
        return False

    if not job.get("document_pages"):
        return False

    if job.get("status") not in {
        "reviewing",
        "correction_review",
    }:
        return False

    existing = _review_tasks.get(job_id)

    if existing and not existing.done():
        return False

    _review_tasks[job_id] = (
        asyncio.create_task(
            run_review(job_id)
        )
    )

    return True


# ============================================================
# DOCUMENT EXTRACTION
# ============================================================

def extract(
    data: bytes,
    filename: str,
) -> str:

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
            texts: list[str] = []

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

                pattern = patterns[
                    suffix[1:]
                ]

                names = [
                    name
                    for name in names
                    if re.match(
                        pattern,
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
                        and
                        element.tag.rsplit(
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
        f"{suffix or 'unknown'}"
    )


def upload_pages(
    filename: str,
    data: bytes,
) -> list[dict[str, Any]]:

    text = clean_text(
        extract(
            data,
            filename,
        )
    )

    if not text:
        raise ValueError(
            "The uploaded document contains "
            "no extractable text."
        )

    # Uploaded documents first use the document parser.
    parsed = document_text_to_pages(text)

    pages = normalize_pages(parsed)

    # If the parser collapsed the entire document
    # into one huge page, paginate it here.
    if len(pages) == 1:

        content = pages[0]["content"]

        if len(content) > PAGE_TARGET_CHARS:
            pages = paginate_text(content)

    if not pages:
        pages = paginate_text(text)

    return stored(pages)


# ============================================================
# GENERATED DOCUMENT EXTRACTION
# ============================================================

def generated_pages(result: Any) -> list[dict[str, Any]]:
    """
    Convert every possible AdaResponse creation result into
    the authoritative complete page collection.
    """

    return stored(
        ensure_document_pages(result)
    )


# ============================================================
# DOCUMENT CREATION
# ============================================================

async def create_work(
    assistant: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
) -> list[dict[str, Any]]:
    """
    Use only actual document-creation methods exposed by
    AdaResponse.

    IMPORTANT:
    There is deliberately NO call to:
        respond(..., create_work=True)

    because respond() is a conversational method and does not
    necessarily accept the create_work argument.
    """

    # --------------------------------------------------------
    # Preferred structured creation methods.
    # --------------------------------------------------------

    for method_name in (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    ):

        method = getattr(
            assistant,
            method_name,
            None,
        )

        if not callable(method):
            continue

        try:

            result = await asyncio.to_thread(
                method,
                customer_request=
                    customer_request,
                service=request.service,
                form_data=request.form_data,
                context=context,
                event=request.event,
            )

            return generated_pages(result)

        except TypeError as exception:

            # Some deployed versions may expose a simpler
            # signature. Retry without form_data/event.
            if (
                "unexpected keyword argument"
                not in str(exception)
            ):
                raise

            result = await asyncio.to_thread(
                method,
                customer_request=
                    customer_request,
                service=request.service,
                context=context,
            )

            return generated_pages(result)

    # --------------------------------------------------------
    # No document creator exists.
    #
    # We DO NOT abuse respond() with create_work=True.
    # --------------------------------------------------------

    raise AttributeError(
        "AdaResponse does not expose a supported "
        "document creation method. Expected one of: "
        "create_document, generate_document, "
        "create_work, generate_work."
    )


# ============================================================
# HTML PAGES
# ============================================================

def html(name: str):

    path = find_file(name)

    if not path:
        return err(
            "PAGE",
            f"{name} was not found.",
            404,
            "HTML_NOT_FOUND",
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


@app.get("/")
async def root():
    return html("index.html")


@app.get("/index.html")
async def index():
    return html("index.html")


@app.get("/conversation.html")
async def conversation():
    return html("conversation.html")


@app.get("/workspace.html")
async def workspace():
    return html("workspace.html")


@app.get("/review.html")
async def review_page():
    return html("review.html")


@app.get("/payment.html")
async def payment_page():
    return html("payment.html")


@app.get("/download.html")
async def download_page():
    return html("download.html")


# ============================================================
# STATUS
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
        "workflow": "complete_document_page_collection",
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

        "active_reviews":
            sum(
                1
                for task in _review_tasks.values()
                if not task.done()
            ),

        "active_corrections":
            sum(
                1
                for task in _correction_tasks.values()
                if not task.done()
            ),
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
                "The uploaded file is empty.",
                400,
                "EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD:
            return err(
                "UPLOAD",
                "The uploaded document is too large.",
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

            "filename":
                file.filename,

            "job_id":
                job_id_value,

            "customer_id":
                customer_id,

            "client_request_id":
                client_request_id,

            "service":
                service,

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
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(request: Chat):

    if not request.activate_intelligence:

        return err(
            "INTELLIGENCE",
            "Intelligence activation is disabled.",
            400,
            "INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():

        return err(
            "INTELLIGENCE",
            "AdaResponse is not configured.",
            503,
            "INTELLIGENCE_NOT_CONFIGURED",
        )

    job_id = (
        str(request.job_id or "").strip()
        or str(uuid.uuid4())
    )

    application_context = request_context(
        request
    )

    # --------------------------------------------------------
    # Authoritative document pages from frontend.
    # --------------------------------------------------------

    pages = stored(
        request.document_pages or []
    )

    # If frontend supplied raw document text,
    # turn it into a complete page collection.
    if (
        not pages
        and request.document_text
        and request.document_text.strip()
    ):

        pages = paginate_text(
            request.document_text
        )

    try:

        assistant = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        # ----------------------------------------------------
        # Guidance-only conversational response.
        # ----------------------------------------------------

        if request.guidance_only:

            if not request.message.strip():

                return err(
                    "GUIDANCE",
                    "The guidance message is empty.",
                    400,
                    "EMPTY_GUIDANCE_MESSAGE",
                )

            reply = await asyncio.to_thread(
                assistant.respond,
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

        customer_request = form_request(
            request
        )

        # ----------------------------------------------------
        # Determine whether the service form is requesting
        # actual document creation.
        # ----------------------------------------------------

        creation_requested = (
            request.create_work
            or ev(request.event)
            in {
                "form_submitted_create_work",
                "create_work",
                "create_document",
            }
        )

        # ----------------------------------------------------
        # FORM -> DOCUMENT CREATION -> REVIEW
        # ----------------------------------------------------

        if creation_requested and not pages:

            if not customer_request:

                return err(
                    "WORK_CREATION",
                    "The customer service request "
                    "contains no usable information.",
                    400,
                    "EMPTY_WORK_REQUEST",
                )

            made_pages = await create_work(
                assistant,
                request,
                customer_request,
                application_context,
            )

            # This is the critical boundary:
            #
            # Whatever the intelligence generated is converted
            # into a COMPLETE page collection before the job
            # exists.
            made_pages = stored(
                made_pages
            )

            if not made_pages:

                raise ValueError(
                    "Document creation returned "
                    "no document pages."
                )

            job = new_job(
                job_id,
                request,
                customer_request,
                made_pages,
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

        # ----------------------------------------------------
        # EXISTING DOCUMENT -> REVIEW
        #
        # Any request carrying authoritative document pages
        # enters the document review pipeline.
        # ----------------------------------------------------

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

                existing_task = (
                    _review_tasks.get(
                        job_id
                    )
                )

                if (
                    existing_task
                    and not existing_task.done()
                    and job.get("status")
                    in {
                        "reviewing",
                        "correction_review",
                    }
                ):
                    return job_response(
                        job
                    )

                pages = stored(
                    pages
                )

                job.update(
                    {
                        "document_pages":
                            pages,

                        "review_pages":
                            build_review_pages(
                                pages
                            ),

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
                                "total": len(pages),
                            },

                        "customer_id":
                            request.customer_id,

                        "service":
                            request.service
                            or job.get(
                                "service"
                            ),

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
                        "The complete document is now being "
                        "reviewed page by page.",

                    "created_work":
                        True,

                    "review_started":
                        started,
                }
            )

            return output

        # ----------------------------------------------------
        # NORMAL CHAT
        # ----------------------------------------------------

        if not request.message.strip():

            return err(
                "CHAT",
                "The chat message is empty.",
                400,
                "EMPTY_MESSAGE",
            )

        reply = await asyncio.to_thread(
            assistant.respond,
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
                or assistant.service,

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
# REVIEW
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
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        job.get("status")
        in {
            "reviewing",
            "correction_review",
        }
    ):
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
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        job.get("status")
        in {
            "reviewing",
            "correction_review",
        }
    ):
        start_review(
            job_id
        )

    pages = stored(
        job.get(
            "document_pages",
            [],
        )
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
            len(pages),

        # Complete authoritative document.
        "pages":
            pages,

        "document_pages":
            pages,

        # Independent review state.
        "review_pages":
            job["review_pages"],

        "progress":
            job["progress"],

        "approved":
            job["approved"],

        "paid":
            job["paid"],

        "review_error":
            job.get(
                "review_error"
            ),

        "assembled_review":
            job.get(
                "assembled_review",
                "",
            ),
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
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if not instruction:

        return err(
            "CORRECTION",
            "Correction instruction is empty.",
            400,
            "EMPTY_CORRECTION",
        )

    if job.get("status") in {
        "reviewing",
        "correction_review",
        "correcting",
    }:

        return err(
            "CORRECTION",
            "The document is still being processed.",
            409,
            "DOCUMENT_STILL_PROCESSING",
        )

    if not job.get(
        "document_pages"
    ):

        return err(
            "CORRECTION",
            "There is no document available for correction.",
            409,
            "NO_DOCUMENT",
        )

    # --------------------------------------------------------
    # New document version.
    # --------------------------------------------------------

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

    async def correction_worker():

        try:

            assistant = get_session(
                job.get(
                    "customer_id"
                ),
                request.job_id,
                job.get(
                    "service"
                ),
            )

            result = await asyncio.to_thread(
                assistant.correct_document,

                document_pages=
                    stored(
                        job["document_pages"]
                    ),

                correction=
                    instruction,

                service=
                    job.get(
                        "service"
                    ),

                context=
                    job.get(
                        "context"
                    ),

                progress_callback=None,
            )

            corrected_pages = generated_pages(
                result
            )

            if not corrected_pages:

                raise ValueError(
                    "Correction returned no document pages."
                )

            job["document_pages"] = (
                stored(
                    corrected_pages
                )
            )

            job["review_pages"] = (
                build_review_pages(
                    job["document_pages"]
                )
            )

            job["assembled_review"] = ""

            job["status"] = (
                "correction_review"
            )

            job["review_started"] = True
            job["review_finished"] = False

            job["progress"] = {
                "completed": 0,
                "total":
                    len(
                        job["document_pages"]
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
        correction_worker()
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
            "The corrected complete document "
            "will be reviewed again page by page.",
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
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        request.version_id
        != job["version_id"]
    ):

        return err(
            "APPROVAL",
            "The supplied document version does not match.",
            409,
            "VERSION_MISMATCH",
        )

    if (
        job["status"]
        != "review_complete"
    ):

        return err(
            "APPROVAL",
            "The document review is not complete.",
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
            f"/payment.html?"
            f"job_id={request.job_id}"
            f"&version_id={request.version_id}",
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
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return err(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job["approved"]:

        return err(
            "PAYMENT",
            "The document must be approved before payment.",
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
            f"/download.html?"
            f"job_id={job_id}"
            f"&version_id={version_id}",

        "api_download_url":
            f"/api/download?"
            f"job_id={job_id}"
            f"&version_id={version_id}",
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
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return err(
            "PAYMENT_STATE",
            "Version mismatch.",
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
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return err(
            "DOWNLOAD",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job["approved"]:

        return err(
            "DOWNLOAD",
            "The current document version "
            "has not been approved.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    if not job["paid"]:

        return err(
            "DOWNLOAD",
            "Payment for the current document "
            "version has not been completed.",
            409,
            "PAYMENT_NOT_COMPLETED",
        )

    pages = stored(
        job["document_pages"]
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
            len(pages),

        "pages":
            pages,

        "document_pages":
            pages,

        "message":
            "The approved and paid complete document "
            "is ready for final document generation.",
    }


# ============================================================
# CLEAR CONVERSATION
# ============================================================

@app.post("/api/chat/clear")
async def clear(
    customer_id: str | None = None,
    job_id: str | None = None,
):

    assistant = _sessions.get(
        make_key(
            customer_id,
            job_id,
        )
    )

    if assistant:

        try:
            assistant.clear_history()
        except Exception:
            pass

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
        "Complete document page collection: ENABLED"
    )

    print(
        "Page-by-page review: ENABLED"
    )

    print(
        "Logical pagination:",
        PAGE_TARGET_CHARS,
        "characters/page"
    )

    print(
        "Keyword intelligence: DISABLED"
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
