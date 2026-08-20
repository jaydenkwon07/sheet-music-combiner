# Sheet Music Assembler — Deployment (Vercel + Render)

**Date:** 2026-08-20
**Status:** Approved (design), pending implementation plan
**Type:** Infrastructure — deploy configuration for the existing web UI, no application code changes

## Purpose

Make the web UI (`web/`) reachable at a public URL so it can be shared with
one other person, without Jayden manually running two local dev servers.
Currently it only runs via `uv run uvicorn ...` + `npm run dev` on localhost.

## Why not host everything on Vercel

Vercel's Python support is serverless functions: each request can land on a
different, short-lived instance with an ephemeral `/tmp` that isn't reliably
shared across requests. The existing backend session model (upload once →
tweak margin/page-split → re-assemble → download, all against the same
on-disk session directory, no re-upload) depends on a persistent filesystem
across multiple requests. Rebuilding that around external storage (e.g.
Vercel Blob) would be real application rework, not config. Vercel serverless
functions also impose tighter request-body size limits than a normal host,
which cuts against multi-image uploads. None of this is a good trade for a
personal tool being shared with one person.

## Chosen approach: split hosting

- **Frontend** (static React/Vite build) → **Vercel**. This is what Vercel is
  built for: a single shareable link, fast CDN, git-push-to-deploy.
- **Backend** (FastAPI + numpy/Pillow, session directories on local disk) →
  **Render**, free web-service tier. A real single persistent instance with a
  normal filesystem — the current session/TTL design works completely
  unmodified.

This is not a workaround: the existing web-UI design doc
(`docs/superpowers/specs/2026-08-19-web-ui-design.md`) already built
"deploy-later hooks" for exactly this split — `SMC_CORS_ORIGINS` and the
frontend's `VITE_API_BASE` are already configurable env vars with no
absolute paths hardcoded anywhere. This spec only adds deploy configuration
on top of them.

## Non-negotiable constraint

**No code changes** to `scripts/`, `tests/`, `web/backend/*.py`, or
`web/frontend/src/*`. This is deploy configuration only, added as new files
alongside the existing app. If a code change turns out to be required, that's
a signal to stop and re-scope, not to improvise inline.

## Topology

```
Browser
  │
  ▼
Vercel (static, web/frontend, Vite build)
  │  fetch() to VITE_API_BASE
  ▼
Render (single persistent web service, web/backend via FastAPI/uvicorn)
  │  local disk session dirs, TTL sweep (unchanged)
  ▼
scripts/assemble_sheet_music.py (unchanged, imported by assembler_bridge.py)
```

## Backend deploy config (Render)

New file `render.yaml` at the repo root (Render "Blueprint" — checked into
git, avoids manual dashboard configuration and is reproducible):

```yaml
services:
  - type: web
    name: sheet-music-combiner-backend
    runtime: python
    plan: free
    buildCommand: pip install uv && uv sync --extra web --frozen
    startCommand: uv run uvicorn web.backend.app:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: SMC_CORS_ORIGINS
        value: https://<your-vercel-project>.vercel.app
```

The build/start commands replicate the exact `uv sync` / `uv run uvicorn`
invocations already used locally and in the README — no pip packaging
workaround needed since `uv` already handles this repo's dependency-only
`pyproject.toml` (no `[build-system]` table) correctly today.

`SMC_SESSION_ROOT`, `SMC_SESSION_TTL`, `SMC_MAX_UPLOAD` keep their existing
code defaults (see `web/backend/config.py`) — no override needed. Render's
free-tier filesystem persists for the life of the running instance (what the
session model needs) and resets on redeploy/cold-start after idling, which
is a superset of what the existing TTL sweep already does, not a new
failure mode.

## Frontend deploy config (Vercel)

No new file — configured via the Vercel dashboard/CLI when the project is
linked, since Vite is auto-detected:

- Root Directory: `web/frontend`
- Build Command: `npm run build` (default)
- Output Directory: `dist` (default)
- Env var: `VITE_API_BASE` = `https://<your-render-service>.onrender.com`

The dev-only `/api` proxy in `vite.config.ts` only affects `npm run dev` and
is inert in the production build — no change needed there.

## Deploy sequencing

The Render URL isn't known until that service is first created, and the
Vercel URL isn't known until that project is first created — so:

1. Create the Render service (via `render.yaml` blueprint) with a placeholder
   or blank `SMC_CORS_ORIGINS`; note the assigned `*.onrender.com` URL.
2. Create the Vercel project pointed at `web/frontend`, set `VITE_API_BASE`
   to that Render URL; note the assigned `*.vercel.app` URL.
3. Set `SMC_CORS_ORIGINS` on Render to the real Vercel URL and redeploy the
   backend.

## Known limitations (accepted, not built around)

- Render's free tier sleeps after ~15 min idle; the next request cold-starts
  in roughly 30–60 seconds. Acceptable for occasional shared use by two
  people; not something this spec adds UI/loading-state handling for.
- No custom domain, no CI beyond each platform's default git-push-to-deploy,
  no Docker, no multi-region. Matches the original web-UI spec's "out of
  scope" list.

## Testing / verification

No new automated tests — this is infrastructure, not application logic.
Verification is manual, end-to-end, after both services are live:

- Open the Vercel URL, confirm the app loads.
- Upload a real numbered snippet set, confirm session creation succeeds
  (backend reachable, CORS not blocking).
- Assemble, confirm page previews load and the PDF downloads.
- Re-assemble with a changed margin against the same session (no re-upload),
  confirm the preview updates (cache-busting still works cross-origin).
- Trigger the N=7 case, confirm the split prompt still round-trips.

## Out of scope (restated)

Auth/access control (explicitly declined), custom domain, Docker,
multi-region, CI/CD beyond default git-push-to-deploy, any change to
`scripts/assemble_sheet_music.py`, the subagent, or existing tests.
