"""
Naija Pocket Business Center
Current FastAPI Intelligence Gateway

CURRENT ARCHITECTURE

workspace.html
        ↓
POST /api/chat
        ↓
AdaResponse
        ↓
Groq
        ↓
customer response

This file belongs to the current FastAPI architecture.

IMPORTANT:
- No Flask
- No phone_bridge.py
- No AdaController
- No AdaAIEngine
- No keyword workflow
- No retired architecture
"""

from __future__ import annotations

import os
import traceback
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ada_response import (
    AdaResponse,
    get_ada_model,
    is_configured,
)


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
# ACTIVE ADA SESSIONS
# ============================================================

"""
Each customer/job receives its own AdaResponse instance.

This allows conversation history to remain attached to the
active request instead of creating a new intelligence object
for every message.
"""

_sessions: dict[str, AdaResponse] = {}


def session_key(
    customer_id: str | None,
    job_id: str | None,
) -> str:

    customer = (
        str(customer_id or "anonymous").strip()
    )

    job = (
        str(job_id or "default").strip()
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
            service=service,
        )

    elif service:

        _sessions[key].set_service(
            service
        )

    return _sessions[key]


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):

    message: str = Field(
        default="",
    )

    service: str | None = None

    event: str | None = None

    customer_id: str | None = None

    job_id: str | None = None

    client_request_id: str | None = None

    activate_intelligence: bool = True

    context: str | None = None


# ============================================================
# BASIC RESPONSE
# ============================================================

@app.get("/")
async def root():

    return {
        "success": True,
        "service": "Naija Pocket Business Center",
        "api": "FastAPI",
        "intelligence": "AdaResponse",
        "model": get_ada_model(),
        "configured": is_configured(),
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():

    return {
        "success": True,
        "status": "ok",
        "intelligence_configured": is_configured(),
        "model": get_ada_model(),
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    message = (
        str(request.message or "")
        .strip()
    )

    if not message:

        return {
            "success": False,
            "reply": "Please tell me what you would like help with.",
            "message": "Message is required.",
        }


    # --------------------------------------------------------
    # INTELLIGENCE CHECK
    # --------------------------------------------------------

    if not is_configured():

        print(
            "CHAT ERROR: AdaResponse is not configured."
        )

        return {
            "success": False,
            "reply": (
                "The business center service is "
                "temporarily unavailable. Please try again shortly."
            ),
            "error": "INTELLIGENCE_NOT_CONFIGURED",
        }


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

        print(
            "SESSION ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()

        return {
            "success": False,
            "reply": (
                "Sorry, I could not start your request "
                "right now. Please try again."
            ),
            "error": "SESSION_ERROR",
        }


    # --------------------------------------------------------
    # APPLICATION CONTEXT
    # --------------------------------------------------------

    application_context = (
        request.context
        or ""
    ).strip()


    if request.customer_id:

        application_context += (
            "\n\nCUSTOMER REQUEST IDENTIFIER\n"
            f"{request.customer_id}"
        )


    if request.job_id:

        application_context += (
            "\n\nACTIVE JOB IDENTIFIER\n"
            f"{request.job_id}"
        )


    if request.client_request_id:

        application_context += (
            "\n\nCLIENT REQUEST IDENTIFIER\n"
            f"{request.client_request_id}"
        )


    if request.service:

        application_context += (
            "\n\nCURRENT SELECTED SERVICE\n"
            f"{request.service}"
        )


    # --------------------------------------------------------
    # CURRENT EVENT
    # --------------------------------------------------------

    event = (
        request.event
        or ""
    ).strip()


    # --------------------------------------------------------
    # CALL CURRENT INTELLIGENCE
    # --------------------------------------------------------

    try:

        reply = ada.respond(
            message=message,
            service=request.service,
            event=event or None,
            context=application_context or None,
        )

    except Exception as error:

        print(
            "ADA INTELLIGENCE ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()

        return {
            "success": False,
            "reply": (
                "Sorry, I could not process your "
                "request right now. Please try again."
            ),
            "error": "INTELLIGENCE_ERROR",
        }


    # --------------------------------------------------------
    # VALIDATE RESPONSE
    # --------------------------------------------------------

    reply = str(
        reply or ""
    ).strip()


    if not reply:

        return {
            "success": False,
            "reply": (
                "I am ready to help. "
                "Please tell me what you would like to do next."
            ),
            "error": "EMPTY_INTELLIGENCE_RESPONSE",
        }


    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    return {
        "success": True,
        "reply": reply,
        "service": (
            request.service
            or ada.service
        ),
        "customer_id": request.customer_id,
        "job_id": request.job_id,
        "client_request_id": request.client_request_id,
        "intelligence": "AdaResponse",
    }


# ============================================================
# CLEAR CURRENT CHAT SESSION
# ============================================================

@app.post("/api/chat/clear")
async def clear_chat(
    customer_id: str | None = None,
    job_id: str | None = None,
):

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
        "message": "Conversation cleared.",
    }


# ============================================================
# CURRENT INTELLIGENCE STATUS
# ============================================================

@app.get("/api/status")
async def api_status():

    return {
        "success": True,
        "fastapi": True,
        "intelligence": "AdaResponse",
        "configured": is_configured(),
        "model": get_ada_model(),
        "active_sessions": len(
            _sessions
        ),
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload_placeholder():

    """
    The current intelligence architecture does not fabricate
    upload success.

    The actual document/upload implementation should remain
    in the application's existing upload system.

    This endpoint deliberately reports that an upload handler
    is not connected rather than pretending that a file was
    received.
    """

    return {
        "success": False,
        "message": (
            "The current upload handler is not connected "
            "to this FastAPI gateway yet."
        ),
    }


# ============================================================
# VOICE
# ============================================================

@app.post("/api/voice")
async def voice_placeholder():

    return {
        "success": False,
        "message": (
            "The current voice handler is not connected "
            "to this FastAPI gateway yet."
        ),
    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
async def approve_placeholder():

    return {
        "success": False,
        "message": (
            "Approval processing is not connected "
            "to this FastAPI gateway yet."
        ),
    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
async def payment_create_placeholder():

    return {
        "success": False,
        "message": (
            "Payment processing is not connected "
            "to this FastAPI gateway yet."
        ),
    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
async def payment_status_placeholder():

    return {
        "success": False,
        "message": (
            "Payment status processing is not connected "
            "to this FastAPI gateway yet."
        ),
    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
async def download_placeholder():

    return {
        "success": False,
        "message": (
            "Download delivery is not connected "
            "to this FastAPI gateway yet."
        ),
    }


# ============================================================
# CUSTOMER SERVICE
# ============================================================

@app.post("/api/customer-service")
async def customer_service(
    customer_id: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
):

    ada = get_session(
        customer_id=customer_id,
        job_id=job_id,
        service=service,
    )

    try:

        reply = ada.respond(
            message=(
                "The customer has requested "
                "Customer Service assistance. "
                "Please respond appropriately "
                "to their request."
            ),
            service=service,
            event="customer_service_requested",
        )

        return {
            "success": True,
            "reply": reply,
        }

    except Exception as error:

        print(
            "CUSTOMER SERVICE ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()

        return {
            "success": False,
            "reply": (
                "Customer Service is temporarily "
                "unavailable. Please try again."
            ),
        }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():

    print()
    print("=" * 70)
    print("NAIJA POCKET BUSINESS CENTER")
    print("CURRENT FASTAPI INTELLIGENCE GATEWAY")
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
        "Keyword workflow:",
        "DISABLED",
    )
    print(
        "Flask:",
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
# LOCAL RUN
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
