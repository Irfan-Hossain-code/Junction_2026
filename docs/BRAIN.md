# CancerFlow brain: end-to-end pathway optimisation

_Implements the strategy report "Strategic Vision, Optimisation Architecture & Implementation Report" (14 Nov 2026).
Code: `backend/mpc/pathway/` · tests: `backend/tests/test_pathway.py` · results: `backend/mpc/pathway/results/east_finland.json`.
Recommendation-only, behind `CANCERFLOW_MPC=1`._

## 1. Gap analysis (before this work)

| Requirement | Existed | Gap closed here |
|---|---|---|
| Whole pathway is the unit of optimisation | Next-step MPC with downstream *forecast* only | Time-expanded multi-stage MIP with **downstream capacity** |
| Unknown diagnosis | Not in the optimiser | Downstream stages are probability-weighted expected demand, flagged *tentative*, never booked |
| National coordination | Flow-level strategic MIP; next-step MPC | Patient transfers + sample routing across providers in one model |
| Resource movement | None | Relocating a surgeon day: **origin loses exactly what destination gains** |
| Investment last | Free-form capacity scenarios | Existing-first → bottleneck pass → budget-limited investment, ranked per EUR |
| Counterfactual benchmark | Flow-level SimPy scenario comparison | Same patients, capacity and random streams across 6 strategies, 30 replications |
| Auditable recommendations | Next-step MPC records | Actions, alternatives considered, rejection reasons, constraint checks, digest |

## 2. Model

**Stages:** colonoscopy (PATIENT) → pathology (SAMPLE: lab, shipping lag) → MDT (MEETING at the treating centre) → treatment (PATIENT).
- Each stage has a duration (turnaround) and a conditional probability of being needed.
- For undiagnosed patients, P(treatment) is the product of those probabilities, e.g. 0.7 × 0.3.

**Decision:** `x[p,j,h,b] ∈ {0,1}`, meaning stage j of patient p starts at provider h in bucket b.
- Buckets are daily for 14 days, then weekly up to 63 days. This is the hybrid near-term / long-term horizon.
- An overflow option keeps every instance feasible.

**Hard constraints (Tier 0):**
- one option per stage
- precedence: `S[j+1] ≥ S[j] + duration[j] + ship_lag` (the sample must *arrive* before its lab slot)
- capacity: `Σ π·x ≤ capacity − moved_out + moved_in + invested`
- eligibility (capability)
- consent and acceptance (`≥ 0.4`) and travel (`≤ 240 min`) for transfers
- one relocation per resource per week, never more than the origin has
- investment within budget

**Lexicographic tiers**, each locked at its optimum (+1e-4) before the next:
1. urgent lateness: unweighted (as if treatment is needed), with a **7-day safety buffer** (assumption)
2. severe lateness: days beyond target + 14
3. expected target breaches
4. clinically weighted lateness, plus a 1e-3 tie-break towards **earlier treatment**
5. equity: the maximum regional mean lateness
6. burden: transfers, travel hours, routed samples, staff moves
7. expenditure (EUR)

**Strategies:**

| Strategy | What it is |
|---|---|
| baseline FIFO | Greedy, home providers, first come first served |
| priority greedy | Greedy, home providers, urgent first then earliest deadline |
| diagnostics-first | *Conventional*: endoscopy units minimise their own appointment dates; the lab works FIFO |
| A local pathway | End-to-end MIP, home providers only |
| B national pathway | A + eligible patient transfers + routing samples to labs with spare capacity |
| C resource movement | B + relocating an existing surgeon day. Chosen only if it is lexicographically better **and** reduces weighted lateness ≥ 20 % more than B (engineering **hypothesis**, configurable) **and** survives replay |
| Investment pass | Budget-limited, on top of the chosen plan, ranked by breaches avoided per 10 kEUR |

**Validation:**
- **Stochastic replay** with common random numbers per patient. The draws are biopsy needed, cancer, transfer accepted, and attendance (3 no-shows means lost).
- The replay runs on real daily capacity. Declined transfers are rebooked at home, never counted as accepted.
- A **validator** checks capacity, precedence, eligibility, consent, the strategy rules, budget, no hidden spending and no confirmed booking for an unknown diagnosis.

