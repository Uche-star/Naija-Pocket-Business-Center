ada_api.py — Diagnostic Replacement

"""
ada_api.py

Ada Intelligence API
Naija Pocket Business Center

DIAGNOSTIC VERSION
------------------

This version preserves the existing customer/job session architecture
but temporarily exposes the REAL AdaController exception through the
/api/chat response when an intelligence request fails.

IMPORTANT:
This is for debugging only.

Once the actual failure is identified, replace the diagnostic error
response with the normal customer-safe message again.
"""

# ==========================================================
# IMPORTS
# ==========================================================

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pydantic import BaseModel

from pathlib import Path

import traceback
import uuid


from ada_controller import AdaController


# ==========================================================
# APPLICATION
# ==========================================================

app = FastAPI(
    title="Ada Intelligence API - Naija Pocket Business Center",
    version="0.6.0",
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
# DIRECTORIES
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"

VOICE_DIR = BASE_DIR / "voice_uploads"

UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

VOICE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ==========================================================
# CUSTOMER MESSAGES
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
    "Your file has been received and is now attached "
    "to your active request."
)

CUSTOMER_UPLOAD_ERROR_MESSAGE = (
    "Sorry, we could not receive that file right now. "
    "Please try again."
)

CUSTOMER_VOICE_SUCCESS_MESSAGE = (
    "Your voice recording has been received. "
    "Ada can now continue with your request."
)

CUSTOMER_VOICE_ERROR_MESSAGE = (
    "Sorry, we could not receive your voice recording right now. "
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
# SESSION STORAGE
# ==========================================================

ADA_SESSIONS = {}

MAX_SESSIONS = 100


# ==========================================================
# UPLOAD REGISTRY
# ==========================================================

ADA_UPLOADS = {}


# ==========================================================
# SESSION VALUE CLEANING
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
        "unknown",
    }:
        return None

    return value


# ==========================================================
# NORMALISE EVENT
# ==========================================================

def normalise_event(event):

    event = clean_session_value(event)

    if not event:
        return None

    return (
        event
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


# ==========================================================
# SESSION KEY
# ==========================================================

def get_session_key(
    customer_id=None,
    job_id=None,
    client_request_id=None,
    service=None,
):

    customer_id = clean_session_value(customer_id)

    job_id = clean_session_value(job_id)

    client_request_id = clean_session_value(
        client_request_id
    )

    service = clean_session_value(service)

    if customer_id and job_id:

        return (
            f"customer:{customer_id}:"
            f"job:{job_id}"
        )

    if job_id:

        return f"job:{job_id}"

    if customer_id:

        return f"customer:{customer_id}"

    if client_request_id:

        return f"client:{client_request_id}"

    if service:

        return (
            "service:"
            f"{service.lower()}"
        )

    return "anonymous"


# ==========================================================
# ADA CONTROLLER
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
            "ADA SESSION LIMIT REACHED"
        )

        print(
            "REMOVING OLDEST SESSION:",
            repr(oldest_key)
        )

        ADA_SESSIONS.pop(
            oldest_key,
            None
        )

        ADA_UPLOADS.pop(
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

    removed = False

    if session_key in ADA_SESSIONS:

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

        removed = True

    ADA_UPLOADS.pop(
        session_key,
        None
    )

    return removed


# ==========================================================
# SESSION FILE REGISTRATION
# ==========================================================

def register_upload(
    session_key,
    upload_information,
):

    if session_key not in ADA_UPLOADS:

        ADA_UPLOADS[session_key] = []

    ADA_UPLOADS[session_key].append(
        upload_information
    )


# ==========================================================
# STARTUP
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

print(
    "VOICE DIRECTORY:",
    str(VOICE_DIR)
)

print(
    "DIAGNOSTIC MODE:",
    "ENABLED"
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
# CONVERSATION
# ==========================================================

@app.get("/conversation.html")
def conversation():

    return FileResponse(
        BASE_DIR / "conversation.html"
    )


# ==========================================================
# WORKSPACE
# ==========================================================

@app.get("/workspace.html")
def workspace():

    return FileResponse(
        BASE_DIR / "workspace.html"
    )


# ==========================================================
# VOICE PAGE
# ==========================================================

@app.get("/voice")
def voice_page():

    voice_file = BASE_DIR / "voice.html"

    if voice_file.exists():

        return FileResponse(
            voice_file
        )

    return {
        "success": False,
        "message": CUSTOMER_SERVICE_MESSAGE,
    }


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

            groq_connected = bool(
                controller.intelligence.is_connected()
            )

        except Exception as error:

            print(
                "HEALTH CONNECTION ERROR:",
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
                groq_model,

            "uploads":
                len(
                    ADA_UPLOADS.get(
                        session_key,
                        []
                    )
                ),

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
            session_status,

    }


# ==========================================================
# FILE UPLOAD HANDLER
# ==========================================================

async def handle_file_upload(
    file: UploadFile,
    customer_id=None,
    job_id=None,
    client_request_id=None,
    service=None,
):

    session_key = get_session_key(
        customer_id=customer_id,
        job_id=job_id,
        client_request_id=client_request_id,
        service=service,
    )

    try:

        safe_customer = (
            clean_session_value(customer_id)
            or
            "anonymous"
        )

        safe_job = (
            clean_session_value(job_id)
            or
            "job"
        )

        original_name = Path(
            file.filename or
            "uploaded_file"
        ).name

        unique_name = (
            uuid.uuid4().hex
            + "_"
            + original_name
        )

        customer_folder = (
            UPLOAD_DIR /
            safe_customer
        )

        job_folder = (
            customer_folder /
            safe_job
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

        file_size = len(content)

        upload_information = {

            "original_filename":
                original_name,

            "stored_filename":
                unique_name,

            "path":
                str(destination),

            "content_type":
                file.content_type,

            "size":
                file_size,

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
                ),

        }

        register_upload(
            session_key,
            upload_information
        )

        print()
        print("=" * 80)
        print("ADA FILE UPLOAD SUCCESS")
        print("=" * 80)

        print(
            "SESSION:",
            repr(session_key)
        )

        print(
            "FILE:",
            repr(original_name)
        )

        print(
            "SIZE:",
            file_size
        )

        print(
            "PATH:",
            str(destination)
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
                ),

            "session_id":
                session_key,

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
                CUSTOMER_UPLOAD_ERROR_MESSAGE,

            "session_id":
                session_key,

        }


# ==========================================================
# FILE UPLOAD
# ==========================================================

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):

    return await handle_file_upload(
        file=file,
        customer_id=customer_id,
        job_id=job_id,
        client_request_id=client_request_id,
        service=service,
    )


