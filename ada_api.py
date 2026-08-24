"""
Naija Pocket Business Center
CURRENT FastAPI APPLICATION

CUSTOMER WEBSITE
    /
    /conversation.html
    /workspace.html

CURRENT INTELLIGENCE
    /api/chat
        ↓
    AdaResponse
        ↓
    Groq

CURRENT ARCHITECTURE ONLY
NO FLASK
NO phone_bridge.py
NO AdaController
NO AdaAIEngine
NO RETIRED INTELLIGENCE CHAIN

DEBUG MODE
------------
Real application errors are returned by the API so that
the actual failure can be seen during troubleshooting.

Sensitive configuration values such as API keys are NEVER
returned.
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path

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


def find_file(
    filename: str,
) -> Path | None:

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


# ============================================================
# CORS
# ============================================================

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
        str(
            customer_id or "anonymous"
        ).strip()
    )

    job = (
        str(
            job_id or "default"
        ).strip()
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
        "error": error_code or "APPLICATION_ERROR",
        "error_type": error_type,
        "error_message": error_message,
    }

    if DEBUG_ERRORS:

        content[
            "debug"
        ] = (
            "Real exception exposed because "
            "ADA_DEBUG_ERRORS is enabled."
        )

    else:

        content[
            "error_message"
        ] = (
            "An internal application error occurred."
        )

    return JSONResponse(
        status_code=status_code,
        content=content,
    )


# ============================================================
# CHAT REQUEST
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


# ============================================================
# CUSTOMER WEBSITE
# ============================================================

@app.get("/")
async def customer_home():

    index_file = find_file(
        "index.html"
    )

    if index_file is None:

        return error_response(
            stage="CUSTOMER_HOME",
            error=(
                "index.html was not found. "
                f"BASE_DIR={BASE_DIR}"
            ),
            status_code=500,
            error_code="INDEX_HTML_NOT_FOUND",
        )

    return FileResponse(
        index_file,
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
                "conversation.html was not found. "
                f"BASE_DIR={BASE_DIR}"
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
                "workspace.html was not found. "
                f"BASE_DIR={BASE_DIR}"
            ),
            status_code=404,
            error_code="WORKSPACE_HTML_NOT_FOUND",
        )

    return FileResponse(
        file,
        media_type="text/html",
    )


# ============================================================
# API STATUS
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
        "debug_errors": DEBUG_ERRORS,
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
        "debug_errors": DEBUG_ERRORS,
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

    print(
        "Service:",
        request.service,
    )

    print(
        "Customer:",
        request.customer_id,
    )

    print(
        "Job:",
        request.job_id,
    )

    print(
        "Event:",
        request.event,
    )

    print(
        "Message:",
        request.message,
    )

    print(
        "Activate intelligence:",
        request.activate_intelligence,
    )

    print("-" * 70)


    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    message = (
        str(
            request.message or ""
        ).strip()
    )


    if not message:

        return error_response(
            stage="CHAT_VALIDATION",
            error=(
                "The chat message is empty."
            ),
            status_code=400,
            error_code="EMPTY_MESSAGE",
        )


    # --------------------------------------------------------
    # INTELLIGENCE CONFIGURATION
    # --------------------------------------------------------

    try:

        configured = (
            is_configured()
        )

    except Exception as error:

        return error_response(
            stage="INTELLIGENCE_CONFIGURATION_CHECK",
            error=error,
            status_code=500,
            error_code=(
                "CONFIGURATION_CHECK_ERROR"
            ),
        )


    if not configured:

        return error_response(
            stage="INTELLIGENCE_CONFIGURATION",
            error=(
                "AdaResponse is not configured. "
                "Check GROQ_API_KEY and the Groq "
                "client configuration."
            ),
            status_code=503,
            error_code=(
                "INTELLIGENCE_NOT_CONFIGURED"
            ),
        )


    # --------------------------------------------------------
    # SESSION
    # --------------------------------------------------------

    try:

        ada = get_session(
            customer_id=request.customer_id,
            job_id=request.job_id,
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
    # APPLICATION CONTEXT
    # --------------------------------------------------------

    context_parts: list[str] = []


    if request.context:

        context_parts.append(
            str(
                request.context
            ).strip()
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
            part
            for part in context_parts
            if part
        )
    )


    # --------------------------------------------------------
    # CURRENT EVENT
    # --------------------------------------------------------

    event = (
        str(
            request.event or ""
        ).strip()
        or None
    )


    # --------------------------------------------------------
    # INTELLIGENCE CALL
    # --------------------------------------------------------

    print()
    print(
        "CALLING AdaResponse..."
    )

    print(
        "Model:",
        get_ada_model(),
    )

    print(
        "Service:",
        request.service,
    )

    print(
        "Event:",
        event,
    )

    print()


    try:

        reply = ada.respond(
            message=message,
            service=request.service,
            event=event,
            context=(
                application_context
                or None
            ),
        )

    except Exception as error:

        return error_response(
            stage="ADA_INTELLIGENCE",
            error=error,
            status_code=500,
            error_code=(
                "INTELLIGENCE_ERROR"
            ),
        )


    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    reply = str(
        reply or ""
    ).strip()


    if not reply:

        return error_response(
            stage="ADA_INTELLIGENCE_RESPONSE",
            error=(
                "AdaResponse returned an empty response."
            ),
            status_code=500,
            error_code=(
                "EMPTY_INTELLIGENCE_RESPONSE"
            ),
        )


    print()
    print(
        "AdaResponse returned successfully."
    )

    print(
        "Reply:",
        reply,
    )

    print()


    return {
        "success": True,
        "reply": reply,
        "service": (
            request.service
            or ada.service
        ),
        "customer_id": (
            request.customer_id
        ),
        "job_id": (
            request.job_id
        ),
        "client_request_id": (
            request.client_request_id
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
            "message": (
                "Conversation cleared."
            ),
        }

    except Exception as error:

        return error_response(
            stage="CLEAR_CHAT",
            error=error,
            status_code=500,
            error_code="CLEAR_CHAT_ERROR",
        )


# ============================================================
# CUSTOMER SERVICE
# ============================================================

@app.post("/api/customer-service")
async def customer_service(
    customer_id: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
):

    try:

        ada = get_session(
            customer_id=customer_id,
            job_id=job_id,
            service=service,
        )

        reply = ada.respond(
            message=(
                "The customer is requesting "
                "Customer Service assistance. "
                "Respond appropriately."
            ),
            service=service,
            event=(
                "customer_service_requested"
            ),
        )

        return {
            "success": True,
            "reply": reply,
        }

    except Exception as error:

        return error_response(
            stage="CUSTOMER_SERVICE",
            error=error,
            status_code=500,
            error_code=(
                "CUSTOMER_SERVICE_ERROR"
            ),
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print()
    print("=" * 70)
    print(
        "NAIJA POCKET BUSINESS CENTER"
    )
    print(
        "CURRENT FASTAPI APPLICATION"
    )
    print("=" * 70)

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
        "Debug errors:",
        DEBUG_ERRORS,
    )

    print(
        "Website:",
        "FastAPI FileResponse",
    )

    print(
        "Keyword workflow:",
        "DISABLED",
    )

    print(
        "Flask:",
        "NOT USED",
    )

    print(
        "phone_bridge.py:",
        "NOT USED",
    )

    print(
        "AdaController:",
        "NOT USED",
    )

    print(
        "AdaAIEngine:",
        "NOT USED",
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
