"""
ada_api.py

Ada Intelligence API
Naija Pocket Business Center

DIAGNOSTIC VERSION
------------------

Purpose:
    Expose the REAL backend/intelligence error.

Flow:

    workspace.html
        ↓
    FastAPI
        ↓
    AdaController
        ↓
    AdaAIEngine
        ↓
    Groq

IMPORTANT:
    This version does NOT hide the real exception in the
    server terminal.

    Every failure prints:
        - error type
        - error message
        - representation
        - full traceback

    The customer-facing response remains simple, while
    the terminal exposes the real technical problem.
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
    title="Ada Intelligence API - Naija Pocket Business Center",
    version="0.2.0"
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

    # These fields are accepted because workspace.html
    # already sends them.

    event: str | None = None
    customer_id: str | None = None
    job_id: str | None = None
    client_request_id: str | None = None
    activate_intelligence: bool | None = None


# ==========================================================
# ADA CONTROLLER
# ==========================================================

print()
print("=" * 80)
print("STARTING ADA INTELLIGENCE API")
print("=" * 80)

controller = None


try:

    controller = AdaController()

    print()
    print("ADA CONTROLLER CREATED:", True)

    try:

        print(
            "GROQ CONNECTED:",
            controller.intelligence.is_connected()
        )

    except Exception as error:

        print()
        print("!!!!!!!! GROQ CONNECTION CHECK FAILED !!!!!!!!")
        print("ERROR TYPE:", type(error).__name__)
        print("ERROR:", str(error))
        traceback.print_exc()

    try:

        print(
            "GROQ MODEL:",
            controller.intelligence.get_model()
        )

    except Exception as error:

        print()
        print("!!!!!!!! GROQ MODEL CHECK FAILED !!!!!!!!")
        print("ERROR TYPE:", type(error).__name__)
        print("ERROR:", str(error))
        traceback.print_exc()

except Exception as error:

    print()
    print("=" * 80)
    print("!!!!!!!! ADA STARTUP REAL ERROR !!!!!!!!")
    print("=" * 80)

    print()
    print("ERROR TYPE:")
    print(type(error).__name__)

    print()
    print("ERROR MESSAGE:")
    print(str(error))

    print()
    print("ERROR REPRESENTATION:")
    print(repr(error))

    print()
    print("FULL TRACEBACK:")
    print()

    traceback.print_exc()

    print()
    print("=" * 80)
    print("!!!!!!!! END ADA STARTUP REAL ERROR !!!!!!!!")
    print("=" * 80)
    print()

    controller = None


print()
print("=" * 80)
print("ADA INTELLIGENCE API READY")
print("=" * 80)
print(
    "CONTROLLER AVAILABLE:",
    controller is not None
)
print("=" * 80)
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

    groq_connected = False
    groq_model = None

    if controller is not None:

        try:

            groq_connected = (
                controller.intelligence.is_connected()
            )

        except Exception as error:

            print()
            print("HEALTH CHECK GROQ ERROR:")
            print(type(error).__name__)
            print(str(error))
            traceback.print_exc()

        try:

            groq_model = (
                controller.intelligence.get_model()
            )

        except Exception as error:

            print()
            print("HEALTH CHECK MODEL ERROR:")
            print(type(error).__name__)
            print(str(error))
            traceback.print_exc()

    return {

        "status": "ok",

        "service":
            "Ada FastAPI",

        "ada_controller":
            controller is not None,

        "groq_connected":
            groq_connected,

        "groq_model":
            groq_model

    }


# ==========================================================
# CHAT
# ==========================================================

@app.post("/api/chat")
def chat(req: ChatRequest):

    print()
    print("=" * 80)
    print("!!!!!!!! NEW ADA CHAT REQUEST !!!!!!!!")
    print("=" * 80)

    print()
    print("SERVICE:")
    print(repr(req.service))

    print()
    print("MESSAGE:")
    print(repr(req.message))

    print()
    print("EVENT:")
    print(repr(req.event))

    print()
    print("CUSTOMER ID:")
    print(repr(req.customer_id))

    print()
    print("JOB ID:")
    print(repr(req.job_id))

    print()
    print("CLIENT REQUEST ID:")
    print(repr(req.client_request_id))

    print()
    print("ACTIVATE INTELLIGENCE:")
    print(repr(req.activate_intelligence))

    print()
    print("CONTROLLER EXISTS:")
    print(controller is not None)

    print()
    print("=" * 80)


    # ======================================================
    # REQUEST VALIDATION
    # ======================================================

    if not req.message:

        print()
        print("=" * 80)
        print("!!!!!!!! EMPTY MESSAGE !!!!!!!!")
        print("=" * 80)

        return {

            "success":
                False,

            "reply":
                "Please enter a message.",

            "error":
                "FastAPI received an empty message."

        }


    # ======================================================
    # CONTROLLER CHECK
    # ======================================================

    if controller is None:

        print()
        print("=" * 80)
        print("!!!!!!!! CONTROLLER IS NONE !!!!!!!!")
        print("=" * 80)

        return {

            "success":
                False,

            "reply":
                "Your request could not be connected to the Business Center right now.",

            "error":
                "AdaController was not created during startup."

        }


    # ======================================================
    # CONTROLLER DIAGNOSTICS
    # ======================================================

    try:

        print()
        print("=" * 80)
        print("ADA CONTROLLER STATUS")
        print("=" * 80)

        try:

            print(
                "Groq Connected:",
                controller.intelligence.is_connected()
            )

        except Exception as error:

            print()
            print("GROQ STATUS CHECK FAILED")

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR:",
                str(error)
            )

            traceback.print_exc()


        try:

            print(
                "Groq Model:",
                controller.intelligence.get_model()
            )

        except Exception as error:

            print()
            print("MODEL CHECK FAILED")

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR:",
                str(error)
            )

            traceback.print_exc()


        print("=" * 80)


        # ==================================================
        # SEND TO ADA CONTROLLER
        # ==================================================

        print()
        print("=" * 80)
        print("SENDING REQUEST TO ADA CONTROLLER")
        print("=" * 80)

        print(
            "message =",
            repr(req.message)
        )

        print(
            "service =",
            repr(req.service)
        )

        print("=" * 80)


        reply = controller.process_message(

            message=
                req.message,

            service=
                req.service

        )


        # ==================================================
        # ENGINE SUCCESS
        # ==================================================

        print()
        print("=" * 80)
        print("!!!!!!!! ADA INTELLIGENCE SUCCESS !!!!!!!!")
        print("=" * 80)

        print()
        print("RESPONSE TYPE:")
        print(type(reply).__name__)

        print()
        print("RESPONSE:")
        print(repr(reply))

        print()
        print("=" * 80)


        if reply is None:

            raise RuntimeError(
                "AdaController returned None."
            )


        reply = str(
            reply
        ).strip()


        if not reply:

            raise RuntimeError(
                "AdaController returned an empty response."
            )


        return {

            "success":
                True,

            "reply":
                reply

        }


    # ======================================================
    # REAL EXCEPTION
    # ======================================================

    except Exception as error:

        print()
        print()
        print("#" * 80)
        print("!!!!!!!! REAL ADA INTELLIGENCE ERROR !!!!!!!!")
        print("#" * 80)

        print()
        print("ERROR TYPE:")
        print(type(error).__name__)

        print()
        print("ERROR MESSAGE:")
        print(str(error))

        print()
        print("ERROR REPRESENTATION:")
        print(repr(error))

        print()
        print("FULL TRACEBACK:")
        print()

        traceback.print_exc()

        print()
        print("#" * 80)
        print("!!!!!!!! END REAL ADA INTELLIGENCE ERROR !!!!!!!!")
        print("#" * 80)
        print()


        # --------------------------------------------------
        # IMPORTANT
        #
        # We deliberately expose the actual error in the
        # JSON response too.
        #
        # This is a diagnostic version.
        # Do NOT leave this version exposed publicly once
        # debugging is finished.
        # --------------------------------------------------

        return {

            "success":
                False,

            "reply":
                "Ada encountered a real processing error. The technical details have been exposed for troubleshooting.",

            "error":
                (
                    f"{type(error).__name__}: "
                    f"{str(error)}"
                )

        }


# ==========================================================
# DIRECT SERVER DIAGNOSTIC
# ==========================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("ADA API MODULE DIRECT EXECUTION")
    print("=" * 80)
    print()

    print(
        "Controller:",
        controller
    )

    if controller is not None:

        try:

            print(
                "Groq Connected:",
                controller.intelligence.is_connected()
            )

        except Exception as error:

            print()
            print("GROQ CHECK FAILED")
            print(type(error).__name__)
            print(str(error))
            traceback.print_exc()

    print()
    print("=" * 80)
