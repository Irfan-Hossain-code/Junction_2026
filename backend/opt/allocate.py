"""OR-Tools MIP: re-allocate referral flows and add capacity units to remove bottlenecks equitably.

    min   W_OVER * sum_f over_f                      (load above target utilisation -> queues explode)
        + W_EQ   * z                                 (z >= mean travel minutes of every county: minimax equity)
        + W_TRAV * sum_{m,f} x_mf * t_mf / D          (average travel burden, incl. cross-YTA friction)
        + W_MOVE * sum_{m,f != base} x_mf / D         (prefer minimal disruption of existing referral routes)
    s.t.  sum_f x_mf = d_m                           (all demand served)
          sum_m x_mf <= rho * (C_f + U * y_f) + over_f
          sum_f y_f <= budget,  y_f integer >= 0     (capacity units, only where the capability exists)
          x_mf = 0 if t_mf > max_travel_min (except the current hospital)

Demand d_m is the SAME expected arrival stream the SimPy model uses -> the optimiser's plan is then
VERIFIED in simulation (stochastic queues), not trusted on its own.
"""
from __future__ import annotations

from collections import defaultdict

from ortools.linear_solver import pywraplp

from backend.sim import params as P
from backend.sim.snapshot import Snapshot


def usage_prob(cap: str) -> dict:
    """Probability a patient on each route uses the capability (consistent with the SimPy model)."""
    treat = {c: p for _, c, p in P.TREATMENT_MIX if c}
    if cap == "colonoscopy":
        return {"screening": 1.0, "symptomatic": 1.0}
    if cap in ("ct_imaging", "mdt_crc"):
        return dict(P.P_CANCER)
    if cap in treat:
        return {r: P.P_CANCER[r] * treat[cap] for r in P.P_CANCER}
    return {"screening": 0.0, "symptomatic": 0.0}


def demand(snap: Snapshot, cap: str) -> dict:
    up = usage_prob(cap)
    tot = sum(m["pop"] for m in snap.munis.values())
    out = {}
    for mid, m in snap.munis.items():
        share = m["pop"] / tot
        sf = snap.screening_factor.get(m["county"], 1.0) if snap.screening_factor else 1.0
        out[mid] = share * (snap.arrivals_per_week["screening"] * sf * up["screening"] +
                            snap.arrivals_per_week["symptomatic"] * up["symptomatic"])
    return out


