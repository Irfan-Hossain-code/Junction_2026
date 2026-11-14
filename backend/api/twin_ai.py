"""Twin context, AI summary, natural-language intervention interpretation, one-shot simulate.

Grounding rules: every quantitative statement comes from backend.services.twin_context (stored SIMULATED runs,
OBSERVED indicators, SYNTHETIC operations). The LLM writes prose over those facts and is rejected if it
introduces numbers not present in them; it never predicts outcomes, it only maps text -> simulator controls.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.twin import base_snapshot, compare, ensure_baseline, get_conn_dep, latest_run, router, run_scenario
from backend.opt.allocate import optimise
from backend.services import llm
from backend.services import twin_context as tc
from backend.sim.snapshot import apply_scenario

SESSION_SIZE = 8   # ASSUMPTION: one endoscopy/theatre "session" ~ 8 patient slots (shown to the user)


def _ctx(conn, scope, id, run_id, base_run_id):
    snap = base_snapshot(conn)
    base = base_run_id or latest_run(conn, baseline=True)
    run_id = run_id or base
    try:
        return tc.build_context(conn, snap, scope, id, run_id, base if base != run_id else None)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.get("/context")
def context(scope: str = "national", id: Optional[str] = None, run_id: Optional[str] = None,
            base_run_id: Optional[str] = None, conn=Depends(get_conn_dep)):
    """All grounded facts for the panels: compliance, delays, bottlenecks, recommendations (+ base run when comparing)."""
    return _ctx(conn, scope, id, run_id, base_run_id)


# ------------------------------------------------------------------ AI situation summary
class SummaryIn(BaseModel):
    scope: str = "national"
    id: Optional[str] = None
    run_id: Optional[str] = None
    base_run_id: Optional[str] = None
    use_llm: bool = True


_summary_cache: dict[str, dict] = {}

SUMMARY_SYSTEM = """You are CancerFlow's analyst for Finnish cancer-care decision-makers.
Write a situation summary of at most 120 words in plain English (no markdown, no headings, no bullet points).
RULES:
- Use ONLY numbers that appear in the FACTS JSON. Never invent, estimate, round differently or extrapolate numbers.
- Say "simulated" for SIMULATED_* values and "observed" for OBSERVED_statistics; SYNTHETIC_* is demo data.
- The 42-day target is a reference target, not a legal requirement.
- If SIMULATED_before_intervention exists, explain what improved or worsened versus before, and why
  (capacity/queue effects versus process delays such as stalled steps).
