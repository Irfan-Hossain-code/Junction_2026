"""The three living digital twins of the national map (precomputed, comparable).

  TWIN 1  baseline    the existing baseline scenario (no interventions)
  TWIN 2  disruption  SYNTHETIC referral-demand surge in Southern Finland (Helsinki + Uusimaa x1.6, Pirkanmaa x1.4)
  TWIN 3  recovery    the SAME surge + CancerFlow's response chosen by the OR-Tools optimiser (national flow layer),
                      following the intervention hierarchy:
                        1. existing scheduling: stall monitoring + reminders/follow-up in the surge counties
                        2. behaviour-aware re-routing of colonoscopy and CT referrals over EXISTING capacity
                        3. (resource redistribution between hospitals: not modelled in the national layer; reported)
                        4. remaining bottlenecks: reported from the simulated outcome
                        5. investment: a separate optimiser pass with a budget is RECOMMENDED only if overload remains
                           (it is NOT added to the simulated twin)

Fairness: same model version, same base snapshot, same seed / replications / horizon / warm-up for all three.
Twin 2 vs 3 share the same arrival stream (same surge), so their differences come from the response only.

    python -m backend.sim.twins            # sequential, single process (memory-safe), ~10 min
    python -m backend.sim.twins --only recovery
"""
from __future__ import annotations

import argparse
import json
import time

from backend.db.conn import connect
from backend.opt.allocate import optimise
from backend.sim.runner import MODEL_VERSION, ensure_baseline, run_scenario
from backend.sim.snapshot import apply_scenario, build_snapshot

SURGE = {"hva-960": 1.6, "hva-977": 1.6, "hva-961": 1.6, "hva-966": 1.6, "hva-969": 1.6, "hva-970": 1.4}
SETTINGS = {"reps": 4, "seed": 1000, "horizon": 365, "warmup": 90}
NAMES = {"disruption": "Twin 2 · Disruption: Southern Finland demand surge (SYNTHETIC)",
         "recovery": "Twin 3 · MPC Recovery: CancerFlow response to the Southern surge"}


def _scenario(conn, key: str, params: dict, description: str) -> str:
    row = conn.execute("SELECT id FROM scenario WHERE params->>'twin'=%s ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
    if row:
        conn.execute("UPDATE scenario SET name=%s, description=%s, params=%s::jsonb WHERE id=%s",
                     (NAMES[key], description, json.dumps(params), row[0]))
        return str(row[0])
    return str(conn.execute("""INSERT INTO scenario(name,description,pack_id,params) VALUES (%s,%s,'colorectal',%s::jsonb) RETURNING id""",
                            (NAMES[key], description, json.dumps(params))).fetchone()[0])


def recovery_params(base) -> tuple[dict, dict]:
    """Optimiser on the DISRUPTED snapshot. Returns (scenario params, plan summary for the UI / assistant)."""
    disrupted = apply_scenario(base, {"demand_surge": SURGE})
    params = {"twin": "recovery", "demand_surge": SURGE, "monitoring": True, "reminders": list(SURGE), "routing_set": {}}
    summary = {"hierarchy": [], "routing": {}, "investment_recommendation": {}}
    summary["hierarchy"].append({"step": 1, "lever": "scheduling", "action": "stall monitoring nationwide + reminders/follow-up calls in the surge counties"})
    for cap in ("colonoscopy", "ct_imaging"):
        plan = optimise(disrupted, cap, budget_units=0, behavioral=True, time_limit_s=30)
        params["routing_set"].update(plan["scenario_params"]["routing_set"])
        top = [{"from": c["name"], "county": c["county"], "home_facility": disrupted.facilities.get(c["from"], {}).get("name", c["from"]),
                "to": [[disrupted.facilities.get(f, {}).get("name", f), sh] for f, sh in c["to"]], "share_moved": c.get("share_moved"),
                "per_week": round(c.get("demand_per_week", 0) * c.get("share_moved", 0), 2),
                "acceptance": c.get("expected_acceptance")} for c in plan["routing_changes"][:8]]
        summary["routing"][cap] = {"moved_per_week": plan["moved_per_week"], "expected_acceptance": plan["expected_acceptance_of_moves"],
                                   "overload_before": plan["overload_before"], "overload_after_existing_resources": plan["overload_after"],
                                   "status": plan["status"], "top_moves": top}
        if plan["overload_after"] > 1.0:                    # step 5: only if existing resources cannot absorb the surge
            inv = optimise(disrupted, cap, budget_units=4, unit_per_week=5.0, behavioral=True, time_limit_s=30)
            summary["investment_recommendation"][cap] = {
                "residual_overload_per_week": plan["overload_after"], "with_up_to_4_units_overload": inv["overload_after"],
                "units": inv["capacity_additions"], "note": "optimiser estimate (not simulated); not part of Twin 3"}
    summary["hierarchy"].append({"step": 2, "lever": "re-routing over existing capacity",
                                 "action": f"{sum(r['moved_per_week'] for r in summary['routing'].values()):.1f} referrals/week offered elsewhere "
                                           "(behaviour-aware: expected acceptance included)"})
    summary["hierarchy"].append({"step": 3, "lever": "resource redistribution", "action": "not modelled in the national flow layer"})
    summary["hierarchy"].append({"step": 5, "lever": "investment", "action": "recommended only where overload remains (see investment_recommendation)"})
    return params, summary


def main(only: str | None = None):
    t0 = time.time()
    with connect() as conn:
        base = build_snapshot(conn)
        bid = ensure_baseline(conn)
        conn.execute("UPDATE scenario SET name='Twin 1 · Baseline: normal Finland', params = params || '{\"twin\": \"baseline\"}'::jsonb WHERE id=%s", (bid,))
        did = _scenario(conn, "disruption", {"twin": "disruption", "demand_surge": SURGE},
                        "SYNTHETIC referral surge: Helsinki, Vantaa-Kerava and Uusimaa x1.6, Pirkanmaa x1.4 (all routes). Same capacity.")
        rparams, summary = recovery_params(base)
        rparams["plan_summary"] = summary
        rid = _scenario(conn, "recovery", rparams, "Same surge + CancerFlow response (monitoring, reminders, behaviour-aware re-routing). "
                                                   "Investment only recommended, not simulated.")
        conn.commit()
        print("optimiser done", round(time.time() - t0), "s", json.dumps({k: v["moved_per_week"] for k, v in summary["routing"].items()}))
        for key, sid in (("baseline", bid), ("disruption", did), ("recovery", rid)):
            if only and key != only:
                continue
            r = run_scenario(conn, sid, base=base, parallel=False, **SETTINGS)
            conn.commit()
            print(key, MODEL_VERSION, r, round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["baseline", "disruption", "recovery"])
    main(ap.parse_args().only)
