"""
Naija Pocket Business Center (NPBC)
Complete Gmail SMTP Email Delivery Module

FILE NAME:
    email_delivery.py

PURPOSE:
    Send an existing, saved NPBC document directly to a customer's email.

EMAIL PROVIDER:
    Gmail SMTP

REQUIRED RENDER ENVIRONMENT VARIABLES:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

SMTP SETTINGS:
    Host: smtp.gmail.com
    Port: 587
    Security: STARTTLS

SUPPORTED ATTACHMENTS:
    DOCX
    PDF
    XLSX
    XLS
    PPTX
    PPT
    TXT
    CSV
    RTF
    ODT
    ODS
    ZIP

IMPORTANT:
    - This module handles email delivery only.
    - It does not handle payment processing.
    - It does not require payment confirmation.
    - It does not regenerate documents.
    - It does not create missing documents.
    - It attaches the actual existing file supplied to it.
    - If the file does not exist, delivery fails safely.
    - Both plain-text and HTML email alternatives are included.
    - The HTML email uses email-client-compatible tables.
    - Uses Python's standard library; no third-party package is required.

PUBLIC FUNCTIONS:
    configuration_status()
    send_document_email()
    send_email()

COMPATIBILITY:
    Supports calls using:
        recipient_email
        document_path
        service
        document_title
        customer_name
        subject
"""

from __future__ import annotations

import html
import mimetypes
import os
import re
import smtplib
import socket
import ssl
import time

from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any, Dict, Optional


# ============================================================
# CONFIGURATION
# ============================================================

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 30

GMAIL_ADDRESS_ENV = "GMAIL_ADDRESS"
GMAIL_APP_PASSWORD_ENV = "GMAIL_APP_PASSWORD"

BRAND_NAME = "Naija Pocket Business Center"

BRAND_GOLD = "#D4AF37"
BRAND_BLACK = "#080808"
BRAND_WHITE = "#FFFFFF"
BRAND_MUTED = "#666666"

MAX_ATTACHMENT_SIZE_MB = 25
MAX_ATTACHMENT_SIZE_BYTES = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024

DEFAULT_SUBJECT = "Your Document from Naija Pocket Business Center"

SUPPORTED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".xls",
    ".pptx",
    ".ppt",
    ".txt",
    ".csv",
    ".rtf",
    ".odt",
    ".ods",
    ".zip",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def _clean_text(value: Any, default: str = "") -> str:
    """
    Convert a value into safe, trimmed text.
    """
    if value is None:
        return default

    try:
        result = str(value).strip()
    except Exception:
        return default

    return result if result else default


def _clean_header(value: Any, default: str = "") -> str:
    """
    Prevent newline characters from entering email headers.
    """
    value = _clean_text(value, default)
    value = value.replace("\r", " ").replace("\n", " ")
    return " ".join(value.split())


def _safe_html(value: Any) -> str:
    """
    Escape text before inserting it into HTML email content.
    """
    return html.escape(_clean_text(value), quote=True)


def _valid_email_address(address: str) -> bool:
    """
    Perform practical email-address validation without requiring
    the third-party email-validator package.
    """
    address = _clean_header(address)

    if not address or len(address) > 254:
        return False

    if any(character.isspace() for character in address):
        return False

    parsed_name, parsed_address = parseaddr(address)

    if parsed_name:
        return False

    if parsed_address.lower() != address.lower():
        return False

    if address.count("@") != 1:
        return False

    local_part, domain = address.rsplit("@", 1)

    if not local_part or not domain:
        return False

    if len(local_part) > 64:
        return False

    if domain.startswith(".") or domain.endswith("."):
        return False

    if ".." in address:
        return False

    if "." not in domain:
        return False

    if domain.startswith("-") or domain.endswith("-"):
        return False

    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",
        local_part,
    ):
        return False

    domain_labels = domain.split(".")

    for label in domain_labels:
        if not label:
            return False

        if label.startswith("-") or label.endswith("-"):
            return False

        if not re.fullmatch(r"[A-Za-z0-9-]+", label):
            return False

    return True


