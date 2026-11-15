"""Disruptions and the auditable intervention lifecycle.

  DETECTED -> PROPOSED -> EVALUATED -> AWAITING_APPROVAL -> APPROVED -> APPLIED     (+ REJECTED, FAILED, SUPERSEDED)

Voice, map and buttons all call these functions, so there is ONE proposal state. Nothing operational changes before
a human approves. Apply is idempotent (deterministic event/appointment ids, unique notification keys). Patient
agents then answer their offers; declines / time requests trigger a re-plan of those patients' remaining pathway
(their current booking is held until a human approves the re-plan).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import timedelta

from psycopg.rows import dict_row

from backend.ops import agents, core, evaluate
from backend.ops.core import COORD_USER, ENDO_USER, did
from backend.ops.world import FAC_COUNTY, FAC_SHORT, HOME_FACILITY
from backend.services import pathways as svc

OPEN = ("DETECTED", "PROPOSED", "EVALUATED", "AWAITING_APPROVAL", "APPROVED")


class StalePlan(svc.Conflict):
    pass


def is_stale(conn, iv) -> bool:
    ver = (iv.get("evaluation") or {}).get("state_version")
    return bool(ver) and ver != evaluate.state_version(conn)


def recompute(conn, parent) -> str:
    """Fresh outer-MPC plan for the active disruption on the CURRENT state (supersedes pending optimiser plans)."""
    d = recovery_context(conn)
    if not d:
        raise svc.Conflict("no disruption to recover from (no outage and no recovery plan on record)")
    for (oid,) in conn.execute("""SELECT id FROM ops_intervention WHERE disruption_id=%s AND source IN ('optimiser','replan')
                                  AND status = ANY(%s)""", (d["id"], ["PROPOSED", "EVALUATED", "AWAITING_APPROVAL"])).fetchall():
        set_status(conn, oid, "SUPERSEDED", "cancerflow", "superseded by a fresh plan on the current state")
    new_id = uuid.uuid4()
    now = svc.now(conn)
    conn.execute("""INSERT INTO ops_intervention(id,disruption_id,parent_id,source,status,title,created_at,updated_at)
                    VALUES (%s,%s,%s,'optimiser','DETECTED',%s,%s,%s)""",
                 (new_id, d["id"], (parent or {}).get("id"), f"Recover {d['summary'].get('affected_count')} appointments (fresh MPC plan)", now, now))
    log(conn, new_id, "DETECTED", "cancerflow", "fresh optimisation requested on the current state")
    evaluate_optimiser(conn, new_id)
    return str(new_id)


def approve_checked(conn, iv_id, actor=COORD_USER, justification="") -> dict:
    """Transactional, idempotent approval with a machine-readable outcome (used by voice, chat and buttons).
    committed=True only after the database commit succeeded. Never raises for expected failures."""
    try:
        iv = get(conn, iv_id)
    except svc.NotFound:
        return {"committed": False, "error_category": "not_found", "message": f"no plan {iv_id}"}
    if iv["status"] == "APPLIED":
        return {"committed": True, "already_applied": True, "plan_id": str(iv_id), "status": "APPLIED"}
    if iv["status"] != "AWAITING_APPROVAL":
        return {"committed": False, "error_category": "not_pending", "plan_id": str(iv_id), "status": iv["status"],
                "message": f"this plan is {iv['status']}, not awaiting approval"}
    hold = approval_hold(conn)
    if hold and hold.get("on"):
        return {"committed": False, "error_category": "on_hold", "plan_id": str(iv_id),
                "message": f"approvals are on hold ({hold.get('reason') or 'operator asked not to submit plans'}); lift the hold first"}
    if is_stale(conn, iv):
        new_id = recompute(conn, iv)
        conn.commit()
        return {"committed": False, "error_category": "stale_plan", "plan_id": str(iv_id), "new_plan_id": new_id,
                "message": "the bookable state changed since this plan was computed; a fresh plan was computed and is awaiting approval"}
    try:
        with conn.transaction():                          # savepoint: a failed apply leaves no partial bookings
            approve(conn, iv_id, actor, justification)
        conn.commit()
    except Exception as e:
        conn.rollback()
        try:
            log(conn, iv_id, "AWAITING_APPROVAL", "cancerflow", f"approval attempt failed ({type(e).__name__}): {str(e)[:200]}")
            conn.commit()
        except Exception:
            conn.rollback()
        cat = "apply_conflict" if isinstance(e, svc.Conflict) else "internal_error"
        return {"committed": False, "error_category": cat, "plan_id": str(iv_id), "message": str(e)[:300],
                "pending": True, "retry_safe": False}
    done = get(conn, iv_id)
    return {"committed": True, "plan_id": str(iv_id), "status": done["status"],
            "appointments_moved": len(done["proposal"].get("applied", [])),
            "notified": "patients and receiving hospitals", "approved_by": actor}


# ------------------------------------------------------------------ helpers
def log(conn, iv_id, status, actor=None, note=None):
    conn.execute("INSERT INTO ops_intervention_log(intervention_id,status,actor,note,at) VALUES (%s,%s,%s,%s,%s)",
                 (iv_id, status, actor, note, svc.now(conn)))


def set_status(conn, iv_id, status, actor=None, note=None, **fields):
    sets, args = ["status=%s", "updated_at=%s"], [status, svc.now(conn)]
    for k, v in fields.items():
        sets.append(f"{k}=%s" + ("::jsonb" if k in ("proposal", "evaluation") else ""))
        args.append(json.dumps(v, default=str) if k in ("proposal", "evaluation") else v)
    conn.execute(f"UPDATE ops_intervention SET {', '.join(sets)} WHERE id=%s", (*args, iv_id))
    log(conn, iv_id, status, actor, note)
    core.emit({"kind": "ops", "what": "intervention", "id": str(iv_id), "status": status})


def get(conn, iv_id) -> dict:
    r = conn.cursor(row_factory=dict_row).execute("SELECT * FROM ops_intervention WHERE id=%s", (iv_id,)).fetchone()
    if not r:
        raise svc.NotFound(f"intervention {iv_id}")
    r["log"] = conn.cursor(row_factory=dict_row).execute(
        "SELECT status, actor, note, at FROM ops_intervention_log WHERE intervention_id=%s ORDER BY id", (iv_id,)).fetchall()
    return _clean(r)


def _clean(r):
    for k in ("id", "disruption_id", "parent_id"):
        if r.get(k):
            r[k] = str(r[k])
    return r


def active_disruption(conn):
    r = conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM ops_disruption WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
    if r:
        r["id"] = str(r["id"])
    return r


def recovery_context(conn):
    """The disruption the operator is working on: the active one, or (after the outage window ended or the clock moved on)
    the one behind the latest recovery plan. Resolved from the backend, never from conversation memory."""
    d = active_disruption(conn)
    if d:
        return d
    r = conn.cursor(row_factory=dict_row).execute(
        """SELECT d.* FROM ops_intervention i JOIN ops_disruption d ON d.id=i.disruption_id
           WHERE i.source IN ('optimiser','replan','admin','voice','text','map') ORDER BY i.created_at DESC LIMIT 1""").fetchone()
    if r:
        r["id"] = str(r["id"])
    return r


def approval_hold(conn):
    return core.get_setting(conn, "ops_approval_hold") or None


def set_hold(conn, on: bool, reason: str = "", actor: str = COORD_USER) -> dict:
    """'Don't submit any plans': no approval can execute until the operator lifts the hold."""
    val = {"on": True, "reason": reason, "by": actor, "at": svc.now(conn).isoformat()} if on else None
    core.set_setting(conn, "ops_approval_hold", val)
    core.emit({"kind": "ops", "what": "hold", "on": on})
    return {"hold": bool(on), "reason": reason}


