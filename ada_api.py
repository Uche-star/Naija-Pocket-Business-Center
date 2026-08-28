"""
Naija Pocket Business Center
CURRENT FASTAPI APPLICATION

ARCHITECTURE
------------

Customer
    ↓
FastAPI
    ↓
AdaResponse
    ↓
Groq
    ↓
FastAPI Job State
    ↓
Review Page

RESPONSIBILITIES
----------------

FastAPI:
    - HTTP/API connection
    - customer/session identity
    - form data collection
    - job creation
    - document-generation task management
    - review state
    - correction state
    - approval state

AdaResponse:
    - all intelligence
    - all Groq communication
    - all document generation
    - all document assembly
    - all conversational reasoning

NO:
    - Flask
    - phone_bridge.py
    - AdaController
    - AdaAIEngine
    - keyword intelligence
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

BASE_DIR = Path(__file__).resolve().parent


def find_file(filename: str) -> Path | None:

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
    version="current-fastapi",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ADA SESSIONS
# ============================================================

_sessions: dict[str, AdaResponse] = {}


def session_key(
    customer_id: str | None,
    job_id: str | None,
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
    customer_id: str | None,
    job_id: str | None,
    service: str | None = None,
) -> AdaResponse:

    key = session_key(
        customer_id,
        job_id,
    )

    if key not in _sessions:

        _sessions[key] = AdaResponse(
            service=service
        )

    elif service:

        _sessions[key].set_service(
            service
        )

    return _sessions[key]


# ============================================================
# JOB STATE
# ============================================================

_jobs: dict[str, dict[str, Any]] = {}

_generation_tasks: dict[
    str,
    asyncio.Task
] = {}


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    message: str = Field(
        default=""
    )

    service: str | None = None

    event: str | None = None

    customer_id: str | None = None

    job_id: str | None = None

    client_request_id: str | None = None

    activate_intelligence: bool = True

    context: str | None = None

    # --------------------------------------------------------
    # IMPORTANT
    # These fields are sent by workspace.html.
    # The previous API silently discarded them.
    # --------------------------------------------------------

    form_data: dict[str, Any] | None = None

    guidance_only: bool = False

    create_work: bool = False


class CorrectionRequest(BaseModel):

    job_id: str

    instruction: str


class ApprovalRequest(BaseModel):

    job_id: str

    version_id: str


# ============================================================
# ERROR HANDLING
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
        if isinstance(error, Exception)
        else "Error"
    )

    error_message = str(error)

    print()
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER ERROR")
    print("=" * 70)
    print("Stage:", stage)
    print("Type:", error_type)
    print("Message:", error_message)
    print("=" * 70)

    if isinstance(error, Exception):
        traceback.print_exc()

    content = {
        "success": False,
        "stage": stage,
        "error": error_code,
        "error_type": error_type,
        "error_message": (
            error_message
            if DEBUG_ERRORS
            else "An internal application error occurred."
        ),
    }

    return JSONResponse(
        status_code=status_code,
        content=content,
    )


# ============================================================
# FORM DATA → CUSTOMER REQUEST
# ============================================================

def build_customer_request(
    *,
    message: str,
    service: str | None,
    form_data: dict[str, Any] | None,
    context: str | None,
) -> str:

    parts: list[str] = []

    if service:

        parts.append(
            "SELECTED SERVICE:\n"
            + str(service).strip()
        )

    if form_data:

        form_lines: list[str] = []

        for key, value in form_data.items():

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

            form_lines.append(
                f"{label}: {value_text}"
            )

        if form_lines:

            parts.append(
                "CUSTOMER PROVIDED SERVICE INFORMATION:\n"
                + "\n".join(form_lines)
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

    message_text = str(
        message or ""
    ).strip()

    if message_text:

        parts.append(
            "CUSTOMER REQUEST:\n"
            + message_text
        )

    return "\n\n".join(parts).strip()


# ============================================================
# JOB CREATION
# ============================================================

def create_job(
    *,
    job_id: str,
    customer_id: str | None,
    service: str | None,
    original_request: str,
    context: str | None,
    client_request_id: str | None,
) -> dict[str, Any]:

    job = {
        "job_id": job_id,

        "customer_id": customer_id,

        "service": service,

        "original_request": original_request,

        "context": context,

        "client_request_id":
            client_request_id,

        "status": "generating",

        "current_version": 1,

        "version_id": (
            f"{job_id}:1"
        ),

        "approved": False,

        "paid": False,

        "progress": {
            "completed": 0,
            "total": 1,
        },

        "sections": [
            {
                "section_id":
                    f"{job_id}:section:1",

                "section_order": 1,

                "title":
                    service
                    or "Your Requested Service",

                "status": "generating",
            }
        ],

        "document_html": "",

        "error": None,

        "generation_started": False,

        "generation_finished": False,
    }

    _jobs[job_id] = job

    return job


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
            error_code="CONVERSATION_HTML_NOT_FOUND",
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
            error_code="WORKSPACE_HTML_NOT_FOUND",
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

    try:

        configured = is_configured()

    except Exception as error:

        return error_response(
            stage="HEALTH",
            error=error,
            status_code=500,
            error_code="HEALTH_ERROR",
        )

    return {
        "success": True,
        "status": "ok",
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": configured,
    }


@app.get("/api/status")
async def api_status():

    try:

        configured = is_configured()

    except Exception as error:

        return error_response(
            stage="API_STATUS",
            error=error,
            status_code=500,
            error_code="STATUS_ERROR",
        )

    return {
        "success": True,
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": configured,
        "active_sessions": len(_sessions),
        "active_jobs": len(_jobs),
        "active_generation_tasks": len(
            _generation_tasks
        ),
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    print()
    print("-" * 70)
    print("CHAT REQUEST RECEIVED")
    print("-" * 70)

    message = str(
        request.message or ""
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
            error_code="INTELLIGENCE_NOT_ACTIVATED",
        )

    try:

        configured = is_configured()

    except Exception as error:

        return error_response(
            stage="CONFIGURATION_CHECK",
            error=error,
            status_code=500,
            error_code="CONFIGURATION_ERROR",
        )

    if not configured:

        return error_response(
            stage="INTELLIGENCE_CONFIGURATION",
            error=(
                "AdaResponse is not configured."
            ),
            status_code=503,
            error_code="INTELLIGENCE_NOT_CONFIGURED",
        )

    # --------------------------------------------------------
    # JOB ID
    # --------------------------------------------------------

    job_id = (
        str(
            request.job_id or ""
        ).strip()
        or str(uuid.uuid4())
    )

    # --------------------------------------------------------
    # APPLICATION CONTEXT
    # --------------------------------------------------------

    context_parts: list[str] = []

    if request.context:

        value = str(
            request.context
        ).strip()

        if value:
            context_parts.append(
                value
            )

    if request.customer_id:

        context_parts.append(
            "CUSTOMER ID:\n"
            + str(
                request.customer_id
            )
        )

    if request.client_request_id:

        context_parts.append(
            "CLIENT REQUEST ID:\n"
            + str(
                request.client_request_id
            )
        )

    application_context = (
        "\n\n".join(
            context_parts
        )
        or None
    )

    # ========================================================
    # GUIDANCE REQUEST
    # ========================================================
    #
    # Guidance must NEVER create a document job.
    #
    # workspace.html uses this when the service page opens.
    # ========================================================

    if request.guidance_only:

        try:

            ada = get_session(
                customer_id=request.customer_id,
                job_id=job_id,
                service=request.service,
            )

            reply = ada.respond(
                message=message,
                service=request.service,
                event=request.event,
                context=application_context,
            )

            return {
                "success": True,
                "reply": str(
                    reply or ""
                ).strip(),
                "job_id": job_id,
                "service": request.service,
                "created_work": False,
            }

        except Exception as error:

            return error_response(
                stage="GUIDANCE_RESPONSE",
                error=error,
                status_code=500,
                error_code="GUIDANCE_ERROR",
            )

    # ========================================================
    # DOCUMENT CREATION REQUEST
    # ========================================================
    #
    # THIS IS THE IMPORTANT PATH.
    #
    # Do NOT call ada.respond() here first.
    #
    # The previous implementation did:
    #
    #     ada.respond()
    #          ↓
    #     document generation
    #
    # and then later:
    #
    #     generate_complete_document()
    #
    # That generated the work twice.
    #
    # Now FastAPI creates the job and lets the dedicated
    # generation task call AdaResponse exactly once.
    # ========================================================

    if request.create_work:

        complete_request = build_customer_request(
            message=message,
            service=request.service,
            form_data=request.form_data,
            context=request.context,
        )

        if not complete_request:

            return error_response(
                stage="DOCUMENT_REQUEST",
                error=(
                    "No usable customer information "
                    "was supplied."
                ),
                status_code=400,
                error_code="EMPTY_DOCUMENT_REQUEST",
            )

        existing_job = _jobs.get(
            job_id
        )

        if existing_job is None:

            job = create_job(
                job_id=job_id,
                customer_id=request.customer_id,
                service=request.service,
                original_request=complete_request,
                context=application_context,
                client_request_id=(
                    request.client_request_id
                ),
            )

        else:

            job = existing_job

            job["service"] = (
                request.service
                or job.get("service")
            )

            job["original_request"] = (
                complete_request
            )

            if application_context:

                job["context"] = (
                    application_context
                )

            job["status"] = "generating"
            job["error"] = None
            job["generation_finished"] = False

        # ----------------------------------------------------
        # Start ONE background generation task.
        # ----------------------------------------------------

        ensure_generation_started(
            job_id
        )

        print(
            "DOCUMENT JOB CREATED:",
            job_id,
        )

        print(
            "FORM DATA RECEIVED:",
            bool(request.form_data),
        )

        if request.form_data:

            print(
                "FORM FIELDS:",
                ", ".join(
                    str(
                        key
                    )
                    for key in request.form_data
                ),
            )

        print("-" * 70)

        return {
            "success": True,

            "reply": (
                "Your request has been received. "
                "Ada is now preparing your complete "
                "document for review."
            ),

            "job_id": job_id,

            "service": request.service,

            "customer_id":
                request.customer_id,

            "client_request_id":
                request.client_request_id,

            "created_work": True,

            "status": "generating",

            "review_url":
                "/review.html?job_id="
                + job_id,
        }

    # ========================================================
    # NORMAL CONVERSATION
    # ========================================================
    #
    # Customer messages that are NOT explicitly asking the
    # application to create work remain ordinary AdaResponse
    # conversations.
    # ========================================================

    try:

        ada = get_session(
            customer_id=request.customer_id,
            job_id=job_id,
            service=request.service,
        )

        reply = ada.respond(
            message=message,
            service=request.service,
            event=request.event,
            context=application_context,
        )

    except Exception as error:

        return error_response(
            stage="ADA_RESPONSE",
            error=error,
            status_code=500,
            error_code="ADA_RESPONSE_ERROR",
        )

    reply = str(
        reply or ""
    ).strip()

    if not reply:

        return error_response(
            stage="ADA_RESPONSE",
            error=(
                "AdaResponse returned an empty response."
            ),
            status_code=500,
            error_code="EMPTY_ADA_RESPONSE",
        )

    print(
        "Normal AdaResponse conversation completed."
    )

    print("-" * 70)

    return {
        "success": True,

        "reply": reply,

        "job_id": job_id,

        "service": (
            request.service
            or ada.service
        ),

        "customer_id":
            request.customer_id,

        "client_request_id":
            request.client_request_id,

        "created_work": False,
    }


# ============================================================
# DOCUMENT GENERATION
# ============================================================

async def generate_document_for_job(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    if job.get(
        "generation_started"
    ):
        return

    job["generation_started"] = True
    job["generation_finished"] = False
    job["status"] = "generating"

    job["progress"] = {
        "completed": 0,
        "total": 1,
    }

    for section in job["sections"]:

        section["status"] = "generating"

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

        print()
        print("=" * 70)
        print("DOCUMENT GENERATION STARTED")
        print("=" * 70)
        print("Job:", job_id)
        print("Service:", job.get("service"))
        print()
        print("REQUEST PASSED TO ADARESPONSE:")
        print(
            job.get(
                "original_request",
                "",
            )
        )
        print("=" * 70)

        # ----------------------------------------------------
        # AdaResponse is called EXACTLY ONCE here.
        # ----------------------------------------------------

        document_html = (
            await asyncio.to_thread(
                ada.generate_complete_document,
                original_request=job[
                    "original_request"
                ],
                service=job.get(
                    "service"
                ),
                context=job.get(
                    "context"
                ),
                correction=False,
                existing_work=None,
                event="document_generation",
            )
        )

        document_html = str(
            document_html or ""
        ).strip()

        if not document_html:

            raise RuntimeError(
                "AdaResponse generated an empty document."
            )

        # ----------------------------------------------------
        # READY
        # ----------------------------------------------------

        job["document_html"] = (
            document_html
        )

        job["progress"] = {
            "completed": 1,
            "total": 1,
        }

        job["sections"][0][
            "status"
        ] = "done"

        job["sections"][0][
            "title"
        ] = (
            job.get("service")
            or "Completed Service"
        )

        job["status"] = "complete"

        job["generation_finished"] = True

        print()
        print("=" * 70)
        print("DOCUMENT GENERATION COMPLETE")
        print("=" * 70)
        print("Job:", job_id)
        print(
            "Characters:",
            len(document_html),
        )
        print("=" * 70)
        print()

    except Exception as error:

        job["status"] = "error"

        job["error"] = {
            "type":
                type(error).__name__,

            "message":
                str(error),
        }

        job["generation_finished"] = True

        job["progress"] = {
            "completed": 0,
            "total": 1,
        }

        job["sections"][0][
            "status"
        ] = "error"

        print()
        print("=" * 70)
        print("DOCUMENT GENERATION FAILED")
        print("=" * 70)

        traceback.print_exc()

        print("=" * 70)
        print()


def ensure_generation_started(
    job_id: str,
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    if job.get(
        "generation_finished"
    ):
        return

    existing_task = (
        _generation_tasks.get(
            job_id
        )
    )

    if (
        existing_task is not None
        and not existing_task.done()
    ):
        return

    _generation_tasks[
        job_id
    ] = asyncio.create_task(
        generate_document_for_job(
            job_id
        )
    )


# ============================================================
# REVIEW
# ============================================================

@app.get("/api/review")
async def review(
    job_id: str,
):

    job_id = str(
        job_id or ""
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
                "The requested document job "
                "does not exist in this application session."
            ),
            status_code=404,
            error_code="JOB_NOT_FOUND",
        )

    ensure_generation_started(
        job_id
    )

    return {
        "success": True,

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

        "sections":
            job["sections"],

        "document_html":
            job["document_html"],

        "approved":
            job["approved"],

        "paid":
            job["paid"],

        "error":
            job["error"],
    }


# ============================================================
# CORRECTION
# ============================================================

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
    ).strip()

    if not instruction:

        return error_response(
            stage="CORRECTION",
            error="Correction instruction is empty.",
            status_code=400,
            error_code="EMPTY_CORRECTION",
        )

    if job["status"] == "generating":

        return error_response(
            stage="CORRECTION",
            error=(
                "The document is still being prepared. "
                "Please wait until it is ready for review."
            ),
            status_code=409,
            error_code="DOCUMENT_STILL_GENERATING",
        )

    existing_document = str(
        job.get(
            "document_html",
            ""
        )
    ).strip()

    if not existing_document:

        return error_response(
            stage="CORRECTION",
            error=(
                "There is no completed document "
                "available for correction."
            ),
            status_code=409,
            error_code="NO_DOCUMENT_TO_CORRECT",
        )

    job["status"] = "generating"

    job["approved"] = False

    job["current_version"] += 1

    job["version_id"] = (
        f"{request.job_id}:"
        f"{job['current_version']}"
    )

    job["progress"] = {
        "completed": 0,
        "total": 1,
    }

    job["sections"][0][
        "status"
    ] = "generating"

    job["generation_started"] = False

    job["generation_finished"] = False

    job["error"] = None

    job["original_request"] = (
        job["original_request"]
        + "\n\nCUSTOMER CORRECTION:\n"
        + instruction
    )

    # --------------------------------------------------------
    # Remove completed task reference.
    # --------------------------------------------------------

    old_task = _generation_tasks.pop(
        request.job_id,
        None,
    )

    if (
        old_task is not None
        and not old_task.done()
    ):
        old_task.cancel()

    # --------------------------------------------------------
    # Correction generation.
    # --------------------------------------------------------

    _generation_tasks[
        request.job_id
    ] = asyncio.create_task(
        generate_corrected_document(
            request.job_id,
            existing_document,
        )
    )

    return {
        "success": True,
        "job_id": request.job_id,
        "status": "generating",
        "version_id": job["version_id"],
    }


async def generate_corrected_document(
    job_id: str,
    existing_work: str,
):

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    job["generation_started"] = True

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

        document_html = (
            await asyncio.to_thread(
                ada.generate_complete_document,
                original_request=job[
                    "original_request"
                ],
                service=job.get(
                    "service"
                ),
                context=job.get(
                    "context"
                ),
                correction=True,
                existing_work=existing_work,
                event="document_correction",
            )
        )

        document_html = str(
            document_html or ""
        ).strip()

        if not document_html:

            raise RuntimeError(
                "AdaResponse returned an empty corrected document."
            )

        job["document_html"] = (
            document_html
        )

        job["progress"] = {
            "completed": 1,
            "total": 1,
        }

        job["sections"][0][
            "status"
        ] = "done"

        job["status"] = "complete"

        job["generation_finished"] = True

    except Exception as error:

        job["status"] = "error"

        job["error"] = {
            "type":
                type(error).__name__,
            "message":
                str(error),
        }

        job["generation_finished"] = True

        job["sections"][0][
            "status"
        ] = "error"

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

    if request.version_id != job[
        "version_id"
    ]:

        return error_response(
            stage="APPROVAL",
            error=(
                "The supplied document version "
                "does not match the current version."
            ),
            status_code=409,
            error_code="VERSION_MISMATCH",
        )

    if job["status"] != "complete":

        return error_response(
            stage="APPROVAL",
            error=(
                "The document is not ready "
                "for approval."
            ),
            status_code=409,
            error_code="DOCUMENT_NOT_READY",
        )

    if not job.get(
        "document_html"
    ):

        return error_response(
            stage="APPROVAL",
            error=(
                "There is no completed document "
                "to approve."
            ),
            status_code=409,
            error_code="EMPTY_DOCUMENT",
        )

    job["approved"] = True

    return {
        "success": True,
        "job_id":
            request.job_id,
        "version_id":
            request.version_id,
        "approved": True,
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

    return error_response(
        stage="DOWNLOAD",
        error=(
            "Payment and final download workflow "
            "has not been connected yet."
        ),
        status_code=409,
        error_code="DOWNLOAD_NOT_CONNECTED",
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
            "success": True,
            "message":
                "Conversation cleared.",
        }

    except Exception as error:

        return error_response(
            stage="CLEAR_CHAT",
            error=error,
            status_code=500,
            error_code="CLEAR_CHAT_ERROR",
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print()
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER")
    print("FASTAPI + AdaResponse + REVIEW")
    print("=" * 70)

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

    try:

        configured = is_configured()

    except Exception:

        configured = False

    print(
        "Configured:",
        configured,
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
        "Payment:",
        "NOT CONNECTED YET",
    )

    print(
        "Download:",
        "NOT CONNECTED YET",
    )

    print(
        "Flask:",
        "DISABLED",
    )

    print(
        "AdaController:",
        "DISABLED",
    )

    print(
        "AdaAIEngine:",
        "DISABLED",
    )

    print("=" * 70)
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
