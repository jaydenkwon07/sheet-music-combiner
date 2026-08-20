# Vercel + Render Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing web UI reachable at a public URL (frontend on Vercel, backend on Render) so it can be shared with one other person, with zero changes to application code.

**Architecture:** Add a Render Blueprint (`render.yaml`) that runs the existing `uv sync --extra web` / `uv run uvicorn web.backend.app:app` commands unmodified on a persistent instance. Configure a Vercel project (via dashboard, no new file needed — Vite auto-detects) pointed at `web/frontend`, using the already-existing `VITE_API_BASE` env var to reach the Render backend. Wire `SMC_CORS_ORIGINS` on Render to the live Vercel URL once both are up.

**Tech Stack:** Render Blueprint (`render.yaml`), Vercel dashboard project config, existing FastAPI/uvicorn + Vite/React app (unchanged).

**Spec:** `docs/superpowers/specs/2026-08-20-vercel-render-deploy-design.md`

## Global Constraints

- **No changes to `scripts/`, `tests/`, `web/backend/*.py`, or `web/frontend/src/*`.** This plan only adds deploy configuration and README docs.
- **No auth/access control** — explicitly declined in the design.
- **No custom domain, no Docker, no CI beyond each platform's default git-push-to-deploy.**
- Backend build/start commands must exactly replicate what already works locally (`uv sync --extra web --frozen`, `uv run uvicorn web.backend.app:app`) — no alternate pip-based packaging.
- Repo already has a GitHub remote (`https://github.com/jaydenkwon07/sheet-music-combiner.git`) — both Render and Vercel connect to it via "import from GitHub" in their dashboards, no new remote needed.
- Neither the Vercel nor Render CLI is installed locally, and creating accounts/authorizing GitHub access on either platform requires the user's own browser-based login — those specific steps cannot be executed by an agent and are written as explicit manual instructions for the user to perform, with the agent verifying the result afterward via `curl`.

---

### Task 1: Render Blueprint for the backend

**Files:**
- Create: `render.yaml` (repo root)

**Interfaces:**
- Consumes: nothing new — reuses `web/backend/app.py`'s existing `app` object and `web/backend/config.py`'s existing `SMC_CORS_ORIGINS` env var.
- Produces: a Blueprint file Render's dashboard reads when the user creates a new Blueprint-based service (Task 3).

- [ ] **Step 1: Write `render.yaml`**

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
        value: http://localhost:5173
```

`SMC_CORS_ORIGINS` starts at the local-dev default; Task 5 updates it to the
real Vercel URL once that exists. Render auto-detects `.python-version`
(already `3.12` in this repo) for the Python runtime version — no extra
config needed.

- [ ] **Step 2: Verify the build command works from a clean sync**

Run: `uv sync --extra web --frozen`
Expected: exits 0, no lockfile drift (if this fails, the lockfile is out of
sync with `pyproject.toml` and must be fixed before deploying — run
`uv lock` and investigate why, don't just accept a changed lockfile blindly).

- [ ] **Step 3: Verify the start command boots and serves**

Run in the background: `uv run uvicorn web.backend.app:app --host 0.0.0.0 --port 8001`
Then: `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/docs`
Expected: `200`
Stop the background server afterward.

- [ ] **Step 4: Commit**

```bash
git add render.yaml
git commit -m "Add Render blueprint for the backend"
```

---

### Task 2: Document deployment in README

**Files:**
- Modify: `README.md` (add a "## Deploying" section after "## Web UI", before "## Tests")

**Interfaces:** none (docs only).

- [ ] **Step 1: Add the section**

Insert after the existing "## Web UI" section (which ends with "...doesn't
need a re-upload.") and before "## Tests":

```markdown
## Deploying

The web UI splits across two hosts: the frontend (static Vite build) on
Vercel, the backend (FastAPI + numpy/Pillow, needs a persistent filesystem
for session dirs) on Render. See
`docs/superpowers/specs/2026-08-20-vercel-render-deploy-design.md` for why.

1. **Backend on Render:** New → Blueprint → connect this GitHub repo. Render
   reads `render.yaml` and creates the service. Note the assigned
   `https://<name>.onrender.com` URL.
2. **Frontend on Vercel:** New Project → import this GitHub repo → set Root
   Directory to `web/frontend` (Vite is auto-detected) → set the env var
   `VITE_API_BASE` to the Render URL from step 1 → deploy. Note the assigned
   `https://<name>.vercel.app` URL.
3. **Wire CORS:** back on Render, set `SMC_CORS_ORIGINS` to the Vercel URL
   from step 2 and redeploy the backend service.

Render's free tier sleeps after ~15 min idle and cold-starts (~30-60s) on
the next request — expected, not a bug.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Document Vercel+Render deployment"
```

---

### Task 3: Deploy the backend to Render (manual + verify)

**Files:** none — this task is operational, not code.

**Interfaces:**
- Consumes: `render.yaml` from Task 1.
- Produces: a live backend URL, needed by Task 4.

- [ ] **Step 1: Hand off to the user**

The agent cannot create a Render account or authorize GitHub access — this
requires the user's own browser login. Tell the user, verbatim or close to
it:

> "Go to https://dashboard.render.com → New → Blueprint → connect the
> `sheet-music-combiner` GitHub repo → Render will detect `render.yaml` and
> propose the `sheet-music-combiner-backend` service → Apply. Once it
> finishes deploying, paste me the `https://....onrender.com` URL it was
> assigned."