def current(conn):
    """The intervention the administrator should look at now (latest open one; else the latest)."""
    r = conn.cursor(row_factory=dict_row).execute(
        """SELECT id FROM ops_intervention ORDER BY (status = ANY(%s)) DESC, created_at DESC LIMIT 1""", (list(OPEN),)).fetchone()
    return get(conn, r["id"]) if r else None


def listing(conn) -> list[dict]:
    rows = conn.cursor(row_factory=dict_row).execute(
        "SELECT id, parent_id, source, status, title, created_at, decided_by FROM ops_intervention ORDER BY created_at").fetchall()
    return [_clean(r) for r in rows]


# ------------------------------------------------------------------ disruption
def resolve_expired(conn) -> None:
    """When the demo clock passes a disruption's end, the resource comes back online (its later slots were never blocked)."""
    now = svc.now(conn)
    for d_id, rid in conn.execute("SELECT id, resource_id FROM ops_disruption WHERE status='active' AND ends_at <= %s", (now,)).fetchall():
        conn.execute("UPDATE ops_disruption SET status='resolved' WHERE id=%s", (d_id,))
        conn.execute("UPDATE ops_resource SET status='online' WHERE id=%s", (rid,))
        core.notify(conn, key=f"dis-end-{d_id}", audience="admin", kind="recovered", title="Endoscopy Unit 2 is back online",
                    body="The 48-hour outage has ended; remaining capacity is bookable again.")
        core.emit({"kind": "ops", "what": "disruption_resolved", "id": str(d_id)})


