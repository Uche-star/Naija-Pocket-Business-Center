"""
Naija Pocket Business Center
EMAIL DELIVERY SERVICE

Product-first document delivery using Resend.

PURPOSE
-------
This module is responsible ONLY for sending an already-saved
canonical document to a customer's email address.

It does NOT:
- create payment records
- verify payment
- unlock downloads
- regenerate documents
- modify the canonical document
- create products
- handle Review logic
- handle Back Office logic

CANONICAL DELIVERY RULE
-----------------------
The file supplied to this module must already be the exact
canonical document saved by the Payment/Product API.

The module reads that exact file and attaches it to the email.

RESEND
------
Required Render environment variable:

    RESEND_API_KEY

Optional:

    RESEND_FROM_EMAIL
    RESEND_FROM_NAME

For initial testing, if RESEND_FROM_EMAIL is not supplied,
the module uses:

    onboarding@resend.dev

The Resend API key must NEVER be placed in GitHub/source code.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

RESEND_API_URL = "https://api.resend.com/emails"

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()

RESEND_FROM_EMAIL = (
    os.getenv("RESEND_FROM_EMAIL", "").strip()
    or "onboarding@resend.dev"
)

RESEND_FROM_NAME = (
    os.getenv("RESEND_FROM_NAME", "").strip()
    or "Naija Pocket Business Center"
)

REQUEST_TIMEOUT_SECONDS = 60


# ---------------------------------------------------------------------------
# BASIC HELPERS
# ---------------------------------------------------------------------------

def clean(value: Any) -> str:
    """
    Convert a value to a clean string.
    """
    if value is None:
        return ""

    return str(value).strip()


def html_escape(value: Any) -> str:
    """
    Minimal HTML escaping without requiring another package.
    """
    text = clean(value)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def valid_email(value: str) -> bool:
    """
    Basic email validation.

    This is intentionally not overly restrictive because the
    actual email provider performs final validation.
    """
    value = clean(value)

    if not value:
        return False

    return bool(
        re.match(
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            value,
            re.IGNORECASE,
        )
    )


def get_content_type(file_path: Path) -> str:
    """
    Determine the MIME type from the document extension.
    """
    content_type, _ = mimetypes.guess_type(file_path.name)

    if content_type:
        return content_type

    suffix = file_path.suffix.lower()

    if suffix == ".docx":
        return (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    if suffix == ".pdf":
        return "application/pdf"

    if suffix == ".xlsx":
        return (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )

    if suffix == ".pptx":
        return (
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        )

    if suffix == ".txt":
        return "text/plain"

    return "application/octet-stream"


def build_from_address() -> str:
    """
    Build the sender address accepted by Resend.

    If a display name is configured, return:

        Name <email@example.com>

    Otherwise return the email address alone.
    """
    email = clean(RESEND_FROM_EMAIL)
    name = clean(RESEND_FROM_NAME)

    if not email:
        return ""

    if name:
        return f"{name} <{email}>"

    return email


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

def validate_configuration() -> None:
    """
    Validate the Resend configuration.

    Raises RuntimeError if the API key is missing.
    """
    if not RESEND_API_KEY:
        raise RuntimeError(
            "RESEND_API_KEY is not configured. "
            "Add RESEND_API_KEY to the Render environment variables."
        )

    if not RESEND_FROM_EMAIL:
        raise RuntimeError(
            "RESEND_FROM_EMAIL is not configured."
        )


def validate_document_path(document_path: str | Path) -> Path:
    """
    Validate that the canonical document exists and is a file.

    This function deliberately does NOT create or regenerate a file.
    """
    if not document_path:
        raise ValueError("DOCUMENT_PATH_REQUIRED")

    path = Path(document_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Canonical document does not exist: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Canonical document path is not a file: {path}"
        )

    if path.stat().st_size <= 0:
        raise ValueError(
            f"Canonical document is empty: {path}"
        )

    return path


# ---------------------------------------------------------------------------
# EMAIL CONTENT
# ---------------------------------------------------------------------------

def build_email_subject(
    service: str,
    document_title: str,
) -> str:
    """
    Build the customer-facing subject.
    """
    service = clean(service)
    document_title = clean(document_title)

    if document_title and service:
        return f"Your {service} Document — {document_title}"

    if document_title:
        return f"Your Document — {document_title}"

    if service:
        return f"Your {service} Document"

    return "Your Document"


def build_email_html(
    customer_name: str = "",
    service: str = "",
    document_title: str = "",
) -> str:
    """
    Build the branded customer email.

    The actual document is attached separately.
    """
    customer_name = clean(customer_name)
    service = clean(service)
    document_title = clean(document_title)

    greeting = (
        f"Dear {html_escape(customer_name)},"
        if customer_name
        else "Hello,"
    )

    document_description = ""

    if document_title and service:
        document_description = (
            f'<p style="margin:0 0 18px 0;">'
            f'Your <strong>{html_escape(service)}</strong> document '
            f'<strong>{html_escape(document_title)}</strong> '
            f'is attached to this email.</p>'
        )
    elif document_title:
        document_description = (
            f'<p style="margin:0 0 18px 0;">'
            f'Your document '
            f'<strong>{html_escape(document_title)}</strong> '
            f'is attached to this email.</p>'
        )
    else:
        document_description = (
            '<p style="margin:0 0 18px 0;">'
            'Your completed document is attached to this email.'
            '</p>'
        )

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Your Document</title>
</head>

<body style="
    margin:0;
    padding:0;
    background:#050505;
    font-family:Arial,Helvetica,sans-serif;
    color:#eeeeee;
">

<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#050505;padding:30px 12px;">

<tr>
<td align="center">

<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="
           max-width:620px;
           background:#101010;
           border:1px solid #2a2a2a;
           border-radius:12px;
           overflow:hidden;
       ">

<tr>
<td style="
    padding:28px 30px;
    background:#080808;
    border-bottom:1px solid #2a2a2a;
">

<div style="
    font-size:12px;
    letter-spacing:2px;
    color:#c9a227;
    font-weight:bold;
    text-transform:uppercase;
">
NAIJA POCKET BUSINESS CENTER
</div>

<div style="
    margin-top:8px;
    font-size:13px;
    color:#9c9c9c;
">
Fast • Convenient • Open 24/7
</div>

</td>
</tr>

<tr>
<td style="padding:32px 30px;">

<div style="
    font-size:22px;
    line-height:1.35;
    font-weight:bold;
    color:#ffffff;
    margin-bottom:20px;
">
Your Document Is Ready
</div>

<div style="
    font-size:15px;
    line-height:1.7;
    color:#d7d7d7;
">

<p style="margin:0 0 18px 0;">
{greeting}
</p>

{document_description}

<p style="margin:0 0 18px 0;">
Please keep this email for your records.
</p>

</div>

<div style="
    margin-top:28px;
    padding:18px;
    background:#151515;
    border-left:3px solid #c9a227;
    color:#bdbdbd;
    font-size:13px;
    line-height:1.6;
">
Your document is attached as the exact completed file prepared
through your Naija Pocket Business Center service.
</div>

</td>
</tr>

<tr>
<td style="
    padding:22px 30px;
    background:#080808;
    border-top:1px solid #2a2a2a;
">

<div style="
    font-size:12px;
    color:#8d8d8d;
    line-height:1.6;
">
Naija Pocket Business Center<br>
Fast • Convenient • Open 24/7
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


def build_email_text(
    customer_name: str = "",
    service: str = "",
    document_title: str = "",
) -> str:
    """
    Plain-text fallback for email clients that do not display HTML.
    """
    customer_name = clean(customer_name)
    service = clean(service)
    document_title = clean(document_title)

    greeting = (
        f"Dear {customer_name},"
        if customer_name
        else "Hello,"
    )

    if document_title and service:
        description = (
            f"Your {service} document "
            f"\"{document_title}\" is attached to this email."
        )
    elif document_title:
        description = (
            f"Your document "
            f"\"{document_title}\" is attached to this email."
        )
    else:
        description = (
            "Your completed document is attached to this email."
        )

    return (
        "NAIJA POCKET BUSINESS CENTER\n"
        "Fast • Convenient • Open 24/7\n"
        "\n"
        "Your Document Is Ready\n"
        "\n"
        f"{greeting}\n"
        "\n"
        f"{description}\n"
        "\n"
        "Please keep this email for your records.\n"
        "\n"
        "Your document is attached as the exact completed file "
        "prepared through your Naija Pocket Business Center service.\n"
        "\n"
        "Naija Pocket Business Center\n"
        "Fast • Convenient • Open 24/7"
    )


# ---------------------------------------------------------------------------
# RESEND REQUEST
# ---------------------------------------------------------------------------

def _send_resend_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send the email request directly to Resend's REST API.

    No Resend Python package is required.
    """
    validate_configuration()

    body = json.dumps(
        payload,
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        RESEND_API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "Naija-Pocket-Business-Center/"
                "email-delivery"
            ),
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:

            raw_response = response.read().decode(
                "utf-8",
                errors="replace",
            )

            status_code = response.getcode()

            try:
                parsed = json.loads(raw_response)
            except json.JSONDecodeError:
                parsed = {
                    "raw": raw_response,
                }

            if status_code < 200 or status_code >= 300:
                raise RuntimeError(
                    f"RESEND_HTTP_{status_code}: "
                    f"{raw_response}"
                )

            if isinstance(parsed, dict):
                parsed["_http_status"] = status_code

            return parsed

    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            error_body = str(exc)

        raise RuntimeError(
            f"RESEND_HTTP_{exc.code}: {error_body}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"RESEND_CONNECTION_ERROR: {exc.reason}"
        ) from exc

    except TimeoutError as exc:
        raise RuntimeError(
            "RESEND_TIMEOUT"
        ) from exc


