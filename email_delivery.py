"""
Naija Pocket Business Center
Resend Email Delivery Service
================================

FIRST TEST VERSION

Purpose:
    Send an exact existing NPBC document by email through Resend.

This file is independent of:
    - Ada API
    - Payment API
    - Review API
    - Back Office
    - Billing
    - Payment verification

The document supplied to send_document_email() is treated as
the exact final document. This service does not regenerate,
rewrite, or modify it.

Render environment variable required:
    RESEND_API_KEY

Initial Resend test sender:
    onboarding@resend.dev
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Optional

import httpx


RESEND_API_URL = "https://api.resend.com/emails"

RESEND_API_KEY = os.getenv(
    "RESEND_API_KEY",
    ""
).strip()

RESEND_SENDER = "Naija Pocket Business Center <onboarding@resend.dev>"


class EmailDeliveryError(Exception):
    """Raised when NPBC email delivery fails."""


def _clean(value: object) -> str:
    return str(value or "").strip()


def _valid_email(email: str) -> bool:
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

    return filename or "Naija-Pocket-Document.docx"


def _read_exact_document(
    document_path: str
) -> tuple[str, bytes]:
    """
    Read the exact existing document.

    Nothing is regenerated or modified.
    """

    path = Path(
        _clean(document_path)
    )

    if not path.exists():
        raise EmailDeliveryError(
            "The document file could not be found."
        )

    if not path.is_file():
        raise EmailDeliveryError(
            "The document path is not a file."
        )

    try:
        document_bytes = path.read_bytes()
    except Exception as exc:
        raise EmailDeliveryError(
            f"The document could not be read: {exc}"
        ) from exc

    if not document_bytes:
        raise EmailDeliveryError(
            "The document file is empty."
        )

    return (
        _safe_filename(path.name),
        document_bytes
    )


def _configuration_error() -> Optional[str]:
    if not RESEND_API_KEY:
        return (
            "RESEND_API_KEY is not configured."
        )

    return None


def build_email_html(
    customer_name: str = "",
    document_title: str = ""
) -> str:
    """
    Build the NPBC customer-facing email.
    """

    customer_name = _clean(customer_name)
    document_title = _clean(document_title)

    greeting = (
        f"Hello {customer_name},"
        if customer_name
        else "Hello,"
    )

    display_title = (
        document_title
        if document_title
        else "Your completed document"
    )

    return f"""
<!DOCTYPE html>

<html lang="en">

<head>
<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1.0"
>

<title>Your Document Is Ready</title>

</head>

<body
style="
margin:0;
padding:0;
background:#050505;
font-family:Arial,Helvetica,sans-serif;
color:#eeeeee;
"
>

<table
width="100%"
cellpadding="0"
cellspacing="0"
border="0"
style="background:#050505;"
>

<tr>

<td
align="center"
style="padding:32px 16px;"
>

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
padding:30px;
text-align:center;
border-bottom:1px solid #2c2c2c;
"
>

<div
style="
font-size:12px;
letter-spacing:3px;
color:#d4af37;
font-weight:bold;
"
>
NAIJA POCKET BUSINESS CENTER
</div>

<div
style="
margin-top:10px;
font-size:25px;
font-weight:bold;
color:#ffffff;
"
>
Your Document Is Ready
</div>

</td>

</tr>

<tr>

<td style="padding:30px;">

<div
style="
font-size:16px;
line-height:1.7;
color:#eeeeee;
"
>
{greeting}
</div>

<div
style="
margin-top:16px;
font-size:16px;
line-height:1.7;
color:#eeeeee;
"
>
Your completed document is attached to this email.
</div>

<div
style="
margin-top:22px;
padding:18px;
background:#181818;
border-left:3px solid #d4af37;
border-radius:6px;
"
>

<div
style="
font-size:11px;
text-transform:uppercase;
letter-spacing:1.5px;
color:#999999;
"
>
DOCUMENT
</div>

