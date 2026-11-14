"""Behavioural-agent endpoints: network anomalies, sampled patient journeys, grounded patient explanations,
and primary-care -> hospital flows for the regional map."""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from fastapi import Depends, HTTPException
from psycopg.rows import dict_row

from backend.api.twin import base_snapshot, get_conn_dep, latest_run, router
from backend.services import anomalies as an
from backend.services import llm
from backend.services.twin_context import metrics_of


def _jitter(pid: int, scale=0.06):
    h = hashlib.md5(str(pid).encode()).digest()
    return (h[0] / 255 - 0.5) * scale * 2, (h[1] / 255 - 0.5) * scale


def _scope_filter(a: dict, scope: str, sid: Optional[str], snap) -> bool:
    if scope == "national" or not sid:
        return True
    if scope == "county":
        return a["location"].get("county_id") == sid or sid in a.get("counties", []) or any(
            snap.facilities.get(n, {}).get("county") == sid for n in a.get("nodes", []))
    return sid in a.get("nodes", []) or a["location"].get("facility_id") == sid


@router.get("/anomalies")
def anomalies(run_id: Optional[str] = None, base_run_id: Optional[str] = None, scope: str = "national",
              id: Optional[str] = None, conn=Depends(get_conn_dep)):
    snap = base_snapshot(conn)
    run_id = run_id or latest_run(conn, baseline=True)
    B = metrics_of(conn, base_run_id) if base_run_id and base_run_id != run_id else None
    alarms = an.detect(snap, metrics_of(conn, run_id), B)
    return {"run_id": run_id, "thresholds": an.T, "alarms": [a for a in alarms if _scope_filter(a, scope, id, snap)],
            "total": len(alarms)}


@router.get("/patients")
def patients(run_id: Optional[str] = None, county: Optional[str] = None, limit: int = 80, conn=Depends(get_conn_dep)):
    """Sampled synthetic patient agents (1 in 10 of replication 0) with home location + outcome summary."""
    run_id = run_id or latest_run(conn, baseline=True)
    q = """SELECT p.pid, p.route, p.county, p.municipality, p.archetype, p.attrs, p.outcome,
                  ST_X(m.centroid::geometry) lon, ST_Y(m.centroid::geometry) lat,
                  (SELECT count(*) FROM jsonb_array_elements(p.journey) e WHERE e->>'kind'='offer' AND e->>'accepted'='false') declined,
                  (SELECT count(*) FROM jsonb_array_elements(p.journey) e WHERE e->>'kind'='attendance' AND e->>'attended'='false') missed
           FROM sim_patient p LEFT JOIN region m ON m.id=p.municipality WHERE p.run_id=%s"""
    args: list = [run_id]
    if county:
        q += " AND p.county=%s"; args.append(county)
    rows = conn.cursor(row_factory=dict_row).execute(q + " ORDER BY p.pid LIMIT %s", args + [limit]).fetchall()
    out = []
    for r in rows:
        dx, dy = _jitter(r["pid"])
        rtt = (r["outcome"].get("clocks") or {}).get("referral_to_treatment") or {}
        out.append({"pid": r["pid"], "route": r["route"], "county": r["county"], "archetype": r["archetype"],
                    "urgent": r["attrs"].get("urgent"), "age": r["attrs"].get("age"),
                    "lon": (r["lon"] or 25) + dx, "lat": (r["lat"] or 63) + dy,
                    "lost": r["outcome"].get("lost"), "dna": r["outcome"].get("dna"), "declined": r["declined"], "missed": r["missed"],
                    "rtt_status": rtt.get("status"), "rtt_days": rtt.get("days"), "tag": "SIMULATED (synthetic agent)"})
    return {"run_id": run_id, "sample": "every 10th simulated patient, replication 0", "patients": out}


def _patient(conn, run_id, pid):
    r = conn.cursor(row_factory=dict_row).execute("SELECT * FROM sim_patient WHERE run_id=%s AND pid=%s", (run_id, pid)).fetchone()
    if not r:
        return None
    r["run_id"] = str(r["run_id"])
    return r


