"""
Naija Pocket Business Center (NPBC)
Complete Gmail SMTP Email Delivery Module

FILE NAME:
    email_delivery.py

PURPOSE:
    Send an existing, saved NPBC document directly to a customer's email.

EMAIL PROVIDER:
    Gmail SMTP

RENDER ENVIRONMENT VARIABLES:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

SMTP:
    Host: smtp.gmail.com
    Port: 587
    Security: STARTTLS

IMPORTANT:
    - Email delivery does not require payment confirmation.
    - Does not regenerate documents.
    - Does not create missing documents.
    - Attaches the exact existing document supplied to it.
    - Uses Python standard library only.
    - Supports multiple argument names for compatibility with payment_api.py.
    - Provides detailed configuration and SMTP error messages.
"""

from __future__ import annotations

import html
import mimetypes
import os
import re
import smtplib
import socket
import ssl

from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any, Dict


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
# GENERAL HELPERS
# ============================================================

def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default

    try:
        result = str(value).strip()
    except Exception:
        return default

    return result if result else default


def _clean_header(value: Any, default: str = "") -> str:
    value = _clean_text(value, default)
    value = value.replace("\r", " ").replace("\n", " ")
    return " ".join(value.split())


def _safe_html(value: Any) -> str:
    return html.escape(_clean_text(value), quote=True)


def _valid_email_address(address: str) -> bool:
    address = _clean_header(address)

    if not address or len(address) > 254:
        return False

    if any(character.isspace() for character in address):
        return False

    parsed_name, parsed_address = parseaddr(address)

    if parsed_name or parsed_address.lower() != address.lower():
        return False

    if address.count("@") != 1 or ".." in address:
        return False

    local_part, domain = address.rsplit("@", 1)

    if not local_part or not domain or len(local_part) > 64:
        return False

    if domain.startswith(".") or domain.endswith("."):
        return False

    if "." not in domain:
        return False

    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",
        local_part,
    ):
        return False

    for label in domain.split("."):
        if (
            not label
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[A-Za-z0-9-]+", label)
        ):
            return False

    return True


# ============================================================
# GMAIL CONFIGURATION
# ============================================================

def _get_smtp_credentials() -> tuple[str, str]:
    """
    Read the current environment variables directly.

    Render environment variables are read when this function runs,
    rather than being cached when the module is first imported.
    """

    gmail_address = _clean_text(
        os.environ.get(GMAIL_ADDRESS_ENV)
    )

    app_password = _clean_text(
        os.environ.get(GMAIL_APP_PASSWORD_ENV)
    )

    if not gmail_address:
        raise RuntimeError(
            "GMAIL_ADDRESS is missing or empty in the running application. "
            "Check the Render environment variable name and redeploy."
        )

    if not app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing or empty in the running "
            "application. Check the Render environment variable name "
            "and redeploy."
        )

    if not _valid_email_address(gmail_address):
        raise RuntimeError(
            "GMAIL_ADDRESS was found, but its value is not a valid "
            "email address. Check the value configured on Render."
        )

    # Google may display an app password with spaces.
    # Remove spaces before authenticating with Gmail.
    app_password = re.sub(r"\s+", "", app_password)

    if not app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD contains no usable characters."
        )

    return gmail_address, app_password


def configuration_status() -> Dict[str, Any]:
    """
    Return the actual configuration state without exposing the password.
    Does not attempt to send an email or connect to Gmail.
    """

    try:
        gmail_address, _app_password = _get_smtp_credentials()

        return {
            "configured": True,
            "ready": True,
            "provider": "Gmail SMTP",
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "security": "STARTTLS",
            "gmail_address": gmail_address,
            "gmail_address_configured": True,
            "app_password_configured": True,
            "message": "Gmail SMTP credentials are present and valid in format.",
        }

    except RuntimeError as exc:
        gmail_address = _clean_text(
            os.environ.get(GMAIL_ADDRESS_ENV)
        )
        app_password = _clean_text(
            os.environ.get(GMAIL_APP_PASSWORD_ENV)
        )

        return {
            "configured": False,
            "ready": False,
            "provider": "Gmail SMTP",
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "security": "STARTTLS",
            "gmail_address": (
                gmail_address
                if _valid_email_address(gmail_address)
                else ""
            ),
            "gmail_address_configured": bool(gmail_address),
            "app_password_configured": bool(app_password),
            "message": str(exc),
        }


# Compatibility aliases for applications using other status names.

def get_configuration_status() -> Dict[str, Any]:
    return configuration_status()


def is_configured() -> bool:
    return bool(configuration_status()["configured"])


# ============================================================
# DOCUMENT VALIDATION
# ============================================================

