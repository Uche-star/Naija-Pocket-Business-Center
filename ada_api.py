
"""
Naija Pocket Business Center
CURRENT FASTAPI APPLICATION

REAL REVIEW INTELLIGENCE ARCHITECTURE

Customer
    ↓
Workspace
    ↓ SEND
FastAPI /api/chat
    ↓
Actual document pages
    ↓
AdaResponse
    ↓
Groq
    ↓
Page 1 review
    ↓
FastAPI review state
    ↓
Page 2 review
    ↓
FastAPI review state
    ↓
...
    ↓
Complete assembled review
    ↓
review.html

FastAPI owns:
    - session
    - job
    - complete document
    - individual pages
    - review state
    - progress
    - versions
    - approval

AdaResponse owns:
    - intelligence
    - reasoning
    - Groq communication
    - page review
    - correction reasoning
    - document assembly logic

No keyword intelligence.
"""

from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
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

DEBUG_ERRORS = (
    os.getenv(
        "ADA_DEBUG_ERRORS",
        "true",
    )
    .strip()
    .lower()
    in {
        "1",
        "true",
        "yes",
        "on",
    }
)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(
    __file__
).resolve().parent


def find_file(
    filename: str,
) -> Path | None:

    candidates = [
        BASE_DIR / filename,
        BASE_DIR / "app" / filename,
        BASE_DIR / "static" / filename,
        BASE_DIR / "public" / filename,
        BASE_DIR / "assets" / filename,
    ]

    for path in candidates:

        if path.is_file():
            return path

    return None


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Naija Pocket Business Center",
    version="review-intelligence-v2",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# SESSIONS
# ============================================================

_sessions: dict[
    str,
    AdaResponse,
] = {}


def session_key(
    customer_id: str | None,
    job_id: str | None,
) -> str:

    customer = (
        str(
            customer_id
            or "anonymous"
        ).strip()
        or "anonymous"
    )

    job = (
        str(
            job_id
            or "default"
        ).strip()
        or "default"
    )

    return (
        f"{customer}:{job}"
    )


def get_session(
    customer_id: str | None,
    job_id: str | None,
    service: str | None = None,
) -> AdaResponse:

    key = session_key(
        customer_id,
        job_id,
    )

    if key not in _sessions:

        _sessions[key] = (
            AdaResponse(
                service=service
            )
        )

    elif service:

        _sessions[
            key
        ].set_service(
            service
        )

    return _sessions[key]


# ============================================================
# JOBS
# ============================================================

_jobs: dict[
    str,
    dict[str, Any],
] = {}


_review_tasks: dict[
    str,
    asyncio.Task,
] = {}


_correction_tasks: dict[
    str,
    asyncio.Task,
] = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    message: str = ""

    service: str | None = None

    event: str | None = None

    customer_id: str | None = None

    job_id: str | None = None

    client_request_id: str | None = None

    activate_intelligence: bool = True

    context: str | None = None

    form_data: dict[
        str,
        Any
    ] | None = None

    guidance_only: bool = False

    create_work: bool = False

    # --------------------------------------------------------
    # REAL DOCUMENT INPUT
    # --------------------------------------------------------

    document_pages: list[
        Any
    ] | None = None

    document_text: str | None = None


class CorrectionRequest(BaseModel):

    job_id: str

    instruction: str


class ApprovalRequest(BaseModel):

    job_id: str

    version_id: str


# ============================================================
# ERROR
# ============================================================

def error_response(
    *,
    stage: str,
    error: Exception | str,
    status_code: int = 500,
    error_code: str = "APPLICATION_ERROR",
):

    error_type = (
        type(error).__name__
        if isinstance(
            error,
            Exception,
        )
        else "Error"
    )

    error_message = str(
        error
    )

    print()
    print("=" * 78)
    print("NAIJA POCKET BUSINESS CENTER ERROR")
    print("=" * 78)
    print("Stage:", stage)
    print("Type:", error_type)
    print(
        "Message:",
        error_message,
    )
    print("=" * 78)

    if isinstance(
        error,
        Exception,
    ):
        traceback.print_exc()

    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "stage": stage,
            "error": error_code,
            "error_type": error_type,
            "error_message": (
                error_message
                if DEBUG_ERRORS
                else (
                    "An internal "
                    "application error occurred."
                )
            ),
        },
    )