def trigger_resource_down(conn, resource_id: str, hours: float = 48, background: bool = True) -> dict:
    run = core.get_setting(conn, "ops_story", {}).get("run", 0)
    d_id = did("disruption", run, resource_id)
    ex = conn.execute("SELECT id FROM ops_disruption WHERE id=%s", (d_id,)).fetchone()
    now = svc.now(conn)
    res = conn.cursor(row_factory=dict_row).execute("SELECT * FROM ops_resource WHERE id=%s", (resource_id,)).fetchone()
    if not res:
        raise svc.NotFound(f"resource {resource_id}")
    if not ex:
        ends = now + timedelta(hours=hours)
        conn.execute("INSERT INTO ops_disruption(id,kind,resource_id,starts_at,ends_at,created_at) VALUES (%s,'resource_down',%s,%s,%s,%s)",
                     (d_id, resource_id, now, ends, now))
        conn.execute("UPDATE ops_resource SET status='offline' WHERE id=%s", (resource_id,))
        conn.execute("UPDATE ops_slot SET status='blocked' WHERE resource_id=%s AND starts_at >= %s AND starts_at < %s",
                     (resource_id, now, ends))
    aff = evaluate.affected_patients(conn, d_id)
    d_row = conn.execute("SELECT starts_at, ends_at FROM ops_disruption WHERE id=%s", (d_id,)).fetchone()
    summary = {"resource": res["name"], "resource_id": resource_id, "facility_id": res["facility_id"],
               "facility": FAC_SHORT.get(res["facility_id"]), "hours": hours,
               "starts_at": d_row[0].isoformat(), "ends_at": d_row[1].isoformat(),
               "affected_count": len(aff), "urgent_count": sum(1 for a in aff if a["priority"] == "urgent"),
               "affected": [{"patient_id": a["patient_id"], "name": a["name"], "priority": a["priority"],
                             "pathway_id": str(a["pathway_id"]), "at": a["starts_at"].isoformat()} for a in aff]}
    conn.execute("UPDATE ops_disruption SET summary=%s::jsonb WHERE id=%s", (json.dumps(summary), d_id))
    core.notify(conn, key=f"dis-admin-{d_id}", audience="admin", severity="critical", kind="disruption",
                title=f"{res['name']} at {FAC_SHORT.get(res['facility_id'])} is offline for {int(hours)} h",
                body=f"{len(aff)} booked colonoscopies affected ({summary['urgent_count']} urgent). Recovery options are being evaluated.",
                facility_id=res["facility_id"], payload={"disruption_id": str(d_id)})
    core.notify(conn, key=f"dis-fac-{d_id}", audience="facility", severity="critical", kind="disruption", facility_id=res["facility_id"],
                title=f"{res['name']} offline: {len(aff)} appointments need rescheduling",
                body="CancerFlow is preparing a coordinated recovery plan for the regional coordinator.")
    iv_id = did("iv-opt", d_id)
    if not conn.execute("SELECT 1 FROM ops_intervention WHERE id=%s", (iv_id,)).fetchone():
        conn.execute("""INSERT INTO ops_intervention(id,disruption_id,source,status,title,created_at,updated_at)
                        VALUES (%s,%s,'optimiser','DETECTED',%s,%s,%s)""",
                     (iv_id, d_id, f"Recover {len(aff)} appointments from {res['name']}", now, now))
        log(conn, iv_id, "DETECTED", "cancerflow", f"{len(aff)} affected appointments")
        if background:
            _bg(evaluate_optimiser, iv_id)
        else:
            evaluate_optimiser(conn, iv_id)
    core.emit({"kind": "ops", "what": "disruption", "id": str(d_id)})
    return {"id": str(d_id), "summary": summary, "intervention_id": str(iv_id)}


def _bg(fn, *args):
    """Run fn(conn, *args) on its own connection after the caller's transaction has committed."""
    def work():
        from backend.db.conn import connect
        for _ in range(40):                               # wait until the triggering request has committed
            with connect() as c:
                if c.execute("SELECT 1 FROM ops_intervention WHERE id=%s", (args[0],)).fetchone():
                    break
            time.sleep(0.25)
        try:
            with connect() as c:
                fn(c, *args)
        except Exception as e:                            # never leave the lifecycle hanging silently
            with connect() as c:
                set_status(c, args[0], "FAILED", "cancerflow", f"evaluation error: {e}")
    threading.Thread(target=work, daemon=True).start()


