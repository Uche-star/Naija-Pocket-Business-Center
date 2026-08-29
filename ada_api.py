from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ada_response import (
    AdaResponse,
    get_ada_model,
    is_configured,
)


# ============================================================
# NAIJA POCKET BUSINESS CENTER
# ADA API
#
# ARCHITECTURE:
#
#       CUSTOMER
#          |
#       SERVICE
#          |
#     ADA INTELLIGENCE
#          |
#   ------------------
#   |       |        |
# CREATE   REVIEW  CORRECT
#   |       |        |
#   --------+--------
#          |
#       APPROVAL
#          |
#       PAYMENT
#          |
#       DOWNLOAD
#
# IMPORTANT
# ----------
# This API is NOT the intelligence.
#
# It does NOT decide:
# - page count
# - document type
# - service structure
# - CV length
# - letterhead structure
# - seminar-paper length
# - review criteria
# - correction strategy
#
# AdaResponse owns those decisions.
#
# This file is transport + job state only.
# ============================================================


BASE = Path(__file__).resolve().parent

MAX_UPLOAD = int(
    os.getenv(
        "ADA_MAX_UPLOAD_BYTES",
        str(25 * 1024 * 1024),
    )
)


# ============================================================
# RUNTIME
# ============================================================

_sessions: dict[str, AdaResponse] = {}
_jobs: dict[str, dict[str, Any]] = {}

_review_tasks: dict[str, asyncio.Task] = {}
_correction_tasks: dict[str, asyncio.Task] = {}


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center",
    version="intelligence-controlled-workflow",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# FILES
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


def serve_html(name: str):
    path = find_file(name)

    if not path:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": "HTML_NOT_FOUND",
                "message": f"{name} was not found.",
            },
        )

    return FileResponse(
        path,
        media_type="text/html",
    )


# ============================================================
# HELPERS
# ============================================================

def job_key(
    customer_id: Any,
    job_id: Any,
) -> str:
    customer = (
        str(customer_id or "anonymous").strip()
        or "anonymous"
    )

    job = (
        str(job_id or "default").strip()
        or "default"
    )

    return f"{customer}:{job}"


def get_session(
    customer_id: Any,
    job_id: Any,
    service: str | None = None,
) -> AdaResponse:

    key = job_key(
        customer_id,
        job_id,
    )

    ada = _sessions.get(key)

    if ada is None:
        ada = AdaResponse(
            service=service
        )

        _sessions[key] = ada

    elif service:

        setter = getattr(
            ada,
            "set_service",
            None,
        )

        if callable(setter):
            setter(service)

    return ada


def fail(
    stage: str,
    message: str,
    status: int = 500,
    error: str = "APPLICATION_ERROR",
):
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "stage": stage,
            "error": error,
            "message": message,
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
# CONTEXT
#
# The API merely packages factual customer input.
# Intelligence decides what it means.
# ============================================================

def build_customer_input(
    request: Chat,
) -> str:

    parts: list[str] = []

    if request.service:
        parts.append(
            f"SELECTED SERVICE:\n{request.service.strip()}"
        )

    if request.form_data:

        values = []

        for key, value in request.form_data.items():

            value_text = str(
                value or ""
            ).strip()

            if not value_text:
                continue

            label = (
                str(key)
                .replace("_", " ")
                .strip()
                .title()
            )

            values.append(
                f"{label}: {value_text}"
            )

        if values:
            parts.append(
                "CUSTOMER PROVIDED INFORMATION:\n"
                + "\n".join(values)
            )

    if request.context:
        if request.context.strip():
            parts.append(
                "ADDITIONAL CONTEXT:\n"
                + request.context.strip()
            )

    if request.message:
        if request.message.strip():
            parts.append(
                "CUSTOMER MESSAGE:\n"
                + request.message.strip()
            )

    return "\n\n".join(parts).strip()


def build_context(
    request: Chat,
) -> str | None:

    parts = []

    if request.context:
        if request.context.strip():
            parts.append(
                request.context.strip()
            )

    if request.customer_id:
        parts.append(
            f"CUSTOMER ID:\n{request.customer_id}"
        )

    if request.client_request_id:
        parts.append(
            f"CLIENT REQUEST ID:\n"
            f"{request.client_request_id}"
        )

    return "\n\n".join(parts) or None