def _facility_lookup(conn, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {r[0]: {"name": r[1], "kind": r[2], "lon": r[3], "lat": r[4]} for r in conn.execute(
        "SELECT id, name, kind, ST_X(geom::geometry), ST_Y(geom::geometry) FROM facility WHERE id = ANY(%s)", (list(set(ids)),))}


@router.get("/patients/{run_id}/{pid}")
def patient_detail(run_id: str, pid: int, compare_run: Optional[str] = None, conn=Depends(get_conn_dep)):
    """One agent's attributes + full journey; with compare_run, the SAME patient (common random numbers) in the other run."""
    p = _patient(conn, run_id, pid)
    if not p:
        raise HTTPException(404, "patient not sampled in this run")
    other = _patient(conn, compare_run, pid) if compare_run else None
    ids = [e.get("facility") for e in p["journey"]] + [e.get("home") for e in p["journey"]]
    if other:
        ids += [e.get("facility") for e in other["journey"]]
    m = conn.execute("SELECT name_fi, ST_X(centroid::geometry), ST_Y(centroid::geometry) FROM region WHERE id=%s", (p["municipality"],)).fetchone()
    dx, dy = _jitter(pid)
    return {"patient": p, "compare": other, "facilities": _facility_lookup(conn, ids),
            "home": {"municipality": m[0] if m else None, "lon": (m[1] if m else 25) + dx, "lat": (m[2] if m else 63) + dy},
            "tag": "SIMULATED synthetic agent (fictional person)"}


EXPLAIN_SYSTEM = """You explain ONE fictional simulated patient's journey to a healthcare planner in at most 90 words.
Use ONLY the recorded attributes and events in the JSON. Do not invent motivations, feelings or facts.
Explain decisions in terms of the recorded variables (e.g. travel minutes vs the patient's max_travel_min, p_accept,
p_attend, waiting days, urgency, archetype). Say clearly this is a synthetic agent. If COMPARE is present, say how the
same patient's journey differed in the modified scenario. Plain text, no lists."""


@router.post("/patients/{run_id}/{pid}/explain")
def explain(run_id: str, pid: int, compare_run: Optional[str] = None, conn=Depends(get_conn_dep)):
    d = patient_detail(run_id, pid, compare_run, conn)
    names = {k: v["name"] for k, v in d["facilities"].items()}

    def slim(p):
        return {"attrs": p["attrs"], "route": p["route"],
                "events": [{**e, "facility": names.get(e.get("facility"), e.get("facility")), "home": names.get(e.get("home"), e.get("home"))}
                           for e in p["journey"]],
                "outcome": {"clocks": p["outcome"].get("clocks"), "lost": p["outcome"].get("lost"), "missed_appointments": p["outcome"].get("dna")}}
    facts = {"PATIENT": slim(d["patient"]), **({"COMPARE": slim(d["compare"])} if d["compare"] else {})}
    fallback = _fallback_explain(facts)
    out = {"mode": "fallback", "text": fallback, "facts": facts}
    if llm.available():
        txt = llm.chat([{"role": "system", "content": EXPLAIN_SYSTEM},
                        {"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)}], max_tokens=900)
        if txt:
            bad = llm.ungrounded_numbers(txt, facts)
            if bad:
                out["rejected"] = {"reason": "numbers not in the recorded journey", "numbers": bad[:6]}
            else:
                out.update({"mode": "llm", "text": txt})
    return out


def _fallback_explain(f) -> str:
    p = f["PATIENT"]
    a = p["attrs"]
    parts = [f"Synthetic agent ({a['archetype'].replace('_', ' ')}, age {a['age']}, {'urgent' if a['urgent'] else 'standard'} "
             f"{p['route']} referral; tolerates about {a['max_travel_min']:.0f} min travel, attends with p≈{a['p_attend']:.2f})."]
    for e in p["events"]:
        if e["kind"] == "offer":
            parts.append(f"Day {e['t']:.0f}: offered colonoscopy at {e['facility']} ({e['travel_min']} min away, ~{e['est_wait_days']} d wait) "
                         f"vs home {e['home']} ({e['home_travel_min']} min, ~{e['home_wait_days']} d): p_accept {e['p_accept']:.2f} → "
                         f"{'accepted' if e['accepted'] else 'declined, rebooked at home'}.")
        elif e["kind"] == "attendance" and not e["attended"]:
            parts.append(f"Day {e['t']:.0f}: missed the appointment at {e['facility']} (p_attend {e['p_attend']:.2f}); the slot was wasted.")
        elif e["kind"] == "stall":
            parts.append(f"Day {e['t']:.0f}: step '{e['stage']}' stalled for {e['days']} d"
                         + (" (caught by monitoring)." if e.get("detected_by_monitoring") else " (nobody noticed)."))
        elif e["kind"] == "lost_to_follow_up":
            parts.append("Lost to follow-up after repeated missed appointments.")
    rtt = (p["outcome"]["clocks"] or {}).get("referral_to_treatment")
    if rtt and rtt.get("days") is not None:
        parts.append(f"Referral → treatment: {rtt['days']} d ({rtt['status']}).")
    if "COMPARE" in f:
        c = (f["COMPARE"]["outcome"]["clocks"] or {}).get("referral_to_treatment")
        if c and c.get("days") is not None:
            parts.append(f"Same patient in the modified scenario: {c['days']} d ({c['status']}).")
    return " ".join(parts)


@router.get("/hc_flows")
def hc_flows(run_id: Optional[str] = None, county: Optional[str] = None, conn=Depends(get_conn_dep)):
    """Primary care (health centre) -> colonoscopy unit referral flows for symptomatic patients (simulated)."""
    run_id = run_id or latest_run(conn, baseline=True)
    rows = conn.execute("""SELECT scope_id, mean FROM sim_metric WHERE run_id=%s AND scope_kind='edge'
                           AND scope_id LIKE 'hc:%%' AND metric='flow_per_week'""", (run_id,)).fetchall()
    flows = []
    for sid, v in rows:
        src, rest = sid.split("->")
        flows.append({"hc": src[3:], "facility": rest.split("|")[0], "flow_per_week": v})
    fl = _facility_lookup(conn, [f["hc"] for f in flows] + [f["facility"] for f in flows])
    cty = {r[0]: r[1] for r in conn.execute("SELECT id, region_id FROM facility WHERE id = ANY(%s)", ([f["hc"] for f in flows],))}
    out = []
    for f in flows:
        if county and cty.get(f["hc"]) != county:
            continue
        a, b = fl.get(f["hc"]), fl.get(f["facility"])
        if a and b:
            out.append({**f, "hc_name": a["name"], "from": [a["lon"], a["lat"]], "to": [b["lon"], b["lat"]], "county": cty.get(f["hc"]), "tag": "SIMULATED"})
    return {"run_id": run_id, "flows": out}