def optimise(snap: Snapshot, cap: str = "colonoscopy", budget_units: int = 4, unit_per_week: float = 5.0,
             rho: float = 0.85, max_travel_min: float = 180.0, cross_yta_penalty_min: float = 30.0,
             w_over: float = 1000.0, w_eq: float = 2.0, w_trav: float = 1.0, w_move: float = 20.0,
             time_limit_s: int = 20) -> dict:
    d = demand(snap, cap)
    D = sum(d.values())
    facs = [f for f in snap.facilities if f"{f}|{cap}" in snap.capacity and snap.capacity[f"{f}|{cap}"] > 0]
    base = {m: max(snap.routing.get(f"{m}|{cap}", [[None, 1]]), key=lambda x: x[1])[0] for m in snap.munis}
    s = pywraplp.Solver.CreateSolver("SCIP")
    s.SetTimeLimit(time_limit_s * 1000)
    x, cost = {}, {}
    for m in snap.munis:
        if d[m] <= 0:
            continue
        for f in facs:
            t = snap.travel.get(f"{m}|{f}")
            if t is None or (t > max_travel_min and f != base[m]):
                continue
            friction = cross_yta_penalty_min if snap.facilities[f]["yta"] != snap.counties.get(snap.munis[m]["county"], {}).get("yta") else 0
            x[m, f] = s.NumVar(0, d[m], f"x[{m},{f}]")
            cost[m, f] = t + friction
    y = {f: s.IntVar(0, budget_units, f"y[{f}]") for f in facs}
    over = {f: s.NumVar(0, s.infinity(), f"over[{f}]") for f in facs}
    z = s.NumVar(0, s.infinity(), "z")
    by_m, by_f = defaultdict(list), defaultdict(list)
    for (m, f), v in x.items():
        by_m[m].append(v)
        by_f[f].append(((m, f), v))
    for m, vs in by_m.items():
        s.Add(sum(vs) == d[m])
    for f in facs:
        s.Add(sum(v for _, v in by_f[f]) <= rho * (snap.capacity[f"{f}|{cap}"] + unit_per_week * y[f]) + over[f])
    s.Add(sum(y.values()) <= budget_units)
    county_d = defaultdict(float)
    for m in by_m:
        county_d[snap.munis[m]["county"]] += d[m]
    for c, Dc in county_d.items():
        terms = [v * cost[k] for k, v in x.items() if snap.munis[k[0]]["county"] == c]
        s.Add(z * Dc >= sum(terms))
    move = sum(v for (m, f), v in x.items() if f != base[m])
    s.Minimize(w_over * sum(over.values()) + w_eq * z + w_trav * sum(v * cost[k] for k, v in x.items()) / D + w_move * move / D)
    status = s.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError(f"solver status {status}")

    # ------------------------------------------------ extract plan
    routing_set, changes = {}, []
    for m in by_m:
        alloc = [(f, x[m, f].solution_value()) for f in facs if (m, f) in x and x[m, f].solution_value() > 1e-6]
        tot = sum(a for _, a in alloc) or 1
        shares = [[f, a / tot] for f, a in alloc if a / tot > 0.01]
        routing_set[f"{m}|{cap}"] = shares
        moved = sum(sh for f, sh in shares if f != base[m])
        if moved > 0.01:
            changes.append({"municipality": m, "name": snap.munis[m]["name"], "county": snap.munis[m]["county"],
                            "from": base[m], "to": [[f, round(sh, 3)] for f, sh in shares if f != base[m]],
                            "share_moved": round(moved, 3), "demand_per_week": round(d[m], 3)})
    adds = [{"facility_id": f, "capability": cap, "units": int(round(y[f].solution_value())),
             "add_per_week": unit_per_week * int(round(y[f].solution_value()))} for f in facs if y[f].solution_value() > 0.5]

    def loads(assign):
        L = defaultdict(float)
        for m, shares in assign.items():
            for f, sh in shares:
                L[f] += d[m] * sh
        return L
    base_assign = {m: [[base[m], 1.0]] for m in by_m}
    Lb, La = loads(base_assign), loads({k.split("|")[0]: v for k, v in routing_set.items()})
    add = {a["facility_id"]: a["add_per_week"] for a in adds}
    util = [{"facility_id": f, "name": snap.facilities[f]["name"], "capacity_before": round(snap.capacity[f"{f}|{cap}"], 2),
             "capacity_after": round(snap.capacity[f"{f}|{cap}"] + add.get(f, 0), 2),
             "load_before": round(Lb[f], 2), "load_after": round(La[f], 2),
             "util_before": round(Lb[f] / snap.capacity[f"{f}|{cap}"], 3),
             "util_after": round(La[f] / (snap.capacity[f"{f}|{cap}"] + add.get(f, 0)), 3)} for f in facs]

    def county_travel(assign):
        acc = defaultdict(lambda: [0.0, 0.0])
        for m, shares in assign.items():
            c = snap.munis[m]["county"]
            for f, sh in shares:
                acc[c][0] += d[m] * sh * snap.travel.get(f"{m}|{f}", 0)
                acc[c][1] += d[m] * sh
        return {c: v[0] / v[1] for c, v in acc.items() if v[1] > 0}
    return {
        "capability": cap, "status": "OPTIMAL" if status == pywraplp.Solver.OPTIMAL else "FEASIBLE",
        "objective": s.Objective().Value(), "solve_ms": s.wall_time(),
        "settings": {"budget_units": budget_units, "unit_per_week": unit_per_week, "rho": rho, "max_travel_min": max_travel_min,
                     "cross_yta_penalty_min": cross_yta_penalty_min, "weights": {"over": w_over, "equity": w_eq, "travel": w_trav, "move": w_move}},
        "capacity_additions": adds, "routing_changes": sorted(changes, key=lambda c: -c["demand_per_week"] * c["share_moved"]),
        "utilisation": sorted(util, key=lambda u: -u["util_before"]),
        "overload_before": round(sum(max(0, Lb[f] - rho * snap.capacity[f"{f}|{cap}"]) for f in facs), 3),
        "overload_after": round(sum(over[f].solution_value() for f in facs), 3),
        "county_travel_min_before": county_travel(base_assign), "county_travel_min_after": county_travel(
            {k.split("|")[0]: v for k, v in routing_set.items()}),
        "scenario_params": {"routing_set": routing_set,
                            "capacity": [{"facility_id": a["facility_id"], "capability": cap, "add_per_week": a["add_per_week"]} for a in adds]},
    }
