"""
ada_api.py
Naija Pocket Business Center

CLEAN ADA API
=============

LIVE ARCHITECTURE

    workspace.html
          |
          v
      ada_api.py
          |
          +---- ada_response.py
          |
          +---- BillingManager
          |
          +---- Payment records
          |
          +---- Job/session state
          |
          +---- Protected download
          |
          v
    Ada / gpt-oss-20b

The old AdaController / AdaAIEngine chain is NOT used.

This file is intentionally self-contained around the
customer-facing workflow.

Customer flow:

    Select service
        ->
    Ada starts service
        ->
    Customer sends information
        ->
    Upload / Voice
        ->
    Review
        ->
    Approve
        ->
    Billing
        ->
    Payment
        ->
    Payment confirmation
        ->
    Download

API keys are NEVER stored in this file.
"""

from __future__ import annotations

import json
import os
import traceback
import uuid

from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    UploadFile,
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


# ============================================================
# ADA RESPONSE
# ============================================================

from ada_response import (
    AdaResponse,
    get_ada_model,
    is_configured,
)


# ============================================================
# BILLING
# ============================================================

from billing_manager import BillingManager


# ============================================================
# PAYMENT DATABASE
# ============================================================

from database import (
    get_connection,
    initialize_database,
)

from payments import (
    create_payment,
    get_job_payments,
    update_payment_status,
)


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Ada Intelligence API",
    version="1.0.0",
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(
    __file__
).resolve().parent


UPLOAD_DIR = (
    BASE_DIR /
    "uploads"
)


VOICE_DIR = (
    BASE_DIR /
    "voice_uploads"
)


DELIVERY_DIR = (
    BASE_DIR /
    "deliveries"
)


UPLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


VOICE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


DELIVERY_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# INITIALIZE DATABASE
# ============================================================

try:

    initialize_database()

    print(
        "Ada database initialized."
    )

except Exception as error:

    print(
        "Database initialization warning:",
        type(error).__name__,
        str(error),
    )


# ============================================================
# MANAGERS
# ============================================================

billing = BillingManager()


# ============================================================
# CUSTOMER MESSAGES
# ============================================================

EMPTY_MESSAGE = (
    "Please enter a message so Ada can continue helping you."
)


SERVICE_REQUIRED = (
    "Please select a Business Center service before we begin."
)


PROCESSING_ERROR = (
    "Sorry, we could not complete that step right now. "
    "Please try again in a moment."
)


START_ERROR = (
    "Sorry, we could not start your request right now. "
    "Please try again in a moment."
)


UPLOAD_SUCCESS = (
    "Your file has been received and attached to your "
    "active request."
)


UPLOAD_ERROR = (
    "Sorry, we could not receive that file right now. "
    "Please try again."
)


VOICE_SUCCESS = (
    "Your voice recording has been received and attached "
    "to your active request."
)


VOICE_ERROR = (
    "Sorry, we could not receive your voice recording right now. "
    "Please try again."
)


PAYMENT_PENDING = (
    "Payment is still pending. "
    "Your document will become available after payment "
    "has been confirmed."
)


PAYMENT_REPORTED = (
    "Your payment has been reported successfully. "
    "Payment verification is now pending."
)


DOWNLOAD_LOCKED = (
    "Your document is not available for download yet. "
    "Payment must be confirmed first."
)


# ============================================================
# IN-MEMORY CUSTOMER SESSIONS
# ============================================================

ADA_SESSIONS: dict[str, AdaResponse] = {}

SESSION_DATA: dict[str, dict[str, Any]] = {}

MAX_SESSIONS = 250


# ============================================================
# REQUEST MODEL
# ============================================================

class ChatRequest(BaseModel):

    message: str

    service: str | None = None

    event: str | None = None

    customer_id: str | None = None

    job_id: str | None = None

    client_request_id: str | None = None

    activate_intelligence: bool | None = None


# ============================================================
# PAYMENT REQUEST
# ============================================================

class PaymentCreateRequest(BaseModel):

    job_id: str

    customer_id: str | None = None

    order_number: str | None = None

    service: str | None = None

    amount: float

    payment_method: str = "bank_transfer"


# ============================================================
# HELPER
# ============================================================

def clean(
    value: Any,
) -> str | None:

    if value is None:
        return None

    value = str(
        value
    ).strip()

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


