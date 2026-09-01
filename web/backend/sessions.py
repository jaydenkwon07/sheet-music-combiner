"""Per-upload temp working directories with TTL cleanup."""

from __future__ import annotations

import asyncio
import shutil
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Awaitable, Callable


class SessionStore:
    def __init__(self, root: Path, ttl_seconds: int) -> None:
        self.root = Path(root)
        self.ttl = ttl_seconds
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> str:
        sid = uuid.uuid4().hex
        (self.root / sid / "in").mkdir(parents=True)
        (self.root / sid / "out").mkdir(parents=True)
        return sid

    def _sub(self, sid: str, name: str) -> Path:
        d = self.root / sid / name
        if not d.is_dir():
            raise KeyError(sid)
        return d

    def in_dir(self, sid: str) -> Path:
        return self._sub(sid, "in")

    def out_dir(self, sid: str) -> Path:
        return self._sub(sid, "out")

    def save_upload(self, sid: str, filename: str, data: bytes) -> None:
        safe = unicodedata.normalize("NFC", Path(filename).name)
        (self.in_dir(sid) / safe).write_bytes(data)

    def clear_results(self, sid: str) -> None:
        out = self.out_dir(sid)
        for child in out.iterdir():
            child.unlink()

    def sweep(self) -> None:
        cutoff = time.time() - self.ttl
        if not self.root.is_dir():
            return
        for child in self.root.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)


async def periodic_sweep(
    store: SessionStore,
    interval_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Sweep expired sessions on a fixed interval, independent of request
    traffic. Session files live under tempfile.gettempdir(), which on
    Render is tmpfs (RAM-backed) — without this, sessions only get swept
    when someone happens to start a new upload, so a quiet-but-long-running
    instance's memory grows unbounded until the container gets OOM-killed."""
    while True:
        store.sweep()
        await sleep(interval_seconds)
