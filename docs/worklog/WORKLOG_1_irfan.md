# WORKLOG 1 (irfan + Claude Code)

Shared coordination log for Irfan's Claude instance and Shams's AI assistant. Entries below are **append-only**; only CURRENT STATUS is edited in place. Once this file gets long, continue in `WORKLOG_2_irfan.md`.

## CURRENT STATUS (last updated 2026-11-14 Sat)

| Area | Status |
|---|---|
| Backend | **API working** (pathways, events, alerts, worklist, facilities, regions, demo clock, WS). 24 tests pass |
| Frontend / integration | **Twin UI working** (`twin-ui/`, http://localhost:5173). Shams: care app against the same API (`docs/HANDOFF.md`) |
| Database | Aiven PG 18 + PostGIS, migrations 001–002. Real reference data loaded. 600 synthetic pathways + 4 demo stories |
| Simulation | **Done**: SimPy DES, replications + CI, stored runs, `/twin/*` API |
| Optimization | **Done**: OR-Tools MIP (flows + capacity units, equity), verified in sim |
| Blockers | None |
| Next priorities | 1) rehearse `docs/DEMO.md` 2) polish 3) source remaining HYPOTHESIS capabilities |

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

### 2026-11-14 21:00 — Claude (irfan) — National twin frontend (twin-ui/)
- Summary: React + TS + MapLibre + deck.gl command centre per the tech lead's sketch: map (counties, hospitals, flows with particles, health centres), Original / Modified / Δ toggle (camera + selection preserved), grounded Nemotron situation analysis with deterministic fallback, compliance panel, delays panel, Intervention Studio (3 recommendations + NL → controls → real SimPy run → auto-switch to Modified).
- Backend: `/twin/context`, `/twin/summary`, `/twin/interpret`, `/twin/simulate` (`backend/api/twin_ai.py`, `backend/services/{twin_context,llm}.py`). Sim scoring adds per-stage dwell + RTT histogram. Nemotron runs with `enable_thinking: false` (0.8–4 s instead of 3–20 s). The grounding check rejects LLM text that contains numbers not in the facts (verified: it caught an ungrounded "28").
- Verified in Chrome: national → click North Karelia → Modified (27 % → 57 %, bottleneck moves colonoscopy → MDT) → typed custom intervention → Nemotron parsed to +16 slots, re-route, monitoring → simulated in 35 s → NK 27 % → 41 %, national 83.4 → 84.9 %.
- Runs: baseline `190a5f4c…`, demo scenario "Demo: optimised colonoscopy (4 units) + monitoring" `6a6fc36d…`.
- Impact on Shams: none breaking. Both UIs share the API; monitoring in the twin is the simulated value of his app.
- Next: demo script, polish, verify remaining HYPOTHESIS capabilities.

### 2026-11-15 00:30 — Claude (irfan) — Behavioural patient agents + living network (model crc-abm-0.4)
- Audit (before): sim patient = route+municipality only, no attributes/identity, FIFO, no attendance/refusal, no GP stage, health centres were map-only, Docrates was default-routed (bug), particles unscaled, no network anomaly detection.
- Agents: persistent fictional patients (age, symptom category → urgency, archetype engaged/travel-constrained/low-follow-through with CONTINUOUS travel tolerance, attendance, acceptance, reminder response). Colonoscopy booking slice: offer → accept/decline (logistic utility on travel, wait, urgency, transport) → attend/no-show (slot wasted) → rebook / lost to follow-up. Urgent priority queues. GP first contact at nearest health centre. Separate clinical/behaviour/timing RNG streams per patient → paired comparisons (regression test).
- Network: 206 private clinics (OSM, first contact, never routed by default), Mehiläinen Joensuu private colonoscopy pilot (HYPOTHESIS, purchasing scenario only), DNA-aware capacity planning (8 %).
- Optimiser: `behavioral=True` expected-value model (acceptance-weighted load, declines fall back home, excess no-shows). Naive vs behaviour-aware demo: naive plan 41–61 % of offers declined.
- Anomalies: `backend/services/anomalies.py` with 9 rule types, evidence + thresholds + suggestion; `/twin/anomalies`.
- Patients: `sim_patient` table (migration 003), `/twin/patients`, `/twin/patients/{run}/{pid}?compare_run=`, `/explain` (Nemotron, grounded).
- UI: tabs Analysis | Network alarms | Patient agent; alarm markers + highlight; patient dots + journey paths; GP→endoscopy flows; particle key; follow-through KPIs; recommendations triggered by data.
- Tests: 30+ (incl. pairing + decline tests). Commits 3440086, 3466727.
- Impact on Shams: none (analytical tables only).