# ============================================================
# SESSION KEY
# ============================================================

def session_key(
    customer_id: str | None,
    job_id: str | None,
    client_request_id: str | None,
) -> str:

    customer_id = clean(
        customer_id
    )

    job_id = clean(
        job_id
    )

    client_request_id = clean(
        client_request_id
    )


    if customer_id and job_id:

        return (
            "customer:"
            + customer_id
            + ":job:"
            + job_id
        )


    if job_id:

        return (
            "job:"
            + job_id
        )


    if customer_id:

        return (
            "customer:"
            + customer_id
        )


    if client_request_id:

        return (
            "request:"
            + client_request_id
        )


    return "anonymous"


# ============================================================
# SESSION DATA
# ============================================================

def get_session_data(
    key: str,
) -> dict[str, Any]:

    if key not in SESSION_DATA:

        SESSION_DATA[key] = {

            "service": None,

            "customer_id": None,

            "job_id": None,

            "client_request_id": None,

            "files": [],

            "voices": [],

            "approved": False,

            "payment_reported": False,

            "payment_id": None,

            "payment_status": "pending",

            "delivery_file": None,

            "order_number": None,

        }


    return SESSION_DATA[key]


# ============================================================
# ADA SESSION
# ============================================================

def get_ada(
    key: str,
    service: str | None = None,
) -> AdaResponse:

    if key in ADA_SESSIONS:

        ada = ADA_SESSIONS[key]

        if service:

            ada.set_service(
                service
            )

        return ada


    if len(
        ADA_SESSIONS
    ) >= MAX_SESSIONS:

        oldest = next(
            iter(
                ADA_SESSIONS
            )
        )

        ADA_SESSIONS.pop(
            oldest,
            None
        )

        SESSION_DATA.pop(
            oldest,
            None
        )


    ada = AdaResponse(
        service=service
    )


    ADA_SESSIONS[key] = ada


    return ada


# ============================================================
# RESET JOB
# ============================================================

def reset_job(
    key: str,
):

    ADA_SESSIONS.pop(
        key,
        None
    )

    SESSION_DATA.pop(
        key,
        None
    )


# ============================================================
# NORMALIZE EVENT
# ============================================================

def normalize_event(
    event: str | None,
) -> str | None:

    event = clean(
        event
    )

    if not event:
        return None

    return (
        event
        .lower()
        .replace(
            "-",
            "_",
        )
        .replace(
            " ",
            "_",
        )
    )


# ============================================================
# ORDER NUMBER
# ============================================================

def create_order_number(
    job_id: str,
) -> str:

    short_job = (
        clean(job_id)
        or uuid.uuid4().hex
    )

    short_job = (
        short_job
        .replace(
            "job_",
            "",
        )
        .replace(
            "-",
            "",
        )
    )


    return (
        "NPBC-"
        + short_job[
            :12
        ].upper()
    )


# ============================================================
# BILLING INFORMATION
# ============================================================

def get_billing(
    service: str | None,
) -> dict[str, Any]:

    if not service:

        return {
            "service": None,
            "price": 0,
            "billing": None,
            "available": False,
        }


    internal_service = (
        billing.normalize_service(
            service
        )
    )


    if not internal_service:

        return {
            "service": service,
            "price": 0,
            "billing": None,
            "available": False,
        }


    item = billing.get_service(
        service
    )


    if not item:

        return {
            "service": internal_service,
            "price": 0,
            "billing": None,
            "available": False,
        }


    return {

        "service":
            internal_service,

        "price":
            item.get(
                "price",
                0,
            ),

        "billing":
            item.get(
                "billing"
            ),

        "available":
            True,

    }


# ============================================================
# SAVE SESSION IDENTITY
# ============================================================

def update_session_identity(
    key: str,
    customer_id: str | None,
    job_id: str | None,
    client_request_id: str | None,
    service: str | None,
):

    data = get_session_data(
        key
    )


    if customer_id:
        data["customer_id"] = (
            clean(customer_id)
        )


    if job_id:
        data["job_id"] = (
            clean(job_id)
        )


    if client_request_id:
        data["client_request_id"] = (
            clean(client_request_id)
        )


    if service:

        data["service"] = (
            clean(service)
        )


    if not data.get(
        "order_number"
    ):

        if data.get(
            "job_id"
        ):

            data["order_number"] = (
                create_order_number(
                    data["job_id"]
                )
            )


