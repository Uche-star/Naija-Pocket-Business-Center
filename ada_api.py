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
    "true"
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
    locations = [
        BASE / name,
        BASE / "app" / name,
        BASE / "static" / name,
        BASE / "public" / name,
        BASE / "assets" / name,
    ]

    for path in locations:
        if path.is_file():
            return path

    return None


# ============================================================
# GENERAL HELPERS
# ============================================================

def event_value(value: Any) -> str:
    return str(value or "").strip().lower()


def job_key(customer_id: Any, job_id: Any) -> str:
    customer = str(customer_id or "anonymous").strip() or "anonymous"
    job = str(job_id or "default").strip() or "default"
    return f"{customer}:{job}"


def get_session(
    customer_id: Any,
    job_id: Any,
    service: str | None = None,
) -> AdaResponse:

    k = job_key(customer_id, job_id)

    ada = _sessions.get(k)

    if ada is None:
        ada = AdaResponse(service=service)
        _sessions[k] = ada

    elif service:
        setter = getattr(ada, "set_service", None)

        if callable(setter):
            setter(service)

    return ada


def application_error(
    stage: str,
    error: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):
    print(f"[{stage}] {type(error).__name__}: {error}")

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
                str(error)
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


# ============================================================
# CHAT REQUEST MODELS
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

def build_form_request(r: Chat) -> str:
    parts: list[str] = []

    if r.service:
        parts.append(
            "SELECTED SERVICE:\n"
            + r.service.strip()
        )

    if r.form_data:
        information = []

        for key, value in r.form_data.items():
            value_text = str(value or "").strip()

            if value_text:
                label = (
                    str(key)
                    .replace("_", " ")
                    .title()
                )

                information.append(
                    f"{label}: {value_text}"
                )

        if information:
            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(information)
            )

    if r.context and r.context.strip():
        parts.append(
            "ADDITIONAL CONTEXT:\n"
            + r.context.strip()
        )

    if r.message and r.message.strip():
        parts.append(
            "CUSTOMER REQUEST:\n"
            + r.message.strip()
        )

    return "\n\n".join(parts).strip()


def build_context(r: Chat) -> str | None:
    parts: list[str] = []

    if r.context and r.context.strip():
        parts.append(r.context.strip())

    if r.customer_id:
        parts.append(
            "CUSTOMER ID:\n"
            + r.customer_id
        )

    if r.client_request_id:
        parts.append(
            "CLIENT REQUEST ID:\n"
            + r.client_request_id
        )

    return "\n\n".join(parts) or None


# ============================================================
# DOCUMENT PAGE NORMALIZATION
# ============================================================

def stored_pages(pages: Any) -> list[dict[str, Any]]:
    normalized = normalize_document_pages(
        pages or []
    )

    output: list[dict[str, Any]] = []

    for position, page in enumerate(normalized, 1):

        if isinstance(page, dict):

            page_number = page.get(
                "page_number",
                position,
            )

            try:
                page_number = int(
                    page_number or position
                )
            except Exception:
                page_number = position

            output.append(
                {
                    **page,
                    "page_number": page_number,
                    "position": position,
                    "content": str(
                        page.get("content", "")
                        or ""
                    ),
                }
            )

    return output


# ============================================================
# REVIEW PAGE STATE
# ============================================================

def make_review_pages(
    pages: Any,
) -> list[dict[str, Any]]:

    output = []

    for position, page in enumerate(
        stored_pages(pages),
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
                    page.get("content", "")
                    or ""
                ),
                "review": "",
                "error": None,
            }
        )

    return output


# ============================================================
# JOB RESPONSE
# ============================================================

def make_job_response(
    job: dict[str, Any],
) -> dict[str, Any]:

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
            "/review.html"
            f"?job_id={job['job_id']}"
        ),
    }


# ============================================================
# CREATE JOB
# ============================================================

