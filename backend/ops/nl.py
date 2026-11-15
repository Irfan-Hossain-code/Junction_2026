"""Natural-language commands and grounded explanations for the operations workflow.

parse(): text -> a typed command (Claude Haiku with a JSON schema; regex fallback). The LLM only maps words to
parameters; the backend validates and evaluates everything. explain(): deterministic bullet points computed from
the evaluation (always), plus an optional short conversational answer that must pass the numeric grounding check.
"""
from __future__ import annotations

import json
import re

from backend.ops import agents
from backend.ops.world import FAC_SHORT
from backend.services import llm

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["propose", "approve", "reject", "explain", "status", "unknown"]},
        "allocations": {"type": "array", "items": {
            "type": "object",
            "properties": {"facility": {"type": "string"}, "share": {"type": ["number", "null"]}, "count": {"type": ["integer", "null"]}},
            "required": ["facility", "share", "count"], "additionalProperties": False}},
        "per_patient": {"type": "array", "items": {
            "type": "object", "properties": {"patient": {"type": "string"}, "facility": {"type": "string"}},
            "required": ["patient", "facility"], "additionalProperties": False}},
        "justification": {"type": "string"},
    },
    "required": ["action", "allocations", "per_patient", "justification"], "additionalProperties": False,
}

PARSE_SYSTEM = f"""Map a healthcare coordinator's instruction about rescheduling colonoscopy patients after an endoscopy
unit breakdown in Joensuu to a command. Hospitals: {', '.join(FAC_SHORT.values())}. Counties: North Savo = Kuopio,
South Savo = Mikkeli and Savonlinna, Central Finland = Jyväskylä. 'half' = share 0.5, 'a third' = 0.333.
'approve'/'go ahead'/'accept' = approve. Named patients go in per_patient (first names are fine); 'everyone else' /
'the rest' goes in allocations with share 1. Never invent hospitals. If unclear use action 'unknown'."""


def parse(text: str) -> dict:
    quick = _regex(text)                       # simple intents (approve / reject / explain) never need an LLM call
    if quick["action"] in ("approve", "reject", "explain"):
        return quick
    c = agents.claude()
    if c:
        try:
            r = c.messages.create(model=agents.MODEL, max_tokens=600, system=PARSE_SYSTEM, output_config={
                "effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": text}])
            t = next((b.text for b in r.content if b.type == "text"), None)
            if t:
                d = json.loads(t)
                d["parser"] = "claude"
                return d
        except Exception:
            pass
    return _regex(text)


def _regex(text: str) -> dict:
    t = text.lower()
    out = {"action": "unknown", "allocations": [], "per_patient": [], "justification": text, "parser": "rules"}
    if re.search(r"\b(approve|approved|go ahead|accept|do it|confirm|go with)\b", t) and not re.search(r"\b(move|send|half|split)\b", t):
        out["action"] = "approve"
        return out
    if re.search(r"\b(reject|cancel|no,? don't)\b", t):
        out["action"] = "reject"
        return out
    if re.search(r"\b(why|explain)\b", t):
        out["action"] = "explain"
        return out
    names = {"kuopio": "Kuopio", "north savo": "Kuopio", "mikkeli": "Mikkeli", "south savo": "Mikkeli",
             "savonlinna": "Savonlinna", "jyväskylä": "Jyväskylä", "jyvaskyla": "Jyväskylä", "central finland": "Jyväskylä",
             "joensuu": "Joensuu"}
    found = []
    for k, v in names.items():
        i = t.find(k)
        if i >= 0 and v not in [f for f, _ in found]:
            found.append((v, i))
    found.sort(key=lambda x: x[1])
    if found:
        share = 0.5 if "half" in t else (1.0 / len(found))
        out["action"] = "propose"
        out["allocations"] = [{"facility": f, "share": share, "count": None} for f, _ in found]
    return out


