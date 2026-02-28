"""
api/certificate.py
FastAPI router for all certificate-related endpoints.
"""
import asyncio
import zipfile
import io
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.models.certificate_model import CertificateStatus
from app.services.csv_service import parse_csv
from app.services.email_service import send_certificate_email
from app.services.pdf_generator import generate_certificate_pdf
from app.utils.helpers import (
    generate_certificate_id,
    get_logger,
    safe_delete,
    utcnow,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/certificates", tags=["Certificates"])


def get_db() -> AsyncIOMotorDatabase:
    """Lazy import to avoid circular dependency."""
    from app.main import db
    return db


# ── Upload template ────────────────────────────────────────────────────────────

@router.post("/upload-template")
async def upload_template(file: UploadFile = File(...)):
    """Upload a certificate background PNG template."""
    if not file.filename.lower().endswith(".png"):
        raise HTTPException(status_code=400, detail="Template must be a PNG file.")

    content = await file.read()
    dest = settings.TEMPLATE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.info(f"Template uploaded: {dest}")
    return {"message": "Template uploaded successfully.", "path": str(dest)}


# ── Start batch processing (SSE streaming) ────────────────────────────────────

@router.post("/send-batch")
async def send_batch(
    csv_file: UploadFile = File(...),
    email_subject: str = Form(...),
    email_body: str = Form(...),
):
    """
    Upload CSV and start batch certificate generation + email sending.
    Returns a batch_id for SSE progress tracking.
    """
    if not csv_file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Must be a CSV file.")

    csv_bytes = await csv_file.read()
    try:
        rows = parse_csv(csv_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="CSV contains no valid rows.")

    db = get_db()
    batch_id = generate_certificate_id()

    # Insert all records as PENDING
    docs = [
        {
            "certificate_id": generate_certificate_id(),
            "batch_id": batch_id,
            "name": name,
            "email": email,
            "status": CertificateStatus.PENDING,
            "file_path": None,
            "error_message": None,
            "created_at": utcnow(),
        }
        for name, email in rows
    ]
    await db.certificates.insert_many(docs)
    logger.info(f"Batch {batch_id}: {len(docs)} records inserted.")

    # Launch background task
    asyncio.create_task(
        _process_batch(batch_id, email_subject, email_body, db)
    )

    return {"batch_id": batch_id, "total": len(docs)}


async def _process_batch(
    batch_id: str,
    email_subject: str,
    email_body: str,
    db: AsyncIOMotorDatabase,
):
    """Background task: generate PDFs and send emails for a batch."""
    cursor = db.certificates.find({"batch_id": batch_id, "status": CertificateStatus.PENDING})
    async for doc in cursor:
        cert_id = doc["certificate_id"]
        name = doc["name"]
        email = doc["email"]
        pdf_path = settings.GENERATED_DIR / f"{cert_id}.pdf"

        try:
            # Generate PDF
            generate_certificate_pdf(name, cert_id, pdf_path)

            # Send email
            await send_certificate_email(name, email, email_subject, email_body, pdf_path)

            # Update DB
            await db.certificates.update_one(
                {"certificate_id": cert_id},
                {"$set": {"status": CertificateStatus.SENT, "file_path": str(pdf_path)}},
            )
            logger.info(f"[{batch_id}] Sent to {email}")

        except Exception as e:
            logger.error(f"[{batch_id}] Failed for {email}: {e}")
            await db.certificates.update_one(
                {"certificate_id": cert_id},
                {"$set": {"status": CertificateStatus.FAILED, "error_message": str(e)}},
            )
        finally:
            # Delay between sends (batch-safe)
            await asyncio.sleep(settings.EMAIL_DELAY_SECONDS)
            # Clean up PDF after send (comment out to keep files)
            safe_delete(pdf_path)


# ── Progress endpoint ──────────────────────────────────────────────────────────

@router.get("/progress/{batch_id}")
async def get_progress(batch_id: str):
    """Return current batch progress counts."""
    db = get_db()
    total = await db.certificates.count_documents({"batch_id": batch_id})
    sent = await db.certificates.count_documents({"batch_id": batch_id, "status": CertificateStatus.SENT})
    failed = await db.certificates.count_documents({"batch_id": batch_id, "status": CertificateStatus.FAILED})
    pending = total - sent - failed

    return {
        "batch_id": batch_id,
        "total": total,
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "done": pending == 0 and total > 0,
    }


# ── Status table endpoint ──────────────────────────────────────────────────────

@router.get("/status/{batch_id}")
async def get_status(batch_id: str, skip: int = 0, limit: int = 100):
    """Paginated list of certificate statuses for a batch."""
    db = get_db()
    cursor = db.certificates.find(
        {"batch_id": batch_id},
        {"_id": 0, "certificate_id": 1, "name": 1, "email": 1, "status": 1, "error_message": 1, "created_at": 1},
    ).skip(skip).limit(limit)
    records = await cursor.to_list(length=limit)
    return {"records": records}


# ── Retry failed ───────────────────────────────────────────────────────────────

@router.post("/retry/{batch_id}")
async def retry_failed(
    batch_id: str,
    email_subject: str = Form(...),
    email_body: str = Form(...),
):
    """Reset failed records to PENDING and re-launch batch processing."""
    db = get_db()
    result = await db.certificates.update_many(
        {"batch_id": batch_id, "status": CertificateStatus.FAILED},
        {"$set": {"status": CertificateStatus.PENDING, "error_message": None}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="No failed records found for this batch.")

    asyncio.create_task(_process_batch(batch_id, email_subject, email_body, db))
    return {"message": f"Retrying {result.modified_count} failed records."}


# ── Download ZIP of all generated certificates ─────────────────────────────────

@router.get("/download-zip/{batch_id}")
async def download_zip(batch_id: str):
    """
    Re-generate all certificates for a batch and return as a ZIP file.
    Note: PDF files are deleted after emailing, so this re-generates them.
    """
    db = get_db()
    cursor = db.certificates.find({"batch_id": batch_id})
    records = await cursor.to_list(length=10000)

    if not records:
        raise HTTPException(status_code=404, detail="Batch not found.")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in records:
            name = doc["name"]
            cert_id = doc["certificate_id"]
            pdf_path = settings.GENERATED_DIR / f"{cert_id}_dl.pdf"
            try:
                generate_certificate_pdf(name, cert_id, pdf_path)
                zf.write(pdf_path, arcname=f"{name.replace(' ', '_')}_certificate.pdf")
            except Exception as e:
                logger.warning(f"Skipped {name} in ZIP: {e}")
            finally:
                safe_delete(pdf_path)

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=certificates_{batch_id[:8]}.zip"},
    )
