"""
NPBC EMAIL TEST API
==================================================

Purpose:
    Standalone testing bridge for email_delivery.py.

Flow:
    NPBC Email Test HTML
            ↓
    email_test_api.py
            ↓
    email_delivery.py
            ↓
    Resend
            ↓
    Recipient

This API has NO Payment API logic.
It does NOT create payment records.
It does NOT verify payments.
It does NOT unlock downloads.
It does NOT save uploaded documents permanently.

Required existing file:
    email_delivery.py
"""

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from email_delivery import (
    send_document_email,
    configuration_status,
)


app = FastAPI(
    title="NPBC Email Test API",
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

MAX_FILE_SIZE = 25 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".txt",
}


# --------------------------------------------------
# BASIC ROUTES
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "NPBC Email Test API",
        "message": "Email testing service is running.",
        "payment_api": False,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "NPBC Email Test API",
    }


@app.get("/api/test-email/status")
def email_status():
    """
    Shows email configuration status without exposing
    the actual Resend API key.
    """

    try:
        status = configuration_status()

        return {
            "ok": True,
            "email": status,
        }

    except Exception as exc:

        return {
            "ok": False,
            "message": str(exc),
        }


# --------------------------------------------------
# EMAIL TEST
# --------------------------------------------------

@app.post("/api/test-email")
async def test_email(
    recipient_email: str = Form(...),
    document: UploadFile = File(...),
    service: str = Form(""),
    document_title: str = Form(""),
    customer_name: str = Form(""),
    subject: str = Form(""),
):
    """
    Sends one test document using the existing
    email_delivery.py module.

    Nothing is permanently stored.
    """

    recipient_email = recipient_email.strip()
    service = service.strip()
    document_title = document_title.strip()
    customer_name = customer_name.strip()
    subject = subject.strip()

    if not recipient_email:
        raise HTTPException(
            status_code=400,
            detail="Recipient email is required.",
        )

    if not document:
        raise HTTPException(
            status_code=400,
            detail="A document is required.",
        )

    filename = (
        Path(document.filename or "document")
        .name
    )

    extension = (
        Path(filename)
        .suffix
        .lower()
    )

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported document type. "
                "Allowed types: "
                ".docx, .pdf, .xlsx, .pptx, .txt"
            ),
        )

    temporary_path = None
    total_size = 0

    try:

        # ------------------------------------------
        # Create temporary file
        # ------------------------------------------

        temporary_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=extension,
        )

        temporary_path = temporary_file.name

        # ------------------------------------------
        # Receive uploaded file
        # ------------------------------------------

        while True:

            chunk = await document.read(1024 * 1024)

            if not chunk:
                break

            total_size += len(chunk)

            if total_size > MAX_FILE_SIZE:

                raise HTTPException(
                    status_code=413,
                    detail=(
                        "File is too large. "
                        "Maximum allowed size is 25 MB."
                    ),
                )

            temporary_file.write(chunk)

        temporary_file.close()

        # ------------------------------------------
        # Make sure something was uploaded
        # ------------------------------------------

        if total_size == 0:

            raise HTTPException(
                status_code=400,
                detail="The selected document is empty.",
            )

        # ------------------------------------------
        # Send through existing email_delivery.py
        # ------------------------------------------

        result = send_document_email(
            recipient_email=recipient_email,
            document_path=temporary_path,
            service=service,
            document_title=document_title,
            customer_name=customer_name,
            subject=subject or None,
        )

        # ------------------------------------------
        # Return result
        # ------------------------------------------

        if not isinstance(result, dict):

            return {
                "ok": True,
                "message": "Email delivery function completed.",
                "recipient": recipient_email,
                "filename": filename,
            }

        if not result.get("ok"):

            return {
                "ok": False,
                "message": result.get(
                    "message",
                    "Email delivery failed.",
                ),
                "recipient": recipient_email,
                "filename": filename,
                "details": result,
            }

        return {
            "ok": True,
            "message": (
                "Email sent successfully through "
                "the NPBC email delivery system."
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

    except HTTPException:
        raise

    except Exception as exc:

        return {
            "ok": False,
            "message": "Email test failed.",
            "error": str(exc),
            "recipient": recipient_email,
            "filename": filename,
        }

    finally:

        # ------------------------------------------
        # Delete temporary document
        # ------------------------------------------

        if temporary_path:

            try:
                os.remove(temporary_path)
            except Exception:
                pass


# --------------------------------------------------
# TEST PAGE
# --------------------------------------------------

@app.get("/test")
def test_page_info():
    return {
        "ok": True,
        "message": (
            "Use the NPBC Email Test HTML page "
            "to send a test document."
        ),
        "endpoint": "/api/test-email",
    }
