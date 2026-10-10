"""
Naija Pocket Business Center (NPBC)
Email-Focused Payment API
Gmail SMTP Document Delivery

Purpose:
    Send an existing, saved NPBC document to a customer's email.

Email endpoint:
    POST /api/delivery/email

JSON request:
    {
        "recipient_email": "customer@example.com",
        "service": "Seminar Paper",
        "document_title": "Why Nigeria Is Poor in the Midst of Abundance"
    }

Required Render environment variables:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

Optional Render environment variable:
    NPBC_DOCUMENTS_ROOT

Default document directory:
    downloads/

This API does not generate documents or process payments.
"""

import os
import re
import ssl
import smtplib
import mimetypes
import logging

from pathlib import Path
from email.message import EmailMessage
from email.utils import formataddr

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "NPBC Email Delivery API"
APP_VERSION = "1.0.0"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".txt",
}

DOCUMENTS_ROOT = Path(
    os.getenv("NPBC_DOCUMENTS_ROOT", "downloads")
).expanduser()

DEFAULT_SUBJECT = "Your NPBC Document Is Ready"

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger("npbc.email_delivery")


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "NPBC Gmail SMTP document delivery. "
        "This service sends existing saved documents by email."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================
# REQUEST MODEL
# ============================================================

class EmailDeliveryRequest(BaseModel):
    recipient_email: str = Field(
        ...,
        min_length=5,
        max_length=254,
    )

    service: str = Field(
        ...,
        min_length=1,
        max_length=150,
    )

    document_title: str = Field(
        ...,
        min_length=1,
        max_length=250,
    )

    customer_name: str = Field(
        default="Valued Customer",
        max_length=150,
    )


# ============================================================
# GENERAL HELPERS
# ============================================================

def configuration_status():
    gmail_address = os.getenv(
        "GMAIL_ADDRESS", ""
    ).strip()

    gmail_password = os.getenv(
        "GMAIL_APP_PASSWORD", ""
    ).strip().replace(" ", "")

    return {
        "gmail_configured": bool(
            gmail_address and gmail_password
        ),
        "gmail_address_configured": bool(
            gmail_address
        ),
        "gmail_app_password_configured": bool(
            gmail_password
        ),
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "security": "STARTTLS",
        "document_directory_configured": (
            DOCUMENTS_ROOT.is_dir()
        ),
    }


def normalize_text(value):
    """
    Normalize titles and service names for comparison.

    This does not invent a title or modify the document.
    """

    value = str(value or "").strip()

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    value = value.replace("_", " ")

    return value.casefold().strip()


def safe_filename(value):
    """
    Produce a safe attachment filename.
    """

    name = Path(
        str(value or "")
    ).name

    name = re.sub(
        r"[\r\n\x00]",
        "",
        name,
    )

    name = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        name,
    )

    return name or "NPBC_Document"


def escape_html(value):
    """
    Escape customer-provided values before HTML insertion.
    """

    value = str(value or "")

    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def valid_email_address(address):
    address = str(address or "").strip()

    if "\r" in address or "\n" in address:
        return False

    return bool(
        re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            address,
        )
    )


# ============================================================
# SAVED DOCUMENT LOOKUP
# ============================================================

def find_saved_document(service, document_title):
    """
    Find an existing saved document.

    Supported layouts include:

        downloads/
            Seminar Paper/
                Why Nigeria Is Poor in the Midst of Abundance/
                    document.docx

    And:

        downloads/
            Seminar Paper/
                Why Nigeria Is Poor in the Midst of Abundance.docx

    The search requires an exact normalized service/title
    match. It will not select an arbitrary document.

    If your NPBC documents are stored elsewhere, configure
    NPBC_DOCUMENTS_ROOT on Render.
    """

    if not service or not document_title:
        raise HTTPException(
            status_code=400,
            detail=(
                "Both service and document_title "
                "are required."
            ),
        )

    if not DOCUMENTS_ROOT.is_dir():
        logger.error(
            "Saved-document directory does not exist: %s",
            DOCUMENTS_ROOT,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "The saved-document directory is not "
                "configured correctly. Set "
                "NPBC_DOCUMENTS_ROOT to the directory "
                "containing the existing NPBC documents."
            ),
        )

    requested_service = normalize_text(service)
    requested_title = normalize_text(document_title)

    matches = []

    try:
        for path in DOCUMENTS_ROOT.rglob("*"):

            if not path.is_file():
                continue

            if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            try:
                resolved_path = path.resolve()
                resolved_root = DOCUMENTS_ROOT.resolve()

                if not resolved_path.is_relative_to(
                    resolved_root
                ):
                    continue

            except (OSError, ValueError):
                continue

            relative_parts = path.relative_to(
                DOCUMENTS_ROOT
            ).parts

            normalized_parts = [
                normalize_text(part)
                for part in relative_parts
            ]

            service_matches = (
                requested_service in normalized_parts
            )

            title_matches = (
                requested_title in normalized_parts
            )

            # Also allow the document filename itself
            # to identify the requested title.
            filename_title = normalize_text(
                path.stem
            )

            filename_matches = (
                filename_title == requested_title
            )

            if service_matches and (
                title_matches or filename_matches
            ):
                matches.append(path)

    except OSError:
        logger.exception(
            "Unable to search saved documents."
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to search the saved-document "
                "directory."
            ),
        )

    # Remove duplicate paths while preserving order.
    unique_matches = list(
        dict.fromkeys(matches)
    )

    if not unique_matches:
        logger.warning(
            "Document not found. Service=%r Title=%r Root=%s",
            service,
            document_title,
            DOCUMENTS_ROOT,
        )

        raise HTTPException(
            status_code=404,
            detail=(
                "The saved document could not be found. "
                "Check the document title, service name, "
                "and NPBC_DOCUMENTS_ROOT configuration."
            ),
        )

    if len(unique_matches) > 1:
        logger.error(
            "Multiple documents match Service=%r Title=%r",
            service,
            document_title,
        )

        raise HTTPException(
            status_code=409,
            detail=(
                "Multiple saved documents match this "
                "service and title. Delivery stopped "
                "to avoid sending the wrong document."
            ),
        )

    selected_path = unique_matches[0]

    if not selected_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="The saved document no longer exists.",
        )

    try:
        file_size = selected_path.stat().st_size
    except OSError:
        raise HTTPException(
            status_code=500,
            detail="Unable to inspect the saved document.",
        )

    if file_size <= 0:
        raise HTTPException(
            status_code=422,
            detail="The saved document is empty.",
        )

    if file_size > MAX_ATTACHMENT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                "The document exceeds the 25 MB "
                "email attachment limit."
            ),
        )

    logger.info(
        "Saved document located: %s",
        selected_path,
    )

    return selected_path


