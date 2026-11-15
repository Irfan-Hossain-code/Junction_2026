"""Continuity of care: a missed appointment never silently closes or abandons a pathway.

record_missed -> the unresolved step is detected, the responsible care team is alerted, replacement slots are
evaluated (clinical priority preserved, home hospital first, then the nearest with capacity), and a follow-up
proposal waits for CLINICIAN authorisation. The patient is contacted; booking is then confirmed by the patient.
"""
from __future__ import annotations

import json
from datetime import timedelta

from psycopg.rows import dict_row

from backend.ops import core, interventions
from backend.ops.core import ENDO_USER, did
from backend.ops.world import FAC_SHORT, HOME_FACILITY
from backend.services import pathways as svc


def record_missed(conn, appointment_id) -> str:
    a = conn.cursor(row_factory=dict_row).execute(
        """SELECT a.*, p.patient_id, pt.display_name AS name, ag.persona FROM appointment a JOIN pathway p ON p.id=a.pathway_id
           JOIN patient pt ON pt.id=p.patient_id LEFT JOIN ops_patient_agent ag ON ag.patient_id=p.patient_id WHERE a.id=%s""",
        (appointment_id,)).fetchone()
    if not a:
        raise svc.NotFound("appointment")
    if a["status"] == "booked":
        core.post(conn, a["pathway_id"], {"id": did("missed", appointment_id), "type": "APPOINTMENT_STATUS",
                                          "payload": {"appointment_id": str(appointment_id), "status": "missed"}}, ENDO_USER)
    ref = conn.execute("""SELECT payload FROM event WHERE pathway_id=%s AND stage='REFERRAL_SENT' ORDER BY seq LIMIT 1""",
                       (a["pathway_id"],)).fetchone()
    priority = (ref[0] if ref else {}).get("priority", "standard")
    now = svc.now(conn)
    # replacement options: earliest free slot at the home hospital, plus the two earliest elsewhere (within travel limit)
    per = a["persona"] or {}
    opts = []
    for fid in [HOME_FACILITY] + [f for f in FAC_SHORT if f != HOME_FACILITY]:
        s = conn.cursor(row_factory=dict_row).execute(
            """SELECT s.id, s.starts_at, s.resource_id FROM ops_slot s JOIN ops_resource r ON r.id=s.resource_id
               WHERE r.facility_id=%s AND r.capability='colonoscopy' AND s.status='free' AND s.starts_at >= %s
               ORDER BY s.starts_at LIMIT 1""", (fid, now + timedelta(hours=18))).fetchone()
        if s:
            tm = (per.get("travel_min") or {}).get(fid)
            if fid != HOME_FACILITY and tm and tm > (per.get("max_travel_min") or 240):
                continue
            opts.append({"facility_id": fid, "facility": FAC_SHORT[fid], "slot_id": str(s["id"]), "resource_id": s["resource_id"],
                         "at": s["starts_at"].isoformat(), "when": core.fmt_time(s["starts_at"]), "travel_min": tm,
                         "accept_prob": (per.get("accept_prob") or {}).get(fid, 1.0)})
    # earliest acceptable slot first (urgent priority kept); the home hospital wins only if within 2 days of the earliest
    opts = [o for o in opts if o["facility_id"] == HOME_FACILITY or (o["accept_prob"] or 0) >= 0.4]
    opts.sort(key=lambda o: o["at"])
    if opts:
        first = opts[0]["at"]
        from datetime import datetime as _dt
        home = next((o for o in opts if o["facility_id"] == HOME_FACILITY), None)
        if home and (_dt.fromisoformat(home["at"]) - _dt.fromisoformat(first)).total_seconds() <= 2 * 86400:
            opts.remove(home); opts.insert(0, home)
    opts = opts[:3]
    iv_id = did("iv-followup", appointment_id)
    if not conn.execute("SELECT 1 FROM ops_intervention WHERE id=%s", (iv_id,)).fetchone():
        conn.execute("""INSERT INTO ops_intervention(id,source,status,title,proposal,created_at,updated_at)
                        VALUES (%s,'followup','DETECTED',%s,%s::jsonb,%s,%s)""",
                     (iv_id, f"Follow-up: {a['name']} missed a colonoscopy ({priority})",
                      json.dumps({"kind": "followup", "pathway_id": str(a["pathway_id"]), "patient_id": a["patient_id"],
                                  "name": a["name"], "priority": priority, "missed_appointment_id": str(appointment_id),
                                  "options": opts, "responsible": "Endoscopy unit, North Karelia Central Hospital"}), now, now))
        interventions.log(conn, iv_id, "DETECTED", "cancerflow", "appointment missed; pathway step unresolved")
        interventions.set_status(conn, iv_id, "AWAITING_APPROVAL", "cancerflow",
                                 "replacement slots evaluated; needs clinician authorisation and patient consent")
    core.notify(conn, key=f"miss-team-{appointment_id}", audience="care_team", facility_id=HOME_FACILITY, pathway_id=a["pathway_id"],
                patient_id=a["patient_id"], severity="critical", kind="missed",
                title=f"{a['name']} did not attend her colonoscopy ({priority} pathway)" if (per.get("sex") == "F") else
                      f"{a['name']} did not attend the colonoscopy ({priority} pathway)",
                body=f"Pathway is NOT closed. Clinical priority kept: {priority}. {len(opts)} replacement slots held for review. "
                     "Please contact the patient and authorise a new booking.",
                payload={"intervention_id": str(iv_id)})
    core.notify(conn, key=f"miss-pat-{appointment_id}", audience="patient", patient_id=a["patient_id"], pathway_id=a["pathway_id"],
                severity="warning", kind="missed", title="We missed you today",
                body="Your colonoscopy didn't take place. That's okay: your care team will call you to find a new time that works. "
                     "Your referral stays active.")
    return f"{a['name']} missed the appointment: care team alerted, {len(opts)} replacement slots evaluated (awaiting clinician)."