# ============================================================
# INTELLIGENCE RESULT NORMALIZATION
#
# IMPORTANT:
#
# This is NOT pagination.
#
# It only discovers structures that intelligence itself
# returned.
#
# The API NEVER invents a page count.
# ============================================================

def intelligence_pages(
    result: Any,
) -> list[dict[str, Any]]:

    if not isinstance(result, dict):
        return []

    candidates = (
        "pages",
        "document_pages",
        "prepared_pages",
        "content_pages",
    )

    for key in candidates:

        value = result.get(key)

        if not isinstance(value, list):
            continue

        pages = []

        for position, item in enumerate(
            value,
            1,
        ):

            if isinstance(item, dict):

                content = str(
                    item.get(
                        "content",
                        item.get(
                            "text",
                            "",
                        ),
                    )
                    or ""
                )

                page_number = item.get(
                    "page_number",
                    position,
                )

                try:
                    page_number = int(
                        page_number
                    )
                except Exception:
                    page_number = position

                pages.append(
                    {
                        **item,
                        "page_number": page_number,
                        "position": position,
                        "content": content,
                    }
                )

            elif isinstance(item, str):

                pages.append(
                    {
                        "page_number": position,
                        "position": position,
                        "content": item,
                    }
                )

        if pages:
            return pages

    return []


def intelligence_document(
    result: Any,
) -> dict[str, Any]:

    """
    Preserve the intelligence response.

    Nothing here decides what a document SHOULD be.
    """

    if isinstance(result, dict):
        return dict(result)

    if isinstance(result, str):
        return {
            "reply": result,
            "document_text": result,
        }

    return {
        "result": result
    }


# ============================================================
# JOB STATE
# ============================================================

