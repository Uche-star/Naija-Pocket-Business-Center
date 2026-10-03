"""
Naija Pocket Business Center
Brevo Email Delivery Service
================================

Purpose
-------
Send an NPBC document to a customer's email through Brevo.

This module is intentionally independent of:
- Ada API
- Payment API
- Review API
- Back Office
- Customer download activation

It receives an already-prepared document and sends that exact
document as an email attachment.

Required Render environment variables
-------------------------------------
BREVO_API_KEY
BREVO_SENDER_EMAIL
BREVO_SENDER_NAME       (optional)

Example:

BREVO_SENDER_EMAIL=your-verified-sender@example.com
BREVO_SENDER_NAME=Naija Pocket Business Center
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Optional

import httpx


BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()

BREVO_SENDER_EMAIL = os.getenv(
    "BREVO_SENDER_EMAIL",
    ""
).strip()

BREVO_SENDER_NAME = os.getenv(
    "BREVO_SENDER_NAME",
    "Naija Pocket Business Center"
).strip()


class EmailDeliveryError(Exception):
    """Raised when NPBC email delivery cannot be completed."""


def _clean(value: object) -> str:
    return str(value or "").strip()


def _valid_email(email: str) -> bool:
    """
    Basic email validation.

    Brevo remains responsible for final validation.
    """
    email = _clean(email)

    if not email:
        return False

    return bool(
        re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            email
        )
    )


def _safe_filename(filename: str) -> str:
    """
    Keep the customer's document filename safe for use
    as an email attachment.
    """
    filename = _clean(filename)

    if not filename:
        filename = "Naija-Pocket-Document.docx"

    filename = Path(filename).name

    filename = re.sub(
        r"[^A-Za-z0-9._()\- ]+",
        "_",
        filename
    )

    filename = filename.strip()

    if not filename:
        filename = "Naija-Pocket-Document.docx"

    return filename


def _read_document(
    document_path: str
) -> tuple[str, bytes]:
    """
    Read the exact existing document from disk.

    The document is not regenerated or modified.
    """
    path = Path(document_path)

    if not path.exists():
        raise EmailDeliveryError(
            "The document file could not be found."
        )

    if not path.is_file():
        raise EmailDeliveryError(
            "The document path is not a file."
        )

    try:
        content = path.read_bytes()
    except Exception as exc:
        raise EmailDeliveryError(
            f"The document could not be read: {exc}"
        ) from exc

    if not content:
        raise EmailDeliveryError(
            "The document file is empty."
        )

    return _safe_filename(path.name), content


def _document_to_base64(content: bytes) -> str:
    """
    Convert the exact document bytes to the base64 representation
    required by Brevo's attachment API.
    """
    return base64.b64encode(content).decode("ascii")


def build_email_html(
    customer_name: str = "",
    document_title: str = ""
) -> str:
    """
    Build the customer-facing NPBC email.

    This is intentionally generated here for the first version so
    the service can be tested without another dependency.
    """
    customer_name = _clean(customer_name)

    document_title = _clean(document_title)

    if not customer_name:
        greeting = "Hello,"
    else:
        greeting = f"Hello {customer_name},"

    if not document_title:
        document_title = "your document"

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Your document is ready</title>
</head>

<body style="
    margin:0;
    padding:0;
    background:#050505;
    font-family:Arial,Helvetica,sans-serif;
    color:#eeeeee;
">

<table
    width="100%"
    cellpadding="0"
    cellspacing="0"
    border="0"
    style="background:#050505;"
>
<tr>
<td align="center" style="padding:32px 16px;">

<table
    width="100%"
    cellpadding="0"
    cellspacing="0"
    border="0"
    style="
        max-width:620px;
        background:#101010;
        border:1px solid #2c2c2c;
        border-radius:14px;
        overflow:hidden;
    "
>

<tr>
<td
    style="
        padding:28px 30px;
        text-align:center;
        border-bottom:1px solid #2c2c2c;
    "
>

<div style="
    font-size:12px;
    letter-spacing:3px;
    color:#d4af37;
    font-weight:bold;
">
NAIJA POCKET BUSINESS CENTER
</div>

<div style="
    margin-top:10px;
    font-size:25px;
    font-weight:bold;
    color:#ffffff;
">
Your Document Is Ready
</div>

</td>
</tr>

<tr>
<td style="padding:30px;">

<div style="
    font-size:16px;
    line-height:1.7;
    color:#eeeeee;
">
{greeting}
</div>

<div style="
    margin-top:16px;
    font-size:16px;
    line-height:1.7;
    color:#eeeeee;
">
Your completed document is attached to this email.
</div>

<div style="
    margin-top:22px;
    padding:18px;
    background:#181818;
    border-left:3px solid #d4af37;
    border-radius:6px;
">

<div style="
    font-size:12px;
    text-transform:uppercase;
    letter-spacing:1.5px;
    color:#a9a9a9;
">
Document
</div>

<div style="
    margin-top:7px;
    font-size:17px;
    font-weight:bold;
    color:#d4af37;
">
{document_title}
</div>

</div>

<div style="
    margin-top:24px;
    font-size:14px;
    line-height:1.7;
    color:#bdbdbd;
">
Please keep this email and its attachment for your records.
</div>

</td>
</tr>

<tr>
<td
    style="
        padding:22px 30px;
        text-align:center;
        border-top:1px solid #2c2c2c;
    "
>

<div style="
    font-size:12px;
    color:#888888;
">
Fast &bull; Convenient &bull; Open 24/7
</div>

<div style="
    margin-top:8px;
    font-size:11px;
    color:#666666;
">
Naija Pocket Business Center
</div>

</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
""".strip()


