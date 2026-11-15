"""Counterfactual benchmark: same patients, same capacity, same random streams for every strategy.

    python -m backend.mpc.pathway.benchmark            # writes backend/mpc/pathway/results/east_finland.json

Order follows the intervention hierarchy: baselines -> local -> patient/sample movement -> resource movement
(chosen only if MATERIALLY better) -> bottleneck identification -> budget-limited investment, ranked per EUR.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy import stats

from backend.mpc.pathway.demo import build_demo
from backend.mpc.pathway.heuristics import schedule_greedy
from backend.mpc.pathway.instance import PATIENT, SAMPLE, Instance
from backend.mpc.pathway.replay import kpis, replay
from backend.mpc.pathway.solver import Plan, Strategy, lex_better, solve
from backend.mpc.pathway.validate import validate

RESULTS = Path(__file__).resolve().parent / "results"
MATERIAL_IMPROVEMENT = 0.20   # ENGINEERING HYPOTHESIS (not a clinical threshold): resource movement must deliver
                              # >= 20 % more weighted-lateness reduction than patient movement to be preferred
KEYS = ["pct_within_target", "breaches", "urgent_breaches", "severe_delays_over_14d", "weighted_lateness_days",
        "mean_colonoscopy_day", "mean_diagnosis_day", "mean_treatment_day", "regional_gap_pp", "transfers",
        "patient_travel_hours", "routed_samples", "staff_relocation_weeks", "no_shows", "lost_to_follow_up", "overbooked_slots"]


def evaluate(inst: Instance, plan: Plan, reps: int, seed: int) -> dict:
    runs = [kpis(inst, replay(inst, plan, r, seed), plan) for r in range(reps)]
    out = {}
    for k in KEYS:
        v = np.array([r[k] for r in runs if r[k] is not None and not (isinstance(r[k], float) and np.isnan(r[k]))], dtype=float)
        if len(v) == 0:
            out[k] = None
            continue
        h = float(stats.t.ppf(0.975, len(v) - 1) * v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 else 0.0
        out[k] = {"mean": round(float(v.mean()), 2), "ci95": [round(float(v.mean() - h), 2), round(float(v.mean() + h), 2)]}
    reg = {}
    for r in runs:
        for name, val in r["pct_within_by_region"].items():
            reg.setdefault(name, []).append(val)
    out["pct_within_by_region"] = {k: round(float(np.mean(v)), 1) for k, v in reg.items()}
    return out


def patient_outcomes(inst, plan, reps, seed):
    acc: dict = {}
    for r in range(reps):
        o = replay(inst, plan, r, seed)["patients"]
        for pid, x in o.items():
            acc.setdefault(pid, []).append(x)
    res = {}
    for pid, xs in acc.items():
        t = [x["treatment_day"] for x in xs if x["treatment_day"] is not None]
        res[pid] = {"p_needs_treatment": round(float(np.mean([x["needs_treatment"] for x in xs])), 2),
                    "treatment_day_mean": round(float(np.mean(t)), 1) if t else None,
                    "treatment_day_p10_p90": [round(float(np.percentile(t, 10)), 1), round(float(np.percentile(t, 90)), 1)] if t else None,
                    "diag_day_mean": round(float(np.mean([x["diag_day"] for x in xs if x["diag_day"] is not None] or [np.nan])), 1)}
    return res


def bottlenecks(inst: Instance, plan: Plan, horizon_share: float = 0.5) -> list:
    """Pass B: which resources stay saturated under the BEST existing-resource plan? Planned (expected) utilisation
    over the first part of the horizon, where the current waiting list concentrates. >= 95 % = binding."""
    from backend.mpc.pathway.replay import realised_capacity
    cap = realised_capacity(inst, plan)
    win = int(inst.horizon_days * horizon_share)
    totals: dict = {}
    for (h, c, d), v in cap.items():
        if d < win and v > 0:
            totals[h, c] = totals.get((h, c), 0) + v
    used: dict = {}
    pmap = {p.pid: p for p in inst.patients}
    for (pid, j), a in plan.assignments.items():
        if a.provider and a.start_day < win:
            k = (a.provider, pmap[pid].stages[j].capability)
            used[k] = used.get(k, 0.0) + a.p_needed
    rows = [{"provider": inst.providers[h].name, "provider_id": h, "capability": c,
             "planned_utilisation": round(used.get((h, c), 0.0) / tot, 3), "capacity_in_window": tot, "window_days": win,
             "binding": used.get((h, c), 0.0) / tot >= 0.95} for (h, c), tot in totals.items()]
    rows.sort(key=lambda r: -r["planned_utilisation"])
    return rows


def plan_actions(inst: Instance, plan: Plan, base: Plan) -> list:
    pmap = {p.pid: p for p in inst.patients}
    acts = []
    for (pid, j), a in plan.assignments.items():
        p, sp = pmap[pid], pmap[pid].stages[j]
        b = base.assignments.get((pid, j))
        home = p.home_for(sp)
        if a.provider and a.provider != home:
            acts.append({"type": "route_sample" if sp.mode == SAMPLE else "offer_transfer", "patient": pid, "stage": sp.key,
                         "from": home, "to": a.provider, "planned_day": a.start_day, "tentative": a.tentative,
                         "p_accept": None if sp.mode == SAMPLE else p.accept_prob.get(a.provider),
                         "status": "requires_authorisation" if sp.mode != SAMPLE else "requires_coordinator_approval"})
        elif b and a.provider == b.provider and abs(a.start_day - b.start_day) >= 3:
            acts.append({"type": "reschedule", "patient": pid, "stage": sp.key, "at": a.provider, "from_day": b.start_day,
                         "to_day": a.start_day, "tentative": a.tentative, "status": "recommended"})
    for m in plan.moves:
        r = next(r for r in inst.movable if r.id == m["resource"])
        acts.append({"type": "relocate_resource", "resource": r.id, "capability": r.capability, "from": r.origin, "to": m["to"],
                     "week_starting_day": m["bucket_start"], "units": m["units"],
                     "origin_loses": m["units"], "status": "requires_authorisation"})
    for oid, u in plan.investments.items():
        acts.append({"type": "invest", "option": oid, "units": u, "status": "requires_budget_decision"})
    return acts


def clean(x):
    """NaN/inf -> None so the document is strict JSON."""
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def _rejections(results: dict, chosen: str) -> dict:
    out = {}
    c = results[chosen]["kpis"]
    for k, v in results.items():
        if k == chosen:
            continue
        r = v["kpis"]
        why = []
        if r["urgent_breaches"]["mean"] > c["urgent_breaches"]["mean"] + 1e-9:
            why.append(f"more urgent breaches ({r['urgent_breaches']['mean']} vs {c['urgent_breaches']['mean']})")
        if r["breaches"]["mean"] > c["breaches"]["mean"] + 1e-9:
            why.append(f"more target breaches ({r['breaches']['mean']} vs {c['breaches']['mean']})")
        if r["weighted_lateness_days"]["mean"] > c["weighted_lateness_days"]["mean"] + 1e-9:
            why.append(f"higher weighted lateness ({r['weighted_lateness_days']['mean']} vs {c['weighted_lateness_days']['mean']})")
        if v["predicted_tiers"] and v["solver_status"] == "optimal" and k.startswith("C") and why:
            why.append("its optimiser prediction did not survive stochastic replay (simulation validation)")
        if v["notes"]:
            why.append(v["notes"][0])
        out[k] = why or ["equivalent or worse on secondary criteria; simpler option preferred"]
    return out


def run(seed: int = 7, reps: int = 30, time_limit: float = 45.0, out: Path | None = None) -> dict:
    t0 = time.time()
    inst = build_demo(seed)
    log = {}
    plans: dict = {}
    plans["baseline_fifo"] = schedule_greedy(inst, "fifo")
    plans["priority_greedy"] = schedule_greedy(inst, "priority")
    S = {
        "diagnostics_first": Strategy("diagnostics_first", allow_patient_transfer=True, objective="diagnostics", time_limit_s=time_limit,
                                      label="Conventional: endoscopy units minimise their own appointment dates (transfers allowed), lab FIFO"),
        "A_local_pathway": Strategy("A_local_pathway", time_limit_s=time_limit, label="End-to-end pathway, home providers only"),
        "B_national_pathway": Strategy("B_national_pathway", allow_patient_transfer=True, allow_sample_routing=True, time_limit_s=time_limit,
                                       label="End-to-end pathway + eligible patient transfers + sample routing (existing resources)"),
        "C_resource_movement": Strategy("C_resource_movement", allow_patient_transfer=True, allow_sample_routing=True,
                                        allow_resource_moves=True, time_limit_s=time_limit * 2,
                                        label="B + relocation of an existing surgeon day (origin loses capacity)"),
    }
    plans["diagnostics_first"] = solve(inst, S["diagnostics_first"])
    plans["A_local_pathway"] = solve(inst, S["A_local_pathway"], hint=plans["priority_greedy"])
    plans["B_national_pathway"] = solve(inst, S["B_national_pathway"], hint=plans["A_local_pathway"])
    c = solve(inst, S["C_resource_movement"], hint=plans["B_national_pathway"])
    if c.status != "optimal" or not lex_better(c, plans["B_national_pathway"]):
        why = ("relocation search did not finish all tiers within the compute budget" if c.status != "optimal"
               else "relocation found no lexicographic improvement over B")
        c = Plan("C_resource_movement", dict(plans["B_national_pathway"].assignments), [], {}, dict(plans["B_national_pathway"].tiers),
                 "kept_B", c.solve_ms, [why + "; B's plan is kept (no staff moved)"])
    plans["C_resource_movement"] = c
    labels = {"baseline_fifo": "Baseline: first come first served, home providers",
              "priority_greedy": "Priority-aware greedy (urgent first, earliest deadline), home providers",
              **{k: v.label for k, v in S.items()}}
    results = {}
    for name, plan in plans.items():
        st = S.get(name)
        results[name] = {"label": labels[name], "solver_status": plan.status, "solve_ms": plan.solve_ms, "notes": plan.notes,
                         "predicted_tiers": plan.tiers, "constraint_violations": validate(inst, plan, st),
                         "kpis": evaluate(inst, plan, reps, seed), "moves": plan.moves}
    # ------------------------------------------------ choose the existing-resource recommendation (hierarchy)
    wl = lambda k: results[k]["kpis"]["weighted_lateness_days"]["mean"]
    base_wl = wl("baseline_fifo")
    gain_B, gain_C = base_wl - wl("B_national_pathway"), base_wl - wl("C_resource_movement")
    chosen = "B_national_pathway"
    if plans["C_resource_movement"].moves and gain_C >= (1 + MATERIAL_IMPROVEMENT) * max(gain_B, 1e-9) \
            and results["C_resource_movement"]["kpis"]["urgent_breaches"]["mean"] <= results["B_national_pathway"]["kpis"]["urgent_breaches"]["mean"]:
        chosen = "C_resource_movement"
    best = plans[chosen]
    # ------------------------------------------------ pass B: bottlenecks left after the best existing-resource plan
    bn = bottlenecks(inst, best)
    # ------------------------------------------------ pass C: budget-limited investment, ranked by marginal value per EUR
    invest = []
    weeks = inst.horizon_days / 7
    for o in sorted(inst.investments, key=lambda o: o.eur_per_week):
        budget = o.eur_per_week * weeks
        sti = Strategy(f"invest_{o.id}", allow_patient_transfer=True, allow_sample_routing=True,
                       allow_resource_moves=(chosen == "C_resource_movement"), investment_budget_eur=budget, time_limit_s=time_limit)
        pi = solve(inst, sti, hint=best)
        if not lex_better(pi, best):
            pi = best
        k = evaluate(inst, pi, reps, seed)
        spent = sum(next(x for x in inst.investments if x.id == i).eur_per_week * weeks * u for i, u in pi.investments.items())
        dwl = results[chosen]["kpis"]["weighted_lateness_days"]["mean"] - k["weighted_lateness_days"]["mean"]
        dbr = results[chosen]["kpis"]["breaches"]["mean"] - k["breaches"]["mean"]
        invest.append({"option_evaluated": o.id, "option": f"+{o.units_per_week}/wk {o.capability} at {inst.providers[o.provider].name}",
                       "budget_eur": round(budget), "chosen_investments": pi.investments, "spent_eur": round(spent),
                       "weighted_lateness_reduction": round(dwl, 1), "breaches_avoided": round(dbr, 2),
                       "breaches_avoided_per_10k_eur": round(10000 * dbr / spent, 3) if spent else 0.0,
                       "kpis": k, "constraint_violations": validate(inst, pi, sti)})
    invest.sort(key=lambda r: -r["breaches_avoided_per_10k_eur"])
    # ------------------------------------------------ patient story (Act 4): largest expected improvement among at-risk patients
    po_base = patient_outcomes(inst, plans["baseline_fifo"], reps, seed)
    po_best = patient_outcomes(inst, best, reps, seed)
    pmap = {p.pid: p for p in inst.patients}
    cands = []
    for pid, b in po_base.items():
        a = po_best[pid]
        if b["treatment_day_mean"] is not None and a["treatment_day_mean"] is not None and b["p_needs_treatment"] >= 0.5:
            cands.append((b["treatment_day_mean"] - a["treatment_day_mean"], pid))
    cands.sort(reverse=True)
    story = None
    if cands:
        pid = cands[0][1]
        p = pmap[pid]
        fmt = lambda plan: [{"stage": p.stages[j].key, "provider": (plan.assignments[pid, j].provider or "beyond horizon"),
                             "planned_day": plan.assignments[pid, j].start_day, "tentative": plan.assignments[pid, j].tentative}
                            for j in range(len(p.stages))]
        story = {"pid": pid, "priority": p.priority, "region": p.region, "deadline_day": p.deadline_day,
                 "diagnosis_known": p.diagnosis_known, "original_plan": fmt(plans["baseline_fifo"]), "recommended_plan": fmt(best),
                 "original_outcome": po_base[pid], "recommended_outcome": po_best[pid],
                 "actions": [a for a in plan_actions(inst, best, plans["baseline_fifo"]) if a.get("patient") == pid],
                 "tag": "SYNTHETIC patient, SIMULATED outcome"}
    actions = plan_actions(inst, best, plans["baseline_fifo"])
    rec = {
        "recommended_strategy": chosen, "label": labels[chosen],
        "why": (f"Best existing-resource plan by the lexicographic objective; resource movement is preferred only if it reduces "
                f"weighted lateness >= {int(100 * MATERIAL_IMPROVEMENT)} % more than patient/sample movement (hypothesis)."),
        "summary_actions": {t: sum(1 for a in actions if a["type"] == t) for t in sorted({a["type"] for a in actions})},
        "actions": actions[:200],
        "rejected_alternatives": _rejections(results, chosen),
        "alternatives_considered": {k: {"pct_within_target": results[k]["kpis"]["pct_within_target"],
                                        "weighted_lateness_days": results[k]["kpis"]["weighted_lateness_days"],
                                        "mean_treatment_day": results[k]["kpis"]["mean_treatment_day"]} for k in results},
        "constraint_checks": {k: (len(v["constraint_violations"]) == 0) for k, v in results.items()},
        "human_approval": "Every action is a recommendation: transfers and relocations need authorisation, patients may decline.",
    }
    out_doc = {
        "instance": {**inst.meta, "digest": inst.digest(), "patients": len(inst.patients),
                     "urgent": sum(p.priority == "urgent" for p in inst.patients), "horizon_days": inst.horizon_days,
                     "providers": {k: asdict(v) for k, v in inst.providers.items()}},
        "evaluation": {"replications": reps, "seed": seed, "method": "stochastic replay with common random numbers per patient",
                       "material_improvement_threshold": MATERIAL_IMPROVEMENT},
        "strategies": results, "recommendation": rec, "bottlenecks_after_best_plan": bn,
        "investment_pass": invest, "patient_story": story,
        "runtime_s": round(time.time() - t0, 1),
        "tags": {"outcomes": "SIMULATED", "patients": "SYNTHETIC", "capacities/costs/probabilities": "ASSUMPTION (illustrative)"},
    }
    out_doc = clean(out_doc)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(out_doc, indent=1, default=str, allow_nan=False), encoding="utf-8")
    return out_doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--time-limit", type=float, default=45.0)
    a = ap.parse_args()
    doc = run(a.seed, a.reps, a.time_limit, RESULTS / "east_finland.json")
    print(json.dumps({k: {"within": v["kpis"]["pct_within_target"], "wLate": v["kpis"]["weighted_lateness_days"],
                          "treat": v["kpis"]["mean_treatment_day"], "viol": len(v["constraint_violations"]), "status": v["solver_status"]}
                      for k, v in doc["strategies"].items()}, indent=1))
    print("recommended:", doc["recommendation"]["recommended_strategy"], doc["recommendation"]["summary_actions"])
    print("bottlenecks:", [(b["provider_id"], b["capability"], b["planned_utilisation"]) for b in doc["bottlenecks_after_best_plan"][:4]])
    print("investment:", [(i["chosen_investments"], i["spent_eur"], i["breaches_avoided"], i["breaches_avoided_per_10k_eur"]) for i in doc["investment_pass"]])
    print("story:", doc["patient_story"] and (doc["patient_story"]["pid"], doc["patient_story"]["original_outcome"], doc["patient_story"]["recommended_outcome"]))
    print("runtime", doc["runtime_s"], "s")
