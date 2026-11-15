"""/twin/chat + /twin/evaluate: the Analysis panel's conversation (Nemotron first), as decision support.

The assistant RECOMMENDS (it may say "yes, buy" / "no, reroute first"); approval is only needed to EXECUTE changes.
It sees: the active twin and what it changed, the comparison twin, the selected county / hospital / alarm, the
bottlenecks with their county relationship, the rule-based options, and any optimiser estimate the user requested.
Numbers in the answer must appear in those facts (grounding check), else the next model / a deterministic answer.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends
from pydantic import BaseModel

from backend.api.twin import base_snapshot, get_conn_dep, router
from backend.opt.allocate import optimise
from backend.services import llm
from backend.services import twin_context as tc
from backend.services.anomalies import T as ALARM_T
from backend.sim.snapshot import apply_scenario

TWIN_LABEL = {"baseline": "Baseline (normal Finland)", "disruption": "Disruption (SYNTHETIC Southern Finland demand surge)",
              "recovery": "MPC Recovery (same surge + CancerFlow response)"}

CHAT_SYSTEM = """You are CancerFlow's decision-support analyst for a Finnish healthcare administrator.
You RECOMMEND. Approval is only needed to EXECUTE operational changes, never to discuss or recommend them, so do not
say that a human must approve unless the user asks you to carry something out.

How to answer:
- First line: a direct answer or recommendation in at most 2 sentences (e.g. "Not yet: reroute and add sessions first").
- Then up to 4 short lines starting with "- ": the evidence and the mechanism (cause -> effect), what is uncertain.
- For "should we buy / invest" questions follow this order: better scheduling (stalled steps, no-shows), rerouting to
  hospitals with spare capacity, moving existing resources, and only then new capacity. Say which step the evidence supports.
- For "why did it change" questions explain the MECHANISM: what the scenario changed -> which delay or queue it acted on ->
  the outcome; and why something did NOT change if relevant. Do not just restate the before/after numbers.
- A hospital in another county is not a "local" bottleneck: say it serves this area's patients (referral flow).
- Utilisation alone is not proof of a bottleneck: combine it with queue days, throughput and alarms.
- Operational patient alerts (care-app clock alerts on individual synthetic patients) are NOT network alarms
  (thresholds on simulated hospital metrics). Do not mix them up and do not speculate about broken data feeds.
