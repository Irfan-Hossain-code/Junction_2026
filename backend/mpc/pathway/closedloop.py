"""Closed-loop (receding-horizon) execution of the whole-pathway MPC, versus open-loop execution of one plan.

Both are executed on the SAME true world: the same patients, the same pre-drawn random numbers (biopsy, cancer,
acceptance, attendance; common random numbers via replay._pid_key) and the same disruption.

  open loop    solve once at day 0 on NOMINAL capacity, then execute it (late stages fall back to the next free day).
  closed loop  at each control epoch (every `replan_every` days AND when a disruption is first observed):
                 observe the state: stages completed, patients waiting, declined hospitals, capacity already used,
                                    and capacity outages OBSERVED so far (never future ones);
                 re-solve the remaining pathways with the SAME lexicographic MIP (solver.solve, warm-started);
                 execute only until the next epoch.
               Declined transfer offers become hard exclusions in the next plan (patient decisions feed back).

No new optimisation engine: it reuses Instance / Strategy / solve / schedule_greedy / replay's random streams / kpis.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import replace

import numpy as np

from backend.mpc.pathway.heuristics import schedule_greedy
from backend.mpc.pathway.instance import PATIENT, SAMPLE, Instance
from backend.mpc.pathway.replay import MAX_ATTEMPTS, REBOOK_DECLINE, REBOOK_DNA, _pid_key, kpis
from backend.mpc.pathway.solver import Strategy, lex_better, solve


def _draws(inst: Instance, rep: int, seed: int) -> dict:
    out = {}
    for p in inst.patients:
        rng = np.random.default_rng([seed, rep, _pid_key(p.pid)])
        out[p.pid] = {"need": rng.random(len(p.stages)), "accept": rng.random(len(p.stages)),
                      "attend": rng.random((len(p.stages), MAX_ATTEMPTS))}
    return out


def true_capacity(inst: Instance, disruption: dict | None) -> dict:
    cap = dict(inst.capacity)
    if disruption:
        for d in range(*disruption["days"]):
            cap[disruption["provider"], disruption["capability"], d] = 0
    return cap


def _sub_instance(inst: Instance, st: dict, t: int, known_cap: dict, used: dict) -> tuple[Instance, dict]:
    """Remaining-pathway instance at absolute day t (relative days, capacity net of what is already used)."""
    pats, idx = [], {}
    for p in inst.patients:
        s = st[p.pid]
        if s["done"] or s["j"] >= len(p.stages):
            continue
        acc = dict(p.accept_prob)
        for h in s["declined"]:
            acc[h] = 0.0                                       # a refusal is a hard exclusion from now on
        pats.append(replace(p, stages=p.stages[s["j"]:], ready_day=max(0.0, s["ready"] - t),
                            deadline_day=p.deadline_day - t, accept_prob=acc,
                            diagnosis_known=p.diagnosis_known or s["j"] >= 2))
        idx[p.pid] = s["j"]
    H = inst.horizon_days
    cap = {}
    for (h, c, d), v in known_cap.items():
        if d >= t:
            left = v - used.get((h, c, d), 0)
            if left > 0:
                cap[h, c, d - t] = left
    return Instance(inst.providers, cap, pats, horizon_days=H, near_term_days=inst.near_term_days,
                    ship_lag_days=inst.ship_lag_days, movable=[], investments=[], meta=inst.meta), idx


def _execute(inst, orig, plan, offset, idx, st, true_cap, used, draws, out, t_end):
    """Execute plan (relative days / stage indices, from absolute day `offset`) on the TRUE capacity until day t_end.
    `orig`: the original patients (absolute stage indices, deadlines and true acceptance)."""
    pmap = orig
    heap = []
    for pid, j0 in idx.items():
        a = plan.assignments.get((pid, 0))
        start = offset + (a.start_day if a else 0)
        heapq.heappush(heap, (start, pmap[pid].priority != "urgent", pmap[pid].deadline_day, pid))
    while heap:
        planned, _, _, pid = heapq.heappop(heap)
        s, p = st[pid], pmap[pid]
        j = s["j"]
        if s["done"] or j >= len(p.stages) or planned >= t_end:
            continue
        sp, d = p.stages[j], draws[pid]
        if d["need"][j] >= sp.p_needed:                         # stage not needed: pathway ends here
            s["done"] = True
            continue
        if sp.is_treatment:
            out[pid]["needs_treatment"] = True
        a = plan.assignments.get((pid, j - idx[pid]))
        h = a.provider if (a and a.provider) else p.home_for(sp)
        earliest = max(planned, s["ready"])
        if sp.mode == SAMPLE and h != p.home_lab:
            earliest = max(earliest, s["ready"] + inst.ship_lag_days)
        if sp.mode == PATIENT and h != p.home_for(sp) and (j, h) not in s["offered"]:
            s["offered"].add((j, h))
            if d["accept"][j] < p.accept_prob.get(h, 0.0):
                out[pid]["transfers"] += 1
                out[pid]["travel_min"] += p.travel_min.get(h, 0.0)
                s["accepted"][j] = h
            else:                                               # declined: observed by the controller at the next epoch
                s["declined"].add(h)
                h, earliest = p.home_for(sp), earliest + REBOOK_DECLINE
        elif sp.mode == PATIENT and h != p.home_for(sp) and s["accepted"].get(j) != h:
            h = p.home_for(sp)
        day = int(math.ceil(earliest))
        while day < t_end and true_cap.get((h, sp.capability, day), 0) - used.get((h, sp.capability, day), 0) <= 0:
            day += 1
        if day >= t_end:                                        # not served in this window: waits for the next plan
            s["ready"] = max(s["ready"], earliest)
            continue
        used[h, sp.capability, day] = used.get((h, sp.capability, day), 0) + 1
        if sp.mode == SAMPLE and h != p.home_lab:
            out[pid]["routed_samples"] += 1
        if sp.mode == PATIENT:
            p_att = p.p_attend - 0.03 * max(0.0, p.travel_min.get(h, 0.0) / 60 - 1)
            if d["attend"][j, min(s["attempts"], MAX_ATTEMPTS - 1)] >= p_att:
                out[pid]["no_shows"] += 1
                s["attempts"] += 1
                if s["attempts"] >= MAX_ATTEMPTS:
                    out[pid]["lost"], s["done"] = True, True
                    continue
                s["ready"] = day + REBOOK_DNA
                heapq.heappush(heap, (s["ready"], p.priority != "urgent", p.deadline_day, pid))
                continue
        s["attempts"] = 0
        if sp.key == "colonoscopy":
            out[pid]["colonoscopy_day"] = day
        if sp.key == "pathology" or (sp.key == "colonoscopy" and j == len(p.stages) - 1):
            out[pid]["diag_day"] = day + sp.duration_days
        if sp.is_treatment:
            out[pid]["treatment_day"] = day
            s["done"] = True
            continue
        s["j"], s["ready"] = j + 1, day + sp.duration_days
        nxt = plan.assignments.get((pid, j + 1 - idx[pid]))
        heapq.heappush(heap, (offset + nxt.start_day if nxt else s["ready"], p.priority != "urgent", p.deadline_day, pid))


def run(inst: Instance, st: Strategy, rep: int, disruption: dict | None = None, closed: bool = True,
        replan_every: int = 7, seed: int = 2026, horizon_end: int | None = None) -> dict:
    true_cap = true_capacity(inst, disruption)
    draws = _draws(inst, rep, seed)
    state = {p.pid: {"j": 0, "ready": p.ready_day, "done": False, "declined": set(), "offered": set(), "accepted": {}, "attempts": 0}
             for p in inst.patients}
    out = {p.pid: {"treatment_day": None, "needs_treatment": False, "diag_day": None, "colonoscopy_day": None, "lost": False,
                   "transfers": 0, "travel_min": 0.0, "no_shows": 0, "routed_samples": 0} for p in inst.patients}
    used: dict = {}
    end = horizon_end or (inst.horizon_days + 365)
    epochs = sorted({0, *range(replan_every, end, replan_every)} | ({disruption["days"][0]} if (closed and disruption) else set()))
    if not closed:
        epochs = [0]
    log = []
    hint = None
    for k, t in enumerate(epochs):
        if all(s["done"] for s in state.values()):
            break
        known = true_capacity(inst, disruption if (disruption and t >= disruption["days"][0]) else None) if closed else dict(inst.capacity)
        sub, idx = _sub_instance(inst, state, t, known, used)
        if not sub.patients:
            break
        greedy = schedule_greedy(sub, "priority")
        try:
            plan = solve(sub, replace(st, time_limit_s=st.time_limit_s), hint=greedy)
            if lex_better(greedy, plan):
                plan = greedy
        except Exception:
            plan = greedy
        nxt = epochs[k + 1] if (closed and k + 1 < len(epochs)) else end
        log.append({"day": t, "patients_open": len(sub.patients), "status": plan.status,
                    "outage_known": bool(disruption and closed and t >= disruption["days"][0])})
        _execute(sub, {p.pid: p for p in inst.patients}, plan, t, idx, state, true_cap, used, draws, out, nxt)
        hint = plan
    # anything still unserved within the run horizon counts as lost (consistent with replay)
    for pid, s in state.items():
        if not s["done"]:
            out[pid]["lost"] = True
    return {"patients": out, "overbooked_slots": sum(1 for k, v in used.items() if v > true_cap.get(k, 0)), "epochs": log}


def compare(inst: Instance, st: Strategy, disruption: dict, reps: int = 5, replan_every: int = 7) -> dict:
    """Open vs closed loop on the same world. Returns pooled KPIs + the closed loop's control epochs (rep 0)."""
    res = {}
    for mode in ("open_loop", "closed_loop"):
        ks, ep = [], None
        for r in range(reps):
            o = run(inst, st, r, disruption, closed=(mode == "closed_loop"), replan_every=replan_every)
            ks.append(kpis(inst, o, schedule_greedy(inst, "fifo")))
            if r == 0:
                ep = o["epochs"]
        agg = {}
        for k in ("pct_within_target", "urgent_breaches", "breaches", "weighted_lateness_days", "mean_treatment_day",
                  "routed_samples", "transfers", "lost_to_follow_up", "overbooked_slots"):
            v = [x[k] for x in ks if x.get(k) is not None and not (isinstance(x[k], float) and math.isnan(x[k]))]
            agg[k] = round(float(np.mean(v)), 2) if v else None
        res[mode] = {"kpis": agg, "epochs": ep}
    return res


