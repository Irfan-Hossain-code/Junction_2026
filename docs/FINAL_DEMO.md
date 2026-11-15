# CancerFlow: final demo (about 6 min)

**Message:** CancerFlow doesn't just get patients to the next appointment. It helps the healthcare system get them all the way to treatment.

Everything below is real shared state. The care app, the twin map, the text box and the ElevenLabs voice agent all call the same backend (`/ops/*`, `/pathways/*`). All patients, slots and disruptions are **SYNTHETIC**, and every outcome number is **SIMULATED** by the brain's replay, not hard-coded.

## Run

```bash
python -m uvicorn backend.api.main:app --port 8000      # CANCERFLOW_MPC=1 is only needed for the national optimiser modal
cd twin-ui && npm run dev                               # http://localhost:5173  (twin + Operations + call)
cd care-app && npx vite --port 5174                     # http://localhost:5174/?as=pat-emma#/  (Emma's phone)
                                                        # http://localhost:5174/#/demo  (clinician + phone side by side)
```

- **Reset before the demo:** twin → **Operations** → **Story** → **Reset** (≈40 s). Or `POST /ops/demo/reset`.
- **Keys:** `.env` holds `ANTHROPIC_API_KEY` (Haiku: parsing + patient messages) and `VERDA_*` (Nemotron). `.env.local` holds `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID` (created by `python -m backend.ops.voice_setup`).

## Script

| Act | Do | What judges see |
|---|---|---|
| 1 Patient enters | Twin: national map. Phone (Emma): **"I have symptoms: book a GP appointment"**. Story: *GP visit → urgent referral* | Pathway created from the phone. GP sets **urgent** (clinical criteria) and CancerFlow tracks the referral |
| 2 Reminder | Story: *Hospital receives referral*, then *Reminder*. Phone: **Confirm** | Reminder appears on the phone. Confirmation is written to the shared appointment |
| | Story: *Specialist → colonoscopy in 3 days* | Booked on **Endoscopy Unit 2**, Joensuu, alongside 7 other patients |
| 3 System breaks | Operations → Disruption library → **Endoscopy Unit 2 offline · 48 h** | Map flies to East Finland and the unit pulses red. 8 appointments affected (4 urgent). Lifecycle DETECTED → AWAITING_APPROVAL. The comparison table shows *before breakdown / no action / CancerFlow plan* |
| 4 The call | **Incoming call** rings → **Answer** | The voice agent briefs Donny using `get_situation` (backend numbers only) |
| 5 Challenge | Say: *"Move half the affected patients to North Savo and half to South Savo."* (or type it, or select a patient row and click a hospital on the map) | Backend evaluates the proposal: overload at Kuopio (3 free slots, 4 sent), low-acceptance patients, worse or comparable target. The verdict is spoken and shown. **Why did the results change?** toggle |
| 6 Approve | Say: *"OK, go with your recommendation."* The agent asks for confirmation; you say yes (or click **Approve & apply**) | APPROVED → APPLIED. Appointments re-booked (append-only events), receiving hospitals and patients notified, map routes turn green. **Patient replies** arrive (persona agents). Declines trigger an automatic **re-plan** proposal |
| 7 Not lost | Phone: new time → **Confirm**. Story: *Pre-procedure check-in*, then *Emma misses the appointment* | Pathway NOT closed. Care team alerted (also in the clinician worklist). Replacement slots ranked with priority kept. Clinician **authorises**, then Emma is notified |
| 8 Treatment | Story: colonoscopy → result (clinician) → staging MRI → MDT → surgery | Full timeline on the phone, including disruption, reschedule, miss and follow-up. Treatment about 31 days after referral |

**Typical numbers** (current seed): within 42 days, **before breakdown ≈ 95 %**, **no action ≈ 42 %**, **CancerFlow plan ≈ 78 %**. The 50/50 split scores about 65 % (*worse*). They are simulated and recomputed live, so quote what the screen shows.

## The three living twins (national map)
Selector on the map: **Baseline | Disruption | MPC Recovery** (+ **Δ** toggle). Old runs are under *Advanced · simulation history*.

| Twin | What it is | Finland within 42 d (simulated) |
|---|---|---|
| Baseline | Normal Finland, no interventions | 73.4 % |
| Disruption | **SYNTHETIC** referral surge: Helsinki, Vantaa-Kerava, Uusimaa ×1.6, Pirkanmaa ×1.4. Same capacity | 36.6 % (Lohja, Hyvinkää, Jorvi, HUS colonoscopy at 100 %) |
| MPC Recovery | Same surge + CancerFlow response: stall monitoring, reminders in surge counties, behaviour-aware re-routing of ~223 colonoscopy referrals/week over existing capacity | 53.3 % (Jorvi, HUS still at 100 %: remaining bottleneck) |

- **Fair comparison:** model `crc-abm-0.5`, same snapshot, seed 1000, 4 replications, 365 d horizon + 90 d warm-up. Disruption and Recovery share the same arrival stream.
- **Regenerate:** `python -m backend.sim.twins`, single process, about 8 min. It never touches operational data.
- **Recovery** uses the brain's national flow layer: the OR-Tools behaviour-aware optimiser. Resource redistribution is not modelled at national level. Investment is only *recommended* where overload remains, and is not simulated.
- **Δ view:** hospitals coloured by the change in utilisation versus the comparison twin. Absolute-load pulses and alarm markers are hidden there.
- **Demo questions:**
  - Disruption: "Why is Southern Finland suddenly overloaded?"
  - Recovery: "What did CancerFlow change?", "Which bottlenecks remain?", "Should we invest?" Use **⚙ Evaluate with MPC** for an optimiser estimate.

## Fallbacks
- **Voice fails** (no mic or no network): use **Talk to CancerFlow** in Operations. It uses the same `/ops/command` and the same proposal.
- **Claude unavailable:** rule parser for approve/reject/explain and "half to X and Y". Patient messages use templates.
- **Anything stuck:** Story → **run** on the step, or `POST /ops/story/next`. **Reset** restores the start state.
- **Evaluation** runs single-process and small: 8 patients, 200 replays, about 5 s for the disruption and about 10 s for a proposal. It is safe on a laptop.

## Honest caveats
- One regional instance (8 named patients + background occupancy). Capacities, downstream assumptions and acceptance are illustrative (`backend/ops/world.py`, `evaluate.DOWNSTREAM`).
- "Within target" is pooled over 200 stochastic replays (biopsy/cancer/acceptance/attendance) with common random numbers across options.
- The optimiser chooses *where*, and a left-shift step chooses the earliest *when*. Transfers are offered only if P(accept) ≥ 25 % (assumption).
- The LLMs never decide. Patient decisions are seeded draws from persona acceptance. Voice and text only call typed backend tools. Numbers in generated text are checked against the facts.
