"""
Naija Pocket Business Center
Current FastAPI Application

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

NO FLASK
NO phone_bridge.py
NO AdaController
NO AdaAIEngine
NO RETIRED INTELLIGENCE CHAIN
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
        str(customer_id or "anonymous")
        .strip()
    )

    job = (
        str(job_id or "default")
        .strip()
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

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "index.html not found",
                "base_directory": str(
                    BASE_DIR
                ),
            },
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

        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": (
                    "conversation.html not found"
                ),
            },
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

        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "error": (
                    "workspace.html not found"
                ),
            },
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
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    message = (
        str(
            request.message or ""
        )
        .strip()
    )

    if not message:

        return {
            "success": False,
            "reply": (
                "Please tell me what "
                "you would like help with."
            ),
        }


    if not is_configured():

        print(
            "CHAT ERROR: "
            "AdaResponse is not configured."
        )

        return {
            "success": False,
            "reply": (
                "The business center service "
                "is temporarily unavailable. "
                "Please try again shortly."
            ),
            "error": (
                "INTELLIGENCE_NOT_CONFIGURED"
            ),
        }


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
                "Sorry, I could not start "
                "your request right now. "
                "Please try again."
            ),
            "error": "SESSION_ERROR",
        }


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
        )
        .strip()
        or None
    )


    # --------------------------------------------------------
    # CURRENT INTELLIGENCE
    # --------------------------------------------------------

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

        print(
            "ADA INTELLIGENCE ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()

        return {
            "success": False,
            "reply": (
                "Sorry, I could not process "
                "your request right now. "
                "Please try again."
            ),
            "error": "INTELLIGENCE_ERROR",
        }


    reply = str(
        reply or ""
    ).strip()


    if not reply:

        return {
            "success": False,
            "reply": (
                "I am ready to help. "
                "Please tell me what you "
                "would like to do next."
            ),
            "error": (
                "EMPTY_INTELLIGENCE_RESPONSE"
            ),
        }


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

        print(
            "CUSTOMER SERVICE ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()

        return {
            "success": False,
            "reply": (
                "Customer Service is "
                "temporarily unavailable. "
                "Please try again."
            ),
        }


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
            "8000"
        )
    )

    uvicorn.run(
        "ada_api:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
