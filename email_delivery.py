"""
Naija Pocket Business Center (NPBC)
Gmail SMTP Email Delivery Module

Provider:
    Gmail SMTP

Required Render Environment Variables:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

SMTP:
    smtp.gmail.com
    Port 587
    STARTTLS

Features:
    - Sends documents as email attachments.
    - Supports DOCX, PDF, XLSX, PPTX, and TXT.
    - Provides plain-text and HTML email alternatives.
    - Uses temporary files supplied by the calling API.
    - Does not permanently store documents.
    - Never returns email passwords in responses.
"""

import os
import re
import smtplib
import mimetypes

from pathlib import Path
from email.message import EmailMessage
from email.utils import formataddr


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

ALLOWED_EXTENSIONS = {
    ".docx",
    ".pdf",
    ".xlsx",
    ".pptx",
    ".txt",
}

MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024

DEFAULT_SUBJECT = "Your NPBC Document Is Ready"


# --------------------------------------------------
# ENVIRONMENT CONFIGURATION
# --------------------------------------------------

def configuration_status():
    """
    Return a safe summary of Gmail configuration.
    Never expose the app password.
    """

    gmail_address = os.getenv(
        "GMAIL_ADDRESS", ""
    ).strip()

    gmail_password = os.getenv(
        "GMAIL_APP_PASSWORD", ""
    ).strip().replace(" ", "")

    return {
        "provider": "Gmail SMTP",
        "configured": bool(
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
    }


# --------------------------------------------------
# HTML HELPERS
# --------------------------------------------------

def escape_html(value):
    """
    Escape user-provided text before placing it in HTML.
    """

    value = str(value or "")

    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def safe_filename(value):
    """
    Return a safe attachment filename.
    """

    name = Path(str(value or "")).name

    name = re.sub(
        r"[\r\n\x00]",
        "",
        name,
    )

    return name or "NPBC_Document"


# --------------------------------------------------
# EMAIL CONTENT
# --------------------------------------------------

def build_email_content(
    service,
    document_title,
    customer_name,
):
    """
    Build plain-text and Gmail-compatible HTML bodies.
    """

    service = str(
        service or "NPBC Service"
    ).strip()

    document_title = str(
        document_title or "Your Document"
    ).strip()

    customer_name = str(
        customer_name or "Valued Customer"
    ).strip()

    plain_text = (
        f"Hello {customer_name},\n\n"
        "Your document from Naija Pocket Business "
        "Center is ready.\n\n"
        f"Service: {service}\n"
        f"Document: {document_title}\n\n"
        "Your document is attached to this email.\n\n"
        "Thank you for choosing Naija Pocket "
        "Business Center.\n\n"
        "Fast • Convenient • Open 24/7\n"
        "Naija Pocket Business Center"
    )

    safe_customer = escape_html(customer_name)
    safe_service = escape_html(service)
    safe_title = escape_html(document_title)

    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1.0">
<title>Your NPBC Document Is Ready</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;
             font-family:Arial,Helvetica,sans-serif;">

<table role="presentation" width="100%" cellpadding="0"
       cellspacing="0" border="0"
       style="background:#f4f4f4;">
<tr>
<td align="center" style="padding:24px 10px;">

<table role="presentation" width="100%" cellpadding="0"
       cellspacing="0" border="0"
       style="max-width:600px;background:#ffffff;
              border:1px solid #e5e5e5;">

<tr>
<td align="center"
    style="background:#111111;padding:28px 20px;">

<p style="margin:0;color:#d4af37;font-size:23px;
          font-weight:bold;letter-spacing:1px;">
Naija Pocket Business Center
</p>

<p style="margin:10px 0 0;color:#ffffff;
          font-size:13px;">
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
Your document from Naija Pocket Business Center
is ready. Please find your document attached
to this email.
</p>

<table role="presentation" width="100%"
       cellpadding="0" cellspacing="0" border="0"
       style="background:#faf8f0;
              border:1px solid #eadca7;">
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

<p style="margin:22px 0 0;font-size:14px;
          line-height:1.7;">
The document is attached to this email.
You can open the attachment to view your file.
</p>

<p style="margin:24px 0 0;font-size:14px;
          line-height:1.7;">
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

<p style="margin:8px 0 0;color:#ffffff;
          font-size:12px;">
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


# --------------------------------------------------
# SEND DOCUMENT EMAIL
# --------------------------------------------------

def send_document_email(
    recipient_email,
    document_path,
    service="NPBC Service",
    document_title="Your Document",
    customer_name="Valued Customer",
    subject=None,
):
    """
    Send a document through Gmail SMTP.

    Compatible with email_test_api.py:

        result = send_document_email(
            recipient_email=...,
            document_path=...,
            service=...,
            document_title=...,
            customer_name=...,
            subject=...,
        )

    Returns:
        {
            "ok": True or False,
            "message": "...",
            "recipient": "...",
            "filename": "...",
            "message_id": "..."
        }
    """

    recipient_email = str(
        recipient_email or ""
    ).strip()

    if (
        not recipient_email
        or "\r" in recipient_email
        or "\n" in recipient_email
        or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            recipient_email,
        )
    ):
        return {
            "ok": False,
            "message": "A valid recipient email is required.",
            "recipient": recipient_email,
        }

    gmail_address = os.getenv(
        "GMAIL_ADDRESS", ""
    ).strip()

    gmail_password = os.getenv(
        "GMAIL_APP_PASSWORD", ""
    ).strip().replace(" ", "")

    if not gmail_address or not gmail_password:
        return {
            "ok": False,
            "message": (
                "Gmail is not configured. Check the "
                "GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                "environment variables in Render."
            ),
            "recipient": recipient_email,
        }

    if (
        "\r" in gmail_address
        or "\n" in gmail_address
    ):
        return {
            "ok": False,
            "message": "Invalid Gmail sender configuration.",
            "recipient": recipient_email,
        }

    if (
        "\r" in str(subject or "")
        or "\n" in str(subject or "")
    ):
        return {
            "ok": False,
            "message": "Invalid email subject.",
            "recipient": recipient_email,
        }

    if not document_path:
        return {
            "ok": False,
            "message": "No document was provided.",
            "recipient": recipient_email,
        }

    path = Path(document_path)

    if not path.is_file():
        return {
            "ok": False,
            "message": "The document file could not be found.",
            "recipient": recipient_email,
        }

    filename = safe_filename(path.name)
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        return {
            "ok": False,
            "message": (
                "Unsupported attachment type. "
                "Allowed: DOCX, PDF, XLSX, PPTX, TXT."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }

    try:
        file_size = path.stat().st_size
    except OSError:
        return {
            "ok": False,
            "message": "Unable to inspect the document file.",
            "recipient": recipient_email,
            "filename": filename,
        }

    if file_size <= 0:
        return {
            "ok": False,
            "message": "The document file is empty.",
            "recipient": recipient_email,
            "filename": filename,
        }

    if file_size > MAX_ATTACHMENT_SIZE:
        return {
            "ok": False,
            "message": (
                "The document exceeds the 25 MB "
                "attachment limit."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }

    try:
        with path.open("rb") as document_file:
            attachment_data = document_file.read(
                MAX_ATTACHMENT_SIZE + 1
            )

        if len(attachment_data) > MAX_ATTACHMENT_SIZE:
            return {
                "ok": False,
                "message": (
                    "The document exceeds the 25 MB "
                    "attachment limit."
                ),
                "recipient": recipient_email,
                "filename": filename,
            }

        mime_type, _ = mimetypes.guess_type(filename)

        if mime_type and "/" in mime_type:
            maintype, subtype = mime_type.split("/", 1)
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

        message["Subject"] = (
            str(subject).strip()
            if subject and str(subject).strip()
            else DEFAULT_SUBJECT
        )

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

        # ------------------------------------------
        # Gmail SMTP + STARTTLS
        # ------------------------------------------

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=30,
        ) as smtp:

            smtp.ehlo()

            smtp.starttls()

            smtp.ehlo()

            smtp.login(
                gmail_address,
                gmail_password,
            )

            smtp.send_message(message)

        return {
            "ok": True,
            "message": "Email sent successfully via Gmail SMTP.",
            "recipient": recipient_email,
            "filename": filename,
            "message_id": message.get("Message-ID"),
        }

    except smtplib.SMTPAuthenticationError:
        return {
            "ok": False,
            "message": (
                "Gmail authentication failed. Check that "
                "GMAIL_ADDRESS matches the Google account "
                "that generated the app password, and "
                "verify GMAIL_APP_PASSWORD in Render."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }

    except smtplib.SMTPRecipientsRefused:
        return {
            "ok": False,
            "message": (
                "Gmail refused the recipient address. "
                "Check the destination email address."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }

    except smtplib.SMTPException:
        return {
            "ok": False,
            "message": (
                "Gmail SMTP could not complete delivery. "
                "Check the Render logs for the SMTP error."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }

    except (OSError, TimeoutError):
        return {
            "ok": False,
            "message": (
                "A network or file error interrupted "
                "email delivery. Check the Render logs."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }

    except Exception:
        # Do not expose credentials or internal details.
        return {
            "ok": False,
            "message": (
                "Unexpected email delivery error. "
                "Check the Render logs for details."
            ),
            "recipient": recipient_email,
            "filename": filename,
        }