- [ ] **Step 2: Verify the live backend once the user provides the URL**

Run (substituting the real URL):
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<render-url>/docs
```
Expected: `200`. If it's not 200 (or times out — remember free-tier cold
start can take up to a minute on first hit, retry once), check the Render
service's deploy logs with the user before proceeding; do not guess at a
fix blind.

No commit — nothing in the repo changes in this task.

---

### Task 4: Deploy the frontend to Vercel (manual + verify)

**Files:** none — operational.

**Interfaces:**
- Consumes: the Render URL from Task 3.
- Produces: a live frontend URL, needed by Task 5.

- [ ] **Step 1: Hand off to the user**

Same reasoning as Task 3 — Vercel project creation needs the user's own
login. Tell the user:

> "Go to https://vercel.com/new → import the `sheet-music-combiner` GitHub
> repo → set Root Directory to `web/frontend` → it should auto-detect Vite
> (build command `npm run build`, output `dist`) → add an environment
> variable `VITE_API_BASE` = `<the Render URL from Task 3>` → Deploy. Once
> it finishes, paste me the `https://....vercel.app` URL."

- [ ] **Step 2: Verify the live frontend once the user provides the URL**

Run (substituting the real URL):
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<vercel-url>/
```
Expected: `200`.

No commit — nothing in the repo changes in this task.

---

### Task 5: Wire CORS and run the end-to-end smoke test

**Files:** none — operational (Render dashboard env var change + redeploy).

**Interfaces:**
- Consumes: both live URLs from Tasks 3 and 4.
- Produces: a working shared deployment — the deliverable this whole plan builds toward.

- [ ] **Step 1: Hand off to the user to update CORS**

> "Back in the Render dashboard, open the `sheet-music-combiner-backend`
> service → Environment → set `SMC_CORS_ORIGINS` to
> `<the Vercel URL from Task 4>` → save, which triggers a redeploy. Let me
> know when it's back up."

- [ ] **Step 2: Verify CORS is actually wired (not just that the backend is up)**

Run (substituting both real URLs):
```bash
curl -s -i -X OPTIONS https://<render-url>/api/session \
  -H "Origin: https://<vercel-url>" \
  -H "Access-Control-Request-Method: POST" \
  | grep -i "access-control-allow-origin"
```
Expected: a line containing the Vercel URL. If missing, the env var wasn't
saved/redeployed correctly — check with the user before moving on.

- [ ] **Step 3: Run the full manual smoke test from the spec**

Using a real numbered snippet set (e.g. from `input/` or any
`{Prefix}_1.png, {Prefix}_2.png, ...` set on hand):

1. Open the Vercel URL in a browser, confirm the page loads.
2. Drop the numbered PNGs in, confirm the status line shows the correct
   piece count with no missing/duplicate error.
3. Click Assemble, confirm the page-count breakdown appears, the warnings
   panel behaves as expected, and page previews render.
4. Change the margin and re-assemble against the same session (no
   re-upload), confirm the preview visibly updates (this exercises the
   cross-origin cache-busting from commit `036b039` — if the preview looks
   stale, that's a real bug to investigate, not a caching quirk to ignore).
5. Click Download PDF, confirm a valid PDF downloads.
6. If a 7-piece set is available, upload it and confirm the split-required
   prompt appears and a manual `--pages`-style split resolves it.

If browser automation is available in this session (e.g. a Chrome
automation skill), use it to drive this check directly and report the
result. Otherwise, ask the user to perform steps 1-6 and report back what
they saw — do not claim this task complete without either the agent or the
user having actually observed the live app behave correctly.

No commit — nothing in the repo changes in this task. This is the final
task in the plan; once it passes, the deployment goal is met.

---

## Self-Review

**Spec coverage:**
- Split hosting (Vercel frontend / Render backend), no code changes → Tasks 1-4. ✓
- `render.yaml` Blueprint with exact build/start commands → Task 1. ✓
- Deploy sequencing (Render first, then Vercel, then wire CORS) → Tasks 3-5, matches spec's numbered sequence exactly. ✓
- README documentation of the deploy flow → Task 2. ✓
- Known limitations (Render free-tier sleep/cold-start) called out, not built around → Task 2 (README) and Task 3 (Step 2 retry note). ✓
- Manual end-to-end verification checklist (upload, assemble, re-assemble/cache-busting, download, N=7) → Task 5 Step 3, lifted directly from the spec's Testing section. ✓
- Out of scope items (auth, custom domain, Docker, CI) — no task builds any of them. ✓

**Placeholder scan:** No TBD/TODO. Tasks 3-5 are necessarily manual-handoff steps (account creation requires human OAuth) but each has concrete verbatim instructions and a concrete verification command, not vague guidance. ✓

**Type consistency:** N/A — no new functions/types introduced; all interfaces are existing env vars (`SMC_CORS_ORIGINS`, `VITE_API_BASE`) and URLs passed between tasks. Names match `web/backend/config.py` and `web/frontend/src/api.ts` exactly. ✓
