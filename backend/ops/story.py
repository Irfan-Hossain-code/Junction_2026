"""Emma's scripted-but-real storyline. Every step posts real care events through the same validated path as the
care app (append-only log, pack transitions), so the care app, the twin and the voice agent all see one state.

Reset is non-destructive for everyone else: earlier demo runs are closed with a WITHDRAWN event (the log is
append-only), ops tables are rebuilt, and new patient ids get a run suffix.
"""
from __future__ import annotations

import json
from datetime import timedelta

from psycopg.rows import dict_row

from backend.ops import core
from backend.ops.core import COORD_USER, ENDO_USER, GP_USER, NURSE_USER, SURGEON_USER, did
from backend.ops.world import (DAYS, DISRUPTED_RESOURCE, FAC_COORD, GP_FACILITY, HOME_FACILITY, PERSONAS, RESOURCES, FI,
                               accept_prob, background_mask, slot_time, travel_min)
from backend.services import pathways as svc

ALT_FACILITIES = ["fi-hosp-kys", "fi-hosp-savonlinna", "fi-hosp-mks", "fi-hosp-nova"]


# ------------------------------------------------------------------ state
def state(conn) -> dict:
    return core.get_setting(conn, "ops_story", {}) or {}


def save(conn, st: dict) -> None:
    core.set_setting(conn, "ops_story", st)


def emma_pathway(conn):
    return state(conn).get("emma_pathway")


def emma_patient(conn):
    return state(conn).get("emma_patient")


def persona_of(conn, patient_id: str) -> dict:
    r = conn.execute("SELECT persona FROM ops_patient_agent WHERE patient_id=%s", (patient_id,)).fetchone()
    return r[0] if r else {}


# ------------------------------------------------------------------ reset / seed
def _ensure_users(conn, emma_pid: str) -> None:
    users = [(GP_USER, "doctor", "Dr. Anna Kettunen (GP, Niinivaara)", None, GP_FACILITY, "general_practitioner"),
             (ENDO_USER, "doctor", "Dr. Ville Hakkarainen (gastroenterology)", None, HOME_FACILITY, "endoscopist"),
             (COORD_USER, "doctor", "Donny Lehtonen (regional care coordinator)", None, HOME_FACILITY, "administrator"),
             ("pat-emma", "patient", "Emma Nieminen", emma_pid, None, None)]
    for u in users:
        conn.execute("""INSERT INTO app_user(id,role,display_name,patient_id,facility_id,staff_role) VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO UPDATE SET display_name=EXCLUDED.display_name, patient_id=EXCLUDED.patient_id,
                        facility_id=EXCLUDED.facility_id, staff_role=EXCLUDED.staff_role""", u)


def _close_previous_runs(conn) -> int:
    rows = conn.execute("SELECT id FROM pathway WHERE status='active' AND patient_id LIKE 'OPS-%%'").fetchall()
    for (pw,) in rows:
        try:
            core.post(conn, pw, {"id": did("withdraw", pw), "type": "STAGE_ENTERED", "stage": "WITHDRAWN",
                                 "occurred_at": svc.now(conn), "payload": {"reason": "demo reset (synthetic)"}}, COORD_USER)
        except svc.Conflict:
            pass
    return len(rows)


def _muni_id(conn, name: str):
    r = conn.execute("SELECT id FROM region WHERE kind='municipality' AND name_fi=%s", (name,)).fetchone()
    return r[0] if r else None


