"""Adapter: operational state (real free slots, booked occupancy, the disruption) -> the brain's pathway instance,
then evaluation of recovery options with the brain's own solver, greedy baselines and stochastic replay.

Nothing here invents capacity: colonoscopy capacity per (hospital, day) is the number of FREE ops slots after the
disruption (slots already booked by other patients are occupied). Downstream stages (pathology, MDT, surgery) use
the brain's documented assumptions (ASSUMPTION, see DOWNSTREAM) so plans stay aware of what follows the scope.

Outcomes are pooled over many stochastic replications (same random numbers for every option), so "within target"
is the share of the patients who turn out to need treatment and start it within 42 days of referral.
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta

import numpy as np
from psycopg.rows import dict_row

from backend.mpc.pathway.heuristics import schedule_greedy
from backend.mpc.pathway.instance import MEETING, PATIENT, SAMPLE, Instance, Patient, Provider, StageSpec
from backend.mpc.pathway.replay import kpis, replay
from backend.mpc.pathway.solver import Plan, StageAssignment, Strategy, lex_better, solve
from backend.ops import core
from backend.ops.world import DAYS, FAC_COUNTY, FAC_SHORT, HOME_FACILITY
from backend.services import pathways as svc

LAB = "LAB_PKKS"
P_BIOPSY, P_CANCER = 0.7, 0.3            # same assumptions as the brain benchmark (backend/mpc/pathway/demo.py)
DOWNSTREAM = {"pathology_per_day": 2, "pathology_turnaround_days": 5, "mdt_weekday": 1, "mdt_cap": 6,
              "surgery_days": (0, 2, 3), "surgery_per_day": 1, "beyond_calendar_free_share": 0.3,
              "tag": "ASSUMPTION (capacity left for this cohort after other patients)"}
REPS = 200
MIN_ACCEPT = 0.25         # offer a transfer only if P(accept) >= 25 % (a declined offer costs a re-booking; ASSUMPTION)
HORIZON = 63


def _stages(p_needed: bool = True):
    pb, pc = (P_BIOPSY, P_CANCER) if p_needed else (1.0, 1.0)
    return (StageSpec("colonoscopy", "colonoscopy", PATIENT, 1.0),
            StageSpec("pathology", "pathology", SAMPLE, DOWNSTREAM["pathology_turnaround_days"], pb),
            StageSpec("mdt", "mdt", MEETING, 3.0, pc),
            StageSpec("treatment", "treatment", PATIENT, 0.0, 1.0, is_treatment=True))


_SEL = """SELECT s.id AS slot_id, s.starts_at, s.resource_id, r.facility_id, a.id AS appointment_id, a.pathway_id, a.confirmed_at,
                  p.patient_id, pt.display_name AS name, ag.persona,
                  ST_X(pt.home_geom::geometry) AS lon, ST_Y(pt.home_geom::geometry) AS lat
           FROM ops_slot s JOIN ops_resource r ON r.id=s.resource_id
           JOIN appointment a ON a.id=s.appointment_id AND a.status='booked'
           JOIN pathway p ON p.id=a.pathway_id JOIN patient pt ON pt.id=p.patient_id
           LEFT JOIN ops_patient_agent ag ON ag.patient_id=p.patient_id"""


def affected_patients(conn, disruption_id) -> list[dict]:
    """Booked appointments on the disrupted resource inside the outage window, with persona + referral."""
    rows = conn.cursor(row_factory=dict_row).execute(
        _SEL + """ JOIN ops_disruption d ON s.resource_id=d.resource_id AND s.starts_at >= d.starts_at AND s.starts_at < d.ends_at
           WHERE d.id=%s ORDER BY s.starts_at""", (disruption_id,)).fetchall()
    return _with_referral(conn, rows)


def booked_colonoscopy(conn, patient_ids: list) -> list[dict]:
    rows = conn.cursor(row_factory=dict_row).execute(
        _SEL + " WHERE r.capability='colonoscopy' AND p.patient_id = ANY(%s) ORDER BY s.starts_at", (patient_ids,)).fetchall()
    return _with_referral(conn, rows)


def _with_referral(conn, rows):
    for r in rows:
        ref = conn.execute("""SELECT occurred_at, payload FROM event WHERE pathway_id=%s AND type='STAGE_ENTERED'
                              AND stage='REFERRAL_SENT' ORDER BY seq LIMIT 1""", (r["pathway_id"],)).fetchone()
        r["referral_at"] = ref[0] if ref else None
        r["priority"] = (ref[1] or {}).get("priority", "standard") if ref else "standard"   # clinically supplied
    return rows


def _capacity(conn, day0_at, now, add_back: list | None = None) -> dict:
    """(provider, capability, day) -> units. Colonoscopy from FREE slots; downstream from assumptions."""
    cap: dict = {}
    t0 = core.t0(conn)
    start_story_day = int(math.floor((day0_at - t0).total_seconds() / 86400))
    for fid, n, d in conn.execute(
            """SELECT r.facility_id, count(*), floor(extract(epoch from (s.starts_at - %s)) / 86400)::int
               FROM ops_slot s JOIN ops_resource r ON r.id=s.resource_id
               WHERE r.capability='colonoscopy' AND s.status='free' AND s.starts_at >= %s
               GROUP BY 1, 3""", (day0_at, now)):
        if d >= 0:
            cap[fid, "colonoscopy", d] = cap.get((fid, "colonoscopy", d), 0) + int(n)
    per_day = {fid: int(n) for fid, n in conn.execute(
        "SELECT facility_id, sum(slots_per_day) FROM ops_resource WHERE capability='colonoscopy' GROUP BY 1")}
    for d in range(DAYS - start_story_day, HORIZON + 400):               # beyond the slot calendar (ASSUMPTION)
        for fid, n in per_day.items():
            cap[fid, "colonoscopy", d] = int(round(n * DOWNSTREAM["beyond_calendar_free_share"]))
    for (fid, d) in (add_back or []):
        cap[fid, "colonoscopy", d] = cap.get((fid, "colonoscopy", d), 0) + 1
    for d in range(HORIZON + 400):
        wd = (start_story_day + d) % 7
        cap[LAB, "pathology", d] = DOWNSTREAM["pathology_per_day"]
        if wd == DOWNSTREAM["mdt_weekday"]:
            cap[HOME_FACILITY, "mdt", d] = DOWNSTREAM["mdt_cap"]
        if wd in DOWNSTREAM["surgery_days"]:
            cap[HOME_FACILITY, "treatment", d] = DOWNSTREAM["surgery_per_day"]
    return cap


def build(conn, disruption_id, baseline: bool = False, extra_avoid: dict | None = None, earliest: dict | None = None,
          rows: list | None = None):
    now = svc.now(conn)
    t0 = core.t0(conn)
    sd = math.floor((now - t0).total_seconds() / 86400)
    day0_at = t0 + timedelta(days=sd)
    aff = rows if rows is not None else affected_patients(conn, disruption_id)
    providers = {fid: Provider(fid, FAC_SHORT.get(fid, fid), FAC_COUNTY.get(fid, "")) for (fid,) in conn.execute(
        "SELECT DISTINCT facility_id FROM ops_resource WHERE capability='colonoscopy'")}
    providers[LAB] = Provider(LAB, "North Karelia pathology", "North Karelia")
    add_back = []
    if baseline:
        add_back = [(HOME_FACILITY, int((a["starts_at"] - day0_at).total_seconds() // 86400)) for a in aff]
    if rows is not None:                                   # re-plan: the patients' own current slots are theirs to give up
        add_back += [(r["facility_id"], int((r["starts_at"] - day0_at).total_seconds() // 86400)) for r in aff]
    cap = _capacity(conn, day0_at, now, add_back)
    pts, info = [], {}
    for a in aff:
        per = a["persona"] or {}
        acc = dict(per.get("accept_prob", {}))
        for f in (extra_avoid or {}).get(a["patient_id"], []):
            acc[f] = 0.0                                            # the patient already declined this hospital
        elapsed = (now - a["referral_at"]).total_seconds() / 86400 if a["referral_at"] else 0.0
        ready = 0.0
        if earliest and a["patient_id"] in earliest:
            ready = max(0.0, earliest[a["patient_id"]] - sd)
        pid = a["patient_id"]
        pts.append(Patient(pid=pid, region="North Karelia", home=HOME_FACILITY, home_lab=LAB, priority=a["priority"],
                           ready_day=ready, deadline_day=round(42 - elapsed, 1), stages=_stages(True), diagnosis_known=False,
                           travel_min={f: float(v) for f, v in per.get("travel_min", {}).items()},
                           accept_prob={f: float(v) for f, v in acc.items()}, consent_transfer=True,
                           p_attend=float(per.get("p_attend", 0.93)), treat_home=HOME_FACILITY))
        info[pid] = {**a, "orig_day": int((a["starts_at"] - day0_at).total_seconds() // 86400), "elapsed_days": round(elapsed, 1)}
    inst = Instance(providers, cap, pts, horizon_days=HORIZON, near_term_days=14, ship_lag_days=1.0,
                    meta={"name": "Operational instance (live free slots)", "day0": day0_at.isoformat()})
    return inst, info, day0_at


def _if_cancer(inst: Instance) -> Instance:
    return replace(inst, patients=[replace(p, stages=_stages(False), diagnosis_known=True) for p in inst.patients])


def _evaluate(inst: Instance, plan: Plan) -> dict:
    """Stochastic replay with the brain's own semantics (biopsy / cancer / acceptance / attendance drawn per patient
    with common random numbers). Outcomes are POOLED over replications: 'within target' = share of the patients who
    turn out to need treatment and start it by their 42-day target."""
    outs = [replay(inst, plan, r, seed=2026) for r in range(REPS)]
    need = on = urg_need = urg_on = 0
    breaches, cds, dds, tds, transfers, travel, lost = [], [], [], [], [], [], []
    per = {p.pid: {"need": 0, "on": 0, "cd": [], "td": [], "acc": 0} for p in inst.patients}
    pm = {p.pid: p for p in inst.patients}
    for o in outs:
        b = 0
        for pid, x in o["patients"].items():
            p = pm[pid]
            if x["colonoscopy_day"] is not None:
                cds.append(x["colonoscopy_day"]); per[pid]["cd"].append(x["colonoscopy_day"])
            if x["diag_day"] is not None:
                dds.append(x["diag_day"])
            per[pid]["acc"] += 1 if x["transfers"] > 0 else 0
            if x["needs_treatment"]:
                ok = x["treatment_day"] is not None and not x["lost"] and x["treatment_day"] <= p.deadline_day
                need += 1; on += ok; per[pid]["need"] += 1; per[pid]["on"] += ok
                if p.priority == "urgent":
                    urg_need += 1; urg_on += ok
                b += 0 if ok else 1
                if x["treatment_day"] is not None:
                    tds.append(x["treatment_day"]); per[pid]["td"].append(x["treatment_day"])
        breaches.append(b)
        transfers.append(sum(x["transfers"] for x in o["patients"].values()))
        travel.append(sum(x["travel_min"] for x in o["patients"].values()) / 60)
        lost.append(sum(1 for x in o["patients"].values() if x["lost"]))
    r1 = lambda v: round(float(np.mean(v)), 1) if v else None
    agg = {"pct_within_target": round(100 * on / need, 1) if need else None,
           "pct_urgent_within_target": round(100 * urg_on / urg_need, 1) if urg_need else None,
           "breaches_per_run": round(float(np.mean(breaches)), 2), "mean_colonoscopy_day": r1(cds),
           "mean_diagnosis_day": r1(dds), "mean_treatment_day": r1(tds), "transfers": r1(transfers),
           "patient_travel_hours": r1(travel), "lost_to_follow_up": round(float(np.mean(lost)), 2),
           "pathways_needing_treatment": need, "replications": REPS}
    pts = {}
    for p in inst.patients:
        q = per[p.pid]
        pts[p.pid] = {"p_on_time": round(q["on"] / q["need"], 2) if q["need"] else None, "colonoscopy_day": r1(q["cd"]),
                      "treatment_day": r1(q["td"]), "deadline_day": p.deadline_day, "accepted_share": round(q["acc"] / REPS, 2)}
    return {"kpis": agg, "patients": pts}


def _fixed_colonoscopy(inst: Instance, alloc: dict, keep_day: dict | None = None) -> dict:
    """Admin / baseline allocation: hospital per patient -> earliest day with remaining capacity (priority order)."""
    rem = {}
    for (h, c, d), v in inst.capacity.items():
        if c == "colonoscopy":
            rem[h, d] = v
    fixed = {}
    for p in sorted(inst.patients, key=lambda p: (p.priority != "urgent", p.deadline_day, p.pid)):
        h = alloc.get(p.pid, p.home)
        d0 = (keep_day or {}).get(p.pid)
        days = [d0] if d0 is not None else range(int(math.ceil(p.ready_day)), inst.horizon_days + 300)
        day = None
        for d in days:
            if rem.get((h, d), 0) >= 1:
                rem[h, d] -= 1
                day = d
                break
        fixed[p.pid, 0] = StageAssignment(p.pid, 0, "colonoscopy", h, float(day if day is not None else inst.horizon_days + 7),
                                          1.0, False)
    return fixed


def _compact(inst: Instance, plan: Plan) -> Plan:
    """Left-shift: keep the optimiser's choice of hospital per patient, give each the earliest free colonoscopy day
    there (clinical-priority order), then schedule downstream. Removes slack the expected-value MIP is indifferent
    to but that matters once no-shows and declines happen."""
    alloc = {p.pid: (plan.assignments.get((p.pid, 0)).provider or p.home) for p in inst.patients if plan.assignments.get((p.pid, 0))}
    out = schedule_greedy(inst, "priority", fixed=_fixed_colonoscopy(inst, alloc))
    out.strategy, out.tiers, out.status = plan.strategy, plan.tiers, plan.status + "+leftshift"
    return out


def _plan_rows(conn, inst, plan, info, day0_at, ev, take_slots: bool) -> list[dict]:
    """Concrete moves: the planned colonoscopy (hospital, day) -> a real free slot (reserved only on apply)."""
    rows, used = [], set()
    t0 = core.t0(conn)
    now = svc.now(conn)
    for p in sorted(inst.patients, key=lambda p: (p.priority != "urgent", p.deadline_day, p.pid)):
        a = plan.assignments.get((p.pid, 0))
        h = a.provider if a and a.provider else p.home
        day = int(a.start_day) if a else info[p.pid]["orig_day"]
        slot = None
        for s in conn.cursor(row_factory=dict_row).execute(
                """SELECT s.id, s.starts_at, s.resource_id FROM ops_slot s JOIN ops_resource r ON r.id=s.resource_id
                   WHERE r.facility_id=%s AND r.capability='colonoscopy' AND s.status='free'
                   AND s.starts_at >= %s AND s.starts_at >= %s ORDER BY s.starts_at LIMIT 40""",
                (h, day0_at + timedelta(days=day), now)).fetchall():
            if s["id"] not in used:
                slot = s
                used.add(s["id"])
                break
        i = info[p.pid]
        per = i["persona"] or {}
        rows.append({"patient_id": p.pid, "name": i["name"], "pathway_id": str(i["pathway_id"]), "priority": p.priority,
                     "appointment_id": str(i["appointment_id"]),
                     "from": {"facility_id": i["facility_id"], "facility": FAC_SHORT.get(i["facility_id"]), "at": i["starts_at"].isoformat(),
                              "resource_id": i["resource_id"]},
                     "to": {"facility_id": h, "facility": FAC_SHORT.get(h, h), "county": FAC_COUNTY.get(h),
                            "planned_day": day, "at": slot["starts_at"].isoformat() if slot else None,
                            "slot_id": str(slot["id"]) if slot else None, "resource_id": slot["resource_id"] if slot else None},
                     "transfer": h != HOME_FACILITY, "travel_min": per.get("travel_min", {}).get(h),
                     "accept_prob": p.accept_prob.get(h, 1.0 if h == HOME_FACILITY else 0.0),
                     "home": {"lon": i["lon"], "lat": i["lat"], "muni": per.get("muni")},
                     "outcome": ev["patients"].get(p.pid)})
    return rows


def _window_load(inst, plan, days=3) -> dict:
    out = {}
    for (pid, j), a in plan.assignments.items():
        if j == 0 and a.provider:
            out.setdefault(a.provider, {"assigned": 0, "free_next_72h": sum(inst.capacity.get((a.provider, "colonoscopy", d), 0)
                                                                           for d in range(days))})
            out[a.provider]["assigned"] += 1
    return out


def evaluate(conn, disruption_id, admin_alloc: dict | None = None, extra_avoid: dict | None = None,
             earliest: dict | None = None, include: tuple = ("baseline", "no_action", "optimised")) -> dict:
    inst, info, day0_at = build(conn, disruption_id, extra_avoid=extra_avoid, earliest=earliest)
    if not inst.patients:
        return {"options": {}, "patients": 0}
    res, plans = {}, {}
    if "baseline" in include:
        binst, _, _ = build(conn, disruption_id, baseline=True)
        bplan = schedule_greedy(binst, "priority", fixed=_fixed_colonoscopy(binst, {}, {pid: i["orig_day"] for pid, i in info.items()}))
        res["baseline"] = {"label": "Original plan (no breakdown)", **_evaluate(binst, bplan)}
    if "no_action" in include:
        plans["no_action"] = schedule_greedy(inst, "fifo")
        res["no_action"] = {"label": "Breakdown, no coordination (rebooked locally, first come first served)",
                            **_evaluate(inst, plans["no_action"])}
    if "optimised" in include:
        hint = schedule_greedy(inst, "priority")
        st = Strategy("B_national_pathway", allow_patient_transfer=True, allow_sample_routing=False, time_limit_s=8.0, min_accept=MIN_ACCEPT,
                      label="End-to-end national pathway (patient transfers within consent and acceptance)")
        try:
            sol = solve(inst, st, hint=hint)
            best = sol if (sol.status in ("optimal", "feasible", "partial") and not lex_better(hint, sol)) else hint
        except Exception as e:                                   # never break the demo: fall back to the greedy plan
            best, sol = hint, None
        best = _compact(inst, best)
        plans["optimised"] = best
        res["optimised"] = {"label": "CancerFlow recommendation (whole-pathway optimiser)", **_evaluate(inst, best),
                            "solver_status": getattr(sol, "status", "fallback-greedy"), "tiers": getattr(best, "tiers", {})}
    if admin_alloc is not None:
        fixed = _fixed_colonoscopy(inst, admin_alloc)
        plans["admin"] = schedule_greedy(inst, "priority", fixed=fixed)
        res["admin"] = {"label": "Administrator's proposal", **_evaluate(inst, plans["admin"])}
    out = {"day0": day0_at.isoformat(), "story_day": int((day0_at - core.t0(conn)).total_seconds() // 86400),
           "patients": len(inst.patients), "replications": REPS,
           "assumptions": {**DOWNSTREAM, "p_biopsy": P_BIOPSY, "p_cancer": P_CANCER,
                           "outcomes": "pooled over 200 stochastic replications (biopsy, cancer, acceptance, attendance drawn per patient)",
                           "colonoscopy_capacity": "live FREE slots (occupied slots excluded)",
                           "acceptance": "persona-specific (national agent utility + persistent constraints)"},
           "options": res, "plans": {}}
    for k, pl in plans.items():
        out["plans"][k] = {"rows": _plan_rows(conn, inst, pl, info, day0_at, res[k], take_slots=False),
                           "load": _window_load(inst, pl)}
    return out


def evaluate_subset(conn, patient_ids: list, applied: dict, avoid: dict, earliest: dict) -> dict:
    """Re-plan for patients who replied (declined / asked for another time), on their CURRENT bookings and the
    capacity left after everyone else's bookings. Compared with simply keeping the current booking."""
    rows = booked_colonoscopy(conn, patient_ids)
    inst, info, day0_at = build(conn, None, extra_avoid=avoid, earliest=earliest, rows=rows)
    keep = schedule_greedy(inst, "priority", fixed={
        (p.pid, 0): StageAssignment(p.pid, 0, "colonoscopy", info[p.pid]["facility_id"], float(max(0, info[p.pid]["orig_day"])), 1.0, False)
        for p in inst.patients})
    hint = schedule_greedy(inst, "priority")
    st = Strategy("replan", allow_patient_transfer=True, time_limit_s=6.0, min_accept=MIN_ACCEPT)
    try:
        sol = solve(inst, st, hint=hint)
        best = sol if not lex_better(hint, sol) else hint
    except Exception:
        best = hint
    best = _compact(inst, best)
    ev_keep, ev_new = _evaluate(inst, keep), _evaluate(inst, best)
    out_rows = _plan_rows(conn, inst, best, info, day0_at, ev_new, take_slots=False)
    by = {}
    for r in out_rows:
        by[r["to"]["facility"]] = by.get(r["to"]["facility"], 0) + 1
    return {"options": {"keep_current": {"label": "Keep the booking the patient declined", **ev_keep},
                        "replan": {"label": "Re-planned with the patient's stated constraints", **ev_new}},
            "rows": out_rows, "by_facility": by, "constraints": {"avoid": avoid, "earliest_story_day": earliest},
            "replications": REPS}
