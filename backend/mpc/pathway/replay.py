"""Stochastic replay of a plan: the digital-twin check of a schedule, with common random numbers.

For each replication, every patient has pre-drawn random numbers (keyed by seed, replication, pid), identical
across strategies:
  * whether each uncertain stage is actually needed (biopsy needed? cancer?)
  * whether a transfer offer is accepted (otherwise rebooked at home, +3 d)
  * attendance at PATIENT stages (no-show: the slot is consumed, rebook +7 d, after 3 no-shows: lost)
Stages are executed day by day on REAL daily capacity (existing + planned resource moves + investments). Requests
are served in the plan's intended order, which is how a plan's prioritisation shows up in outcomes.
"""
from __future__ import annotations

import hashlib
import heapq

import numpy as np

from backend.mpc.pathway.instance import PATIENT, SAMPLE, Instance
from backend.mpc.pathway.solver import Plan

REBOOK_DECLINE, REBOOK_DNA, MAX_ATTEMPTS = 3, 7, 3


def _pid_key(pid: str) -> int:
    return int(hashlib.md5(pid.encode()).hexdigest()[:8], 16)


def realised_capacity(inst: Instance, plan: Plan) -> dict:
    cap = dict(inst.capacity)
    for m in plan.moves:                                      # relocation: origin loses exactly what destination gains
        r = next(r for r in inst.movable if r.id == m["resource"])
        left = m["units"]
        for d in range(m["bucket_start"], m["bucket_start"] + 7):
            take = min(left, cap.get((r.origin, r.capability, d), 0))
            if take <= 0:
                continue
            cap[r.origin, r.capability, d] -= take
            cap[m["to"], r.capability, d] = cap.get((m["to"], r.capability, d), 0) + int(round(take * (1 - r.setup_loss)))
            left -= take
            if left <= 0:
                break
    for oid, units in plan.investments.items():
        o = next(o for o in inst.investments if o.id == oid)
        per_week = o.units_per_week * units
        for w0 in range(0, inst.horizon_days + 120, 7):
            for i in range(per_week):
                d = w0 + (i % 5)
                cap[o.provider, o.capability, d] = cap.get((o.provider, o.capability, d), 0) + 1
    return cap


def replay(inst: Instance, plan: Plan, rep: int, seed: int = 2026) -> dict:
    cap = realised_capacity(inst, plan)
    used: dict = {}
    pmap = {p.pid: p for p in inst.patients}
    draws = {}
    for p in inst.patients:
        rng = np.random.default_rng([seed, rep, _pid_key(p.pid)])
        draws[p.pid] = {"need": rng.random(len(p.stages)), "accept": rng.random(len(p.stages)),
                        "attend": rng.random((len(p.stages), MAX_ATTEMPTS))}
    out = {p.pid: {"treatment_day": None, "needs_treatment": False, "diag_day": None, "colonoscopy_day": None, "lost": False,
                   "transfers": 0, "travel_min": 0.0, "no_shows": 0, "routed_samples": 0} for p in inst.patients}
    heap = []
    for p in inst.patients:
        a = plan.assignments.get((p.pid, 0))
        heapq.heappush(heap, (a.start_day if a else inst.horizon_days, p.priority != "urgent", p.deadline_day, p.pid, 0, p.ready_day))
    while heap:
        planned, _, _, pid, j, ready = heapq.heappop(heap)
        p, sp, d = pmap[pid], pmap[pid].stages[j], draws[pid]
        if d["need"][j] >= sp.p_needed:                       # stage not needed (e.g. no biopsy / benign)
            continue
        if sp.is_treatment:
            out[pid]["needs_treatment"] = True
        a = plan.assignments.get((pid, j))
        h = a.provider if (a and a.provider) else p.home_for(sp)
        earliest = max(planned, ready)
        if sp.mode == SAMPLE and h != p.home_lab:
            earliest = max(earliest, ready + inst.ship_lag_days)
            out[pid]["routed_samples"] += 1
        if sp.mode == PATIENT and h != p.home_for(sp):
            if d["accept"][j] < p.accept_prob.get(h, 0.0):
                out[pid]["transfers"] += 1
                out[pid]["travel_min"] += p.travel_min.get(h, 0.0)
            else:                                             # declined: never treated as accepted
                h, earliest = p.home_for(sp), earliest + REBOOK_DECLINE
        day, attempts = int(np.ceil(earliest)), 0
        limit = inst.horizon_days + 365
        while True:
            while day <= limit and cap.get((h, sp.capability, day), 0) - used.get((h, sp.capability, day), 0) <= 0:
                day += 1
            if day > limit:                                       # no capacity within a year: unserved (counted as lost)
                out[pid]["lost"] = True
                break
            used[h, sp.capability, day] = used.get((h, sp.capability, day), 0) + 1
            if sp.mode != PATIENT:
                break
            p_att = p.p_attend - 0.03 * max(0.0, p.travel_min.get(h, 0.0) / 60 - 1)
            if d["attend"][j, min(attempts, MAX_ATTEMPTS - 1)] < p_att:
                break
            out[pid]["no_shows"] += 1
            attempts += 1
            if attempts >= MAX_ATTEMPTS:
                out[pid]["lost"] = True
                break
            day += REBOOK_DNA
        if out[pid]["lost"]:
            continue
        if sp.key == "colonoscopy":
            out[pid]["colonoscopy_day"] = day
        if sp.key in ("pathology",) or (sp.key == "colonoscopy" and j == len(p.stages) - 1):
            out[pid]["diag_day"] = day + sp.duration_days
        if sp.is_treatment:
            out[pid]["treatment_day"] = day
            continue
        if j + 1 < len(p.stages):
            nxt = plan.assignments.get((pid, j + 1))
            heapq.heappush(heap, (nxt.start_day if nxt else day + sp.duration_days, p.priority != "urgent", p.deadline_day,
                                  pid, j + 1, day + sp.duration_days))
    over = sum(1 for k, v in used.items() if v > cap.get(k, 0))
    return {"patients": out, "overbooked_slots": over}


