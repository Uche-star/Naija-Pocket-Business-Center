"""
ada_api.py

Ada Intelligence API
Naija Pocket Business Center

FLOW:

    Conversation Page
          ↓
    Cloudflare Worker
          ↓
    FastAPI
          ↓
    AdaController
          ↓
    AdaAIEngine V10
          ↓
    Groq
          ↓
    Ada response

IMPORTANT:

1. AdaController is created ONCE when FastAPI starts.
2. AdaConversationMemory therefore remains alive between
   successive /api/chat requests while this application
   instance remains running.
3. Every customer message goes through the SAME controller.
4. The selected service is passed to AdaController on every
   request.
5. Real backend errors are printed with a full traceback.
6. The API never silently converts a real Ada error into a
   successful response.
7. Empty Ada responses are treated as errors.
"""


from pathlib import Path
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ada_controller import AdaController


# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="Ada Intelligence API - Naija Pocket Business Center",
    version="1.0.0"
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
# THIS IS VERY IMPORTANT.
#
# DO NOT create AdaController inside /api/chat.
#
# If we did this:
#
#     every message
#          ↓
#     new controller
#          ↓
#     new AdaAIEngine
#          ↓
#     new conversation memory
#
# Ada would forget the previous question.
#
# We therefore create ONE controller here.
# ==========================================================

print()
print("=" * 70)
print("STARTING ADA INTELLIGENCE API")
print("=" * 70)

controller = None

try:

    controller = AdaController()

    print()
    print("ADA CONTROLLER CREATED: True")

    try:

        print(
            "GROQ CONNECTED:",
            controller.intelligence.is_connected()
        )

    except Exception as error:

        print(
            "GROQ STATUS ERROR:",
            repr(error)
        )

    try:

        print(
            "GROQ MODEL:",
            controller.intelligence.get_model()
        )

    except Exception as error:

        print(
            "GROQ MODEL ERROR:",
            repr(error)
        )

except Exception as error:

    print()
    print("=" * 70)
    print("!!!!!!!! ADA STARTUP ERROR !!!!!!!!")
    print("=" * 70)

    print(
        "ERROR TYPE:",
        type(error).__name__
    )

    print(
        "ERROR:",
        repr(error)
    )

    print()
    print("FULL TRACEBACK:")
    traceback.print_exc()

    print("=" * 70)
    print()

    controller = None


print("=" * 70)
print("ADA INTELLIGENCE API READY")
print("=" * 70)
print()


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

    controller_exists = (
        controller is not None
    )

    groq_connected = False

    active_service = None

    if controller_exists:

        try:

            groq_connected = (
                controller
                .intelligence
                .is_connected()
            )

        except Exception as error:

            print(
                "Health Groq Check Error:",
                repr(error)
            )

        try:

            active_service = (
                controller
                .get_active_service()
            )

        except Exception as error:

            print(
                "Health Service Check Error:",
                repr(error)
            )

    return {
        "status": "ok",
        "service": "Ada FastAPI",
        "ada_controller": controller_exists,
        "groq_connected": groq_connected,
        "active_service": active_service
    }


# ==========================================================
# ADA STATUS
# ==========================================================

@app.get("/api/status")
def ada_status():

    if controller is None:

        return {
            "success": False,
            "controller": False,
            "groq_connected": False,
            "active_service": None,
            "job_state": None
        }

    try:

        intelligence = controller.intelligence

        return {
            "success": True,
            "controller": True,
            "groq_connected": (
                intelligence.is_connected()
            ),
            "active_service": (
                controller.get_active_service()
            ),
            "job_state": (
                controller.get_job_state()
            )
        }

    except Exception as error:

        print()
        print("=" * 70)
        print("ADA STATUS ERROR")
        print("=" * 70)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print("=" * 70)
        print()

        return {
            "success": False,
            "controller": True,
            "groq_connected": False,
            "active_service": None,
            "job_state": None,
            "error": (
                f"{type(error).__name__}: "
                f"{str(error)}"
            )
        }


# ==========================================================
# CHAT
# ==========================================================