def authorise(conn, iv_id, option: int, user_id: str) -> dict:
    iv = interventions.get(conn, iv_id)
    if iv["status"] == "APPLIED":
        return iv
    if iv["status"] != "AWAITING_APPROVAL":
        raise svc.Conflict(f"follow-up is {iv['status']}")
    pr = iv["proposal"]
    o = pr["options"][option]
    interventions.set_status(conn, iv_id, "APPROVED", user_id, f"clinician authorised {o['facility']} {o['when']}", decided_by=user_id)
    new_id = did("appt", iv_id, "followup")
    row = {"patient_id": pr["patient_id"], "name": pr["name"], "pathway_id": pr["pathway_id"],
           "appointment_id": pr["missed_appointment_id"], "to": {"facility_id": o["facility_id"], "facility": o["facility"],
                                                                 "slot_id": o["slot_id"]}}
    m = interventions._move(conn, iv_id, row, "followup", actor=user_id)
    when = core.fmt_time(m["at"]) if m else o["when"]
    core.notify(conn, key=f"fu-pat-{iv_id}", audience="patient", patient_id=pr["patient_id"], pathway_id=pr["pathway_id"],
                severity="warning", kind="rescheduled", title="New colonoscopy time",
                body=f"{o['facility']}, {when}. Your priority is unchanged. Please confirm, or reply if you need help getting there.",
                payload={"appointment_id": (m or {}).get("appointment_id", str(new_id)), "needs_confirmation": True})
    interventions.set_status(conn, iv_id, "APPLIED", "cancerflow", f"rebooked {o['facility']} {when}; patient notified")
    return interventions.get(conn, iv_id)


def authorise_first(conn, pathway_id, user_id: str) -> str:
    r = conn.execute("""SELECT id FROM ops_intervention WHERE source='followup' AND status='AWAITING_APPROVAL'
                        AND proposal->>'pathway_id'=%s ORDER BY created_at DESC LIMIT 1""", (str(pathway_id),)).fetchone()
    if not r:
        return "No follow-up awaiting authorisation."
    iv = authorise(conn, r[0], 0, user_id)
    o = iv["proposal"]["options"][0]
    return f"Clinician authorised a new slot: {o['facility']}, {o['when']}; Emma notified."