def evaluate_optimiser(conn, iv_id):
    iv = get(conn, iv_id)
    set_status(conn, iv_id, "PROPOSED", "optimiser", "whole-pathway optimiser proposal requested")
    ev = evaluate.evaluate(conn, iv["disruption_id"])
    ctl = ev.get("controller") or {"level": "global"}
    log(conn, iv_id, "PROPOSED", "inner MPC", "local repair evaluated: " + "; ".join(ctl.get("escalation_reasons", [])))
    if ctl["level"] == "global":
        log(conn, iv_id, "PROPOSED", "outer MPC", "escalated to whole-pathway replanning on the current state")
    plan = ev["plans"].get("optimised" if ctl["level"] == "global" else "no_action", {})
    rows = plan.get("rows", [])
    moved = [r for r in rows if r["transfer"]]
    by_fac = {}
    for r in rows:
        by_fac[r["to"]["facility"]] = by_fac.get(r["to"]["facility"], 0) + 1
    proposal = {"kind": "reschedule", "rows": rows, "by_facility": by_fac, "transfers": len(moved),
                "basis": "optimised" if ctl["level"] == "global" else "local_repair", "controller": ctl,
                "state_version": ev.get("state_version")}
    core.set_setting(conn, "ops_plan_version", ev.get("state_version"))
    set_status(conn, iv_id, "EVALUATED", "optimiser", f"replayed against baseline and local repair (state v{ev.get('state_version')})",
               evaluation=ev, proposal=proposal)
    set_status(conn, iv_id, "AWAITING_APPROVAL", "cancerflow", "needs the regional coordinator's approval")
    o, n = ev["options"].get("optimised", {}).get("kpis", {}), ev["options"].get("no_action", {}).get("kpis", {})
    core.notify(conn, key=f"iv-ready-{iv_id}", audience="admin", severity="warning", kind="proposal",
                title="Recovery plan ready for approval",
                body=f"Simulated: {o.get('pct_within_target')} % of those needing treatment start within 42 days vs "
                     f"{n.get('pct_within_target')} % with no coordination.", payload={"intervention_id": str(iv_id)})


# ------------------------------------------------------------------ administrator proposals
def _resolve_facility(conn, name: str) -> str | None:
    name = (name or "").strip().lower()
    if not name:
        return None
    for fid, short in FAC_SHORT.items():
        if name in short.lower() or name in fid or name == FAC_COUNTY.get(fid, "").lower():
            return fid
    aliases = {"kuopio": "fi-hosp-kys", "north savo": "fi-hosp-kys", "pohjois-savo": "fi-hosp-kys", "kys": "fi-hosp-kys",
               "savonlinna": "fi-hosp-savonlinna", "mikkeli": "fi-hosp-mks", "south savo": "fi-hosp-mks", "etelä-savo": "fi-hosp-mks",
               "jyväskylä": "fi-hosp-nova", "jyvaskyla": "fi-hosp-nova", "central finland": "fi-hosp-nova", "nova": "fi-hosp-nova",
               "joensuu": HOME_FACILITY, "north karelia": HOME_FACILITY, "pkks": HOME_FACILITY}
    for k, v in aliases.items():
        if k in name:
            return v
    return None