def _get_smtp_credentials() -> tuple[str, str]:
    """
    Read Gmail SMTP credentials from environment variables.
    """
    gmail_address = _clean_text(
        os.getenv(GMAIL_ADDRESS_ENV)
    )

    app_password = _clean_text(
        os.getenv(GMAIL_APP_PASSWORD_ENV)
    )

    if not gmail_address:
        raise RuntimeError(
            "GMAIL_ADDRESS is missing. "
            "Add your Gmail address to the Render environment variables."
        )

    if not app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing. "
            "Add your Google app password to the Render environment variables."
        )

    if not _valid_email_address(gmail_address):
        raise RuntimeError(
            "GMAIL_ADDRESS is not a valid email address."
        )

    # Google app passwords are commonly displayed with spaces.
    # Remove spaces so either display format works.
    app_password = re.sub(r"\s+", "", app_password)

    if not app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is empty."
        )

    return gmail_address, app_password


# ============================================================
# CONFIGURATION STATUS
# ============================================================

def configuration_status() -> Dict[str, Any]:
    """
    Report whether Gmail SMTP configuration is present.

    This function does not send an email and does not expose
    the Gmail app password.
    """
    gmail_address = _clean_text(
        os.getenv(GMAIL_ADDRESS_ENV)
    )

    app_password = _clean_text(
        os.getenv(GMAIL_APP_PASSWORD_ENV)
    )

    address_valid = _valid_email_address(gmail_address)

    configured = bool(
        address_valid
        and app_password
    )

    return {
        "configured": configured,
        "provider": "Gmail SMTP",
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "security": "STARTTLS",
        "gmail_address": gmail_address if address_valid else "",
        "gmail_address_configured": bool(gmail_address),
        "app_password_configured": bool(app_password),
        "ready": configured,
        "message": (
            "Gmail SMTP is configured."
            if configured
            else (
                "Gmail SMTP is not fully configured. "
                "Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                "in the Render environment variables."
            )
        ),
    }


# ============================================================
# DOCUMENT VALIDATION
# ============================================================

def _resolve_existing_document(document_path: Any) -> Path:
    """
    Resolve an existing document without creating, regenerating,
    modifying, or replacing it.
    """
    if document_path is None:
        raise ValueError(
            "A saved document path is required for email delivery."
        )

    try:
        path = Path(str(document_path)).expanduser().resolve()
    except Exception as exc:
        raise ValueError(
            "The supplied document path is invalid."
        ) from exc

    if not path.exists():
        raise FileNotFoundError(
            "The saved document could not be found. "
            "No document was generated or recreated."
        )

    if not path.is_file():
        raise ValueError(
            "The supplied document path does not point to a file."
        )

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            "Unsupported attachment format: "
            f"{path.suffix or '(no file extension)'}. "
            "Please provide a supported saved document."
        )

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise OSError(
            "The saved document could not be accessed."
        ) from exc

    if file_size <= 0:
        raise ValueError(
            "The saved document is empty and cannot be emailed."
        )

    if file_size > MAX_ATTACHMENT_SIZE_BYTES:
        raise ValueError(
            "The saved document exceeds the "
            f"{MAX_ATTACHMENT_SIZE_MB} MB attachment limit."
        )

    if not os.access(str(path), os.R_OK):
        raise PermissionError(
            "The saved document cannot be read by the application."
        )

    return path


def _get_attachment_mime_type(path: Path) -> tuple[str, str]:
    """
    Determine the MIME type of the existing attachment.
    """
    extension = path.suffix.lower()

    known_types = {
        ".docx": (
            "application",
            "vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ".pdf": (
            "application",
            "pdf",
        ),
        ".xlsx": (
            "application",
            "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ".xls": (
            "application",
            "vnd.ms-excel",
        ),
        ".pptx": (
            "application",
            "vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ".ppt": (
            "application",
            "vnd.ms-powerpoint",
        ),
        ".txt": (
            "text",
            "plain",
        ),
        ".csv": (
            "text",
            "csv",
        ),
        ".rtf": (
            "application",
            "rtf",
        ),
        ".odt": (
            "application",
            "vnd.oasis.opendocument.text",
        ),
        ".ods": (
            "application",
            "vnd.oasis.opendocument.spreadsheet",
        ),
        ".zip": (
            "application",
            "zip",
        ),
    }

    if extension in known_types:
        return known_types[extension]

    guessed_type, _encoding = mimetypes.guess_type(
        str(path)
    )

    if guessed_type and "/" in guessed_type:
        main_type, sub_type = guessed_type.split("/", 1)
        return main_type, sub_type

    return "application", "octet-stream"


# ============================================================
# EMAIL CONTENT
# ============================================================

