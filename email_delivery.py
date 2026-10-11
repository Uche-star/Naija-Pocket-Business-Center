"""
Naija Pocket Business Center (NPBC)
Complete Gmail SMTP Email Delivery Module

FILE NAME:
    email_delivery.py

FEATURES:
    - Sends existing saved documents.
    - Accepts reviewed document content directly from Review.
    - Creates a valid DOCX attachment from supplied document content.
    - Does not regenerate the document using AI.
    - Does not require payment confirmation.
    - Uses Gmail SMTP and Google App Password.
    - Supports existing payment_api.py argument names.
    - Uses Python standard library only.
"""

from __future__ import annotations

import html
import os
import re
import smtplib
import socket
import ssl
import tempfile
import mimetypes
import zipfile

from pathlib import Path
from typing import Any, Dict
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from xml.sax.saxutils import escape


# ============================================================
# CONFIGURATION
# ============================================================

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 30

GMAIL_ADDRESS_ENV = "GMAIL_ADDRESS"
GMAIL_APP_PASSWORD_ENV = "GMAIL_APP_PASSWORD"

BRAND_NAME = "Naija Pocket Business Center"

MAX_ATTACHMENT_SIZE_MB = 25
MAX_ATTACHMENT_SIZE_BYTES = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024

DEFAULT_SUBJECT = "Your Document from Naija Pocket Business Center"

SUPPORTED_EXTENSIONS = {
    ".docx", ".pdf", ".xlsx", ".xls",
    ".pptx", ".ppt", ".txt", ".csv",
    ".rtf", ".odt", ".ods", ".zip",
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default

    try:
        value = str(value).strip()
    except Exception:
        return default

    return value if value else default


def _header(value: Any, default: str = "") -> str:
    value = _text(value, default)
    value = value.replace("\r", " ").replace("\n", " ")
    return " ".join(value.split())


def _valid_email(address: str) -> bool:
    address = _header(address)

    if not address or len(address) > 254:
        return False

    if any(ch.isspace() for ch in address):
        return False

    display_name, parsed_address = parseaddr(address)

    if display_name or parsed_address.lower() != address.lower():
        return False

    if address.count("@") != 1 or ".." in address:
        return False

    local, domain = address.rsplit("@", 1)

    if not local or not domain or len(local) > 64:
        return False

    if "." not in domain:
        return False

    if domain.startswith(".") or domain.endswith("."):
        return False

    if not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+",
        local,
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


def _safe_filename(value: str) -> str:
    value = _text(value, "NPBC Document")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")

    return value[:150] or "NPBC Document"


# ============================================================
# GMAIL CONFIGURATION
# ============================================================

def _get_credentials() -> tuple[str, str]:
    sender = _text(os.environ.get(GMAIL_ADDRESS_ENV))
    password = _text(os.environ.get(GMAIL_APP_PASSWORD_ENV))

    if not sender:
        raise RuntimeError(
            "GMAIL_ADDRESS is missing from the application environment."
        )

    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing from the application environment."
        )

    if not _valid_email(sender):
        raise RuntimeError(
            "GMAIL_ADDRESS is not a valid email address."
        )

    # Google sometimes displays app passwords with spaces.
    password = re.sub(r"\s+", "", password)

    if not password:
        raise RuntimeError("GMAIL_APP_PASSWORD is empty.")

    return sender, password


def configuration_status() -> Dict[str, Any]:
    try:
        sender, _password = _get_credentials()

        return {
            "configured": True,
            "ready": True,
            "provider": "Gmail SMTP",
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "security": "STARTTLS",
            "gmail_address": sender,
            "message": "Gmail credentials are configured.",
        }

    except RuntimeError as exc:
        return {
            "configured": False,
            "ready": False,
            "provider": "Gmail SMTP",
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "security": "STARTTLS",
            "message": str(exc),
        }


def get_configuration_status() -> Dict[str, Any]:
    return configuration_status()


def is_configured() -> bool:
    return bool(configuration_status()["configured"])


# ============================================================
# REVIEWED CONTENT TO DOCX
# ============================================================

