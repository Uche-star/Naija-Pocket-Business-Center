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
    #
    # NEVER expose an internal Ada availability message
    # to the customer.
    #
    # The customer-facing response follows the agreed
    # saved-work / temporary-network procedure.
    #
    # The actual internal error remains available in the
    # "error" field for server-side troubleshooting.
    # ------------------------------------------------------

    if controller is None:

        print()
        print("=" * 60)
        print("ADA CONTROLLER UNAVAILABLE")
        print("=" * 60)

        print(
            "Customer request cannot currently reach "
            "the Ada Controller."
        )

        print("=" * 60)
        print()

        return {
            "success": False,
            "reply": (
                "Your work has been saved safely. "
                "We are experiencing a temporary network connection issue. "
                "Your request has not been lost. "
                "Please stay on this page. "
                "When the connection returns, we will continue your work "
                "from where we stopped."
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
        # CUSTOMER-FACING RESPONSE
        #
        # Never expose Ada, Groq, FastAPI, controllers,
        # Python errors, APIs, or internal system details.
        #
        # The customer's work remains saved by the
        # workspace's localStorage procedure.
        # --------------------------------------------------

        return {
            "success": False,
            "reply": (
                "Your work has been saved safely. "
                "We are experiencing a temporary network connection issue. "
                "Your request has not been lost. "
                "Please stay on this page. "
                "When the connection returns, we will continue your work "
                "from where we stopped."
            ),
            "error": (
                f"{type(error).__name__}: "
                f"{str(error)}"
            )
        }