def _build_plain_text(
    customer_name: str,
    service: str,
    document_title: str,
) -> str:
    """
    Build a plain-text version of the email.
    """
    greeting = (
        f"Dear {customer_name},"
        if customer_name
        else "Hello,"
    )

    lines = [
        greeting,
        "",
        "Your document from Naija Pocket Business Center "
        "is attached to this email.",
        "",
        f"Service: {service}",
        f"Document title: {document_title}",
        "",
        "Please open the attachment to access your document.",
        "",
        "If you need assistance, please contact NPBC Customer Care.",
        "",
        "Thank you for choosing Naija Pocket Business Center.",
        "",
        "Fast • Convenient • Open 24/7",
        "",
        "Naija Pocket Business Center",
    ]

    return "\n".join(lines)


def _build_html_email(
    customer_name: str,
    service: str,
    document_title: str,
) -> str:
    """
    Build a Gmail-compatible HTML email using table-based layout.
    """
    safe_customer_name = _safe_html(customer_name)
    safe_service = _safe_html(service)
    safe_document_title = _safe_html(document_title)

    if safe_customer_name:
        greeting = f"Dear {safe_customer_name},"
    else:
        greeting = "Hello,"

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your NPBC Document</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f2f2;font-family:Arial,Helvetica,sans-serif;color:#202020;">

<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="width:100%;background-color:#f2f2f2;">
<tr>
<td align="center" style="padding:24px 10px;">

<table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background-color:#ffffff;border:1px solid #e5e5e5;">

<tr>
<td align="center" style="padding:28px 20px;background-color:#080808;border-bottom:4px solid #D4AF37;">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
<tr>
<td align="center" style="font-family:Arial,Helvetica,sans-serif;font-size:24px;line-height:32px;font-weight:bold;color:#D4AF37;">
Naija Pocket Business Center
</td>
</tr>
<tr>
<td align="center" style="padding-top:8px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:20px;color:#ffffff;">
Fast &bull; Convenient &bull; Open 24/7
</td>
</tr>
</table>
</td>
</tr>

<tr>
<td style="padding:30px 24px 12px 24px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:25px;color:#202020;">

<p style="margin:0 0 18px 0;">{greeting}</p>

<p style="margin:0 0 18px 0;">
Your document from <strong>Naija Pocket Business Center</strong>
is attached to this email.
</p>

<p style="margin:0 0 18px 0;">
Please open the attachment to access your document.
</p>

</td>
</tr>

<tr>
<td style="padding:0 24px 26px 24px;">

<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="width:100%;border:1px solid #e5e5e5;">

<tr>
<td colspan="2" style="padding:14px 16px;background-color:#080808;color:#D4AF37;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:22px;font-weight:bold;">
Document Details
</td>
</tr>

<tr>
<td width="34%" valign="top" style="padding:12px 10px 12px 16px;border-bottom:1px solid #eeeeee;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:21px;font-weight:bold;color:#555555;">
Service
</td>
<td width="66%" valign="top" style="padding:12px 16px 12px 10px;border-bottom:1px solid #eeeeee;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:21px;color:#202020;word-break:break-word;">
{safe_service}
</td>
</tr>

<tr>
<td width="34%" valign="top" style="padding:12px 10px 12px 16px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:21px;font-weight:bold;color:#555555;">
Document Title
</td>
<td width="66%" valign="top" style="padding:12px 16px 12px 10px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:21px;color:#202020;word-break:break-word;">
{safe_document_title}
</td>
</tr>

</table>

</td>
</tr>

<tr>
<td style="padding:0 24px 28px 24px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:23px;color:#444444;">

<p style="margin:0 0 14px 0;">
If you need assistance, please contact NPBC Customer Care.
</p>

<p style="margin:0;">
Thank you for choosing Naija Pocket Business Center.
</p>

</td>
</tr>

<tr>
<td align="center" style="padding:20px 16px;background-color:#080808;border-top:3px solid #D4AF37;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:20px;color:#ffffff;">
<strong style="color:#D4AF37;">Naija Pocket Business Center</strong>
<br>
Fast &bull; Convenient &bull; Open 24/7
</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""


# ============================================================
# EMAIL MESSAGE CREATION
# ============================================================

