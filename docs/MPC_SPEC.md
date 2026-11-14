# CancerFlow MPC: specification and foundation

_Status: foundation implemented (`backend/mpc/`), **disabled by default** (`CANCERFLOW_MPC=1` to enable). Recommendation-only.
Existing demo, simulator, optimiser and stored runs are unchanged._

## 0. Decision: optimiser, not "some AI", for the control decisions

`potential_brain/` is **empty** (in this working copy and on every remote branch), so there was nothing to evaluate. If it is meant to hold a model, add it and assess it against §8.

For the **decision layer** we use a mathematical program (OR-Tools MIP), not a learned policy or an LLM:
- Clinical requirements must hold as **hard constraints**, and clinical priority must strictly dominate convenience (**lexicographic**). An MIP guarantees both. An RL policy or LLM can only approximate them.
- Every recommendation must be **auditable**: which constraint was binding, which tier decided, and the predicted difference against a baseline.
- There is no real operational history to train on, and a policy trained on synthetic agents would learn our own assumptions back.

AI is useful **around** the controller:
- **Forecasting:** arrivals, attendance, downstream delays (`DownstreamForecast` is the plug-in point).
- **A learned surrogate** of the SimPy simulator for faster verification.
- **Nemotron** for interpretation and explanation, as it does today. It never sets numbers.

## 1. Mapping to the existing architecture

| MPC concept | Existing component (reused) |
|---|---|
| Patient state | `pathway`, `event`, `pathway_clock` (operational DB) → `mpc.state.from_operational_db` (read-only) |
| Resource state | `Snapshot.capacity` (SYNTHETIC weekly capacity) → day slots |
| Plant / prediction model | SimPy `backend.sim.model` (agents, queues): used for **verification** of plans |
| Downstream forecast | stored SIMULATED runs (`sim_metric`) → `DownstreamForecast.from_sim_metrics` |
| Strategic optimiser | `backend.opt.allocate.optimise(..., behavioral=True)` **unchanged**, called by `MPCCoordinator.strategic_preferences` |
| Interventions | `apply_scenario(params)` (scenario level), MPC recommendations (patient level) |
| Alarms | `backend.services.anomalies.detect`: can act as event triggers (§4) |

**Hierarchy.** The strategic layer runs weekly: the existing flow MIP sets capacity and routing shares. The operational layer runs daily or on events: the new MIP assigns patient × facility × day.

## 2. State, inputs, disturbances

**State** at decision time t, versioned with `MPCState.version`:
- Patient p:
  - next required step `k_p` (pathway dependencies already resolved by the pack state machine)
  - clinical priority `π_p ∈ {urgent, standard}`, **clinically supplied**, never inferred
  - elapsed time on the target clock `e_p`
  - target `τ_p` (pathway pack, e.g. 42 d REFERENCE)
  - home facility `h_p`, eligible facilities `E_p` (capability, clinical appropriateness, travel ≤ 180 min)
  - travel `t_pf`, acceptance probability `a_pf`
  - consent flag, authorisation requirement
- Resources: free slots `c(f,k,d)` for day d ∈ {0..H−1}.
- Pending actions: standing recommendations awaiting consent or authorisation. These keep their capacity in local re-optimisations.

**Control inputs `u_t`:**
- booking decision `x_pfd ∈ {0,1}` (patient p, facility f, day d)
- deferral `y_p`
- transfer offers (x with f ≠ h_p)
- strategic, weekly: capacity redistribution and support interventions (reminders, transport, purchasing) via the existing optimiser and scenarios

**Disturbances `w_t`:**
- arrivals
- acceptance / decline, attendance / no-show, cancellations
- equipment failure (capacity drop)
- pathway outcome (cancer or not, treatment type)
- downstream delays

These are modelled as samples (operational MIP) or as stochastic agents (SimPy).

## 3. Optimisation problem (operational step)

The next step's completion time for option (p,f,d) under scenario s is `T_pfd^s = e_p + d + R_f^s`, where `R_f^s` is a downstream sample (common across options at f).

A transfer is accepted with probability `a_pf`. If declined, the patient falls back home after the horizon: `T_fb^s = e_p + H + R_{h_p}^s`.

**Sample-average approximation** over S scenarios:
- expected violation `V_pfd = a_pf·mean_s max(0, T^s−τ_p) + (1−a_pf)·mean_s max(0, T_fb^s−τ_p)`
- violation probability `P_pfd`
- expected completion `C_pfd`

**Lexicographic objective**, solved as sequential MIPs. Each tier is then locked with `≤ optimum + 1e-4` (no relative slack) and a MIP gap of 1e-7:

1. **L1 clinical:** `min Σ w(π_p) · V_pfd · x_pfd + Σ w(π_p) · V_p^defer · y_p`. Here `w` is the clinically supplied priority weight (urgent 5, standard 1: configurable, to be set by clinicians).
2. **L2 waiting:** `min Σ C_pfd · x_pfd + Σ C_p^defer · y_p`.
3. **L3 burden:** `min Σ (transfer + admin·needs_auth + travel/60 − strategic_plan_bonus) · x_pfd`.

