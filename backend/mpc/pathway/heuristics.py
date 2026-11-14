"""Baseline schedulers with the SAME capacity accounting as the MIP (expected load pi for uncertain stages):
  fifo      first come, first served; home provider / home lab only
  priority  urgent first, then earliest deadline; home only
Used as counterfactual baselines and as the downstream repair for diagnostics-only plans.
"""
from __future__ import annotations

from backend.mpc.pathway.instance import SAMPLE, Instance
from backend.mpc.pathway.solver import Plan, StageAssignment


def schedule_greedy(inst: Instance, order: str = "fifo", fixed: dict | None = None) -> Plan:
    B = inst.buckets()
    rem = {}
    for (h, c) in inst.capabilities():
        for bi, (b0, bl) in enumerate(B):
            rem[h, c, bi] = float(inst.bucket_capacity(h, c, b0, bl))
    fixed = fixed or {}
    for a in fixed.values():                                     # fixed (e.g. diagnostic) slots consume capacity first
        if a.provider is None:
            continue
        p = next(q for q in inst.patients if q.pid == a.pid)
        bi = max(i for i, (b0, bl) in enumerate(B) if b0 <= a.start_day)
        rem[a.provider, p.stages[a.j].capability, bi] -= a.p_needed
    if order == "priority":
        pts = sorted(inst.patients, key=lambda p: (p.priority != "urgent", p.deadline_day, p.pid))
    else:
        pts = sorted(inst.patients, key=lambda p: (p.deadline_day, p.pid))   # referral order (same target for all)
    plan = Plan(f"greedy-{order}", {})
    for p in pts:
        prev_end = p.ready_day
        for j, sp in enumerate(p.stages):
            pij = p.p_stage(j)
            tent = (not p.diagnosis_known and pij < 0.999)
            if (p.pid, j) in fixed:
                a = fixed[p.pid, j]
                plan.assignments[p.pid, j] = a
                prev_end = a.start_day + sp.duration_days
                continue
            h = p.home_for(sp)
            chosen = None
            for bi, (b0, bl) in enumerate(B):
                start = max(b0, prev_end) if j == 0 else b0
                if (j == 0 and b0 + bl <= prev_end) or (j > 0 and b0 < prev_end):
                    continue
                if rem.get((h, sp.capability, bi), 0.0) >= pij - 1e-9:
                    rem[h, sp.capability, bi] -= pij
                    chosen = StageAssignment(p.pid, j, sp.key, h, float(start), pij, tent)
                    break
            if chosen is None:
                chosen = StageAssignment(p.pid, j, sp.key, None, float(inst.horizon_days + 7 * (j + 1)), pij, tent)
            plan.assignments[p.pid, j] = chosen
            prev_end = chosen.start_day + sp.duration_days
    plan.status = "heuristic"
    return plan
