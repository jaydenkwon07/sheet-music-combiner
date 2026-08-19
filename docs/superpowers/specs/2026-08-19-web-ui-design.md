# Sheet Music Assembler — Web UI Design

**Date:** 2026-08-19
**Status:** Approved (design), pending implementation plan
**Type:** Architectural — new web subsystem wrapping the existing CLI

## Purpose

Give the existing `scripts/assemble_sheet_music.py` CLI a visual, local web UI:
drag numbered snippet PNGs into a browser page, click Assemble, preview the
paginated result, download the PDF. It exists for easier access than the
terminal/subagent flow — same deterministic engine, friendlier front door.

## Non-negotiable constraint

The image-processing logic lives **only** in `scripts/assemble_sheet_music.py`
and is not reimplemented anywhere. Per the project contract ("two parts that
must not merge"), the web backend is a **third thin consumer** of that logic,
alongside the terminal CLI and the `sheet-music-assembler` subagent. It
**imports and calls** `assemble()` / `discover_pieces()` and the typed
exceptions — it never re-derives balancing, cleanup, scaling, or I/O.

`scripts/assemble_sheet_music.py` requires **zero changes**. The existing 56
tests stay green and untouched.

## Requirements (locked during brainstorming)

- **Hosting:** local-first now (`uv run`, open localhost), structured so it can
  be deployed later without rework. No deploy config built yet.
- **Stack:** FastAPI backend + React/Vite frontend, mirroring the sibling
  `yt-to-gif` project's `backend/` + `frontend/` split.
- **Backend/frontend interaction:** session-based (Approach B). Upload once →
  a server-side working dir + session id; re-run assembly against that session
  with different params **without re-uploading**.
- **Features exposed in the UI:**
  - Core flow: upload → assemble → preview → download PDF.
  - Manual page-split override (also the only way to resolve N=7).
  - Margin control (default 22).
  - Warnings panel (surfacing the script's own warnings).
- **Explicitly out (YAGNI):** insert-a-piece, accounts/auth, multi-song batch,
  persistent history, Docker/deploy config, changes to the CLI subagent.

## Repository layout

Web code is purely additive under `web/`. Nothing under `scripts/` or `tests/`
moves or changes.

```
sheet-music-combiner/
  scripts/assemble_sheet_music.py   # UNCHANGED — single source of truth
  tests/                            # UNCHANGED — 56 tests stay green
  web/
    backend/
      app.py              # FastAPI app: route definitions only
      assembler_bridge.py # sys.path shim + run_assemble(); the ONLY import of the script
      sessions.py         # temp session-dir create / lookup / TTL cleanup
      config.py           # env-driven settings (host, port, TTL, max upload, CORS)
      tests/test_api.py   # FastAPI TestClient tests
    frontend/             # React + Vite (mirrors yt-to-gif)
      package.json
      vite.config.ts
      index.html
      src/
        main.tsx
        App.tsx
        api.ts            # typed fetch wrappers, configurable API base
        components/       # DropZone, Controls, WarningsPanel, PagePreview
```

Backend Python deps (`fastapi`, `uvicorn`, `python-multipart`) are added to the
**root `pyproject.toml`** as an optional dependency group `web`, so the server
runs in the same environment that already provides numpy/Pillow and can import
the assembler module directly.

Launch command: `uv run uvicorn web.backend.app:app --reload`.

## The import bridge (zero script changes)

`web/backend/assembler_bridge.py` is the single seam to the image logic:

1. Prepends the repo's `scripts/` directory to `sys.path` — exactly what
   pytest already does via `pythonpath = ["scripts"]`. No `__init__.py` is
   added to `scripts/`; the existing tests' `from assemble_sheet_music import …`
   imports keep working unchanged.
2. Imports `assemble`, `discover_pieces`, `DiscoveryError`, `PageBalanceError`
   from `assemble_sheet_music`.
3. Exposes thin functions (`validate_upload(...)`, `run_assemble(...)`) that
   call the script and translate `DiscoveryError` / `PageBalanceError` /
   `ValueError` into typed result objects the routes map to HTTP status codes.

No image logic appears in the bridge — it only marshals arguments and
exceptions.

## Backend — session model + endpoints

A **session** is a temp working directory holding the uploaded snippet PNGs and
(after assembly) a results subdir. Sessions have a TTL; a lightweight sweep
removes directories older than the configured TTL. Nothing persists long-term,
matching the tool's stateless, deterministic nature.

### `POST /api/session`
- Accepts a multipart upload of the snippet PNGs.
- NFC-normalizes each filename, writes files into a fresh session dir.
- Auto-derives the prefix: the common `{prefix}` before the trailing `_{n}.png`.
- Runs `discover_pieces()` to validate a gap-free 1-based sequence.
- **200** → `{ session_id, prefix, num_pieces, files: [...] }`.
- **422** → `{ error }` with the exact missing/duplicate message from
  `DiscoveryError` (e.g. "Missing: [4]"). The client cannot assemble until
  validation passes — mirrors the CLI's refusal to proceed silently.

### `POST /api/session/{id}/assemble`
- Body: `{ margin?: int, pages?: string }` (`pages` is the explicit
  `"5,5,4"`-style override; both optional).
- Calls `assemble()` against the session's snippet dir into a results subdir,
  passing `prefix`, `margin`, and `pages_spec`.
- **200** → `{ counts, uniform_scale, warnings, page_urls: [...], pdf_url }`.
- **422** on N=7 `PageBalanceError` →
  `{ needs_split: true, message, options }` so the UI can prompt for a split.
- **422** on `ValueError` (e.g. bad `--pages` sum) → `{ error }`.

### `GET /api/session/{id}/file/{name}`
- Serves a result artifact by name from the session's results dir: a page PNG
  (`{prefix}_page{n}.png`) for preview, or the `{prefix}.pdf` for download.
- Path is constrained to the session results dir (no traversal).

### Config (`config.py`)
Env-driven, with local-friendly defaults: host, port, session TTL, max upload
size, allowed CORS origins, and the temp root. No absolute paths hardcoded.

## Frontend — single-page flow

One screen, top to bottom:

1. **Drop zone** — drag the numbered PNGs (or click to pick). On drop, calls
   `POST /api/session`. Displays the derived prefix (editable) and a validation
   line: green "9 pieces, 1–9 ✓" or red "missing #4" from the 422.
2. **Controls** — a margin number field (default 22) and an optional page-split
   text field, with an **Assemble** button. Enabled only after a valid session.
3. **Result** —
   - Per-page breakdown ("2 pages: 5, 4") and the uniform scale.
   - **Warnings panel** listing the script's warnings (stray marks removed,
     border lines stripped, insert rescale — whatever `assemble()` returned).
   - **Page preview**: the result page PNGs, scrollable, at a legible size.
   - **Download PDF** button (hits the file endpoint for the PDF).

Re-running with a changed margin or split calls `/assemble` again against the
same session — **no re-upload**.

`api.ts` centralizes fetch calls and reads the API base URL from config so the
frontend can point at a hosted backend later.

## N=7 and validation handling

- **Missing / duplicate numbers** are surfaced inline at upload time, before
  Assemble is possible — the UI mirrors the CLI's hard refusal to proceed.
- **N=7** (the one page-balancing gap the formula cannot resolve): the assemble
  call returns `needs_split`; the UI reveals the page-split field pre-filled
  with the two options and prompts the user to choose. The tool never guesses,
  matching the CLI/subagent contract.

## Testing

- **Backend** (`web/backend/tests/test_api.py`): `pytest` + FastAPI
  `TestClient`, reusing the synthetic-PNG fixture approach the existing suite
  uses (small images with fake staff lines). Cases:
  - Happy path: upload valid sequence → 200 with prefix + count → assemble →
    200 with counts, warnings, page URLs, and a fetchable PDF.
  - Missing number → upload 422 with the gap named.
  - Duplicate number → upload 422.
  - N=7 → assemble 422 `needs_split`; re-assemble with `pages` override → 200.
  - Margin passthrough → assemble honors a non-default margin.
  - File endpoint serves a page PNG and the PDF; rejects path traversal.
- **Script tests**: untouched; still 56 green.
- **Frontend**: kept light — one smoke test that the flow renders and wires the
  primary actions. Visual correctness is the human eyeball check (the same role
  the subagent's sanity-check step plays for the terminal flow).

## Deploy-later hooks (built now, not deployed)

- All config via env with local defaults; no absolute paths.
- CORS origins and frontend API base URL configurable.
- Session dirs under a configurable temp root with TTL cleanup.
- No Dockerfile, no cloud config, no auth — those are a later, separate task.

## Documentation

Add a short section to `CLAUDE.md` documenting the web tier as the **third thin
consumer** of the assembler logic (import-only, never reimplements), so a future
session understands the boundary. The "two parts that must not merge" rule is
preserved and extended, not broken.

## Out of scope (restated)

Insert-a-piece UI, accounts/auth, multi-song batching, persistent history,
deployment/Docker config, and any change to `scripts/assemble_sheet_music.py`,
the existing tests, or the `sheet-music-assembler` subagent.