- Name the single most important bottleneck and why it matters. Be specific. No generic advice."""


@router.post("/summary")
def summary(body: SummaryIn, conn=Depends(get_conn_dep)):
    ctx = _ctx(conn, body.scope, body.id, body.run_id, body.base_run_id)
    facts = tc.facts_for_llm(ctx)
    key = json.dumps([body.scope, body.id, ctx["run_id"], ctx["base_run_id"], body.use_llm])
    if key in _summary_cache:
        return _summary_cache[key]
    out: dict[str, Any] = {"mode": "fallback", "text": tc.fallback_summary(ctx), "facts": facts, "rejected": None}
    if body.use_llm and llm.available():
        txt = llm.chat([{"role": "system", "content": SUMMARY_SYSTEM},
                        {"role": "user", "content": "FACTS:\n" + json.dumps(facts, ensure_ascii=False)}], max_tokens=1500)
        if txt:
            bad = llm.ungrounded_numbers(txt, facts)
            if bad:
                out["rejected"] = {"reason": "LLM used numbers not present in the facts", "numbers": bad[:6]}
            else:
                out.update({"mode": "llm", "text": txt, "model": "nemotron-3.5-super-vl"})
    _summary_cache[key] = out
    return out


# ------------------------------------------------------------------ natural language -> simulator controls
class InterpretIn(BaseModel):
    text: str
    scope: str = "national"
    id: Optional[str] = None


INTERPRET_SYSTEM = """You translate a healthcare planner's intervention request into simulator controls.
Return ONLY a JSON object: {"actions": [...], "unsupported": [...], "explanation": "..."}.
Supported action objects (use ids exactly as in the CATALOGUE):
 {"type":"add_capacity","facility_id":ID,"capability":CAP,"add_per_week":NUMBER}  (CAP in colonoscopy, ct_imaging, mdt_crc, crc_surgery, oncology, radiotherapy; the hospital must list CAP)
 {"type":"reroute","county_id":ID,"capability":CAP,"max_travel_min":NUMBER}      (optimiser redistributes that county's referrals to hospitals with spare capacity)
 {"type":"route_share","from_county":ID,"capability":CAP,"to_facility":ID,"share":0..1}
 {"type":"monitoring"}                                                             (automatic stalled-step / follow-up alerts)
 {"type":"screening","county_id":ID or "*","factor":NUMBER}                        (multiplier on screening participation, e.g. 1.1)
Convert "sessions" to slots using SESSION_SIZE patients per session. If the user names a region but not a hospital, use
the hospital in that region that has the capability. Anything you cannot express (staff training, budgets, new equipment
types, pathway rule changes, etc.) goes into "unsupported" as short strings. The explanation is one sentence describing the
mapping. Never predict outcomes."""

CAP_WORDS = [("colonoscop", "colonoscopy"), ("endoscop", "colonoscopy"), ("surg", "crc_surgery"), ("operat", "crc_surgery"),
             ("chemo", "oncology"), ("oncolog", "oncology"), ("radio", "radiotherapy"), (" ct ", "ct_imaging"),
             ("imaging", "ct_imaging"), ("staging", "ct_imaging"), ("mdt", "mdt_crc"), ("multidisciplinary", "mdt_crc")]
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10}


def expand_actions(snap, actions: list[dict]) -> tuple[dict, list[dict], list[str]]:
    params: dict = {}
    ok, errors = [], []
    for a in actions or []:
        t = a.get("type")
        try:
            if t == "add_capacity":
                k = f"{a['facility_id']}|{a['capability']}"
                if k not in snap.capacity:
                    raise ValueError(f"{a['facility_id']} has no {a['capability']}")
                add = float(a["add_per_week"])
                if not 0 < add <= 60:
                    raise ValueError("add_per_week must be in (0, 60]")
                params.setdefault("capacity", []).append({"facility_id": a["facility_id"], "capability": a["capability"], "add_per_week": add})
                ok.append({**a, "label": f"+{add:g} {tc.CAP_LABEL.get(a['capability'])} slots/week at {snap.facilities[a['facility_id']]['name']}"})
            elif t == "reroute":
                cid, cap = a["county_id"], a["capability"]
                if cid not in snap.counties:
                    raise ValueError(f"unknown county {cid}")
                plan = optimise(snap, cap, budget_units=0, max_travel_min=float(a.get("max_travel_min", 180)))
                munis = {m for m, v in snap.munis.items() if v["county"] == cid}
                rs = {f"{c['municipality']}|{cap}": plan["scenario_params"]["routing_set"][f"{c['municipality']}|{cap}"]
                      for c in plan["routing_changes"] if c["municipality"] in munis}
                if not rs:
                    raise ValueError(f"optimiser found no beneficial re-routing for {cap} from {snap.counties[cid]['name']}")
                params.setdefault("routing_set", {}).update(rs)
                ok.append({**a, "label": f"Optimiser re-routes {len(rs)} municipalities' {tc.CAP_LABEL.get(cap)} referrals from {snap.counties[cid]['name']}"})
            elif t == "route_share":
                if f"{a['to_facility']}|{a['capability']}" not in snap.capacity:
                    raise ValueError(f"{a['to_facility']} has no {a['capability']}")
                share = float(a["share"])
                if not 0 < share <= 1:
                    raise ValueError("share must be in (0, 1]")
                params.setdefault("routing", []).append({"from_county": a["from_county"], "capability": a["capability"],
                                                         "to_facility": a["to_facility"], "share": share})
                ok.append({**a, "label": f"Send {share:.0%} of {tc.CAP_LABEL.get(a['capability'])} from "
                                         f"{snap.counties[a['from_county']]['name']} to {snap.facilities[a['to_facility']]['name']}"})
            elif t == "monitoring":
                params["monitoring"] = True
                ok.append({"type": "monitoring", "label": "CancerFlow stalled-step monitoring ON"})
            elif t == "screening":
                f = float(a["factor"])
                if not 0.5 <= f <= 1.5:
                    raise ValueError("factor must be within 0.5-1.5")
                params.setdefault("screening_factor", {})[a.get("county_id", "*")] = f
                ok.append({**a, "label": f"Screening participation x{f:g} in {a.get('county_id', 'all counties')}"})
            else:
                raise ValueError(f"unsupported action type {t}")
        except (KeyError, ValueError, TypeError) as e:
            errors.append(f"{t}: {e}")
    return params, ok, errors


def rule_parse(snap, text: str, scope: str, sid: Optional[str]) -> dict:
    t = " " + text.lower() + " "
    county = next((c for c, v in snap.counties.items()
                   if v["name"].lower().replace(" wellbeing services county", "").replace("city of ", "") in t), None)
    if county is None and scope == "county":
        county = sid
    if county is None and scope == "facility" and sid in snap.facilities:
        county = snap.facilities[sid]["county"]
    cap = next((c for w, c in CAP_WORDS if w in t), "colonoscopy")
    actions, unsupported = [], []
    if re.search(r"monitor|alert|follow[- ]?up|stall|track", t):
        actions.append({"type": "monitoring"})
    m = re.search(r"(\d+|" + "|".join(NUM_WORDS) + r")\s+(?:extra\s+|more\s+|additional\s+)?(?:\w+\s+)?(sessions?|slots?|lists?)", t)
    if m or re.search(r"\badd\b|increase|more capacity", t):
        if m:
            n = float(m.group(1)) if m.group(1).isdigit() else NUM_WORDS[m.group(1)]
            add = n * (SESSION_SIZE if m.group(2).startswith("session") else 1)
        else:
            add = 5.0
        fac = sid if scope == "facility" else None
        if fac is None and county:
            cands = [(snap.capacity[f"{f}|{cap}"], f) for f, v in snap.facilities.items()
                     if v["county"] == county and f"{f}|{cap}" in snap.capacity]
            fac = max(cands)[1] if cands else None
        if fac:
            actions.append({"type": "add_capacity", "facility_id": fac, "capability": cap, "add_per_week": add})
    if re.search(r"rerout|re-rout|redistribut|nearby|spare capacity|neighbo", t) and county:
        actions.append({"type": "reroute", "county_id": county, "capability": cap, "max_travel_min": 180})
    sm = re.search(r"screening[^.]*?(\d+)\s*%", t)
    if sm:
        actions.append({"type": "screening", "county_id": county or "*", "factor": 1 + float(sm.group(1)) / 100})
    if re.search(r"staff|train|hire|budget|euro|€|equipment|rule", t):
        unsupported.append("staffing, training or budget changes are not modelled directly (only as capacity)")
    return {"actions": actions, "unsupported": unsupported,
            "explanation": "Parsed with the deterministic rule-based interpreter."}


@router.post("/interpret")
def interpret(body: InterpretIn, conn=Depends(get_conn_dep)):
    """Natural language -> validated simulator params. The LLM never predicts outcomes; it only fills controls."""
    snap = base_snapshot(conn)
    catalogue = {"counties": {c: v["name"] for c, v in snap.counties.items()},
                 "hospitals": {f: {"name": v["name"], "county": v["county"],
                                   "capabilities": sorted(k.split("|")[1] for k in snap.capacity if k.startswith(f + "|"))}
                               for f, v in snap.facilities.items()},
                 "SESSION_SIZE": SESSION_SIZE, "current_context": {"scope": body.scope, "id": body.id}}
    mode, raw = "fallback", None
    if llm.available():
        txt = llm.chat([{"role": "system", "content": INTERPRET_SYSTEM},
                        {"role": "user", "content": "CATALOGUE:\n" + json.dumps(catalogue, ensure_ascii=False) + "\n\nREQUEST:\n" + body.text}],
                       max_tokens=2000, temperature=0.0, json_mode=True)
        raw = llm.extract_json(txt)
        if raw and isinstance(raw.get("actions"), list):
            mode = "llm"
    if mode == "fallback":
        raw = rule_parse(snap, body.text, body.scope, body.id)
    params, ok, errors = expand_actions(snap, raw.get("actions", []))
    if mode == "llm" and not ok:                     # LLM gave nothing usable -> deterministic parser
        raw2 = rule_parse(snap, body.text, body.scope, body.id)
        p2, ok2, e2 = expand_actions(snap, raw2.get("actions", []))
        if ok2:
            params, ok, errors, mode, raw = p2, ok2, errors + e2, "fallback", raw2
    return {"mode": mode, "params": params, "actions": ok, "errors": errors,
            "unsupported": raw.get("unsupported", []), "explanation": raw.get("explanation", ""),
            "assumptions": {"SESSION_SIZE": SESSION_SIZE}, "simulatable": bool(params)}


# ------------------------------------------------------------------ one-shot simulate
class SimulateIn(BaseModel):
    name: str = "Custom intervention"
    description: str = ""
    params: dict[str, Any]
    reps: int = Field(4, ge=1, le=8)


@router.post("/simulate")
def simulate(body: SimulateIn, conn=Depends(get_conn_dep)):
    """Create a scenario from params, run it (SimPy replications), compare with the latest baseline run."""
    snap = base_snapshot(conn)
    try:
        apply_scenario(snap, body.params)
    except Exception as e:
        raise HTTPException(400, f"invalid params: {e}")
    sid = conn.execute("INSERT INTO scenario(name,description,pack_id,params) VALUES (%s,%s,'colorectal',%s::jsonb) RETURNING id",
                       (body.name, body.description, json.dumps(body.params))).fetchone()[0]
    conn.commit()
    base_run = latest_run(conn, baseline=True)
    if base_run is None:
        base_run = run_scenario(conn, ensure_baseline(conn), base=snap, reps=body.reps)["run_id"]
        conn.commit()
    res = run_scenario(conn, sid, base=snap, reps=body.reps)
    conn.commit()
    cmp_ = compare(uuid.UUID(base_run), uuid.UUID(res["run_id"]), conn)
    head = {r["metric"]: r for r in cmp_["rows"] if r["scope_kind"] == "national"}
    return {"scenario_id": str(sid), "run_id": res["run_id"], "base_run_id": base_run, "seconds": res["seconds"],
            "headline": head, "comparison": cmp_, "tag": "SIMULATED"}
