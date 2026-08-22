"""
ada_api.py

Ada Intelligence API
Naija Pocket Business Center

CUSTOMER / JOB SESSION-ISOLATED VERSION

Flow:

    workspace.html
        ↓
    FastAPI
        ↓
    Customer / Job Session
        ↓
    AdaController
        ↓
    AdaAIEngine
        ↓
    Groq

This file is the single FastAPI gateway for the customer-facing
workspace.

Important:
- Customer/job sessions are isolated.
- Technical diagnostics remain on the server.
- Customer-facing responses never expose Python, FastAPI, Groq,
  traceback or internal server details.
- Service selection immediately enters AdaController.
"""

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
import traceback
import uuid


from ada_controller import AdaController


# ==========================================================
# FASTAPI APPLICATION
# ==========================================================

app = FastAPI(
    title="Ada Intelligence API - Naija Pocket Business Center",
    version="0.5.0"
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

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ==========================================================
# CUSTOMER-FACING MESSAGES
# ==========================================================

CUSTOMER_PROCESSING_MESSAGE = (
    "Sorry, we could not complete that step right now. "
    "Please try again in a moment. "
    "If the problem continues, please contact Customer Service."
)

CUSTOMER_START_MESSAGE = (
    "Sorry, we could not start your request right now. "
    "Please try again in a moment. "
    "If the problem continues, please contact Customer Service."
)

CUSTOMER_EMPTY_MESSAGE = (
    "Please enter a message so Ada can continue helping you."
)

CUSTOMER_UPLOAD_SUCCESS_MESSAGE = (
    "Your file has been received and is now attached to your active request."
)

CUSTOMER_UPLOAD_ERROR_MESSAGE = (
    "Sorry, we could not receive that file right now. "
    "Please try again."
)

CUSTOMER_SERVICE_MESSAGE = (
    "Customer Service is available to help. "
    "Please tell us what you need assistance with."
)


# ==========================================================
# CHAT REQUEST
# ==========================================================

class ChatRequest(BaseModel):

    message: str

    service: str | None = None

    event: str | None = None

    customer_id: str | None = None

    job_id: str | None = None

    client_request_id: str | None = None

    activate_intelligence: bool | None = None


# ==========================================================
# ADA SESSION STORAGE
# ==========================================================

ADA_SESSIONS = {}


# ==========================================================
# SESSION LIMIT
# ==========================================================

MAX_SESSIONS = 100


# ==========================================================
# SESSION HELPERS
# ==========================================================

def clean_session_value(value):

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    if value.lower() in {
        "none",
        "null",
        "undefined",
        "unknown"
    }:
        return None

    return value


def get_session_key(req: ChatRequest):

    customer_id = clean_session_value(
        req.customer_id
    )

    job_id = clean_session_value(
        req.job_id
    )

    client_request_id = clean_session_value(
        req.client_request_id
    )

    service = clean_session_value(
        req.service
    )

    # ------------------------------------------------------
    # BEST CASE
    # ------------------------------------------------------

    if customer_id and job_id:

        return (
            f"customer:{customer_id}:"
            f"job:{job_id}"
        )

    # ------------------------------------------------------
    # JOB ONLY
    # ------------------------------------------------------

    if job_id:

        return f"job:{job_id}"

    # ------------------------------------------------------
    # CUSTOMER ONLY
    # ------------------------------------------------------

    if customer_id:

        return f"customer:{customer_id}"

    # ------------------------------------------------------
    # REQUEST FALLBACK
    # ------------------------------------------------------

    if client_request_id:

        return f"client:{client_request_id}"

    # ------------------------------------------------------
    # MANUAL / SWAGGER FALLBACK
    # ------------------------------------------------------

    if service:

        return (
            f"swagger-service:"
            f"{service.lower()}"
        )

    return "anonymous"


# ==========================================================
# ADA SESSION CREATION
# ==========================================================

def get_ada_controller(session_key):

    if session_key in ADA_SESSIONS:

        print()
        print("=" * 80)
        print("REUSING EXISTING ADA SESSION")
        print("=" * 80)

        print(
            "SESSION KEY:",
            repr(session_key)
        )

        print(
            "TOTAL SESSIONS:",
            len(ADA_SESSIONS)
        )

        print("=" * 80)
        print()

        return ADA_SESSIONS[session_key]

    print()
    print("=" * 80)
    print("CREATING NEW ADA SESSION")
    print("=" * 80)

    print(
        "SESSION KEY:",
        repr(session_key)
    )

    print("=" * 80)
    print()

    controller = AdaController()

    if len(ADA_SESSIONS) >= MAX_SESSIONS:

        oldest_key = next(
            iter(ADA_SESSIONS)
        )

        print(
            "ADA SESSION LIMIT REACHED."
        )

        print(
            "REMOVING OLDEST SESSION:",
            repr(oldest_key)
        )

        ADA_SESSIONS.pop(
            oldest_key,
            None
        )

    ADA_SESSIONS[session_key] = controller

    print()
    print("=" * 80)
    print("NEW ADA SESSION CREATED")
    print("=" * 80)

    print(
        "SESSION KEY:",
        repr(session_key)
    )

    print(
        "TOTAL SESSIONS:",
        len(ADA_SESSIONS)
    )

    print("=" * 80)
    print()

    return controller


# ==========================================================
# REMOVE SESSION
# ==========================================================

def remove_ada_session(session_key):

    if session_key not in ADA_SESSIONS:
        return False

    print()
    print("=" * 80)
    print("REMOVING ADA SESSION")
    print("=" * 80)

    print(
        "SESSION KEY:",
        repr(session_key)
    )

    print("=" * 80)
    print()

    ADA_SESSIONS.pop(
        session_key,
        None
    )

    return True


# ==========================================================
# NORMALISE EVENT
# ==========================================================

def normalise_event(event):

    event = clean_session_value(
        event
    )

    if not event:
        return None

    return (
        event.lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


# ==========================================================
# STARTUP DIAGNOSTICS
# ==========================================================

print()
print("=" * 80)
print("STARTING ADA INTELLIGENCE API")
print("=" * 80)

print(
    "SESSION MODE:",
    "CUSTOMER/JOB ISOLATED"
)

print(
    "MAX SESSIONS:",
    MAX_SESSIONS
)

print(
    "UPLOAD DIRECTORY:",
    str(UPLOAD_DIR)
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

    print()
    print("=" * 80)
    print("ADA HEALTH CHECK")
    print("=" * 80)

    print(
        "ACTIVE ADA SESSIONS:",
        len(ADA_SESSIONS)
    )

    print(
        "SESSION KEYS:",
        list(ADA_SESSIONS.keys())
    )

    print("=" * 80)
    print()

    session_status = []

    for session_key, controller in ADA_SESSIONS.items():

        groq_connected = False
        groq_model = None

        try:

            groq_connected = (
                controller.intelligence.is_connected()
            )

        except Exception as error:

            print(
                "HEALTH CONNECTION ERROR:",
                repr(session_key),
                type(error).__name__,
                str(error)
            )

            traceback.print_exc()

        try:

            groq_model = (
                controller.intelligence.get_model()
            )

        except Exception as error:

            print(
                "HEALTH MODEL ERROR:",
                repr(session_key),
                type(error).__name__,
                str(error)
            )

            traceback.print_exc()

        session_status.append({

            "session":
                session_key,

            "groq_connected":
                groq_connected,

            "groq_model":
                groq_model

        })

    return {

        "status":
            "ok",

        "service":
            "Ada FastAPI",

        "session_mode":
            "customer_job_isolated",

        "active_sessions":
            len(ADA_SESSIONS),

        "max_sessions":
            MAX_SESSIONS,

        "sessions":
            session_status

    }


# ==========================================================
# FILE UPLOAD
# ==========================================================

@app.post("/upload")
async def upload_file(

    file: UploadFile = File(...),

    customer_id: str | None = Form(None),

    job_id: str | None = Form(None),

    service: str | None = Form(None)

):

    print()
    print("=" * 80)
    print("NEW FILE UPLOAD")
    print("=" * 80)

    print(
        "FILE:",
        repr(file.filename)
    )

    print(
        "CUSTOMER ID:",
        repr(customer_id)
    )

    print(
        "JOB ID:",
        repr(job_id)
    )

    print(
        "SERVICE:",
        repr(service)
    )

    try:

        original_name = (
            Path(
                file.filename or
                "uploaded_file"
            ).name
        )

        unique_name = (
            uuid.uuid4().hex +
            "_" +
            original_name
        )

        customer_folder = (
            UPLOAD_DIR /
            clean_session_value(
                customer_id
            ) or
            "anonymous"
        )

        job_folder = (
            customer_folder /
            clean_session_value(
                job_id
            ) or
            "job"
        )

        job_folder.mkdir(
            parents=True,
            exist_ok=True
        )

        destination = (
            job_folder /
            unique_name
        )

        content = await file.read()

        destination.write_bytes(
            content
        )

        print(
            "FILE SAVED:",
            str(destination)
        )

        print(
            "FILE SIZE:",
            len(content)
        )

        print("=" * 80)
        print()

        return {

            "success":
                True,

            "message":
                CUSTOMER_UPLOAD_SUCCESS_MESSAGE,

            "filename":
                original_name,

            "stored_filename":
                unique_name,

            "customer_id":
                clean_session_value(
                    customer_id
                ),

            "job_id":
                clean_session_value(
                    job_id
                ),

            "service":
                clean_session_value(
                    service
                )

        }

    except Exception as error:

        print()
        print("#" * 80)
        print("FILE UPLOAD ERROR")
        print("#" * 80)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            str(error)
        )

        traceback.print_exc()

        print("#" * 80)
        print()

        return {

            "success":
                False,

            "message":
                CUSTOMER_UPLOAD_ERROR_MESSAGE

        }


# ==========================================================
# VOICE PAGE
# ==========================================================

@app.get("/voice")
def voice():

    voice_file = (
        BASE_DIR /
        "voice.html"
    )

    if voice_file.exists():

        return FileResponse(
            voice_file
        )

    return {

        "success":
            False,

        "message":
            "Voice service is not currently available."

    }


# ==========================================================
# CHAT
# ==========================================================

@app.post("/api/chat")
def chat(req: ChatRequest):

    print()
    print("#" * 80)
    print("NEW ADA CHAT REQUEST")
    print("#" * 80)

    print(
        "SERVICE:",
        repr(req.service)
    )

    print(
        "MESSAGE:",
        repr(req.message)
    )

    print(
        "EVENT:",
        repr(req.event)
    )

    print(
        "CUSTOMER ID:",
        repr(req.customer_id)
    )

    print(
        "JOB ID:",
        repr(req.job_id)
    )

    print(
        "CLIENT REQUEST ID:",
        repr(req.client_request_id)
    )

    print(
        "ACTIVATE INTELLIGENCE:",
        repr(req.activate_intelligence)
    )

    # ======================================================
    # MESSAGE VALIDATION
    # ======================================================

    message = clean_session_value(
        req.message
    )

    if not message:

        print(
            "EMPTY CHAT MESSAGE"
        )

        return {

            "success":
                False,

            "reply":
                CUSTOMER_EMPTY_MESSAGE

        }

    # ======================================================
    # SESSION
    # ======================================================

    session_key = get_session_key(
        req
    )

    print()
    print("=" * 80)
    print("ADA SESSION INFORMATION")
    print("=" * 80)

    print(
        "SESSION KEY:",
        repr(session_key)
    )

    print(
        "ACTIVE SESSION COUNT:",
        len(ADA_SESSIONS)
    )

    print(
        "SESSION EXISTS:",
        session_key in ADA_SESSIONS
    )

    print("=" * 80)

    # ======================================================
    # EVENT
    # ======================================================

    normalized_event = normalise_event(
        req.event
    )

    # ======================================================
    # NEW JOB EVENTS
    # ======================================================

    new_job_events = {

        "new_job",
        "start_job",
        "reset_job",
        "new_conversation",
        "start_new_job",
        "start_new_conversation"

    }

    if normalized_event in new_job_events:

        print()
        print("=" * 80)
        print("NEW JOB EVENT RECEIVED")
        print("=" * 80)

        print(
            "EVENT:",
            repr(normalized_event)
        )

        print(
            "SESSION:",
            repr(session_key)
        )

        print("=" * 80)

        remove_ada_session(
            session_key
        )

    # ======================================================
    # GET ADA CONTROLLER
    # ======================================================

    try:

        controller = get_ada_controller(
            session_key
        )

    except Exception as error:

        print()
        print("#" * 80)
        print("ADA SESSION CREATION ERROR")
        print("#" * 80)

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR:",
            str(error)
        )

        traceback.print_exc()

        print("#" * 80)

        return {

            "success":
                False,

            "reply":
                CUSTOMER_START_MESSAGE,

            "session_id":
                session_key

        }

    # ======================================================
    # CONTROLLER DIAGNOSTICS
    # ======================================================

    print()
    print("=" * 80)
    print("ADA CONTROLLER STATUS")
    print("=" * 80)

    print(
        "SESSION:",
        repr(session_key)
    )

    print(
        "CONTROLLER:",
        type(controller).__name__
    )

    try:

        print(
            "GROQ CONNECTED:",
            controller.intelligence.is_connected()
        )

    except Exception as error:

        print(
            "GROQ STATUS CHECK FAILED:",
            type(error).__name__,
            str(error)
        )

    try:

        print(
            "GROQ MODEL:",
            controller.intelligence.get_model()
        )

    except Exception as error:

        print(
            "MODEL CHECK FAILED:",
            type(error).__name__,
            str(error)
        )

    try:

        print(
            "ACTIVE SERVICE BEFORE:",
            repr(
                controller.get_active_service()
            )
        )

    except Exception:

        print(
            "ACTIVE SERVICE BEFORE: UNAVAILABLE"
        )

    try:

        print(
            "JOB STATE BEFORE:",
            controller.get_job_state()
        )

    except Exception:

        print(
            "JOB STATE BEFORE: UNAVAILABLE"
        )

    print("=" * 80)

    # ======================================================
    # SEND TO ADA CONTROLLER
    # ======================================================

    try:

        print()
        print("=" * 80)
        print("SENDING REQUEST TO ADA CONTROLLER")
        print("=" * 80)

        print(
            "SESSION:",
            repr(session_key)
        )

        print(
            "MESSAGE:",
            repr(message)
        )

        print(
            "SERVICE:",
            repr(req.service)
        )

        print(
            "EVENT:",
            repr(normalized_event)
        )

        print("=" * 80)

        reply = controller.process_message(

            message=message,

            service=req.service

        )

        # ==================================================
        # VALIDATE ADA RESPONSE
        # ==================================================

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

        # ==================================================
        # SUCCESS
        # ==================================================

        print()
        print("=" * 80)
        print("ADA INTELLIGENCE SUCCESS")
        print("=" * 80)

        print(
            "SESSION:",
            repr(session_key)
        )

        print(
            "RESPONSE:",
            repr(reply)
        )

        try:

            print(
                "ACTIVE SERVICE AFTER:",
                repr(
                    controller.get_active_service()
                )
            )

        except Exception:

            pass

        try:

            print(
                "JOB STATE AFTER:",
                controller.get_job_state()
            )

        except Exception:

            pass

        print("=" * 80)
        print()

        return {

            "success":
                True,

            "reply":
                reply,

            "session_id":
                session_key,

            "event":
                normalized_event,

            "service":
                req.service,

            "job_id":
                clean_session_value(
                    req.job_id
                )

        }

    # ======================================================
    # REAL ADA ERROR
    # ======================================================

    except Exception as error:

        print()
        print("#" * 80)
        print("REAL ADA INTELLIGENCE ERROR")
        print("#" * 80)

        print(
            "SESSION:",
            repr(session_key)
        )

        print(
            "SERVICE:",
            repr(req.service)
        )

        print(
            "EVENT:",
            repr(normalized_event)
        )

        print(
            "ERROR TYPE:",
            type(error).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(error)
        )

        print()
        print("FULL TRACEBACK:")
        traceback.print_exc()

        print("#" * 80)
        print()

        # --------------------------------------------------
        # IMPORTANT:
        #
        # Keep the session alive.
        # --------------------------------------------------

        return {

            "success":
                False,

            "reply":
                CUSTOMER_PROCESSING_MESSAGE,

            "session_id":
                session_key,

            "event":
                normalized_event,

            "service":
                req.service,

            "job_id":
                clean_session_value(
                    req.job_id
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

    print(
        "ACTIVE SESSIONS:",
        len(ADA_SESSIONS)
    )

    print(
        "SESSION KEYS:",
        list(ADA_SESSIONS.keys())
    )

    print("=" * 80)
