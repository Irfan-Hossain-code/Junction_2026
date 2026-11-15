"""Grounded facts for one twin context (national / county / facility) and one or two runs.

Everything the UI panels and the LLM say is computed here from stored SIMULATED runs, OBSERVED
indicators and live (SYNTHETIC) operational data. The LLM only ever sees this dict.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from backend.opt.allocate import optimise
from backend.pathways.pack import get_pack
from backend.sim import params as P  # noqa: F811
from backend.sim.snapshot import Snapshot

PACK = get_pack("colorectal")
TARGET = 42
CAP_LABEL = {"colonoscopy": "colonoscopy", "ct_imaging": "CT staging", "mdt_crc": "MDT meetings",
             "crc_surgery": "colorectal surgery", "oncology": "oncology", "radiotherapy": "radiotherapy", "pathology": "pathology"}
STAGE_ORDER = ["FIT_POSITIVE", "NURSE_ASSESSMENT", "REFERRAL_SENT", "REFERRAL_RECEIVED", "COLONOSCOPY", "HISTOLOGY",
               "CANCER_CONFIRMED", "STAGING", "MDT_DECISION"]
OBS = {"sotkanet:6103": "Colorectal screening participation, %", "sotkanet:3627": "Bowel cancer 5-yr relative survival, %",
       "sotkanet:3336": "Waited > 6 months for specialist care, per 10 000", "sotkanet:6411": "Non-urgent GP visits waiting > 7 d, %",
       "sotkanet:13": "Specialist referrals handled within 3 d, %"}


def metrics_of(conn, run_id) -> dict:
    out: dict = {}
    if not run_id:
        return out
    for r in conn.execute("SELECT scope_kind, scope_id, metric, mean, ci_low, ci_high FROM sim_metric WHERE run_id=%s", (run_id,)):
        out.setdefault(r[0], {}).setdefault(r[1], {})[r[2]] = {"mean": r[3], "ci": [r[4], r[5]]}
    return out


def _v(d, k):
    x = d.get(k)
    return None if x is None else x["mean"]


def _ci(d, k):
    x = d.get(k)
    return None if x is None else x["ci"]


def scope_info(snap: Snapshot, scope: str, sid: Optional[str]) -> dict:
    if scope == "county":
        if sid not in snap.counties:
            raise KeyError(f"unknown county {sid}")
        return {"scope": scope, "id": sid, "name": snap.counties[sid]["name"], "county": sid}
    if scope == "facility":
        if sid not in snap.facilities:
            raise KeyError(f"unknown facility {sid}")
        f = snap.facilities[sid]
        return {"scope": scope, "id": sid, "name": f["name"], "county": f["county"], "kind": f["kind"],
                "county_name": snap.counties.get(f["county"], {}).get("name")}
    return {"scope": "national", "id": "fi", "name": "Finland", "county": None}


def region_block(M: dict, info: dict) -> dict:
    if info["scope"] == "national":
        return M.get("national", {}).get("national", {})
    return M.get("region", {}).get(info["county"], {})


def compliance(reg: dict) -> dict:
    p, n = _v(reg, "referral_to_treatment.p_within_target"), _v(reg, "referral_to_treatment.n")
    return {"p_within_target": p, "ci": _ci(reg, "referral_to_treatment.p_within_target"), "cohort_n_per_year": n,
            "exceeding_per_year": None if p is None or n is None else n * (1 - p),
            "fit_to_colonoscopy_p_within": _v(reg, "fit_positive_to_colonoscopy.p_within_target"),
            "behaviour": {"dna_rate": _v(reg, "behaviour.dna_rate"), "alt_decline_rate": _v(reg, "behaviour.alt_decline_rate"),
                          "alt_offers_per_week": _v(reg, "behaviour.alt_offers_per_week"), "lost_rate": _v(reg, "behaviour.lost_rate"),
                          "tag": "SIMULATED (behavioural agents)"},
            "target_days": TARGET, "target_tag": "REFERENCE (FI-1, 6 weeks)", "tag": "SIMULATED"}


def delays(reg: dict) -> dict:
    stages = [{"stage": s, "label": PACK.stages[s].label, "mean_days": _v(reg, f"stage_days.{s}")}
              for s in STAGE_ORDER if reg.get(f"stage_days.{s}")]
    hist = [{"bin": b, "share": _v(reg, f"rtt_hist.{b}")} for b in (0, 14, 28, 42, 56, 70) if reg.get(f"rtt_hist.{b}")]
    return {"median_days": _v(reg, "referral_to_treatment.median_days"), "p90_days": _v(reg, "referral_to_treatment.p90_days"),
            "median_ci": _ci(reg, "referral_to_treatment.median_days"),
            "fit_to_colonoscopy_median_days": _v(reg, "fit_positive_to_colonoscopy.median_days"),
            "stall_days_mean": _v(reg, "stall_days.mean"), "stalls_per_year": _v(reg, "stalls.n"),
            "stages": stages, "rtt_histogram": hist, "tag": "SIMULATED"}


def stations_in_scope(snap: Snapshot, M: dict, info: dict) -> list[dict]:
    fac_ids = set()
    if info["scope"] == "facility":
        fac_ids = {info["id"]}
    elif info["scope"] == "county":
        fac_ids = {f for f, v in snap.facilities.items() if v["county"] == info["county"]}
        for k, e in M.get("edge", {}).items():          # hospitals receiving this county's patients
            src, rest = k.split("->")
            if src == info["county"] and e["flow_per_week"]["mean"] >= 0.5:
                fac_ids.add(rest.split("|")[0])
    out = []
    for k, m in M.get("facility", {}).items():
        fac, cap = k.split("|")
        if fac_ids and fac not in fac_ids:
            continue
        if cap == "pathology":
            continue
        out.append({"facility_id": fac, "facility": snap.facilities.get(fac, {}).get("name", fac), "capability": cap,
                    "capability_label": CAP_LABEL.get(cap, cap), "utilisation": _v(m, "utilisation"),
                    "mean_wait_days": _v(m, "mean_wait_days"), "p90_wait_days": _v(m, "p90_wait_days"),
                    "capacity_per_week": _v(m, "capacity_per_week"), "throughput_per_week": _v(m, "throughput_per_week"),
                    "tag": "SIMULATED (capacity SYNTHETIC)"})
    out.sort(key=lambda s: (-(s["mean_wait_days"] or 0) - 5 * max(0, (s["utilisation"] or 0) - 0.9)))
    return out


def county_ranking(snap: Snapshot, M: dict) -> list[dict]:
    rows = []
    for cid, c in snap.counties.items():
        reg = M.get("region", {}).get(cid, {})
        p = _v(reg, "referral_to_treatment.p_within_target")
        if p is not None:
            rows.append({"county_id": cid, "name": c["name"].replace(" wellbeing services county", ""), "p_within_target": p,
                         "ci": _ci(reg, "referral_to_treatment.p_within_target"), "median_days": _v(reg, "referral_to_treatment.median_days")})
    return sorted(rows, key=lambda r: r["p_within_target"])


def observed(conn, info) -> dict:
    rid = info["county"] or "fi"
    out = {}
    for r in conn.execute("""SELECT DISTINCT ON (indicator_id) indicator_id, year, value FROM indicator_value
                             WHERE region_id=%s AND gender='total' AND indicator_id = ANY(%s)
                             ORDER BY indicator_id, year DESC""", (rid, list(OBS))):
        out[OBS[r[0]]] = {"value": r[2], "year": r[1], "tag": "OBSERVED", "source": f"THL Sotkanet {r[0].split(':')[1]}"}
    return out


def live(conn, info) -> dict:
    q = """SELECT count(DISTINCT p.id) FILTER (WHERE p.status='active'),
                  count(a.id) FILTER (WHERE a.status<>'resolved'),
                  count(a.id) FILTER (WHERE a.status<>'resolved' AND a.severity='critical')
           FROM pathway p JOIN patient pt ON pt.id=p.patient_id
           LEFT JOIN region m ON m.id=pt.home_region_id
           LEFT JOIN alert a ON a.pathway_id=p.id WHERE true"""
    args: list = []
    if info["scope"] == "county":
        q += " AND m.parent_id=%s"; args.append(info["county"])
    elif info["scope"] == "facility":
        q += " AND (a.responsible_facility_id=%s OR EXISTS (SELECT 1 FROM event e WHERE e.pathway_id=p.id AND e.facility_id=%s))"
        args += [info["id"], info["id"]]
    a, b, c = conn.execute(q, args).fetchone()
    return {"active_pathways": a, "open_alerts": b, "critical_alerts": c, "tag": "SYNTHETIC operational data"}


def recommendations(snap: Snapshot, info: dict, stations: list[dict], dl: dict, comp: dict | None = None) -> list[dict]:
    """Data-triggered interventions; each carries runnable scenario params. Behavioural ones only appear when the
    simulated behaviour metric that motivates them is actually elevated."""
    from backend.sim.snapshot import apply_scenario
    recs = []
    beh = (comp or {}).get("behaviour") or {}
    counties = [info["county"]] if info.get("county") else list(snap.counties)
    scope_param = counties if info.get("county") else True
    stall, stalls = dl.get("stall_days_mean"), dl.get("stalls_per_year")
    recs.append({
        "id": "monitoring", "title": "Detect stalled steps automatically",
        "action": "Switch on CancerFlow stalled-step alerts (care app) for this area",
        "rationale": (f"Stalled steps wait on average {stall:.0f} days before anyone acts ({stalls:.0f} per year in this cohort, SIMULATED)."
                      if stall and stalls else "Stalled referrals and missing follow-ups are detected only when patients chase them."),
        "params": {"monitoring": True}, "controls": []})
    dna = beh.get("dna_rate") or 0
    if dna >= 0.06:
        recs.append({
            "id": "reminders", "title": "Proactive reminders & follow-up calls",
            "action": "Reminder + confirmation contact before every colonoscopy appointment",
            "rationale": f"{100 * dna:.1f} % of colonoscopy appointments are missed and each no-show burns a slot; "
                         f"{100 * (beh.get('lost_rate') or 0):.1f} % of patients are lost to follow-up (SIMULATED agents).",
            "params": {"reminders": scope_param}, "controls": []})
    top = next((s for s in stations if s["capability"] in ("colonoscopy", "crc_surgery", "mdt_crc", "ct_imaging", "oncology", "radiotherapy")), None)
    if top:
        cap = top["capability"]
        restrict = [info["county"]] if info.get("county") else None
        tsnap = apply_scenario(snap, {"transport_support": restrict or True})
        plan = optimise(tsnap, cap, budget_units=0, max_travel_min=180, behavioral=True, restrict_counties=restrict)
        if info["scope"] == "facility":
            munis = {m for m in snap.munis if any(f == info["id"] for f, _ in snap.routing.get(f"{m}|{cap}", []))}
            changes = [c for c in plan["routing_changes"] if c["municipality"] in munis or c["from"] == top["facility_id"]]
        else:
            changes = plan["routing_changes"]
        rs = {f"{c['municipality']}|{cap}": plan["scenario_params"]["routing_set"][f"{c['municipality']}|{cap}"] for c in changes}
        if rs:
            dest = sorted({f for c in changes for f, _ in c["to"]})
            acc = plan.get("expected_acceptance_of_moves")
            recs.append({
                "id": "reroute", "title": f"Behaviour-aware re-routing of {CAP_LABEL[cap]} + transport support",
                "action": f"Offer {len(changes)} municipalities' {CAP_LABEL[cap]} patients slots at "
                          + ", ".join(snap.facilities[d]["name"] for d in dest[:3]) + ", with transport support",
                "rationale": f"OR-Tools plan that accounts for patient acceptance (expected {100 * (acc or 0):.0f} % accept, with transport "
                             f"support) and no-shows; {top['facility']} {CAP_LABEL[cap]} is at {100 * (top['utilisation'] or 0):.0f} % (SIMULATED).",
                "params": {"routing_set": rs, "transport_support": restrict or True}, "controls": [],
                "evidence": {"status": "optimizer_estimated", "model": "OR-Tools strategic network-flow (expected value, behaviour-aware)",
                             "scope": info["name"], "resource": CAP_LABEL[cap],
                             "overload_before_per_week": plan["overload_before"], "overload_after_per_week": plan["overload_after"],
                             "referrals_moved_per_week": plan["moved_per_week"], "expected_acceptance": acc}})
    pilot = P.PRIVATE_PILOT_FACILITY
    if pilot in snap.facilities and info.get("county") == snap.facilities[pilot]["county"]:
        recs.append({
            "id": "private", "title": f"Purchase colonoscopies from {snap.facilities[pilot]['name']} (pilot)",
            "action": "Service purchase: ~100 colonoscopies/month from an eligible local private provider",
            "rationale": f"Local capacity in the same town: little extra travel, so high expected acceptance. SYNTHETIC pilot: assumed "
                         f"capacity, cost {P.PRIVATE_COST_PER_COLONOSCOPY_EUR} EUR/procedure (ASSUMPTION).",
            "params": {"private_purchase": [{"facility_id": pilot, "capability": "colonoscopy", "per_week": 23, "eligible_counties": [info["county"]]}]},
            "controls": [{"path": "private_purchase.0.per_week", "label": "Purchased colonoscopies / week", "min": 5, "max": 50, "step": 1, "value": 23}]})
    if top:
        cap = top["capability"]
        recs.append({
            "id": "capacity", "title": f"Add {CAP_LABEL[cap]} capacity at {top['facility'].split(' (')[0]}",
            "action": f"Add weekly {CAP_LABEL[cap]} slots at {top['facility']}",
            "rationale": f"Utilisation {100 * (top['utilisation'] or 0):.0f} %, mean queue {top['mean_wait_days'] or 0:.1f} d at "
                         f"{top['capacity_per_week'] or 0:.1f} slots/week (SYNTHETIC capacity, SIMULATED load).",
            "params": {"capacity": [{"facility_id": top["facility_id"], "capability": cap, "add_per_week": 5}]},
            "controls": [{"path": "capacity.0.add_per_week", "label": "Added slots / week", "min": 1, "max": 20, "step": 1, "value": 5}]})
    for r in recs:                      # provenance: only claim what was actually computed
        r.setdefault("evidence", {"status": "suggested",
                                  "basis": "rule triggered by a simulated indicator; not optimised or simulated yet"})
    return recs[:5]


def mark_validated(conn, recs: list[dict], info: dict) -> list[dict]:
    """A recommendation is 'simulation_validated' only if a stored SimPy run of it exists for this area
    (the Intervention Studio names its runs '<area>: <title> + ...')."""
    short = info["name"].replace(" wellbeing services county", "")
    for r in recs:
        row = conn.execute("""SELECT s.id, r.id FROM scenario s JOIN sim_run r ON r.scenario_id=s.id AND r.status='done'
                              WHERE s.name ILIKE %s AND s.name ILIKE %s ORDER BY r.finished_at DESC LIMIT 1""",
                           (short + ":%", "%" + r["title"] + "%")).fetchone()
        if row:
            r["evidence"] = {**r.get("evidence", {}), "status": "simulation_validated", "scenario_id": str(row[0]), "run_id": str(row[1]),
                             "basis": "SimPy run with behavioural agents stored for this area (open it to compare)"}
    return recs


def primary_issue(info, comp, dl, stations) -> dict:
    top = stations[0] if stations else None
    parts = []
    if comp.get("p_within_target") is not None:
        parts.append(f"{100 * comp['p_within_target']:.0f} % of patients start treatment within the {TARGET}-day reference target")
    if top and (top["mean_wait_days"] or 0) > 2:
        parts.append(f"the tightest resource is {top['capability_label']} at {top['facility']} "
                     f"({100 * (top['utilisation'] or 0):.0f} % utilised, {top['mean_wait_days']:.1f} d mean queue)")
    if dl.get("stall_days_mean"):
        parts.append(f"stalled steps add {dl['stall_days_mean']:.0f} d on average before anyone acts")
    return {"headline": f"{info['name']}: " + "; ".join(parts) + "." if parts else f"{info['name']}: no simulated data yet.",
            "top_station": top, "tag": "SIMULATED"}


def build_context(conn, snap: Snapshot, scope: str, sid: Optional[str], run_id: Optional[str], base_run_id: Optional[str]) -> dict:
    info = scope_info(snap, scope, sid)
    M = metrics_of(conn, run_id)
    reg = region_block(M, info)
    comp, dl = compliance(reg), delays(reg)
    st = stations_in_scope(snap, M, info)
    ctx: dict[str, Any] = {"context": info, "run_id": run_id, "base_run_id": base_run_id,
                           "compliance": comp, "delays": dl, "bottlenecks": st[:8], "observed": observed(conn, info),
                           "live": live(conn, info), "primary_issue": primary_issue(info, comp, dl, st),
                           "ranking": county_ranking(snap, M) if scope == "national" else None,
                           "national_compliance": compliance(M.get("national", {}).get("national", {}))}
    if base_run_id and base_run_id != run_id:
        B = metrics_of(conn, base_run_id)
        breg = region_block(B, info)
        ctx["base"] = {"compliance": compliance(breg), "delays": delays(breg), "bottlenecks": stations_in_scope(snap, B, info)[:8],
                       "national_compliance": compliance(B.get("national", {}).get("national", {})),
                       "ranking": county_ranking(snap, B) if scope == "national" else None}
    ctx["recommendations"] = mark_validated(conn, recommendations(snap, info, st, dl, comp), info)
    from backend.services.anomalies import detect
    alarms = detect(snap, M, metrics_of(conn, base_run_id) if base_run_id and base_run_id != run_id else None)
    if info["scope"] == "county":
        alarms = [a for a in alarms if a["location"].get("county_id") == info["county"] or info["county"] in a.get("counties", [])
                  or any(snap.facilities.get(n, {}).get("county") == info["county"] for n in a.get("nodes", []))]
    elif info["scope"] == "facility":
        alarms = [a for a in alarms if info["id"] in a.get("nodes", [])]
    ctx["anomalies"] = alarms[:12]
    ctx["outcome_scope"] = ("regional outcomes for patients living in " + (info.get("county_name") or "")) if info["scope"] == "facility" else info["name"]
    return ctx


def facts_for_llm(ctx: dict) -> dict:
    """Compact, rounded, tagged facts (what the LLM is allowed to talk about)."""
    def r(x, k=1):
        return None if x is None else round(x, k)

    def comp(c):
        return {"pct_within_42d": r(100 * c["p_within_target"]) if c.get("p_within_target") is not None else None,
                "ci_pct": [r(100 * x) for x in c["ci"]] if c.get("ci") and None not in c["ci"] else None,
                "patients_exceeding_per_year": r(c.get("exceeding_per_year"), 0),
                "pct_fit_to_colonoscopy_within_28d": r(100 * c["fit_to_colonoscopy_p_within"]) if c.get("fit_to_colonoscopy_p_within") else None}

    def dly(d):
        return {"median_referral_to_treatment_days": r(d.get("median_days")), "p90_days": r(d.get("p90_days")),
                "stalled_step_mean_days": r(d.get("stall_days_mean")),
                "slowest_stages_days": {s["label"]: r(s["mean_days"]) for s in sorted(d.get("stages", []), key=lambda s: -(s["mean_days"] or 0))[:3]}}
    info = ctx["context"]
    f = {"context": info["name"], "scope": info["scope"],
         "outcome_figures_refer_to": (f"patients living in {info['county_name']} (the hospital's county); hospital-level "
                                      "figures are only the bottleneck queues") if info["scope"] == "facility" else info["name"],
         "fit_to_colonoscopy_target_days": 28,
         "SIMULATED_current": {**comp(ctx["compliance"]), **dly(ctx["delays"])},
         "SIMULATED_national": comp(ctx["national_compliance"]),
         "SIMULATED_bottlenecks": [{"hospital": s["facility"], "resource": s["capability_label"],
                                    "utilisation_pct": r(100 * (s["utilisation"] or 0), 0), "mean_queue_days": r(s["mean_wait_days"])}
                                   for s in ctx["bottlenecks"][:3]],
         "OBSERVED_statistics": {k: f"{v['value']} ({v['year']}, {v['source']})" for k, v in ctx["observed"].items()},
         "SYNTHETIC_live_operations": {k: v for k, v in ctx["live"].items() if k != "tag"},
         "reference_target": "42 days referral-to-treatment (Finnish 6-week recommendation, FI-1)"}
    b = ctx["compliance"].get("behaviour") or {}
    f["SIMULATED_patient_behaviour"] = {"pct_appointments_missed": r(100 * b["dna_rate"]) if b.get("dna_rate") is not None else None,
                                        "pct_alternative_offers_declined": r(100 * b["alt_decline_rate"]) if b.get("alt_offers_per_week") else None,
                                        "pct_lost_to_follow_up": r(100 * b["lost_rate"]) if b.get("lost_rate") is not None else None}
    f["SIMULATED_network_alarms"] = [a["title"] for a in ctx.get("anomalies", [])[:3]]
    if ctx.get("ranking"):
        f["SIMULATED_worst_counties"] = [{"county": x["name"], "pct_within_42d": r(100 * x["p_within_target"])} for x in ctx["ranking"][:3]]
        f["SIMULATED_best_counties"] = [{"county": x["name"], "pct_within_42d": r(100 * x["p_within_target"])} for x in ctx["ranking"][-2:]]
    if ctx.get("base"):
        f["SIMULATED_before_intervention"] = {**comp(ctx["base"]["compliance"]), **dly(ctx["base"]["delays"]),
                                              "national": comp(ctx["base"]["national_compliance"])}
    return f


def fallback_summary(ctx: dict) -> str:
    c, d, info = ctx["compliance"], ctx["delays"], ctx["context"]
    lines = []
    who = info.get("county_name") or info["name"]
    if info["scope"] == "facility":
        lines.append(f"{info['name']} serves {who}; outcome figures below are for that county's patients.")
    if c.get("p_within_target") is not None:
        ci = c.get("ci") or [None, None]
        lines.append(f"In the simulated {('modified' if ctx.get('base') else 'baseline')} scenario, {100 * c['p_within_target']:.1f} % of "
                     f"{who} patients start treatment within the 42-day reference target"
                     + (f" (95 % CI {100 * ci[0]:.1f}–{100 * ci[1]:.1f} %)." if None not in ci else "."))
    if ctx.get("base"):
        b = ctx["base"]["compliance"].get("p_within_target")
        if b is not None and c.get("p_within_target") is not None:
            lines.append(f"Before the intervention it was {100 * b:.1f} % ({100 * (c['p_within_target'] - b):+.1f} percentage points).")
    if d.get("median_days"):
        lines.append(f"Median referral-to-treatment time is {d['median_days']:.0f} days (90th percentile {d['p90_days']:.0f} d).")
    top = ctx["primary_issue"].get("top_station")
    if top:
        lines.append(f"The most constrained resource is {top['capability_label']} at {top['facility']}: "
                     f"{100 * (top['utilisation'] or 0):.0f} % utilised with a mean queue of {top['mean_wait_days']:.1f} days.")
    if d.get("stall_days_mean"):
        lines.append(f"Stalled steps (lost referrals, missing follow-ups) wait {d['stall_days_mean']:.0f} days on average before anyone acts.")
    if ctx.get("ranking"):
        w = ctx["ranking"][:2]
        lines.append("Weakest regions: " + ", ".join(f"{x['name']} ({100 * x['p_within_target']:.0f} %)" for x in w) + ".")
    obs = ctx["observed"]
    k = "Waited > 6 months for specialist care, per 10 000"
    if k in obs:
        lines.append(f"Observed context: {obs[k]['value']:.0f} per 10 000 residents waited over 6 months for specialist care "
                     f"({obs[k]['year']}, all specialties, THL).")
    lines.append("All outcome figures are simulation outputs on synthetic capacity, not observed Finnish statistics.")
    return " ".join(lines)