@app.post("/api/chat")
def chat(req: ChatRequest):

    print()
    print("=" * 70)
    print("FASTAPI → ADA CONTROLLER")
    print("=" * 70)

    print(
        "REQUEST SERVICE:",
        repr(req.service)
    )

    print(
        "REQUEST MESSAGE:",
        repr(req.message)
    )

    print(
        "CONTROLLER EXISTS:",
        controller is not None
    )

    print("=" * 70)
    print()

    # ======================================================
    # VALIDATE MESSAGE
    # ======================================================

    if req.message is None:

        raise ValueError(
            "FastAPI received message=None"
        )

    message = str(
        req.message
    ).strip()

    if not message:

        raise ValueError(
            "FastAPI received an empty message"
        )

    # ======================================================
    # NORMALIZE SERVICE
    # ======================================================

    selected_service = None

    if req.service is not None:

        service_text = str(
            req.service
        ).strip()

        if service_text:

            if (
                service_text.lower()
                != "service not selected"
            ):

                selected_service = service_text

    print(
        "NORMALIZED SERVICE:",
        repr(selected_service)
    )

    print(
        "NORMALIZED MESSAGE:",
        repr(message)
    )

    # ======================================================
    # CONTROLLER CHECK
    # ======================================================

    if controller is None:

        print()
        print("=" * 70)
        print("ADA CONTROLLER IS NOT AVAILABLE")
        print("=" * 70)
        print(
            "The controller failed during FastAPI startup."
        )
        print("=" * 70)
        print()

        return {
            "success": False,
            "reply": (
                "Your request could not be connected "
                "to the Business Center right now. "
                "Please try again shortly."
            ),
            "error": (
                "AdaController was not created "
                "during FastAPI startup."
            )
        }

    # ======================================================
    # CHECK GROQ CONNECTION
    # ======================================================

    try:

        groq_connected = (
            controller
            .intelligence
            .is_connected()
        )

    except Exception as error:

        print()
        print("=" * 70)
        print("GROQ CONNECTION STATUS CHECK FAILED")
        print("=" * 70)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print("=" * 70)
        print()

        raise

    if not groq_connected:

        print()
        print("=" * 70)
        print("GROQ IS NOT CONNECTED")
        print("=" * 70)
        print("=" * 70)
        print()

        return {
            "success": False,
            "reply": (
                "Your request could not be processed "
                "right now. Please try again shortly."
            ),
            "error": (
                "AdaAIEngine reports that Groq "
                "is not connected."
            )
        }

    # ======================================================
    # SEND MESSAGE TO ADA CONTROLLER
    # ======================================================

    try:

        print()
        print("=" * 70)
        print("SENDING MESSAGE TO ADA CONTROLLER")
        print("=" * 70)

        print(
            "SERVICE:",
            repr(selected_service)
        )

        print(
            "MESSAGE:",
            repr(message)
        )

        print("=" * 70)
        print()

        response = controller.process_message(
            message=message,
            service=selected_service
        )

    # ======================================================
    # REAL ADA ERROR
    # ======================================================

    except Exception as error:

        print()
        print("=" * 70)
        print("!!!!!!!! FASTAPI → ADA REAL ERROR !!!!!!!!")
        print("=" * 70)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        print()
        print("ERROR REPRESENTATION:")
        print(
            repr(error)
        )

        print()
        print("FULL TRACEBACK:")
        print()

        traceback.print_exc()

        print()
        print("=" * 70)
        print("!!!!!!!! END REAL ADA ERROR !!!!!!!!")
        print("=" * 70)
        print()

        # --------------------------------------------------
        # IMPORTANT
        #
        # Do NOT pretend this was a successful Ada response.
        # The frontend receives success=False.
        #
        # The REAL exception is printed above.
        # --------------------------------------------------

        return {
            "success": False,
            "reply": (
                "I could not complete that request "
                "right now. Please try again shortly."
            ),
            "error": (
                f"{type(error).__name__}: "
                f"{str(error)}"
            )
        }

    # ======================================================
    # CHECK ADA RESPONSE
    # ======================================================

    print()
    print("=" * 70)
    print("ADA CONTROLLER RETURNED")
    print("=" * 70)

    print(
        "RESPONSE TYPE:",
        type(response).__name__
    )

    print(
        "RESPONSE:",
        repr(response)
    )

    print("=" * 70)
    print()

    if response is None:

        error_message = (
            "AdaController.process_message() "
            "returned None."
        )

        print()
        print("=" * 70)
        print("EMPTY ADA RESPONSE")
        print("=" * 70)
        print(error_message)
        print("=" * 70)
        print()

        return {
            "success": False,
            "reply": (
                "I could not complete that request "
                "right now. Please try again shortly."
            ),
            "error": error_message
        }

    reply = str(
        response
    ).strip()

    if not reply:

        error_message = (
            "AdaController.process_message() "
            "returned an empty response."
        )

        print()
        print("=" * 70)
        print("EMPTY ADA RESPONSE")
        print("=" * 70)
        print(error_message)
        print("=" * 70)
        print()

        return {
            "success": False,
            "reply": (
                "I could not complete that request "
                "right now. Please try again shortly."
            ),
            "error": error_message
        }

    # ======================================================
    # SUCCESS
    # ======================================================

    try:

        current_job_state = (
            controller.get_job_state()
        )

    except Exception:

        current_job_state = None

    print()
    print("=" * 70)
    print("ADA RESPONSE SUCCESS")
    print("=" * 70)

    print(
        "REPLY:",
        reply
    )

    print(
        "JOB STATE:",
        current_job_state
    )

    print("=" * 70)
    print()

    return {
        "success": True,
        "reply": reply,
        "service": (
            controller.get_active_service()
        ),
        "job_state": current_job_state
    }