def make_review_pages(
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    return [
        {
            "page_number": page["page_number"],
            "position": page["position"],
            "status": "queued",
            "content": page.get(
                "content",
                "",
            ),
            "review": "",
            "error": None,
        }
        for page in pages
    ]


def create_job(
    request: Chat,
    customer_request: str,
    intelligence_result: Any,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:

    job_id = (
        str(
            request.job_id or ""
        ).strip()
        or str(uuid.uuid4())
    )

    job = {
        "job_id": job_id,

        "customer_id":
            request.customer_id,

        "service":
            request.service,

        "original_request":
            customer_request,

        "context":
            build_context(request),

        "client_request_id":
            request.client_request_id,

        "intelligence_result":
            intelligence_document(
                intelligence_result
            ),

        "document_pages":
            pages,

        "review_pages":
            make_review_pages(
                pages
            ),

        "status":
            "reviewing",

        "review_started":
            False,

        "review_finished":
            False,

        "review_error":
            None,

        "approved":
            False,

        "paid":
            False,

        "current_version":
            1,

        "version_id":
            f"{job_id}:1",

        "progress":
            {
                "completed": 0,
                "total": len(pages),
            },

        "assembled_review":
            "",
    }

    _jobs[job_id] = job

    return job


# ============================================================
# JOB RESPONSE
# ============================================================

def job_response(
    job: dict[str, Any],
) -> dict[str, Any]:

    pages = job.get(
        "document_pages",
        [],
    )

    review_pages = job.get(
        "review_pages",
        [],
    )

    progress = job.get(
        "progress",
        {},
    )

    return {
        "success": True,

        "job_id":
            job["job_id"],

        "customer_id":
            job.get("customer_id"),

        "service":
            job.get("service"),

        "status":
            job.get("status"),

        "current_version":
            job.get(
                "current_version",
                1,
            ),

        "version_id":
            job.get(
                "version_id"
            ),

        "review_started":
            job.get(
                "review_started",
                False,
            ),

        "review_finished":
            job.get(
                "review_finished",
                False,
            ),

        "approved":
            job.get(
                "approved",
                False,
            ),

        "paid":
            job.get(
                "paid",
                False,
            ),

        "progress":
            {
                "completed":
                    int(
                        progress.get(
                            "completed",
                            0,
                        )
                    ),

                "total":
                    len(pages),
            },

        "total_pages":
            len(pages),

        "document_pages":
            pages,

        "pages":
            pages,

        "review_pages":
            review_pages,

        "assembled_review":
            job.get(
                "assembled_review",
                "",
            ),

        "error":
            job.get(
                "review_error"
            ),

        "intelligence_result":
            job.get(
                "intelligence_result"
            ),

        "review_url":
            f"/review.html?job_id={job['job_id']}",
    }


# ============================================================
# INTELLIGENCE — CREATE
#
# The intelligence is asked to create the work.
#
# No API pagination.
# No service-specific API rules.
# ============================================================

async def intelligence_create(
    ada: AdaResponse,
    request: Chat,
    customer_request: str,
    context: str | None,
):

    """
    Give the intelligence the operation.

    Preferred method:
        create_document()

    Compatibility:
        generate_document()
        create_work()
        generate_work()
        respond()

    The API does NOT impose document structure.
    """

    operation = (
        "Create the customer's requested service.\n\n"
        "You are responsible for deciding the correct "
        "document structure and format for this service.\n"
        "Do not assume that every service needs multiple "
        "pages.\n"
        "Do not assume that every service needs one page.\n"
        "If the customer explicitly requests a number of "
        "pages, honour that requirement where appropriate.\n"
        "If the service has no page-count requirement, use "
        "the appropriate professional structure for that "
        "service.\n\n"
        "Return the completed work in a structured form "
        "suitable for the review workflow. If the work "
        "naturally consists of pages, return those pages "
        "explicitly."
        "\n\n"
        "CUSTOMER REQUEST:\n"
        + customer_request
    )

    methods = (
        "create_document",
        "generate_document",
        "create_work",
        "generate_work",
    )

    for name in methods:

        method = getattr(
            ada,
            name,
            None,
        )

        if not callable(method):
            continue

        try:

            return await asyncio.to_thread(
                method,
                customer_request=operation,
                service=request.service,
                form_data=request.form_data,
                context=context,
                event=request.event,
            )

        except TypeError:
            pass

        try:

            return await asyncio.to_thread(
                method,
                message=operation,
                service=request.service,
                context=context,
                event=request.event,
            )

        except TypeError:
            pass

    respond = getattr(
        ada,
        "respond",
        None,
    )

    if not callable(respond):
        raise AttributeError(
            "AdaResponse does not expose a "
            "document creation or respond method."
        )

    return await asyncio.to_thread(
        respond,
        message=operation,
        service=request.service,
        event=request.event,
        context=context,
    )


# ============================================================
# INTELLIGENCE — REVIEW
# ============================================================

async def intelligence_review(
    ada: AdaResponse,
    job: dict[str, Any],
):

    method = getattr(
        ada,
        "review_document_pages",
        None,
    )

    if not callable(method):

        raise AttributeError(
            "AdaResponse does not expose "
            "review_document_pages()."
        )

    return await asyncio.to_thread(
        method,
        pages=job[
            "document_pages"
        ],
        service=job.get(
            "service"
        ),
        context=job.get(
            "context"
        ),
        customer_request=job.get(
            "original_request"
        ),
        event="send_for_review",
    )


# ============================================================
# REVIEW WORKER
# ============================================================

async def run_review(
    job_id: str,
):

    job = _jobs.get(job_id)

    if not job:
        return

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

        result = await intelligence_review(
            ada,
            job,
        )

        job[
            "intelligence_review_result"
        ] = intelligence_document(
            result
        )

        returned_pages = intelligence_pages(
            result
        )

        if returned_pages:

            job[
                "review_pages"
            ] = make_review_pages(
                returned_pages
            )

            for page in job[
                "review_pages"
            ]:

                page["status"] = "reviewed"

            # Intelligence has authority over the
            # reviewed document representation.

            job[
                "document_pages"
            ] = returned_pages

        if isinstance(
            result,
            dict,
        ):

            job[
                "assembled_review"
            ] = str(
                result.get(
                    "assembled_review",
                    result.get(
                        "review",
                        "",
                    ),
                )
                or ""
            )

        total = len(
            job[
                "document_pages"
            ]
        )

        job[
            "progress"
        ] = {
            "completed": total,
            "total": total,
        }

        job[
            "status"
        ] = "review_complete"

        job[
            "review_started"
        ] = True

        job[
            "review_finished"
        ] = True

        job[
            "review_error"
        ] = None

    except asyncio.CancelledError:
        raise

    except Exception as error:

        job[
            "status"
        ] = "review_error"

        job[
            "review_started"
        ] = True

        job[
            "review_finished"
        ] = True

        job[
            "review_error"
        ] = {
            "type":
                type(error).__name__,

            "message":
                str(error),
        }

        traceback.print_exc()


def start_review(
    job_id: str,
) -> bool:

    job = _jobs.get(job_id)

    if not job:
        return False

    if not job.get(
        "document_pages"
    ):
        return False

    existing = _review_tasks.get(
        job_id
    )

    if (
        existing
        and not existing.done()
    ):
        return False

    job[
        "review_started"
    ] = True

    job[
        "status"
    ] = "reviewing"

    _review_tasks[
        job_id
    ] = asyncio.create_task(
        run_review(job_id)
    )

    return True


# ============================================================
# HTML
# ============================================================

@app.get("/")
async def root():
    return serve_html("index.html")


@app.get("/index.html")
async def index():
    return serve_html("index.html")


@app.get("/conversation.html")
async def conversation():
    return serve_html("conversation.html")


@app.get("/workspace.html")
async def workspace():
    return serve_html("workspace.html")


@app.get("/review.html")
async def review():
    return serve_html("review.html")


@app.get("/payment.html")
async def payment():
    return serve_html("payment.html")


@app.get("/download.html")
async def download_page():
    return serve_html("download.html")


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
        "architecture":
            "intelligence-controlled",
    }


@app.get("/api/status")
async def status():

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
        "architecture":
            "intelligence-controlled",
    }


