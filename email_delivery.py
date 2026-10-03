"""
Naija Pocket Business Center
EMAIL TEST API

TESTING ONLY
------------

This API exists solely to test NPBC email delivery.

FLOW
----
Client / Review page
        |
        v
POST /api/test-email
        |
        v
Temporary document file
        |
        v
email_delivery.py
        |
        v
Resend
        |
        v
Recipient email

IMPORTANT
---------
This file does NOT use the Payment API.

It does NOT:
- create payments
- verify payments
- unlock downloads
- create products
- modify Review
- regenerate documents
- expose the Resend API key

The Resend API key is read only from the Render
environment variable:

    RESEND_API_KEY

The uploaded test document is stored temporarily
only for the duration of the email operation and
deleted afterward.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from email_delivery import (
    configuration_status,
    send_document_email,
)


# ---------------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Naija Pocket Business Center — Email Test API",
    description="Standalone testing API for NPBC email delivery.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# LIMITS
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 25 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".txt",
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def clean(value: Optional[str]) -> str:
    if value is None:
        return ""

    return str(value).strip()


def safe_filename(filename: str) -> str:
    """
    Prevent path traversal and unsafe filenames.
    """
    name = Path(filename or "document").name

    if not name:
        name = "document"

    return name


def validate_extension(filename: str) -> str:
    """
    Validate supported document extension.
    """
    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(
            sorted(ALLOWED_EXTENSIONS)
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "UNSUPPORTED_DOCUMENT_TYPE. "
                f"Allowed types: {allowed}"
            ),
        )

    return suffix


async def save_upload_temporarily(
    upload: UploadFile,
    destination: Path,
) -> int:
    """
    Stream the uploaded document into a temporary file.

    Returns the number of bytes written.

    The file is rejected if it exceeds MAX_FILE_SIZE.
    """

    total_size = 0

    try:
        with destination.open("wb") as output:

            while True:
                chunk = await upload.read(1024 * 1024)

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "DOCUMENT_TOO_LARGE. "
                            "Maximum test document size is "
                            "25 MB."
                        ),
                    )

                output.write(chunk)

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "TEMPORARY_DOCUMENT_SAVE_FAILED: "
                f"{str(exc)}"
            ),
        ) from exc

    return total_size


# ---------------------------------------------------------------------------
# ROOT
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "ok": True,
        "service": "Naija Pocket Business Center",
        "component": "Email Test API",
        "payment_api": False,
        "purpose": "EMAIL_TESTING_ONLY",
    }


# ---------------------------------------------------------------------------
# HEALTH
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "email_test_api",
        "payment_api": False,
    }


# ---------------------------------------------------------------------------
# EMAIL CONFIGURATION STATUS
# ---------------------------------------------------------------------------

@app.get("/api/test-email/status")
def email_status():
    """
    Safe diagnostic endpoint.

    NEVER returns the actual Resend API key.
    """

    status = configuration_status()

    return {
        "ok": True,
        "payment_api": False,
        "email_service": status,
    }


# ---------------------------------------------------------------------------
# SEND TEST EMAIL
# ---------------------------------------------------------------------------

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
    Send one uploaded document as a test email.

    This endpoint has NOTHING to do with payments.

    FORM FIELDS
    -----------
    recipient_email
    document
    service
    document_title
    customer_name
    subject
    """

    recipient_email = clean(recipient_email)
    service = clean(service)
    document_title = clean(document_title)
    customer_name = clean(customer_name)
    subject = clean(subject)

    if not recipient_email:
        raise HTTPException(
            status_code=400,
            detail="RECIPIENT_EMAIL_REQUIRED",
        )

    if not document:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_REQUIRED",
        )

    original_filename = safe_filename(
        document.filename or "document"
    )

    suffix = validate_extension(
        original_filename
    )

    temporary_path: Optional[Path] = None

    try:
        # ---------------------------------------------------------------
        # CREATE TEMPORARY FILE
        # ---------------------------------------------------------------

        with tempfile.NamedTemporaryFile(
            prefix="npbc_email_test_",
            suffix=suffix,
            delete=False,
        ) as temporary_file:

            temporary_path = Path(
                temporary_file.name
            )

        # ---------------------------------------------------------------
        # SAVE UPLOAD
        # ---------------------------------------------------------------

        document_size = await save_upload_temporarily(
            upload=document,
            destination=temporary_path,
        )

        if document_size <= 0:
            raise HTTPException(
                status_code=400,
                detail="DOCUMENT_EMPTY",
            )

        # ---------------------------------------------------------------
        # SEND THROUGH EXISTING EMAIL DELIVERY MODULE
        # ---------------------------------------------------------------

        result = send_document_email(
            recipient_email=recipient_email,
            document_path=temporary_path,
            service=service,
            document_title=document_title,
            customer_name=customer_name,
            subject=subject or None,
        )

        # ---------------------------------------------------------------
        # SUCCESS
        # ---------------------------------------------------------------

        return {
            "ok": True,
            "message": "EMAIL_TEST_SENT",
            "payment_api": False,
            "recipient": recipient_email,
            "filename": original_filename,
            "document_size": document_size,
            "message_id": result.get(
                "message_id",
                "",
            ),
            "email_result": result,
        }

    except HTTPException:
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "EMAIL_TEST_FAILED: "
                f"{str(exc)}"
            ),
        ) from exc

    finally:
        # ---------------------------------------------------------------
        # DELETE TEMPORARY DOCUMENT
        # ---------------------------------------------------------------

        if temporary_path is not None:

            try:
                if temporary_path.exists():
                    temporary_path.unlink()

            except Exception:
                # Do not replace a successful email result with
                # a cleanup error.
                pass

        try:
            await document.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# OPTIONAL SIMPLE TEST FORM
# ---------------------------------------------------------------------------

@app.get("/test")
def test_page():
    """
    Very small browser test page.

    This is only for testing the API without modifying Review.
    """

    return {
        "ok": True,
        "message": (
            "Use POST /api/test-email with multipart/form-data."
        ),
        "fields": {
            "recipient_email": "required",
            "document": "required",
            "service": "optional",
            "document_title": "optional",
            "customer_name": "optional",
            "subject": "optional",
        },
    }
