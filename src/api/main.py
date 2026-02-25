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
# ---------------------------------------------------------------------------
dashboard_path = Path("src/dashboard")
if dashboard_path.is_dir():
    app.mount("/", StaticFiles(directory="src/dashboard", html=True), name="dashboard")