def _resolve_existing_document(document_path: Any) -> Path:
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
            "No document was generated or recreated. "
            f"Checked path: {path}"
        )

    if not path.is_file():
        raise ValueError(
            "The supplied document path does not point to a file."
        )

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported attachment format: {path.suffix or '(no extension)'}. "
            "Supported formats include DOCX, PDF, XLSX, XLS, PPTX, PPT, "
            "TXT, CSV, RTF, ODT, ODS and ZIP."
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
            f"The document exceeds the {MAX_ATTACHMENT_SIZE_MB} MB "
            "attachment limit."
        )

    if not os.access(str(path), os.R_OK):
        raise PermissionError(
            "The saved document cannot be read by the application."
        )

    return path


def _get_attachment_mime_type(path: Path) -> tuple[str, str]:
    known_types = {
        ".docx": (
            "application",
            "vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ".pdf": ("application", "pdf"),
        ".xlsx": (
            "application",
            "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        ".xls": ("application", "vnd.ms-excel"),
        ".pptx": (
            "application",
            "vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ".ppt": ("application", "vnd.ms-powerpoint"),
        ".txt": ("text", "plain"),
        ".csv": ("text", "csv"),
        ".rtf": ("application", "rtf"),
        ".odt": ("application", "vnd.oasis.opendocument.text"),
        ".ods": ("application", "vnd.oasis.opendocument.spreadsheet"),
        ".zip": ("application", "zip"),
    }

    extension = path.suffix.lower()

    if extension in known_types:
        return known_types[extension]

    guessed_type, _encoding = mimetypes.guess_type(str(path))

    if guessed_type and "/" in guessed_type:
        return tuple(guessed_type.split("/", 1))

    return "application", "octet-stream"


# ============================================================
# EMAIL CONTENT
# ============================================================

def _build_plain_text(
    customer_name: str,
    service: str,
    document_title: str,
) -> str:

    greeting = (
        f"Dear {customer_name},"
        if customer_name
        else "Hello,"
    )

    return "\n".join([
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
    ])


def _build_html_email(
    customer_name: str,
    service: str,
    document_title: str,
) -> str:

    safe_name = _safe_html(customer_name)
    safe_service = _safe_html(service)
    safe_title = _safe_html(document_title)

    greeting = (
        f"Dear {safe_name},"
        if safe_name
        else "Hello,"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your NPBC Document</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f2f2;font-family:Arial,Helvetica,sans-serif;color:#202020;">

<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#f2f2f2;">
<tr>
<td align="center" style="padding:24px 10px;">

<table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background-color:#ffffff;border:1px solid #e5e5e5;">

<tr>
<td align="center" style="padding:28px 20px;background-color:#080808;border-bottom:4px solid #D4AF37;">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
<tr>
<td align="center" style="font-size:24px;line-height:32px;font-weight:bold;color:#D4AF37;">
Naija Pocket Business Center
</td>
</tr>
<tr>
<td align="center" style="padding-top:8px;font-size:13px;line-height:20px;color:#ffffff;">
Fast &bull; Convenient &bull; Open 24/7
</td>
</tr>
</table>
</td>
</tr>

<tr>
<td style="padding:30px 24px 12px;font-size:15px;line-height:25px;color:#202020;">
<p style="margin:0 0 18px;">{greeting}</p>
<p style="margin:0 0 18px;">
Your document from <strong>Naija Pocket Business Center</strong>
is attached to this email.
</p>
<p style="margin:0 0 18px;">
Please open the attachment to access your document.
</p>
</td>
</tr>

<tr>
<td style="padding:0 24px 26px;">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="border:1px solid #e5e5e5;">
<tr>
<td colspan="2" style="padding:14px 16px;background-color:#080808;color:#D4AF37;font-size:15px;line-height:22px;font-weight:bold;">
Document Details
</td>
</tr>
<tr>
<td width="34%" valign="top" style="padding:12px 10px 12px 16px;border-bottom:1px solid #eeeeee;font-size:13px;line-height:21px;font-weight:bold;color:#555555;">
Service
</td>
<td width="66%" valign="top" style="padding:12px 16px 12px 10px;border-bottom:1px solid #eeeeee;font-size:13px;line-height:21px;color:#202020;word-break:break-word;">
{safe_service}
</td>
</tr>
<tr>
<td width="34%" valign="top" style="padding:12px 10px 12px 16px;font-size:13px;line-height:21px;font-weight:bold;color:#555555;">
Document Title
</td>
<td width="66%" valign="top" style="padding:12px 16px 12px 10px;font-size:13px;line-height:21px;color:#202020;word-break:break-word;">
{safe_title}
</td>
</tr>
</table>
</td>
</tr>

<tr>
<td style="padding:0 24px 28px;font-size:14px;line-height:23px;color:#444444;">
<p style="margin:0 0 14px;">
If you need assistance, please contact NPBC Customer Care.
</p>
<p style="margin:0;">
Thank you for choosing Naija Pocket Business Center.
</p>
</td>
</tr>

<tr>
<td align="center" style="padding:20px 16px;background-color:#080808;border-top:3px solid #D4AF37;font-size:12px;line-height:20px;color:#ffffff;">
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
</html>"""


# ============================================================
# EMAIL MESSAGE
# ============================================================

def _build_email_message(
    sender_email: str,
    recipient_email: str,
    document_path: Path,
    service: str,
    document_title: str,
    customer_name: str,
    subject: str,
) -> EmailMessage:

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

    message.set_content(
        _build_plain_text(
            customer_name,
            service,
            document_title,
        )
    )

    message.add_alternative(
        _build_html_email(
            customer_name,
            service,
            document_title,
        ),
        subtype="html",
    )

    # Attach the existing document without changing its contents.
    with document_path.open("rb") as document_file:
        attachment_bytes = document_file.read()

    if not attachment_bytes:
        raise ValueError("The saved document is empty.")

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
# GMAIL SMTP TRANSPORT
# ============================================================

def _send_via_gmail(
    sender_email: str,
    app_password: str,
    recipient_email: str,
    message: EmailMessage,
) -> None:

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

        refused = smtp.send_message(
            message,
            from_addr=sender_email,
            to_addrs=[recipient_email],
        )

        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


# ============================================================
# RESULT HELPER
# ============================================================

def _failure(
    message: str,
    error: str,
    recipient_email: str = "",
    service: str = "",
    document_title: str = "",
    details: str = "",
) -> Dict[str, Any]:

    result = {
        "ok": False,
        "success": False,
        "sent": False,
        "configured": bool(configuration_status()["configured"]),
        "provider": "Gmail SMTP",
        "message": message,
        "error": error,
        "recipient_email": recipient_email,
        "service": service,
        "document_title": document_title,
    }

    if details:
        result["details"] = details

    return result


# ============================================================
# PUBLIC EMAIL DELIVERY FUNCTION
# ============================================================

def send_document_email(
    recipient_email: str = "",
    document_path: Any = None,
    service: str = "",
    document_title: str = "",
    customer_name: str = "",
    subject: str = "",
    file_path: Any = None,
    saved_path: Any = None,
    attachment_path: Any = None,
    file: Any = None,
    document_file: Any = None,
    saved_document_path: Any = None,
    customer_email: str = "",
    email: str = "",
    to_email: str = "",
    to: str = "",
    recipient: str = "",
    service_name: str = "",
    title: str = "",
    filename: str = "",
    file_name: str = "",
    name: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Send an existing saved document.

    Supports argument names commonly used by payment_api.py.

    Recipient aliases:
        recipient_email, customer_email, email, to_email, to, recipient

    Document path aliases:
        document_path, file_path, saved_path, attachment_path,
        file, document_file, saved_document_path

    Title aliases:
        document_title, title, filename, file_name

    Service aliases:
        service, service_name

    No payment confirmation is required by this function.
    """

    # --------------------------------------------------------
    # Resolve recipient
    # --------------------------------------------------------

    recipient_email = (
        recipient_email
        or customer_email
        or email
        or to_email
        or to
        or recipient
        or kwargs.get("recipient_email")
        or kwargs.get("customer_email")
        or kwargs.get("email")
        or kwargs.get("to_email")
        or kwargs.get("to")
        or kwargs.get("recipient")
        or ""
    )

    # --------------------------------------------------------
    # Resolve document path
    # --------------------------------------------------------

    document_path = (
        document_path
        or file_path
        or saved_path
        or attachment_path
        or file
        or document_file
        or saved_document_path
        or kwargs.get("document_path")
        or kwargs.get("file_path")
        or kwargs.get("saved_path")
        or kwargs.get("attachment_path")
        or kwargs.get("file")
        or kwargs.get("document_file")
        or kwargs.get("saved_document_path")
    )

    # --------------------------------------------------------
    # Resolve document title, service and customer name
    # --------------------------------------------------------

    document_title = (
        document_title
        or title
        or filename
        or file_name
        or kwargs.get("document_title")
        or kwargs.get("title")
        or kwargs.get("filename")
        or kwargs.get("file_name")
        or kwargs.get("document_name")
        or ""
    )

    service = (
        service
        or service_name
        or kwargs.get("service")
        or kwargs.get("service_name")
        or "Document Service"
    )

    customer_name = (
        customer_name
        or name
        or kwargs.get("customer_name")
        or kwargs.get("name")
        or ""
    )

    subject = (
        subject
        or kwargs.get("subject")
        or DEFAULT_SUBJECT
    )

    recipient_email = _clean_header(recipient_email)
    service = _clean_text(service, "Document Service")
    document_title = _clean_text(document_title)
    customer_name = _clean_header(customer_name)
    subject = _clean_header(subject, DEFAULT_SUBJECT)

    # --------------------------------------------------------
    # Validate customer email
    # --------------------------------------------------------

    if not _valid_email_address(recipient_email):
        return _failure(
            message="A valid customer email address is required.",
            error="invalid_recipient_email",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
        )

    # --------------------------------------------------------
    # Validate document title
    # --------------------------------------------------------

    if not document_title:
        return _failure(
            message="The document title is required for email delivery.",
            error="missing_document_title",
            recipient_email=recipient_email,
            service=service,
        )

    # --------------------------------------------------------
    # Validate existing saved document
    # --------------------------------------------------------

    try:
        saved_document = _resolve_existing_document(document_path)

    except FileNotFoundError as exc:
        return _failure(
            message=str(exc),
            error="document_not_found",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
        )

    except PermissionError as exc:
        return _failure(
            message=str(exc),
            error="document_access_denied",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
        )

    except (ValueError, OSError) as exc:
        return _failure(
            message=str(exc),
            error="invalid_document",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
        )

    # --------------------------------------------------------
    # Read Gmail credentials directly from the environment
    # --------------------------------------------------------

    try:
        sender_email, app_password = _get_smtp_credentials()

    except RuntimeError as exc:
        return _failure(
            message=str(exc),
            error="smtp_not_configured",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
        )

    # --------------------------------------------------------
    # Build message and attach existing document
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

    except Exception as exc:
        return _failure(
            message="The email could not be prepared.",
            error="email_preparation_failed",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    # --------------------------------------------------------
    # Send email through Gmail
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
            "success": True,
            "sent": True,
            "configured": True,
            "provider": "Gmail SMTP",
            "message": (
                "The document email was accepted by Gmail SMTP."
            ),
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
            "attachment": saved_document.name,
            "attachment_path": str(saved_document),
        }

    except smtplib.SMTPAuthenticationError as exc:
        return _failure(
            message=(
                "Gmail rejected the login. Verify that GMAIL_ADDRESS "
                "matches the Google account that created the app password, "
                "and that GMAIL_APP_PASSWORD contains that account's "
                "current Google app password."
            ),
            error="smtp_authentication_failed",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except smtplib.SMTPRecipientsRefused as exc:
        return _failure(
            message=(
                "Gmail refused the recipient address. "
                "Check the customer's email address."
            ),
            error="recipient_refused",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except smtplib.SMTPConnectError as exc:
        return _failure(
            message="Could not establish a connection to Gmail SMTP.",
            error="smtp_connection_failed",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except smtplib.SMTPServerDisconnected as exc:
        return _failure(
            message=(
                "Gmail disconnected before confirming the email operation."
            ),
            error="smtp_disconnected",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except smtplib.SMTPException as exc:
        return _failure(
            message="Gmail SMTP could not send the document email.",
            error="smtp_error",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except (socket.timeout, TimeoutError) as exc:
        return _failure(
            message="The connection to Gmail timed out.",
            error="smtp_timeout",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except OSError as exc:
        return _failure(
            message="A network or system error prevented email delivery.",
            error="network_error",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )

    except Exception as exc:
        return _failure(
            message="An unexpected error occurred during email delivery.",
            error="unexpected_delivery_error",
            recipient_email=recipient_email,
            service=service,
            document_title=document_title,
            details=str(exc),
        )


# ============================================================
# COMPATIBILITY ALIASES
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

    return send_document_email(
        recipient_email=recipient_email,
        document_path=document_path,
        service=service,
        document_title=document_title,
        customer_name=customer_name,
        subject=subject,
        **kwargs,
    )


def send_document(
    recipient_email: str = "",
    document_path: Any = None,
    service: str = "",
    document_title: str = "",
    customer_name: str = "",
    subject: str = "",
    **kwargs: Any,
) -> Dict[str, Any]:

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
# STANDALONE CONFIGURATION CHECK
# ============================================================

if __name__ == "__main__":
    status = configuration_status()

    print("NPBC Gmail SMTP Configuration")
    print("=" * 36)

    for key, value in status.items():
        print(f"{key}: {value}")

    print()

    if status["configured"]:
        print(
            "Both Gmail environment variables were detected. "
            "This check does not send an email or verify the Gmail login."
        )
    else:
        print(
            "The running Python process cannot use the required Gmail "
            "configuration. Review the message above."
        )
