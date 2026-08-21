"""
ada_api.py

Ada Intelligence API
Naija Pocket Business Center

SESSION-ISOLATED DIAGNOSTIC VERSION
-----------------------------------

Flow:

    workspace.html
        ↓
    FastAPI
        ↓
    Customer / Job Session
        ↓
    AdaController
        ↓
    AdaAIEngine V11
        ↓
    Groq

IMPORTANT
---------
Each customer/job gets its own AdaController and therefore
its own AdaAIEngine conversation memory.

This prevents:

    Customer A
        ↓
    failed conversation
        ↓
    Customer B
        ↓
    old conversation blocking Customer B

A genuinely new job receives a fresh Ada conversation.

Existing conversation messages for the same job continue
using the same AdaController/AdaAIEngine instance.

This version deliberately preserves diagnostic logging.
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
    version="0.3.0"
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

    event: str | None = None

    customer_id: str | None = None

    job_id: str | None = None

    client_request_id: str | None = None

    activate_intelligence: bool | None = None


# ==========================================================
# ADA SESSION STORAGE
# ==========================================================
#
# IMPORTANT
# ---------
# DO NOT create one global AdaController anymore.
#
# Instead:
#
#     customer/job
#          ↓
#     AdaController
#          ↓
#     AdaAIEngine
#          ↓
#     conversation memory
#
# Example:
#
#     customer_001 + job_001
#         → Controller A
#
#     customer_001 + job_002
#         → Controller B
#
#     customer_002 + job_003
#         → Controller C
#
# Therefore old failed conversations cannot interfere
# with new jobs.
#
# This is an in-memory session store.
#
# It is appropriate for the current single-instance
# Render deployment and gives us the simplest reliable
# solution without introducing a database/session system.
#
# ==========================================================

ADA_SESSIONS = {}


# ==========================================================
# SESSION LIMIT
# ==========================================================
#
# Prevent unlimited memory growth on the Render instance.
#
# When the limit is reached, the oldest stored session
# is removed.
#
# This does NOT affect active customer conversations
# until the server reaches this limit.
#
# ==========================================================

MAX_SESSIONS = 100


# ==========================================================
# NORMALIZE SESSION VALUE
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


# ==========================================================
# CREATE SESSION KEY
# ==========================================================

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
    #
    # customer_id + job_id
    # ------------------------------------------------------

    if customer_id and job_id:

        return (
            f"customer:{customer_id}:"
            f"job:{job_id}"
        )

    # ------------------------------------------------------
    # SECOND BEST
    #
    # job_id alone
    #
    # A job should normally be unique.
    # ------------------------------------------------------

    if job_id:

        return (
            f"job:{job_id}"
        )

    # ------------------------------------------------------
    # CUSTOMER ONLY
    #
    # Useful if the workspace has a customer ID but
    # has not yet created a job ID.
    # ------------------------------------------------------

    if customer_id:

        return (
            f"customer:{customer_id}"
        )

    # ------------------------------------------------------
    # CLIENT REQUEST ID
    #
    # We intentionally DO NOT use this as the primary
    # session identifier because it may be unique for
    # every individual HTTP request.
    #
    # It is therefore only used as a temporary fallback
    # when no customer/job information exists.
    # ------------------------------------------------------

    if client_request_id:

        return (
            f"client:{client_request_id}"
        )

    # ------------------------------------------------------
    # SWAGGER / MANUAL TEST FALLBACK
    #
    # This allows Swagger testing to continue working
    # even when no customer/job IDs are supplied.
    #
    # Service is included so different Swagger services
    # do not immediately collide.
    # ------------------------------------------------------

    if service:

        return (
            f"swagger-service:{service.lower()}"
        )

    return "anonymous"


# ==========================================================
# GET OR CREATE ADA SESSION
# ==========================================================

def get_ada_controller(session_key):

    # ------------------------------------------------------
    # EXISTING SESSION
    # ------------------------------------------------------

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


    # ------------------------------------------------------
    # SESSION DOES NOT EXIST
    # ------------------------------------------------------

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


    # ------------------------------------------------------
    # SESSION LIMIT
    # ------------------------------------------------------

    if len(ADA_SESSIONS) >= MAX_SESSIONS:

        oldest_key = next(
            iter(ADA_SESSIONS)
        )

        print()
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


    # ------------------------------------------------------
    # STORE NEW CONTROLLER
    # ------------------------------------------------------

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
# REMOVE ADA SESSION
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

            print()
            print(
                "HEALTH SESSION GROQ ERROR:"
            )

            print(
                "SESSION:",
                repr(session_key)
            )

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

            groq_model = (
                controller.intelligence.get_model()
            )

        except Exception as error:

            print()
            print(
                "HEALTH SESSION MODEL ERROR:"
            )

            print(
                "SESSION:",
                repr(session_key)
            )

            print(
                "ERROR TYPE:",
                type(error).__name__
            )

            print(
                "ERROR:",
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
# CHAT
# ==========================================================

@app.post("/api/chat")
def chat(req: ChatRequest):

    print()
    print("#" * 80)
    print("!!!!!!!! NEW ADA CHAT REQUEST !!!!!!!!")
    print("#" * 80)

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


    # ======================================================
    # REQUEST VALIDATION
    # ======================================================

    if req.message is None:

        print()
        print(
            "!!!!!!!! MESSAGE IS NONE !!!!!!!!"
        )

        return {

            "success":
                False,

            "reply":
                "Please enter a message.",

            "error":
                "FastAPI received message=None."

        }


    message = str(
        req.message
    ).strip()


    if not message:

        print()
        print(
            "!!!!!!!! EMPTY MESSAGE !!!!!!!!"
        )

        return {

            "success":
                False,

            "reply":
                "Please enter a message.",

            "error":
                "FastAPI received an empty message."

        }


    # ======================================================
    # DETERMINE SESSION
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
    # NEW JOB EVENT
    # ======================================================
    #
    # If workspace explicitly says that a genuinely new
    # job is beginning, remove the old session first.
    #
    # Supported event names:
    #
    #     new_job
    #     start_job
    #     reset_job
    #     new_conversation
    #
    # ======================================================

    event = clean_session_value(
        req.event
    )


    if event:

        normalized_event = (
            event.lower()
            .replace("-", "_")
            .replace(" ", "_")
        )

    else:

        normalized_event = None


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
        print("!!!!!!!! NEW JOB EVENT RECEIVED !!!!!!!!")
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
    # GET SESSION CONTROLLER
    # ======================================================

    try:

        controller = get_ada_controller(
            session_key
        )

    except Exception as error:

        print()
        print("#" * 80)
        print("!!!!!!!! ADA SESSION CREATION ERROR !!!!!!!!")
        print("#" * 80)

        print()
        print("ERROR TYPE:")
        print(
            type(error).__name__
        )

        print()
        print("ERROR MESSAGE:")
        print(
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
        print("#" * 80)
        print()


        return {

            "success":
                False,

            "reply":
                "Ada could not start your request right now.",

            "error":
                (
                    f"{type(error).__name__}: "
                    f"{str(error)}"
                )

        }


    # ======================================================
    # CONTROLLER DIAGNOSTICS
    # ======================================================

    try:

        print()
        print("=" * 80)
        print("ADA CONTROLLER STATUS")
        print("=" * 80)

        print(
            "Session:",
            repr(session_key)
        )

        print(
            "Controller:",
            type(controller).__name__
        )


        try:

            print(
                "Groq Connected:",
                controller.intelligence.is_connected()
            )

        except Exception as error:

            print()
            print(
                "GROQ STATUS CHECK FAILED"
            )

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
            print(
                "MODEL CHECK FAILED"
            )

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
                "Active Service Before:",
                repr(
                    controller.get_active_service()
                )
            )

        except Exception as error:

            print(
                "Active Service:",
                "Unable to read"
            )

            print(
                "ERROR:",
                str(error)
            )


        try:

            print(
                "Job State Before:",
                controller.get_job_state()
            )

        except Exception as error:

            print(
                "Job State:",
                "Unable to read"
            )

            print(
                "ERROR:",
                str(error)
            )


        print("=" * 80)


        # ==================================================
        # SEND TO ADA CONTROLLER
        # ==================================================

        print()
        print("=" * 80)
        print("SENDING REQUEST TO ADA CONTROLLER")
        print("=" * 80)

        print(
            "SESSION =",
            repr(session_key)
        )

        print(
            "MESSAGE =",
            repr(message)
        )

        print(
            "SERVICE =",
            repr(req.service)
        )

        print("=" * 80)


        reply = controller.process_message(

            message=
                message,

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
        print(
            "SESSION:"
        )

        print(
            repr(session_key)
        )

        print()
        print(
            "RESPONSE TYPE:"
        )

        print(
            type(reply).__name__
        )

        print()
        print(
            "RESPONSE:"
        )

        print(
            repr(reply)
        )


        try:

            print()
            print(
                "ACTIVE SERVICE AFTER:"
            )

            print(
                repr(
                    controller.get_active_service()
                )
            )

        except Exception:

            pass


        try:

            print()
            print(
                "JOB STATE AFTER:"
            )

            print(
                controller.get_job_state()
            )

        except Exception:

            pass


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
                reply,

            "session_id":
                session_key

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
        print("SESSION:")
        print(
            repr(session_key)
        )

        print()
        print("ERROR TYPE:")
        print(
            type(error).__name__
        )

        print()
        print("ERROR MESSAGE:")
        print(
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
        print("#" * 80)
        print("!!!!!!!! END REAL ADA INTELLIGENCE ERROR !!!!!!!!")
        print("#" * 80)
        print()


        # --------------------------------------------------
        # IMPORTANT
        #
        # DO NOT DELETE THE SESSION HERE.
        #
        # A temporary Groq/network failure should NOT destroy
        # the customer's conversation.
        #
        # The same job can retry the request.
        #
        # A genuinely new job gets a new session key/event.
        # --------------------------------------------------

        return {

            "success":
                False,

            "reply":
                "Ada encountered a processing error. Please try again.",

            "error":
                (
                    f"{type(error).__name__}: "
                    f"{str(error)}"
                ),

            "session_id":
                session_key

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
        "Active Sessions:",
        len(ADA_SESSIONS)
    )

    print(
        "Session Keys:",
        list(ADA_SESSIONS.keys())
    )

    print()
    print("=" * 80)