# ============================================================
# REQUEST → COMPLETE CUSTOMER CONTEXT
# ============================================================

def build_customer_request(
    *,
    message: str,
    service: str | None,
    form_data: dict[str, Any] | None,
    context: str | None,
) -> str:

    parts = []

    if service:

        parts.append(
            "SELECTED SERVICE:\n"
            + str(
                service
            ).strip()
        )

    if form_data:

        lines = []

        for key, value in (
            form_data.items()
        ):

            value_text = str(
                value or ""
            ).strip()

            if not value_text:
                continue

            label = (
                str(key)
                .replace(
                    "_",
                    " ",
                )
                .strip()
                .title()
            )

            lines.append(
                f"{label}: {value_text}"
            )

        if lines:

            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(lines)
            )

    if context:

        context_text = str(
            context
        ).strip()

        if context_text:

            parts.append(
                "ADDITIONAL CONTEXT:\n"
                + context_text
            )

    if message:

        parts.append(
            "CUSTOMER REQUEST:\n"
            + str(
                message
            ).strip()
        )

    return "\n\n".join(
        parts
    ).strip()


# ============================================================
# APPLICATION CONTEXT
# ============================================================

def build_application_context(
    request: ChatRequest,
) -> str | None:

    parts = []

    if request.context:

        value = str(
            request.context
        ).strip()

        if value:
            parts.append(
                value
            )

    if request.customer_id:

        parts.append(
            "CUSTOMER ID:\n"
            + str(
                request.customer_id
            )
        )

    if request.client_request_id:

        parts.append(
            "CLIENT REQUEST ID:\n"
            + str(
                request.client_request_id
            )
        )

    return (
        "\n\n".join(
            parts
        )
        or None
    )


# ============================================================
# DOCUMENT INTAKE
# ============================================================

def extract_document_pages(
    request: ChatRequest,
) -> list[dict[str, Any]]:

    # --------------------------------------------------------
    # FIRST: explicit page array
    # --------------------------------------------------------

    if request.document_pages:

        pages = normalize_document_pages(
            request.document_pages
        )

        if pages:
            return pages

    # --------------------------------------------------------
    # SECOND: complete text
    # --------------------------------------------------------

    if request.document_text:

        return document_text_to_pages(
            request.document_text
        )

    # --------------------------------------------------------
    # THIRD: nothing supplied
    # --------------------------------------------------------

    return []


# ============================================================
# JOB CREATION
# ============================================================

def create_review_job(
    *,
    job_id: str,
    customer_id: str | None,
    service: str | None,
    original_request: str,
    context: str | None,
    client_request_id: str | None,
    document_pages: list[dict[str, Any]],
) -> dict[str, Any]:

    total_pages = len(
        document_pages
    )

    page_state = []

    for position, page in enumerate(
        document_pages,
        start=1,
    ):

        page_number = int(
            page.get(
                "page_number",
                position,
            )
        )

        content = str(
            page.get(
                "content",
                "",
            )
        )

        page_state.append(
            {
                "page_number":
                    page_number,

                "position":
                    position,

                "status":
                    "queued",

                "content":
                    content,

                "review":
                    "",

                "error":
                    None,
            }
        )

    job = {

        "job_id":
            job_id,

        "customer_id":
            customer_id,

        "service":
            service,

        "original_request":
            original_request,

        "context":
            context,

        "client_request_id":
            client_request_id,

        # ----------------------------------------------------
        # REVIEW STATE
        # ----------------------------------------------------

        "status":
            "reviewing",

        "review_started":
            True,

        "review_finished":
            False,

        "review_error":
            None,

        "progress":
            {
                "completed": 0,
                "total": total_pages,
            },

        # ----------------------------------------------------
        # COMPLETE ORIGINAL DOCUMENT
        # ----------------------------------------------------

        "document_pages":
            document_pages,

        # ----------------------------------------------------
        # REVIEWED PAGES
        # ----------------------------------------------------

        "review_pages":
            page_state,

        # ----------------------------------------------------
        # ASSEMBLED REVIEW
        # ----------------------------------------------------

        "assembled_review":
            "",

        # ----------------------------------------------------
        # VERSION
        # ----------------------------------------------------

        "current_version":
            1,

        "version_id":
            f"{job_id}:1",

        # ----------------------------------------------------
        # APPROVAL
        # ----------------------------------------------------

        "approved":
            False,

        "paid":
            False,
    }

    _jobs[
        job_id
    ] = job

    return job