def kpis(inst: Instance, rep_out: dict, plan: Plan) -> dict:
    pmap = {p.pid: p for p in inst.patients}
    rows = [(pmap[pid], o) for pid, o in rep_out["patients"].items() if o["needs_treatment"]]
    late = []
    for p, o in rows:
        if o["lost"] or o["treatment_day"] is None:
            late.append((p, None))
        else:
            late.append((p, max(0.0, o["treatment_day"] - p.deadline_day)))
    n = max(1, len(late))
    within = [1.0 if l == 0 else 0.0 for _, l in late]
    w = {"urgent": 5.0, "standard": 1.0}
    big = 60.0                                                # lost patient counted as 60 d late (penalty, disclosed)
    by_region: dict = {}
    for (p, l), wi in zip(late, within):
        by_region.setdefault(p.region, []).append(wi)
    reg = {r: float(np.mean(v)) for r, v in by_region.items()}
    diag = [o["diag_day"] for o in rep_out["patients"].values() if o["diag_day"] is not None]
    return {
        "patients_needing_treatment": len(late),
        "pct_within_target": 100 * float(np.mean(within)) if late else None,
        "breaches": float(sum(1 for x in within if x == 0)),
        "urgent_breaches": float(sum(1 for (p, l) in late if p.priority == "urgent" and (l is None or l > 0))),
        "weighted_lateness_days": float(sum(w.get(p.priority, 1) * (big if l is None else l) for p, l in late)),
        "severe_delays_over_14d": float(sum(1 for _, l in late if l is None or l > 14)),
        "mean_treatment_day": float(np.mean([o["treatment_day"] for _, o in rows if o["treatment_day"] is not None] or [np.nan])),
        "mean_diagnosis_day": float(np.mean(diag)) if diag else None,
        "mean_colonoscopy_day": float(np.mean([o["colonoscopy_day"] for o in rep_out["patients"].values() if o["colonoscopy_day"] is not None] or [np.nan])),
        "regional_gap_pp": 100 * (max(reg.values()) - min(reg.values())) if len(reg) > 1 else 0.0,
        "pct_within_by_region": {r: round(100 * v, 1) for r, v in reg.items()},
        "transfers": float(sum(o["transfers"] for o in rep_out["patients"].values())),
        "patient_travel_hours": float(sum(o["travel_min"] for o in rep_out["patients"].values()) / 60),
        "routed_samples": float(sum(o["routed_samples"] for o in rep_out["patients"].values())),
        "no_shows": float(sum(o["no_shows"] for o in rep_out["patients"].values())),
        "lost_to_follow_up": float(sum(1 for o in rep_out["patients"].values() if o["lost"])),
        "staff_relocation_weeks": float(len(plan.moves)),
        "overbooked_slots": float(rep_out["overbooked_slots"]),
    }
