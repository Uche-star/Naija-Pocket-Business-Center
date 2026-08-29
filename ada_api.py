from __future__ import annotations

import asyncio
import json
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
# THIN TRANSPORT + WORKFLOW BRIDGE
#
# IMPORTANT:
#
# The API is NOT the intelligence.
#
# The API does NOT:
#   - decide what a service needs
#   - impose page counts
#   - split documents by character count
#   - invent document structure
#   - use keyword matching
#   - decide whether CVs, letters, letterheads, etc. need pages
#
# Intelligence is responsible for understanding the customer's
# request and creating the appropriate work.
#
# The API's job is to transport the request and preserve the
# intelligence result for Review -> Approval -> Payment ->
# Download.
# ============================================================


DEBUG = os.getenv(
    "ADA_DEBUG_ERRORS",
    "true",
).lower() in {
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
# RUNTIME
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
# BASIC HELPERS
# ============================================================

def event_value(value: Any) -> str:
    return str(value or "").strip().lower()


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


def application_error(
    stage: str,
    error: Exception | str,
    status: int = 500,
    code: str = "APPLICATION_ERROR",
):

    print(
        f"[{stage}] "
        f"{type(error).__name__}: "
        f"{error}"
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
                str(error)
                if DEBUG
                else "An internal application error occurred."
            ),
        },
    )


# ============================================================
# SESSION
# ============================================================

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


# ============================================================
# REQUEST MODEL
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
# CUSTOMER CONTEXT
#
# No service-specific logic lives here.
# We simply give intelligence all available information.
# ============================================================

def build_intelligence_request(
    request: Chat,
) -> str:

    parts: list[str] = []

    if request.service:
        parts.append(
            "SELECTED SERVICE:\n"
            + request.service.strip()
        )

    if request.form_data:

        values: list[str] = []

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

        context = request.context.strip()

        if context:
            parts.append(
                "CONTEXT:\n"
                + context
            )

    if request.message:

        message = request.message.strip()

        if message:
            parts.append(
                "CUSTOMER REQUEST:\n"
                + message
            )

    if request.document_text:

        document_text = (
            request.document_text.strip()
        )

        if document_text:

            parts.append(
                "CUSTOMER DOCUMENT CONTENT:\n"
                + document_text
            )

    if request.document_pages:

        parts.append(
            "CUSTOMER DOCUMENT PAGES:\n"
            + json.dumps(
                request.document_pages,
                ensure_ascii=False,
            )
        )

    return "\n\n".join(parts).strip()


def build_context(
    request: Chat,
) -> str | None:

    parts: list[str] = []

    if request.context:

        value = request.context.strip()

        if value:
            parts.append(value)

    if request.customer_id:

        parts.append(
            "CUSTOMER ID: "
            + request.customer_id
        )

    if request.client_request_id:

        parts.append(
            "CLIENT REQUEST ID: "
            + request.client_request_id
        )

    return "\n\n".join(parts) or None


# ============================================================
# INTELLIGENCE RESULT NORMALIZATION
#
# IMPORTANT:
#
# This is NOT pagination.
#
# It only reads whatever structured work intelligence returns.
#
# If intelligence returns pages, we preserve them.
# If intelligence returns document_text, we preserve it as one
# document value rather than inventing page boundaries.
# ============================================================

def normalize_pages(
    value: Any,
) -> list[dict[str, Any]]:

    if not isinstance(value, list):
        return []

    pages: list[dict[str, Any]] = []

    for index, item in enumerate(value, 1):

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
                index,
            )

            try:
                page_number = int(
                    page_number
                )
            except Exception:
                page_number = index

            pages.append(
                {
                    **item,
                    "page_number": page_number,
                    "position": index,
                    "content": content,
                }
            )

        elif isinstance(item, str):

            pages.append(
                {
                    "page_number": index,
                    "position": index,
                    "content": item,
                }
            )

    return pages


