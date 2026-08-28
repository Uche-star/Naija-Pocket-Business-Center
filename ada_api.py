"""
Naija Pocket Business Center
CURRENT FASTAPI APPLICATION

LIVE ARCHITECTURE
-----------------

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

ACTIVE FILES
------------
ada_api.py
ada_response.py
review.html

NO FLASK
NO phone_bridge.py
NO AdaController
NO AdaAIEngine
NO RETIRED KEYWORD INTELLIGENCE

AdaResponse remains the intelligence engine.

FastAPI is the connection/state layer.

Review is the visual document/review layer.
"""

from __future__ import annotations

import asyncio
import html
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
    version="current",
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
# REVIEW JOB STATE
# ============================================================
#
# FastAPI owns application state.
#
# AdaResponse owns intelligence.
#
# Review reads this state.
#
# This is intentionally kept here rather than inside
# ada_response.py so the intelligence engine does not become
# a web/application-state manager.
# ============================================================

_jobs: dict[str, dict[str, Any]] = {}

_generation_tasks: dict[str, asyncio.Task] = {}


def get_job(job_id: str) -> dict[str, Any] | None:

    return _jobs.get(
        str(job_id)
    )


def create_job(
    *,
    job_id: str,
    customer_id: str | None,
    service: str | None,
    message: str,
    context: str | None,
    client_request_id: str | None,
) -> dict[str, Any]:

    job = {
        "job_id": job_id,

        "customer_id": customer_id,

        "service": service,

        "original_request": message,

        "context": context,

        "client_request_id":
            client_request_id,

        "status": "generating",

        "current_version": 1,

        "version_id": f"{job_id}:1",

        "approved": False,

        "paid": False,

        "progress": {
            "completed": 0,
            "total": 1,
        },

        "sections": [
            {
                "section_id": f"{job_id}:section:1",
                "section_order": 1,
                "title": (
                    service
                    or "Your Requested Service"
                ),
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
# ERROR FORMAT
# ============================================================

def error_response(
    *,
    stage: str,
    error: Exception | str,
    status_code: int = 500,
    error_code: str | None = None,
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
        "error": (
            error_code
            or "APPLICATION_ERROR"
        ),
        "error_type": error_type,
        "error_message": error_message,
    }

    if DEBUG_ERRORS:

        content["debug"] = (
            "Real exception exposed because "
            "ADA_DEBUG_ERRORS is enabled."
        )

    else:

        content["error_message"] = (
            "An internal application error occurred."
        )

    return JSONResponse(
        status_code=status_code,
        content=content,
    )


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


class CorrectionRequest(BaseModel):

    job_id: str

    instruction: str


class ApprovalRequest(BaseModel):

    job_id: str

    version_id: str


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
            error=(
                "index.html was not found."
            ),
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
            error=(
                "review.html was not found."
            ),
            status_code=404,
            error_code=(
                "REVIEW_HTML_NOT_FOUND"
            ),
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

    message = (
        str(
            request.message or ""
        ).strip()
    )

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
        or str(
            uuid.uuid4()
        )
    )

    # --------------------------------------------------------
    # CONTEXT
    # --------------------------------------------------------

    context_parts: list[str] = []

    if request.context:

        value = (
            str(
                request.context
            ).strip()
        )

        if value:
            context_parts.append(
                value
            )

    if request.customer_id:

        context_parts.append(
            "CUSTOMER ID\n"
            + str(
                request.customer_id
            )
        )

    if request.job_id:

        context_parts.append(
            "ACTIVE JOB ID\n"
            + str(
                request.job_id
            )
        )

    if request.client_request_id:

        context_parts.append(
            "CLIENT REQUEST ID\n"
            + str(
                request.client_request_id
            )
        )

    if request.service:

        context_parts.append(
            "CURRENT SELECTED SERVICE\n"
            + str(
                request.service
            )
        )

    application_context = (
        "\n\n".join(
            context_parts
        )
        or None
    )

    # --------------------------------------------------------
    # ADA SESSION
    # --------------------------------------------------------

    try:

        ada = get_session(
            customer_id=request.customer_id,
            job_id=job_id,
            service=request.service,
        )

    except Exception as error:

        return error_response(
            stage="SESSION_CREATION",
            error=error,
            status_code=500,
            error_code="SESSION_ERROR",
        )

    # --------------------------------------------------------
    # NORMAL ADA CONVERSATION
    # --------------------------------------------------------

    try:

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

    # --------------------------------------------------------
    # CREATE / UPDATE REVIEW JOB
    # --------------------------------------------------------

    existing_job = _jobs.get(
        job_id
    )

    if existing_job is None:

        create_job(
            job_id=job_id,
            customer_id=request.customer_id,
            service=request.service,
            message=message,
            context=application_context,
            client_request_id=(
                request.client_request_id
            ),
        )

    else:

        existing_job["original_request"] = message

        if request.service:

            existing_job["service"] = (
                request.service
            )

        if application_context:

            existing_job["context"] = (
                application_context
            )

    print(
        "AdaResponse returned successfully."
    )

    print(
        "Job:",
        job_id,
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

        "review_url":
            "/review.html?job_id="
            + job_id,
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
    job["status"] = "generating"

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
        print("=" * 70)

        # ----------------------------------------------------
        # ADAResponse remains responsible for the actual
        # document intelligence.
        #
        # FastAPI simply passes the complete request/context
        # through.
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
        # DOCUMENT READY
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
        print(
            "DOCUMENT GENERATION COMPLETE"
        )
        print(
            "Job:",
            job_id,
        )
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

        print()
        print(
            "DOCUMENT GENERATION FAILED"
        )

        traceback.print_exc()


def ensure_generation_started(
    job_id: str,
):

    if job_id in _generation_tasks:

        task = _generation_tasks[
            job_id
        ]

        if not task.done():
            return

    job = _jobs.get(
        job_id
    )

    if job is None:
        return

    if job.get(
        "generation_finished"
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

    job_id = (
        str(job_id or "").strip()
    )

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

    instruction = (
        str(
            request.instruction
        ).strip()
    )

    if not instruction:

        return error_response(
            stage="CORRECTION",
            error="Correction instruction is empty.",
            status_code=400,
            error_code="EMPTY_CORRECTION",
        )

    job["status"] = "generating"

    job["approved"] = False

    job["progress"] = {
        "completed": 0,
        "total": 1,
    }

    job["sections"][0][
        "status"
    ] = "generating"

    job["generation_finished"] = False

    job["original_request"] = (
        job["original_request"]
        + "\n\nCUSTOMER CORRECTION:\n"
        + instruction
    )

    # Remove old completed task reference.
    _generation_tasks.pop(
        request.job_id,
        None,
    )

    # Start a new generation.
    ensure_generation_started(
        request.job_id
    )

    return {
        "success": True,
        "job_id": request.job_id,
        "status": "generating",
    }


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

    job["approved"] = True

    return {
        "success": True,
        "job_id": request.job_id,
        "version_id": request.version_id,
        "approved": True,
    }


# ============================================================
# DOWNLOAD PLACEHOLDER
# ============================================================
#
# Payment/download is deliberately NOT being developed yet.
#
# The review connection is being established first.
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