# ============================================================
# REVIEW PROGRESS CALLBACK
# ============================================================

def make_review_progress_callback(
    job_id: str,
):

    def callback(
        update: dict[str, Any]
    ):

        job = _jobs.get(
            job_id
        )

        if job is None:
            return

        update_type = update.get(
            "type"
        )

        page_number = update.get(
            "page_number"
        )

        position = update.get(
            "position"
        )

        total_pages = update.get(
            "total_pages"
        )

        if total_pages:

            job[
                "progress"
            ][
                "total"
            ] = total_pages

        # ----------------------------------------------------
        # PAGE STARTED
        # ----------------------------------------------------

        if (
            update_type
            == "page_started"
        ):

            for page in job[
                "review_pages"
            ]:

                if page[
                    "page_number"
                ] == page_number:

                    page[
                        "status"
                    ] = "reviewing"

                    break

            job[
                "status"
            ] = "reviewing"

        # ----------------------------------------------------
        # PAGE COMPLETED
        # ----------------------------------------------------

        elif (
            update_type
            == "page_completed"
        ):

            for page in job[
                "review_pages"
            ]:

                if page[
                    "page_number"
                ] == page_number:

                    page[
                        "status"
                    ] = "reviewed"

                    page[
                        "content"
                    ] = update.get(
                        "content",
                        page.get(
                            "content",
                            "",
                        ),
                    )

                    page[
                        "review"
                    ] = update.get(
                        "review",
                        "",
                    )

                    page[
                        "error"
                    ] = None

                    break

            job[
                "progress"
            ][
                "completed"
            ] = position or (
                job[
                    "progress"
                ][
                    "completed"
                ]
            )

            # ------------------------------------------------
            # IMPORTANT:
            # The review page can now see the page immediately.
            # ------------------------------------------------

            job[
                "assembled_review"
            ] = build_live_review(
                job
            )

        # ----------------------------------------------------
        # PAGE ERROR
        # ----------------------------------------------------

        elif (
            update_type
            == "page_error"
        ):

            for page in job[
                "review_pages"
            ]:

                if page[
                    "page_number"
                ] == page_number:

                    page[
                        "status"
                    ] = "error"

                    page[
                        "error"
                    ] = update.get(
                        "error"
                    )

                    break

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        elif (
            update_type
            == "review_completed"
        ):

            job[
                "assembled_review"
            ] = update.get(
                "assembled_review",
                "",
            )

            job[
                "status"
            ] = "review_complete"

            job[
                "review_finished"
            ] = True

            job[
                "progress"
            ][
                "completed"
            ] = job[
                "progress"
            ][
                "total"
            ]

    return callback


# ============================================================
# LIVE REVIEW ASSEMBLY
# ============================================================

def build_live_review(
    job: dict[str, Any],
) -> str:

    parts = []

    for page in job.get(
        "review_pages",
        [],
    ):

        review = str(
            page.get(
                "review",
                "",
            )
        ).strip()

        if not review:
            continue

        page_number = page.get(
            "page_number"
        )

        parts.append(
            "PAGE "
            + str(page_number)
            + "\n\n"
            + review
        )

    if not parts:

        return ""

    return (
        "COMPLETE DOCUMENT REVIEW\n\n"
        + "\n\n".join(
            parts
        )
    )


