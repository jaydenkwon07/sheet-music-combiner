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
    max_upload_bytes: int
    cors_origins: list[str]
    host: str
    port: int


def get_settings() -> Settings:
    default_root = Path(tempfile.gettempdir()) / "smc-web-sessions"
    origins = os.environ.get("SMC_CORS_ORIGINS", "http://localhost:5173")
    return Settings(
        session_root=Path(os.environ.get("SMC_SESSION_ROOT", str(default_root))),
        session_ttl_seconds=int(os.environ.get("SMC_SESSION_TTL", str(6 * 3600))),
        max_upload_bytes=int(os.environ.get("SMC_MAX_UPLOAD", str(50 * 1024 * 1024))),
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        host=os.environ.get("SMC_HOST", "127.0.0.1"),
        port=int(os.environ.get("SMC_PORT", "8000")),
    )