def main(reps: int = 3, time_limit: float = 8.0):
    """python -m backend.mpc.pathway.closedloop  -> results/closed_loop.json (single process, a few minutes)."""
    import json
    import time
    from backend.mpc.pathway.benchmark import RESULTS, clean
    from backend.mpc.pathway.demo import build_demo
    t0 = time.time()
    inst = build_demo(7)
    st = Strategy("B_national_pathway", allow_patient_transfer=True, allow_sample_routing=True, time_limit_s=time_limit)
    dis = {"provider": "LAB_E", "capability": "pathology", "days": (3, 10), "label": "Eastern Finland shared pathology lab offline days 3-9"}
    res = compare(inst, st, dis, reps=reps)
    doc = {"instance": inst.meta.get("name"), "disruption": dis, "replications": reps, "solver_time_limit_s": time_limit,
           "replan_every_days": 7, "event_triggered": "re-solve when the outage is first observed",
           "note": "Same executor, same patients, same random numbers and the same outage for both modes. The open loop "
                   "plans once on nominal capacity; the closed loop re-plans on observed state (completed stages, used "
                   "capacity, declined hospitals, observed outages). SIMULATED, illustrative instance.",
           **res, "runtime_s": round(time.time() - t0, 1)}
    (RESULTS / "closed_loop.json").write_text(json.dumps(clean(doc), indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: res[k]["kpis"] for k in res}, indent=1), doc["runtime_s"])


if __name__ == "__main__":
    main()
