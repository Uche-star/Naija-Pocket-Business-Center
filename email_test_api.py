"""
NPBC Email Delivery Test API - Standalone Bridge

Architecture:
npbc_email_test.html
    ->
email_test_api.py
    ->
email_delivery.py
    ->
Resend
    ->
recipient

STANDALONE ONLY
- No Payment API
- No Ada API
- No Workspace
- No database
- No permanent document storage
"""

import os
import tempfile
from pathlib import Path

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
)
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from email_delivery import (
    send_document_email,
    configuration_status,
)


app = FastAPI(
    title="NPBC Email Test API",
    description=(
        "Standalone email delivery test bridge "
        "-> email_delivery.py -> Resend"
    ),
    version="1.0.0",
)


# --------------------------------------------------
# CORS
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

ALLOWED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".txt",
}

MAX_FILE_SIZE = 25 * 1024 * 1024


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def get_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_allowed_file(filename: str) -> bool:
    return get_extension(filename) in ALLOWED_EXTENSIONS


# --------------------------------------------------
# BASIC ROUTES
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "NPBC Email Test API",
        "status": "running",
        "architecture": (
            "npbc_email_test.html -> "
            "email_test_api.py -> "
            "email_delivery.py -> Resend"
        ),
        "payment_api": False,
        "ada_api": False,
        "workspace": False,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "npbc-email-test",
    }


# --------------------------------------------------
# EMAIL CONFIGURATION STATUS
# --------------------------------------------------

@app.get("/api/test-email/status")
def test_email_status():

    try:

        status = configuration_status()

        if isinstance(status, dict):

            sanitized = {
                key: value
                for key, value in status.items()
                if "key" not in key.lower()
                and "secret" not in key.lower()
            }

            return {
                "success": True,
                "config": sanitized,
            }

        return {
            "success": True,
            "config": status,
        }

    except Exception:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Configuration check failed.",
            },
        )


# --------------------------------------------------
# EMAIL TEST
# --------------------------------------------------

@app.post("/api/test-email")
async def send_test_email(
    recipient_email: str = Form(...),
    document: UploadFile = File(...),
    service: str = Form("NPBC Test"),
    document_title: str = Form("Test Document"),
    customer_name: str = Form("NPBC Test User"),
    subject: str = Form("NPBC Email Delivery Test"),
):

    recipient_email = recipient_email.strip()

    if not recipient_email or "@" not in recipient_email:

        raise HTTPException(
            status_code=400,
            detail="Invalid recipient_email",
        )

    if not document.filename:

        raise HTTPException(
            status_code=400,
            detail="No file provided",
        )

    filename = Path(
        document.filename
    ).name

    if not is_allowed_file(filename):

        raise HTTPException(
            status_code=400,
            detail=(
                "File type not allowed. "
                "Allowed: "
                ".docx, .pdf, .xlsx, .pptx, .txt"
            ),
        )

    temp_path = None

    try:

        # ------------------------------------------
        # Read uploaded document
        # ------------------------------------------

        contents = await document.read()

        file_size = len(contents)

        if file_size == 0:

            raise HTTPException(
                status_code=400,
                detail="Empty file",
            )

        if file_size > MAX_FILE_SIZE:

            raise HTTPException(
                status_code=413,
                detail=(
                    "File too large. "
                    "Maximum size is 25 MB."
                ),
            )

        # ------------------------------------------
        # Create temporary file
        # ------------------------------------------

        suffix = get_extension(filename)

        fd, temp_path = tempfile.mkstemp(
            suffix=suffix
        )

        os.close(fd)

        with open(
            temp_path,
            "wb",
        ) as temporary_file:

            temporary_file.write(contents)

        # ------------------------------------------
        # Send using existing email_delivery.py
        # ------------------------------------------

        result = send_document_email(
            recipient_email=recipient_email,
            document_path=temp_path,
            service=service.strip(),
            document_title=document_title.strip(),
            customer_name=customer_name.strip(),
            subject=subject.strip() or None,
        )

        # ------------------------------------------
        # Handle email_delivery.py result
        # ------------------------------------------

        if isinstance(result, dict):

            if result.get("ok") is not True:

                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": result.get(
                            "message",
                            "Email delivery failed.",
                        ),
                        "recipient": recipient_email,
                        "filename": filename,
                    },
                )

            return {
                "success": True,
                "message": (
                    "Email sent successfully via Resend."
                ),
                "recipient": result.get(
                    "recipient",
                    recipient_email,
                ),
                "filename": result.get(
                    "filename",
                    filename,
                ),
                "message_id": result.get(
                    "message_id"
                ),
            }

        # ------------------------------------------
        # Unexpected return type
        # ------------------------------------------

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": (
                    "Unexpected response from "
                    "email_delivery.py."
                ),
                "recipient": recipient_email,
                "filename": filename,
            },
        )

    except HTTPException:
        raise

    except Exception:

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Email delivery failed.",
                "recipient": recipient_email,
                "filename": filename,
            },
        )

    finally:

        # ------------------------------------------
        # Delete temporary document
        # ------------------------------------------

        if temp_path and os.path.exists(temp_path):

            try:
                os.unlink(temp_path)

            except Exception:
                pass