# ============================================================
# UPLOAD
#
# Upload extraction is transport functionality.
#
# It does NOT decide how the service should use the file.
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
            return fail(
                "UPLOAD",
                "The uploaded file is empty.",
                400,
                "EMPTY_FILE",
            )

        if len(data) > MAX_UPLOAD:
            return fail(
                "UPLOAD",
                "The uploaded file is too large.",
                413,
                "FILE_TOO_LARGE",
            )

        filename = (
            file.filename
            or "uploaded-file"
        )

        # The API deliberately does not turn the upload
        # into artificial review pages.
        #
        # It sends the extracted content to intelligence
        # during the actual operation.

        text = ""

        suffix = Path(
            filename
        ).suffix.lower()

        if suffix in {
            ".txt",
            ".csv",
        }:

            text = data.decode(
                "utf-8",
                "replace",
            )

        elif suffix == ".pdf":

            from pypdf import PdfReader

            reader = PdfReader(
                __import__(
                    "io"
                ).BytesIO(data)
            )

            text = "\n\n".join(
                page.extract_text() or ""
                for page in reader.pages
            )

        else:

            # Preserve raw upload metadata.
            # Actual service intelligence can decide
            # how the file should be handled.

            return {
                "success": True,
                "filename": filename,
                "job_id":
                    job_id
                    or str(uuid.uuid4()),
                "customer_id":
                    customer_id,
                "client_request_id":
                    client_request_id,
                "service":
                    service,
                "uploaded":
                    True,
                "document_text":
                    "",
                "message":
                    "File received. Intelligence will "
                    "handle it as part of the customer request.",
            }

        return {
            "success": True,

            "filename":
                filename,

            "job_id":
                job_id
                or str(uuid.uuid4()),

            "customer_id":
                customer_id,

            "client_request_id":
                client_request_id,

            "service":
                service,

            "uploaded":
                True,

            "document_text":
                text.strip(),
        }

    except Exception as error:

        traceback.print_exc()

        return fail(
            "UPLOAD",
            str(error),
            400,
            "UPLOAD_ERROR",
        )


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat,
):

    if not request.activate_intelligence:

        return fail(
            "INTELLIGENCE",
            "Intelligence activation is disabled.",
            400,
            "INTELLIGENCE_NOT_ACTIVATED",
        )

    if not is_configured():

        return fail(
            "INTELLIGENCE",
            "AdaResponse is not configured.",
            503,
            "INTELLIGENCE_NOT_CONFIGURED",
        )

    job_id = (
        str(
            request.job_id or ""
        ).strip()
        or str(uuid.uuid4())
    )

    try:

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        customer_request = (
            build_customer_input(
                request
            )
        )

        context = build_context(
            request
        )

        # ====================================================
        # GUIDANCE
        # ====================================================

        if request.guidance_only:

            if not request.message.strip():

                return fail(
                    "GUIDANCE",
                    "The guidance message is empty.",
                    400,
                    "EMPTY_GUIDANCE",
                )

            result = await asyncio.to_thread(
                ada.respond,
                message=request.message.strip(),
                service=request.service,
                event=request.event,
                context=context,
            )

            return {
                "success": True,
                "reply": str(
                    result or ""
                ).strip(),
                "job_id": job_id,
                "created_work": False,
            }

        # ====================================================
        # EXISTING DOCUMENT
        #
        # If Workspace already possesses a document
        # representation, preserve it.
        # ====================================================

        supplied_pages = []

        if request.document_pages:

            for position, item in enumerate(
                request.document_pages,
                1,
            ):

                if isinstance(
                    item,
                    dict,
                ):

                    supplied_pages.append(
                        {
                            **item,
                            "page_number":
                                item.get(
                                    "page_number",
                                    position,
                                ),
                            "position":
                                position,
                            "content":
                                str(
                                    item.get(
                                        "content",
                                        "",
                                    )
                                    or ""
                                ),
                        }
                    )

                elif isinstance(
                    item,
                    str,
                ):

                    supplied_pages.append(
                        {
                            "page_number":
                                position,
                            "position":
                                position,
                            "content":
                                item,
                        }
                    )

        # ====================================================
        # CREATE WORK
        #
        # Intelligence decides the document.
        # ====================================================

        create_requested = (
            request.create_work
            or str(
                request.event or ""
            ).strip().lower()
            in {
                "create_work",
                "create_document",
                "form_submitted_create_work",
                "submit_service",
                "service_submitted",
            }
        )

        if create_requested:

            # Existing pages are accepted as authoritative
            # input when Workspace explicitly supplied them.
            #
            # Otherwise intelligence creates the work.

            if supplied_pages:

                result = {
                    "pages":
                        supplied_pages
                }

                pages = supplied_pages

            else:

                if not customer_request:

                    return fail(
                        "WORK_CREATION",
                        "The customer request is empty.",
                        400,
                        "EMPTY_WORK_REQUEST",
                    )

                result = await intelligence_create(
                    ada=ada,
                    request=request,
                    customer_request=customer_request,
                    context=context,
                )

                # IMPORTANT:
                #
                # We do NOT paginate text here.
                #
                # If intelligence did not return a structured
                # page collection, the API does not invent one.

                pages = intelligence_pages(
                    result
                )

            if not pages:

                # This is deliberately a hard failure.
                #
                # Previously the API tried to manufacture pages
                # from arbitrary text. That hid the real problem.
                #
                # Now we expose the real contract failure.

                return fail(
                    "INTELLIGENCE_OUTPUT",
                    (
                        "Intelligence completed the operation "
                        "but did not return a structured "
                        "document page collection. "
                        "The API will not invent page structure."
                    ),
                    422,
                    "INTELLIGENCE_DID_NOT_RETURN_DOCUMENT_PAGES",
                )

            job = create_job(
                request=request,
                customer_request=customer_request,
                intelligence_result=result,
                pages=pages,
            )

            started = start_review(
                job["job_id"]
            )

            response = job_response(
                job
            )

            response.update(
                {
                    "reply":
                        (
                            "Your service has been prepared "
                            "and sent for review."
                        ),

                    "created_work":
                        True,

                    "work_created":
                        True,

                    "review_started":
                        started,
                }
            )

            return response

        # ====================================================
        # DOCUMENT SUPPLIED DIRECTLY
        # ====================================================

        if supplied_pages:

            job = create_job(
                request=request,
                customer_request=customer_request,
                intelligence_result={
                    "pages":
                        supplied_pages
                },
                pages=supplied_pages,
            )

            started = start_review(
                job["job_id"]
            )

            response = job_response(
                job
            )

            response.update(
                {
                    "reply":
                        (
                            "Your document has been received "
                            "and sent for review."
                        ),

                    "created_work":
                        True,

                    "review_started":
                        started,
                }
            )

            return response

        # ====================================================
        # NORMAL CHAT
        # ====================================================

        if not request.message.strip():

            return fail(
                "CHAT",
                "The chat message is empty.",
                400,
                "EMPTY_MESSAGE",
            )

        result = await asyncio.to_thread(
            ada.respond,
            message=request.message.strip(),
            service=request.service,
            event=request.event,
            context=context,
        )

        return {
            "success": True,

            "reply":
                str(
                    result or ""
                ).strip(),

            "job_id":
                job_id,

            "service":
                request.service,

            "created_work":
                False,
        }

    except Exception as error:

        traceback.print_exc()

        return fail(
            "CHAT",
            str(error),
            500,
            "CHAT_ERROR",
        )


