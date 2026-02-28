"""
services/email_service.py
Async email sending via Resend API (HTTP-based, works on Render free plan).
"""
import base64
import resend
from pathlib import Path

from app.core.config import settings
from app.utils.helpers import get_logger, replace_template_vars

logger = get_logger(__name__)


async def send_certificate_email(
    recipient_name: str,
    recipient_email: str,
    subject_template: str,
    body_template: str,
    pdf_path: str | Path,
) -> None:
    """
    Send a single certificate email via Resend API.
    """
    subject = replace_template_vars(subject_template, recipient_name)
    body    = replace_template_vars(body_template, recipient_name)
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Read and base64 encode the PDF
    with open(pdf_path, "rb") as f:
        pdf_data = base64.b64encode(f.read()).decode("utf-8")

    filename = f"certificate_{recipient_name.replace(' ', '_')}.pdf"

    resend.api_key = settings.RESEND_API_KEY

    params = {
        "from": f"Certificates <onboarding@resend.dev>",
        "to": [recipient_email],
        "subject": subject,
        "text": body,
        "attachments": [
            {
                "filename": filename,
                "content": pdf_data,
            }
        ],
    }

    resend.Emails.send(params)
    logger.info(f"Email sent via Resend to {recipient_email}")