def extract_intelligence_result(
    result: Any,
) -> dict[str, Any]:

    # --------------------------------------------------------
    # AdaResponse may return a dictionary.
    # --------------------------------------------------------

    if isinstance(result, dict):

        output = dict(result)

        pages = []

        for key in (
            "document_pages",
            "pages",
            "prepared_pages",
            "content_pages",
        ):

            candidate = output.get(key)

            if isinstance(candidate, list):

                pages = normalize_pages(
                    candidate
                )

                if pages:
                    break

        if pages:
            output["document_pages"] = pages
            output["pages"] = pages

        return output

    # --------------------------------------------------------
    # AdaResponse may return a JSON string.
    # --------------------------------------------------------

    if isinstance(result, str):

        text = result.strip()

        if not text:
            return {
                "reply": "",
            }

        try:

            decoded = json.loads(text)

            if isinstance(
                decoded,
                dict,
            ):

                return extract_intelligence_result(
                    decoded
                )

        except Exception:
            pass

        # Plain intelligence response.
        #
        # We DO NOT turn it into artificial pages.
        return {
            "reply": text,
            "document_text": text,
        }

    # --------------------------------------------------------
    # Unknown result.
    # --------------------------------------------------------

    return {
        "reply": str(
            result or ""
        )
    }


# ============================================================
# INTELLIGENCE INVOCATION
# ============================================================

async def ask_intelligence(
    ada: AdaResponse,
    request: Chat,
    intelligence_request: str,
    context: str | None,
) -> dict[str, Any]:

    """
    Give the existing intelligence complete responsibility for
    understanding and executing the customer's request.

    No keyword routing.
    No artificial pagination.
    No service-specific API rules.
    """

    # --------------------------------------------------------
    # Preferred document/work methods.
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
                customer_request=intelligence_request,
                service=request.service,
                form_data=request.form_data,
                context=context,
                event=request.event,
            )

            return extract_intelligence_result(
                result
            )

        except TypeError:

            try:

                result = await asyncio.to_thread(
                    method,
                    message=intelligence_request,
                    service=request.service,
                    context=context,
                    event=request.event,
                )

                return extract_intelligence_result(
                    result
                )

            except TypeError:
                continue

    # --------------------------------------------------------
    # Existing respond() path.
    # --------------------------------------------------------

    respond = getattr(
        ada,
        "respond",
        None,
    )

    if not callable(respond):

        raise AttributeError(
            "AdaResponse does not expose a supported "
            "intelligence method."
        )

    result = await asyncio.to_thread(
        respond,
        message=intelligence_request,
        service=request.service,
        event=request.event,
        context=context,
    )

    return extract_intelligence_result(
        result
    )


# ============================================================
# WORK EXTRACTION
# ============================================================

