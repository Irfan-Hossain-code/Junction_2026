"""Lightweight conversational patient agents.

DECISIONS are deterministic-probabilistic: the persona's acceptance probability for the offered hospital
(national agent utility + persistent constraints, see world.accept_prob) against a seeded draw per (offer, patient),
plus hard persona constraints (e.g. "cannot travel before Friday" -> asks for another time). The LLM never decides.

MESSAGES: a cheap model (Claude Haiku, a few hundred tokens) only phrases the already-made decision in the
persona's voice, using ONLY the persona's stated circumstances. Cached in the agent's memory; template fallback.
"""
from __future__ import annotations

import hashlib
import json
import os

from backend.ops import core
from backend.services import pathways as svc

_client = None
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
SPEND_CAP_CALLS = int(os.environ.get("CLAUDE_MAX_CALLS", "400"))   # hard cap per process (credit guard)
_calls = 0


def claude():
    global _client
    if _client is None and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        _client = anthropic.Anthropic(timeout=20.0, max_retries=1)
    return _client


def claude_text(system: str, user: str, max_tokens: int = 300) -> str | None:
    global _calls
    c = claude()
    if not c or _calls >= SPEND_CAP_CALLS:
        return None
    _calls += 1
    try:
        r = c.messages.create(model=MODEL, max_tokens=max_tokens, system=system, output_config={"effort": "low"},
                              messages=[{"role": "user", "content": user}])
        if r.stop_reason == "refusal":
            return None
        return "".join(b.text for b in r.content if b.type == "text").strip() or None
    except Exception:
        return None


def _u(*parts) -> float:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16) / float(16 ** 12)


def decide(persona: dict, offer: dict, key: str) -> dict:
    """offer: {facility_id, facility, story_day, travel_min, transfer}. Returns decision + the persona reason used."""
    fid = offer["facility_id"]
    for c in persona.get("constraints", []):
        if c.get("earliest_story_day") is not None and offer.get("story_day") is not None and offer["story_day"] < c["earliest_story_day"]:
            return {"decision": "counter", "reason": c["text"], "reason_kind": c["kind"],
                    "request": {"earliest_story_day": c["earliest_story_day"]}, "p_accept": None, "draw": None}
    p = 1.0 if not offer.get("transfer") else float(persona.get("accept_prob", {}).get(fid, 0.3))
    u = _u(key, persona.get("patient_id"))
    if u < p:
        return {"decision": "accept", "reason": None, "reason_kind": None, "p_accept": p, "draw": round(u, 3)}
    reason = next((c for c in persona.get("constraints", []) if fid in c.get("avoid", [])), None) or \
        next((c for c in persona.get("constraints", []) if c.get("travel_limit_min") and (offer.get("travel_min") or 0) > c["travel_limit_min"]), None) or \
        next(iter(persona.get("constraints", [])), None)
    text = reason["text"] if reason else f"finds {round(offer.get('travel_min') or 0)} minutes of travel too much"
    return {"decision": "decline", "reason": text, "reason_kind": reason["kind"] if reason else "travel",
            "p_accept": p, "draw": round(u, 3)}


SYSTEM = """You voice a SYNTHETIC patient replying by text message to a hospital scheduling offer in Finland.
Write 1-2 short sentences in ENGLISH, first person, in the persona's tone. The DECISION is already made - express exactly it.
Only mention circumstances listed in the persona. No medical claims, no numbers that are not in the input, no emojis."""


def message(persona: dict, offer: dict, d: dict) -> tuple[str, bool]:
    first = persona.get("name", "").split(" ")[0]
    if d["decision"] == "accept":
        tpl = f"Yes, {offer['facility']} works for me. I'll be there."
    elif d["decision"] == "counter":
        tpl = "I can't make that time this week. Could you offer me something later in the week?"
    else:
        tpl = f"I'm sorry, I can't manage {offer['facility']}. Is there anything closer to home?"
    user = json.dumps({"persona": {k: persona.get(k) for k in ("name", "birth_year", "occupation", "household", "transport", "tone")},
                       "circumstances": [c["text"] for c in persona.get("constraints", [])],
                       "offer": {"hospital": offer["facility"], "when": offer.get("when"), "travel_minutes": offer.get("travel_min")},
                       "decision": d["decision"], "reason_to_use": d["reason"]}, ensure_ascii=False)
    t = claude_text(SYSTEM, user, max_tokens=200)
    if t and len(t) < 400:
        return t, True
    return tpl, False


def respond(conn, patient_id: str, offer: dict, key: str) -> dict:
    """Decide + phrase + remember. Idempotent per key (re-reads memory instead of re-billing the LLM)."""
    row = conn.execute("SELECT persona, memory FROM ops_patient_agent WHERE patient_id=%s", (patient_id,)).fetchone()
    if not row:
        return {"decision": "accept", "message": "", "llm": False}
    persona, memory = row
    for m in memory:
        if m.get("key") == key:
            return m["response"]
    d = decide(persona, offer, key)
    text, used_llm = message(persona, offer, d)
    resp = {**d, "message": text, "llm": used_llm, "patient_id": patient_id, "name": persona.get("name"), "offer": offer}
    conn.execute("UPDATE ops_patient_agent SET memory = memory || %s::jsonb WHERE patient_id=%s",
                 (json.dumps([{"key": key, "at": svc.now(conn).isoformat(), "text": f"offer {offer['facility']}: {d['decision']}",
                               "response": resp}], default=str, ensure_ascii=False), patient_id))
    return resp


def memory(conn, patient_id: str) -> list:
    r = conn.execute("SELECT memory FROM ops_patient_agent WHERE patient_id=%s", (patient_id,)).fetchone()
    return r[0] if r else []