def create_job(
    job_id: str,
    request: Chat,
    original_request: str,
    pages: Any,
) -> dict[str, Any]:

    pages = stored_pages(pages)

    if not pages:
        raise ValueError(
            "Cannot create a job without document pages."
        )

    job = {
        "job_id": job_id,

        "customer_id": request.customer_id,

        "service": request.service,

        "original_request": original_request,

        "context": build_context(request),

        "client_request_id": (
            request.client_request_id
        ),

        "status": "reviewing",

        "review_started": True,
        "review_finished": False,

        "review_error": None,

        "progress": {
            "completed": 0,
            "total": len(pages),
        },

        "document_pages": pages,

        "review_pages": make_review_pages(
            pages
        ),

        "assembled_review": "",

        "current_version": 1,

        "version_id": (
            job_id + ":1"
        ),

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

        update_type = event_value(
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

                if str(
                    page["page_number"]
                ) == page_number:

                    page["status"] = "reviewing"

        elif update_type == "page_completed":

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

                    page["content"] = str(
                        update.get(
                            "content",
                            page["content"],
                        )
                        or ""
                    )

                    page["error"] = None

            try:
                completed = int(
                    update.get(
                        "position",
                        job["progress"][
                            "completed"
                        ],
                    )
                )
            except Exception:
                completed = job["progress"][
                    "completed"
                ]

            job["progress"][
                "completed"
            ] = completed

        elif update_type == "page_error":

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

        elif update_type == "review_completed":

            total = len(
                job["document_pages"]
            )

            job["status"] = (
                "review_complete"
            )

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
# REVIEW ENGINE
# ============================================================

async def run_review(
    job_id: str,
):

    job = _jobs.get(job_id)

    if not job:
        return

    try:

        ada = get_session(
            job.get("customer_id"),
            job_id,
            job.get("service"),
        )

        pages = stored_pages(
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
            progress_callback=review_callback(
                job_id
            ),
        )

        if not isinstance(result, dict):
            raise TypeError(
                "Invalid review result."
            )

        returned_pages = result.get(
            "pages",
            [],
        )

        for returned in returned_pages or []:

            if not isinstance(
                returned,
                dict,
            ):
                continue

            returned_number = str(
                returned.get(
                    "page_number"
                )
            )

            for page in job["review_pages"]:

                if str(
                    page["page_number"]
                ) == returned_number:

                    if "review" in returned:
                        page["review"] = str(
                            returned["review"]
                            or ""
                        )

                    if "content" in returned:
                        page["content"] = str(
                            returned["content"]
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

        job["status"] = (
            "review_complete"
        )

        job["review_finished"] = True

        job["review_error"] = None

        total = len(
            job["document_pages"]
        )

        job["progress"] = {
            "completed": total,
            "total": total,
        }

    except asyncio.CancelledError:
        raise

    except Exception as error:

        job["status"] = "review_error"

        job["review_finished"] = True

        job["review_error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }

        traceback.print_exc()


def start_review(
    job_id: str,
) -> bool:

    job = _jobs.get(job_id)

    if (
        not job
        or not job.get("document_pages")
        or job.get("status") != "reviewing"
    ):
        return False

    task = _review_tasks.get(job_id)

    if task and not task.done():
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

            patterns = {
                "docx":
                    "word/document.xml",

                "pptx":
                    r"ppt/slides/slide\d+\.xml",

                "xlsx":
                    r"xl/worksheets/sheet\d+\.xml",
            }

            if suffix == ".docx":

                names = [
                    patterns["docx"]
                ] if (
                    patterns["docx"]
                    in names
                ) else []

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

            texts = []

            for name in sorted(names):

                root = ET.fromstring(
                    archive.read(name)
                )

                values = [
                    element.text or ""
                    for element
                    in root.iter()
                    if (
                        isinstance(
                            element.tag,
                            str,
                        )
                        and
                        element.tag.rsplit(
                            "}",
                            1,
                        )[-1] == "t"
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
            "The uploaded document contains "
            "no extractable text."
        )

    return stored_pages(
        document_text_to_pages(text)
    )


# ============================================================
# EXTRACT GENERATED WORK FROM ADA
# ============================================================

def generated_document_pages(
    result: Any,
) -> list[dict[str, Any]]:

    # Structured document output first.
    if isinstance(result, dict):

        for key in (
            "pages",
            "document_pages",
            "prepared_pages",
            "content_pages",
        ):

            value = result.get(key)

            if isinstance(value, list):

                pages = stored_pages(value)

                if pages:
                    return pages

        # Text/document fields next.
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

                return stored_pages(
                    document_text_to_pages(
                        value
                    )
                )

    # Plain string response.
    if (
        isinstance(result, str)
        and result.strip()
    ):

        return stored_pages(
            document_text_to_pages(
                result
            )
        )

    raise ValueError(
        "AdaResponse returned no usable "
        "document work."
    )


# ============================================================
# DOCUMENT CREATION
# ============================================================

async def create_document_work(
    ada: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
) -> list[dict[str, Any]]:

    """
    Create the customer's actual document through
    AdaResponse.

    IMPORTANT:
    We never pass unsupported arguments into
    AdaResponse.respond().

    The old implementation passed:

        create_work=True

    into respond(), which caused:

        AdaResponse.respond() got an unexpected
        keyword argument 'create_work'

    The compatibility path below calls respond()
    only with the normal supported arguments.
    """

    # --------------------------------------------------------
    # Preferred dedicated document-generation methods.
    # --------------------------------------------------------

    for method_name in (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    ):

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

            return generated_document_pages(
                result
            )

        except TypeError:

            # Some deployed versions expose
            # a smaller signature.
            try:

                result = await asyncio.to_thread(
                    method,
                    message=customer_request,
                    service=request.service,
                    context=context,
                    event=request.event,
                )

                return generated_document_pages(
                    result
                )

            except TypeError:
                continue

    # --------------------------------------------------------
    # SIGNATURE-SAFE respond() compatibility path.
    #
    # DO NOT PASS create_work.
    # DO NOT PASS form_data.
    # --------------------------------------------------------

    respond = getattr(
        ada,
        "respond",
        None,
    )

    if not callable(respond):
        raise AttributeError(
            "AdaResponse has no document creation "
            "method and no respond() method."
        )

    result = await asyncio.to_thread(
        respond,
        message=customer_request,
        service=request.service,
        event=request.event,
        context=context,
    )

    return generated_document_pages(
        result
    )


# ============================================================
# HTML ROUTES
# ============================================================

def serve_html(
    filename: str,
):
    path = find_file(filename)

    if not path:
        return application_error(
            "PAGE",
            f"{filename} was not found.",
            404,
            "HTML_NOT_FOUND",
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


@app.get("/")
async def root():
    return serve_html(
        "index.html"
    )


@app.get("/index.html")
async def index():
    return serve_html(
        "index.html"
    )


@app.get("/conversation.html")
async def conversation():
    return serve_html(
        "conversation.html"
    )


@app.get("/workspace.html")
async def workspace():
    return serve_html(
        "workspace.html"
    )


@app.get("/review.html")
async def review_page():
    return serve_html(
        "review.html"
    )


@app.get("/payment.html")
async def payment_page():
    return serve_html(
        "payment.html"
    )


@app.get("/download.html")
async def download_page():
    return serve_html(
        "download.html"
    )


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
async def api_status():

    return {
        "success": True,
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
        "active_sessions": len(
            _sessions
        ),
        "active_jobs": len(
            _jobs
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
            return application_error(
                "UPLOAD",
                "The uploaded file is empty.",
                400,
                "EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD:
            return application_error(
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

        job_id_value = (
            str(job_id or "").strip()
            or str(uuid.uuid4())
        )

        return {
            "success": True,
            "filename": filename,
            "job_id": job_id_value,
            "customer_id": customer_id,
            "client_request_id": client_request_id,
            "service": service,
            "total_pages": len(pages),
            "document_pages": pages,
            "pages": pages,
        }

    except Exception as error:

        return application_error(
            "UPLOAD",
            error,
            400,
            "DOCUMENT_UPLOAD_ERROR",
        )


# ============================================================
# MAIN CHAT / WORKFLOW ENTRY
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat,
):

    # --------------------------------------------------------
    # Intelligence must be active.
    # --------------------------------------------------------

    if not request.activate_intelligence:

        return application_error(
            "INTELLIGENCE",
            "Intelligence activation is disabled.",
            400,
            "INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():

        return application_error(
            "INTELLIGENCE",
            "AdaResponse is not configured.",
            503,
            "INTELLIGENCE_NOT_CONFIGURED",
        )

    job_id = (
        str(request.job_id or "").strip()
        or str(uuid.uuid4())
    )

    context = build_context(
        request
    )

    pages = stored_pages(
        request.document_pages or []
    )

    # --------------------------------------------------------
    # If the frontend sends document_text instead
    # of document_pages, turn it into authoritative pages.
    # --------------------------------------------------------

    if (
        not pages
        and request.document_text
        and request.document_text.strip()
    ):

        pages = stored_pages(
            document_text_to_pages(
                request.document_text
            )
        )

    try:

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        # ====================================================
        # GUIDANCE-ONLY CHAT
        # ====================================================

        if request.guidance_only:

            if not request.message.strip():

                return application_error(
                    "GUIDANCE",
                    "The guidance message is empty.",
                    400,
                    "EMPTY_GUIDANCE_MESSAGE",
                )

            reply = await asyncio.to_thread(
                ada.respond,
                message=request.message.strip(),
                service=request.service,
                event=request.event,
                context=context,
            )

            return {
                "success": True,
                "reply": str(
                    reply or ""
                ).strip(),
                "job_id": job_id,
                "created_work": False,
            }

        # ====================================================
        # CUSTOMER REQUEST
        # ====================================================

        customer_request = (
            build_form_request(request)
        )

        # ====================================================
        # FORM SUBMISSION / DOCUMENT CREATION
        # ====================================================

        create_requested = (
            request.create_work
            or event_value(
                request.event
            )
            in {
                "form_submitted_create_work",
                "create_work",
                "create_document",
            }
        )

        if create_requested and not pages:

            if not customer_request:

                return application_error(
                    "WORK_CREATION",
                    "The customer service request "
                    "contains no usable information.",
                    400,
                    "EMPTY_WORK_REQUEST",
                )

            # REAL INTELLIGENCE CREATES THE WORK.
            created_pages = (
                await create_document_work(
                    ada=ada,
                    request=request,
                    customer_request=customer_request,
                    context=context,
                )
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

            response = make_job_response(
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
        # AUTHORITATIVE DOCUMENT PAGES
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
                    and job.get(
                        "status"
                    ) == "reviewing"
                ):

                    return make_job_response(
                        job
                    )

                job.update(
                    {
                        "document_pages": pages,

                        "review_pages":
                            make_review_pages(
                                pages
                            ),

                        "assembled_review": "",

                        "status": "reviewing",

                        "review_started": True,

                        "review_finished": False,

                        "review_error": None,

                        "approved": False,

                        "paid": False,

                        "progress": {
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
                            context,
                    }
                )

            started = start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {
                    "reply": (
                        "Your document has been received. "
                        "It is now being reviewed page by page."
                    ),
                    "created_work": True,
                    "review_started": started,
                }
            )

            return response

        # ====================================================
        # NORMAL INTELLIGENT CHAT
        # ====================================================

        if not request.message.strip():

            return application_error(
                "CHAT",
                "The chat message is empty.",
                400,
                "EMPTY_MESSAGE",
            )

        reply = await asyncio.to_thread(
            ada.respond,
            message=request.message.strip(),
            service=request.service,
            event=request.event,
            context=context,
        )

        return {
            "success": True,
            "reply": str(
                reply or ""
            ).strip(),
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
        }

    except Exception as error:

        return application_error(
            "CHAT",
            error,
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

        return application_error(
            "REVIEW",
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    start_review(
        job_id
    )

    return make_job_response(
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
        job_id
    )

    if not job:

        return application_error(
            "REVIEW_PAGES",
            "The requested review job does not exist.",
            404,
            "JOB_NOT_FOUND",
        )

    start_review(
        job_id
    )

    return {
        "success": True,

        "job_id": job_id,

        "current_version":
            job["current_version"],

        "version_id":
            job["version_id"],

        "status":
            job["status"],

        "total_pages":
            len(
                job["document_pages"]
            ),

        "pages":
            stored_pages(
                job["document_pages"]
            ),

        "document_pages":
            stored_pages(
                job["document_pages"]
            ),

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
# CORRECTIONS
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

        return application_error(
            "CORRECTION",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if not instruction:

        return application_error(
            "CORRECTION",
            "Correction instruction is empty.",
            400,
            "EMPTY_CORRECTION",
        )

    if job.get(
        "status"
    ) in {
        "reviewing",
        "correcting",
    }:

        return application_error(
            "CORRECTION",
            "The document is still being processed.",
            409,
            "DOCUMENT_STILL_PROCESSING",
        )

    if not job.get(
        "document_pages"
    ):

        return application_error(
            "CORRECTION",
            "There is no document available for correction.",
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
            "status": "correcting",

            "approved": False,
            "paid": False,

            "review_started": False,
            "review_finished": False,

            "review_error": None,

            "correction_instruction":
                instruction,

            "progress": {
                "completed": 0,
                "total": len(
                    job["document_pages"]
                ),
            },
        }
    )

    async def correction_worker():

        try:

            ada = get_session(
                job.get(
                    "customer_id"
                ),
                request.job_id,
                job.get(
                    "service"
                ),
            )

            correct_method = getattr(
                ada,
                "correct_document",
                None,
            )

            if not callable(
                correct_method
            ):
                raise AttributeError(
                    "AdaResponse has no "
                    "correct_document() method."
                )

            result = await asyncio.to_thread(
                correct_method,

                document_pages=
                    stored_pages(
                        job["document_pages"]
                    ),

                correction=
                    instruction,

                service=
                    job.get("service"),

                context=
                    job.get("context"),

                progress_callback=None,
            )

            corrected_pages = (
                generated_document_pages(
                    result
                )
            )

            job["document_pages"] = (
                corrected_pages
            )

            job["review_pages"] = (
                make_review_pages(
                    corrected_pages
                )
            )

            job["status"] = "reviewing"

            job["review_started"] = True

            job["review_finished"] = False

            job["progress"] = {
                "completed": 0,
                "total": len(
                    corrected_pages
                ),
            }

            start_review(
                request.job_id
            )

        except Exception as error:

            job["status"] = (
                "correction_error"
            )

            job["review_error"] = {
                "type":
                    type(error).__name__,

                "message":
                    str(error),
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
            "The corrected document will be "
            "reviewed again.",
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

        return application_error(
            "APPROVAL",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        request.version_id
        != job["version_id"]
    ):

        return application_error(
            "APPROVAL",
            "The supplied document version does not match.",
            409,
            "VERSION_MISMATCH",
        )

    if (
        job["status"]
        != "review_complete"
    ):

        return application_error(
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

        "approved": True,

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
# PAYMENT
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

        return application_error(
            "PAYMENT",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return application_error(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job["approved"]:

        return application_error(
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

        "paid": True,

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
async def payment_state(
    job_id: str,
    version_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return application_error(
            "PAYMENT_STATE",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return application_error(
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

        return application_error(
            "DOWNLOAD",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job["version_id"]
    ):

        return application_error(
            "DOWNLOAD",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job["approved"]:

        return application_error(
            "DOWNLOAD",
            "The current document version "
            "has not been approved.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    if not job["paid"]:

        return application_error(
            "DOWNLOAD",
            "Payment for the current document "
            "version has not been completed.",
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
            "The approved and paid document "
            "is ready for final document generation.",
    }


# ============================================================
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
):

    ada = _sessions.get(
        job_key(
            customer_id,
            job_id,
        )
    )

    if ada:

        clear_method = getattr(
            ada,
            "clear_history",
            None,
        )

        if callable(
            clear_method
        ):
            clear_method()

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
        "Signature-safe AdaResponse integration: ENABLED"
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