def work_from_result(
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:

    """
    Extract actual structured pages if intelligence supplied
    them.

    No page generation occurs here.
    """

    pages = normalize_pages(
        result.get(
            "document_pages"
        )
    )

    if not pages:

        pages = normalize_pages(
            result.get(
                "pages"
            )
        )

    document_text = ""

    for key in (
        "document_text",
        "prepared_work",
        "document",
        "content",
        "text",
    ):

        value = result.get(key)

        if isinstance(
            value,
            str,
        ) and value.strip():

            document_text = value.strip()
            break

    return pages, document_text


# ============================================================
# JOB
# ============================================================

def create_job(
    job_id: str,
    request: Chat,
    intelligence_result: dict[str, Any],
) -> dict[str, Any]:

    pages, document_text = (
        work_from_result(
            intelligence_result
        )
    )

    job = {

        "job_id":
            job_id,

        "customer_id":
            request.customer_id,

        "service":
            request.service,

        "original_request":
            build_intelligence_request(
                request
            ),

        "context":
            build_context(
                request
            ),

        "intelligence_result":
            intelligence_result,

        "document_pages":
            pages,

        "document_text":
            document_text,

        "status":
            "reviewing",

        "review_started":
            True,

        "review_finished":
            False,

        "review_error":
            None,

        "review_pages":
            [
                {
                    "page_number":
                        page[
                            "page_number"
                        ],

                    "position":
                        page[
                            "position"
                        ],

                    "status":
                        "queued",

                    "content":
                        page[
                            "content"
                        ],

                    "review":
                        "",

                    "error":
                        None,
                }

                for page in pages
            ],

        "assembled_review":
            "",

        "current_version":
            1,

        "version_id":
            f"{job_id}:1",

        "approved":
            False,

        "paid":
            False,

        "progress":
            {
                "completed":
                    0,

                "total":
                    len(pages),
            },
    }

    _jobs[job_id] = job

    return job


# ============================================================
# JOB RESPONSE
# ============================================================

def make_job_response(
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

    return {

        "success":
            True,

        "job_id":
            job["job_id"],

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

        "total_pages":
            len(pages),

        "document_pages":
            pages,

        "pages":
            pages,

        "document_text":
            job.get(
                "document_text",
                "",
            ),

        "review_pages":
            review_pages,

        "assembled_review":
            job.get(
                "assembled_review",
                "",
            ),

        "progress":
            job.get(
                "progress",
                {
                    "completed": 0,
                    "total": len(pages),
                },
            ),

        "error":
            job.get(
                "review_error"
            ),

        "review_url":
            "/review.html"
            f"?job_id={job['job_id']}",
    }


# ============================================================
# REVIEW
#
# Review receives the actual work produced by intelligence.
# The API does not create artificial pages.
# ============================================================

async def run_review(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

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

        pages = job.get(
            "document_pages",
            [],
        )

        # If intelligence did not provide structured pages,
        # we still give the review intelligence the complete
        # document text.
        #
        # We do NOT manufacture pages here.

        review_method = getattr(
            ada,
            "review_document_pages",
            None,
        )

        if not callable(
            review_method
        ):

            raise AttributeError(
                "AdaResponse does not expose "
                "review_document_pages()."
            )

        result = await asyncio.to_thread(
            review_method,
            pages=pages,
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
            progress_callback=None,
        )

        result = extract_intelligence_result(
            result
        )

        returned_pages = normalize_pages(
            result.get(
                "pages"
            )
        )

        if returned_pages:

            job[
                "review_pages"
            ] = [

                {
                    "page_number":
                        page[
                            "page_number"
                        ],

                    "position":
                        page[
                            "position"
                        ],

                    "status":
                        "reviewed",

                    "content":
                        page.get(
                            "content",
                            "",
                        ),

                    "review":
                        page.get(
                            "review",
                            "",
                        ),

                    "error":
                        None,
                }

                for page in returned_pages
            ]

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

        job[
            "status"
        ] = "review_complete"

        job[
            "review_finished"
        ] = True

        job[
            "review_error"
        ] = None

        job[
            "progress"
        ] = {
            "completed":
                len(
                    job.get(
                        "document_pages",
                        [],
                    )
                ),

            "total":
                len(
                    job.get(
                        "document_pages",
                        [],
                    )
                ),
        }

    except asyncio.CancelledError:
        raise

    except Exception as error:

        job[
            "status"
        ] = "review_error"

        job[
            "review_finished"
        ] = True

        job[
            "review_error"
        ] = {
            "type":
                type(
                    error
                ).__name__,

            "message":
                str(
                    error
                ),
        }

        traceback.print_exc()


def start_review(
    job_id: str,
) -> bool:

    job = _jobs.get(
        job_id
    )

    if not job:
        return False

    task = _review_tasks.get(
        job_id
    )

    if (
        task
        and not task.done()
    ):
        return False

    _review_tasks[
        job_id
    ] = asyncio.create_task(
        run_review(
            job_id
        )
    )

    return True


# ============================================================
# HTML
# ============================================================

def find_file(
    name: str,
):

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


def serve_html(
    filename: str,
):

    path = find_file(
        filename
    )

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

        "success":
            True,

        "status":
            "ok",

        "api":
            "FastAPI",

        "intelligence":
            "AdaResponse",

        "model":
            get_ada_model(),

        "configured":
            is_configured(),

        "architecture":
            "intelligence-controlled",
    }


@app.get("/api/status")
async def api_status():

    return {

        "success":
            True,

        "api":
            "FastAPI",

        "intelligence":
            "AdaResponse",

        "model":
            get_ada_model(),

        "configured":
            is_configured(),

        "active_sessions":
            len(
                _sessions
            ),

        "active_jobs":
            len(
                _jobs
            ),

        "architecture":
            "intelligence-controlled",
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

        # ----------------------------------------------------
        # Upload is transport only.
        #
        # We deliberately do not perform API pagination or
        # document interpretation here.
        # ----------------------------------------------------

        return {

            "success":
                True,

            "filename":
                filename,

            "job_id":
                (
                    str(
                        job_id
                        or ""
                    ).strip()
                    or str(
                        uuid.uuid4()
                    )
                ),

            "customer_id":
                customer_id,

            "client_request_id":
                client_request_id,

            "service":
                service,

            "size":
                len(data),

            "message":
                "Document received successfully.",
        }

    except Exception as error:

        return application_error(
            "UPLOAD",
            error,
            400,
            "DOCUMENT_UPLOAD_ERROR",
        )


# ============================================================
# CHAT / INTELLIGENCE ENTRY
# ============================================================

@app.post("/api/chat")
async def chat(
    request: Chat,
):

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
        str(
            request.job_id
            or ""
        ).strip()
        or str(
            uuid.uuid4()
        )
    )

    context = build_context(
        request
    )

    intelligence_request = (
        build_intelligence_request(
            request
        )
    )

    if (
        request.guidance_only
        and not request.message.strip()
    ):

        return application_error(
            "GUIDANCE",
            "The guidance message is empty.",
            400,
            "EMPTY_GUIDANCE_MESSAGE",
        )

    if (
        not request.guidance_only
        and not intelligence_request
    ):

        return application_error(
            "CHAT",
            "No usable customer request was supplied.",
            400,
            "EMPTY_REQUEST",
        )

    try:

        ada = get_session(
            request.customer_id,
            job_id,
            request.service,
        )

        # ----------------------------------------------------
        # INTELLIGENCE OWNS THE REQUEST.
        # ----------------------------------------------------

        result = await ask_intelligence(
            ada=ada,
            request=request,
            intelligence_request=
                intelligence_request,
            context=context,
        )

        # ----------------------------------------------------
        # Guidance chat does not create a job unless
        # intelligence explicitly returns work.
        # ----------------------------------------------------

        if request.guidance_only:

            return {

                "success":
                    True,

                "reply":
                    str(
                        result.get(
                            "reply",
                            result.get(
                                "message",
                                "",
                            ),
                        )
                        or ""
                    ).strip(),

                "job_id":
                    job_id,

                "created_work":
                    False,

                "intelligence":
                    result,
            }

        # ----------------------------------------------------
        # Determine whether intelligence actually created
        # document/work output.
        # ----------------------------------------------------

        pages, document_text = (
            work_from_result(
                result
            )
        )

        explicit_work = (
            bool(pages)
            or bool(document_text)
            or bool(
                result.get(
                    "document"
                )
            )
            or bool(
                result.get(
                    "prepared_work"
                )
            )
        )

        create_requested = (
            request.create_work
            or event_value(
                request.event
            )
            in {
                "create_work",
                "create_document",
                "form_submitted_create_work",
                "send_for_review",
                "review",
            }
        )

        # ----------------------------------------------------
        # If intelligence created work, preserve it exactly.
        # ----------------------------------------------------

        if explicit_work or create_requested:

            if not explicit_work:

                return {

                    "success":
                        True,

                    "reply":
                        str(
                            result.get(
                                "reply",
                                result.get(
                                    "message",
                                    "",
                                ),
                            )
                            or ""
                        ).strip(),

                    "job_id":
                        job_id,

                    "created_work":
                        False,

                    "intelligence":
                        result,

                    "message":
                        "Intelligence has not returned "
                        "completed document work yet.",
                }

            job = create_job(
                job_id=
                    job_id,

                request=
                    request,

                intelligence_result=
                    result,
            )

            started = start_review(
                job_id
            )

            response = make_job_response(
                job
            )

            response.update(
                {

                    "reply":
                        str(
                            result.get(
                                "reply",
                                "Your work has been prepared "
                                "and sent for review.",
                            )
                            or ""
                        ).strip(),

                    "created_work":
                        True,

                    "work_created":
                        True,

                    "review_started":
                        started,

                    "intelligence":
                        result,
                }
            )

            return response

        # ----------------------------------------------------
        # Normal intelligent conversation.
        # ----------------------------------------------------

        return {

            "success":
                True,

            "reply":
                str(
                    result.get(
                        "reply",
                        result.get(
                            "message",
                            "",
                        ),
                    )
                    or ""
                ).strip(),

            "job_id":
                job_id,

            "service":
                request.service
                or getattr(
                    ada,
                    "service",
                    None,
                ),

            "created_work":
                False,

            "intelligence":
                result,
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

        "success":
            True,

        "job_id":
            job_id,

        "current_version":
            job[
                "current_version"
            ],

        "version_id":
            job[
                "version_id"
            ],

        "status":
            job[
                "status"
            ],

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

        "document_text":
            job.get(
                "document_text",
                "",
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

    if not job:

        return application_error(
            "CORRECTION",
            "Job not found.",
            404,
            "JOB_NOT_FOUND",
        )

    instruction = (
        request.instruction.strip()
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

            "correction_instruction":
                instruction,

            "progress":
                {
                    "completed":
                        0,

                    "total":
                        len(
                            job.get(
                                "document_pages",
                                [],
                            )
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
                    "AdaResponse does not expose "
                    "correct_document()."
                )

            result = await asyncio.to_thread(
                correct_method,

                document_pages=
                    job.get(
                        "document_pages",
                        [],
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

                progress_callback=
                    None,
            )

            corrected_result = (
                extract_intelligence_result(
                    result
                )
            )

            corrected_pages, corrected_text = (
                work_from_result(
                    corrected_result
                )
            )

            if not corrected_pages and not corrected_text:

                raise ValueError(
                    "Intelligence returned no corrected work."
                )

            job[
                "intelligence_result"
            ] = corrected_result

            job[
                "document_pages"
            ] = corrected_pages

            job[
                "document_text"
            ] = corrected_text

            job[
                "review_pages"
            ] = [

                {
                    "page_number":
                        page[
                            "page_number"
                        ],

                    "position":
                        page[
                            "position"
                        ],

                    "status":
                        "queued",

                    "content":
                        page.get(
                            "content",
                            "",
                        ),

                    "review":
                        "",

                    "error":
                        None,
                }

                for page in corrected_pages
            ]

            job[
                "status"
            ] = "reviewing"

            job[
                "review_started"
            ] = True

            job[
                "review_finished"
            ] = False

            job[
                "progress"
            ] = {

                "completed":
                    0,

                "total":
                    len(
                        corrected_pages
                    ),
            }

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
                    type(
                        error
                    ).__name__,

                "message":
                    str(
                        error
                    ),
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

        "success":
            True,

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
            "Correction has started. "
            "The corrected work will be reviewed again.",
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
        != job[
            "version_id"
        ]
    ):

        return application_error(
            "APPROVAL",
            "The supplied document version does not match.",
            409,
            "VERSION_MISMATCH",
        )

    if (
        job[
            "status"
        ]
        != "review_complete"
    ):

        return application_error(
            "APPROVAL",
            "The document review is not complete.",
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

        "success":
            True,

        "job_id":
            request.job_id,

        "version_id":
            request.version_id,

        "current_version":
            job[
                "current_version"
            ],

        "approved":
            True,

        "status":
            "approved",

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

        "payment_url":
            "/payment.html"
            f"?job_id={request.job_id}"
            f"&version_id={request.version_id}",
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
        != job[
            "version_id"
        ]
    ):

        return application_error(
            "PAYMENT",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job[
        "approved"
    ]:

        return application_error(
            "PAYMENT",
            "The document must be approved before payment.",
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

        "success":
            True,

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
                job.get(
                    "document_pages",
                    [],
                )
            ),

        "download_url":
            "/download.html"
            f"?job_id={job_id}"
            f"&version_id={version_id}",

        "api_download_url":
            "/api/download"
            f"?job_id={job_id}"
            f"&version_id={version_id}",
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
        != job[
            "version_id"
        ]
    ):

        return application_error(
            "PAYMENT_STATE",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    return {

        "success":
            True,

        "job_id":
            job_id,

        "version_id":
            version_id,

        "status":
            job[
                "status"
            ],

        "approved":
            job[
                "approved"
            ],

        "paid":
            job[
                "paid"
            ],

        "total_pages":
            len(
                job.get(
                    "document_pages",
                    [],
                )
            ),

        "payment_complete":
            job[
                "paid"
            ],
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
        != job[
            "version_id"
        ]
    ):

        return application_error(
            "DOWNLOAD",
            "Version mismatch.",
            409,
            "VERSION_MISMATCH",
        )

    if not job[
        "approved"
    ]:

        return application_error(
            "DOWNLOAD",
            "The current document version "
            "has not been approved.",
            409,
            "DOCUMENT_NOT_APPROVED",
        )

    if not job[
        "paid"
    ]:

        return application_error(
            "DOWNLOAD",
            "Payment for the current document "
            "version has not been completed.",
            409,
            "PAYMENT_NOT_COMPLETED",
        )

    return {

        "success":
            True,

        "job_id":
            job_id,

        "version_id":
            version_id,

        "status":
            "paid",

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

        "document_text":
            job.get(
                "document_text",
                "",
            ),

        "message":
            "The approved and paid work is ready "
            "for final document generation.",
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

        "success":
            True,

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
        "Intelligence model:",
        get_ada_model(),
    )

    print(
        "Intelligence configured:",
        is_configured(),
    )

    print(
        "API document pagination: DISABLED"
    )

    print(
        "API service rules: DISABLED"
    )

    print(
        "API keyword routing: DISABLED"
    )

    print(
        "Intelligence owns document creation: ENABLED"
    )

    print(
        "Intelligence owns service interpretation: ENABLED"
    )

    print(
        "Review workflow: ENABLED"
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
