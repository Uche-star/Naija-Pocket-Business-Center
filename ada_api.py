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
from pydantic import BaseModel, Field

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

BASE = Path(__file__).resolve().parent


# ============================================================
# APPLICATION
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
# RUNTIME STATE
# ============================================================

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}

_review_tasks: dict[str, asyncio.Task] = {}
_correction_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# FILE / SESSION HELPERS
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


def clean(value: Any) -> str:
    return str(value or "").strip()


def event_name(value: Any) -> str:
    return clean(value).lower()


def job_key(
    customer_id: str | None,
    job_id: str | None,
) -> str:
    customer = clean(customer_id) or "anonymous"
    job = clean(job_id) or "default"
    return f"{customer}:{job}"


def get_session(
    customer_id: str | None,
    job_id: str | None,
    service: str | None = None,
) -> AdaResponse:

    k = job_key(customer_id, job_id)

    current = _sessions.get(k)

    if current is None:
        current = AdaResponse(service=service)
        _sessions[k] = current

    elif service:
        try:
            current.set_service(service)
        except Exception:
            pass

    return current


# Keep the old helper name available internally.
session = get_session


# ============================================================
# ERROR HANDLING
# ============================================================

def error_response(
    stage: str,
    error: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):
    message = str(error)

    print(
        f"[{stage}] "
        f"{type(error).__name__ if isinstance(error, Exception) else 'ERROR'}: "
        f"{message}"
    )

    if isinstance(error, Exception):
        traceback.print_exc()

    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": code,
            "error_type": (
                type(error).__name__
                if isinstance(error, Exception)
                else "ApplicationError"
            ),
            "error_message": (
                message
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


err = error_response


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

def build_form_request(request: Chat) -> str:
    parts: list[str] = []

    if clean(request.service):
        parts.append(
            "SELECTED SERVICE:\n"
            + clean(request.service)
        )

    if request.form_data:
        lines: list[str] = []

        for key, value in request.form_data.items():
            if clean(value):
                label = (
                    str(key)
                    .replace("_", " ")
                    .strip()
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

    if clean(request.context):
        parts.append(
            "ADDITIONAL CONTEXT:\n"
            + clean(request.context)
        )

    if clean(request.message):
        parts.append(
            "CUSTOMER REQUEST:\n"
            + clean(request.message)
        )

    return "\n\n".join(parts).strip()


def build_context(request: Chat) -> str | None:
    parts: list[str] = []

    if clean(request.context):
        parts.append(clean(request.context))

    if clean(request.customer_id):
        parts.append(
            "CUSTOMER ID:\n"
            + clean(request.customer_id)
        )

    if clean(request.client_request_id):
        parts.append(
            "CLIENT REQUEST ID:\n"
            + clean(request.client_request_id)
        )

    result = "\n\n".join(parts).strip()

    return result or None


# Preserve old function names.
form_request = build_form_request
ctx = build_context


# ============================================================
# DOCUMENT PAGE NORMALIZATION
# ============================================================

def stored_pages(pages: list[Any] | None) -> list[dict[str, Any]]:
    """
    Convert every document page into one stable internal format.

    The document content itself is authoritative here.

    Every page receives:
        page_number
        position
        content

    Additional metadata from the intelligence layer is preserved.
    """

    normalized = normalize_document_pages(pages or [])

    result: list[dict[str, Any]] = []

    for position, page in enumerate(normalized, start=1):

        if not isinstance(page, dict):
            page = {
                "content": clean(page)
            }

        page_number = page.get(
            "page_number",
            position,
        )

        try:
            page_number = int(page_number or position)
        except Exception:
            page_number = position

        result.append(
            {
                **page,
                "page_number": page_number,
                "position": position,
                "content": clean(
                    page.get("content", "")
                ),
            }
        )

    return result


stored = stored_pages


def make_review_pages(
    pages: list[Any] | None,
) -> list[dict[str, Any]]:

    source = stored_pages(pages)

    result: list[dict[str, Any]] = []

    for position, page in enumerate(
        source,
        start=1,
    ):
        result.append(
            {
                "page_number": page.get(
                    "page_number",
                    position,
                ),
                "position": position,
                "status": "queued",
                "content": clean(
                    page.get("content", "")
                ),
                "review": "",
                "error": None,
            }
        )

    return result


review_pages = make_review_pages


# ============================================================
# JOB CREATION
# ============================================================

def create_job(
    job_id: str,
    request: Chat,
    original_request: str,
    pages: list[Any],
) -> dict[str, Any]:

    normalized_pages = stored_pages(pages)

    if not normalized_pages:
        raise ValueError(
            "Cannot create a job without document pages."
        )

    job = {
        "job_id": job_id,

        "customer_id": request.customer_id,

        "service": request.service,

        "original_request": original_request,

        "context": build_context(request),

        "client_request_id": request.client_request_id,

        "status": "reviewing",

        "review_started": True,

        "review_finished": False,

        "review_error": None,

        "progress": {
            "completed": 0,
            "total": len(normalized_pages),
        },

        # AUTHORITATIVE DOCUMENT
        "document_pages": normalized_pages,

        # PAGE REVIEW STATE
        "review_pages": make_review_pages(
            normalized_pages
        ),

        "assembled_review": "",

        "current_version": 1,

        "version_id": f"{job_id}:1",

        "approved": False,

        "paid": False,

        "created_work": True,
    }

    _jobs[job_id] = job

    return job


new_job = create_job


# ============================================================
# JOB RESPONSE
# ============================================================

def job_response(job: dict[str, Any]) -> dict[str, Any]:

    pages = stored_pages(
        job.get("document_pages", [])
    )

    job["document_pages"] = pages

    progress = job.get(
        "progress",
        {},
    )

    return {
        "success": True,

        "job_id": job["job_id"],

        "customer_id": job.get(
            "customer_id"
        ),

        "service": job.get(
            "service"
        ),

        "status": job.get(
            "status"
        ),

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

            "total": len(pages),
        },

        "total_pages": len(pages),

        # Both names are deliberately returned.
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

        "review_url": (
            f"/review.html?job_id="
            f"{job['job_id']}"
        ),
    }


# ============================================================
# REVIEW CALLBACK
# ============================================================

def review_callback(job_id: str):

    def callback(update: dict[str, Any]):

        job = _jobs.get(job_id)

        if not job:
            return

        if not isinstance(update, dict):
            return

        update_type = event_name(
            update.get("type")
        )

        page_number = clean(
            update.get("page_number")
        )

        review_pages_state = job.setdefault(
            "review_pages",
            make_review_pages(
                job.get("document_pages", [])
            ),
        )

        if update_type == "review_started":

            job["status"] = "reviewing"
            job["review_started"] = True
            job["review_finished"] = False

            return

        if update_type == "page_started":

            for page in review_pages_state:

                if clean(
                    page.get("page_number")
                ) == page_number:

                    page["status"] = "reviewing"

                    break

            return

        if update_type == "page_completed":

            for page in review_pages_state:

                if clean(
                    page.get("page_number")
                ) == page_number:

                    page["status"] = "reviewed"

                    if "review" in update:
                        page["review"] = clean(
                            update.get("review")
                        )

                    if "content" in update:
                        page["content"] = clean(
                            update.get("content")
                        )

                    page["error"] = None

                    break

            position = update.get(
                "position"
            )

            try:
                completed = int(position)
            except Exception:
                completed = int(
                    job.get(
                        "progress",
                        {},
                    ).get(
                        "completed",
                        0,
                    )
                ) + 1

            total = len(
                job.get(
                    "document_pages",
                    [],
                )
            )

            job["progress"] = {
                "completed": min(
                    completed,
                    total,
                ),
                "total": total,
            }

            return

        if update_type == "page_error":

            for page in review_pages_state:

                if clean(
                    page.get("page_number")
                ) == page_number:

                    page["status"] = "error"

                    page["error"] = clean(
                        update.get(
                            "error",
                            "Page review failed.",
                        )
                    )

                    break

            return

        if update_type == "review_completed":

            total = len(
                job.get(
                    "document_pages",
                    [],
                )
            )

            job["status"] = "review_complete"

            job["review_started"] = True

            job["review_finished"] = True

            job["review_error"] = None

            job["progress"] = {
                "completed": total,
                "total": total,
            }

            job["assembled_review"] = clean(
                update.get(
                    "assembled_review",
                    "",
                )
            )

    return callback


cb_review = review_callback


# ============================================================
# REVIEW ENGINE
# ============================================================

async def run_review(job_id: str):

    job = _jobs.get(job_id)

    if not job:
        return

    try:

        pages = stored_pages(
            job.get(
                "document_pages",
                [],
            )
        )

        if not pages:
            raise ValueError(
                "There are no document pages available for review."
            )

        job["document_pages"] = pages

        job["review_pages"] = make_review_pages(
            pages
        )

        job["status"] = "reviewing"

        job["review_started"] = True

        job["review_finished"] = False

        job["review_error"] = None

        job["progress"] = {
            "completed": 0,
            "total": len(pages),
        }

        ada = get_session(
            job.get("customer_id"),
            job_id,
            job.get("service"),
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
            progress_callback=review_callback(
                job_id
            ),
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

                returned_number = clean(
                    reviewed_page.get(
                        "page_number"
                    )
                )

                for page in job["review_pages"]:

                    if clean(
                        page.get(
                            "page_number"
                        )
                    ) == returned_number:

                        if "review" in reviewed_page:
                            page["review"] = clean(
                                reviewed_page.get(
                                    "review"
                                )
                            )

                        if "content" in reviewed_page:
                            page["content"] = clean(
                                reviewed_page.get(
                                    "content"
                                )
                            )

                        page["status"] = "reviewed"

                        page["error"] = None

                        break

        job["assembled_review"] = clean(
            result.get(
                "assembled_review",
                "",
            )
        )

        total = len(
            job.get(
                "document_pages",
                [],
            )
        )

        job["progress"] = {
            "completed": total,
            "total": total,
        }

        job["status"] = "review_complete"

        job["review_started"] = True

        job["review_finished"] = True

        job["review_error"] = None

    except asyncio.CancelledError:
        raise

    except Exception as exc:

        job["status"] = "review_error"

        job["review_finished"] = True

        job["review_error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }

        traceback.print_exc()


def start_review(job_id: str) -> bool:

    job = _jobs.get(job_id)

    if not job:
        return False

    pages = job.get(
        "document_pages",
        [],
    )

    if not pages:
        return False

    if job.get("status") not in {
        "reviewing",
        "review_error",
        "review_complete",
    }:
        return False

    existing = _review_tasks.get(job_id)

    if existing and not existing.done():

        return False

    # Do not restart an already completed review.
    if (
        job.get("status") == "review_complete"
        and job.get("review_finished")
    ):
        return False

    _review_tasks[job_id] = asyncio.create_task(
        run_review(job_id)
    )

    return True


# ============================================================
# DOCUMENT EXTRACTION
# ============================================================

def extract_document(
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

        pages: list[str] = []

        for page in reader.pages:

            pages.append(
                page.extract_text() or ""
            )

        return "\n\n".join(pages)

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

            if suffix == ".docx":

                target = (
                    "word/document.xml"
                )

                names = (
                    [target]
                    if target in names
                    else []
                )

            elif suffix == ".pptx":

                names = sorted(
                    name
                    for name in names
                    if re.match(
                        r"ppt/slides/slide\d+\.xml",
                        name,
                    )
                )

            elif suffix == ".xlsx":

                names = sorted(
                    name
                    for name in names
                    if re.match(
                        r"xl/worksheets/sheet\d+\.xml",
                        name,
                    )
                )

            for name in names:

                root = ET.fromstring(
                    archive.read(name)
                )

                values = [
                    node.text or ""
                    for node in root.iter()
                    if (
                        isinstance(
                            node.tag,
                            str,
                        )
                        and node.tag.rsplit(
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


extract = extract_document


def upload_to_pages(
    filename: str,
    data: bytes,
) -> list[dict[str, Any]]:

    text = extract_document(
        data,
        filename,
    ).strip()

    if not text:
        raise ValueError(
            "The uploaded document contains no extractable text."
        )

    return stored_pages(
        document_text_to_pages(text)
    )


upload_pages = upload_to_pages


# ============================================================
# GENERATED WORK EXTRACTION
# ============================================================

def generated_pages(
    result: Any,
) -> list[dict[str, Any]]:

    # --------------------------------------------------------
    # Structured page results are preferred.
    # --------------------------------------------------------

    if isinstance(result, dict):

        for key in (
            "pages",
            "document_pages",
            "prepared_pages",
            "content_pages",
        ):

            candidate = result.get(key)

            if isinstance(
                candidate,
                list,
            ):

                pages = stored_pages(
                    candidate
                )

                if pages:
                    return pages

    # --------------------------------------------------------
    # If the intelligence layer returned document text,
    # convert it into authoritative pages.
    # --------------------------------------------------------

    if isinstance(result, dict):

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

            value = result.get(key)

            if (
                isinstance(value, str)
                and value.strip()
            ):

                pages = stored_pages(
                    document_text_to_pages(
                        value
                    )
                )

                if pages:
                    return pages

    # --------------------------------------------------------
    # Direct string result.
    # --------------------------------------------------------

    if (
        isinstance(result, str)
        and result.strip()
    ):

        pages = stored_pages(
            document_text_to_pages(
                result
            )
        )

        if pages:
            return pages

    raise ValueError(
        "AdaResponse returned no usable document work."
    )


# ============================================================
# DOCUMENT CREATION
# ============================================================

async def create_work(
    ada: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
) -> list[dict[str, Any]]:

    """
    Ask the intelligence layer to create the customer's
    requested document.

    No keyword document generation is performed here.
    """

    method_names = (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    )

    for method_name in method_names:

        method = getattr(
            ada,
            method_name,
            None,
        )

        if not callable(method):
            continue

        try:

            result = await asyncio.to_thread(
                method,
                customer_request=customer_request,
                service=request.service,
                form_data=request.form_data,
                context=context,
                event=request.event,
            )

            return generated_pages(result)

        except TypeError:

            try:

                result = await asyncio.to_thread(
                    method,
                    message=customer_request,
                    service=request.service,
                    context=context,
                    event=request.event,
                )

                return generated_pages(result)

            except TypeError:
                continue

    # --------------------------------------------------------
    # Compatibility fallback:
    # normal intelligence response can return structured work
    # when create_work=True.
    # --------------------------------------------------------

    respond = getattr(
        ada,
        "respond",
        None,
    )

    if not callable(respond):
        raise AttributeError(
            "AdaResponse has no document creation method."
        )

    result = await asyncio.to_thread(
        respond,
        message=customer_request,
        service=request.service,
        event=request.event,
        context=context,
        create_work=True,
        form_data=request.form_data,
    )

    return generated_pages(result)


# ============================================================
# HTML PAGES
# ============================================================

def html_page(
    filename: str,
):
    path = find_file(filename)

    if not path:

        return error_response(
            "PAGE",
            f"{filename} was not found.",
            404,
            "HTML_NOT_FOUND",
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


html = html_page


@app.get("/")
async def root():
    return html_page("index.html")


@app.get("/index.html")
async def index():
    return html_page("index.html")


@app.get("/conversation.html")
async def conversation():
    return html_page("conversation.html")


@app.get("/workspace.html")
async def workspace():
    return html_page("workspace.html")


@app.get("/review.html")
async def review_page():
    return html_page("review.html")


@app.get("/payment.html")
async def payment_page():
    return html_page("payment.html")


@app.get("/download.html")
async def download_page():
    return html_page("download.html")


# ============================================================
# HEALTH
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
        "active_sessions": len(_sessions),
        "active_jobs": len(_jobs),
        "active_reviews": sum(
            1
            for task in _review_tasks.values()
            if not task.done()
        ),
        "active_corrections": sum(
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

            return error_response(
                "UPLOAD",
                "The uploaded file is empty.",
                400,
                "EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD:

            return error_response(
                "UPLOAD",
                "The uploaded document is too large.",
                413,
                "FILE_TOO_LARGE",
            )

        filename = (
            file.filename
            or "document"
        )

        pages = await asyncio.to_thread(
            upload_to_pages,
            filename,
            data,
        )

        # IMPORTANT:
        # If the frontend supplied a job_id, preserve it.
        # Otherwise create one once and return it to the frontend.
        resolved_job_id = (
            clean(job_id)
            or str(uuid.uuid4())
        )

        return {
            "success": True,

            "filename": filename,

            "job_id": resolved_job_id,

            "customer_id": customer_id,

            "client_request_id": client_request_id,

            "service": service,

            "total_pages": len(pages),

            "document_pages": pages,

            "pages": pages,

            "status": "uploaded",

            "message": (
                "Document received successfully. "
                "The document pages are ready for review."
            ),
        }

    except Exception as exc:

        return error_response(
            "UPLOAD",
            exc,
            400,
            "DOCUMENT_UPLOAD_ERROR",
        )


# ============================================================
# CHAT / DOCUMENT WORK / REVIEW ENTRY POINT
# ============================================================

@app.post("/api/chat")
async def chat(request: Chat):

    # --------------------------------------------------------
    # INTELLIGENCE CHECK
    # --------------------------------------------------------

    if not request.activate_intelligence:

        return error_response(
            "INTELLIGENCE",
            "Intelligence activation is disabled.",
            400,
            "INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():

        return error_response(
            "INTELLIGENCE",
            "AdaResponse is not configured.",
            503,
            "INTELLIGENCE_NOT_CONFIGURED",
        )

    # --------------------------------------------------------
    # RESOLVE JOB ID ONCE.
    #
    # The job_id is the identity of the customer's document
    # workflow.
    # --------------------------------------------------------

    job_id = (
        clean(request.job_id)
        or str(uuid.uuid4())
    )

    context = build_context(request)

    # --------------------------------------------------------
    # DOCUMENT PAGES FROM REQUEST
    # --------------------------------------------------------

    pages = stored_pages(
        request.document_pages
        or []
    )

    # If frontend sends document_text instead of pages,
    # immediately turn it into pages.
    if (
        not pages
        and clean(request.document_text)
    ):

        pages = stored_pages(
            document_text_to_pages(
                clean(
                    request.document_text
                )
            )
        )

    try:

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        # ====================================================
        # GUIDANCE ONLY
        # ====================================================

        if request.guidance_only:

            if not clean(request.message):

                return error_response(
                    "GUIDANCE",
                    "The guidance message is empty.",
                    400,
                    "EMPTY_GUIDANCE_MESSAGE",
                )

            reply = await asyncio.to_thread(
                ada.respond,
                message=clean(
                    request.message
                ),
                service=request.service,
                event=request.event,
                context=context,
            )

            return {
                "success": True,
                "reply": clean(reply),
                "job_id": job_id,
                "created_work": False,
                "work_created": False,
            }

        # ====================================================
        # BUILD CUSTOMER REQUEST
        # ====================================================

        customer_request = build_form_request(
            request
        )

        # ====================================================
        # DOCUMENT CREATION REQUEST
        # ====================================================

        create_requested = (
            request.create_work
            or event_name(request.event)
            in {
                "form_submitted_create_work",
                "create_work",
                "create_document",
                "prepare_document",
                "prepare_work",
            }
        )

        # ----------------------------------------------------
        # If creation was requested and the frontend has not
        # already supplied authoritative pages, ask Ada to
        # create the work.
        # ----------------------------------------------------

        if create_requested and not pages:

            if not customer_request:

                return error_response(
                    "WORK_CREATION",
                    (
                        "The customer service request "
                        "contains no usable information."
                    ),
                    400,
                    "EMPTY_WORK_REQUEST",
                )

            created_pages = await create_work(
                ada=ada,
                request=request,
                customer_request=customer_request,
                context=context,
            )

            if not created_pages:

                raise ValueError(
                    "No document pages were created."
                )

            job = create_job(
                job_id=job_id,
                request=request,
                original_request=customer_request,
                pages=created_pages,
            )

            started = start_review(
                job_id
            )

            response = job_response(
                job
            )

            response.update(
                {
                    "reply": (
                        "Your request has been prepared "
                        "and sent into document review."
                    ),

                    "created_work": True,

                    "work_created": True,

                    "review_started": started,
                }
            )

            return response

        # ====================================================
        # DOCUMENT INTAKE / REVIEW
        # ====================================================
        #
        # If authoritative pages are present, do NOT treat
        # this as ordinary chat.
        #
        # This is the key document-flow rule.
        # ====================================================

        if pages:

            job = _jobs.get(
                job_id
            )

            if job is None:

                job = create_job(
                    job_id=job_id,
                    request=request,
                    original_request=customer_request,
                    pages=pages,
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
                    == "reviewing"
                ):

                    response = job_response(
                        job
                    )

                    response.update(
                        {
                            "reply": (
                                "Your document is already "
                                "being reviewed page by page."
                            ),

                            "created_work": True,

                            "work_created": True,

                            "review_started": True,
                        }
                    )

                    return response

                # --------------------------------------------
                # Replace the authoritative document with the
                # newly supplied pages.
                # --------------------------------------------

                job["document_pages"] = pages

                job["review_pages"] = (
                    make_review_pages(
                        pages
                    )
                )

                job["assembled_review"] = ""

                job["status"] = "reviewing"

                job["review_started"] = True

                job["review_finished"] = False

                job["review_error"] = None

                job["approved"] = False

                job["paid"] = False

                job["progress"] = {
                    "completed": 0,
                    "total": len(pages),
                }

                job["customer_id"] = (
                    request.customer_id
                    or job.get(
                        "customer_id"
                    )
                )

                job["service"] = (
                    request.service
                    or job.get(
                        "service"
                    )
                )

                if customer_request:
                    job["original_request"] = (
                        customer_request
                    )

                if context:
                    job["context"] = context

            started = start_review(
                job_id
            )

            response = job_response(
                job
            )

            response.update(
                {
                    "reply": (
                        "Your document has been received. "
                        "It is now being reviewed page by page."
                    ),

                    "created_work": True,

                    "work_created": True,

                    "review_started": started,
                }
            )

            return response

        # ====================================================
        # ORDINARY ADA CHAT
        # ====================================================

        if not clean(request.message):

            return error_response(
                "CHAT",
                "The chat message is empty.",
                400,
                "EMPTY_MESSAGE",
            )

        reply = await asyncio.to_thread(
            ada.respond,
            message=clean(
                request.message
            ),
            service=request.service,
            event=request.event,
            context=context,
        )

        return {
            "success": True,

            "reply": clean(reply),

            "job_id": job_id,

            "service": (
                request.service
                or getattr(
                    ada,
                    "service",
                    None,
                )
            ),

            "created_work": False,

            "work_created": False,
        }

    except Exception as exc:

        return error_response(
            "CHAT",
            exc,
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
        clean(job_id)
    )

    if not job:

        return error_response(
            "REVIEW",
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    # If a job is marked reviewing but no live task exists,
    # safely resume it.
    if (
        job.get("status")
        == "reviewing"
        and not job.get(
            "review_finished",
            False,
        )
    ):

        start_review(
            clean(job_id)
        )

    return job_response(
        job
    )


# ============================================================
# REVIEW PAGES
# ============================================================

@app.get("/api/review/pages")
async def get_review_pages(
    job_id: str,
):

    job = _jobs.get(
        clean(job_id)
    )

    if not job:

        return error_response(
            "REVIEW_PAGES",
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        job.get("status")
        == "reviewing"
        and not job.get(
            "review_finished",
            False,
        )
    ):

        start_review(
            clean(job_id)
        )

    document_pages = stored_pages(
        job.get(
            "document_pages",
            [],
        )
    )

    review_state = job.get(
        "review_pages",
        make_review_pages(
            document_pages
        ),
    )

    return {
        "success": True,

        "job_id": clean(job_id),

        "current_version": job.get(
            "current_version",
            1,
        ),

        "version_id": job.get(
            "version_id"
        ),

        "status": job.get(
            "status"
        ),

        "total_pages": len(
            document_pages
        ),

        # Authoritative source document.
        "pages": document_pages,

        "document_pages": document_pages,

        # Intelligence review results.
        "review_pages": review_state,

        "assembled_review": job.get(
            "assembled_review",
            "",
        ),

        "progress": job.get(
            "progress",
            {
                "completed": 0,
                "total": len(
                    document_pages
                ),
            },
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

        "error": job.get(
            "review_error"
        ),
    }


# ============================================================
# CORRECTION
# ============================================================

@app.post("/api/correct")
async def correct(
    request: Correction,
):

    job_id = clean(
        request.job_id
    )

    instruction = clean(
        request.instruction
    )

    job = _jobs.get(
        job_id
    )

    if not job:

        return error_response(
            "CORRECTION",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if not instruction:

        return error_response(
            "CORRECTION",
            "Correction instruction is empty.",
            400,
            "EMPTY_CORRECTION",
        )

    if job.get("status") in {
        "reviewing",
        "correcting",
    }:

        return error_response(
            "CORRECTION",
            "The document is still being processed.",
            409,
            "DOCUMENT_STILL_PROCESSING",
        )

    document_pages = stored_pages(
        job.get(
            "document_pages",
            [],
        )
    )

    if not document_pages:

        return error_response(
            "CORRECTION",
            "There is no document available for correction.",
            409,
            "NO_DOCUMENT",
        )

    # --------------------------------------------------------
    # Cancel any old correction worker.
    # --------------------------------------------------------

    old_task = _correction_tasks.get(
        job_id
    )

    if (
        old_task
        and not old_task.done()
    ):

        old_task.cancel()

    # --------------------------------------------------------
    # New version.
    # --------------------------------------------------------

    current_version = int(
        job.get(
            "current_version",
            1,
        )
    ) + 1

    job["current_version"] = (
        current_version
    )

    job["version_id"] = (
        f"{job_id}:{current_version}"
    )

    job["status"] = "correcting"

    job["approved"] = False

    job["paid"] = False

    job["review_started"] = False

    job["review_finished"] = False

    job["review_error"] = None

    job["correction_instruction"] = (
        instruction
    )

    job["progress"] = {
        "completed": 0,
        "total": len(
            document_pages
        ),
    }

    async def correction_worker():

        try:

            ada = get_session(
                job.get(
                    "customer_id"
                ),
                job_id,
                job.get(
                    "service"
                ),
            )

            result = await asyncio.to_thread(
                ada.correct_document,
                document_pages=document_pages,
                correction=instruction,
                service=job.get(
                    "service"
                ),
                context=job.get(
                    "context"
                ),
                progress_callback=None,
            )

            corrected_pages = (
                generated_pages(
                    result
                )
            )

            if not corrected_pages:

                raise ValueError(
                    "Correction produced no document pages."
                )

            job["document_pages"] = (
                corrected_pages
            )

            job["review_pages"] = (
                make_review_pages(
                    corrected_pages
                )
            )

            job["assembled_review"] = ""

            job["status"] = "reviewing"

            job["review_started"] = True

            job["review_finished"] = False

            job["review_error"] = None

            job["progress"] = {
                "completed": 0,
                "total": len(
                    corrected_pages
                ),
            }

            start_review(
                job_id
            )

        except asyncio.CancelledError:

            raise

        except Exception as exc:

            job["status"] = (
                "correction_error"
            )

            job["review_error"] = {
                "type": type(
                    exc
                ).__name__,

                "message": str(
                    exc
                ),
            }

            traceback.print_exc()

    _correction_tasks[job_id] = (
        asyncio.create_task(
            correction_worker()
        )
    )

    return {
        "success": True,

        "job_id": job_id,

        "status": "correcting",

        "version_id": job[
            "version_id"
        ],

        "current_version": job[
            "current_version"
        ],

        "message": (
            "Correction has started. "
            "The corrected document will be "
            "reviewed again."
        ),
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: Approval,
):

    job_id = clean(
        request.job_id
    )

    version_id = clean(
        request.version_id
    )

    job = _jobs.get(
        job_id
    )

    if not job:

        return error_response(
            "APPROVAL",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != clean(
        job.get("version_id")
    ):

        return error_response(
            "APPROVAL",
            "The supplied document version does not match.",
            409,
            "VERSION_MISMATCH",
        )

    if job.get("status") != "review_complete":

        return error_response(
            "APPROVAL",
            "The document review is not complete.",
            409,
            "REVIEW_NOT_COMPLETE",
        )

    job["approved"] = True

    job["status"] = "approved"

    return {
        "success": True,

        "job_id": job_id,

        "version_id": version_id,

        "current_version": job[
            "current_version"
        ],

        "approved": True,

        "status": "approved",

        "total_pages": len(
            job.get(
                "document_pages",
                [],
            )
        ),

        "pages": stored_pages(
            job.get(
                "document_pages",
                [],
            )
        ),

        "document_pages": stored_pages(
            job.get(
                "document_pages",
                [],
            )
        ),

        "payment_url": (
            "/payment.html"
            f"?job_id={job_id}"
            f"&version_id={version_id}"
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

    job_id = clean(
        job_id
    )

    version_id = clean(
        version_id
    )

    job = _jobs.get(
        job_id
    )

    if not job:

        return error_response(
            "PAYMENT",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != clean(
        job.get("version_id")
    ):

        return error_response(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job.get(
        "approved",
        False,
    ):

        return error_response(
            "PAYMENT",
            "The document must be approved before payment.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    job["paid"] = True

    job["status"] = "paid"

    return {
        "success": True,

        "job_id": job_id,

        "version_id": version_id,

        "paid": True,

        "status": "paid",

        "total_pages": len(
            job.get(
                "document_pages",
                [],
            )
        ),

        "download_url": (
            "/download.html"
            f"?job_id={job_id}"
            f"&version_id={version_id}"
        ),

        "api_download_url": (
            "/api/download"
            f"?job_id={job_id}"
            f"&version_id={version_id}"
        ),
    }


# ============================================================
# PAYMENT STATE
# ============================================================

@app.get("/api/payment")
async def payment_state(
    job_id: str,
    version_id: str,
):

    job_id = clean(
        job_id
    )

    version_id = clean(
        version_id
    )

    job = _jobs.get(
        job_id
    )

    if not job:

        return error_response(
            "PAYMENT_STATE",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != clean(
        job.get("version_id")
    ):

        return error_response(
            "PAYMENT_STATE",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    return {
        "success": True,

        "job_id": job_id,

        "version_id": version_id,

        "status": job.get(
            "status"
        ),

        "approved": job.get(
            "approved",
            False,
        ),

        "paid": job.get(
            "paid",
            False,
        ),

        "total_pages": len(
            job.get(
                "document_pages",
                [],
            )
        ),

        "payment_complete": job.get(
            "paid",
            False,
        ),
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download(
    job_id: str,
    version_id: str,
):

    job_id = clean(
        job_id
    )

    version_id = clean(
        version_id
    )

    job = _jobs.get(
        job_id
    )

    if not job:

        return error_response(
            "DOWNLOAD",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if version_id != clean(
        job.get("version_id")
    ):

        return error_response(
            "DOWNLOAD",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job.get(
        "approved",
        False,
    ):

        return error_response(
            "DOWNLOAD",
            "The current document version has not been approved.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    if not job.get(
        "paid",
        False,
    ):

        return error_response(
            "DOWNLOAD",
            "Payment for the current document version has not been completed.",
            409,
            "PAYMENT_NOT_COMPLETED",
        )

    pages = stored_pages(
        job.get(
            "document_pages",
            [],
        )
    )

    return {
        "success": True,

        "job_id": job_id,

        "version_id": version_id,

        "status": "paid",

        "total_pages": len(
            pages
        ),

        "pages": pages,

        "document_pages": pages,

        "message": (
            "The approved and paid document "
            "is ready for final document generation."
        ),
    }


# ============================================================
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
):

    current = _sessions.get(
        job_key(
            customer_id,
            job_id,
        )
    )

    if current:

        try:
            current.clear_history()
        except Exception:
            pass

    return {
        "success": True,
        "message": "Conversation cleared.",
    }


# ============================================================
# DEBUG / JOB INSPECTION
# ============================================================

@app.get("/api/job")
async def get_job(
    job_id: str,
):

    job_id = clean(
        job_id
    )

    job = _jobs.get(
        job_id
    )

    if not job:

        return error_response(
            "JOB",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    return job_response(
        job
    )


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
        "Document pages are authoritative: ENABLED"
    )

    print(
        "Page-by-page review: ENABLED"
    )

    print(
        "Correction → new version → re-review: ENABLED"
    )

    print(
        "Keyword document generation: DISABLED"
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
