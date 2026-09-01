"""FastAPI wiring: routes only. All image logic goes through assembler_bridge."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from web.backend import assembler_bridge as br
from web.backend.config import get_settings
from web.backend.sessions import SessionStore

settings = get_settings()
store = SessionStore(settings.session_root, settings.session_ttl_seconds)

app = FastAPI(title="Sheet Music Assembler")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AssembleRequest(BaseModel):
    prefix: str
    margin: int = 22
    pages: str | None = None


def _safe_basename(filename: str | None) -> str | None:
    if not filename:
        return None
    name = Path(filename).name
    if name in ("", ".", ".."):
        return None
    return name


@app.post("/api/session")
async def create_session(files: list[UploadFile] = File(...)):
    store.sweep()
    sid = store.create()
    names: list[str] = []
    total_bytes = 0
    for f in files:
        if _safe_basename(f.filename) is None:
            continue
        data = await f.read()
        total_bytes += len(data)
        if total_bytes > settings.max_upload_bytes:
            raise HTTPException(413, "Upload exceeds maximum allowed size")
        store.save_upload(sid, f.filename, data)
        names.append(f.filename)
    if not names:
        raise HTTPException(422, "No files with a valid filename were uploaded")
    prefix = br.derive_prefix(names)
    if prefix is None:
        raise HTTPException(
            422,
            "Could not derive one {prefix}_{n}.{png,jpg,jpeg,pdf} name from the uploads",
        )
    result = br.validate_upload(store.in_dir(sid), prefix)
    if not result.ok:
        raise HTTPException(422, result.error)
    return {
        "session_id": sid,
        "prefix": result.prefix,
        "num_pieces": result.num_pieces,
        "files": result.files,
    }


@app.post("/api/session/{sid}/assemble")
def assemble_session(sid: str, req: AssembleRequest):
    try:
        in_dir = store.in_dir(sid)
        store.clear_results(sid)
        out_dir = store.out_dir(sid)
    except KeyError:
        raise HTTPException(404, "unknown session")
    result = br.run_assemble(in_dir, req.prefix, out_dir, req.margin, req.pages)
    if result.needs_split:
        raise HTTPException(
            422, detail={"needs_split": True, "message": result.error, "options": result.options}
        )
    if not result.ok:
        raise HTTPException(422, detail={"error": result.error})
    return {
        "counts": result.counts,
        "uniform_scale": result.uniform_scale,
        "warnings": result.warnings,
        "page_urls": [f"/api/session/{sid}/file/{n}" for n in result.page_files],
        "pdf_url": f"/api/session/{sid}/file/{result.pdf_file}",
    }


@app.get("/api/session/{sid}/file/{name}")
def get_file(sid: str, name: str):
    try:
        out_dir = store.out_dir(sid).resolve()
    except KeyError:
        raise HTTPException(404, "unknown session")
    target = (out_dir / name).resolve()
    if out_dir != target.parent or not target.is_file():
        raise HTTPException(404, "not found")
    # Results are regenerated in place on each re-assemble under the same
    # session/filename, so the browser must never serve a stale cached copy.
    return FileResponse(target, headers={"Cache-Control": "no-store"})