### 2026-11-15 01:30 — Claude (irfan) — Demo seeded (paired RNG) + interpreter levers
- Re-seeded baseline + Demo 1–3 with clinical/behaviour/timing streams. NK: 41.6 % → naive 49.1 % (35 % declined) / capacity 54.1 % / behaviour-aware 61.2 % (missed 7.6→3.5 %, lost 1.0→0 %). Script in `docs/DEMO.md` with caveats.
- `/twin/interpret` now maps reminders, transport support, private purchase (pilot) and behaviour-aware re-routing.

### 2026-11-15 — Claude (irfan) — MPC foundation (feature flag, default OFF)
- Summary: hierarchical stochastic MPC. Strategic layer = existing `optimise(behavioral=True)` (unchanged). New operational layer = lexicographic MIP (L1 clinically weighted expected target violation → L2 expected RTT → L3 transfer/admin/travel burden) over patient × facility × day, with SAA over downstream forecasts, acceptance probabilities, consent, capacity, urgent safety limit, authorisation. Coordinator: versioned state, stale-result protection, scheduled + event-triggered local re-optimisation, compute budget with greedy fallback, escalation (log; ElevenLabs voice placeholder disabled).
- `potential_brain/` is empty (locally and on all remotes), so there was nothing to evaluate. Decision: MIP for decisions, AI for forecasting/explanation (see `docs/MPC_SPEC.md` §0).
- Files: `backend/mpc/{state,forecast,problem,coordinator,escalation,api}.py`, `backend/tests/test_mpc.py`, `docs/MPC_SPEC.md`; `backend/api/main.py` mounts `/mpc` only if `CANCERFLOW_MPC=1`.
- Tests: 12 MPC tests + full suite 44/44 pass. Read-only DB smoke: 48 waiting patients, 580 slots, lexicographic-optimal in 1.1–2.5 s. L1 equals myopic on current data (no occupancy data yet), never worse (property test).
- Found and fixed: reading MIP solution after adding the tier lock invalidated it; SCIP default MIP gap (1e-4) let lower tiers leak clinical slack → gap 1e-7 + absolute tier lock.
- Impact on Shams: none (flag off). Future: "recommended actions" list via `/mpc/step`.
- Demo untouched: no stored scenarios recomputed, no services restarted, no simulation workers.

### 2026-11-15 — Claude (irfan) — Brain: end-to-end pathway optimiser (branch `brain`)
- Merge status: `setting_up_understanding` was already fully contained in `main` (PRs #4–#8 added the care app, Modal deploy and twin mobile layouts); this work branches from current `main`.
- Built `backend/mpc/pathway/`: time-expanded multi-stage MIP (colonoscopy → pathology → MDT → treatment) with downstream capacity, probabilistic downstream for unknown diagnoses (tentative, never booked), patient transfers, sample routing, resource relocation (origin loses capacity), budget-limited investment; 7 lexicographic tiers (urgent w/ 7-d buffer → severe → breaches → weighted lateness + earliest-treatment tie-break → equity → burden → cost). Baselines (FIFO, priority greedy, diagnostics-first), stochastic replay with common random numbers, validator, benchmark with selection hierarchy + material-improvement hypothesis (20 %) + bottleneck pass + investment ranking + patient story + rejection reasons.
- Found and fixed while building: ship-lag precedence semantics, partial-solve guard, urgent patients under-protected by π-weighting (now conservative + buffer), breach-minimisation sacrificing lost causes (severe tier), no incentive to treat earlier than target (tie-break), NaN in JSON.
- Result (illustrative East Finland, 60 pts, 30 reps, committed results file): FIFO 28.3 % → B national 53.9 % within target, urgent breaches 6.5 → 0.7 (priority greedy 0.8), weighted lateness −48 %, no new capacity, 0 violations. Run-to-run ±2 pp (wall-clock solver limits). Diagnostics-first: colonoscopy −27 %, treatment unchanged. Relocation predicted better but rejected by replay.
- API `/mpc/brain/*` (flag), twin UI "National optimiser" modal, `docs/BRAIN.md`. Tests: 11 new (report §15), suite 55/55.