def _build_email_message(
    *,
    sender_email: str,
    recipient_email: str,
    document_path: Path,
    service: str,
    document_title: str,
    customer_name: str,
    subject: str,
) -> EmailMessage:
    """
    Build an email with plain-text and HTML alternatives and
    attach the exact existing document.
    """
    message = EmailMessage()

    message["From"] = formataddr(
        (BRAND_NAME, sender_email)
    )

    message["To"] = recipient_email

    message["Subject"] = _clean_header(
        subject,
        DEFAULT_SUBJECT,
    )

    message["X-Mailer"] = (
        "Naija Pocket Business Center Email Delivery"
    )

    plain_text = _build_plain_text(
        customer_name=customer_name,
        service=service,
        document_title=document_title,
    )

    html_content = _build_html_email(
        customer_name=customer_name,
        service=service,
        document_title=document_title,
    )

    message.set_content(plain_text)

    message.add_alternative(
        html_content,
        subtype="html",
    )

    # Read and attach the exact existing file.
    # No document regeneration or replacement occurs here.
    with document_path.open("rb") as document_file:
        attachment_bytes = document_file.read()

    if not attachment_bytes:
        raise ValueError(
            "The saved document is empty and cannot be emailed."
        )

    main_type, sub_type = _get_attachment_mime_type(
        document_path
    )

    message.add_attachment(
        attachment_bytes,
        maintype=main_type,
        subtype=sub_type,
        filename=document_path.name,
    )

    return message


# ============================================================
# SMTP SENDING
# ============================================================

def _send_via_gmail(
    *,
    sender_email: str,
    app_password: str,
    recipient_email: str,
    message: EmailMessage,
) -> None:
    """
    Send an email through Gmail SMTP using STARTTLS.
    """
    context = ssl.create_default_context()

    with smtplib.SMTP(
        SMTP_HOST,
        SMTP_PORT,
        timeout=SMTP_TIMEOUT,
    ) as smtp:
        smtp.ehlo()

        smtp.starttls(context=context)

        smtp.ehlo()

        smtp.login(
            sender_email,
            app_password,
        )

        refused_recipients = smtp.send_message(
            message,
            from_addr=sender_email,
            to_addrs=[recipient_email],
        )

        if refused_recipients:
            raise smtplib.SMTPRecipientsRefused(
                refused_recipients
            )


# ============================================================
# PUBLIC DOCUMENT DELIVERY FUNCTION
# ============================================================