def propose(conn, allocations: list | None = None, per_patient: dict | None = None, source: str = "admin",
            actor: str = COORD_USER, note: str = "", background: bool = False) -> dict:
    """allocations: [{"facility": "Kuopio", "share": 0.5} | {"facility": ..., "count": 3}]; per_patient: {patient_id: facility}.
    Shares are split over the affected patients in clinical-priority order (urgent first, earliest deadline)."""
    d = recovery_context(conn)
    if not d:
        raise svc.Conflict("no disruption to recover from (no outage and no recovery plan on record)")
    parent = current(conn)
    base_rows = (parent or {}).get("proposal", {}).get("rows") or []
    aff = evaluate.affected_patients(conn, d["id"])
    order = sorted(aff, key=lambda a: (a["priority"] != "urgent", a["starts_at"], a["patient_id"]))
    alloc, problems = {}, []
    named = {}
    for pid, f in (per_patient or {}).items():                # explicit patients first ("send Tapio to Kuopio")
        fid = f if f in FAC_SHORT else _resolve_facility(conn, f)
        if not fid:
            problems.append(f"unknown hospital '{f}'")
        named[pid] = fid
    order = [a for a in order if a["patient_id"] not in named]
    if not allocations:                                       # everyone else keeps the current proposal
        base = {r["patient_id"]: r["to"]["facility_id"] for r in base_rows}
        for a in order:
            alloc[a["patient_id"]] = base.get(a["patient_id"], HOME_FACILITY)
    else:                                                     # "... and everyone else / half to X"
        parts = []
        for a in allocations:
            fid = _resolve_facility(conn, a.get("facility", ""))
            if not fid:
                problems.append(f"no hospital with endoscopy matches '{a.get('facility')}'")
                continue
            parts.append((fid, a.get("count"), a.get("share")))
        n = len(order)
        counts = [[fid, int(c) if c is not None else None, (float(s) if s is not None else None)] for fid, c, s in parts]
        fixed_total = sum(c for _, c, _ in counts if c is not None)
        shares = [x for x in counts if x[1] is None]
        rest = max(0, n - fixed_total)
        tot_share = sum((x[2] or 0) for x in shares) or (len(shares) or 1)
        raw = [(x, rest * ((x[2] or 1) / tot_share)) for x in shares]
        for x, v in raw:
            x[1] = int(v)
        left = rest - sum(x[1] for x, _ in raw)
        for x, v in sorted(raw, key=lambda t: -(t[1] - int(t[1])))[:left]:
            x[1] += 1
        i = 0
        for fid, c, _ in counts:
            for a in order[i:i + (c or 0)]:
                alloc[a["patient_id"]] = fid
            i += c or 0
        for a in order[i:]:
            alloc[a["patient_id"]] = HOME_FACILITY            # unallocated remainder stays at the home hospital
    alloc.update(named)
    online = {r[0] for r in conn.execute("SELECT DISTINCT facility_id FROM ops_resource WHERE capability='colonoscopy' AND status='online'")}
    for pid, f in alloc.items():
        if f and f not in online:
            problems.append(f"{FAC_SHORT.get(f, f)} has no online endoscopy capacity")
    now = svc.now(conn)
    iv_id = uuid.uuid4()
    title = "Administrator proposal: " + ", ".join(f"{sum(1 for v in alloc.values() if v == f)} → {FAC_SHORT.get(f, f)}"
                                                   for f in sorted(set(alloc.values()), key=str) if f)
    conn.execute("""INSERT INTO ops_intervention(id,disruption_id,parent_id,source,status,title,proposal,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,'PROPOSED',%s,%s::jsonb,%s,%s)""",
                 (iv_id, d["id"], (parent or {}).get("id"), source, title,
                  json.dumps({"kind": "reschedule", "allocation": alloc, "requested": allocations or per_patient, "note": note}), now, now))
    log(conn, iv_id, "PROPOSED", actor, note or title)
    if problems:
        set_status(conn, iv_id, "REJECTED", "cancerflow", "; ".join(sorted(set(problems))),
                   evaluation={"infeasible": sorted(set(problems))})
        return get(conn, iv_id)
    return _evaluate_admin(conn, iv_id, d["id"], alloc, parent)