# ---------------------------------------------------------------------------
# DOCUMENT EMAIL
# ---------------------------------------------------------------------------

def send_document_email(
    recipient_email: str,
    document_path: str | Path,
    service: str = "",
    document_title: str = "",
    customer_name: str = "",
    subject: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Send the exact canonical document as an email attachment.

    PARAMETERS
    ----------
    recipient_email:
        Customer's email address.

    document_path:
        Exact path to the already-saved canonical document.

    service:
        NPBC service name.

    document_title:
        Exact product/document title.

    customer_name:
        Optional customer name.

    subject:
        Optional custom email subject.

    RETURNS
    -------
    Dictionary containing:
        ok
        message
        recipient
        filename
        content_type
        message_id
        resend
    """

    recipient_email = clean(recipient_email)

    if not valid_email(recipient_email):
        raise ValueError(
            "INVALID_RECIPIENT_EMAIL"
        )

    path = validate_document_path(document_path)

    service = clean(service)
    document_title = clean(document_title)
    customer_name = clean(customer_name)

    if subject:
        email_subject = clean(subject)
    else:
        email_subject = build_email_subject(
            service=service,
            document_title=document_title,
        )

    html = build_email_html(
        customer_name=customer_name,
        service=service,
        document_title=document_title,
    )

    text = build_email_text(
        customer_name=customer_name,
        service=service,
        document_title=document_title,
    )

    content_type = get_content_type(path)

    file_bytes = path.read_bytes()

    if not file_bytes:
        raise ValueError(
            "CANONICAL_DOCUMENT_EMPTY"
        )

    encoded_file = base64.b64encode(
        file_bytes
    ).decode("ascii")

    attachment = {
        "filename": path.name,
        "content": encoded_file,
    }

    payload: Dict[str, Any] = {
        "from": build_from_address(),
        "to": [recipient_email],
        "subject": email_subject,
        "html": html,
        "text": text,
        "attachments": [
            attachment
        ],
    }

    result = _send_resend_request(payload)

    message_id = ""

    if isinstance(result, dict):
        message_id = clean(
            result.get("id")
            or result.get("message_id")
            or result.get("email_id")
        )

    return {
        "ok": True,
        "message": "EMAIL_SENT",
        "recipient": recipient_email,
        "filename": path.name,
        "content_type": content_type,
        "document_path": str(path),
        "document_size": len(file_bytes),
        "message_id": message_id,
        "resend": result,
    }


# ---------------------------------------------------------------------------
# CONVENIENCE WRAPPER
# ---------------------------------------------------------------------------

def deliver_document(
    product: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Convenience function for the Payment/Product API.

    Expected product fields may include:

        customer_email
        customer_name
        service
        document_title
        document_saved_path

    The function deliberately uses document_saved_path as the
    source of truth.

    It does NOT reconstruct a document from document_payload.
    """

    if not isinstance(product, dict):
        raise ValueError(
            "PRODUCT_DATA_REQUIRED"
        )

    recipient_email = clean(
        product.get("customer_email")
    )

    customer_name = clean(
        product.get("customer_name")
    )

    service = clean(
        product.get("service")
    )

    document_title = clean(
        product.get("document_title")
    )

    document_path = clean(
        product.get("document_saved_path")
    )

    if not recipient_email:
        raise ValueError(
            "CUSTOMER_EMAIL_REQUIRED"
        )

    if not document_path:
        raise ValueError(
            "CANONICAL_DOCUMENT_PATH_REQUIRED"
        )

    return send_document_email(
        recipient_email=recipient_email,
        document_path=document_path,
        service=service,
        document_title=document_title,
        customer_name=customer_name,
    )


# ---------------------------------------------------------------------------
# SAFE TEST / DIAGNOSTIC FUNCTIONS
# ---------------------------------------------------------------------------

def configuration_status() -> Dict[str, Any]:
    """
    Return safe configuration information.

    The actual API key is NEVER returned.
    """
    return {
        "resend_configured": bool(RESEND_API_KEY),
        "from_email_configured": bool(RESEND_FROM_EMAIL),
        "from_name_configured": bool(RESEND_FROM_NAME),
        "from_email": RESEND_FROM_EMAIL,
        "from_name": RESEND_FROM_NAME,
        "api_key_present": bool(RESEND_API_KEY),
    }


# ---------------------------------------------------------------------------
# MODULE EXPORTS
# ---------------------------------------------------------------------------

__all__ = [
    "send_document_email",
    "deliver_document",
    "configuration_status",
    "build_email_subject",
    "build_email_html",
    "build_email_text",
]
