"""Vercel entry point for the SkyMart Fraud Detection API.

On cold start Vercel's filesystem is read-only, so we copy the
pre-populated SQLite database to /tmp (writable) before importing
the app so that SQLAlchemy gets a writable DATABASE_URL.
"""
from __future__ import annotations

import os
import shutil
import sys

# Make sure `src.*` imports resolve from the project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# --- Vercel: redirect DB to /tmp before the app (and its engine) loads ---
if os.environ.get("VERCEL"):
    _src_db = os.path.join(ROOT, "fraud_detection.db")
    _dst_db = "/tmp/fraud_detection.db"
    if not os.path.exists(_dst_db) and os.path.exists(_src_db):
        shutil.copy2(_src_db, _dst_db)
    # Must be set before src.config is imported (Settings reads env at class-creation time)
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/fraud_detection.db")

from src.api.main import app  # noqa: E402  (import after env setup)

__all__ = ["app"]
