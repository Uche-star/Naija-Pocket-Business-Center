"""
ada_api.py

Ada Intelligence API
Naija Pocket Business Center

IMPORTANT:
AdaController is created once when the FastAPI
application starts.

This keeps AdaConversationMemory alive between
successive /api/chat requests during the same
running application instance.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
import traceback

from ada_controller import AdaController


# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="Ada Intelligence API - Naija Pocket",
    version="0.1.0"
)


# ==========================================================
# CORS
# ==========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================
# BASE DIRECTORY
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent


# ==========================================================
# CHAT REQUEST
# ==========================================================

class ChatRequest(BaseModel):

    message: str
    service: str | None = None


# ==========================================================
# CREATE ADA CONTROLLER ONCE
# ==========================================================
#
# DO NOT move this into /api/chat.
#
# If AdaController is created inside /api/chat,
# every customer message creates:
#
#     new AdaController
#         ↓
#     new AdaAIEngine
#         ↓
#     new AdaConversationMemory
#
# That causes Ada to forget previous messages.
#
# Creating it here allows the same memory to be reused.
# ==========================================================

print()
print("=" * 60)
print("STARTING ADA INTELLIGENCE")
print("=" * 60)

controller = None

try:

    controller = AdaController()

    print(
        "Ada Controller Created:",
        True
    )

    print(
        "Groq Connected:",
        controller.intelligence.is_connected()
    )

except Exception as error:

    print()
    print("=" * 60)
    print("ADA STARTUP ERROR")
    print("=" * 60)

    print(
        "ERROR TYPE:",
        type(error).__name__
    )

    print(
        "ERROR:",
        repr(error)
    )

    traceback.print_exc()

    print("=" * 60)
    print()

    controller = None


# ==========================================================
# HOME
# ==========================================================

@app.get("/")
def home():

    return FileResponse(
        BASE_DIR / "index.html"
    )


# ==========================================================
# CONVERSATION PAGE
# ==========================================================

@app.get("/conversation.html")
def conversation():

    return FileResponse(
        BASE_DIR / "conversation.html"
    )


# ==========================================================
# WORKSPACE PAGE
# ==========================================================

@app.get("/workspace.html")
def workspace():

    return FileResponse(
        BASE_DIR / "workspace.html"
    )


# ==========================================================
# HEALTH
# ==========================================================

@app.get("/health")
def health():

    groq_connected = False

    if controller is not None:

        try:

            groq_connected = (
                controller.intelligence.is_connected()
            )

        except Exception:

            groq_connected = False

    return {
        "status": "ok",
        "service": "Ada FastAPI",
        "ada_controller": controller is not None,
        "groq_connected": groq_connected
    }


# ==========================================================
# CHAT
# ==========================================================

@app.post("/api/chat")
def chat(req: ChatRequest):

    print()
    print("=" * 60)
    print("FASTAPI → ADA CONTROLLER")
    print("=" * 60)

    print(
        "Service:",
        req.service
    )

    print(
        "Message:",
        req.message
    )

    print(
        "Controller Exists:",
        controller is not None
    )

    print("=" * 60)
    print()

    # ------------------------------------------------------
    # CONTROLLER CHECK
    # ------------------------------------------------------

    if controller is None:

        return {
            "success": False,
            "reply": (
                "Ada is currently unavailable. "
                "Please try again shortly."
            ),
            "error": (
                "AdaController was not created "
                "during application startup."
            )
        }

    # ------------------------------------------------------
    # SEND MESSAGE TO ADA
    # ------------------------------------------------------

    try:

        reply = controller.process_message(
            message=req.message,
            service=req.service
        )

        print()
        print("=" * 60)
        print("ADA RESPONSE SUCCESS")
        print("=" * 60)

        print(
            "Reply:",
            str(reply)
        )

        print("=" * 60)
        print()

        return {
            "success": True,
            "reply": str(reply)
        }

    # ------------------------------------------------------
    # REAL ERROR
    # ------------------------------------------------------

    except Exception as error:

        print()
        print("=" * 60)
        print("FASTAPI → ADA REAL ERROR")
        print("=" * 60)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print("=" * 60)
        print()

        # --------------------------------------------------
        # DEBUG RESPONSE
        #
        # Do NOT hide the real exception.
        # The API response will contain the actual
        # error so we can see exactly what failed.
        # --------------------------------------------------

        return {
            "success": False,
            "reply": "Ada could not process the request.",
            "error": (
                f"{type(error).__name__}: "
                f"{str(error)}"
            )
        }