# ==========================================================
# API FILE UPLOAD
# ==========================================================

@app.post("/api/upload")
async def api_upload_file(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):

    return await handle_file_upload(
        file=file,
        customer_id=customer_id,
        job_id=job_id,
        client_request_id=client_request_id,
        service=service,
    )


# ==========================================================
# VOICE UPLOAD HANDLER
# ==========================================================

async def handle_voice_upload(
    file: UploadFile,
    customer_id=None,
    job_id=None,
    client_request_id=None,
    service=None,
):

    session_key = get_session_key(
        customer_id=customer_id,
        job_id=job_id,
        client_request_id=client_request_id,
        service=service,
    )

    try:

        safe_customer = (
            clean_session_value(customer_id)
            or
            "anonymous"
        )

        safe_job = (
            clean_session_value(job_id)
            or
            "job"
        )

        original_name = Path(
            file.filename or
            "voice_recording"
        ).name

        suffix = Path(
            original_name
        ).suffix.lower()

        if not suffix:

            suffix = ".webm"

        unique_name = (
            uuid.uuid4().hex
            + suffix
        )

        customer_folder = (
            VOICE_DIR /
            safe_customer
        )

        job_folder = (
            customer_folder /
            safe_job
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

        file_size = len(content)

        voice_information = {

            "original_filename":
                original_name,

            "stored_filename":
                unique_name,

            "path":
                str(destination),

            "content_type":
                file.content_type,

            "size":
                file_size,

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
                ),

        }

        register_upload(
            session_key,
            voice_information
        )

        print()
        print("=" * 80)
        print("ADA VOICE UPLOAD SUCCESS")
        print("=" * 80)

        print(
            "SESSION:",
            repr(session_key)
        )

        print(
            "FILE:",
            repr(original_name)
        )

        print(
            "SIZE:",
            file_size
        )

        print(
            "PATH:",
            str(destination)
        )

        print("=" * 80)
        print()

        return {

            "success":
                True,

            "message":
                CUSTOMER_VOICE_SUCCESS_MESSAGE,

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
                ),

            "session_id":
                session_key,

        }

    except Exception as error:

        print()
        print("#" * 80)
        print("VOICE UPLOAD ERROR")
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
                CUSTOMER_VOICE_ERROR_MESSAGE,

            "session_id":
                session_key,

        }


# ==========================================================
# VOICE
# ==========================================================