# ============================================================
# FILE REGISTRATION
# ============================================================

def register_file(
    key: str,
    information: dict[str, Any],
):

    data = get_session_data(
        key
    )

    data[
        "files"
    ].append(
        information
    )


# ============================================================
# VOICE REGISTRATION
# ============================================================

def register_voice(
    key: str,
    information: dict[str, Any],
):

    data = get_session_data(
        key
    )

    data[
        "voices"
    ].append(
        information
    )


# ============================================================
# APPLICATION CONTEXT
# ============================================================

def build_application_context(
    key: str,
) -> str:

    data = get_session_data(
        key
    )


    service = (
        data.get(
            "service"
        )
        or
        "Not selected"
    )


    files = data.get(
        "files",
        []
    )


    voices = data.get(
        "voices",
        []
    )


    approved = data.get(
        "approved",
        False
    )


    payment_status = data.get(
        "payment_status",
        "pending"
    )


    billing_info = get_billing(
        service
    )


    lines = [

        f"Selected service: {service}",

        (
            "Billing type: "
            + str(
                billing_info.get(
                    "billing"
                )
            )
        ),

        (
            "Official service price: "
            + (
                f"₦{billing_info.get('price', 0):,}"
                if billing_info.get(
                    "available"
                )
                else
                "Unavailable"
            )
        ),

        (
            "Uploaded files: "
            + str(
                len(files)
            )
        ),

        (
            "Voice recordings: "
            + str(
                len(voices)
            )
        ),

        (
            "Customer approved: "
            + str(
                approved
            )
        ),

        (
            "Payment status: "
            + str(
                payment_status
            )
        ),

    ]


    if files:

        lines.append(
            "File names: "
            + ", ".join(
                str(
                    item.get(
                        "original_filename",
                        ""
                    )
                )
                for item in files
            )
        )


    return "\n".join(
        lines
    )


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    file = (
        BASE_DIR /
        "index.html"
    )

    if file.exists():

        return FileResponse(
            file
        )

    return {
        "success": True,
        "service": "Ada Intelligence API",
    }


# ============================================================
# CONVERSATION
# ============================================================

@app.get("/conversation.html")
def conversation():

    file = (
        BASE_DIR /
        "conversation.html"
    )

    if file.exists():

        return FileResponse(
            file
        )

    return {
        "success": False,
        "message": "Conversation page not found.",
    }


# ============================================================
# WORKSPACE
# ============================================================

@app.get("/workspace.html")
def workspace():

    file = (
        BASE_DIR /
        "workspace.html"
    )

    if file.exists():

        return FileResponse(
            file
        )

    return {
        "success": False,
        "message": "Workspace page not found.",
    }


# ============================================================
# PAYMENT PAGE
# ============================================================

