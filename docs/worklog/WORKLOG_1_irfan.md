# WORKLOG 1 (irfan + Claude Code)

Shared coordination log for Irfan's Claude instance and Shams's AI assistant. Entries below are **append-only**; only CURRENT STATUS is edited in place. Once this file gets long, continue in `WORKLOG_2_irfan.md`.

## CURRENT STATUS (last updated 2026-11-14 Sat)

| Area | Status |
|---|---|
| Backend | **API working** (pathways, events, alerts, worklist, facilities, regions, demo clock, WS). 24 tests pass |
| Frontend / integration | Shams can integrate now: `docs/HANDOFF.md` lists the tasks and demo logins. Routes in `documentation/backend/app_backend_routes.md` |
| Database | Aiven PG 18 + PostGIS, migrations 001–002. Real reference data loaded. 600 synthetic pathways + 4 demo stories |
| Simulation | **Done**: SimPy DES, replications + CI, stored runs, `/twin/*` API |
| Optimization | **Done**: OR-Tools MIP (flows + capacity units, equity), verified in sim |
| Blockers | None |
| Next priorities | 1) twin frontend (TS) 2) demo script 3) source remaining HYPOTHESIS capabilities |

---

### 2026-11-14 — Claude (irfan) — Database
- Summary: First schema. Two worlds: operational (patient, pathway, append-only `event`, `pathway_clock`, `appointment`, `alert`) and analytical (`source`, `region`, `indicator(_value)`, `facility`, `capability`, `facility_capacity`, `staff`, `travel_time`, `scenario`, `sim_run`, `sim_metric`). Epistemic tag enum, enforced with per-table CHECKs.
- Files: `backend/db/migrations/001_core.sql`, `backend/db/migrate.py`, `backend/db/conn.py`, `backend/tests/test_schema.py`, `.gitignore`, `.env.example`
- API/schema changes: new schema `cf`
- Tests: `python -m pytest backend/tests`, 9/9 pass against live Aiven DB
- Impact on Shams: events are append-only (UPDATE/DELETE raise). Corrections = new events. Patients are always `SYNTHETIC`.
- Outstanding: none
- Next: pathway pack + API
- Commit: (see next entry)

### 2026-11-14 — Claude (irfan) — Pathway definition (decision)
- Summary: Tech lead verified the Finnish screening flow: **positive FIT → screening-nurse follow-up assessment → usually colonoscopy (or an alternative exam) → findings → treatment or follow-up.** It does NOT go back through the GP. Symptomatic GP referral is a separate entry route that joins at specialist diagnostics.
- Sources: Syöpärekisteri screening protocol PDF (2023), syoparekisteri.fi colorectal screening page, HUS screening page. Cancer Registry 2024 cohort: participation 73.5 %, FIT positive 4.5 %, 10,244 colonoscopies (stats.cancerregistry.fi; page is JS-rendered, figures verified manually by tech lead).
- Impact on Shams: stage names are in `packs/colorectal.yaml`. Use those exact IDs.

### 2026-11-14 16:10 — Claude (irfan) — Pathway engine + API
- Summary: `packs/colorectal.yaml` (screening: FIT → nurse → colonoscopy; symptomatic: GP → referral), pure engine (`backend/pathways/engine.py`: transitions, clocks, dwell/target alerts), service layer, FastAPI app.
- Files: `packs/colorectal.yaml`, `backend/pathways/*`, `backend/services/pathways.py`, `backend/api/main.py`, `backend/db/migrations/002_settings.sql`, tests
- API/schema changes: all routes in `documentation/backend/app_backend_routes.md`. Migration 002 adds `app_setting` (demo clock).
- Tests: 24/24 pass (schema invariants, engine, API flow inside a rolled-back transaction)
- Impact on Shams: integrate against these routes. Event ids are client-generated UUIDs (idempotent). Invalid transitions return 409 with a reason.
- Commit: 394fca9

### 2026-11-14 17:00 — Claude (irfan) — Reference data + synthetic population
- Summary: Real data loaded: Sotkanet regions (country / 5 YTA / 23 counties / 308 municipalities) with Statistics Finland boundaries and 2024 population. 392 facilities from OpenStreetMap (30 curated hospitals + 362 health centres), spatially joined to counties. Sotkanet indicators. Travel times (DERIVED, great-circle ×1.3 @ 70 km/h). Capabilities: radiotherapy OBSERVED for HUS, Päijät-Häme, Kymenlaakso, Docrates; everything else HYPOTHESIS (documented assumption). Capacity + staff SYNTHETIC (catchment-demand model).
- Generator: 600 pathways, regional delay multipliers from REAL indicators (6411 GP wait, 3336 specialist backlog), random "cracks" at monitored stages, plus 4 scripted demo stories with patient logins.
- Files: `backend/ingest/load_reference.py`, `backend/ingest/raw/*` (OSM, Sotkanet, Statistics Finland snapshots), `backend/generator/population.py`
- Tests: smoke-tested running API: nurse-pkks-scr worklist shows Matti (critical, FIT+ with no colonoscopy for 40 d)
- Impact on Shams: demo logins in HANDOFF. **Running the generator wipes operational data.**
- Outstanding: radiotherapy at Tays/Tyks/KYS/OYS/Nova/Seinäjoki/Vaasa/Pori/Joensuu is still HYPOTHESIS; needs one source each.
- Next: SimPy simulator + OR-Tools optimiser on scenario snapshots

### 2026-11-14 19:30 — Claude (irfan) — Simulation + optimisation + twin API
- Summary: SimPy DES (`backend/sim/model.py`) on pure `Snapshot`s (`backend/sim/snapshot.py`). Two arrival routes, county-scaled delays, stochastic "cracks", capacity-limited stations, and pooled shared resources (background load for CT/oncology/radiotherapy). **Clocks are scored with the same pathway engine** as operations. Runner: parallel replications, mean + 95 % CI, stored in `sim_run`/`sim_metric`. OR-Tools MIP (`backend/opt/allocate.py`): municipality→hospital flows + integer capacity units, utilisation cap ρ = 0.85, minimax county travel (equity), disruption penalty. The plan is **verified in simulation**.
- Results (4 reps, colonoscopy, 4 units): national within-42 d 83.4 % [81.0–85.7] → 87.8 % [85.9–89.6] with plan + monitoring; North Karelia 27 % → 57 %; monitoring cuts stall time 40 → 9 d; NK colonoscopy wait 11.7 → 0.4 d. Finding: capacity alone does not close the NK gap (the rest is process delay).
- Files: `backend/sim/{params,snapshot,model,runner}.py`, `backend/opt/allocate.py`, `backend/api/twin.py`, `backend/tests/test_sim_opt.py`; generator now imports shared params.
- API: `/twin/*` (network, geo, assumptions, scenarios, runs, compare, optimise). Docs in routes file.
- Tests: 30/30 pass
- Impact on Shams: none breaking. Optional: show "Simulated impact of monitoring" from `/twin/compare`.
- Next: twin frontend (map + network + scenario panel)