## 3. Results: illustrative East Finland instance

60 synthetic patients (27 urgent), 30 paired replications. Joensuu, Kuopio and Savonlinna share one regional pathology service (2 per day); Jyväskylä has a spare lab (2-day shipping). **All figures are SIMULATED. Capacities, probabilities and costs are ASSUMPTIONS.**

| Strategy | Within target [95 % CI] | Urgent breaches | Weighted lateness | Colonoscopy day | Diagnosis day | Treatment day | Violations |
|---|---|---|---|---|---|---|---|
| Baseline FIFO | 28.3 % [26–31] | 6.5 | 199 | 4.8 | 19.2 | 22.4 | 0 |
| Priority greedy | 44.3 % [41–47] | 0.8 | 145 | 4.8 | 19.4 | 23.6 | 0 |
| Diagnostics-first | 28.0 % [25–31] | 6.5 | 201 | **3.5** | 19.3 | 22.4 | 0 |
| A local pathway | 48.3 % [45–51] | 0.8 | 140 | 5.0 | 19.1 | 23.4 | 0 |
| **B national pathway** | **53.9 % [51–57]** | **0.7** | **103** | 4.5 | **13.7** | **20.8** | 0 |
| C + resource movement | 50.1 % [47–53] | 1.0 | 110 | 4.3 | 13.4 | 21.2 | 0 |

**What this shows**
1. **Downstream awareness (Act 2).** Diagnostics-first makes colonoscopies 27 % sooner, but diagnosis and treatment don't move: the shared lab is the constraint. B routes about 13 samples to Jyväskylä and reschedules end-to-end, so diagnosis comes about 6 days earlier.
2. **Existing resources first.** B nearly doubles the within-target share (28 → 54 %) and cuts weighted lateness by 48 % (199 → 103), with no added capacity. Urgent patients are protected at least as well as by the simple priority rule (0.7 vs 0.8 urgent breaches; the difference is within noise).
3. **Simulation validation matters.** The optimiser *predicted* that relocating a Kuopio surgeon day to Joensuu (C) would help. The stochastic replay shows it doesn't (50.1 % vs 53.9 %), so it is **rejected**. The plan's expected-value model and the realised execution disagree, which is exactly why plans are validated.
4. **The bottleneck moves.** After B, pathology is no longer the tightest resource (61 %); surgery in Kuopio and Joensuu is (81 % / 77 %). The investment pass agrees. One extra surgery slot per week in Joensuu (€36k over 9 weeks, an assumption) avoids about 1.4 breaches. Extra lab capacity (€27k) avoids about 0.4. Extra colonoscopy capacity is not worth buying.

Regenerate with `python -m backend.mpc.pathway.benchmark` (about 3 min, single process).

## 4. API (with `CANCERFLOW_MPC=1`)
- `GET /mpc/brain/benchmark`: the full comparison document (cached).
- `POST /mpc/brain/run`: recompute (about 3 min; one at a time).
- `GET /mpc/brain/patient/{pid}`: one synthetic patient's original vs recommended pathway and actions (for the care app).
- `POST /mpc/brain/explain`: Nemotron explanation grounded in the solver output, rejected if it uses numbers not in the facts.

UI: twin top bar → **National optimiser**.

## 5. Honest limitations
- The instance is **illustrative**. Capacities, turnaround times, P(biopsy), P(cancer), costs and the 7-day urgent buffer are assumptions, not calibrated.
- The replay is a **schedule-level** stochastic check, not the national SimPy model. It executes plans **open-loop**: no re-optimisation after disturbances. Closed-loop MPC (re-solve daily inside the replay) is the next step and would likely narrow plan/replay gaps such as C's.
- Some solves stop at the time limit on lower tiers (marked `partial`). The higher tiers are optimal. Because the time limits are wall-clock, **results vary by about ±2 percentage points between runs** (a previous run gave B 55.9 %). The committed `results/east_finland.json` is the reference.
- Synthetic patients are not linked to the operational database. `/mpc/brain/patient` is for demo integration only.
- The priority policy (urgent/standard weights, buffer, severe threshold) must be defined and validated by clinicians.
