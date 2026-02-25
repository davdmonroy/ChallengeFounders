#!/usr/bin/env python3
"""SkyMart Fraud Detection Pipeline Runner.

CLI entry point that initialises the database, loads transactions from a
JSON file, and processes them through the fraud detection pipeline.

Usage::

    python scripts/run_pipeline.py [--data-file data/transactions.json] [--delay 0.01]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Ensure the project root is on ``sys.path`` so that ``src.*`` imports work
# when this script is invoked directly from the command line.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.database import create_tables  # noqa: E402
from src.pipeline.ingestion import FraudDetectionPipeline  # noqa: E402


def _configure_logging() -> None:
    """Set up root logger with a clean console format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main() -> None:
    """Parse arguments, initialise the database, and run the pipeline."""
    _configure_logging()

    parser = argparse.ArgumentParser(
        description="Run the SkyMart fraud detection pipeline",
    )
    parser.add_argument(
        "--data-file",
        default="data/transactions.json",
        help="Path to the transactions JSON file (default: data/transactions.json)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.01,
        help="Delay in seconds between transactions to simulate real-time (default: 0.01)",
    )
    args = parser.parse_args()

    print("=== SkyMart Fraud Detection Pipeline ===")
    print("Initializing database...")
    await create_tables()

    print(f"Loading transactions from {args.data_file}...")
    pipeline = FraudDetectionPipeline()
    summary = await pipeline.ingest_from_json(args.data_file, delay_seconds=args.delay)

    total: int = summary["total"]
    flagged: int = summary["flagged"]
    elapsed: float = summary["processing_time_seconds"]
    pct = (flagged / total * 100) if total > 0 else 0.0

    print("\n=== Pipeline Summary ===")
    print(f"Total Transactions: {total}")
    print(f"Flagged as Fraud:   {flagged} ({pct:.1f}%)")
    print(f"Processing Time:    {elapsed:.2f}s")
    print("\nDatabase: fraud_detection.db")
    print("Run 'uvicorn src.api.main:app --reload' to start the dashboard")


if __name__ == "__main__":
    asyncio.run(main())
