"""/ops: the shared operations layer for the final demo (disruption -> evaluation -> approval -> execution).

The national twin (map), the care app (patient + clinician) and the ElevenLabs voice agent all call these routes,
so they act on ONE state. All patients, slots and disruptions are SYNTHETIC. Recommendations only: operational
changes happen exclusively in POST /ops/interventions/{id}/approve (human approval).
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel

from backend.api.twin import get_conn_dep
from backend.ops import agents, core, followup, interventions, nl, story
from backend.ops.core import COORD_USER
from backend.ops.world import FAC_COORD, FAC_COUNTY, FAC_SHORT
from backend.services import pathways as svc

router = APIRouter(prefix="/ops", tags=["operations (demo)"])


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except svc.NotFound as e:
        raise HTTPException(404, str(e))
    except svc.Conflict as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------------------------ demo / story
@router.post("/demo/reset")
def reset(conn=Depends(get_conn_dep)):
    return _run(story.reset, conn)


@router.get("/story")
def get_story(conn=Depends(get_conn_dep)):
    return story.overview(conn)


@router.post("/story/next")
def story_next(conn=Depends(get_conn_dep)):
    return _run(story.next_step, conn)


@router.post("/story/{key}")
def story_step(key: str, conn=Depends(get_conn_dep)):
    return _run(story.run_step, conn, key)


# ------------------------------------------------------------------ state for map / panels
def _resources(conn) -> list[dict]:
    now = svc.now(conn)
    rows = conn.cursor(row_factory=dict_row).execute(
        """SELECT r.*, f.name AS facility_name,
                  count(*) FILTER (WHERE s.starts_at >= %s AND s.starts_at < %s + interval '3 days') AS slots_72h,
                  count(*) FILTER (WHERE s.starts_at >= %s AND s.starts_at < %s + interval '3 days' AND s.status='free') AS free_72h,
                  count(*) FILTER (WHERE s.starts_at >= %s AND s.starts_at < %s + interval '3 days' AND s.status='blocked') AS blocked_72h
           FROM ops_resource r JOIN facility f ON f.id=r.facility_id LEFT JOIN ops_slot s ON s.resource_id=r.id
           GROUP BY r.id, f.name ORDER BY r.facility_id, r.id""", (now, now, now, now, now, now)).fetchall()
    for r in rows:
        r["lon"], r["lat"] = FAC_COORD.get(r["facility_id"], (None, None))
        r["county"] = FAC_COUNTY.get(r["facility_id"])
        r["short"] = FAC_SHORT.get(r["facility_id"])
        r["utilisation_72h"] = round(1 - r["free_72h"] / r["slots_72h"], 2) if r["slots_72h"] else None
    return rows


def _brief(iv: dict | None) -> dict | None:
    if not iv:
        return None
    ev = iv.get("evaluation") or {}
    opts = {k: {"label": v.get("label"), "kpis": v.get("kpis")} for k, v in (ev.get("options") or {}).items()}
    pr = iv.get("proposal") or {}
    return {"id": iv["id"], "status": iv["status"], "source": iv["source"], "title": iv["title"], "parent_id": iv.get("parent_id"),
            "options": opts, "review": ev.get("review"), "by_facility": pr.get("by_facility"), "transfers": pr.get("transfers"),
            "rows": pr.get("rows") or [], "replies": pr.get("replies") or [], "applied": pr.get("applied") or [],
            "options_followup": pr.get("options") if pr.get("kind") == "followup" else None,
            "controller": pr.get("controller"), "state_version": pr.get("state_version") or ev.get("state_version"),
            "log": iv.get("log"), "decided_by": iv.get("decided_by"), "justification": iv.get("justification"),
            "assumptions": ev.get("assumptions"), "replications": ev.get("replications")}


@router.get("/state")
def state(conn=Depends(get_conn_dep)):
    st = story.state(conn)
    if not st:
        return {"initialised": False}
    d = interventions.active_disruption(conn)
    cur = interventions.current(conn)
    emma = None
    if st.get("emma_pathway"):
        s = svc.get_state(conn, st["emma_pathway"])
        emma = {"pathway_id": st["emma_pathway"], "patient_id": st["emma_patient"], "stage": s["current_stage"],
                "appointments": s["appointments"], "lon": s["patient"].get("lon"), "lat": s["patient"].get("lat")}
    return {"initialised": True, "now": svc.now(conn).isoformat(), "story_day": round(core.story_day(conn), 2),
            "resources": _resources(conn), "disruption": d, "intervention": _brief(cur),
            "interventions": interventions.listing(conn), "emma": emma,
            "notifications": core.notifications(conn, audience=["admin", "facility", "care_team"], limit=40),
            "story": story.overview(conn)}


@router.get("/interventions/{iv_id}")
def get_iv(iv_id: str, conn=Depends(get_conn_dep)):
    return _brief(_run(interventions.get, conn, iv_id))


# ------------------------------------------------------------------ disruptions & interventions
class DisruptionIn(BaseModel):
    resource_id: str = "res-pkks-endo2"
    hours: float = 48


@router.post("/disruptions")
def disrupt(body: DisruptionIn, conn=Depends(get_conn_dep)):
    return _run(interventions.trigger_resource_down, conn, body.resource_id, body.hours)


class ProposeIn(BaseModel):
    allocations: Optional[list[dict[str, Any]]] = None       # [{"facility": "Kuopio", "share": 0.5}]
    per_patient: Optional[dict[str, str]] = None             # {patient_id: facility}
    source: str = "admin"
    note: str = ""


@router.post("/interventions/propose")
def propose(body: ProposeIn, conn=Depends(get_conn_dep)):
    iv = _run(interventions.propose, conn, body.allocations, body.per_patient, body.source, COORD_USER, body.note)
    return {**_brief(iv), "explain": nl.explain(iv)}


class DecisionIn(BaseModel):
    justification: str = ""
    actor: str = COORD_USER


@router.post("/interventions/{iv_id}/approve")
def approve(iv_id: str, body: DecisionIn, conn=Depends(get_conn_dep)):
    r = interventions.approve_checked(conn, iv_id, body.actor, body.justification)
    if not r["committed"]:
        raise HTTPException(409 if r["error_category"] != "not_found" else 404, r["message"])
    return _brief(interventions.get(conn, iv_id))


@router.post("/interventions/{iv_id}/reject")
def reject(iv_id: str, body: DecisionIn, conn=Depends(get_conn_dep)):
    return _brief(_run(interventions.reject, conn, iv_id, body.actor, body.justification))


class AuthoriseIn(BaseModel):
    option: int = 0
    actor: str = core.NURSE_USER


@router.post("/followup/{iv_id}/authorise")
def authorise(iv_id: str, body: AuthoriseIn, conn=Depends(get_conn_dep)):
    return _brief(_run(followup.authorise, conn, iv_id, body.option, body.actor))


@router.get("/interventions/{iv_id}/explain")
def explain(iv_id: str, conn=Depends(get_conn_dep)):
    return nl.explain(_run(interventions.get, conn, iv_id))


class AskIn(BaseModel):
    question: str
    intervention_id: Optional[str] = None


@router.post("/ask")
def ask(body: AskIn, conn=Depends(get_conn_dep)):
    """Conversational analysis: a short grounded answer + the deterministic bullets it is grounded in."""
    iv = _run(interventions.get, conn, body.intervention_id) if body.intervention_id else interventions.current(conn)
    if not iv:
        raise HTTPException(409, "no intervention yet")
    ex = nl.explain(iv)
    facts = {"bullets": ex["bullets"], "why": ex["why"], "status": iv["status"], "title": iv["title"],
             "options": {k: v.get("kpis") for k, v in (iv.get("evaluation") or {}).get("options", {}).items()}}
    return {**ex, "answer": nl.answer(body.question, facts)}


class GroundedIn(BaseModel):
    question: str
    facts: dict[str, Any]


@router.post("/answer")
def grounded_answer(body: GroundedIn):
    """Short conversational answer about ANY panel's facts (e.g. the twin's Analysis grounding data). Numbers in the
    answer must appear in the facts, otherwise it is discarded (mode=fallback)."""
    return nl.answer(body.question[:500], body.facts)


# ------------------------------------------------------------------ commands (text box + voice share this)
class CommandIn(BaseModel):
    text: str
    source: str = "text"


@router.post("/command")
def command(body: CommandIn, conn=Depends(get_conn_dep)):
    cmd = nl.parse(body.text)
    act = cmd.get("action")
    cur = interventions.current(conn)
    if act == "propose" and (cmd.get("allocations") or cmd.get("per_patient")):
        per = None
        if cmd.get("per_patient"):
            names = {}
            for r in (cur or {}).get("proposal", {}).get("rows", []):
                names[r["name"].lower()] = names[r["name"].split(" ")[0].lower()] = names[r["name"].split(" ")[-1].lower()] = r["patient_id"]
            per = {names.get(x["patient"].lower(), names.get(x["patient"].split(" ")[0].lower(), x["patient"])): x["facility"]
                   for x in cmd["per_patient"]}
        iv = _run(interventions.propose, conn, cmd.get("allocations") or None, per, body.source, COORD_USER, body.text)
        ex = nl.explain(iv)
        return {"command": cmd, "intervention": _brief(iv), "explain": ex, "reply": _say(iv, ex)}
    if act == "approve" and re_recommended(body.text):
        r = conn.execute("""SELECT id FROM ops_intervention WHERE source IN ('optimiser','replan') AND status='AWAITING_APPROVAL'
                            ORDER BY created_at DESC LIMIT 1""").fetchone()
        if r:
            cur = interventions.get(conn, r[0])
    if act == "approve" and cur and cur["status"] == "AWAITING_APPROVAL":
        r = interventions.approve_checked(conn, cur["id"], COORD_USER, cmd.get("justification") or body.text)
        iv = interventions.get(conn, r.get("new_plan_id") or cur["id"])
        reply = (f"Approved and applied: {iv['title']}. Patients and receiving units are being notified." if r["committed"]
                 else f"Not approved ({r['error_category']}): {r['message']}")
        return {"command": cmd, "intervention": _brief(iv), "approval": r, "reply": reply}
    if act == "reject" and cur and cur["status"] == "AWAITING_APPROVAL":
        iv = _run(interventions.reject, conn, cur["id"], COORD_USER, body.text)
        return {"command": cmd, "intervention": _brief(iv), "reply": "Rejected. The previous recommendation is still available."}
    if cur:
        ex = nl.explain(cur)
        return {"command": cmd, "intervention": _brief(cur), "explain": ex, "reply": _say(cur, ex)}
    return {"command": cmd, "reply": "There is no active disruption right now."}


def re_recommended(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in ("recommend", "your plan", "your proposal", "optimis", "optimiz", "cancerflow"))


def _say(iv, ex) -> str:
    if iv["status"] == "REJECTED":
        return "That plan can't be executed: " + "; ".join(ex["why"][:2] or [iv["log"][-1]["note"]])
    head = ex["bullets"][0] if ex["bullets"] else iv["title"]
    if ex.get("verdict") == "comparable_with_risks":
        return f"{head}. Comparable to my recommendation, but: " + " ".join(ex["why"][:2])
    if ex.get("verdict") == "worse_than_recommendation":
        return f"{head}. That allocation is worse than my recommendation: " + " ".join(ex["why"][:2])
    return f"{head}. " + (" ".join(ex["why"][:1]) if ex["why"] else "")


# ------------------------------------------------------------------ notifications + patient side
@router.get("/notifications")
def notifications(audience: Optional[str] = None, patient_id: Optional[str] = None, facility_id: Optional[str] = None,
                  x_user_id: Optional[str] = Header(default=None), conn=Depends(get_conn_dep)):
    if x_user_id and not patient_id and not facility_id and not audience:
        u = svc.get_user(conn, x_user_id)
        if u["role"] == "patient":
            return core.notifications(conn, audience="patient", patient_id=u["patient_id"])
        return core.notifications(conn, audience=["facility", "care_team"], facility_id=u["facility_id"])
    return core.notifications(conn, audience=audience.split(",") if audience else None, patient_id=patient_id, facility_id=facility_id)


@router.post("/appointments/{appointment_id}/confirm")
def confirm(appointment_id: str, x_user_id: Optional[str] = Header(default=None), conn=Depends(get_conn_dep)):
    user = x_user_id or "pat-emma"
    u = _run(svc.get_user, conn, user)
    if u["role"] == "patient":
        owner = conn.execute("""SELECT p.patient_id FROM appointment a JOIN pathway p ON p.id=a.pathway_id WHERE a.id=%s""",
                             (appointment_id,)).fetchone()
        if not owner or owner[0] != u["patient_id"]:
            raise HTTPException(403, "patients may only confirm their own appointments")
    return _run(story.confirm_appointment, conn, appointment_id, user)


@router.get("/patients/{patient_id}/agent")
def patient_agent(patient_id: str, conn=Depends(get_conn_dep)):
    r = conn.execute("SELECT persona, memory FROM ops_patient_agent WHERE patient_id=%s", (patient_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no agent")
    p = r[0]
    return {"persona": {k: p.get(k) for k in ("name", "birth_year", "muni", "occupation", "household", "transport", "constraints",
                                              "tone", "archetype", "accept_prob", "travel_min", "p_attend")},
            "memory": r[1], "tag": "SYNTHETIC agent"}


# ------------------------------------------------------------------ voice (ElevenLabs)
@router.get("/voice/session")
def voice_session(conn=Depends(get_conn_dep)):
    """Signed URL for the in-browser ElevenLabs conversation + dynamic variables taken from backend state."""
    key, agent = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_AGENT_ID")
    if not key or not agent:
        raise HTTPException(503, "voice not configured (ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID)")
    from elevenlabs import ElevenLabs
    url = ElevenLabs(api_key=key).conversational_ai.conversations.get_signed_url(agent_id=agent).signed_url
    return {"signed_url": url, "dynamic_variables": voice_brief(conn)}


@router.get("/voice/brief")
def voice_brief(conn=Depends(get_conn_dep)):
    d = interventions.active_disruption(conn)
    iv = interventions.current(conn)
    if not d:
        return {"headline": "There is no active disruption.", "affected_count": 0, "urgent_count": 0, "status": "none"}
    s = d["summary"]
    ex = nl.explain(iv) if iv else {"bullets": []}
    ev = (iv or {}).get("evaluation", {}).get("options", {})
    o, n = ev.get("optimised", {}).get("kpis", {}), ev.get("no_action", {}).get("kpis", {})
    by = (iv or {}).get("proposal", {}).get("by_facility") or {}
    return {"resource": s["resource"], "facility": s["facility"], "hours": int(s["hours"]),
            "affected_count": s["affected_count"], "urgent_count": s["urgent_count"],
            "status": (iv or {}).get("status", "none"),
            "recommendation": ", ".join(f"{v} to {k}" for k, v in by.items()) or "still being evaluated",
            "on_time_recommended": o.get("pct_within_target"), "on_time_no_action": n.get("pct_within_target"),
            "urgent_on_time_recommended": o.get("pct_urgent_within_target"), "urgent_on_time_no_action": n.get("pct_urgent_within_target"),
            "headline": f"{s['resource']} at {s['facility']} is offline for {int(s['hours'])} hours; "
                        f"{s['affected_count']} colonoscopies are affected, {s['urgent_count']} of them urgent.",
            "bullets": ex.get("bullets", [])}


# ------------------------------------------------------------------ typed voice tools (no natural-language parsing)
from backend.ops import evaluate as _ev  # noqa: E402

GLOSSARY = {"mean_colonoscopy_day": "days from today until the colonoscopy (diagnostic appointment)",
            "mean_diagnosis_day": "days from today until the histology result (confirmed diagnosis)",
            "mean_treatment_day": "days from today until TREATMENT STARTS (only patients who turn out to need treatment)",
            "p90_treatment_day": "90th percentile of treatment start (days from today)",
            "pct_within_target": "% of patients needing treatment who start it within 42 days of referral",
            "pct_urgent_within_target": "same, urgent referrals only",
            "urgent_mean_lateness_days": "urgent patients: mean days past the 42-day target",
            "mean_lateness_days": "all patients: mean days past target (lost patient = 60 d)",
            "pct_severe_delay": "% more than 14 days past target"}
HIERARCHY = [lbl for *_, lbl in _ev.LEX]


def _pending_plan(conn, which: str = "recommended", plan_id: Optional[str] = None):
    if plan_id:
        return interventions.get(conn, plan_id)
    srcs = ["optimiser", "replan"] if which == "recommended" else ["optimiser", "replan", "admin", "voice", "text", "map"]
    r = conn.execute("""SELECT id FROM ops_intervention WHERE source = ANY(%s) AND status IN ('AWAITING_APPROVAL','APPLIED')
                        ORDER BY (status='AWAITING_APPROVAL') DESC, created_at DESC LIMIT 1""", (srcs,)).fetchone()
    return interventions.get(conn, r[0]) if r else None


def _plan_summary(conn, iv) -> dict:
    ev = iv.get("evaluation") or {}
    pr = iv.get("proposal") or {}
    opts = ev.get("options") or {}
    mine = "admin" if "admin" in opts else ("replan" if "replan" in opts else ("optimised" if pr.get("basis") == "optimised" else "no_action"))
    k = (opts.get(mine) or {}).get("kpis", {})
    return {"plan_id": iv["id"], "status": iv["status"], "title": iv["title"], "source": iv["source"],
            "computed_on_state_version": ev.get("state_version"),
            "stale": interventions.is_stale(conn, iv) if iv["status"] == "AWAITING_APPROVAL" else False,
            "provenance": {"engine": "whole-pathway MPC (lexicographic MIP) + 200 stochastic replays" if mine != "admin" else
                           "administrator allocation, scheduled and replayed 200 times", "evaluated_option": mine,
                           "controller": pr.get("controller"), "day0": ev.get("day0"), "simulated": True},
            "allocation": pr.get("by_facility"), "kpis": {m: k.get(m) for m in GLOSSARY},
            "patients": [{"name": r["name"], "priority": r["priority"], "to": r["to"]["facility"], "colonoscopy_at": r["to"]["at"],
                          "expected_colonoscopy_day": (r.get("outcome") or {}).get("colonoscopy_day"),
                          "expected_treatment_day_if_needed": (r.get("outcome") or {}).get("treatment_day"),
                          "p_treated_within_target": (r.get("outcome") or {}).get("p_on_time"),
                          "p_accepts_offer": r.get("accept_prob")} for r in pr.get("rows", [])],
            "glossary": GLOSSARY}


def available_tools(conn, d, plan) -> dict:
    """What the agent may actually offer right now (checked against backend state, not assumed)."""
    hold = interventions.approval_hold(conn)
    has_ctx = bool(d)
    return {"get_situation": True, "compare_plans": bool(plan), "get_plan_details": bool(plan), "explain_plan": bool(plan),
            "run_mpc": has_ctx, "propose_allocation": has_ctx,
            "approve_plan": bool(plan and plan["status"] == "AWAITING_APPROVAL" and not (hold and hold.get("on"))),
            "check_approval": bool(plan), "set_hold": True,
            "approvals_on_hold": bool(hold and hold.get("on"))}


class HoldIn(BaseModel):
    hold: bool = True
    reason: str = ""


@router.post("/voice/hold")
def voice_hold(body: HoldIn, conn=Depends(get_conn_dep)):
    return interventions.set_hold(conn, body.hold, body.reason)


@router.get("/voice/situation")
def voice_situation(conn=Depends(get_conn_dep)):
    d = interventions.recovery_context(conn)
    plan = _pending_plan(conn)
    return {"disruption": d and {"resource": d["summary"]["resource"], "facility": d["summary"]["facility"], "hours": d["summary"]["hours"],
                                 "from": d["summary"]["starts_at"], "until": d["summary"]["ends_at"],
                                 "affected": d["summary"]["affected"], "affected_count": d["summary"]["affected_count"],
                                 "urgent_count": d["summary"]["urgent_count"]},
            "outage_active": bool(d and d.get("status") == "active"),
            "current_plan": plan and _plan_summary(conn, plan), "objective_hierarchy": HIERARCHY,
            "available_tools": available_tools(conn, d, plan),
            "note": "propose_allocation and run_mpc are evaluations only: they never change appointments."}


@router.post("/voice/optimise")
def voice_optimise(conn=Depends(get_conn_dep)):
    """'Use MPC to find a solution': fresh outer-MPC plan on the current state (a few seconds)."""
    plan = _pending_plan(conn)
    new_id = _run(interventions.recompute, conn, plan)
    conn.commit()
    iv = interventions.get(conn, new_id)
    return {"plan": _plan_summary(conn, iv), "ranking": _ev.rank_options((iv.get("evaluation") or {}).get("options") or {}),
            "objective_hierarchy": HIERARCHY}


@router.get("/voice/compare")
def voice_compare(conn=Depends(get_conn_dep)):
    """All evaluated options for the active disruption, ranked by the clinical hierarchy (not by one average)."""
    plan = _pending_plan(conn)
    if not plan:
        return {"ranking": [], "message": "no evaluated plan yet: call run_mpc"}
    opts = dict((plan.get("evaluation") or {}).get("options") or {})
    for (iid,) in conn.execute("""SELECT id FROM ops_intervention WHERE source IN ('admin','voice','text','map')
                                  AND status IN ('AWAITING_APPROVAL','EVALUATED') ORDER BY created_at DESC LIMIT 3""").fetchall():
        a = (interventions.get(conn, iid).get("evaluation") or {}).get("options", {}).get("admin")
        if a:
            opts[f"admin:{iid}"] = {**a, "label": f"Donny's proposal ({str(iid)[:8]})"}
    return {"ranking": _ev.rank_options(opts), "recommended_plan_id": plan["id"], "objective_hierarchy": HIERARCHY,
            "glossary": GLOSSARY, "note": "Ranked tier by tier: a lower tier only breaks ties of the tiers above (tolerances apply). SIMULATED."}


@router.get("/voice/plan")
def voice_plan(which: str = "recommended", plan_id: Optional[str] = None, conn=Depends(get_conn_dep)):
    plan = _pending_plan(conn, which, plan_id)
    if not plan:
        raise HTTPException(404, "no plan")
    return _plan_summary(conn, plan)


class VoiceApproveIn(BaseModel):
    plan_id: Optional[str] = None
    which: str = "recommended"
    justification: str = ""
    donny_words: str = ""          # Donny's literal authorising utterance (checked server-side)


AFFIRM = re.compile(r"\b(approve|approved|yes|go ahead|execute|do it|confirm|confirmed|proceed|apply)\b", re.I)
NEGATE = re.compile(r"\b(don'?t|do not|no|not|cancel|stop|wait|hold|hypothetical|just evaluate|only evaluate)\b", re.I)


def authorisation_ok(words: str) -> tuple[bool, str]:
    """Execution needs Donny's own explicit words: an affirmative and no negation/hypothetical in the same utterance."""
    w = (words or "").strip()
    if not w:
        return False, "missing: quote Donny's exact words authorising execution"
    if NEGATE.search(w):
        return False, "Donny's words contain a negation or a hypothetical; ask whether he wants to execute or only evaluate"
    if not AFFIRM.search(w):
        return False, "Donny's words do not explicitly authorise execution; ask him to confirm"
    return True, ""


@router.post("/voice/approve")
def voice_approve(body: VoiceApproveIn, conn=Depends(get_conn_dep)):
    """Always HTTP 200 with committed true/false + error_category, so the agent never loops on transport errors."""
    ok, why = authorisation_ok(body.donny_words)
    if not ok:
        return {"committed": False, "error_category": "needs_explicit_authorization", "message": why}
    try:
        plan = _pending_plan(conn, body.which, body.plan_id)
    except svc.NotFound:
        plan = None
    if not plan:
        return {"committed": False, "error_category": "not_found", "message": "no plan awaiting approval"}
    return interventions.approve_checked(conn, plan["id"], COORD_USER, body.justification or body.donny_words)


@router.get("/voice/approval/{plan_id}")
def voice_approval(plan_id: str, conn=Depends(get_conn_dep)):
    iv = _run(interventions.get, conn, plan_id)
    return {"plan_id": plan_id, "status": iv["status"], "committed": iv["status"] == "APPLIED",
            "appointments_moved": len((iv.get("proposal") or {}).get("applied", [])), "decided_by": iv.get("decided_by"),
            "log": [f"{x['status']}: {x['note']}" for x in iv.get("log", [])[-4:]]}