def reset(conn) -> dict:
    st_old = state(conn)
    run = int(st_old.get("run", 0)) + 1
    # anchor story day 0 at today's midnight (Finnish time), clock at 08:30 on day 0
    svc.set_clock_offset(conn, 0)                        # earlier runs moved the clock forward
    real_now = svc.now(conn).astimezone(FI)
    t0 = real_now.replace(hour=0, minute=0, second=0, microsecond=0)
    core.set_setting(conn, "ops_t0", t0.isoformat())
    closed = _close_previous_runs(conn) if run > 1 else 0
    core.set_story_clock(conn, 0, 8.5)

    for t in ("ops_notification", "ops_intervention_log", "ops_intervention", "ops_disruption", "ops_slot", "ops_resource"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM ops_patient_agent WHERE patient_id LIKE 'OPS-%%'")

    # resources + slots with background occupancy (SYNTHETIC)
    rows = []
    for rid, fid, cap, name, per_day, plan in RESOURCES:
        conn.execute("INSERT INTO ops_resource(id,facility_id,capability,name,slots_per_day) VALUES (%s,%s,%s,%s,%s)",
                     (rid, fid, cap, name, per_day))
        mask = background_mask(rid, per_day, plan)
        for d in range(DAYS):
            for i in range(per_day):
                rows.append((did("slot", run, rid, d, i), rid, slot_time(t0, d, i, per_day), "booked" if mask[d, i] else "free",
                             "background" if mask[d, i] else None))
    with conn.cursor() as cur, cur.copy("COPY ops_slot (id,resource_id,starts_at,status,booked_by) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)

    # patients + persona agents
    sfx = f"-R{run}"
    emma_pid = None
    cohort = []
    for p in PERSONAS:
        pid = p["pid"] + sfx
        svc.create_patient(conn, p["name"], p["birth_year"], p["sex"], _muni_id(conn, p["muni"]), pid,
                           lon=p["lonlat"][0], lat=p["lonlat"][1])
        persona = {**p, "patient_id": pid,
                   "travel_min": {f: travel_min(p, f) for f in FAC_COORD},
                   "accept_prob": {f: accept_prob(p, f) for f in FAC_COORD}}
        conn.execute("INSERT INTO ops_patient_agent(patient_id,persona,memory) VALUES (%s,%s::jsonb,'[]'::jsonb)",
                     (pid, json.dumps(persona, ensure_ascii=False)))
        if p["pid"] == "OPS-EMMA":
            emma_pid = pid
        else:
            cohort.append((pid, p))
    _ensure_users(conn, emma_pid)

    # the seven cohort pathways: referred earlier, specialist seen, colonoscopy booked on Unit 2 (days 6-7)
    for pid, p in cohort:
        sent = t0 + timedelta(days=6 - p["referral_days_ago"], hours=8)
        pw = did("pathway", pid)
        svc.create_pathway(conn, pid, "colorectal", "symptomatic", core.actor(conn, GP_USER), opened_at=sent - timedelta(days=2),
                           facility_id=GP_FACILITY, pathway_id=pw)
        seq = [("GP_VISIT", sent - timedelta(hours=1), GP_USER, GP_FACILITY, {}),
               ("REFERRAL_SENT", sent, GP_USER, GP_FACILITY, {"priority": p["priority"], "to_facility_id": HOME_FACILITY,
                                                              "reason": "suspected colorectal cancer (symptoms)"}),
               ("REFERRAL_RECEIVED", sent + timedelta(hours=6), NURSE_USER, HOME_FACILITY, {"priority": p["priority"]}),
               ("SPECIALIST_ASSESSMENT", min(sent + timedelta(days=1, hours=4), t0 - timedelta(hours=2)), ENDO_USER, HOME_FACILITY,
                {"requested": "colonoscopy"})]
        for stage, at, u, fac, pl in seq:
            core.post(conn, pw, {"id": did("ev", pid, stage), "type": "STAGE_ENTERED", "stage": stage, "occurred_at": at,
                                 "facility_id": fac, "payload": pl}, u)
        day, idx = p["slot"]
        _book_on_slot(conn, pw, "COLONOSCOPY", DISRUPTED_RESOURCE, slot_time(t0, day, idx, 4), did("appt", pid, "colo"),
                      ENDO_USER, booked_by="story", confirmed=True, run=run)

    st = {"run": run, "emma_patient": emma_pid, "emma_pathway": None, "done": [], "t0": t0.isoformat(),
          "cohort": [pid for pid, _ in cohort], "closed_previous": closed}
    save(conn, st)
    core.emit({"kind": "ops", "what": "reset"})
    return st


def _book_on_slot(conn, pathway_id, stage, resource_id, at, appt_id, user, booked_by, confirmed=False, run=None):
    slot = conn.execute("SELECT id, status FROM ops_slot WHERE resource_id=%s AND starts_at=%s", (resource_id, at)).fetchone()
    fac = conn.execute("SELECT facility_id FROM ops_resource WHERE id=%s", (resource_id,)).fetchone()[0]
    if slot and slot[1] == "booked":
        conn.execute("UPDATE ops_slot SET status='free', booked_by=NULL WHERE id=%s AND pathway_id IS NULL", (slot[0],))
    core.post(conn, pathway_id, {"id": did("book", appt_id), "type": "APPOINTMENT_BOOKED", "occurred_at": svc.now(conn),
                                 "payload": {"appointment_id": str(appt_id), "stage": stage, "scheduled_for": at.isoformat(),
                                             "facility_id": fac, "resource_id": resource_id}}, user)
    if slot:
        core.book_slot(conn, slot[0], pathway_id, appt_id, booked_by)
    if confirmed:
        conn.execute("UPDATE appointment SET confirmed_at=%s WHERE id=%s", (svc.now(conn), appt_id))


# ------------------------------------------------------------------ steps
def _emma_appt(conn, stage: str, status: str = "booked"):
    return conn.cursor(row_factory=dict_row).execute(
        """SELECT a.*, s.resource_id FROM appointment a LEFT JOIN ops_slot s ON s.appointment_id=a.id
           WHERE a.pathway_id=%s AND a.stage=%s AND a.status=%s ORDER BY a.scheduled_for DESC LIMIT 1""",
        (emma_pathway(conn), stage, status)).fetchone()


def _stage(conn, stage, at, user, fac=None, payload=None):
    pw = emma_pathway(conn)
    core.post(conn, pw, {"id": did("emma", state(conn)["run"], stage), "type": "STAGE_ENTERED", "stage": stage,
                         "occurred_at": at, "facility_id": fac, "payload": payload or {}}, user)


def _attend(conn, appt, user):
    core.post(conn, emma_pathway(conn), {"id": did("attend", appt["id"]), "type": "APPOINTMENT_STATUS",
                                         "occurred_at": svc.now(conn),
                                         "payload": {"appointment_id": str(appt["id"]), "status": "attended"}}, user)


def step_book_gp(conn):
    st = state(conn)
    now = core.set_story_clock(conn, 0, 9.0)
    pw = did("pathway", st["emma_patient"])
    if not st.get("emma_pathway"):
        svc.create_pathway(conn, st["emma_patient"], "colorectal", "symptomatic", core.actor(conn, "pat-emma"),
                           opened_at=now, facility_id=GP_FACILITY, pathway_id=pw)
        st["emma_pathway"] = str(pw)
        save(conn, st)
        core.emit({"kind": "pathway_created", "pathway_id": str(pw)})
    at = core.story_time(conn, 1, 10.0)
    core.post(conn, pw, {"id": did("emma-gp-book", st["run"]), "type": "APPOINTMENT_BOOKED", "occurred_at": now,
                         "payload": {"appointment_id": str(did("emma-gp", st["run"])), "stage": "GP_VISIT",
                                     "scheduled_for": at.isoformat(), "facility_id": GP_FACILITY,
                                     "note": "rectal bleeding for 3 weeks, tiredness"}}, "pat-emma")
    conn.execute("UPDATE appointment SET confirmed_at=%s WHERE id=%s", (now, did("emma-gp", st["run"])))
    core.notify(conn, key=f"emma-gp-{st['run']}", audience="patient", patient_id=st["emma_patient"], pathway_id=pw,
                kind="booking", title="GP appointment booked",
                body=f"Niinivaara health station, {core.fmt_time(at)}. Bring a list of your symptoms.")
    return f"Emma reported symptoms and booked a GP appointment for {core.fmt_time(at)}."


def step_attend_gp(conn):
    st = state(conn)
    now = core.set_story_clock(conn, 1, 10.5)
    appt = _emma_appt(conn, "GP_VISIT")
    if appt:
        _attend(conn, appt, GP_USER)
    _stage(conn, "GP_VISIT", now - timedelta(minutes=20), GP_USER, GP_FACILITY)
    _stage(conn, "REFERRAL_SENT", now, GP_USER, GP_FACILITY,
           {"priority": "urgent", "to_facility_id": HOME_FACILITY,
            "reason": "rectal bleeding + change in bowel habit + iron-deficiency anaemia: suspected colorectal cancer",
            "priority_set_by": "GP (clinical criteria)"})
    core.notify(conn, key=f"emma-ref-{st['run']}", audience="patient", patient_id=st["emma_patient"], pathway_id=st["emma_pathway"],
                kind="referral", title="You have been referred to North Karelia Central Hospital",
                body="Your GP sent an urgent referral. CancerFlow will track it until the hospital books you in.")
    core.notify(conn, key=f"emma-ref-fac-{st['run']}", audience="facility", facility_id=HOME_FACILITY, pathway_id=st["emma_pathway"],
                kind="referral", title="Incoming urgent referral: Emma Nieminen", body="Suspected colorectal cancer. Priority set by GP: urgent.")
    return "GP visit done. Urgent referral sent to North Karelia Central Hospital; CancerFlow now monitors it."


def step_referral_received(conn):
    st = state(conn)
    now = core.set_story_clock(conn, 2, 9.0)
    _stage(conn, "REFERRAL_RECEIVED", now, NURSE_USER, HOME_FACILITY, {"priority": "urgent"})
    at = core.story_time(conn, 4, 13.0)
    aid = did("emma-spec", st["run"])
    core.post(conn, st["emma_pathway"], {"id": did("book", aid), "type": "APPOINTMENT_BOOKED", "occurred_at": now,
                                         "payload": {"appointment_id": str(aid), "stage": "SPECIALIST_ASSESSMENT",
                                                     "scheduled_for": at.isoformat(), "facility_id": HOME_FACILITY}}, NURSE_USER)
    core.notify(conn, key=f"emma-spec-{st['run']}", audience="patient", patient_id=st["emma_patient"], pathway_id=st["emma_pathway"],
                kind="booking", title="Specialist appointment booked",
                body=f"Gastroenterology, North Karelia Central Hospital, {core.fmt_time(at)}. Please confirm you can attend.",
                payload={"appointment_id": str(aid), "needs_confirmation": True})
    return f"Hospital received the referral and booked a specialist appointment ({core.fmt_time(at)})."


def step_reminder(conn):
    st = state(conn)
    core.set_story_clock(conn, 3, 18.0)
    appt = _emma_appt(conn, "SPECIALIST_ASSESSMENT")
    if appt and not appt["confirmed_at"]:
        core.notify(conn, key=f"emma-spec-rem-{st['run']}", audience="patient", patient_id=st["emma_patient"],
                    pathway_id=st["emma_pathway"], kind="reminder", severity="warning",
                    title="Reminder: please confirm tomorrow's appointment",
                    body=f"Specialist assessment {core.fmt_time(appt['scheduled_for'])}. Tap Confirm, or tell us if you need another time.",
                    payload={"appointment_id": str(appt["id"]), "needs_confirmation": True})
        return "Emma hasn't confirmed: reminder sent to her phone."
    return "Appointment already confirmed; no reminder needed."


def confirm_appointment(conn, appointment_id, user_id: str) -> dict:
    row = conn.execute("SELECT pathway_id, confirmed_at, stage, scheduled_for FROM appointment WHERE id=%s", (appointment_id,)).fetchone()
    if not row:
        raise svc.NotFound("appointment")
    if not row[1]:
        conn.execute("UPDATE appointment SET confirmed_at=%s WHERE id=%s", (svc.now(conn), appointment_id))
        core.post(conn, row[0], {"id": did("confirm", appointment_id), "type": "NOTE",
                                 "payload": {"text": f"Patient confirmed attendance ({row[2]}, {core.fmt_time(row[3])})",
                                             "kind": "appointment_confirmed", "appointment_id": str(appointment_id)}}, user_id)
        conn.execute("UPDATE ops_notification SET read_at=now() WHERE payload->>'appointment_id'=%s", (str(appointment_id),))
        _remember(conn, row[0], f"confirmed {row[2]} on {core.fmt_time(row[3])}")
    return {"confirmed": True, "appointment_id": str(appointment_id)}


def _remember(conn, pathway_id, text: str) -> None:
    conn.execute("""UPDATE ops_patient_agent a SET memory = (a.memory || %s::jsonb)
                    FROM pathway p WHERE p.id=%s AND p.patient_id=a.patient_id""",
                 (json.dumps([{"at": svc.now(conn).isoformat(), "text": text}]), pathway_id))


def step_confirm_specialist(conn):
    appt = _emma_appt(conn, "SPECIALIST_ASSESSMENT")
    if not appt:
        return "No specialist appointment to confirm."
    confirm_appointment(conn, appt["id"], "pat-emma")
    return "Emma confirmed the specialist appointment (normally done by tapping Confirm in her app)."


def step_attend_specialist(conn):
    st = state(conn)
    now = core.set_story_clock(conn, 4, 13.5)
    appt = _emma_appt(conn, "SPECIALIST_ASSESSMENT")
    if appt:
        _attend(conn, appt, ENDO_USER)
    _stage(conn, "SPECIALIST_ASSESSMENT", now, ENDO_USER, HOME_FACILITY,
           {"requested": "colonoscopy with biopsy within 3 days", "priority": "urgent"})
    at = slot_time(core.t0(conn), 7, 0, 4)
    aid = did("emma-colo", st["run"])
    _book_on_slot(conn, st["emma_pathway"], "COLONOSCOPY", DISRUPTED_RESOURCE, at, aid, ENDO_USER, booked_by="story")
    core.notify(conn, key=f"emma-colo-{st['run']}", audience="patient", patient_id=st["emma_patient"], pathway_id=st["emma_pathway"],
                kind="booking", title="Colonoscopy booked",
                body=f"Endoscopy Unit 2, North Karelia Central Hospital, {core.fmt_time(at)}. Bowel-preparation instructions follow.",
                payload={"appointment_id": str(aid), "needs_confirmation": True})
    return f"Specialist requested a colonoscopy within 3 days: booked {core.fmt_time(at)}, Endoscopy Unit 2."


def step_disruption(conn):
    from backend.ops import interventions
    core.set_story_clock(conn, 6, 7.5)
    d = interventions.trigger_resource_down(conn, DISRUPTED_RESOURCE, hours=48)
    return f"Endoscopy Unit 2 went offline for 48 h: {d['summary']['affected_count']} appointments affected."


def step_recover(conn):
    """Presenter fallback: approve the optimiser's recommendation (normally done by Donny via voice or map)."""
    from backend.ops import interventions
    iv = interventions.current(conn)
    if not iv:
        return "No open intervention."
    if iv["status"] == "AWAITING_APPROVAL":
        interventions.approve(conn, iv["id"], COORD_USER, "approved from presenter panel")
        return "Recovery plan approved and applied."
    return f"Current intervention is {iv['status']}."


def step_confirm_new(conn):
    appt = _emma_appt(conn, "COLONOSCOPY")
    if not appt:
        return "Emma has no booked colonoscopy."
    confirm_appointment(conn, appt["id"], "pat-emma")
    return f"Emma confirmed her new colonoscopy time ({core.fmt_time(appt['scheduled_for'])})."


def step_pre_procedure(conn):
    st = state(conn)
    appt = _emma_appt(conn, "COLONOSCOPY")
    if not appt:
        return "Emma has no booked colonoscopy."
    d = core.story_day(conn, appt["scheduled_for"])
    core.set_story_clock(conn, int(d) - 1 if d >= 1 else 0, 18.0)
    core.notify(conn, key=f"emma-prep-{appt['id']}", audience="patient", patient_id=st["emma_patient"], pathway_id=st["emma_pathway"],
                kind="checkin", severity="warning", title="Tomorrow: colonoscopy check-in",
                body="Please confirm you have started bowel preparation and can attend. Reply if anything has changed.",
                payload={"appointment_id": str(appt["id"]), "needs_checkin": True})
    return "Pre-procedure check-in request sent. (Emma does not answer.)"


def step_miss(conn):
    from backend.ops import followup
    appt = _emma_appt(conn, "COLONOSCOPY")
    if not appt:
        return "Emma has no booked colonoscopy."
    d = core.story_day(conn, appt["scheduled_for"])
    core.set_story_clock(conn, int(d), 15.5)
    return followup.record_missed(conn, appt["id"])


def step_followup_rebook(conn):
    from backend.ops import followup
    return followup.authorise_first(conn, emma_pathway(conn), NURSE_USER)


def step_attend_colonoscopy(conn):
    appt = _emma_appt(conn, "COLONOSCOPY")
    if not appt:
        return "Emma has no booked colonoscopy."
    d = core.story_day(conn, appt["scheduled_for"])
    now = core.set_story_clock(conn, int(d), 12.0)
    _attend(conn, appt, ENDO_USER)
    _stage(conn, "COLONOSCOPY", now, ENDO_USER, appt["facility_id"], {"finding": "tumour in the rectum, biopsies taken"})
    _stage(conn, "HISTOLOGY", now + timedelta(minutes=30), ENDO_USER, appt["facility_id"], {"samples": 6})
    st = state(conn)
    st["colonoscopy_day"] = int(d)
    save(conn, st)
    return f"Colonoscopy done ({core.facility_label(appt['facility_id'])}): biopsies sent to pathology."


def step_histology(conn):
    st = state(conn)
    d = st.get("colonoscopy_day", 10) + 5
    now = core.set_story_clock(conn, d, 11.0)
    core.post(conn, st["emma_pathway"], {"id": did("emma-res", st["run"]), "type": "RESULT_RECORDED", "occurred_at": now,
                                         "payload": {"kind": "histology", "value": "adenocarcinoma (clinician-recorded)"}}, ENDO_USER)
    _stage(conn, "CANCER_CONFIRMED", now, ENDO_USER, HOME_FACILITY, {"recorded_by": "clinician"})
    at = None
    free = core.free_slots(conn, "res-pkks-mri", core.story_time(conn, d + 2, 0))
    if free:
        at = free[0]["starts_at"]
        _book_on_slot(conn, st["emma_pathway"], "STAGING", "res-pkks-mri", at, did("emma-mri", st["run"]), ENDO_USER, "story")
    core.notify(conn, key=f"emma-res-{st['run']}", audience="patient", patient_id=st["emma_patient"], pathway_id=st["emma_pathway"],
                kind="result", title="Your doctor has your results",
                body="Your doctor will call you today. A staging MRI and CT have been booked" + (f" for {core.fmt_time(at)}." if at else "."))
    return "Clinician recorded the histology result (cancer confirmed); staging MRI booked."


def step_staging(conn):
    st = state(conn)
    appt = _emma_appt(conn, "STAGING")
    d = core.story_day(conn, appt["scheduled_for"]) if appt else st.get("colonoscopy_day", 10) + 7
    now = core.set_story_clock(conn, int(d), 15.0)
    if appt:
        _attend(conn, appt, ENDO_USER)
    _stage(conn, "STAGING", now, ENDO_USER, HOME_FACILITY, {"exams": "pelvic MRI + chest/abdomen CT"})
    return "Staging MRI and CT done."


def step_mdt(conn):
    st = state(conn)
    d = core.story_day(conn) + 2
    now = core.set_story_clock(conn, int(d), 14.0)
    _stage(conn, "MDT_DECISION", now, SURGEON_USER, HOME_FACILITY, {"decision": "surgery", "decided_by": "multidisciplinary team"})
    at = core.story_time(conn, int(d) + 9, 8.0)
    aid = did("emma-surg", st["run"])
    core.post(conn, st["emma_pathway"], {"id": did("book", aid), "type": "APPOINTMENT_BOOKED", "occurred_at": now,
                                         "payload": {"appointment_id": str(aid), "stage": "SURGERY", "scheduled_for": at.isoformat(),
                                                     "facility_id": HOME_FACILITY}}, SURGEON_USER)
    core.notify(conn, key=f"emma-mdt-{st['run']}", audience="patient", patient_id=st["emma_patient"], pathway_id=st["emma_pathway"],
                kind="booking", title="Treatment plan agreed",
                body=f"Your care team recommends surgery. Operation date: {core.fmt_time(at)}.")
    return f"MDT decision recorded by clinicians (surgery); operation booked {core.fmt_time(at)}."


def step_treatment(conn):
    appt = _emma_appt(conn, "SURGERY")
    d = core.story_day(conn, appt["scheduled_for"]) if appt else core.story_day(conn) + 9
    now = core.set_story_clock(conn, int(d), 12.0)
    if appt:
        _attend(conn, appt, SURGEON_USER)
    _stage(conn, "SURGERY", now, SURGEON_USER, HOME_FACILITY, {})
    st = state(conn)
    ref = core.story_time(conn, 1, 10.5)
    return f"Treatment started: surgery on story day {int(d)}, {round((now - ref).total_seconds() / 86400)} days after referral."


STEPS = [
    ("book_gp", "Emma books a GP appointment", "patient", step_book_gp),
    ("attend_gp", "GP visit → urgent referral", "clinician", step_attend_gp),
    ("referral_received", "Hospital receives referral, books specialist", "clinician", step_referral_received),
    ("reminder", "Reminder: unconfirmed appointment", "system", step_reminder),
    ("confirm_specialist", "Emma confirms (patient app)", "patient", step_confirm_specialist),
    ("attend_specialist", "Specialist → colonoscopy in 3 days", "clinician", step_attend_specialist),
    ("disruption", "Endoscopy Unit 2 offline (48 h)", "system", step_disruption),
    ("recover", "Donny approves a recovery plan", "admin", step_recover),
    ("confirm_new", "Emma confirms her new time", "patient", step_confirm_new),
    ("pre_procedure", "Pre-procedure check-in request", "system", step_pre_procedure),
    ("miss", "Emma misses the appointment", "patient", step_miss),
    ("followup_rebook", "Care team authorises a new slot", "clinician", step_followup_rebook),
    ("attend_colonoscopy", "Colonoscopy + biopsies", "clinician", step_attend_colonoscopy),
    ("histology", "Result recorded, staging booked", "clinician", step_histology),
    ("staging", "Staging MRI + CT", "clinician", step_staging),
    ("mdt", "MDT treatment decision", "clinician", step_mdt),
    ("treatment", "Treatment starts", "clinician", step_treatment),
]


def run_step(conn, key: str) -> dict:
    fn = next((f for k, _, _, f in STEPS if k == key), None)
    if not fn:
        raise svc.NotFound(f"story step {key}")
    msg = fn(conn)
    st = state(conn)
    if key not in st["done"]:
        st["done"].append(key)
    st["last"] = {"step": key, "message": msg}
    save(conn, st)
    core.emit({"kind": "ops", "what": "story", "step": key, "message": msg})
    return {"step": key, "message": msg, "now": svc.now(conn).isoformat(), "story_day": round(core.story_day(conn), 2)}


def next_step(conn) -> dict:
    done = set(state(conn).get("done", []))
    k = next((k for k, *_ in STEPS if k not in done), None)
    if not k:
        return {"step": None, "message": "Story complete."}
    return run_step(conn, k)


def overview(conn) -> dict:
    st = state(conn)
    done = set(st.get("done", []))
    try:
        day = round(core.story_day(conn), 2)
    except LookupError:
        day = None
    return {"initialised": bool(st), "run": st.get("run"), "story_day": day, "now": svc.now(conn).isoformat(),
            "emma_patient": st.get("emma_patient"), "emma_pathway": st.get("emma_pathway"), "last": st.get("last"),
            "steps": [{"key": k, "label": l, "actor": a, "done": k in done} for k, l, a, _ in STEPS]}