**Hard constraints:**
- one decision per patient: `Σ_{f,d} x_pfd + y_p = 1`
- capacity: `Σ_p x_pfd ≤ c(f,k,d) − standing_use(f,k,d)`
- capability and pathway dependency: options exist only for `f ∈ E_p` with capability `k_p`
- consent: no consent → `E_p = {h_p}`
- mandatory safety (when clinically configured): urgent p has `Σ_{d ≤ D_urgent} x_pfd = 1`. If infeasible, the patient is flagged and **escalated**.
- authorisation: transfers are emitted as `requires_authorisation` and never executed

**No reservation of unconfirmed treatment.** Downstream steps enter only as forecasts `R_f`; no downstream slot is booked.

## 4. Receding horizon

```
observe(state_t, version v) → [forecast R, a] → solve L1→L2→L3 (budget B) → recommendations (version v)
   → publish only if version still v (else STALE, discard) → humans/patients act → observe(state_t+1) → …
```
- **Scheduled:** national step (e.g. nightly, all waiting patients).
- **Event-triggered local:** cancellation, equipment failure or a network alarm triggers re-optimisation of patients whose eligible set touches the affected facilities. Other patients' standing recommendations keep their slots (`fixed_capacity_use`).
- **Concurrency:** solve outside the lock, publish inside it with a version check (`MPCResult.stale`).
- **Bounded compute:** `MPCConfig.budget_s` split across tiers. On timeout or infeasibility, the **greedy fallback** is used (urgent first, longest wait, earliest home slot), which is always feasible. Measured on the synthetic DB: 48 waiting patients, 580 slots, **1.1–2.5 s**, proven optimal.
- **Verification (specified):** plans become `apply_scenario`-style params and are re-simulated with the existing SimPy agents, serially, outside the demo.

## 5. Escalation

An urgent patient's recommendation becomes `escalate_on_call` when:
- the mandatory time limit cannot be met;
- the recommended slot expires before the next routine reviewer acts (`review_delay_days`); or
- a target violation is more likely than not even under the best plan.

Channels: `LogChannel` (default) and `VoiceCallChannel`, an ElevenLabs placeholder that is **disabled**. Escalation only notifies a human and never authorises a transfer.

## 6. Interfaces (implemented)

- `backend/mpc/state.py`: `PatientState`, `SlotState`, `MPCState` (frozen, versioned, `digest()`), `from_operational_db` (read-only).
- `backend/mpc/forecast.py`: `DownstreamForecast` (static or from a stored SIMULATED run).
- `backend/mpc/problem.py`: `MPCConfig`, `solve` (lexicographic MIP), `greedy` (baseline and fallback), `evaluate` (same scoring for any policy).
- `backend/mpc/coordinator.py`: `MPCCoordinator.observe/step/publish/latest`, `strategic_preferences` (calls the existing optimiser), `enabled()`.
- `backend/mpc/escalation.py`: channels.
- `backend/mpc/api.py`: `GET /mpc/status`, `POST /mpc/step`. **Mounted only when `CANCERFLOW_MPC=1`.** Read-only on the DB, returns recommendations.

## 7. Tests (`backend/tests/test_mpc.py`, 12 tests, synthetic, no DB, no workers)

- **Whole-pathway vs local:** the myopic plan books hospital A on day 2 (downstream about 40 d → 47 d, violation). MPC picks B on day 6 (downstream 10 d → 21 d).
- **L3 cannot override L1:** transfer cost 1000 still yields the clinically better transfer.
- **Low acceptance (5 %):** the patient stays local. **No consent:** no transfer offer.
- **Urgent before standard,** with the mandatory day limit respected.
- **Capacity is hard;** the budget-exhausted fallback is feasible.
- **Stale result** (state changes mid-solve) is never published.
- **Event-triggered local re-optimisation** keeps others' standing bookings.
- **Transfers** require authorisation. **Urgent with a late reviewer** escalates.
- **Feature flag** is off by default: no `/mpc` routes are mounted.
- **Property:** across 6 random instances, MPC's L1 ≤ both myopic baselines.

## 8. Remaining work for full integration

1. **Real occupancy.** Subtract booked `appointment` rows and forecast non-CRC demand from `c(f,k,d)`. Today's slots come from SYNTHETIC weekly capacity, so the home facility looks free and MPC ≈ myopic on current data.
2. **Clinically supplied inputs.** Priority weights, the urgent time limit and step-specific targets need clinician sign-off. Write `priority` into the referral event payload from the care app.
3. **Multi-step horizon.** Jointly schedule colonoscopy, CT and MDT (time-expanded network), not only the next step. Downstream is a forecast today.
4. **Closed-loop verification.** Turn MPC recommendations into a SimPy policy hook (patient-level offers) and compare against baseline with common random numbers.
5. **Forecasts.** Replace lognormal downstream samples with per-facility empirical or learned distributions. Add arrival and no-show forecasting.
6. **Scheduler.** Nightly national job plus event subscription to `/stream` and network alarms. Persist `MPCResult`s (new table) for audit.
7. **UI.** A "Recommended actions" list for the care app (Shams) and an MPC tab in the twin, both behind the flag.
