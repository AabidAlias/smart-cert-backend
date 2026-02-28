"""
services/email_service.py
Async email sending via Gmail SMTP with App Password support.
"""
import asyncio
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib

from app.core.config import settings
from app.utils.helpers import get_logger, replace_template_vars

logger = get_logger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


async def send_certificate_email(
    recipient_name: str,
    recipient_email: str,
    subject_template: str,
    body_template: str,
    pdf_path: str | Path,
) -> None:
    """
    Send a single certificate email asynchronously.

    Args:
        recipient_name: Full name of the recipient
        recipient_email: Recipient's email address
        subject_template: Email subject (may contain {{name}})
        body_template: Email body (may contain {{name}})
        pdf_path: Path to the PDF certificate attachment

    Raises:
        Exception: If sending fails
    """
    subject = replace_template_vars(subject_template, recipient_name)
    body = replace_template_vars(body_template, recipient_name)
    pdf_path = Path(pdf_path)

    # Build MIME message
    message = MIMEMultipart()
    message["From"] = settings.GMAIL_ADDRESS
    message["To"] = recipient_email
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    # Attach PDF
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF attachment not found: {pdf_path}")

    with open(pdf_path, "rb") as f:
        part = MIMEApplication(f.read(), _subtype="pdf")
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=f"certificate_{recipient_name.replace(' ', '_')}.pdf",
        )
        message.attach(part)

    # Send via Gmail SMTP
    await aiosmtplib.send(
        message,
        hostname=SMTP_HOST,
        port=SMTP_PORT,
        username=settings.GMAIL_ADDRESS,
        password=settings.GMAIL_APP_PASSWORD,
        start_tls=True,
    )
    logger.info(f"Email sent successfully to {recipient_email}")