# ============================================================
# EMAIL CONTENT
# ============================================================

def build_email_content(
    service,
    document_title,
    customer_name,
):
    service = str(service).strip()
    document_title = str(document_title).strip()

    customer_name = (
        str(customer_name or "").strip()
        or "Valued Customer"
    )

    plain_text = (
        f"Hello {customer_name},\n\n"
        "Your document from Naija Pocket Business Center "
        "is ready.\n\n"
        f"Service: {service}\n"
        f"Document: {document_title}\n\n"
        "Your document is attached to this email.\n\n"
        "Thank you for choosing Naija Pocket Business Center.\n\n"
        "Fast • Convenient • Open 24/7\n"
        "Naija Pocket Business Center"
    )

    safe_customer = escape_html(customer_name)
    safe_service = escape_html(service)
    safe_title = escape_html(document_title)

    html_text = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width">
</head>

<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#f4f4f4;">
<tr>
<td align="center" style="padding:24px 10px;">

<table width="100%" cellpadding="0" cellspacing="0"
       style="max-width:600px;background:#ffffff;border:1px solid #e5e5e5;">

<tr>
<td align="center"
    style="background:#111111;padding:28px 20px;">

<p style="margin:0;color:#d4af37;font-size:23px;font-weight:bold;">
Naija Pocket Business Center
</p>

<p style="margin:10px 0 0;color:#ffffff;font-size:13px;">
Fast • Convenient • Open 24/7
</p>

</td>
</tr>

<tr>
<td style="padding:28px 24px;color:#333333;">

<p style="margin:0 0 18px;font-size:16px;">
Hello {safe_customer},
</p>

<p style="font-size:15px;line-height:1.7;">
Your document from Naija Pocket Business Center is ready.
Please find your document attached to this email.
</p>

<table width="100%" cellpadding="0" cellspacing="0"
       style="background:#faf8f0;border:1px solid #eadca7;">

<tr>
<td style="padding:16px;">

<p style="margin:0 0 10px;font-size:14px;">
<strong>Service:</strong> {safe_service}
</p>

<p style="margin:0;font-size:14px;">
<strong>Document:</strong> {safe_title}
</p>

</td>
</tr>
</table>

<p style="margin:22px 0 0;font-size:14px;line-height:1.6;">
Your saved document is attached to this email.
</p>

<p style="margin:24px 0 0;font-size:14px;">
Thank you for choosing Naija Pocket Business Center.
</p>

</td>
</tr>

<tr>
<td align="center"
    style="background:#111111;padding:18px 15px;">

<p style="margin:0;color:#d4af37;font-size:13px;">
Naija Pocket Business Center
</p>

<p style="margin:8px 0 0;color:#ffffff;font-size:12px;">
Fast • Convenient • Open 24/7
</p>

