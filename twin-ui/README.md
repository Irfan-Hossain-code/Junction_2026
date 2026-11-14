# CancerFlow · National Twin UI

React + TypeScript + MapLibre + deck.gl. This is the executive-facing digital twin. It reads only the `/twin/*` API and never computes predictions itself.

```bash
# backend (repo root, Windows or Linux python with backend/requirements.txt)
uvicorn backend.api.main:app --port 8000
# frontend (WSL / Linux / mac; node 20+)
cd twin-ui && npm install && npm run dev      # http://localhost:5173
# VITE_API=http://host:8000 npm run dev       # if the API runs elsewhere
```

## Layout
| Area | What it shows | Data |
|---|---|---|
| Map (top left) | 22 counties coloured by simulated % within 42 d, an observed THL indicator, or the Δ vs original. Hospitals are sized by synthetic capacity and coloured by simulated utilisation, with pulsing rings when > 93 % or critical alerts exist. Curved county → hospital flows with moving particles (in-county teal, cross-county amber). 362 health centres as dots. Click a county or hospital to drill in; click empty map or the breadcrumb to go back. | `/twin/network`, `/twin/geo/counties`, `/facilities?kind=health_center` |
| Original / Modified / Δ toggle | Swaps the scenario run behind every layer and panel. **The camera and selection never change.** The top bar shows the national (and selected county's) before → after. | two `run_id`s |
| Situation analysis (top right) | Nemotron prose over grounded facts. Rejected and replaced by deterministic text if it introduces numbers not in the facts. "Grounding" shows exactly what it saw. | `/twin/summary` |
| Target compliance | Ring gauge (95 % CI band, white tick = original), exceeding/yr, FIT+ → colonoscopy, weakest regions. | `/twin/context` |
| Treatment delays | Median / p90 / stalled-step days, time-to-treatment histogram vs 42 d, mean days per pathway stage (ticks = original). | `/twin/context` |
| Intervention studio | Primary bottleneck plus 3 data-derived recommendations (monitoring, OR-Tools re-routing, capacity at the tightest station, with a slider), and free-text interpretation via Nemotron or rules into validated controls. "Simulate intervention" runs real SimPy replications (~30 s) and switches to Modified. | `/twin/interpret`, `/twin/simulate` |

Tags everywhere: `OBSERVED` (THL), `SIMULATED` (SimPy), `SYNTHETIC` (demo patients/capacity), `REFERENCE` (42-day target).
