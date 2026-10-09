"""
Naija Pocket Business Center
Gmail SMTP Test API

Required environment variables:
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD

Endpoint:
    GET /test-email?to=recipient@example.com
"""

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

from fastapi import FastAPI, HTTPException, Query
import uvicorn


app = FastAPI(
    title="NPBC Gmail SMTP Test API",
    version="1.0.0",
)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_test_email(recipient: str) -> dict:
    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    if not gmail_address:
        raise RuntimeError(
            "GMAIL_ADDRESS is missing from environment variables."
        )

    if not app_password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing from environment variables."
        )

    recipient = recipient.strip()
    parsed_recipient = parseaddr(recipient)[1]

    if (
        not parsed_recipient
        or parsed_recipient != recipient
        or "@" not in parsed_recipient
        or parsed_recipient.startswith("@")
        or parsed_recipient.endswith("@")
    ):
        raise ValueError("Enter a valid recipient email address.")

    message = EmailMessage()
    message["From"] = gmail_address
    message["To"] = recipient
    message["Subject"] = (
        "NPBC Gmail SMTP Test — Email Delivery Check"
    )

    message.set_content(
        f"""Hello,

This is a test email from Naija Pocket Business Center.

If you received this message, Gmail SMTP email delivery is working.

Test time (UTC):
{datetime.now(timezone.utc).isoformat()}

SMTP server: {SMTP_HOST}
SMTP port: {SMTP_PORT}

This is a test only. No customer documents or payment details are included.

Regards,
Naija Pocket Business Center
"""
    )

    try:
        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=30,
        ) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()

            server.login(
                gmail_address,
                app_password,
            )

            server.send_message(message)

        return {
            "success": True,
            "message": "Test email sent successfully.",
            "from": gmail_address,
            "to": recipient,
            "smtp_server": SMTP_HOST,
            "smtp_port": SMTP_PORT,
        }

    except smtplib.SMTPAuthenticationError:
        raise RuntimeError(
            "Gmail authentication failed. Check GMAIL_ADDRESS "
            "and GMAIL_APP_PASSWORD."
        )

    except smtplib.SMTPRecipientsRefused:
        raise RuntimeError(
            "Gmail refused the recipient address. Check the email address."
        )

    except (smtplib.SMTPException, OSError):
        raise RuntimeError(
            "Gmail SMTP could not complete the email test. "
            "Check the Render logs for more information."
        )


@app.get("/")
def home():
    configured = bool(
        os.getenv("GMAIL_ADDRESS", "").strip()
        and os.getenv("GMAIL_APP_PASSWORD", "").strip()
    )

    return {
        "service": "NPBC Gmail SMTP Test API",
        "status": "running",
        "credentials_configured": configured,
        "smtp_server": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "test_endpoint": "/test-email?to=recipient@example.com",
    }


@app.get("/test-email")
def test_email(
    to: str = Query(
        ...,
        description="Email address to receive the test message.",
    )
):
    try:
        return send_test_email(to)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