</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>"""

    return plain_text, html_text


# ============================================================
# GMAIL SMTP DELIVERY
# ============================================================

def send_document_email(
    recipient_email,
    document_path,
    service,
    document_title,
    customer_name="Valued Customer",
):
    recipient_email = str(
        recipient_email or ""
    ).strip()

    if not valid_email_address(recipient_email):
        raise HTTPException(
            status_code=400,
            detail="Please provide a valid recipient email address.",
        )

    gmail_address = os.getenv(
        "GMAIL_ADDRESS", ""
    ).strip()

    gmail_password = os.getenv(
        "GMAIL_APP_PASSWORD", ""
    ).strip().replace(" ", "")

    if not gmail_address or not gmail_password:
        logger.error(
            "Gmail SMTP credentials are not configured."
        )

        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail delivery is not configured. "
                "Check GMAIL_ADDRESS and "
                "GMAIL_APP_PASSWORD in Render."
            ),
        )

    path = Path(document_path)

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="The saved document file could not be found.",
        )

    extension = path.suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="The saved document has an unsupported file type.",
        )

    try:
        file_size = path.stat().st_size

        if file_size <= 0:
            raise HTTPException(
                status_code=422,
                detail="The saved document is empty.",
            )

        if file_size > MAX_ATTACHMENT_SIZE:
            raise HTTPException(
                status_code=413,
                detail=(
                    "The saved document exceeds the "
                    "25 MB attachment limit."
                ),
            )

        with path.open("rb") as document_file:
            attachment_data = document_file.read(
                MAX_ATTACHMENT_SIZE + 1
            )

        if len(attachment_data) > MAX_ATTACHMENT_SIZE:
            raise HTTPException(
                status_code=413,
                detail="The attachment exceeds the 25 MB limit.",
            )

        filename = safe_filename(path.name)

        mime_type, _ = mimetypes.guess_type(
            filename
        )

        if mime_type and "/" in mime_type:
            maintype, subtype = mime_type.split(
                "/", 1
            )
        else:
            maintype, subtype = (
                "application",
                "octet-stream",
            )

        message = EmailMessage()

        message["From"] = formataddr(
            (
                "Naija Pocket Business Center",
                gmail_address,
            )
        )

        message["To"] = recipient_email
        message["Subject"] = DEFAULT_SUBJECT

        plain_text, html_text = build_email_content(
            service=service,
            document_title=document_title,
            customer_name=customer_name,
        )

        message.set_content(plain_text)

        message.add_alternative(
            html_text,
            subtype="html",
        )

        message.add_attachment(
            attachment_data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

        ssl_context = ssl.create_default_context()

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=30,
        ) as smtp:

            smtp.ehlo()

            smtp.starttls(
                context=ssl_context
            )

            smtp.ehlo()

            smtp.login(
                gmail_address,
                gmail_password,
            )

            smtp.send_message(message)

        logger.info(
            "Document email sent successfully. "
            "Recipient=%s Service=%s Title=%s Filename=%s",
            recipient_email,
            service,
            document_title,
            filename,
        )

        return {
            "ok": True,
            "message": "Document email sent successfully.",
            "recipient": recipient_email,
            "filename": filename,
            "service": service,
            "document_title": document_title,
        }

    except HTTPException:
        raise

    except smtplib.SMTPAuthenticationError:
        logger.exception(
            "Gmail SMTP authentication failed."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail authentication failed. "
                "Check GMAIL_ADDRESS and "
                "GMAIL_APP_PASSWORD in Render."
            ),
        )

    except smtplib.SMTPRecipientsRefused:
        logger.exception(
            "Gmail refused the recipient address."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail refused the recipient address. "
                "Check the customer's email address."
            ),
        )

    except (
        smtplib.SMTPException,
        OSError,
        ssl.SSLError,
    ):
        logger.exception(
            "Gmail SMTP delivery failed."
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Gmail could not complete email delivery. "
                "Check the Render logs."
            ),
        )


# ============================================================
# API STATUS
# ============================================================

@app.get("/")
def home():
    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
        "purpose": "Gmail SMTP document email delivery only",
        "configuration": configuration_status(),
        "endpoints": {
            "health": "/health",
            "config": "/config",
            "send_document_email": "POST /api/delivery/email",
        },
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": APP_NAME,
        "status": "running",
    }


@app.get("/config")
def config():
    return configuration_status()


# ============================================================
# SEND SAVED DOCUMENT BY EMAIL
# ============================================================

@app.post("/api/delivery/email")
def deliver_document_by_email(
    request: EmailDeliveryRequest,
):
    """
    Receive the JSON request from Review.

    Locate the existing saved document, then send that exact
    file as an attachment through Gmail SMTP.
    """

    recipient_email = request.recipient_email.strip()
    service = request.service.strip()
    document_title = request.document_title.strip()

    customer_name = (
        request.customer_name.strip()
        or "Valued Customer"
    )

    if not valid_email_address(recipient_email):
        raise HTTPException(
            status_code=400,
            detail="A valid recipient email address is required.",
        )

    if not service:
        raise HTTPException(
            status_code=400,
            detail="The service name is required.",
        )

    if not document_title:
        raise HTTPException(
            status_code=400,
            detail="The document title is required.",
        )

    logger.info(
        "Email delivery requested. Service=%r Title=%r",
        service,
        document_title,
    )

    # Locate the existing saved file.
    document_path = find_saved_document(
        service=service,
        document_title=document_title,
    )

    # Attach and send the existing file.
    result = send_document_email(
        recipient_email=recipient_email,
        document_path=document_path,
        service=service,
        document_title=document_title,
        customer_name=customer_name,
    )

    return result