def _evaluate_admin(conn, iv_id, disruption_id, alloc, parent):
    ev = evaluate.evaluate(conn, disruption_id, admin_alloc=alloc, include=())
    if parent and parent.get("evaluation", {}).get("options"):
        for k in ("baseline", "no_action", "optimised"):
            if k in parent["evaluation"]["options"]:
                ev["options"][k] = parent["evaluation"]["options"][k]
        ev["plans"]["optimised"] = parent["evaluation"].get("plans", {}).get("optimised")
    a = ev["options"]["admin"]["kpis"]
    o = ev["options"].get("optimised", {}).get("kpis", {})
    rows = ev["plans"]["admin"]["rows"]
    load = ev["plans"]["admin"]["load"]
    warnings = []
    for fid, l in load.items():
        if fid != HOME_FACILITY and l["assigned"] > l["free_next_72h"]:
            warnings.append({"kind": "overload", "facility_id": fid,
                             "text": f"{FAC_SHORT.get(fid)} has {l['free_next_72h']} free colonoscopy slots in the next 72 h; "
                                     f"{l['assigned']} patients sent there."})
    for r in rows:
        if r["transfer"] and (r["accept_prob"] or 0) < 0.4:
            warnings.append({"kind": "acceptance", "patient_id": r["patient_id"],
                             "text": f"{r['name']} is unlikely to accept {r['to']['facility']} (p={r['accept_prob']})."})
        if r["transfer"] and (r["travel_min"] or 0) > 240:
            warnings.append({"kind": "travel", "patient_id": r["patient_id"],
                             "text": f"{r['name']}: {round(r['travel_min'])} min travel exceeds the 240-min limit."})
    unplaced = [r for r in rows if not r["to"]["slot_id"]]
    infeasible = [f"{r['name']}: no free slot at {r['to']['facility']} within the planning horizon" for r in unplaced]
    worse = (a.get("pct_urgent_within_target") or 0) < (o.get("pct_urgent_within_target") or 0) - 1.0 or \
            (a.get("pct_within_target") or 0) < (o.get("pct_within_target") or 0) - 1.0 or \
            (a.get("mean_colonoscopy_day") or 0) > (o.get("mean_colonoscopy_day") or 0) + 1.0
    verdict = "worse_than_recommendation" if worse else ("comparable_with_risks" if warnings else "as_good_or_better")
    ev["review"] = {"verdict": verdict, "warnings": warnings, "infeasible": infeasible,
                    "compare": {"admin": a, "optimised": o}}
    by_fac = {}
    for r in rows:
        by_fac[r["to"]["facility"]] = by_fac.get(r["to"]["facility"], 0) + 1
    iv = get(conn, iv_id)
    proposal = {**iv["proposal"], "rows": rows, "by_facility": by_fac, "transfers": sum(1 for r in rows if r["transfer"]),
                "basis": "admin"}
    set_status(conn, iv_id, "EVALUATED", "cancerflow", f"verdict: {verdict}", evaluation=ev, proposal=proposal)
    if infeasible:
        set_status(conn, iv_id, "REJECTED", "cancerflow", "infeasible: " + "; ".join(infeasible))
    else:
        set_status(conn, iv_id, "AWAITING_APPROVAL", "cancerflow",
                   {"worse_than_recommendation": "feasible, but CancerFlow's recommendation is better",
                    "comparable_with_risks": "feasible and comparable on target, with capacity / acceptance risks"}.get(verdict, "feasible"))
    return get(conn, iv_id)


# ------------------------------------------------------------------ approval + execution
def reject(conn, iv_id, actor=COORD_USER, justification=""):
    iv = get(conn, iv_id)
    if iv["status"] in ("APPLIED", "REJECTED", "FAILED"):
        return iv
    set_status(conn, iv_id, "REJECTED", actor, justification or "rejected by administrator", decided_by=actor,
               justification=justification)
    return get(conn, iv_id)


def approve(conn, iv_id, actor=COORD_USER, justification="", background_agents: bool = True) -> dict:
    iv = get(conn, iv_id)
    if iv["status"] == "APPLIED":
        return iv                                             # idempotent: a second approval changes nothing
    if iv["status"] != "AWAITING_APPROVAL":
        raise svc.Conflict(f"intervention is {iv['status']}; only AWAITING_APPROVAL can be approved")
    if is_stale(conn, iv):                               # checked BEFORE any write; approve_checked() recomputes
        raise StalePlan("the bookable state changed since this plan was computed")
    set_status(conn, iv_id, "APPROVED", actor, justification or "approved", decided_by=actor, justification=justification)
    try:
        offers = _apply(conn, iv)
    except Exception as e:
        set_status(conn, iv_id, "FAILED", "cancerflow", f"apply failed: {e}")
        raise
    conn.execute("UPDATE ops_intervention SET applied_at=%s WHERE id=%s", (svc.now(conn), iv_id))
    set_status(conn, iv_id, "APPLIED", "cancerflow", f"{len(offers)} appointments moved; patients and receiving units notified")
    for other in conn.execute("SELECT id FROM ops_intervention WHERE disruption_id=%s AND id<>%s AND status = ANY(%s)",
                              (iv["disruption_id"], iv_id, ["PROPOSED", "EVALUATED", "AWAITING_APPROVAL"])).fetchall():
        set_status(conn, other[0], "SUPERSEDED", "cancerflow", "another proposal was approved")
    if background_agents:
        _bg(agent_round, iv_id)
    else:
        agent_round(conn, iv_id)
    return get(conn, iv_id)


