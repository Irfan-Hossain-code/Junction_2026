"""Hard-constraint validator for any plan (MIP or heuristic). A recommendable plan must return []."""
from __future__ import annotations

from backend.mpc.pathway.instance import MEETING, PATIENT, SAMPLE, Instance
from backend.mpc.pathway.solver import Plan, Strategy


def validate(inst: Instance, plan: Plan, st: Strategy | None = None) -> list[str]:
    v: list[str] = []
    B = inst.buckets()
    caps = inst.capabilities()
    pmap = {p.pid: p for p in inst.patients}
    load: dict = {}
    for (pid, j), a in plan.assignments.items():
        p, sp = pmap[pid], pmap[pid].stages[j]
        if a.provider is None:
            continue
        if (a.provider, sp.capability) not in caps and not any(m["to"] == a.provider for m in plan.moves) \
                and not any(o.provider == a.provider and o.capability == sp.capability for o in inst.investments if o.id in plan.investments):
            v.append(f"eligibility: {pid} stage {sp.key} at {a.provider} lacks {sp.capability}")
        if sp.mode in (PATIENT, MEETING) and a.provider != p.home_for(sp):
            if not p.consent_transfer:
                v.append(f"consent: {pid} moved without consent")
            if st is not None and not st.allow_patient_transfer:
                v.append(f"strategy: {pid} moved under a no-transfer strategy")
        if sp.mode == SAMPLE and a.provider != p.home_for(sp) and st is not None and not st.allow_sample_routing:
            v.append(f"strategy: {pid} sample routed under a no-routing strategy")
        if not p.diagnosis_known and a.p_needed < 0.999 and not a.tentative:
            v.append(f"unknown pathway: {pid} {sp.key} presented as a confirmed booking")
        bi = max(i for i, (b0, bl) in enumerate(B) if b0 <= a.start_day) if a.start_day < inst.horizon_days else None
        if bi is not None:
            load[a.provider, sp.capability, bi] = load.get((a.provider, sp.capability, bi), 0.0) + a.p_needed
    for (pid, j), a in plan.assignments.items():               # precedence
        nxt = plan.assignments.get((pid, j + 1))
        p = pmap[pid]
        lag = inst.ship_lag_days if (nxt and p.stages[j + 1].mode == SAMPLE and nxt.provider and nxt.provider != p.home_lab) else 0.0
        if nxt and nxt.provider and a.provider and nxt.start_day + 1e-6 < a.start_day + p.stages[j].duration_days + lag:
            v.append(f"precedence: {pid} {nxt.stage} before {a.stage} is complete")
    for (h, c, bi), l in load.items():                         # capacity (expected load) incl. moves/investment
        b0, bl = B[bi]
        cap = inst.bucket_capacity(h, c, b0, bl)
        for m in plan.moves:
            r = next(r for r in inst.movable if r.id == m["resource"])
            if r.capability == c and m["bucket_start"] == b0:
                cap += -m["units"] if r.origin == h else (m["units"] * (1 - r.setup_loss) if m["to"] == h else 0)
        for oid, u in plan.investments.items():
            o = next(o for o in inst.investments if o.id == oid)
            if o.provider == h and o.capability == c:
                cap += u * o.units_per_week * bl / 7
        if l > cap + 1e-6:
            v.append(f"capacity: {h}/{c} bucket {b0} load {l:.2f} > {cap:.2f}")
    if st is not None:
        if st.investment_budget_eur <= 0 and plan.investments:
            v.append("hidden spending: investment in an existing-resource strategy")
        if not st.allow_resource_moves and plan.moves:
            v.append("strategy: resource moved under a no-relocation strategy")
        spent = sum(next(o for o in inst.investments if o.id == k).eur_per_week * (inst.horizon_days / 7) * u for k, u in plan.investments.items())
        if spent > st.investment_budget_eur + 1e-6:
            v.append(f"budget: {spent:.0f} EUR > {st.investment_budget_eur:.0f}")
    return v
