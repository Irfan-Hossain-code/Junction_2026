# WORKLOG 1 (irfan + Claude Code)

Shared coordination log for Irfan's Claude instance and Shams's AI assistant. Entries below are **append-only**; only CURRENT STATUS is edited in place. Once this file gets long, continue in `WORKLOG_2_irfan.md`.

## CURRENT STATUS (last updated 2026-11-14 Sat)

| Area | Status |
|---|---|
| Backend | Schema done. Pathway engine + API in progress |
| Frontend / integration | Shams: care app (TS). Contract in `docs/HANDOFF.md`, routes in `documentation/backend/app_backend_routes.md` |
| Database | Aiven PG 18 + PostGIS, schema `cf`, migration 001 applied, 9 invariant tests pass |
| Simulation | Not started (SimPy, runs on scenario snapshots) |
| Optimization | Not started (OR-Tools) |
| Blockers | None |
| Next priorities | 1) colorectal pack + state machine + API → Shams 2) real regions/hospitals ingest 3) alerts 4) synthetic population 5) SimPy + OR-Tools |

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