@app.post("/api/voice")
async def api_voice_upload(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):

    return await handle_voice_upload(
        file=file,
        customer_id=customer_id,
        job_id=job_id,
        client_request_id=client_request_id,
        service=service,
    )


# ==========================================================
# VOICE ALIAS
# ==========================================================

@app.post("/api/voice/upload")
async def api_voice_upload_alias(
    file: UploadFile = File(...),
    customer_id: str | None = Form(None),
    job_id: str | None = Form(None),
    client_request_id: str | None = Form(None),
    service: str | None = Form(None),
):

    return await handle_voice_upload(
        file=file,
        customer_id=customer_id,
        job_id=job_id,
        client_request_id=client_request_id,
        service=service,
    )


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
    # VALIDATE MESSAGE
    # ======================================================

    message = clean_session_value(
        req.message
    )

    if not message:

        return {

            "success":
                False,

            "reply":
                CUSTOMER_EMPTY_MESSAGE,

        }

    # ======================================================
    # SESSION
    # ======================================================

    session_key = get_session_key(
        customer_id=req.customer_id,
        job_id=req.job_id,
        client_request_id=req.client_request_id,
        service=req.service,
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
        "start_new_conversation",

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
    # CREATE / GET CONTROLLER
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
                session_key,

            # DIAGNOSTIC
            "diagnostic":
                {
                    "stage":
                        "controller_creation",

                    "error_type":
                        type(error).__name__,

                    "error":
                        str(error),

                    "traceback":
                        traceback.format_exc(),
                },

        }

    # ======================================================
    # CONTROLLER STATUS
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
            "INTELLIGENCE CONNECTED:",
            controller.intelligence.is_connected()
        )

    except Exception as error:

        print(
            "INTELLIGENCE STATUS CHECK FAILED:",
            type(error).__name__,
            str(error)
        )

        traceback.print_exc()

    try:

        print(
            "INTELLIGENCE MODEL:",
            controller.intelligence.get_model()
        )

    except Exception as error:

        print(
            "MODEL CHECK FAILED:",
            type(error).__name__,
            str(error)
        )

        traceback.print_exc()

    try:

        print(
            "ACTIVE SERVICE BEFORE:",
            repr(
                controller.get_active_service()
            )
        )

    except Exception as error:

        print(
            "ACTIVE SERVICE BEFORE UNAVAILABLE:",
            type(error).__name__,
            str(error)
        )

    try:

        print(
            "JOB STATE BEFORE:",
            controller.get_job_state()
        )

    except Exception as error:

        print(
            "JOB STATE BEFORE UNAVAILABLE:",
            type(error).__name__,
            str(error)
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

        # --------------------------------------------------
        # THIS IS THE IMPORTANT CALL
        # --------------------------------------------------

        reply = controller.process_message(

            message=message,

            service=req.service,

        )

        # ==================================================
        # RESPONSE VALIDATION
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
            "RESPONSE TYPE:",
            type(reply).__name__
        )

        print(
            "RESPONSE:",
            repr(reply)
        )

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
                ),

        }

    # ======================================================
    # DIAGNOSTIC ADA ERROR
    # ======================================================

    except Exception as error:

        error_type = type(error).__name__

        error_message = str(error)

        error_traceback = traceback.format_exc()

        print()
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
            "MESSAGE:",
            repr(message)
        )

        print(
            "EVENT:",
            repr(normalized_event)
        )

        print(
            "ERROR TYPE:",
            error_type
        )

        print(
            "ERROR MESSAGE:",
            error_message
        )

        print()
        print("FULL TRACEBACK:")
        print()

        print(
            error_traceback
        )

        print(
            "#" * 80
        )

        print(
            "END REAL ADA INTELLIGENCE ERROR"
        )

        print(
            "#" * 80
        )

        print()

        # ==================================================
        # IMPORTANT
        #
        # TEMPORARY DIAGNOSTIC RESPONSE
        #
        # This is deliberately exposing the actual exception
        # so Swagger can tell us what is failing.
        # ==================================================

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
                ),

            "diagnostic":
                {

                    "stage":
                        "controller_process_message",

                    "error_type":
                        error_type,

                    "error":
                        error_message,

                    "traceback":
                        error_traceback,

                    "controller":
                        type(controller).__name__,

                    "session":
                        session_key,

                    "service":
                        req.service,

                    "message":
                        message,

                },

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

    print(
        "ACTIVE UPLOAD REGISTRATIONS:",
        len(ADA_UPLOADS)
    )

    print("=" * 80)
