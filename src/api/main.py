"""FastAPI application entry point for the SkyMart fraud detection API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.api.routes.alerts import alerts_router
from src.api.routes.metrics import metrics_router
from src.api.routes.transactions import transactions_router
from src.api.websocket import manager
from src.models.database import create_tables
from src.pipeline.ingestion import FraudDetectionPipeline

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler for startup and shutdown tasks.

    Creates database tables on startup and logs readiness.

    Args:
        app: The FastAPI application instance.
    """
    await create_tables()
    logger.info("SkyMart Fraud Detection API started. Database tables ready.")
    yield


app = FastAPI(
    title="SkyMart Fraud Detection API",
    description="Real-time fraud detection dashboard API with WebSocket alerts",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include route modules
app.include_router(alerts_router)
app.include_router(metrics_router)
app.include_router(transactions_router)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time fraud alert streaming.

    Accepts a connection, sends a confirmation message, then keeps the
    connection alive until the client disconnects.

    Args:
        websocket: The incoming WebSocket connection.
    """
    await manager.connect(websocket)
    try:
        await websocket.send_json(
            {"type": "connected", "message": "Connected to fraud alert stream"}
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Pipeline trigger endpoint (inline, not in a separate router)
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    """Request body for the generate-and-ingest endpoint."""
    count: int = 500
    seed: int = 42


class GenerateResponse(BaseModel):
    """Response after a generate job is accepted."""
    status: str
    count: int
    seed: int
    message: str


async def _run_generate_pipeline(count: int, seed: int) -> None:
    """Background task: generate synthetic transactions then ingest them."""
    try:
        import random
        from faker import Faker
        from data.generate_data import generate_dataset
        random.seed(seed)
        Faker.seed(seed)
        transactions = generate_dataset(total=count)
        pipeline = FraudDetectionPipeline(broadcast_callback=manager.broadcast)
        summary = await pipeline.ingest_from_list(transactions)
        logger.info(
            "Generate pipeline done: total=%s flagged=%s",
            summary.get("total"), summary.get("flagged"),
        )
    except Exception:
        logger.exception("Generate pipeline failed")


@app.post("/api/pipeline/generate", response_model=GenerateResponse, tags=["pipeline"])
async def generate_and_ingest(
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> GenerateResponse:
    """Generate synthetic transactions and ingest them through the fraud pipeline.

    Works on Vercel — transactions are generated in memory and processed
    directly without writing any intermediate files.

    Args:
        body: count (number of transactions) and seed (for reproducibility).
        background_tasks: FastAPI background task manager.

    Returns:
        Confirmation with job parameters.
    """
    background_tasks.add_task(_run_generate_pipeline, body.count, body.seed)
    logger.info("Generate pipeline triggered: count=%s seed=%s", body.count, body.seed)
    return GenerateResponse(
        status="started",
        count=body.count,
        seed=body.seed,
        message=f"Generating {body.count} transactions in background (seed={body.seed})",
    )


class PipelineTriggerRequest(BaseModel):
    """Request body for triggering the fraud detection pipeline.

    Attributes:
        data_file: Path to the JSON file containing transactions to ingest.
    """

    data_file: str = "data/transactions.json"


class PipelineTriggerResponse(BaseModel):
    """Response body after pipeline trigger is accepted.

    Attributes:
        status: Pipeline execution status.
        message: Human-readable status description.
    """

    status: str
    message: str


async def _run_pipeline(data_file: str) -> None:
    """Background task that runs the fraud detection pipeline.

    Args:
        data_file: Path to the JSON data file to ingest.
    """
    try:
        pipeline = FraudDetectionPipeline(broadcast_callback=manager.broadcast)
        await pipeline.ingest_from_json(data_file)
        logger.info(f"Pipeline completed for file: {data_file}")
    except Exception:
        logger.exception(f"Pipeline failed for file: {data_file}")


@app.post(
    "/api/pipeline/trigger",
    response_model=PipelineTriggerResponse,
    tags=["pipeline"],
)
async def trigger_pipeline(
    body: PipelineTriggerRequest,
    background_tasks: BackgroundTasks,
) -> PipelineTriggerResponse:
    """Trigger the fraud detection pipeline as a background task.

    Accepts a data file path and launches the ingestion pipeline
    asynchronously so the response returns immediately.

    Args:
        body: Request body specifying the data file to process.
        background_tasks: FastAPI background task manager.

    Returns:
        Confirmation that the pipeline has been triggered.
    """
    background_tasks.add_task(_run_pipeline, body.data_file)
    logger.info(f"Pipeline triggered for file: {body.data_file}")
    return PipelineTriggerResponse(status="started", message="Pipeline triggered")


# ---------------------------------------------------------------------------
# Mount static files for dashboard (must be last to avoid route conflicts)
# Use absolute path so it works both locally and on Vercel.
# ---------------------------------------------------------------------------
dashboard_path = Path(__file__).parent.parent / "dashboard"
if dashboard_path.is_dir():
    app.mount("/", StaticFiles(directory=str(dashboard_path), html=True), name="dashboard")
