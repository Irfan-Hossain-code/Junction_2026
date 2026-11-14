# HANDOFF: read this first (for any AI coding assistant on this repo)

_Last updated: 2026-11-14 · Keep this file short. Full design: `docs/CANCERFLOW.md`. History: `docs/worklog/`._

## What this is
CancerFlow: one Python backend with two TypeScript front-ends.
1. **Care app** (Shams): doctor/patient app, "no patient falls through the cracks". **Creates events.**
2. **Network twin** (Irfan + Claude): national, zoomable network of hospitals and patient flows, with simulation (SimPy) and optimisation (OR-Tools) on scenario snapshots.

## Run it
```bash
pip install -r backend/requirements.txt
cp .env.example .env            # ask Irfan for DATABASE_URL (Aiven Postgres). Never commit .env
python -m backend.db.migrate    # apply SQL migrations (idempotent)
python -m pytest backend/tests
uvicorn backend.api.main:app --reload --port 8000   # API docs: http://localhost:8000/docs
```

## Core rules (do not break)
- **Event log is append-only** (DB trigger). Corrections are new events.
- Stage changes happen only through `POST /pathways/{id}/events` with `type: "STAGE_ENTERED"`. The backend validates them against `packs/<pack>.yaml`. Invalid transition → HTTP 409.
- Every patient is `SYNTHETIC`. Every number carries an epistemic tag (`OBSERVED`, `REFERENCE`, `EXTERNAL_PROXY`, `DERIVED`, `CALIBRATED`, `SYNTHETIC`, `SIMULATED`, `HYPOTHESIS`).
- Event `id` is client-generated (UUID v4). Re-POSTing the same id is idempotent.
- Simulation never writes to operational tables.

## Endpoints
See `documentation/backend/app_backend_routes.md` (contracts + examples). Live OpenAPI at `/docs`. Generate TS types with `npx openapi-typescript http://localhost:8000/openapi.json -o src/api.d.ts`.

## Immediate integration tasks for the care app
_(filled in as endpoints land, see below)_