def _move(conn, iv_id, r, tag: str, actor=ENDO_USER) -> dict | None:
    """Cancel the patient's booked appointment and book the target slot. Deterministic ids -> idempotent."""
    old = r["appointment_id"]
    new_id = did("appt", iv_id, r["patient_id"], tag)
    if conn.execute("SELECT 1 FROM appointment WHERE id=%s", (new_id,)).fetchone():
        return None
    slot = None
    if r["to"].get("slot_id"):
        slot = conn.cursor(row_factory=dict_row).execute(
            "SELECT id, starts_at, resource_id FROM ops_slot WHERE id=%s AND status='free'", (r["to"]["slot_id"],)).fetchone()
    if not slot:                                              # taken meanwhile: next free slot at the same hospital
        slot = conn.cursor(row_factory=dict_row).execute(
            """SELECT s.id, s.starts_at, s.resource_id FROM ops_slot s JOIN ops_resource x ON x.id=s.resource_id
               WHERE x.facility_id=%s AND x.capability='colonoscopy' AND s.status='free' AND s.starts_at >= %s
               ORDER BY s.starts_at LIMIT 1""", (r["to"]["facility_id"], svc.now(conn))).fetchone()
    if not slot:
        raise svc.Conflict(f"no free slot left for {r['name']} at {r['to']['facility']}")
    pw = r["pathway_id"]
    if conn.execute("SELECT status FROM appointment WHERE id=%s", (old,)).fetchone()[0] == "booked":
        core.post(conn, pw, {"id": did("cancel", iv_id, old), "type": "APPOINTMENT_STATUS",
                             "payload": {"appointment_id": old, "status": "cancelled", "reason": "resource offline (disruption)",
                                         "intervention_id": str(iv_id)}}, actor)
        core.release_slot_of(conn, old)
    core.post(conn, pw, {"id": did("book", new_id), "type": "APPOINTMENT_BOOKED",
                         "payload": {"appointment_id": str(new_id), "stage": "COLONOSCOPY", "scheduled_for": slot["starts_at"].isoformat(),
                                     "facility_id": r["to"]["facility_id"], "resource_id": slot["resource_id"],
                                     "intervention_id": str(iv_id), "replaces": old}}, actor)
    core.book_slot(conn, slot["id"], pw, new_id, f"intervention:{iv_id}")
    return {"appointment_id": str(new_id), "at": slot["starts_at"], "slot_id": str(slot["id"])}


def _apply(conn, iv) -> list[dict]:
    iv_id = iv["id"]
    offers = []
    for r in iv["proposal"].get("rows", []):
        m = _move(conn, iv_id, r, "main")
        if not m:
            continue
        when = core.fmt_time(m["at"])
        pid = r["patient_id"]
        transfer = r["to"]["facility_id"] != HOME_FACILITY
        core.notify(conn, key=f"iv-pat-{iv_id}-{pid}", audience="patient", patient_id=pid, pathway_id=r["pathway_id"],
                    severity="warning", kind="rescheduled", title="Your colonoscopy has been rescheduled",
                    body=(f"An endoscopy unit in Joensuu is out of service. Your new appointment: {r['to']['facility']}, {when}."
                          + (f" Travel about {round(r['travel_min'] or 0)} min; travel costs are reimbursed (Kela)." if transfer else "")
                          + " Your place in the pathway and your priority are unchanged. Please confirm."),
                    payload={"appointment_id": m["appointment_id"], "needs_confirmation": True, "intervention_id": iv_id})
        if transfer:
            core.notify(conn, key=f"iv-fac-{iv_id}-{pid}", audience="facility", facility_id=r["to"]["facility_id"],
                        pathway_id=r["pathway_id"], kind="incoming",
                        title=f"Incoming patient from North Karelia: {r['name']} ({r['priority']})",
                        body=f"Colonoscopy {when}. Referral, history and pathway clock are shared through CancerFlow.",
                        payload={"intervention_id": iv_id})
        offers.append({**r, "new": m, "when": when})
    core.notify(conn, key=f"iv-done-{iv_id}", audience="admin", kind="applied", title="Recovery plan applied",
                body=f"{len(offers)} appointments moved; {sum(1 for o in offers if o['transfer'])} to other hospitals. Waiting for patient replies.",
                payload={"intervention_id": iv_id})
    st = core.get_setting(conn, "ops_story", {})
    conn.execute("UPDATE ops_intervention SET proposal = proposal || %s::jsonb WHERE id=%s",
                 (json.dumps({"applied": [{"patient_id": o["patient_id"], "appointment_id": o["new"]["appointment_id"],
                                           "at": o["new"]["at"].isoformat(), "facility_id": o["to"]["facility_id"]} for o in offers],
                              "emma_patient": st.get("emma_patient")}), iv_id))
    return offers


