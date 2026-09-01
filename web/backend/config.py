"""Env-driven settings for the web backend. Local-friendly defaults."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    session_root: Path
    session_ttl_seconds: int
    session_sweep_interval_seconds: int
    max_upload_bytes: int
    max_assemble_megapixels: float
    cors_origins: list[str]
    host: str
    port: int


def get_settings() -> Settings:
    # session_root defaults to tempfile.gettempdir(), which on Render (and
    # most containers) is tmpfs — RAM-backed, not disk. session_ttl and the
    # sweep interval are kept short by default so leftover session files
    # from past uploads can't accumulate and exceed the instance's memory
    # limit; see periodic_sweep in sessions.py, which runs on this interval
    # regardless of whether any new upload traffic arrives.
    default_root = Path(tempfile.gettempdir()) / "smc-web-sessions"
    origins = os.environ.get("SMC_CORS_ORIGINS", "http://localhost:5173")
    return Settings(
        session_root=Path(os.environ.get("SMC_SESSION_ROOT", str(default_root))),
        session_ttl_seconds=int(os.environ.get("SMC_SESSION_TTL", str(3600))),
        session_sweep_interval_seconds=int(
            os.environ.get("SMC_SESSION_SWEEP_INTERVAL", str(900))
        ),
        max_upload_bytes=int(os.environ.get("SMC_MAX_UPLOAD", str(50 * 1024 * 1024))),
        # Pre-allocation guard so an oversized job returns a clean 413 instead
        # of OOM-killing the instance. Default sized for the 512 MB free tier
        # (PDF pieces render at 300 DPI, ~24 MP each); raise it after upgrading
        # to a larger instance. See assemble_sheet_music.estimate_megapixels.
        max_assemble_megapixels=float(os.environ.get("SMC_MAX_ASSEMBLE_MP", "40")),
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        host=os.environ.get("SMC_HOST", "127.0.0.1"),
        port=int(os.environ.get("SMC_PORT", "8000")),
    )