def explain(iv: dict) -> dict:
    """Bullets on why the proposal's outcomes differ from the alternatives. Every number comes from the evaluation."""
    ev = iv.get("evaluation") or {}
    opts = ev.get("options") or {}
    mine = "admin" if "admin" in opts else ("replan" if "replan" in opts else "optimised")
    m = (opts.get(mine) or {}).get("kpis", {})
    o = (opts.get("optimised") or {}).get("kpis", {})
    n = (opts.get("no_action") or {}).get("kpis", {})
    b = (opts.get("baseline") or {}).get("kpis", {})
    bullets, why = [], []

    def line(lbl, k, unit="", better="lower"):
        if m.get(k) is None:
            return
        parts = [f"{lbl}: {m[k]}{unit}"]
        for nm, ref in (("recommended", o if mine != "optimised" else None), ("no action", n), ("before breakdown", b)):
            if ref and ref.get(k) is not None:
                parts.append(f"{nm} {ref[k]}{unit}")
        bullets.append(" · ".join(parts))
    line("Treated within 42 days", "pct_within_target", " %")
    line("Urgent patients within target", "pct_urgent_within_target", " %")
    line("Mean colonoscopy day", "mean_colonoscopy_day")
    line("Mean treatment day", "mean_treatment_day")
    line("Patient travel", "patient_travel_hours", " h")
    rv = ev.get("review") or {}
    for w in rv.get("warnings", []):
        why.append(w["text"])
    for x in rv.get("infeasible", []) + (ev.get("infeasible") or []):
        why.append("Infeasible: " + x)
    rows = (iv.get("proposal") or {}).get("rows") or []
    late = [r for r in rows if (r.get("outcome") or {}).get("p_on_time") is not None and r["outcome"]["p_on_time"] < 0.5]
    for r in late[:4]:
        why.append(f"{r['name']} ({r['priority']}): {r['to']['facility']} on day {r['to']['planned_day']} leaves "
                   f"{round(100 * r['outcome']['p_on_time'])} % chance of treatment within target.")
    if mine != "optimised" and o and m:
        if (m.get("mean_colonoscopy_day") or 0) > (o.get("mean_colonoscopy_day") or 0) + 0.3:
            why.append("Patients wait longer for a colonoscopy: the receiving hospitals do not have enough free slots in the next days.")
        if (m.get("patient_travel_hours") or 0) < (o.get("patient_travel_hours") or 0) - 0.3:
            why.append("Your plan asks patients to travel less, which they prefer.")
        if (m.get("pct_within_target") or 0) >= (o.get("pct_within_target") or 0) and not rv.get("warnings"):
            why.append("Your plan matches or beats the recommendation on the target: CancerFlow would accept it.")
        elif (m.get("pct_within_target") or 0) >= (o.get("pct_within_target") or 0) - 1.0:
            why.append("Comparable on the target, but with the capacity and acceptance risks above.")
    by = (iv.get("proposal") or {}).get("by_facility") or {}
    if by:
        bullets.append("Allocation: " + ", ".join(f"{v} → {k}" for k, v in by.items()))
    verdict = rv.get("verdict")
    return {"intervention_id": iv.get("id"), "compared": mine, "verdict": verdict, "bullets": bullets, "why": why,
            "status": iv.get("status")}


ANSWER_SYSTEM = """You are CancerFlow's operations analyst talking to a Finnish regional healthcare coordinator.
Answer in at most 70 words, plain text, conversational. Use ONLY numbers that appear in FACTS. Outcomes are
SIMULATED on synthetic patients. Never make clinical decisions; recommendations need human approval."""


def answer(question: str, facts: dict) -> dict:
    """Short conversational answer, grounded. Claude first (cheap), then Nemotron, else bullets only."""
    blob = json.dumps(facts, default=str, ensure_ascii=False)[:6000]
    t = agents.claude_text(ANSWER_SYSTEM, f"FACTS:\n{blob}\n\nQUESTION: {question}", max_tokens=400)
    src = "claude"
    if not t and llm.available():
        t = llm.chat([{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": f"FACTS:\n{blob}\n\nQUESTION: {question}"}],
                     max_tokens=600)
        src = "nemotron"
    if t and not llm.ungrounded_numbers(t, facts):
        return {"text": t, "mode": src}
    return {"text": None, "mode": "fallback", "rejected": llm.ungrounded_numbers(t, facts)[:5] if t else []}