def _clean_document_content(content: Any) -> str:
    """
    Prepare content supplied by the Review page.

    This does not generate new document content. It removes common
    Markdown formatting markers and HTML tags from the supplied text.
    """

    content = _text(content)

    if not content:
        raise ValueError(
            "The reviewed document content is empty."
        )

    # Decode common HTML entities.
    content = html.unescape(content)

    # Convert common HTML line endings before removing remaining tags.
    content = re.sub(
        r"(?i)<\s*br\s*/?\s*>",
        "\n",
        content,
    )
    content = re.sub(
        r"(?i)</\s*(p|div|h[1-6]|li|tr)\s*>",
        "\n",
        content,
    )
    content = re.sub(r"<[^>]*>", "", content)

    # Remove Markdown heading markers.
    content = re.sub(
        r"(?m)^\s{0,3}#{1,6}\s*",
        "",
        content,
    )

    # Remove horizontal Markdown rules.
    content = re.sub(
        r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$",
        "",
        content,
    )

    # Remove bold and italic markers.
    content = re.sub(r"\*\*(.*?)\*\*", r"\1", content, flags=re.S)
    content = re.sub(r"__(.*?)__", r"\1", content, flags=re.S)
    content = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", content)
    content = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", content)

    # Remove inline-code markers.
    content = re.sub(r"`([^`]*)`", r"\1", content)

    # Normalize line endings.
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # Remove excessive blank lines without joining paragraphs.
    content = re.sub(r"\n[ \t]+\n", "\n\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content)

    content = content.strip()

    if not content:
        raise ValueError(
            "No usable document text remains after cleaning."
        )

    return content


def _paragraph_xml(text: str) -> str:
    """
    Create a WordprocessingML paragraph.

    Leading and trailing spaces are preserved when needed.
    """

    safe_text = escape(text)

    return (
        "<w:p>"
        "<w:r>"
        '<w:rPr><w:sz w:val="24"/></w:rPr>'
        f'<w:t xml:space="preserve">{safe_text}</w:t>'
        "</w:r>"
        "</w:p>"
    )


def _create_docx_from_content(
    content: Any,
    document_title: str,
    output_path: Path,
) -> Path:
    """
    Create a valid DOCX attachment using only Python's standard library.

    The supplied reviewed text is used as the document content.
    No AI generation or external service is called.
    """

    cleaned = _clean_document_content(content)
    paragraphs = []

    for line in cleaned.split("\n"):
        line = line.strip()

        if line:
            paragraphs.append(_paragraph_xml(line))
        else:
            paragraphs.append("<w:p/>")

    document_body = "".join(paragraphs)

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  {document_body}
  <w:sectPr>
   <w:pgSz w:w="11906" w:h="16838"/>
   <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"
    w:header="708" w:footer="708" w:gutter="0"/>
  </w:sectPr>
 </w:body>
</w:document>'''

    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:docDefaults>
  <w:rPrDefault>
   <w:rPr>
    <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>
    <w:sz w:val="24"/>
   </w:rPr>
  </w:rPrDefault>
  <w:pPrDefault>
   <w:pPr>
    <w:spacing w:after="160" w:line="360" w:lineRule="auto"/>
   </w:pPr>
  </w:pPrDefault>
 </w:docDefaults>
</w:styles>'''

    content_types_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels"
  ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml"
  ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
 <Override PartName="/word/styles.xml"
  ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
 <Override PartName="/docProps/core.xml"
  ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>'''

    relationships_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
  Target="word/document.xml"/>
 <Relationship Id="rId2"
  Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
  Target="docProps/core.xml"/>
</Relationships>'''

    document_relationships_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
  Target="styles.xml"/>
</Relationships>'''

    safe_title = escape(_text(document_title, "NPBC Document"))

    core_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties
 xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <dc:title>{safe_title}</dc:title>
 <dc:creator>Naija Pocket Business Center</dc:creator>
 <cp:lastModifiedBy>Naija Pocket Business Center</cp:lastModifiedBy>
</cp:coreProperties>'''

    try:
        with zipfile.ZipFile(
            output_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            archive.writestr(
                "[Content_Types].xml",
                content_types_xml,
            )
            archive.writestr(
                "_rels/.rels",
                relationships_xml,
            )
            archive.writestr(
                "word/document.xml",
                document_xml,
            )
            archive.writestr(
                "word/styles.xml",
                styles_xml,
            )
            archive.writestr(
                "word/_rels/document.xml.rels",
                document_relationships_xml,
            )
            archive.writestr(
                "docProps/core.xml",
                core_xml,
            )

    except Exception:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("The DOCX attachment could not be created.")

    return output_path


# ============================================================
# EXISTING DOCUMENT VALIDATION
# ============================================================

def _resolve_existing_document(document_path: Any) -> Path:
    if document_path is None:
        raise ValueError(
            "A saved document path or reviewed document content is required."
        )

    path = Path(str(document_path)).expanduser().resolve()

    if not path.exists():
        raise FileNotFoundError(
            "The saved document could not be found at the supplied path."
        )

    if not path.is_file():
        raise ValueError("The supplied document path is not a file.")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported document format: {path.suffix or '(no extension)'}."
        )

    if not os.access(str(path), os.R_OK):
        raise PermissionError("The saved document cannot be read.")

    size = path.stat().st_size

    if size <= 0:
        raise ValueError("The saved document is empty.")

    if size > MAX_ATTACHMENT_SIZE_BYTES:
        raise ValueError(
            f"The document exceeds the {MAX_ATTACHMENT_SIZE_MB} MB limit."
        )

    return path


def _mime_type(path: Path) -> tuple[str, str]:
    known = {
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

    if extension in known:
        return known[extension]

    guessed, _encoding = mimetypes.guess_type(str(path))

    if guessed and "/" in guessed:
        return tuple(guessed.split("/", 1))

    return "application", "octet-stream"


# ============================================================
# EMAIL BODY
# ============================================================

def _plain_email(
    customer_name: str,
    service: str,
    document_title: str,
) -> str:
    greeting = (
        f"Dear {customer_name},"
        if customer_name
        else "Hello,"
    )

    return (
        f"{greeting}\n\n"
        "Your document from Naija Pocket Business Center is attached.\n\n"
        f"Service: {service}\n"
        f"Document title: {document_title}\n\n"
        "Please open the attachment to access your document.\n\n"
        "If you need assistance, please contact NPBC Customer Care.\n\n"
        "Thank you for choosing Naija Pocket Business Center.\n\n"
        "Fast • Convenient • Open 24/7\n"
        "Naija Pocket Business Center"
    )


def _html_email(
    customer_name: str,
    service: str,
    document_title: str,
) -> str:
    greeting = (
        f"Dear {html.escape(customer_name)},"
        if customer_name
        else "Hello,"
    )

    safe_service = html.escape(service)
    safe_title = html.escape(document_title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your NPBC Document</title>
</head>
<body style="margin:0;padding:0;background:#f2f2f2;font-family:Arial,Helvetica,sans-serif;color:#202020;">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="background:#f2f2f2;">
<tr><td align="center" style="padding:24px 10px;">
<table role="presentation" width="600" border="0" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e5e5e5;">
<tr><td align="center" style="padding:28px 20px;background:#080808;border-bottom:4px solid #D4AF37;">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0">
<tr><td align="center" style="font-size:24px;line-height:32px;font-weight:bold;color:#D4AF37;">Naija Pocket Business Center</td></tr>
<tr><td align="center" style="padding-top:8px;font-size:13px;line-height:20px;color:#ffffff;">Fast &bull; Convenient &bull; Open 24/7</td></tr>
</table>
</td></tr>
<tr><td style="padding:30px 24px 12px;font-size:15px;line-height:25px;">
<p style="margin:0 0 18px;">{greeting}</p>
<p style="margin:0 0 18px;">Your document from <strong>Naija Pocket Business Center</strong> is attached to this email.</p>
<p style="margin:0 0 18px;">Please open the attachment to access your document.</p>
</td></tr>
<tr><td style="padding:0 24px 26px;">
<table role="presentation" width="100%" border="0" cellspacing="0" cellpadding="0" style="border:1px solid #e5e5e5;">
<tr><td colspan="2" style="padding:14px 16px;background:#080808;color:#D4AF37;font-weight:bold;">Document Details</td></tr>
<tr><td width="34%" style="padding:12px 10px 12px 16px;border-bottom:1px solid #eeeeee;font-weight:bold;color:#555555;">Service</td><td width="66%" style="padding:12px 16px 12px 10px;border-bottom:1px solid #eeeeee;word-break:break-word;">{safe_service}</td></tr>
<tr><td width="34%" style="padding:12px 10px 12px 16px;font-weight:bold;color:#555555;">Document Title</td><td width="66%" style="padding:12px 16px 12px 10px;word-break:break-word;">{safe_title}</td></tr>
</table>
</td></tr>
<tr><td style="padding:0 24px 28px;font-size:14px;line-height:23px;color:#444444;">
<p>If you need assistance, please contact NPBC Customer Care.</p>
<p>Thank you for choosing Naija Pocket Business Center.</p>
</td></tr>
<tr><td align="center" style="padding:20px 16px;background:#080808;border-top:3px solid #D4AF37;font-size:12px;line-height:20px;color:#ffffff;">
<strong style="color:#D4AF37;">Naija Pocket Business Center</strong><br>
Fast &bull; Convenient &bull; Open 24/7
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


# ============================================================
# EMAIL MESSAGE
# ============================================================

def _build_message(
    sender: str,
    recipient: str,
    attachment_path: Path,
    service: str,
    document_title: str,
    customer_name: str,
    subject: str,
) -> EmailMessage:
    message = EmailMessage()

    message["From"] = formataddr((BRAND_NAME, sender))
    message["To"] = recipient
    message["Subject"] = _header(subject, DEFAULT_SUBJECT)
    message["X-Mailer"] = "NPBC Gmail SMTP Delivery"

    message.set_content(
        _plain_email(customer_name, service, document_title)
    )

    message.add_alternative(
        _html_email(customer_name, service, document_title),
        subtype="html",
    )

    with attachment_path.open("rb") as file_handle:
        attachment = file_handle.read()

    if not attachment:
        raise ValueError("The attachment is empty.")

    if len(attachment) > MAX_ATTACHMENT_SIZE_BYTES:
        raise ValueError(
            f"The attachment exceeds the {MAX_ATTACHMENT_SIZE_MB} MB limit."
        )

    main_type, sub_type = _mime_type(attachment_path)

    message.add_attachment(
        attachment,
        maintype=main_type,
        subtype=sub_type,
        filename=attachment_path.name,
    )

    return message


# ============================================================
# GMAIL TRANSPORT
# ============================================================

def _send_via_gmail(
    sender: str,
    password: str,
    recipient: str,
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
        smtp.login(sender, password)

        refused = smtp.send_message(
            message,
            from_addr=sender,
            to_addrs=[recipient],
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
        "configured": is_configured(),
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
# PUBLIC DELIVERY FUNCTION
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
    document_content: Any = None,
    content: Any = None,
    reviewed_content: Any = None,
    document_text: Any = None,
    file_format: str = "docx",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Send a saved document or content supplied by the Review page.

    Content aliases:
        document_content, content, reviewed_content, document_text

    If document_content is supplied, it takes priority over saved-file
    lookup. The supplied reviewed text is placed in a DOCX attachment.

    No payment confirmation is required.
    """

    recipient_email = (
        recipient_email or customer_email or email
        or to_email or to or recipient
        or kwargs.get("recipient_email")
        or kwargs.get("customer_email")
        or kwargs.get("email")
        or kwargs.get("to_email")
        or kwargs.get("recipient")
        or ""
    )

    document_path = (
        document_path or file_path or saved_path
        or attachment_path or file or document_file
        or saved_document_path
        or kwargs.get("document_path")
        or kwargs.get("file_path")
        or kwargs.get("saved_path")
        or kwargs.get("attachment_path")
        or kwargs.get("saved_document_path")
    )

    document_title = (
        document_title or title or filename or file_name
        or kwargs.get("document_title")
        or kwargs.get("title")
        or kwargs.get("filename")
        or kwargs.get("file_name")
        or kwargs.get("document_name")
        or ""
    )

    service = (
        service or service_name
        or kwargs.get("service")
        or kwargs.get("service_name")
        or "Document Service"
    )

    customer_name = (
        customer_name or name
        or kwargs.get("customer_name")
        or kwargs.get("name")
        or ""
    )

    subject = _header(
        subject or kwargs.get("subject"),
        DEFAULT_SUBJECT,
    )

    recipient_email = _header(recipient_email)
    service = _text(service, "Document Service")
    document_title = _text(document_title)
    customer_name = _header(customer_name)

    document_content = (
        document_content
        if document_content is not None
        else content
    )

    if document_content is None:
        document_content = reviewed_content

    if document_content is None:
        document_content = document_text

    if document_content is None:
        document_content = kwargs.get("document_content")

    if document_content is None:
        document_content = kwargs.get("reviewed_content")

    if document_content is None:
        document_content = kwargs.get("document_text")

    if not _valid_email(recipient_email):
        return _failure(
            "A valid customer email address is required.",
            "invalid_recipient_email",
            recipient_email,
            service,
            document_title,
        )

    if not document_title:
        return _failure(
            "The document title is required.",
            "missing_document_title",
            recipient_email,
            service,
        )

    if not document_content and not document_path:
        return _failure(
            "The reviewed document content or a saved document path is required.",
            "missing_document",
            recipient_email,
            service,
            document_title,
        )

    # --------------------------------------------------------
    # Gmail credentials
    # --------------------------------------------------------

    try:
        sender, password = _get_credentials()

    except RuntimeError as exc:
        return _failure(
            str(exc),
            "smtp_not_configured",
            recipient_email,
            service,
            document_title,
        )

    # --------------------------------------------------------
    # Prepare attachment
    # --------------------------------------------------------

    temporary_path = None

    try:
        if document_content:
            # This path is used when Review sends its document text
            # directly to the email API.
            requested_format = _text(file_format, "docx").lower().lstrip(".")

            if requested_format not in {"docx", "txt"}:
                requested_format = "docx"

            with tempfile.NamedTemporaryFile(
                prefix="npbc_email_",
                suffix="." + requested_format,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

            if requested_format == "txt":
                cleaned = _clean_document_content(document_content)
                temporary_path.write_text(
                    cleaned,
                    encoding="utf-8",
                )
            else:
                _create_docx_from_content(
                    content=document_content,
                    document_title=document_title,
                    output_path=temporary_path,
                )

            attachment_name = _safe_filename(document_title)

            final_name = attachment_name + "." + requested_format

            message_path = temporary_path

            # The email attachment uses the document title as its filename.
            # The temporary file itself remains in the secure system temp folder.

        else:
            message_path = _resolve_existing_document(document_path)
            final_name = message_path.name

        message = _build_message(
            sender=sender,
            recipient=recipient_email,
            attachment_path=message_path,
            service=service,
            document_title=document_title,
            customer_name=customer_name,
            subject=subject,
        )

        # Replace the attachment filename with the clean document title.
        for part in message.iter_attachments():
            part.replace_header(
                "Content-Disposition",
                f'attachment; filename="{final_name}"',
            )

    except FileNotFoundError as exc:
        return _failure(
            str(exc),
            "document_not_found",
            recipient_email,
            service,
            document_title,
        )

    except PermissionError as exc:
        return _failure(
            str(exc),
            "document_access_denied",
            recipient_email,
            service,
            document_title,
        )

    except (ValueError, OSError) as exc:
        return _failure(
            str(exc),
            "document_preparation_failed",
            recipient_email,
            service,
            document_title,
        )

    except Exception as exc:
        return _failure(
            "The email attachment could not be prepared.",
            "email_preparation_failed",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    # --------------------------------------------------------
    # Send email
    # --------------------------------------------------------

    try:
        _send_via_gmail(
            sender=sender,
            password=password,
            recipient=recipient_email,
            message=message,
        )

        return {
            "ok": True,
            "success": True,
            "sent": True,
            "configured": True,
            "provider": "Gmail SMTP",
            "message": "The document email was accepted by Gmail SMTP.",
            "recipient_email": recipient_email,
            "service": service,
            "document_title": document_title,
            "attachment": final_name,
        }

    except smtplib.SMTPAuthenticationError as exc:
        return _failure(
            "Gmail rejected the login. Check GMAIL_ADDRESS and the Google app password.",
            "smtp_authentication_failed",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except smtplib.SMTPRecipientsRefused as exc:
        return _failure(
            "Gmail refused the recipient address. Check the customer's email.",
            "recipient_refused",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except smtplib.SMTPConnectError as exc:
        return _failure(
            "Could not establish a connection to Gmail SMTP.",
            "smtp_connection_failed",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except smtplib.SMTPServerDisconnected as exc:
        return _failure(
            "Gmail disconnected before confirming the email operation.",
            "smtp_disconnected",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except smtplib.SMTPException as exc:
        return _failure(
            "Gmail SMTP could not send the document email.",
            "smtp_error",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except (socket.timeout, TimeoutError) as exc:
        return _failure(
            "The connection to Gmail timed out.",
            "smtp_timeout",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except OSError as exc:
        return _failure(
            "A network or system error prevented email delivery.",
            "network_error",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    except Exception as exc:
        return _failure(
            "An unexpected error occurred during email delivery.",
            "unexpected_delivery_error",
            recipient_email,
            service,
            document_title,
            str(exc),
        )

    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except Exception:
                pass


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
            "Gmail credentials were detected. "
            "This check does not send a test email."
        )
    else:
        print(
            "Gmail is not configured correctly. "
            "Check the environment variables shown above."
        )
