"""
Naija Pocket Business Center (NPBC)
Complete Gmail Email Delivery API - EMAIL ONLY, NO PAYMENT

Provider: Gmail SMTP
Env vars needed on Render:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

Deploy: uvicorn main:app --host 0.0.0.0 --port 10000
"""

import os
import re
import smtplib
import mimetypes
import tempfile
from pathlib import Path
from email.message import EmailMessage
from email.utils import formataddr
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
ALLOWED_EXTENSIONS = {".docx", ".pdf", ".xlsx", ".pptx", ".txt"}
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024
DEFAULT_SUBJECT = "Your NPBC Document Is Ready"

def configuration_status():
    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")
    return {
        "provider": "Gmail SMTP",
        "configured": bool(gmail_address and gmail_password),
        "gmail_address_configured": bool(gmail_address),
        "gmail_app_password_configured": bool(gmail_password),
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "security": "STARTTLS",
    }

def escape_html(value):
    value = str(value or "")
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")

def safe_filename(value):
    name = Path(str(value or "")).name
    name = re.sub(r"[\r\n\x00]", "", name)
    return name or "NPBC_Document"

def build_email_content(service, document_title, customer_name):
    service = str(service or "NPBC Service").strip()
    document_title = str(document_title or "Your Document").strip()
    customer_name = str(customer_name or "Valued Customer").strip()

    plain_text = (
        f"Hello {customer_name},\n\n"
        "Your document from Naija Pocket Business Center is ready.\n\n"
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
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;"><tr><td align="center" style="padding:24px 10px;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border:1px solid #e5e5e5;">
<tr><td align="center" style="background:#111111;padding:28px 20px;">
<p style="margin:0;color:#d4af37;font-size:23px;font-weight:bold;letter-spacing:1px;">Naija Pocket Business Center</p>
<p style="margin:10px 0 0;color:#ffffff;font-size:13px;">Fast • Convenient • Open 24/7</p>
</td></tr>
<tr><td style="padding:28px 24px;color:#333333;">
<p style="margin:0 0 18px;font-size:16px;">Hello {safe_customer},</p>
<p style="font-size:15px;line-height:1.7;">Your document from Naija Pocket Business Center is ready. Please find your document attached to this email.</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#faf8f0;border:1px solid #eadca7;"><tr><td style="padding:16px;">
<p style="margin:0 0 10px;font-size:14px;"><strong>Service:</strong> {safe_service}</p>
<p style="margin:0;font-size:14px;"><strong>Document:</strong> {safe_title}</p>
</td></tr></table>
<p style="margin:22px 0 0;font-size:14px;">The document is attached to this email. You can open the attachment to view your file.</p>
<p style="margin:24px 0 0;font-size:14px;">Thank you for choosing Naija Pocket Business Center.</p>
</td></tr>
<tr><td align="center" style="background:#111111;padding:18px 15px;">
<p style="margin:0;color:#d4af37;font-size:13px;">Naija Pocket Business Center</p>
<p style="margin:8px 0 0;color:#ffffff;font-size:12px;">Fast • Convenient • Open 24/7</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""
    return plain_text, html_text

def send_document_email(recipient_email, document_path, service="NPBC Service", document_title="Your Document", customer_name="Valued Customer", subject=None):
    recipient_email = str(recipient_email or "").strip()
    if not recipient_email or "\r" in recipient_email or "\n" in recipient_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient_email):
        return {"ok": False, "message": "A valid recipient email is required.", "recipient": recipient_email}

    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "").strip().replace(" ", "")

    if not gmail_address or not gmail_password:
        return {"ok": False, "message": "Gmail not configured. Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD in Render.", "recipient": recipient_email}

    if not document_path:
        return {"ok": False, "message": "No document was provided.", "recipient": recipient_email}

    path = Path(document_path)
    if not path.is_file():
        return {"ok": False, "message": "Document file not found.", "recipient": recipient_email}

    filename = safe_filename(path.name)
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return {"ok": False, "message": "Unsupported type. Allowed: DOCX, PDF, XLSX, PPTX, TXT.", "recipient": recipient_email, "filename": filename}

    try:
        file_size = path.stat().st_size
    except OSError:
        return {"ok": False, "message": "Unable to inspect file.", "recipient": recipient_email, "filename": filename}

    if file_size <= 0:
        return {"ok": False, "message": "File is empty.", "recipient": recipient_email, "filename": filename}
    if file_size > MAX_ATTACHMENT_SIZE:
        return {"ok": False, "message": "File exceeds 25 MB limit.", "recipient": recipient_email, "filename": filename}

    try:
        with path.open("rb") as f:
            attachment_data = f.read(MAX_ATTACHMENT_SIZE + 1)
        if len(attachment_data) > MAX_ATTACHMENT_SIZE:
            return {"ok": False, "message": "File exceeds 25 MB limit.", "recipient": recipient_email, "filename": filename}

        mime_type, _ = mimetypes.guess_type(filename)
        if mime_type and "/" in mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = ("application", "octet-stream")

        message = EmailMessage()
        message["From"] = formataddr(("Naija Pocket Business Center", gmail_address))
        message["To"] = recipient_email
        message["Subject"] = str(subject).strip() if subject and str(subject).strip() else DEFAULT_SUBJECT

        plain_text, html_text = build_email_content(service=service, document_title=document_title, customer_name=customer_name)
        message.set_content(plain_text)
        message.add_alternative(html_text, subtype="html")
        message.add_attachment(attachment_data, maintype=maintype, subtype=subtype, filename=filename)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(gmail_address, gmail_password)
            smtp.send_message(message)

        return {"ok": True, "message": "Email sent successfully via Gmail SMTP.", "recipient": recipient_email, "filename": filename, "message_id": message.get("Message-ID")}

    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "message": "Gmail authentication failed. Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD.", "recipient": recipient_email, "filename": filename}
    except Exception:
        return {"ok": False, "message": "Unexpected delivery error. Check Render logs.", "recipient": recipient_email, "filename": filename}

