# CancerFlow: judging demo script (about 4 min)

Setup: `uvicorn backend.api.main:app --port 8000` and `cd twin-ui && npm run dev`. Open http://localhost:5173 at 1080p.
All outcome numbers are **SIMULATED** (model crc-abm-0.4, 4 replications, 95 % CI) on synthetic capacity and fictional patient agents. THL indicators are **OBSERVED**.

## 1. Observe (30 s)
- National map: 22 counties coloured by simulated % treated within the 42-day **reference** target. Hospitals sized by capacity and coloured by load. Particles = referrals ("1 particle ≈ 5 referrals/week"). 362 health centres and 206 private clinics (OSM).
- Finland baseline: **73.4 %** within 42 d. The Network alarms tab shows 3 alarms, all from thresholds:
  - North Karelia: patients wait **18.4 d at screening-nurse assessment** (national 9.5 d)
  - Kainuu: staging 8.2 d (national 5.1 d)
  - Colonoscopy at North Karelia Central Hospital at **98 %** utilisation

## 2. Understand (45 s)
- Click North Karelia. It is the weakest region (**41.6 %**, CI 22–61). The GP → endoscopy flows and sampled patient agents appear.
- Click a patient dot. The Patient agent tab shows the archetype, travel tolerance and p(attend), then every recorded step. "Why did this happen?" asks Nemotron to explain **only the recorded decisions**.

## 3. Intervene and simulate (90 s): why behaviour matters
Pick the stored scenarios in the top-right selector (or run one live from the Intervention Studio, about 50 s):

| Scenario (North Karelia) | NK within 42 d | What the agents did |
|---|---|---|
| Original | 41.6 % | 7.6 % of appointments missed, 1.0 % lost to follow-up |
| Demo 1 · naive re-routing (optimiser assumes everyone goes) | 49.1 % | **35 % of patients declined** the distant offer and fell back home |
| Demo 2 · capacity only (+16 slots/wk, two sessions) | 54.1 % | queue gone, but the unit is over-provisioned and behaviour is unchanged |
| Demo 3 · behaviour-aware plan | **61.2 %** (CI 51–72) | missed **7.6 → 3.5 %**, lost **1.0 → 0 %**, declines 21 % |

Demo 3 = acceptance-aware OR-Tools re-routing + transport support + reminders + stalled-step monitoring + a 100/month private purchasing pilot (Mehiläinen Joensuu, **SYNTHETIC** capacity, 650 EUR/procedure assumption).

## 4. Compare (45 s)
- Toggle **Original ↔ Modified**. The zoom and selection stay. The county recolours and the bottom panels show deltas.
- The **same patient** appears in both scenarios (common random numbers). Interventions change behaviour and timing, never the diagnosis.
- Point out the alarm the intervention created: *"Colonoscopy throughput at North Karelia Central Hospital fell 14.7 → 4.0/week"*. Load moved to the private pilot, so the public slots need re-planning.

## 5. The punchline
"A capacity-only fix assumes patients go where the plan sends them. Our agents don't: a third decline a 2-hour trip and 8 % miss appointments. Planning for behaviour (reachable capacity, reminders, transport, follow-up) beats simply adding slots in this model."

## Honest caveats (say them)
- Demo 3's national jump (73 → 85 %) comes mostly from **monitoring switched on nationally**. The North Karelia comparison is the fair one.
- North Karelia CIs overlap between Demos 2 and 3 (small county, 4 replications). The direction is consistent, but this is not statistical proof.
- Behavioural parameters are illustrative assumptions (`backend/sim/params.py`). Capacity is synthetic. Waiting indicators used for calibration cover all specialties.