def send_document_email(
    recipient_email: str = "",
    document_path: Any = None,
    service: str = "",
    document_title: str = "",
    customer_name: str = "",
    subject: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Email an existing NPBC document to a customer.

    Compatible call:
        send_document_email(
            recipient_email="customer@example.com",
            document_path="/path/to/saved/document.docx",
            service="Seminar Paper",
            document_title="My Document",
            customer_name="Customer",
            subject="Your NPBC Document",
        )

    Alternative recipient keywords accepted:
        customer_email
        email
        to_email

    Alternative file keywords accepted:
        file_path
        attachment_path
        saved_document_path

    Alternative title keyword accepted:
        title

    Returns a dictionary containing:
        ok
        sent
        message
        recipient_email
        service
        document_title
        attachment
    """

    # --------------------------------------------------------
    # Accept common keyword variations.
    # --------------------------------------------------------

    if not recipient_email:
        recipient_email = (
            kwargs.get("customer_email")
            or kwargs.get("email")
            or kwargs.get("to_email")
            or ""
        )

    if document_path is None:
        document_path = (
            kwargs.get("file_path")
            or kwargs.get("attachment_path")
            or kwargs.get("saved_document_path")
        )

    if not document_title:
        document_title = (
            kwargs.get("title")
            or kwargs.get("document_name")
            or ""
        )

    recipient_email = _clean_header(
        recipient_email
    )

    service = _clean_text(
        service,
        "Document Service",
    )

    document_title = _clean_text(
        document_title,
    )

    customer_name = _clean_header(
        customer_name
    )

    subject = _clean_header(
        subject,
        DEFAULT_SUBJECT,
    )

    # --------------------------------------------------------
    # Validate recipient.
    # --------------------------------------------------------

    if not _valid_email_address(recipient_email):
        return {
            "ok": False,
            "sent": False,
            "message": (
                "A valid customer email address is required."
            ),
            "error": "invalid_recipient_email",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    # --------------------------------------------------------
    # Validate title.
    # --------------------------------------------------------

    if not document_title:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "The document title is required for email delivery."
            ),
            "error": "missing_document_title",
            "recipient_email": recipient_email,
            "service": service,
        }

    # --------------------------------------------------------
    # Validate actual saved document.
    # --------------------------------------------------------

    try:
        saved_document = _resolve_existing_document(
            document_path
        )

    except FileNotFoundError as exc:
        return {
            "ok": False,
            "sent": False,
            "message": str(exc),
            "error": "document_not_found",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except PermissionError as exc:
        return {
            "ok": False,
            "sent": False,
            "message": str(exc),
            "error": "document_access_denied",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except (ValueError, OSError) as exc:
        return {
            "ok": False,
            "sent": False,
            "message": str(exc),
            "error": "invalid_document",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    # --------------------------------------------------------
    # Read Gmail SMTP credentials.
    # --------------------------------------------------------

    try:
        sender_email, app_password = (
            _get_smtp_credentials()
        )

    except RuntimeError as exc:
        return {
            "ok": False,
            "sent": False,
            "message": str(exc),
            "error": "smtp_not_configured",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    # --------------------------------------------------------
    # Build the email and attach the existing document.
    # --------------------------------------------------------

    try:
        message = _build_email_message(
            sender_email=sender_email,
            recipient_email=recipient_email,
            document_path=saved_document,
            service=service,
            document_title=document_title,
            customer_name=customer_name,
            subject=subject,
        )

    except FileNotFoundError:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "The saved document disappeared before it could "
                "be attached. No replacement document was created."
            ),
            "error": "document_not_found",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except Exception as exc:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "The email could not be prepared. "
                "Check that the saved document is accessible."
            ),
            "error": "email_preparation_failed",
            "details": str(exc),
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    # --------------------------------------------------------
    # Send through Gmail SMTP.
    # --------------------------------------------------------

    try:
        _send_via_gmail(
            sender_email=sender_email,
            app_password=app_password,
            recipient_email=recipient_email,
            message=message,
        )

        return {
            "ok": True,
            "sent": True,
            "message": (
                "The document email was accepted by Gmail SMTP."
            ),
            "provider": "Gmail SMTP",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
            "attachment": saved_document.name,
            "attachment_path": str(saved_document),
        }

    except smtplib.SMTPAuthenticationError:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "Gmail rejected the login. Check GMAIL_ADDRESS "
                "and GMAIL_APP_PASSWORD in the Render environment "
                "variables. Use a Google app password, not your "
                "normal Google account password."
            ),
            "error": "smtp_authentication_failed",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except smtplib.SMTPRecipientsRefused:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "Gmail refused the recipient address. "
                "Check the customer's email address."
            ),
            "error": "recipient_refused",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except smtplib.SMTPConnectError:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "Could not establish a connection to Gmail SMTP."
            ),
            "error": "smtp_connection_failed",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except smtplib.SMTPServerDisconnected:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "Gmail disconnected before confirming email delivery."
            ),
            "error": "smtp_disconnected",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except smtplib.SMTPException as exc:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "Gmail SMTP could not send the document email."
            ),
            "error": "smtp_error",
            "details": str(exc),
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except (socket.timeout, TimeoutError):
        return {
            "ok": False,
            "sent": False,
            "message": (
                "The connection to Gmail timed out."
            ),
            "error": "smtp_timeout",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except OSError as exc:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "A network or system error prevented email delivery."
            ),
            "error": "network_error",
            "details": str(exc),
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }

    except Exception as exc:
        return {
            "ok": False,
            "sent": False,
            "message": (
                "An unexpected error occurred during email delivery."
            ),
            "error": "unexpected_delivery_error",
            "details": str(exc),
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
        }


# ============================================================
# COMPATIBILITY ALIAS
# ============================================================

def send_email(
    recipient_email: str = "",
    document_path: Any = None,
    service: str = "",
    document_title: str = "",
    customer_name: str = "",
    subject: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Compatibility alias for applications that call send_email().
    """
    return send_document_email(
        recipient_email=recipient_email,
        document_path=document_path,
        service=service,
        document_title=document_title,
        customer_name=customer_name,
        subject=subject,
        **kwargs,
    )


# ============================================================
# SIMPLE MODULE TEST
# ============================================================

if __name__ == "__main__":
    status = configuration_status()

    print("NPBC Gmail SMTP Configuration")
    print("--------------------------------")

    for key, value in status.items():
        print(f"{key}: {value}")

    print()

    if status["configured"]:
        print(
            "Gmail SMTP credentials are present. "
            "This check does not send an email."
        )
    else:
        print(
            "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "before sending document emails."
        )
```0
