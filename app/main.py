"""
app/main.py
FastAPI application factory and startup configuration.
"""
import logging
from contextlib import asynccontextmanager

import motor.motor_asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── MongoDB client (module-level, shared across requests) ──────────────────────
client: motor.motor_asyncio.AsyncIOMotorClient | None = None
db: motor.motor_asyncio.AsyncIOMotorDatabase | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and tear down resources on startup/shutdown."""
    global client, db
    logger.info("Connecting to MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB_NAME]

    # Ensure indexes
    await db.certificates.create_index("batch_id")
    await db.certificates.create_index("certificate_id", unique=True)
    await db.certificates.create_index("status")
    logger.info(f"Connected to MongoDB: {settings.MONGO_DB_NAME}")

    yield  # App is running

    logger.info("Shutting down: closing MongoDB connection.")
    client.close()


# ── App factory ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Smart Certificate Automation System",
    description="Generate and email personalized certificates at scale.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS middleware ────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global error handler ───────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# ── Include routers ────────────────────────────────────────────────────────────
from app.api.certificate import router as certificate_router
app.include_router(certificate_router)


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "Smart Certificate Automation"}