# ==========================================================
# RESET CURRENT ADA JOB
# ==========================================================
#
# This endpoint is intentionally separate from /api/chat.
#
# It allows the frontend to explicitly begin a completely
# new customer job rather than accidentally carrying the
# previous conversation into a new request.
# ==========================================================

@app.post("/api/reset")
def reset_ada_job():

    print()
    print("=" * 70)
    print("RESET ADA JOB")
    print("=" * 70)

    if controller is None:

        print(
            "Cannot reset: controller unavailable."
        )

        return {
            "success": False,
            "error": (
                "AdaController is unavailable."
            )
        }

    try:

        controller.reset_job()

        print(
            "Ada conversation memory cleared."
        )

        print(
            "Ada job state reset."
        )

        print("=" * 70)
        print()

        return {
            "success": True,
            "message": (
                "Ada job reset successfully."
            ),
            "job_state": (
                controller.get_job_state()
            )
        }

    except Exception as error:

        print()
        print("=" * 70)
        print("ADA RESET ERROR")
        print("=" * 70)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print("=" * 70)
        print()

        return {
            "success": False,
            "error": (
                f"{type(error).__name__}: "
                f"{str(error)}"
            )
        }


# ==========================================================
# APPLICATION STARTUP INFORMATION
# ==========================================================

@app.get("/api/debug")
def debug():

    if controller is None:

        return {
            "controller_created": False,
            "groq_connected": False,
            "active_service": None,
            "job_state": None
        }

    try:

        intelligence = (
            controller.intelligence
        )

        return {
            "controller_created": True,
            "groq_connected": (
                intelligence.is_connected()
            ),
            "groq_model": (
                intelligence.get_model()
            ),
            "active_service": (
                controller.get_active_service()
            ),
            "job_state": (
                controller.get_job_state()
            ),
            "memory_message_count": (
                intelligence.memory.get_message_count()
            )
        }

    except Exception as error:

        print()
        print("=" * 70)
        print("ADA DEBUG ERROR")
        print("=" * 70)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            repr(error)
        )

        traceback.print_exc()

        print("=" * 70)
        print()

        return {
            "controller_created": True,
            "error": (
                f"{type(error).__name__}: "
                f"{str(error)}"
            )
        }
