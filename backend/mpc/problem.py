"""Operational MPC step: assign each waiting patient's NEXT pathway step to a (facility, day) slot over the horizon.

Lexicographic, solved as a sequence of MIPs with OR-Tools (SCIP):
  L1  clinical:  min  sum_p w_p * E[ max(0, T_p - tau_p) ]          (clinically weighted expected target violation)
  L2  waiting:   min  sum_p E[T_p]                                   (overall referral-to-treatment time)
  L3  burden:    min  sum_p transfer_p + travel_p/60 + admin_p - plan_bonus_p
Each tier is optimised subject to the previous tiers staying within a small tolerance of their optimum, so a lower
tier can never buy its improvement with clinical harm.

T_p (completion time) for option (f, d) = elapsed_p + d + R_f, R_f ~ downstream forecast samples (sample-average
approximation over S scenarios -> stochastic, still linear in x). If f is not the patient's home, the offer is
accepted with probability a_pf; if declined, the patient falls back to home after the horizon (pessimistic).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from ortools.linear_solver import pywraplp

from backend.mpc.forecast import DownstreamForecast
from backend.mpc.state import MPCState, PatientState


@dataclass
class MPCConfig:
    samples: int = 32                      # SAA scenarios for downstream uncertainty
    seed: int = 7
    priority_weight: dict = field(default_factory=lambda: {"urgent": 5.0, "standard": 1.0})   # clinically supplied
    urgent_max_days: int | None = None     # mandatory: urgent next step within N days (must be clinically supplied)
    lex_tolerance: float = 0.0             # relative slack on higher tiers: 0 = strict lexicographic (clinical tier dominates)
    lex_abs_tolerance: float = 1e-4        # numerical tolerance only
    mip_gap: float = 1e-7                  # relative MIP gap per tier
    budget_s: float = 5.0                  # total compute budget for all tiers
    transfer_cost: float = 1.0
    admin_cost: float = 0.5                # extra burden if a transfer needs authorisation
    plan_bonus: float = 0.3                # L3 nudge towards the strategic (weekly) plan's preferred facilities
    review_delay_days: float = 1.0         # time until the next authorised reviewer acts on a recommendation


@dataclass
class Option:
    p: PatientState
    facility: str
    day: int
    ev_violation: float                    # E[max(0, T - tau)]
    p_violation: float                     # P(T > tau)
    e_completion: float                    # E[T]
    burden: float


def build_options(state: MPCState, fc: DownstreamForecast, cfg: MPCConfig, preferred: dict | None = None) -> tuple[list[Option], dict]:
    rng = np.random.default_rng(cfg.seed)
    cap = state.capacity()
    R = {}                                  # common random numbers: the same downstream samples for every option at f
    opts: list[Option] = []
    defer: dict = {}
    for p in state.patients:
        facs = [p.home_facility] if not p.consent_to_transfer else list(dict.fromkeys((p.home_facility,) + tuple(p.eligible)))
        for f in facs:
            if f not in R:
                R[f] = fc.samples(f, cfg.samples, rng)
        fb = p.elapsed_days + state.horizon_days + R[p.home_facility]          # declined / unscheduled -> back home later
        defer[p.pid] = (float(np.mean(np.maximum(0, fb - p.target_days))), float(np.mean(fb > p.target_days)), float(np.mean(fb)))
        for (f, c, d), free in cap.items():
            if c != p.next_capability or f not in facs:
                continue
            T = p.elapsed_days + d + R[f]
            a = 1.0 if f == p.home_facility else float(p.accept_prob.get(f, 0.0))
            ev = a * np.mean(np.maximum(0, T - p.target_days)) + (1 - a) * defer[p.pid][0]
            pv = a * np.mean(T > p.target_days) + (1 - a) * defer[p.pid][1]
            et = a * np.mean(T) + (1 - a) * defer[p.pid][2]
            moved = f != p.home_facility
            burden = (cfg.transfer_cost + (cfg.admin_cost if p.transfer_needs_authorisation else 0.0)) * moved \
                + p.travel_min.get(f, 0.0) / 60.0 - (cfg.plan_bonus if preferred and f in preferred.get(p.pid, ()) else 0.0)
            opts.append(Option(p, f, d, float(ev), float(pv), float(et), float(burden)))
    return opts, defer


@dataclass
class Plan:
    assignment: dict                       # pid -> (facility, day) | None (deferred beyond horizon)
    status: str                            # "lexicographic-optimal" | "fallback-greedy" | ...
    tiers: dict                            # objective values per tier
    solve_ms: float
    notes: list = field(default_factory=list)
    urgent_infeasible: list = field(default_factory=list)


def evaluate(state: MPCState, opts: list[Option], defer: dict, assignment: dict, cfg: MPCConfig) -> dict:
    """Same scoring for any policy (MPC, greedy, do-nothing) -> fair predicted differences."""
    idx = {(o.p.pid, o.facility, o.day): o for o in opts}
    w = cfg.priority_weight
    L1 = L2 = L3 = viol = 0.0
    for p in state.patients:
        a = assignment.get(p.pid)
        if a is None:
            ev, pv, et, b = defer[p.pid][0], defer[p.pid][1], defer[p.pid][2], 0.0
        else:
            o = idx[(p.pid, a[0], a[1])]
            ev, pv, et, b = o.ev_violation, o.p_violation, o.e_completion, o.burden
        L1 += w.get(p.priority, 1.0) * ev
        L2 += et
        L3 += b
        viol += pv
    n = max(1, len(state.patients))
    return {"L1_weighted_expected_violation_days": round(L1, 3), "L2_expected_completion_days_total": round(L2, 2),
            "L3_burden": round(L3, 3), "expected_target_violations": round(viol, 2), "mean_expected_rtt_days": round(L2 / n, 2)}


def greedy(state: MPCState, opts: list[Option], cfg: MPCConfig, local_only: bool = True) -> dict:
    """Myopic baseline / fallback: urgent first, then longest waiting; earliest free slot (at home if local_only).
    Always feasible (may defer)."""
    cap = dict(state.capacity())
    by_p: dict = {}
    for o in opts:
        by_p.setdefault(o.p.pid, []).append(o)
    order = sorted(state.patients, key=lambda p: (p.priority != "urgent", -p.elapsed_days))
    out = {}
    for p in order:
        cands = [o for o in by_p.get(p.pid, []) if (not local_only or o.facility == p.home_facility)
                 and cap.get((o.facility, p.next_capability, o.day), 0) > 0]
        if not cands:
            out[p.pid] = None
            continue
        o = min(cands, key=lambda o: (o.day, o.facility != p.home_facility))
        cap[(o.facility, p.next_capability, o.day)] -= 1
        out[p.pid] = (o.facility, o.day)
    return out


def solve(state: MPCState, fc: DownstreamForecast, cfg: MPCConfig, preferred: dict | None = None,
          fixed_capacity_use: dict | None = None) -> tuple[Plan, list[Option], dict]:
    t0 = time.time()
    opts, defer = build_options(state, fc, cfg, preferred)
    s = pywraplp.Solver.CreateSolver("SCIP")
    if s is None:
        return Plan(greedy(state, opts, cfg), "fallback-greedy", {}, 0, ["SCIP unavailable"]), opts, defer
    x = {i: s.BoolVar(f"x{i}") for i in range(len(opts))}
    y = {p.pid: s.BoolVar(f"defer_{p.pid}") for p in state.patients}
    by_p: dict = {p.pid: [] for p in state.patients}
    by_slot: dict = {}
    for i, o in enumerate(opts):
        by_p[o.p.pid].append(i)
        by_slot.setdefault((o.facility, o.p.next_capability, o.day), []).append(i)
    for pid, idx in by_p.items():
        s.Add(sum(x[i] for i in idx) + y[pid] == 1)                                  # one decision per patient
    cap = state.capacity()
    for k, idx in by_slot.items():
        s.Add(sum(x[i] for i in idx) <= cap.get(k, 0) - (fixed_capacity_use or {}).get(k, 0))   # hard capacity
    urgent_infeasible = []
    if cfg.urgent_max_days is not None:                                              # mandatory clinical safety rule
        for p in state.patients:
            if p.priority != "urgent":
                continue
            ok = [i for i in by_p[p.pid] if opts[i].day <= cfg.urgent_max_days]
            if ok:
                s.Add(sum(x[i] for i in ok) == 1)
            else:
                urgent_infeasible.append(p.pid)
    w = cfg.priority_weight
    L1 = sum(w.get(o.p.priority, 1.0) * o.ev_violation * x[i] for i, o in enumerate(opts)) \
        + sum(w.get(p.priority, 1.0) * defer[p.pid][0] * y[p.pid] for p in state.patients)
    L2 = sum(o.e_completion * x[i] for i, o in enumerate(opts)) + sum(defer[p.pid][2] * y[p.pid] for p in state.patients)
    L3 = sum(o.burden * x[i] for i, o in enumerate(opts))
    tiers, notes = {}, []
    assignment = None
    for name, expr in (("L1", L1), ("L2", L2), ("L3", L3)):
        left = cfg.budget_s - (time.time() - t0)
        if left <= 0.05:
            notes.append(f"budget exhausted before {name}; keeping previous tier's solution")
            break
        s.SetTimeLimit(int(left * 1000 / (3 if name == "L1" else 2 if name == "L2" else 1)))
        s.Minimize(expr)
        prm = pywraplp.MPSolverParameters()
        prm.SetDoubleParam(prm.RELATIVE_MIP_GAP, cfg.mip_gap)      # default 1e-4 would let 'optimal' hide clinical slack
        st = s.Solve(prm)
        if st not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            if name == "L1":
                a = greedy(state, opts, cfg)
                return Plan(a, "fallback-greedy", evaluate(state, opts, defer, a, cfg), (time.time() - t0) * 1000,
                            [f"solver status {st} at L1"], urgent_infeasible), opts, defer
            notes.append(f"{name} not solved (status {st}); previous tier kept")
            break
        v = s.Objective().Value()
        tiers[name] = round(v, 4)
        if st == pywraplp.Solver.FEASIBLE:
            notes.append(f"{name} feasible but not proven optimal within budget")
        assignment = {p.pid: None for p in state.patients}          # read BEFORE changing the model
        for i, o in enumerate(opts):
            if x[i].solution_value() > 0.5:
                assignment[o.p.pid] = (o.facility, o.day)
        s.Add(expr <= v * (1 + cfg.lex_tolerance) + cfg.lex_abs_tolerance)   # lock this tier before the next one
    if assignment is None:                                   # budget gone before L1 -> feasible fallback
        a = greedy(state, opts, cfg)
        return Plan(a, "fallback-greedy", evaluate(state, opts, defer, a, cfg), (time.time() - t0) * 1000,
                    notes + ["no MIP tier solved within budget"], urgent_infeasible), opts, defer
    return (Plan(assignment, "lexicographic-optimal" if not notes else "lexicographic-partial", tiers,
                 (time.time() - t0) * 1000, notes, urgent_infeasible), opts, defer)