# ============================================================
# REVIEW WORKER
# ============================================================

async def run_review_job(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    try:

        ada = get_session(
            customer_id=job.get(
                "customer_id"
            ),
            job_id=job_id,
            service=job.get(
                "service"
            ),
        )

        pages = job.get(
            "document_pages",
            [],
        )

        print()
        print("=" * 78)
        print("ADA REVIEW INTELLIGENCE STARTED")
        print("=" * 78)
        print("Job:", job_id)
        print(
            "Pages:",
            len(pages),
        )
        print(
            "Service:",
            job.get(
                "service"
            ),
        )
        print("=" * 78)

        callback = (
            make_review_progress_callback(
                job_id
            )
        )

        result = await asyncio.to_thread(
            ada.review_document_pages,
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
            progress_callback=callback,
        )

        # ----------------------------------------------------
        # Store final assembled review.
        # ----------------------------------------------------

        job[
            "assembled_review"
        ] = result.get(
            "assembled_review",
            "",
        )

        job[
            "status"
        ] = "review_complete"

        job[
            "review_finished"
        ] = True

        job[
            "progress"
        ][
            "completed"
        ] = job[
            "progress"
        ][
            "total"
        ]

        print()
        print("=" * 78)
        print("ADA REVIEW INTELLIGENCE COMPLETE")
        print("=" * 78)
        print(
            "Job:",
            job_id,
        )
        print(
            "Pages reviewed:",
            len(
                result.get(
                    "pages",
                    [],
                )
            ),
        )
        print("=" * 78)

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
                type(error).__name__,

            "message":
                str(error),
        }

        traceback.print_exc()


def ensure_review_started(
    job_id: str,
):

    task = _review_tasks.get(
        job_id
    )

    if (
        task is not None
        and not task.done()
    ):
        return

    _review_tasks[
        job_id
    ] = asyncio.create_task(
        run_review_job(
            job_id
        )
    )


# ============================================================
# CUSTOMER PAGES
# ============================================================