# ============================================================
# REVIEW
# ============================================================

@app.get("/api/review")
async def review_status(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return fail(
            "REVIEW",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        job.get(
            "document_pages"
        )
        and job.get(
            "status"
        ) == "reviewing"
    ):

        start_review(
            job_id
        )

    return job_response(
        job
    )


@app.get("/api/review/pages")
async def review_pages(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if not job:

        return fail(
            "REVIEW",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        job.get(
            "document_pages"
        )
        and job.get(
            "status"
        ) == "reviewing"
    ):

        start_review(
            job_id
        )

    return {
        "success": True,

        "job_id":
            job_id,

        "status":
            job.get(
                "status"
            ),

        "current_version":
            job.get(
                "current_version"
            ),

        "version_id":
            job.get(
                "version_id"
            ),

        "total_pages":
            len(
                job.get(
                    "document_pages",
                    [],
                )
            ),

        "pages":
            job.get(
                "document_pages",
                [],
            ),

        "document_pages":
            job.get(
                "document_pages",
                [],
            ),

        "review_pages":
            job.get(
                "review_pages",
                [],
            ),

        "progress":
            job.get(
                "progress",
                {},
            ),

        "approved":
            job.get(
                "approved",
                False,
            ),

        "paid":
            job.get(
                "paid",
                False,
            ),

        "error":
            job.get(
                "review_error"
            ),
    }


# ============================================================
# CORRECTION
#
# Intelligence controls correction.
# ============================================================

@app.post("/api/correct")
async def correct(
    request: Correction,
):

    job = _jobs.get(
        request.job_id
    )

    if not job:

        return fail(
            "CORRECTION",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    instruction = (
        request.instruction.strip()
    )

    if not instruction:

        return fail(
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

        return fail(
            "CORRECTION",
            "The document is still being processed.",
            409,
            "DOCUMENT_STILL_PROCESSING",
        )

    if not job.get(
        "document_pages"
    ):

        return fail(
            "CORRECTION",
            "No document is available.",
            409,
            "NO_DOCUMENT",
        )

    job[
        "current_version"
    ] += 1

    job[
        "version_id"
    ] = (
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

            "progress":
                {
                    "completed": 0,
                    "total":
                        len(
                            job[
                                "document_pages"
                            ]
                        ),
                },
        }
    )

    async def worker():

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

            method = getattr(
                ada,
                "correct_document",
                None,
            )

            if not callable(method):

                raise AttributeError(
                    "AdaResponse does not expose "
                    "correct_document()."
                )

            correction_request = (
                "Correct the document according to "
                "the customer's instruction.\n\n"
                "You are responsible for deciding the "
                "appropriate corrected document structure. "
                "Do not assume a fixed page count.\n\n"
                "CUSTOMER CORRECTION:\n"
                + instruction
            )

            result = await asyncio.to_thread(
                method,
                document_pages=
                    job[
                        "document_pages"
                    ],
                correction=
                    correction_request,
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

            corrected_pages = intelligence_pages(
                result
            )

            if not corrected_pages:

                raise ValueError(
                    "Intelligence completed the correction "
                    "but did not return corrected document "
                    "pages."
                )

            job[
                "intelligence_result"
            ] = intelligence_document(
                result
            )

            job[
                "document_pages"
            ] = corrected_pages

            job[
                "review_pages"
            ] = make_review_pages(
                corrected_pages
            )

            job[
                "progress"
            ] = {
                "completed": 0,
                "total":
                    len(
                        corrected_pages
                    ),
            }

            job[
                "status"
            ] = "reviewing"

            job[
                "review_started"
            ] = True

            job[
                "review_finished"
            ] = False

            start_review(
                request.job_id
            )

        except Exception as error:

            job[
                "status"
            ] = "correction_error"

            job[
                "review_error"
            ] = {
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
        worker()
    )

    return {
        "success": True,

        "job_id":
            request.job_id,

        "status":
            "correcting",

        "version_id":
            job[
                "version_id"
            ],

        "current_version":
            job[
                "current_version"
            ],

        "message":
            "Correction has been sent to intelligence.",
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

        return fail(
            "APPROVAL",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        request.version_id
        != job[
            "version_id"
        ]
    ):

        return fail(
            "APPROVAL",
            "Document version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if (
        job.get(
            "status"
        )
        != "review_complete"
    ):

        return fail(
            "APPROVAL",
            "Document review is not complete.",
            409,
            "REVIEW_NOT_COMPLETE",
        )

    job[
        "approved"
    ] = True

    job[
        "status"
    ] = "approved"

    return {
        "success": True,

        "job_id":
            request.job_id,

        "version_id":
            request.version_id,

        "approved":
            True,

        "status":
            "approved",

        "total_pages":
            len(
                job[
                    "document_pages"
                ]
            ),

        "pages":
            job[
                "document_pages"
            ],

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

    job = _jobs.get(job_id)

    if not job:

        return fail(
            "PAYMENT",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job[
            "version_id"
        ]
    ):

        return fail(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job.get(
        "approved"
    ):

        return fail(
            "PAYMENT",
            "Document must be approved before payment.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    job[
        "paid"
    ] = True

    job[
        "status"
    ] = "paid"

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
                job[
                    "document_pages"
                ]
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


@app.get("/api/payment")
async def payment_state(
    job_id: str,
    version_id: str,
):

    job = _jobs.get(job_id)

    if not job:

        return fail(
            "PAYMENT",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job[
            "version_id"
        ]
    ):

        return fail(
            "PAYMENT",
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
            job.get(
                "status"
            ),

        "approved":
            job.get(
                "approved",
                False,
            ),

        "paid":
            job.get(
                "paid",
                False,
            ),

        "total_pages":
            len(
                job.get(
                    "document_pages",
                    [],
                )
            ),

        "payment_complete":
            job.get(
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

    job = _jobs.get(job_id)

    if not job:

        return fail(
            "DOWNLOAD",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    if (
        version_id
        != job[
            "version_id"
        ]
    ):

        return fail(
            "DOWNLOAD",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job.get(
        "approved"
    ):

        return fail(
            "DOWNLOAD",
            "Document has not been approved.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    if not job.get(
        "paid"
    ):

        return fail(
            "DOWNLOAD",
            "Payment has not been completed.",
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
                job[
                    "document_pages"
                ]
            ),

        "pages":
            job[
                "document_pages"
            ],

        "document_pages":
            job[
                "document_pages"
            ],

        "message":
            (
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

    ada = _sessions.get(
        job_key(
            customer_id,
            job_id,
        )
    )

    if ada:

        method = getattr(
            ada,
            "clear_history",
            None,
        )

        if callable(method):
            method()

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
        "Architecture: INTELLIGENCE CONTROLLED"
    )
    print(
        "Intelligence:",
        get_ada_model(),
    )
    print(
        "Configured:",
        is_configured(),
    )
    print(
        "API pagination authority: DISABLED"
    )
    print(
        "API service-specific rules: DISABLED"
    )
    print(
        "Intelligence document authority: ENABLED"
    )
    print(
        "Intelligence review authority: ENABLED"
    )
    print(
        "Intelligence correction authority: ENABLED"
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