@app.get("/payment.html")
def payment_page():

    file = (
        BASE_DIR /
        "payment.html"
    )

    if file.exists():

        return FileResponse(
            file
        )

    return {
        "success": False,
        "message": "Payment page not found.",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {

        "status":
            "ok",

        "service":
            "Ada FastAPI",

        "architecture":
            "clean_ada_response",

        "ada_model":
            get_ada_model(),

        "ada_configured":
            is_configured(),

        "active_sessions":
            len(
                ADA_SESSIONS
            ),

        "billing":
            True,

        "payment":
            True,

        "download":
            True,

    }


# ============================================================
# ADA CHAT
# ============================================================

@app.post("/api/chat")
def chat(
    req: ChatRequest,
):

    message = clean(
        req.message
    )


    if not message:

        return {

            "success":
                False,

            "reply":
                EMPTY_MESSAGE,

        }


    service = clean(
        req.service
    )


    if not service:

        return {

            "success":
                False,

            "reply":
                SERVICE_REQUIRED,

        }


    key = session_key(

        customer_id=
            req.customer_id,

        job_id=
            req.job_id,

        client_request_id=
            req.client_request_id,

    )


    update_session_identity(

        key=key,

        customer_id=
            req.customer_id,

        job_id=
            req.job_id,

        client_request_id=
            req.client_request_id,

        service=
            service,

    )


    event = normalize_event(
        req.event
    )


    if event in {
        "new_job",
        "start_job",
        "reset_job",
        "start_new_job",
        "start_new_conversation",
        "new_conversation",
    }:

        reset_job(
            key
        )

        update_session_identity(

            key=key,

            customer_id=
                req.customer_id,

            job_id=
                req.job_id,

            client_request_id=
                req.client_request_id,

            service=
                service,

        )


    try:

        ada = get_ada(
            key,
            service,
        )


        context = (
            build_application_context(
                key
            )
        )


        reply = ada.respond(

            message=
                message,

            service=
                service,

            event=
                event,

            context=
                context,

        )


        data = get_session_data(
            key
        )


        return {

            "success":
                True,

            "reply":
                reply,

            "session_id":
                key,

            "service":
                data.get(
                    "service"
                ),

            "job_id":
                data.get(
                    "job_id"
                ),

            "billing":
                get_billing(
                    service
                ),

        }


    except Exception as error:

        print(
            "ADA CHAT ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()


        return {

            "success":
                False,

            "reply":
                PROCESSING_ERROR,

            "session_id":
                key,

            "diagnostic":
                {

                    "stage":
                        "ada_response",

                    "error_type":
                        type(error).__name__,

                    "error":
                        str(error),

                },

        }


# ============================================================
# FILE UPLOAD
# ============================================================

async def save_customer_file(
    file: UploadFile,
    customer_id: str | None,
    job_id: str | None,
    client_request_id: str | None,
    service: str | None,
):

    key = session_key(

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

    )


    update_session_identity(

        key=key,

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

        service=
            service,

    )


    try:

        customer_folder = (
            UPLOAD_DIR /
            (
                clean(customer_id)
                or
                "anonymous"
            )
        )


        job_folder = (
            customer_folder /
            (
                clean(job_id)
                or
                "job"
            )
        )


        job_folder.mkdir(
            parents=True,
            exist_ok=True,
        )


        original_name = Path(
            file.filename
            or
            "uploaded_file"
        ).name


        unique_name = (
            uuid.uuid4().hex
            + "_"
            + original_name
        )


        destination = (
            job_folder /
            unique_name
        )


        content = await file.read()


        destination.write_bytes(
            content
        )


        information = {

            "original_filename":
                original_name,

            "stored_filename":
                unique_name,

            "path":
                str(
                    destination
                ),

            "content_type":
                file.content_type,

            "size":
                len(content),

            "customer_id":
                clean(
                    customer_id
                ),

            "job_id":
                clean(
                    job_id
                ),

            "service":
                clean(
                    service
                ),

        }


        register_file(
            key,
            information,
        )


        return {

            "success":
                True,

            "message":
                UPLOAD_SUCCESS,

            "filename":
                original_name,

            "stored_filename":
                unique_name,

            "session_id":
                key,

            "service":
                service,

            "job_id":
                job_id,

        }


    except Exception as error:

        print(
            "FILE UPLOAD ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()


        return {

            "success":
                False,

            "message":
                UPLOAD_ERROR,

            "session_id":
                key,

        }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/upload")
async def upload(
    file: UploadFile = File(...),

    customer_id:
        str | None =
        Form(None),

    job_id:
        str | None =
        Form(None),

    client_request_id:
        str | None =
        Form(None),

    service:
        str | None =
        Form(None),
):

    return await save_customer_file(

        file=file,

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

        service=
            service,

    )


# ============================================================
# API UPLOAD ALIAS
# ============================================================

@app.post("/api/upload")
async def api_upload(
    file: UploadFile = File(...),

    customer_id:
        str | None =
        Form(None),

    job_id:
        str | None =
        Form(None),

    client_request_id:
        str | None =
        Form(None),

    service:
        str | None =
        Form(None),
):

    return await save_customer_file(

        file=file,

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

        service=
            service,

    )


# ============================================================
# VOICE UPLOAD
# ============================================================

@app.post("/api/voice")
async def voice_upload(

    file:
        UploadFile =
        File(...),

    customer_id:
        str | None =
        Form(None),

    job_id:
        str | None =
        Form(None),

    client_request_id:
        str | None =
        Form(None),

    service:
        str | None =
        Form(None),

):

    key = session_key(

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

    )


    update_session_identity(

        key=key,

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

        service=
            service,

    )


    try:

        customer_folder = (
            VOICE_DIR /
            (
                clean(customer_id)
                or
                "anonymous"
            )
        )


        job_folder = (
            customer_folder /
            (
                clean(job_id)
                or
                "job"
            )
        )


        job_folder.mkdir(
            parents=True,
            exist_ok=True,
        )


        original_name = Path(
            file.filename
            or
            "voice_message.webm"
        ).name


        suffix = (
            Path(
                original_name
            ).suffix
            or
            ".webm"
        )


        unique_name = (
            uuid.uuid4().hex
            + suffix
        )


        destination = (
            job_folder /
            unique_name
        )


        content = await file.read()


        destination.write_bytes(
            content
        )


        information = {

            "original_filename":
                original_name,

            "stored_filename":
                unique_name,

            "path":
                str(
                    destination
                ),

            "content_type":
                file.content_type,

            "size":
                len(content),

        }


        register_voice(
            key,
            information,
        )


        return {

            "success":
                True,

            "message":
                VOICE_SUCCESS,

            "filename":
                original_name,

            "stored_filename":
                unique_name,

            "session_id":
                key,

        }


    except Exception as error:

        print(
            "VOICE UPLOAD ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()


        return {

            "success":
                False,

            "message":
                VOICE_ERROR,

            "session_id":
                key,

        }


# ============================================================
# VOICE ALIAS
# ============================================================

@app.post("/api/voice/upload")
async def voice_upload_alias(

    file:
        UploadFile =
        File(...),

    customer_id:
        str | None =
        Form(None),

    job_id:
        str | None =
        Form(None),

    client_request_id:
        str | None =
        Form(None),

    service:
        str | None =
        Form(None),

):

    return await voice_upload(

        file=file,

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

        service=
            service,

    )


# ============================================================
# BILLING ENDPOINT
# ============================================================

@app.get("/api/billing")
def billing_endpoint(
    service: str,
):

    result = get_billing(
        service
    )


    if not result[
        "available"
    ]:

        return {

            "success":
                False,

            "message":
                "Pricing is currently unavailable "
                "for this service.",

        }


    return {

        "success":
            True,

        "service":
            result[
                "service"
            ],

        "price":
            result[
                "price"
            ],

        "billing":
            result[
                "billing"
            ],

        "message":
            billing.bill_message(
                service
            ),

    }


# ============================================================
# APPROVAL
# ============================================================

@app.post("/api/approve")
def approve(
    customer_id: str | None = None,
    job_id: str | None = None,
    client_request_id: str | None = None,
    service: str | None = None,
):

    key = session_key(

        customer_id=
            customer_id,

        job_id=
            job_id,

        client_request_id=
            client_request_id,

    )


    data = get_session_data(
        key
    )


    data[
        "approved"
    ] = True


    if service:

        data[
            "service"
        ] = service


    billing_info = get_billing(
        service
        or
        data.get(
            "service"
        )
    )


    return {

        "success":
            True,

        "approved":
            True,

        "message":
            "Your request has been approved "
            "and is ready for the payment step.",

        "service":
            billing_info.get(
                "service"
            ),

        "amount":
            billing_info.get(
                "price",
                0,
            ),

        "billing":
            billing_info.get(
                "billing"
            ),

        "job_id":
            data.get(
                "job_id"
            ),

        "order_number":
            data.get(
                "order_number"
            ),

    }


# ============================================================
# PAYMENT CREATE
# ============================================================

@app.post("/api/payment/create")
def payment_create(
    req: PaymentCreateRequest,
):

    job_id = clean(
        req.job_id
    )


    if not job_id:

        return {

            "success":
                False,

            "message":
                "Job information is missing.",

        }


    service = clean(
        req.service
    )


    billing_info = get_billing(
        service
    )


    official_amount = float(
        billing_info.get(
            "price",
            0,
        )
    )


    requested_amount = float(
        req.amount
        or
        0
    )


    if (
        official_amount > 0
        and
        requested_amount != official_amount
    ):

        requested_amount = (
            official_amount
        )


    if requested_amount <= 0:

        return {

            "success":
                False,

            "message":
                "A valid payment amount is required.",

        }


    key = session_key(

        customer_id=
            req.customer_id,

        job_id=
            job_id,

        client_request_id=
            None,

    )


    data = get_session_data(
        key
    )


    data[
        "service"
    ] = service


    data[
        "job_id"
    ] = job_id


    if req.customer_id:

        data[
            "customer_id"
        ] = clean(
            req.customer_id
        )


    order_number = (
        clean(
            req.order_number
        )
        or
        data.get(
            "order_number"
        )
        or
        create_order_number(
            job_id
        )
    )


    data[
        "order_number"
    ] = order_number


    # --------------------------------------------------------
    # IMPORTANT
    #
    # The existing database payment table expects an INTEGER
    # job_id, while the customer-facing workspace currently
    # uses a browser-generated job string such as job_xxxxx.
    #
    # We therefore keep the customer job ID as the public
    # identifier and maintain a small API payment registry
    # in the existing database.
    #
    # If a numeric database job already exists, use it.
    # Otherwise create a safe payment record directly.
    # --------------------------------------------------------

    payment_id = None


    try:

        conn = get_connection()

        if conn is not None:

            cursor = conn.cursor()


            numeric_job_id = None


            try:

                numeric_job_id = int(
                    job_id
                )

            except (
                ValueError,
                TypeError,
            ):

                numeric_job_id = None


            if numeric_job_id is not None:

                payment_id = create_payment(

                    numeric_job_id,

                    requested_amount,

                    req.payment_method,

                )


            else:

                # ------------------------------------------------
                # Create a matching internal job record when the
                # frontend job ID is a generated string.
                # ------------------------------------------------

                cursor.execute(
                    """
                    SELECT id
                    FROM jobs
                    WHERE description = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (
                        "NPBC_PUBLIC_JOB:"
                        + job_id,
                    ),
                )


                existing = cursor.fetchone()


                if existing:

                    numeric_job_id = (
                        existing[0]
                    )

                else:

                    cursor.execute(
                        """
                        INSERT INTO jobs
                        (
                            customer_name,
                            phone,
                            service_type,
                            description,
                            status,
                            amount
                        )
                        VALUES
                        (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            clean(
                                req.customer_id
                            )
                            or
                            "Customer",

                            None,

                            service
                            or
                            "Business Center Service",

                            (
                                "NPBC_PUBLIC_JOB:"
                                + job_id
                            ),

                            "approved",

                            requested_amount,
                        ),
                    )


                    numeric_job_id = (
                        cursor.lastrowid
                    )


                    conn.commit()


                payment_id = create_payment(

                    numeric_job_id,

                    requested_amount,

                    req.payment_method,

                )


            conn.close()


    except Exception as error:

        print(
            "PAYMENT CREATE ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()


        return {

            "success":
                False,

            "message":
                "We could not create the payment record. "
                "Please try again.",

        }


    if not payment_id:

        return {

            "success":
                False,

            "message":
                "We could not create the payment record. "
                "Please try again.",

        }


    data[
        "payment_id"
    ] = payment_id


    data[
        "payment_status"
    ] = "pending"


    data[
        "payment_reported"
    ] = True


    return {

        "success":
            True,

        "payment_id":
            payment_id,

        "status":
            "pending",

        "message":
            PAYMENT_REPORTED,

        "job_id":
            job_id,

        "order_number":
            order_number,

        "service":
            service,

        "amount":
            requested_amount,

        "payment_method":
            req.payment_method,

    }


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.get("/api/payment/status")
def payment_status(
    job_id: str,
):

    job_id = clean(
        job_id
    )


    if not job_id:

        return {

            "success":
                False,

            "status":
                "pending",

            "message":
                PAYMENT_PENDING,

        }


    # --------------------------------------------------------
    # First check active in-memory session.
    # --------------------------------------------------------

    found_data = None


    for data in SESSION_DATA.values():

        if (
            data.get(
                "job_id"
            )
            ==
            job_id
        ):

            found_data = data

            break


    if found_data:

        payment_id = (
            found_data.get(
                "payment_id"
            )
        )


        if payment_id:

            try:

                conn = get_connection()

                if conn:

                    cursor = conn.cursor()

                    cursor.execute(
                        """
                        SELECT
                            id,
                            amount,
                            payment_method,
                            payment_status,
                            payment_date
                        FROM payments
                        WHERE id = ?
                        """,
                        (
                            payment_id,
                        ),
                    )

                    row = cursor.fetchone()

                    conn.close()


                    if row:

                        status = (
                            str(
                                row[
                                    3
                                ]
                            )
                            .lower()
                        )


                        found_data[
                            "payment_status"
                        ] = status


                        return {

                            "success":
                                True,

                            "payment_id":
                                row[0],

                            "status":
                                status,

                            "amount":
                                row[1],

                            "payment_method":
                                row[2],

                            "payment_date":
                                row[4],

                            "job_id":
                                job_id,

                        }


            except Exception as error:

                print(
                    "PAYMENT STATUS ERROR:",
                    type(error).__name__,
                    str(error),
                )


    # --------------------------------------------------------
    # Search internal job record using public job marker.
    # --------------------------------------------------------

    try:

        conn = get_connection()

        if conn:

            cursor = conn.cursor()


            cursor.execute(
                """
                SELECT id
                FROM jobs
                WHERE description = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    "NPBC_PUBLIC_JOB:"
                    + job_id,
                ),
            )


            job = cursor.fetchone()


            if job:

                numeric_job_id = (
                    job[0]
                )


                cursor.execute(
                    """
                    SELECT
                        id,
                        amount,
                        payment_method,
                        payment_status,
                        payment_date
                    FROM payments
                    WHERE job_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (
                        numeric_job_id,
                    ),
                )


                payment = (
                    cursor.fetchone()
                )


                conn.close()


                if payment:

                    return {

                        "success":
                            True,

                        "payment_id":
                            payment[0],

                        "status":
                            str(
                                payment[3]
                            ).lower(),

                        "amount":
                            payment[1],

                        "payment_method":
                            payment[2],

                        "payment_date":
                            payment[4],

                        "job_id":
                            job_id,

                    }


            conn.close()


    except Exception as error:

        print(
            "PAYMENT DATABASE STATUS ERROR:",
            type(error).__name__,
            str(error),
        )


    return {

        "success":
            True,

        "payment_id":
            None,

        "status":
            "pending",

        "message":
            PAYMENT_PENDING,

        "job_id":
            job_id,

    }


# ============================================================
# PAYMENT CONFIRMATION
# ============================================================

@app.post("/api/payment/confirm")
def payment_confirm(
    payment_id: int,
):

    try:

        result = update_payment_status(
            payment_id,
            "paid",
        )


        if result is None:

            return {

                "success":
                    False,

                "message":
                    "Payment could not be confirmed.",

            }


        for data in SESSION_DATA.values():

            if (
                data.get(
                    "payment_id"
                )
                ==
                payment_id
            ):

                data[
                    "payment_status"
                ] = "paid"


        return {

            "success":
                True,

            "payment_id":
                payment_id,

            "status":
                "paid",

            "message":
                "Payment confirmed successfully.",

        }


    except Exception as error:

        print(
            "PAYMENT CONFIRM ERROR:",
            type(error).__name__,
            str(error),
        )

        traceback.print_exc()


        return {

            "success":
                False,

            "message":
                "Payment confirmation failed.",

        }


# ============================================================
# DELIVERY FILE REGISTRATION
# ============================================================

@app.post("/api/delivery/register")
def register_delivery(
    job_id: str,
    file_path: str,
):

    job_id = clean(
        job_id
    )

    file_path = clean(
        file_path
    )


    if not job_id or not file_path:

        return {

            "success":
                False,

            "message":
                "Job ID and file path are required.",

        }


    path = Path(
        file_path
    ).resolve()


    try:

        path.relative_to(
            BASE_DIR.resolve()
        )

    except ValueError:

        return {

            "success":
                False,

            "message":
                "Invalid delivery file.",

        }


    if not path.exists() or not path.is_file():

        return {

            "success":
                False,

            "message":
                "Delivery file was not found.",

        }


    for data in SESSION_DATA.values():

        if (
            data.get(
                "job_id"
            )
            ==
            job_id
        ):

            data[
                "delivery_file"
            ] = str(
                path
            )

            return {

                "success":
                    True,

                "message":
                    "Delivery file registered.",

            }


    key = session_key(
        None,
        job_id,
        None,
    )


    data = get_session_data(
        key
    )


    data[
        "job_id"
    ] = job_id


    data[
        "delivery_file"
    ] = str(
        path
    )


    return {

        "success":
            True,

        "message":
            "Delivery file registered.",

    }


# ============================================================
# DOWNLOAD
# ============================================================

@app.get("/api/download")
def download(
    order: str | None = None,
    job_id: str | None = None,
    customer_id: str | None = None,
):

    job_id = clean(
        job_id
    )


    if not job_id:

        return {

            "success":
                False,

            "message":
                DOWNLOAD_LOCKED,

        }


    data = None


    for item in SESSION_DATA.values():

        if (
            item.get(
                "job_id"
            )
            ==
            job_id
        ):

            data = item

            break


    if data is None:

        return {

            "success":
                False,

            "message":
                DOWNLOAD_LOCKED,

        }


    payment_id = data.get(
        "payment_id"
    )


    payment_is_paid = False


    if payment_id:

        try:

            conn = get_connection()

            if conn:

                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT payment_status
                    FROM payments
                    WHERE id = ?
                    """,
                    (
                        payment_id,
                    ),
                )


                row = cursor.fetchone()

                conn.close()


                if row:

                    payment_is_paid = (
                        str(
                            row[0]
                        ).lower()
                        ==
                        "paid"
                    )


        except Exception as error:

            print(
                "DOWNLOAD PAYMENT CHECK ERROR:",
                type(error).__name__,
                str(error),
            )


    if not payment_is_paid:

        return {

            "success":
                False,

            "message":
                DOWNLOAD_LOCKED,

        }


    delivery_file = clean(
        data.get(
            "delivery_file"
        )
    )


    if not delivery_file:

        return {

            "success":
                False,

            "message":
                (
                    "Payment has been confirmed, "
                    "but your document has not yet been "
                    "released for download."
                ),

        }


    path = Path(
        delivery_file
    ).resolve()


    try:

        path.relative_to(
            BASE_DIR.resolve()
        )

    except ValueError:

        return {

            "success":
                False,

            "message":
                "Invalid delivery file.",

        }


    if not path.exists() or not path.is_file():

        return {

            "success":
                False,

            "message":
                (
                    "Your document is not available "
                    "for download yet."
                ),

        }


    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/octet-stream",
    )


# ============================================================
# CUSTOMER SERVICE
# ============================================================

@app.post("/api/customer-service")
def customer_service(
    customer_id: str | None = None,
    job_id: str | None = None,
    service: str | None = None,
):

    key = session_key(
        customer_id,
        job_id,
        None,
    )


    try:

        ada = get_ada(
            key,
            service,
        )


        context = (
            build_application_context(
                key
            )
        )


        reply = ada.respond(

            message=(
                "The customer is requesting "
                "Customer Service assistance. "
                "Please explain the available help "
                "and the next appropriate step."
            ),

            service=service,

            event="customer_service",

            context=context,

        )


        return {

            "success":
                True,

            "reply":
                reply,

        }


    except Exception as error:

        print(
            "CUSTOMER SERVICE ERROR:",
            type(error).__name__,
            str(error),
        )

        return {

            "success":
                False,

            "reply":
                (
                    "Customer Service is available "
                    "to help. Please try again."
                ),

        }


# ============================================================
# SESSION INFORMATION
# ============================================================

@app.get("/api/session")
def session_information(
    customer_id: str | None = None,
    job_id: str | None = None,
    client_request_id: str | None = None,
):

    key = session_key(

        customer_id,
        job_id,
        client_request_id,

    )


    data = get_session_data(
        key
    )


    return {

        "success":
            True,

        "session_id":
            key,

        "customer_id":
            data.get(
                "customer_id"
            ),

        "job_id":
            data.get(
                "job_id"
            ),

        "service":
            data.get(
                "service"
            ),

        "files":
            len(
                data.get(
                    "files",
                    [],
                )
            ),

        "voices":
            len(
                data.get(
                    "voices",
                    [],
                )
            ),

        "approved":
            data.get(
                "approved",
                False,
            ),

        "payment_id":
            data.get(
                "payment_id"
            ),

        "payment_status":
            data.get(
                "payment_status",
                "pending",
            ),

        "order_number":
            data.get(
                "order_number"
            ),

        "delivery_ready":
            bool(
                data.get(
                    "delivery_file"
                )
            ),

    }


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "=" * 70
    )

    print(
        "NAIJA POCKET BUSINESS CENTER"
    )

    print(
        "ADA CLEAN API"
    )

    print(
        "=" * 70
    )

    print(
        "Ada model:",
        get_ada_model(),
    )

    print(
        "Ada configured:",
        is_configured(),
    )

    print(
        "Billing:",
        "READY",
    )

    print(
        "Payments:",
        "READY",
    )

    print(
        "Downloads:",
        "READY",
    )

    print(
        "=" * 70
    )