@app.get("/")
async def customer_home():

    file = find_file(
        "index.html"
    )

    if file is None:

        return error_response(
            stage="CUSTOMER_HOME",
            error="index.html was not found.",
            status_code=500,
            error_code="INDEX_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


@app.get("/index.html")
async def customer_index():

    return await customer_home()


@app.get("/conversation.html")
async def customer_conversation():

    file = find_file(
        "conversation.html"
    )

    if file is None:

        return error_response(
            stage="CONVERSATION_PAGE",
            error=(
                "conversation.html was not found."
            ),
            status_code=404,
            error_code=(
                "CONVERSATION_HTML_NOT_FOUND"
            ),
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


@app.get("/workspace.html")
async def customer_workspace():

    file = find_file(
        "workspace.html"
    )

    if file is None:

        return error_response(
            stage="WORKSPACE_PAGE",
            error=(
                "workspace.html was not found."
            ),
            status_code=404,
            error_code=(
                "WORKSPACE_HTML_NOT_FOUND"
            ),
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


@app.get("/review.html")
async def customer_review():

    file = find_file(
        "review.html"
    )

    if file is None:

        return error_response(
            stage="REVIEW_PAGE",
            error="review.html was not found.",
            status_code=404,
            error_code="REVIEW_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
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
        "intelligence":
            "AdaResponse",
        "model":
            get_ada_model(),
        "configured":
            is_configured(),
    }


@app.get("/api/status")
async def api_status():

    return {
        "success": True,
        "api":
            "FastAPI",
        "intelligence":
            "AdaResponse",
        "model":
            get_ada_model(),
        "configured":
            is_configured(),
        "active_sessions":
            len(_sessions),
        "active_jobs":
            len(_jobs),
        "active_review_tasks":
            len(_review_tasks),
        "active_correction_tasks":
            len(_correction_tasks),
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    print()
    print("=" * 78)
    print("WORKSPACE SEND → FASTAPI")
    print("=" * 78)

    message = str(
        request.message
        or ""
    ).strip()

    if not message:

        return error_response(
            stage="CHAT_VALIDATION",
            error="The chat message is empty.",
            status_code=400,
            error_code="EMPTY_MESSAGE",
        )

    if not request.activate_intelligence:

        return error_response(
            stage="INTELLIGENCE_ACTIVATION",
            error=(
                "Intelligence activation is disabled."
            ),
            status_code=400,
            error_code=(
                "INTELLIGENCE_NOT_ACTIVATED"
            ),
        )

    if not is_configured():

        return error_response(
            stage="INTELLIGENCE_CONFIGURATION",
            error=(
                "AdaResponse is not configured."
            ),
            status_code=503,
            error_code=(
                "INTELLIGENCE_NOT_CONFIGURED"
            ),
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

    application_context = (
        build_application_context(
            request
        )
    )

    # ========================================================
    # GUIDANCE
    # ========================================================

    if request.guidance_only:

        try:

            ada = get_session(
                customer_id=(
                    request.customer_id
                ),
                job_id=job_id,
                service=(
                    request.service
                ),
            )

            reply = ada.respond(
                message=message,
                service=request.service,
                event=request.event,
                context=application_context,
            )

            return {
                "success":
                    True,

                "reply":
                    str(
                        reply
                        or ""
                    ).strip(),

                "job_id":
                    job_id,

                "created_work":
                    False,
            }

        except Exception as error:

            return error_response(
                stage="GUIDANCE_RESPONSE",
                error=error,
                status_code=500,
                error_code=(
                    "GUIDANCE_ERROR"
                ),
            )

    # ========================================================
    # ACTUAL DOCUMENT INTAKE
    # ========================================================

    document_pages = (
        extract_document_pages(
            request
        )
    )

    # ========================================================
    # SEND FOR REVIEW
    # ========================================================
    #
    # If Workspace sends actual pages, THIS is the point where
    # Ada immediately takes over.
    #
    # No preliminary normal chat call.
    # No keyword decision.
    # No document generation detour.
    # ========================================================

    is_review_request = (
        bool(document_pages)
        and (
            request.create_work
            or (
                str(
                    request.event
                    or ""
                ).strip().lower()
                in {
                    "review",
                    "review_requested",
                    "send_for_review",
                    "review_document",
                    "review_called",
                }
            )
        )
    )

    if is_review_request:

        complete_request = (
            build_customer_request(
                message=message,
                service=request.service,
                form_data=request.form_data,
                context=request.context,
            )
        )

        job = _jobs.get(
            job_id
        )

        if job is None:

            job = create_review_job(
                job_id=job_id,
                customer_id=(
                    request.customer_id
                ),
                service=(
                    request.service
                ),
                original_request=(
                    complete_request
                ),
                context=(
                    application_context
                ),
                client_request_id=(
                    request.client_request_id
                ),
                document_pages=(
                    document_pages
                ),
            )

        else:

            # ------------------------------------------------
            # Replace document only when a new SEND explicitly
            # supplies document pages.
            # ------------------------------------------------

            job[
                "document_pages"
            ] = document_pages

            job[
                "review_pages"
            ] = [
                {
                    "page_number":
                        int(
                            page.get(
                                "page_number",
                                index,
                            )
                        ),

                    "position":
                        index,

                    "status":
                        "queued",

                    "content":
                        str(
                            page.get(
                                "content",
                                "",
                            )
                        ),

                    "review":
                        "",

                    "error":
                        None,
                }

                for index, page in enumerate(
                    document_pages,
                    start=1,
                )
            ]

            job[
                "original_request"
            ] = complete_request

            job[
                "context"
            ] = application_context

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
                "review_error"
            ] = None

            job[
                "assembled_review"
            ] = ""

            job[
                "progress"
            ] = {
                "completed":
                    0,

                "total":
                    len(
                        document_pages
                    ),
            }

        ensure_review_started(
            job_id
        )

        print(
            "ADA INTELLIGENCE ACTIVATED."
        )

        print(
            "Document pages received:",
            len(
                document_pages
            ),
        )

        print(
            "Review task started:",
            job_id,
        )

        print("=" * 78)

        return {
            "success":
                True,

            "reply":
                (
                    "Your document has been received. "
                    "Ada is now reviewing it page by page."
                ),

            "job_id":
                job_id,

            "service":
                request.service,

            "created_work":
                True,

            "review_started":
                True,

            "status":
                "reviewing",

            "total_pages":
                len(
                    document_pages
                ),

            "progress":
                job[
                    "progress"
                ],

            "review_url":
                "/review.html?job_id="
                + job_id,
        }

    # ========================================================
    # IMPORTANT VALIDATION
    # ========================================================
    #
    # If this is intended to be a review but Workspace did not
    # actually send pages, do NOT pretend that review started.
    # ========================================================

    if (
        request.create_work
        and not document_pages
        and (
            str(
                request.event
                or ""
            ).strip().lower()
            in {
                "review",
                "review_requested",
                "send_for_review",
                "review_document",
                "review_called",
            }
        )
    ):

        return error_response(
            stage="DOCUMENT_INTAKE",
            error=(
                "Workspace requested document review "
                "but did not send any document pages."
            ),
            status_code=400,
            error_code=(
                "NO_DOCUMENT_PAGES"
            ),
        )

    # ========================================================
    # NORMAL ADA CONVERSATION
    # ========================================================

    try:

        ada = get_session(
            customer_id=(
                request.customer_id
            ),
            job_id=job_id,
            service=(
                request.service
            ),
        )

        reply = ada.respond(
            message=message,
            service=request.service,
            event=request.event,
            context=application_context,
        )

        return {
            "success":
                True,

            "reply":
                str(
                    reply
                    or ""
                ).strip(),

            "job_id":
                job_id,

            "service":
                request.service
                or ada.service,

            "created_work":
                False,
        }

    except Exception as error:

        return error_response(
            stage="ADA_RESPONSE",
            error=error,
            status_code=500,
            error_code=(
                "ADA_RESPONSE_ERROR"
            ),
        )


# ============================================================
# REVIEW STATE
# ============================================================

@app.get("/api/review")
async def review(
    job_id: str,
):

    job_id = str(
        job_id
        or ""
    ).strip()

    if not job_id:

        return error_response(
            stage="REVIEW",
            error="job_id is required.",
            status_code=400,
            error_code="JOB_ID_REQUIRED",
        )

    job = _jobs.get(
        job_id
    )

    if job is None:

        return error_response(
            stage="REVIEW",
            error=(
                "The requested review job "
                "does not exist."
            ),
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    # --------------------------------------------------------
    # Do not recreate the task if already running.
    # --------------------------------------------------------

    ensure_review_started(
        job_id
    )

    return {
        "success":
            True,

        "job_id":
            job["job_id"],

        "status":
            job["status"],

        "current_version":
            job["current_version"],

        "version_id":
            job["version_id"],

        "progress":
            job["progress"],

        # ----------------------------------------------------
        # THE ACTUAL DOCUMENT
        # ----------------------------------------------------

        "document_pages":
            job[
                "document_pages"
            ],

        # ----------------------------------------------------
        # INDIVIDUAL REVIEW RESULTS
        # ----------------------------------------------------

        "review_pages":
            job[
                "review_pages"
            ],

        # ----------------------------------------------------
        # LIVE ASSEMBLED REVIEW
        # ----------------------------------------------------

        "assembled_review":
            job[
                "assembled_review"
            ],

        "approved":
            job[
                "approved"
            ],

        "paid":
            job[
                "paid"
            ],

        "error":
            job[
                "review_error"
            ],
    }


# ============================================================
# CORRECTION
# ============================================================

def make_correction_callback(
    job_id: str,
):

    def callback(
        update: dict[str, Any]
    ):

        job = _jobs.get(
            job_id
        )

        if job is None:
            return

        update_type = update.get(
            "type"
        )

        page_number = update.get(
            "page_number"
        )

        if (
            update_type
            == "correction_page_started"
        ):

            for page in job[
                "document_pages"
            ]:

                if (
                    page.get(
                        "page_number"
                    )
                    == page_number
                ):

                    page[
                        "status"
                    ] = "correcting"

                    break

        elif (
            update_type
            == "correction_page_completed"
        ):

            for page in job[
                "document_pages"
            ]:

                if (
                    page.get(
                        "page_number"
                    )
                    == page_number
                ):

                    page[
                        "content"
                    ] = update.get(
                        "content",
                        page.get(
                            "content",
                            "",
                        ),
                    )

                    page[
                        "status"
                    ] = "corrected"

                    break

            job[
                "progress"
            ] = {
                "completed":
                    update.get(
                        "position",
                        0,
                    ),

                "total":
                    update.get(
                        "total_pages",
                        len(
                            job[
                                "document_pages"
                            ]
                        ),
                    ),
            }

    return callback


@app.post("/api/correct")
async def correct(
    request: CorrectionRequest,
):

    job = _jobs.get(
        request.job_id
    )

    if job is None:

        return error_response(
            stage="CORRECTION",
            error="Job not found.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    instruction = str(
        request.instruction
        or ""
    ).strip()

    if not instruction:

        return error_response(
            stage="CORRECTION",
            error=(
                "Correction instruction is empty."
            ),
            status_code=400,
            error_code="EMPTY_CORRECTION",
        )

    if job[
        "status"
    ] == "reviewing":

        return error_response(
            stage="CORRECTION",
            error=(
                "Ada is still reviewing the document. "
                "Please wait until review is complete."
            ),
            status_code=409,
            error_code=(
                "REVIEW_STILL_RUNNING"
            ),
        )

    if not job.get(
        "document_pages"
    ):

        return error_response(
            stage="CORRECTION",
            error=(
                "There is no document available "
                "for correction."
            ),
            status_code=409,
            error_code=(
                "NO_DOCUMENT"
            ),
        )

    # --------------------------------------------------------
    # New version
    # --------------------------------------------------------

    job[
        "current_version"
    ] += 1

    job[
        "version_id"
    ] = (
        f"{request.job_id}:"
        f"{job['current_version']}"
    )

    job[
        "approved"
    ] = False

    job[
        "status"
    ] = "correcting"

    job[
        "review_finished"
    ] = False

    job[
        "review_error"
    ] = None

    job[
        "progress"
    ] = {
        "completed":
            0,

        "total":
            len(
                job[
                    "document_pages"
                ]
            ),
    }

    job[
        "correction_instruction"
    ] = instruction

    old_task = (
        _correction_tasks.pop(
            request.job_id,
            None,
        )
    )

    if (
        old_task is not None
        and not old_task.done()
    ):
        old_task.cancel()

    task = asyncio.create_task(
        run_correction_job(
            request.job_id,
            instruction,
        )
    )

    _correction_tasks[
        request.job_id
    ] = task

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
    }


# ============================================================
# CORRECTION WORKER
# ============================================================

async def run_correction_job(
    job_id: str,
    instruction: str,
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    try:

        ada = get_session(
            customer_id=(
                job.get(
                    "customer_id"
                )
            ),
            job_id=job_id,
            service=(
                job.get(
                    "service"
                )
            ),
        )

        callback = (
            make_correction_callback(
                job_id
            )
        )

        result = await asyncio.to_thread(
            ada.correct_document,
            document_pages=(
                job[
                    "document_pages"
                ]
            ),
            correction=instruction,
            service=(
                job.get(
                    "service"
                )
            ),
            context=(
                job.get(
                    "context"
                )
            ),
            progress_callback=callback,
        )

        corrected_pages = (
            result.get(
                "pages",
                [],
            )
        )

        job[
            "document_pages"
        ] = corrected_pages

        # ----------------------------------------------------
        # After correction, automatically review the corrected
        # version again.
        # ----------------------------------------------------

        job[
            "review_pages"
        ] = [
            {
                "page_number":
                    int(
                        page.get(
                            "page_number",
                            index,
                        )
                    ),

                "position":
                    index,

                "status":
                    "queued",

                "content":
                    str(
                        page.get(
                            "content",
                            "",
                        )
                    ),

                "review":
                    "",

                "error":
                    None,
            }

            for index, page in enumerate(
                corrected_pages,
                start=1,
            )
        ]

        job[
            "assembled_review"
        ] = ""

        job[
            "status"
        ] = "reviewing"

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

        # ----------------------------------------------------
        # Re-review the corrected document.
        # ----------------------------------------------------

        existing_review_task = (
            _review_tasks.pop(
                job_id,
                None,
            )
        )

        if (
            existing_review_task is not None
            and not existing_review_task.done()
        ):
            existing_review_task.cancel()

        ensure_review_started(
            job_id
        )

    except Exception as error:

        job[
            "status"
        ] = "correction_error"

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


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve(
    request: ApprovalRequest,
):

    job = _jobs.get(
        request.job_id
    )

    if job is None:

        return error_response(
            stage="APPROVAL",
            error="Job not found.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    if (
        request.version_id
        != job[
            "version_id"
        ]
    ):

        return error_response(
            stage="APPROVAL",
            error=(
                "The supplied document version "
                "does not match the current version."
            ),
            status_code=409,
            error_code="VERSION_MISMATCH",
        )

    if job[
        "status"
    ] != "review_complete":

        return error_response(
            stage="APPROVAL",
            error=(
                "The document review is not complete."
            ),
            status_code=409,
            error_code=(
                "REVIEW_NOT_COMPLETE"
            ),
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

        "approved":
            True,
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

    if job is None:

        return error_response(
            stage="DOWNLOAD",
            error="Job not found.",
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    if version_id != job[
        "version_id"
    ]:

        return error_response(
            stage="DOWNLOAD",
            error="Version mismatch.",
            status_code=409,
            error_code="VERSION_MISMATCH",
        )

    return error_response(
        stage="DOWNLOAD",
        error=(
            "Payment and final download workflow "
            "has not been connected yet."
        ),
        status_code=409,
        error_code=(
            "DOWNLOAD_NOT_CONNECTED"
        ),
    )


# ============================================================
# CLEAR CHAT
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
):

    try:

        key = session_key(
            customer_id,
            job_id,
        )

        session = _sessions.get(
            key
        )

        if session:
            session.clear_history()

        return {
            "success":
                True,

            "message":
                "Conversation cleared.",
        }

    except Exception as error:

        return error_response(
            stage="CLEAR_CHAT",
            error=error,
            status_code=500,
            error_code=(
                "CLEAR_CHAT_ERROR"
            ),
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print()
    print("=" * 78)
    print("NAIJA POCKET BUSINESS CENTER")
    print("FASTAPI + ADA RESPONSE REVIEW INTELLIGENCE")
    print("=" * 78)

    print(
        "API:",
        "FastAPI",
    )

    print(
        "Intelligence:",
        "AdaResponse",
    )

    print(
        "Model:",
        get_ada_model(),
    )

    print(
        "Configured:",
        is_configured(),
    )

    print(
        "Workspace:",
        "/workspace.html",
    )

    print(
        "Chat:",
        "/api/chat",
    )

    print(
        "Review:",
        "/api/review",
    )

    print(
        "Correction:",
        "/api/correct",
    )

    print(
        "Approval:",
        "/api/approve",
    )

    print(
        "Page-by-page intelligence:",
        "ENABLED",
    )

    print(
        "Progressive review:",
        "ENABLED",
    )

    print(
        "Complete document preservation:",
        "ENABLED",
    )

    print(
        "Keyword intelligence:",
        "DISABLED",
    )

    print("=" * 78)
    print()


# ============================================================
# LOCAL START
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        "ada_api:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
