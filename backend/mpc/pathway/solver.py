"""Time-expanded, end-to-end pathway MIP (OR-Tools / SCIP), solved lexicographically.

Decision x[p,j,h,b] = 1: remaining stage j of patient p starts at provider h in time bucket b (daily near term,
weekly beyond). An overflow option ("beyond horizon") keeps every instance feasible.

Hard constraints (Tier 0)
  one option per (p, j)                              precedence  S[p,j+1] >= S[p,j] + dur[j] (+ ship lag)
  capacity  sum_p pi[p,j] x <= cap(h,c,b) + moved_in - moved_out + invested      (pi = P(stage needed))
  eligibility (capability), consent, acceptance >= min_accept, travel <= max_travel for PATIENT stages
  resource moves: origin loses units, destination gains units*(1-setup_loss); investment only within a budget
Lexicographic tiers (each locked at its optimum + 1e-4 before the next):
  T1 urgent lateness   sum_{urgent} pi_T * L_p         L_p >= T_p - D_p, L_p >= 0, T_p = start of treatment
  T2 target breaches   sum pi_T * y_p                  T_p - D_p <= M y_p
  T3 weighted lateness sum w_p pi_T L_p
  T4 equity            max_r mean_{p in r} pi_T L_p
  T5 burden            transfers + travel + routed samples + staff moves
  T6 expenditure       investment EUR
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.linear_solver import pywraplp

from backend.mpc.pathway.instance import MEETING, PATIENT, SAMPLE, Instance

TIERS = ("urgent_lateness", "severe_lateness", "breaches", "weighted_lateness", "equity", "burden", "cost")
DIAGNOSTIC_STAGES = ("colonoscopy",)
SEVERE_DAYS = 14.0          # lateness beyond target + 14 d counts as severe (ASSUMPTION, configurable)
EARLY_EPS = 1e-3            # tie-break weight on expected treatment day
URGENT_BUFFER_DAYS = 7.0    # urgent pathways planned to finish >= 7 d before target: slack for no-shows (ASSUMPTION)


@dataclass
class Strategy:
    name: str
    allow_patient_transfer: bool = False
    allow_sample_routing: bool = False
    allow_resource_moves: bool = False
    investment_budget_eur: float = 0.0
    min_accept: float = 0.4               # do not offer a transfer the patient is unlikely to accept
    max_travel_min: float = 240.0
    priority_weight: dict = field(default_factory=lambda: {"urgent": 5.0, "standard": 1.0})   # clinically supplied
    objective: str = "pathway"            # "pathway" (end-to-end) | "diagnostics" (conventional: next-step only)
    time_limit_s: float = 45.0
    label: str = ""


@dataclass
class StageAssignment:
    pid: str
    j: int
    stage: str
    provider: str | None                  # None = beyond horizon
    start_day: float
    p_needed: float
    tentative: bool                       # depends on a diagnosis not yet known -> NOT a confirmed booking


@dataclass
class Plan:
    strategy: str
    assignments: dict                     # (pid, j) -> StageAssignment
    moves: list = field(default_factory=list)          # {resource, to, bucket_start, units}
    investments: dict = field(default_factory=dict)    # option id -> units
    tiers: dict = field(default_factory=dict)
    status: str = ""
    solve_ms: float = 0.0
    notes: list = field(default_factory=list)


def options(inst: Instance, st: Strategy):
    """Eligible (provider, bucket) options per (patient, stage) under a strategy."""
    caps = inst.capabilities()
    B = inst.buckets()
    out = {}
    for p in inst.patients:
        for j, s in enumerate(p.stages):
            provs = sorted({h for (h, c) in caps if c == s.capability})
            home = p.home_for(s)
            if s.mode == PATIENT:
                ok = [h for h in provs if h == home or (
                    st.allow_patient_transfer and p.consent_transfer and inst.providers[h].kind == "public"
                    and p.accept_prob.get(h, 0.0) >= st.min_accept and p.travel_min.get(h, 1e9) <= st.max_travel_min)]
            elif s.mode == SAMPLE:
                ok = [h for h in provs if h == p.home_lab or st.allow_sample_routing]
            else:                                                     # MEETING at the treating centre
                ok = [h for h in provs if h == home or (st.allow_patient_transfer and p.consent_transfer
                                                          and p.accept_prob.get(h, 0.0) >= st.min_accept)]
            out[p.pid, j] = [(h, bi) for h in ok for bi, (b0, bl) in enumerate(B)
                             if (j > 0 or b0 + bl > p.ready_day)]
    return out


def tier_vector(plan: Plan) -> tuple:
    return tuple(plan.tiers.get(k, float("inf")) for k in TIERS)


def lex_better(a: Plan, b: Plan, tol: float = 1e-3) -> bool:
    """True if plan a is lexicographically strictly better than b on the predicted tiers."""
    for x, y in zip(tier_vector(a), tier_vector(b)):
        if x < y - tol:
            return True
        if x > y + tol:
            return False
    return False


def solve(inst: Instance, st: Strategy, hint: Plan | None = None) -> Plan:
    """hint: a feasible plan from a simpler strategy (warm start). Richer strategies then never return a worse
    incumbent than the simpler one when the time budget runs out (the caller keeps the better of the two)."""
    t0 = time.time()
    B = inst.buckets()
    H = inst.horizon_days
    opts = options(inst, st)
    s = pywraplp.Solver.CreateSolver("SCIP")
    x, S, over, LAG = {}, {}, {}, {}
    for p in inst.patients:
        for j, sp in enumerate(p.stages):
            terms, lags = [], []
            for (h, bi) in opts[p.pid, j]:
                v = s.BoolVar(f"x|{p.pid}|{j}|{h}|{bi}")
                x[p.pid, j, h, bi] = v
                start = max(B[bi][0], p.ready_day) if j == 0 else B[bi][0]
                terms.append((v, start))
                if sp.mode == SAMPLE and h != p.home_lab:
                    lags.append(v * inst.ship_lag_days)           # sample must ARRIVE before the lab slot
            o = s.BoolVar(f"over|{p.pid}|{j}")
            over[p.pid, j] = o
            s.Add(sum(v for v, _ in terms) + o == 1)
            S[p.pid, j] = sum(v * t for v, t in terms) + o * (H + 7 * (j + 1))     # beyond horizon: pessimistic
            LAG[p.pid, j] = sum(lags) if lags else 0
        for j in range(len(p.stages) - 1):
            s.Add(S[p.pid, j + 1] >= S[p.pid, j] + p.stages[j].duration_days + LAG[p.pid, j + 1])
    # ------------------------------------------------ capacity (+ resource moves, + investment)
    load: dict = {}
    for (pid, j, h, bi), v in x.items():
        p = next(q for q in inst.patients if q.pid == pid)
        load.setdefault((h, p.stages[j].capability, bi), []).append(p.p_stage(j) * v)
    mv = {}
    if st.allow_resource_moves:
        for r in inst.movable:
            for bi, (b0, bl) in enumerate(B):
                if bl < 7:
                    continue                                          # relocations planned per week, not per day
                for d in r.destinations:
                    mv[r.id, d, bi] = s.IntVar(0, 1, f"mv|{r.id}|{d}|{bi}")
            for bi, (b0, bl) in enumerate(B):
                if bl >= 7:
                    s.Add(sum(mv[r.id, d, bi] for d in r.destinations) <= 1)   # the person is in one place at a time
    inv = {}
    if st.investment_budget_eur > 0:
        weeks = H / 7
        for o in inst.investments:
            inv[o.id] = s.IntVar(0, o.max_units, f"inv|{o.id}")
        s.Add(sum(inv[o.id] * o.eur_per_week * weeks for o in inst.investments) <= st.investment_budget_eur)
    keys = set(load) | {(r.origin, r.capability, bi) for (rid, d, bi) in mv for r in inst.movable if r.id == rid} \
        | {(d, r.capability, bi) for (rid, d, bi) in mv for r in inst.movable if r.id == rid}
    for (h, c, bi) in keys:
        b0, bl = B[bi]
        cap = inst.bucket_capacity(h, c, b0, bl)
        moved_out = sum(mv[r.id, d, bi] * r.units_per_week for r in inst.movable if r.origin == h and r.capability == c
                        for d in r.destinations if (r.id, d, bi) in mv)
        moved_in = sum(mv[r.id, h, bi] * r.units_per_week * (1 - r.setup_loss) for r in inst.movable
                       if r.capability == c and (r.id, h, bi) in mv)
        invested = sum(inv[o.id] * o.units_per_week * bl / 7 for o in inst.investments if o.id in inv and o.provider == h and o.capability == c)
        s.Add(sum(load.get((h, c, bi), [0])) <= cap - moved_out + moved_in + invested)
    for r in inst.movable:                                            # origin never goes negative
        for bi, (b0, bl) in enumerate(B):
            if any((r.id, d, bi) in mv for d in r.destinations):
                s.Add(sum(mv[r.id, d, bi] for d in r.destinations) * r.units_per_week <= inst.bucket_capacity(r.origin, r.capability, b0, bl))
    # ------------------------------------------------ objectives
    M = H + 200
    L, Y, piT, Ls, Lu = {}, {}, {}, {}, {}
    for p in inst.patients:
        jT = next((j for j, sp in enumerate(p.stages) if sp.is_treatment), None)
        if jT is None:
            continue
        piT[p.pid] = p.p_stage(jT)
        L[p.pid] = s.NumVar(0, s.infinity(), f"L|{p.pid}")
        Y[p.pid] = s.BoolVar(f"y|{p.pid}")
        s.Add(L[p.pid] >= S[p.pid, jT] - p.deadline_day)
        if p.priority == "urgent":           # conservative: as if treatment is needed, with a safety buffer
            Lu[p.pid] = s.NumVar(0, s.infinity(), f"Lu|{p.pid}")
            s.Add(Lu[p.pid] >= S[p.pid, jT] - (p.deadline_day - URGENT_BUFFER_DAYS))
        Ls[p.pid] = s.NumVar(0, s.infinity(), f"Ls|{p.pid}")
        s.Add(Ls[p.pid] >= L[p.pid] - SEVERE_DAYS)
        s.Add(S[p.pid, jT] - p.deadline_day <= M * Y[p.pid])
    pw = st.priority_weight
    regions = sorted({p.region for p in inst.patients if p.pid in L})
    z = s.NumVar(0, s.infinity(), "z_equity")
    for r in regions:
        mem = [p for p in inst.patients if p.region == r and p.pid in L]
        s.Add(z * len(mem) >= sum(piT[p.pid] * L[p.pid] for p in mem))
    burden = []
    for (pid, j, h, bi), v in x.items():
        p = next(q for q in inst.patients if q.pid == pid)
        sp = p.stages[j]
        if sp.mode in (PATIENT, MEETING) and h != p.home_for(sp):
            burden.append(v * (1.0 + p.travel_min.get(h, 0.0) / 60.0) * p.p_stage(j))
        elif sp.mode == SAMPLE and h != p.home_lab:
            burden.append(v * 0.2 * p.p_stage(j))
    burden += [v * next(r.burden for r in inst.movable if r.id == k[0]) for k, v in mv.items()]
    cost = sum(inv[o.id] * o.eur_per_week * (H / 7) for o in inst.investments if o.id in inv)
    T = {
        "urgent_lateness": sum(Lu.values()) if Lu else 0,
        "severe_lateness": sum(piT[pid] * Ls[pid] for pid in Ls),
        "breaches": sum(piT[pid] * Y[pid] for pid in Y),
        # + tiny tie-break towards EARLIER treatment (as early as possible even when everyone meets the target);
        #   epsilon is small enough never to trade away lateness within this tier
        "weighted_lateness": sum(pw.get(p.priority, 1.0) * piT[p.pid] * L[p.pid] for p in inst.patients if p.pid in L)
                             + EARLY_EPS * sum(piT[p.pid] * S[p.pid, next(j for j, sp in enumerate(p.stages) if sp.is_treatment)]
                                               for p in inst.patients if p.pid in L),
        "equity": z,
        "burden": sum(burden) if burden else 0,
        "cost": cost if inv else 0,
    }
    if st.objective == "diagnostics":
        # conventional local optimisation: earliest DIAGNOSTIC appointments only, blind to downstream stages
        # conventional local optimisation: the endoscopy unit minimises its own appointment dates (urgent first),
        # blind to pathology, MDT and treatment downstream
        diag = [pw.get(p.priority, 1.0) * S[p.pid, j] for p in inst.patients for j, sp in enumerate(p.stages)
                if sp.key in DIAGNOSTIC_STAGES]
        seq = [("diagnostic_appointments", sum(diag) if diag else 0)]
    else:
        seq = [(k, T[k]) for k in TIERS]
    if hint is not None:
        hv, hval = [], []
        for (pid, j, h, bi), v in x.items():
            a = hint.assignments.get((pid, j))
            hv.append(v)
            hval.append(1.0 if (a and a.provider == h and a.start_day < inst.horizon_days
                                and max(i for i, (b0, bl) in enumerate(B) if b0 <= a.start_day) == bi) else 0.0)
        for (pid, j), v in over.items():
            a = hint.assignments.get((pid, j))
            hv.append(v)
            hval.append(1.0 if (a is None or a.provider is None) else 0.0)
        for v in mv.values():
            hv.append(v)
            hval.append(0.0)
        s.SetHint(hv, hval)
    plan = Plan(st.name, {})
    budget = st.time_limit_s
    for name, expr in seq:
        if isinstance(expr, (int, float)):
            plan.tiers[name] = float(expr)
            continue
        left = budget - (time.time() - t0)
        if left <= 0.2:
            plan.notes.append(f"time budget exhausted before tier {name}")
            break
        s.SetTimeLimit(int(1000 * max(0.5, left / 2)))
        s.Minimize(expr)
        prm = pywraplp.MPSolverParameters()
        prm.SetDoubleParam(prm.RELATIVE_MIP_GAP, 1e-6)
        status = s.Solve(prm)
        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            plan.notes.append(f"tier {name}: solver status {status}")
            break
        if status == pywraplp.Solver.FEASIBLE:
            plan.notes.append(f"tier {name}: feasible, not proven optimal within time")
        val = s.Objective().Value()
        plan.tiers[name] = round(val, 4)
        _extract(inst, st, plan, x, over, mv, inv, B)                  # read BEFORE locking (model change invalidates)
        s.Add(expr <= val + 1e-4)
    plan.status = ("optimal" if not plan.notes else "partial") if plan.assignments else "failed"
    plan.solve_ms = round((time.time() - t0) * 1000)
    if st.objective == "diagnostics":
        _repair_downstream_fifo(inst, plan)
    return plan


def _extract(inst, st, plan, x, over, mv, inv, B):
    plan.assignments = {}
    pmap = {p.pid: p for p in inst.patients}
    for (pid, j, h, bi), v in x.items():
        if v.solution_value() > 0.5:
            p = pmap[pid]
            start = max(B[bi][0], p.ready_day) if j == 0 else B[bi][0]
            plan.assignments[pid, j] = StageAssignment(pid, j, p.stages[j].key, h, float(start), p.p_stage(j),
                                                        tentative=(not p.diagnosis_known and p.p_stage(j) < 0.999))
    for (pid, j), v in over.items():
        if v.solution_value() > 0.5:
            p = pmap[pid]
            plan.assignments[pid, j] = StageAssignment(pid, j, p.stages[j].key, None, float(inst.horizon_days), p.p_stage(j),
                                                        tentative=(not p.diagnosis_known and p.p_stage(j) < 0.999))
    plan.moves = [{"resource": rid, "to": d, "bucket_start": B[bi][0], "units": next(r.units_per_week for r in inst.movable if r.id == rid)}
                  for (rid, d, bi), v in mv.items() if v.solution_value() > 0.5]
    plan.investments = {k: int(round(v.solution_value())) for k, v in inv.items() if v.solution_value() > 0.5}


def _repair_downstream_fifo(inst: Instance, plan: Plan):
    """Diagnostics-only plans keep their diagnostic slots; downstream stages are then booked FIFO at home,
    which is what a locally optimised diagnostic unit hands over to the rest of the pathway."""
    from backend.mpc.pathway.heuristics import schedule_greedy
    pmap = {p.pid: p for p in inst.patients}
    fixed = {k: a for k, a in plan.assignments.items() if pmap[k[0]].stages[k[1]].key in DIAGNOSTIC_STAGES}
    plan.assignments = schedule_greedy(inst, order="fifo", fixed=fixed).assignments
