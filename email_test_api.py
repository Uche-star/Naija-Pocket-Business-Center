"""
NPBC Gmail SMTP Test API
Architecture:
Render -> email_test_api.py -> email_delivery.py -> Gmail SMTP

Standalone test service.
No Resend.
No Payment API.
No Ada API.
No Workspace.
No database.
"""

import logging
import os
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from email_delivery import send_document_email, configuration_status


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npbc_gmail_test")

app = FastAPI(
    title="NPBC Gmail SMTP Test API",
    description="Standalone Gmail SMTP email delivery test",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def create_test_docx(file_path: str) -> None:
    """Create a small valid DOCX without requiring python-docx."""

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  <w:p>
   <w:r>
    <w:t>{escape("NPBC Gmail SMTP test attachment")}</w:t>
   </w:r>
  </w:p>
  <w:p>
   <w:r>
    <w:t>{escape("If you received this file, Gmail email delivery is working.")}</w:t>
   </w:r>
  </w:p>
  <w:sectPr/>
 </w:body>
</w:document>'''

    content_types_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels"
  ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml"
  ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''

    relationships_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
  Target="word/document.xml"/>
</Relationships>'''

    with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types_xml)
        docx.writestr("_rels/.rels", relationships_xml)
        docx.writestr("word/document.xml", document_xml)


@app.get("/")
def root():
    return {
        "service": "NPBC Gmail SMTP Test API",
        "status": "running",
        "credentials_configured": True,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "test_endpoint": "/test-email?to=recipient@example.com",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/test-email")
def test_email(to: str = Query(..., description="Recipient email address")):
    recipient = to.strip()

    if (
        not recipient
        or "@" not in recipient
        or recipient.startswith("@")
        or recipient.endswith("@")
    ):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Enter a valid recipient email address.",
            },
        )

    temp_path = None

    try:
        # Check the existing email_delivery.py configuration.
        status = configuration_status()

        if isinstance(status, dict):
            logger.info("Email configuration status checked.")

            # Do not expose secrets or passwords in the response.
            configured = status.get("configured")

            if configured is False:
                return JSONResponse(
                    status_code=503,
                    content={
                        "success": False,
                        "error": (
                            "Gmail SMTP configuration is incomplete. "
                            "Check the required environment variables on Render."
                        ),
                    },
                )

        # Create a temporary test attachment.
        with tempfile.NamedTemporaryFile(
            suffix=".docx",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name

        create_test_docx(temp_path)

        logger.info("Starting Gmail email test for recipient: %s", recipient)

        result = send_document_email(
            recipient_email=recipient,
            document_path=temp_path,
            service="NPBC Gmail SMTP Test",
            document_title="Gmail SMTP Test Document",
            customer_name="NPBC Test",
            subject="NPBC Gmail SMTP Test",
        )

        if isinstance(result, dict):
            if result.get("ok") is True:
                logger.info("Gmail test email sent successfully.")

                return {
                    "success": True,
                    "message": "Test email sent successfully.",
                    "recipient": result.get("recipient", recipient),
                    "filename": result.get(
                        "filename",
                        "NPBC_Gmail_SMTP_Test.docx",
                    ),
                    "message_id": result.get("message_id"),
                }

            error_message = result.get("message") or (
                "Gmail email delivery failed. Check the Render logs."
            )

            logger.error("Email delivery reported failure: %s", error_message)

            return JSONResponse(
                status_code=502,
                content={
                    "success": False,
                    "error": str(error_message),
                    "recipient": recipient,
                },
            )

        logger.error("Unexpected response from email_delivery.py.")

        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "error": (
                    "email_delivery.py returned an unexpected response. "
                    "Check its send_document_email() function."
                ),
            },
        )

    except Exception as exc:
        logger.exception("Gmail SMTP test failed.")

        # Return the error message without exposing environment variables.
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(exc) or "Unexpected Gmail SMTP error.",
                "recipient": recipient,
                "hint": (
                    "Check the Render logs and the Gmail SMTP settings "
                    "inside email_delivery.py."
                ),
            },
        )

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("Could not remove temporary test attachment.")


@app.get("/api/test-email/status")
def email_status():
    try:
        status = configuration_status()

        if isinstance(status, dict):
            safe_status = {
                key: value
                for key, value in status.items()
                if not any(
                    word in key.lower()
                    for word in (
                        "password",
                        "secret",
                        "token",
                        "app_password",
                    )
                )
            }
        else:
            safe_status = str(status)

        return {
            "success": True,
            "configuration": safe_status,
        }

    except Exception:
        logger.exception("Unable to check email configuration.")

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Could not check the email configuration.",
            },
        )