<div
style="
margin-top:8px;
font-size:17px;
font-weight:bold;
color:#d4af37;
"
>
{display_title}
</div>

</div>

<div
style="
margin-top:24px;
font-size:14px;
line-height:1.7;
color:#bdbdbd;
"
>
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

<div
style="
font-size:12px;
color:#888888;
"
>
Fast &bull; Convenient &bull; Open 24/7
</div>

<div
style="
margin-top:8px;
font-size:11px;
color:#666666;
"
>
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


def send_document_email(
    recipient_email: str,
    document_path: str,
    document_title: str = "",
    customer_name: str = "",
    subject: str = "",
) -> dict:
    """
    Send an exact existing document through Resend.

    Parameters
    ----------
    recipient_email:
        Customer's email address.

    document_path:
        Path to the exact canonical document.

    document_title:
        Document title shown in the email.

    customer_name:
        Optional customer name.

    subject:
        Optional email subject.

    Returns
    -------
    dict
        Normalized NPBC delivery result.
    """

    recipient_email = _clean(
        recipient_email
    )

    document_title = _clean(
        document_title
    )

    customer_name = _clean(
        customer_name
    )

    subject = _clean(
        subject
    )

    configuration_error = (
        _configuration_error()
    )

    if configuration_error:
        raise EmailDeliveryError(
            configuration_error
        )

    if not _valid_email(
        recipient_email
    ):
        raise EmailDeliveryError(
            "The customer's email address is invalid."
        )

    filename, document_bytes = (
        _read_exact_document(
            document_path
        )
    )

    encoded_document = (
        base64.b64encode(
            document_bytes
        ).decode("ascii")
    )

    if not subject:

        if document_title:

            subject = (
                "Your completed document — "
                f"{document_title}"
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
        (
            f"Hello {customer_name},"
            if customer_name
            else "Hello,"
        )
        + "\n\n"
        + "Your completed document is attached "
          "to this email.\n\n"
        + (
            f"Document: {document_title}"
            if document_title
            else "Your completed document"
        )
        + "\n\n"
        + "Naija Pocket Business Center\n"
        + "Fast • Convenient • Open 24/7"
    )

    payload = {
        "from": RESEND_SENDER,

        "to": [
            recipient_email
        ],

        "subject": subject,

        "html": html_content,

        "text": text_content,

        "attachments": [
            {
                "filename": filename,
                "content": encoded_document
            }
        ]
    }

    headers = {
        "Authorization": (
            f"Bearer {RESEND_API_KEY}"
        ),
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:

        response = httpx.post(
            RESEND_API_URL,
            headers=headers,
            json=payload,
            timeout=30.0
        )

    except httpx.TimeoutException as exc:

        raise EmailDeliveryError(
            "Resend email delivery timed out."
        ) from exc

    except httpx.RequestError as exc:

        raise EmailDeliveryError(
            f"Resend could not be reached: {exc}"
        ) from exc

    try:

        response_data = response.json()

    except Exception:

        response_data = {}

    if (
        response.status_code < 200
        or response.status_code >= 300
    ):

        detail = ""

        if isinstance(
            response_data,
            dict
        ):

            detail = (
                response_data.get("message")
                or response_data.get("error")
                or ""
            )

        if not detail:

            detail = (
                response.text[:500].strip()
            )

        raise EmailDeliveryError(
            "Resend rejected the email"
            + (
                f": {detail}"
                if detail
                else "."
            )
        )

    message_id = ""

    if isinstance(
        response_data,
        dict
    ):

        message_id = _clean(
            response_data.get("id")
        )

    return {
        "ok": True,
        "message": "EMAIL_SENT",
        "message_id": message_id,
        "recipient_email": recipient_email,
        "filename": filename
    }


__all__ = [
    "EmailDeliveryError",
    "build_email_html",
    "send_document_email"
]