- If an optimiser estimate is not in the facts and would settle the question, end with: "I can evaluate this with the MPC."
- Use ONLY numbers that appear in FACTS. Never invent outcomes. Say "simulated" for simulated values.
- Plain text, no markdown headers, at most 110 words."""


class ChatMsg(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    messages: list[ChatMsg]
    scope: str = "national"
    id: Optional[str] = None
    run_id: Optional[str] = None
    base_run_id: Optional[str] = None
    alarm_id: Optional[str] = None
    evaluation: Optional[dict[str, Any]] = None


def _scenario_of(conn, run_id):
    if not run_id:
        return None
    r = conn.execute("""SELECT s.name, s.params, s.description, s.is_baseline FROM sim_run r JOIN scenario s ON s.id=r.scenario_id
                        WHERE r.id=%s""", (run_id,)).fetchone()
    if not r:
        return None
    p = r[1] or {}
    key = p.get("twin") or ("baseline" if r[3] else "custom")
    return {"key": key, "name": r[0], "label": TWIN_LABEL.get(key, r[0]), "description": r[2], "params": p}


def chat_facts(conn, body: ChatIn) -> tuple[dict, dict]:
    from backend.api.twin_ai import _ctx
    ctx = _ctx(conn, body.scope, body.id, body.run_id, body.base_run_id)
    f = tc.facts_for_llm(ctx)
    snap = base_snapshot(conn)
    act, cmpw = _scenario_of(conn, ctx["run_id"]), _scenario_of(conn, ctx.get("base_run_id"))
    if act:
        s2 = apply_scenario(snap, {k: v for k, v in act["params"].items() if k not in ("plan_summary", "twin")})
        f["active_twin"] = {"twin": act["label"], "scenario": act["name"], "what_it_changes": s2.notes[:6] or ["nothing (reference)"],
                            "description": act["description"]}
        if act["params"].get("plan_summary"):
            ps = act["params"]["plan_summary"]
            f["CancerFlow_response_selected_by_optimiser"] = {
                "hierarchy": [h["action"] for h in ps.get("hierarchy", [])],
                "rerouting": {c: {"moved_per_week": r["moved_per_week"], "expected_acceptance": r["expected_acceptance"],
                                  "overload_before_per_week": r["overload_before"],
                                  "overload_after_existing_resources_per_week": r["overload_after_existing_resources"],
                                  "largest_moves": [f"{m['from']} -> {', '.join(t for t, _ in m['to'])} ({m['per_week']}/wk)" for m in r["top_moves"][:4]]}
                              for c, r in ps.get("routing", {}).items()},
                "investment_recommendation_not_simulated": ps.get("investment_recommendation") or "none needed by the optimiser"}
    if cmpw:
        f["compared_with_twin"] = cmpw["label"]
    county = ctx["context"].get("county")
    rel = []
    for s in ctx["bottlenecks"][:5]:
        fc = snap.facilities.get(s["facility_id"], {}).get("county")
        rel.append({"hospital": s["facility"], "resource": s["capability_label"],
                    "hospital_county": snap.counties.get(fc, {}).get("name", fc),
                    "in_selected_area": (fc == county) if county else True,
                    "relationship": "local" if (not county or fc == county) else "outside the county; receives this county's referrals",
                    "utilisation_pct": None if s["utilisation"] is None else round(100 * s["utilisation"]),
                    "mean_queue_days": None if s["mean_wait_days"] is None else round(s["mean_wait_days"], 1),
                    "throughput_per_week": None if s["throughput_per_week"] is None else round(s["throughput_per_week"], 1),
                    "capacity_per_week": None if s["capacity_per_week"] is None else round(s["capacity_per_week"], 1)})
    f["SIMULATED_bottlenecks"] = rel
    f["alert_types"] = {"operational_patient_alerts_open": ctx["live"]["open_alerts"],
                        "operational_definition": "care-app clock/dwell alerts on individual SYNTHETIC patients living in the area",
                        "network_alarms": len(ctx.get("anomalies", [])),
                        "network_definition": f"thresholds on SIMULATED hospital metrics, e.g. sustained overload at >= {round(100 * ALARM_T['overload_util'])} % utilisation"}
    f["rule_based_options"] = [{"option": r["title"], "action": r["action"], "why": r["rationale"]} for r in ctx.get("recommendations", [])[:4]]
    if body.alarm_id:
        al = next((a for a in ctx.get("anomalies", []) if a["id"] == body.alarm_id), None)
        if al:
            f["selected_network_alarm"] = {"title": al["title"], "type": al["type"], "severity": al["severity"],
                                           "metric": al["metric"], "value": al["value"], "threshold": al["threshold"],
                                           "evidence": al["evidence"], "rule_suggestion": al["suggestion"]}
    if body.evaluation:
        f["OPTIMISER_estimate_requested_by_user"] = body.evaluation
    f["evaluate_with_mpc"] = "available: OR-Tools behaviour-aware optimiser on this scenario (estimate, not simulated)"
    return f, ctx


def _clean(text: str) -> str:
    return text.replace("**", "").replace("###", "").strip()


@router.post("/chat")
def chat(body: ChatIn, conn=Depends(get_conn_dep)):
    facts, ctx = chat_facts(conn, body)
    hist = [{"role": m.role if m.role in ("user", "assistant") else "user", "content": m.content[:1500]} for m in body.messages[-8:]]
    msgs = [{"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": "FACTS (the only numbers you may use):\n" + json.dumps(facts, ensure_ascii=False, default=str)},
            {"role": "assistant", "content": "Understood. I will answer from these facts only."}] + hist
    tried = []
    if llm.available():
        t = llm.chat(msgs, max_tokens=900, temperature=0.2)
        if t:
            bad = llm.ungrounded_numbers(t, facts)
            if not bad:
                return {"text": _clean(t), "mode": "nemotron", "facts": facts}
            tried.append({"model": "nemotron", "ungrounded": bad[:5]})
    from backend.ops import agents                  # cheap second opinion if Nemotron is unavailable or ungrounded
    sysmsg = CHAT_SYSTEM + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False, default=str)[:12000]
    c = agents.claude()
    if c:
        try:
            r = c.messages.create(model=agents.MODEL, max_tokens=700, system=sysmsg, output_config={"effort": "low"},
                                  messages=[m for m in hist if m["content"]])
            t = "".join(b.text for b in r.content if b.type == "text")
            if t and not llm.ungrounded_numbers(t, facts):
                return {"text": _clean(t), "mode": "claude", "facts": facts, "retried": tried}
            tried.append({"model": "claude", "ungrounded": llm.ungrounded_numbers(t, facts)[:5] if t else []})
        except Exception:
            pass
    opts = facts.get("rule_based_options") or []
    b = (facts.get("SIMULATED_bottlenecks") or [{}])[0]
    text = (f"Start with the existing system before buying: {opts[0]['action'] if opts else 'review scheduling and routing'}.\n"
            + (f"- Tightest resource: {b.get('resource')} at {b.get('hospital')} ({b.get('relationship')}), "
               f"{b.get('utilisation_pct')} % utilised, {b.get('mean_queue_days')} d mean queue (simulated).\n" if b else "")
            + "".join(f"- {o['option']}: {o['why']}\n" for o in opts[1:3])
            + "I can evaluate this with the MPC.")
    return {"text": text, "mode": "deterministic", "facts": facts, "retried": tried}


class EvalIn(BaseModel):
    scope: str = "national"
    id: Optional[str] = None
    run_id: Optional[str] = None
    capability: str = "colonoscopy"
    budget_units: int = 2


@router.post("/evaluate")
def evaluate(body: EvalIn, conn=Depends(get_conn_dep)):
    """'Evaluate with MPC': optimiser estimate on the ACTIVE scenario, existing resources first, then a small budget.
    Expected-value MIP (seconds), NOT a simulation; run the Intervention Studio to verify in SimPy."""
    snap = base_snapshot(conn)
    sc = _scenario_of(conn, body.run_id)
    if sc:
        snap = apply_scenario(snap, {k: v for k, v in sc["params"].items() if k not in ("plan_summary", "twin")})
    info = tc.scope_info(snap, body.scope, body.id)
    restrict = [info["county"]] if info.get("county") else None
    a = optimise(snap, body.capability, budget_units=0, behavioral=True, restrict_counties=restrict, time_limit_s=15)
    b = optimise(snap, body.capability, budget_units=body.budget_units, unit_per_week=5.0, behavioral=True,
                 restrict_counties=restrict, time_limit_s=15)
    name = lambda f: snap.facilities.get(f, {}).get("name", f)
    return {"tag": "OPTIMISER ESTIMATE (expected value, not simulated)", "scope": info["name"], "capability": body.capability,
            "existing_resources": {"overload_before_per_week": a["overload_before"], "overload_after_per_week": a["overload_after"],
                                   "referrals_rerouted_per_week": a["moved_per_week"], "expected_acceptance": a["expected_acceptance_of_moves"],
                                   "main_destinations": sorted({name(f) for c in a["routing_changes"][:6] for f, _ in c["to"]})[:4]},
            "with_investment": {"budget_units": body.budget_units, "unit_per_week": 5.0, "overload_after_per_week": b["overload_after"],
                                "units_added": [f"{x['units']} x 5/wk at {name(x['facility_id'])}" for x in b["capacity_additions"]]},
            "investment_worth_it": bool(b["capacity_additions"]) and (a["overload_after"] - b["overload_after"]) > 0.5,
            "scenario": sc["label"] if sc else None}
