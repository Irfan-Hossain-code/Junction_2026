# CancerFlow · Junction 2026 (AstraZeneca: Equal Care Across Finland)

A national digital twin of Finland's colorectal cancer pathway. It combines real public data (THL Sotkanet, Statistics Finland, OpenStreetMap), behavioural patient agents (SimPy), OR-Tools optimisation, a grounded LLM analyst (Nemotron) and an experimental MPC layer.

| Part | Folder | Docs |
|---|---|---|
| Backend API (FastAPI) | `backend/` | `documentation/backend/app_backend_routes.md` |
| National twin UI (React + MapLibre + deck.gl) | `twin-ui/` | `twin-ui/README.md` |
| Care-continuity app, patient + clinician (React) | `care-app/` | `care-app/README.md` |
| Hosting (Modal, one URL for API + both apps) | `deploy/` | `deploy/README.md` |

**Live demo:** https://shamshaque2103--cancerflow.modal.run (care app at `/app/care/`, national twin at `/app/twin/`, API docs at `/docs`)
| Pathway definitions | `packs/colorectal.yaml` | `docs/CANCERFLOW.md` |
| MPC layer (off by default) | `backend/mpc/` | `docs/MPC_SPEC.md` |
| Demo script | | `docs/DEMO.md` |
| Team coordination | | `docs/HANDOFF.md`, `docs/worklog/` |

---

## 1. Prerequisites
- Python 3.11+ (developed on 3.13)
- Node.js 20+ (developed on 24, inside WSL)
- Access to the shared Aiven PostgreSQL database (ask Irfan for `DATABASE_URL`)
- Optional: a Nemotron (Verda) API key for the AI panels. Without it, deterministic summaries are used.

## 2. Configure secrets
```bash
cp .env.example .env
# fill in DATABASE_URL (required), VERDA_BASE_URL / VERDA_MODEL / VERDA_INFERENCE_KEY (optional)
```
`.env` is git-ignored. Never commit it.

## 3. Backend
```bash
pip install -r backend/requirements.txt
python -m backend.db.migrate                 # apply SQL migrations (idempotent)
uvicorn backend.api.main:app --port 8000     # API on http://localhost:8000, docs at /docs
```
The shared database already contains everything the demo needs: reference data, synthetic patients and stored simulation runs. **Do not re-run the data scripts below unless you mean to.**

| Command | What it does | Caution |
|---|---|---|
| `python -m backend.ingest.load_reference` | reload regions, facilities, indicators, capacity | rewrites reference tables |
| `python -m backend.generator.population --n 600` | regenerate synthetic operational patients | **wipes all operational data**, including care-app events |
| `python -m backend.sim.runner --reps 4` | new baseline simulation run | about 1 min, uses 4 processes |

## 4. National twin UI
```bash
cd twin-ui
npm install
npm run dev                                  # http://localhost:5173
# if the API is not on localhost:8000:
VITE_API=http://<host>:8000 npm run dev
```
On Windows with the repo inside WSL, run `npm` **inside WSL** (UNC paths break npm scripts).

## 5. Demo walkthrough
Follow `docs/DEMO.md`:
- national view
- North Karelia
- patient agent
- Original ↔ Modified for the stored scenarios ("Demo 1–3")
- optionally one live simulation from the Intervention Studio (about 50 s)

All outcome figures are **SIMULATED** on synthetic capacity and fictional agents. THL indicators are **OBSERVED**.

## 6. Experimental MPC layer (off by default)
```bash
CANCERFLOW_MPC=1 uvicorn backend.api.main:app --port 8000      # PowerShell: $env:CANCERFLOW_MPC="1"; uvicorn ...
curl -X POST localhost:8000/mpc/step -H "Content-Type: application/json" -d '{"trigger":"scheduled","budget_s":5}'
curl localhost:8000/mpc/status
```
- It is **read-only** on the database and returns **recommendations** only. Nothing is booked or moved.
- Transfers come back as `requires_authorisation`. Urgent cases may be marked `escalate_on_call`.
- Design, maths and remaining work: `docs/MPC_SPEC.md`.

## 7. Tests
```bash
python -m pytest backend/tests -q            # 44 tests
python -m pytest backend/tests/test_mpc.py -q   # MPC only (synthetic, no DB)
```
The schema and API tests run against the shared DB inside a transaction that is rolled back.

## 8. Resource notes
- Each live simulation runs 4 worker processes (about 50 s). On low-memory machines, close other apps or use the stored scenarios instead.
- The map basemap tiles load from CARTO (internet). Without them, counties and the network still render.