def agent_round(conn, iv_id):
    """Every moved patient (except Emma, who is the presenter) answers. Declines/counters feed back into the optimiser."""
    conn.execute("SELECT pg_advisory_xact_lock(4242)")   # serialised with story reset (no deadlock on agent memory)
    for _ in range(60):                                   # wait until the approval transaction is visible (committed)
        iv = get(conn, iv_id)
        if iv["status"] == "APPLIED" and iv["proposal"].get("applied") is not None:
            break
        time.sleep(0.25)
    else:
        return
    emma = iv["proposal"].get("emma_patient")
    replies, avoid, earliest = [], {}, {}
    t0 = core.t0(conn)
    for a in iv["proposal"].get("applied", []):
        if a["patient_id"] == emma:
            continue
        row = next(r for r in iv["proposal"]["rows"] if r["patient_id"] == a["patient_id"])
        from datetime import datetime
        at = datetime.fromisoformat(a["at"])
        offer = {"facility_id": a["facility_id"], "facility": FAC_SHORT.get(a["facility_id"]), "when": core.fmt_time(at),
                 "story_day": int((at - t0).total_seconds() // 86400), "travel_min": row.get("travel_min"),
                 "transfer": a["facility_id"] != HOME_FACILITY}
        resp = agents.respond(conn, a["patient_id"], offer, key=f"{iv_id}|{a['patient_id']}")
        replies.append(resp)
        core.notify(conn, key=f"reply-{iv_id}-{a['patient_id']}", audience="admin", kind="patient_reply",
                    severity="info" if resp["decision"] == "accept" else "warning",
                    title=f"{resp['name']}: {resp['decision']}", body=resp["message"], patient_id=a["patient_id"],
                    pathway_id=row["pathway_id"], payload={"intervention_id": iv_id, "decision": resp["decision"], "llm": resp["llm"],
                                                          "reason": resp.get("reason")})
        if resp["decision"] == "accept":
            conn.execute("UPDATE appointment SET confirmed_at=%s WHERE id=%s", (svc.now(conn), a["appointment_id"]))
        else:
            if resp["decision"] == "decline":
                avoid[a["patient_id"]] = [a["facility_id"]]
            if resp.get("request", {}).get("earliest_story_day") is not None:
                earliest[a["patient_id"]] = resp["request"]["earliest_story_day"]
        time.sleep(0.4)                                       # replies arrive one by one in the UI
    conn.execute("UPDATE ops_intervention SET proposal = proposal || %s::jsonb WHERE id=%s",
                 (json.dumps({"replies": replies}, default=str), iv_id))
    core.emit({"kind": "ops", "what": "replies", "id": str(iv_id)})
    if avoid or earliest:
        replan(conn, iv, avoid, earliest)


def replan(conn, parent, avoid: dict, earliest: dict):
    """Patients who declined or asked for another time: re-optimise THEIR remaining pathway (others stay booked).
    Their current booking stays in place until a human approves the new proposal."""
    iv_id = did("iv-replan", parent["id"])
    if conn.execute("SELECT 1 FROM ops_intervention WHERE id=%s", (iv_id,)).fetchone():
        return
    now = svc.now(conn)
    names = [r["name"] for r in parent["proposal"]["rows"] if r["patient_id"] in set(avoid) | set(earliest)]
    conn.execute("""INSERT INTO ops_intervention(id,disruption_id,parent_id,source,status,title,created_at,updated_at)
                    VALUES (%s,%s,%s,'replan','DETECTED',%s,%s,%s)""",
                 (iv_id, parent["disruption_id"], parent["id"], f"Re-plan after patient replies: {', '.join(names)}", now, now))
    log(conn, iv_id, "DETECTED", "patient agents", f"{len(avoid)} declined, {len(earliest)} asked for another time")
    # evaluate on the patients' CURRENT bookings (the applied plan) with their stated constraints
    applied = {a["patient_id"]: a for a in parent["proposal"].get("applied", [])}
    ev = evaluate.evaluate_subset(conn, names_ids(parent, avoid, earliest), applied, avoid, earliest)
    set_status(conn, iv_id, "EVALUATED", "optimiser", "remaining pathway re-optimised for the patients who replied", evaluation=ev,
               proposal={"kind": "reschedule", "rows": ev["rows"], "basis": "replan", "transfers": sum(1 for r in ev["rows"] if r["transfer"]),
                         "by_facility": ev["by_facility"]})
    set_status(conn, iv_id, "AWAITING_APPROVAL", "cancerflow", "needs approval")


def names_ids(parent, avoid, earliest):
    return [r["patient_id"] for r in parent["proposal"]["rows"] if r["patient_id"] in set(avoid) | set(earliest)]