def _configuration_error() -> Optional[str]:
    """
    Check required Brevo configuration before making a request.
    """
    if not BREVO_API_KEY:
        return (
            "BREVO_API_KEY is not configured."
        )

    if not BREVO_SENDER_EMAIL:
        return (
            "BREVO_SENDER_EMAIL is not configured."
        )

    if not _valid_email(BREVO_SENDER_EMAIL):
        return (
            "BREVO_SENDER_EMAIL is not a valid email address."
        )

    return None


def send_document_email(
    recipient_email: str,
    document_path: str,
    document_title: str = "",
    customer_name: str = "",
    subject: str = "",
) -> dict:
    """
    Send the exact document at document_path to the recipient.

    Parameters
    ----------
    recipient_email:
        Customer's email address.

    document_path:
        Existing canonical document file.

    document_title:
        Title shown inside the email.

    customer_name:
        Optional customer's name.

    subject:
        Optional custom subject.

    Returns
    -------
    dict
        A normalized success response containing Brevo's message ID.

    Important
    ---------
    This function does not:
    - create a document
    - modify a document
    - regenerate a document
    - verify payment
    - unlock downloads
    - access the Payment API
    """

    recipient_email = _clean(recipient_email)
    document_title = _clean(document_title)
    customer_name = _clean(customer_name)
    subject = _clean(subject)

    configuration_error = _configuration_error()

    if configuration_error:
        raise EmailDeliveryError(
            configuration_error
        )

    if not _valid_email(recipient_email):
        raise EmailDeliveryError(
            "The customer's email address is invalid."
        )

    filename, document_bytes = _read_document(
        document_path
    )

    encoded_document = _document_to_base64(
        document_bytes
    )

    if not subject:
        if document_title:
            subject = (
                f"Your completed document — {document_title}"
            )
        else:
            subject = (
                "Your completed document — "
                "Naija Pocket Business Center"
            )

    html_content = build_email_html(
        customer_name=customer_name,
        document_title=document_title
    )

    text_content = (
        f"{'Hello ' + customer_name + ',' if customer_name else 'Hello,'}\n\n"
        f"Your completed document is attached to this email.\n\n"
        f"Document: {document_title or 'Your document'}\n\n"
        "Naija Pocket Business Center\n"
        "Fast • Convenient • Open 24/7"
    )

    payload = {
        "sender": {
            "name": BREVO_SENDER_NAME,
            "email": BREVO_SENDER_EMAIL,
        },
        "to": [
            {
                "email": recipient_email,
                **(
                    {"name": customer_name}
                    if customer_name
                    else {}
                ),
            }
        ],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": text_content,
        "attachment": [
            {
                "content": encoded_document,
                "name": filename,
            }
        ],
        "tags": [
            "NPBC",
            "document-delivery",
        ],
    }

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    try:
        response = httpx.post(
            BREVO_API_URL,
            headers=headers,
            json=payload,
            timeout=30.0,
        )

    except httpx.TimeoutException as exc:
        raise EmailDeliveryError(
            "Brevo email delivery timed out."
        ) from exc

    except httpx.RequestError as exc:
        raise EmailDeliveryError(
            f"Brevo could not be reached: {exc}"
        ) from exc

    try:
        response_data = response.json()
    except Exception:
        response_data = {}

    if response.status_code < 200 or response.status_code >= 300:

        detail = ""

        if isinstance(response_data, dict):
            detail = (
                response_data.get("message")
                or response_data.get("error")
                or ""
            )

        if not detail:
            detail = response.text[:500].strip()

        raise EmailDeliveryError(
            "Brevo rejected the email"
            + (
                f": {detail}"
                if detail
                else "."
            )
        )

    message_id = ""

    if isinstance(response_data, dict):
        message_id = _clean(
            response_data.get("messageId")
        )

    return {
        "ok": True,
        "message": "EMAIL_SENT",
        "message_id": message_id,
        "recipient_email": recipient_email,
        "filename": filename,
    }


__all__ = [
    "EmailDeliveryError",
    "build_email_html",
    "send_document_email",
]
