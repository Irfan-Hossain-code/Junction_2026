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

## National twin UI
`twin-ui/` (React/TS/MapLibre/deck.gl). Run `cd twin-ui && npm install && npm run dev` → http://localhost:5173 (API on :8000). Reads only `/twin/*`. Simulations take about 30 s and use 4 worker processes.

## Simulation model (crc-abm-0.4)
Hybrid agent-based + SimPy: behavioural patient agents (offers, declines, no-shows, lost to follow-up), urgent priority, GP first contact, private providers opt-in only (purchasing scenario). Parameters and assumptions are in `backend/sim/params.py` (shown at `/twin/assumptions`). Twin extras: `/twin/anomalies`, `/twin/patients`, `/twin/hc_flows`.

## Endpoints
See `documentation/backend/app_backend_routes.md` (contracts + examples). Live OpenAPI at `/docs`. Generate TS types with `npx openapi-typescript http://localhost:8000/openapi.json -o src/api.d.ts`.

## Data currently in the shared Aiven DB (2026-11-14)
- **Real:** 23 wellbeing counties + 5 university-hospital areas (YTA) + 308 municipalities with boundaries and population. 392 facilities from OpenStreetMap (30 curated hospitals, 362 health centres). Sotkanet indicators.
- **Synthetic:** 600 colorectal pathways + 4 demo stories, ~3.2k events, ~70 open alerts. Capacity and staff are `SYNTHETIC`.
- Reload: `python -m backend.ingest.load_reference`, then `python -m backend.generator.population --n 600`.
  ⚠️ The generator **wipes all operational data**, including events your app created. Tell the team before running it.

## Demo logins (`X-User-Id`)
| id | role | where / who |
|---|---|---|
| `nurse-pkks-scr` | doctor (screening nurse) | North Karelia Central Hospital. Worklist shows Matti's overdue colonoscopy |
| `dr-pkks-surg` | doctor | North Karelia Central Hospital |
| `dr-kys-endo` | doctor | KYS Kuopio |
| `dr-hus-endo` | doctor | HUS Meilahti |
| `dr-tyks-onc` | doctor | Tyks Turku |
| `gp-…` | doctor (GP) | health centres used by the demo stories (`GET /auth/users?role=doctor`) |
| `pat-d-0001` | patient | Matti Virtanen: positive FIT, **no colonoscopy for 40 d** (critical) |
| `pat-d-0002` | patient | Aino Korhonen: **referral sent, never received** |
| `pat-d-0003` | patient | Eino Mäkinen: on track, MDT done |
| `pat-d-0004` | patient | Helmi Laine: **histology pending too long** |

## Immediate integration tasks for the care app
1. Login screen: `GET /auth/users?role=…`, then `POST /auth/login`; store the id; send the `X-User-Id` header.
2. Patient home: `GET /me/pathways`, then `GET /pathways/{id}` (timeline, clocks, appointments, next step).
3. Doctor home: `GET /worklist` (pathways + alerts, critical first).
4. Pathway screen: render `allowed_next` as actions → `POST /pathways/{id}/events` with `STAGE_ENTERED`. Handle 409 by showing `detail`.
5. Live refresh: `WS /stream`. On `{"kind":"event"}`, refetch the pathway.
6. Demo trick: `POST /admin/clock {"offset_days": 14}` fast-forwards time so new alerts appear (reset with 0).