# --------------------------------------------------
# FASTAPI APP - EMAIL ONLY
# --------------------------------------------------
app = FastAPI(title="NPBC Email Delivery API - EMAIL ONLY", version="v1-email-only")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {
        "status": "NPBC Email Delivery API running - EMAIL ONLY MODE, NO PAYMENT",
        "gmail": configuration_status(),
        "endpoints": {
            "check_config": "/config",
            "send_email": "POST /send-email"
        }
    }

@app.get("/config")
def check_config():
    return configuration_status()

@app.post("/send-email")
async def send_email_endpoint(
    recipient_email: str = Form(...),
    customer_name: str = Form("Valued Customer"),
    service: str = Form("NPBC Service"),
    document_title: str = Form("Your Document"),
    subject: str = Form(None),
    file: UploadFile = File(...)
):
    if not recipient_email or "@" not in recipient_email:
        raise HTTPException(status_code=400, detail="Valid customer email required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type {suffix}. Allowed: {ALLOWED_EXTENSIONS}")

    temp_dir = tempfile.gettempdir()
    temp_path = Path(temp_dir) / safe_filename(file.filename)

    try:
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="File is empty")
        if len(content) > MAX_ATTACHMENT_SIZE:
            raise HTTPException(status_code=400, detail="File exceeds 25MB limit")

        with open(temp_path, "wb") as f:
            f.write(content)

        result = send_document_email(
            recipient_email=recipient_email,
            document_path=str(temp_path),
            service=service,
            document_title=document_title,
            customer_name=customer_name,
            subject=subject
        )

        try:
            if temp_path.exists():
                temp_path.unlink()
        except:
            pass

        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("message"))

        return {
            "ok": True,
            "message": f"Email sent to {recipient_email}",
            "recipient": recipient_email,
            "filename": result.get("filename"),
            "service": service,
            "document_title": document_title
        }

    except HTTPException:
        raise
    except Exception as e:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except:
            pass
        raise HTTPException(status_code=500, detail=f"Delivery error: {str(e)}")
