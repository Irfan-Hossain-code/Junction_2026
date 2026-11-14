"""Scenario snapshot: a frozen, pure-data view of the network that the simulator and optimiser operate on.

Built read-only from the DB; scenarios are applied as pure transformations (never touching operational data).
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field

from psycopg.rows import dict_row

from backend.sim import params as P

CAPS = ["colonoscopy", "pathology", "ct_imaging", "mdt_crc", "crc_surgery", "oncology", "radiotherapy"]


@dataclass
class Snapshot:
    munis: dict            # muni_id -> {county, pop, name}
    counties: dict         # county_id -> {name, yta, m_pc, m_sp, cap_factor, pop}
    facilities: dict       # fac_id -> {name, kind, county, yta, lon, lat}
    capacity: dict         # "fac|cap" -> per week (effective, after calibration)
    routing: dict          # "muni|cap" -> [[fac, share], ...]
    travel: dict           # "muni|fac" -> minutes
    arrivals_per_week: dict  # route -> national weekly rate
    screening_factor: dict = field(default_factory=dict)   # county -> participation multiplier
    monitoring: bool = False
    notes: list = field(default_factory=list)
    home: dict = field(default_factory=dict)       # "muni|cap" -> nearest PUBLIC facility (where a decliner falls back)
    reminders: dict = field(default_factory=dict)  # county -> True (proactive reminders / follow-up calls)
    transport: dict = field(default_factory=dict)  # county -> True (transport support for distant appointments)
    purchases: list = field(default_factory=list)  # private purchasing contracts (for cost accounting)

    def key(self, a, b):
        return f"{a}|{b}"

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()[:16]


def _mult(vals: dict, lo=P.MULT_LO, hi=P.MULT_HI) -> dict:
    if not vals:
        return {}
    a, b = min(vals.values()), max(vals.values())
    return {k: lo + (hi - lo) * ((v - a) / (b - a) if b > a else 0.5) for k, v in vals.items()}


def build_snapshot(conn) -> Snapshot:
    cur = conn.cursor(row_factory=dict_row)
    munis = {r["id"]: {"county": r["parent_id"], "pop": float(r["population"] or 0), "name": r["name_fi"]}
             for r in cur.execute("""SELECT id, parent_id, population, name_fi FROM region
                                     WHERE kind='municipality' AND parent_id <> 'hva-956'""")}

    def latest(ind):
        return dict(conn.execute("""SELECT DISTINCT ON (region_id) region_id, value FROM indicator_value
                                    WHERE indicator_id=%s AND gender='total' AND region_id LIKE 'hva-%%'
                                    ORDER BY region_id, year DESC""", (f"sotkanet:{ind}",)).fetchall())
    m_pc, m_sp = _mult(latest(6411)), _mult(latest(3336))
    counties = {}
    for r in cur.execute("SELECT id, name_en, parent_id, population FROM region WHERE kind='wellbeing_county' AND id <> 'hva-956'"):
        sp = m_sp.get(r["id"], 1.0)
        # heuristic calibration (CALIBRATED-weak): counties with a worse real specialist backlog [3336]
        # get up to 8 % more .. 8 % less effective capacity than the demand-matched synthetic baseline
        cap_factor = 1.08 - 0.16 * (sp - P.MULT_LO) / (P.MULT_HI - P.MULT_LO)
        counties[r["id"]] = {"name": r["name_en"], "yta": r["parent_id"], "m_pc": m_pc.get(r["id"], 1.0),
                             "m_sp": sp, "cap_factor": round(cap_factor, 3), "pop": float(r["population"] or 0)}
    # hospitals + private providers that have a specialist capability (only routed when a scenario buys capacity)
    facilities = {r["id"]: {**{k: r[k] for k in ("name", "kind", "lon", "lat")}, "county": r["region_id"], "yta": r["yta_id"]}
                  for r in cur.execute("""SELECT id, name, kind, region_id, yta_id, ST_X(geom::geometry) lon, ST_Y(geom::geometry) lat
                                          FROM facility f WHERE kind NOT IN ('health_center','private_clinic')
                                             OR (kind='private_clinic' AND EXISTS (SELECT 1 FROM facility_capability c
                                                 WHERE c.facility_id=f.id AND c.capability NOT IN ('primary_care','private_first_contact')))""")}
    # nearest health centre per municipality (first contact for symptomatic patients)
    for r in conn.execute("""SELECT m.id, (SELECT f.id FROM facility f WHERE f.kind='health_center'
                                           ORDER BY f.geom <-> m.centroid LIMIT 1)
                             FROM region m WHERE m.kind='municipality'"""):
        if r[0] in munis:
            munis[r[0]]["hc"] = r[1]
    capacity = {}
    for r in cur.execute("""SELECT facility_id, capability, (per_week_low+per_week_high)/2 AS mid FROM facility_capacity"""):
        f = facilities.get(r["facility_id"])
        if not f:
            continue
        cf = counties.get(f["county"], {}).get("cap_factor", 1.0)
        dna_plan = 1 / (1 - P.EXPECTED_DNA_FOR_PLANNING) if r["capability"] == "colonoscopy" else 1.0
        capacity[f"{r['facility_id']}|{r['capability']}"] = round(float(r["mid"]) * cf * dna_plan, 3)
    travel = {f"{r[0]}|{r[1]}": float(r[2]) for r in conn.execute("SELECT origin_region_id, facility_id, minutes FROM travel_time")}
    routing, home = {}, {}
    for m in munis:
        for cap in CAPS:
            best = min(((t, f) for f in facilities if f"{f}|{cap}" in capacity and facilities[f]["kind"] != "private_clinic"
                        for t in [travel.get(f"{m}|{f}")] if t is not None), default=None)
            if best:
                routing[f"{m}|{cap}"] = [[best[1], 1.0]]
                home[f"{m}|{cap}"] = best[1]
    weekly_scr = P.SCREENING_COLONOSCOPIES_PER_YEAR / 52.0
    return Snapshot(munis=munis, counties=counties, facilities=facilities, capacity=capacity, routing=routing, home=home,
                    travel=travel, arrivals_per_week={"screening": weekly_scr,
                                                      "symptomatic": weekly_scr * P.SYMPTOMATIC_TO_SCREENING_RATIO},
                    notes=["baseline: nearest capable hospital for every municipality",
                           "capacity: SYNTHETIC demand-matched x county factor from real [3336] backlog (heuristic)"])


def apply_scenario(snap: Snapshot, params: dict) -> Snapshot:
    """Pure: returns a modified copy. params schema:
    {
      "capacity": [{"facility_id": "...", "capability": "colonoscopy", "factor": 1.2} | {..., "add_per_week": 10}],
      "routing":  [{"from_county": "hva-972" | "from_municipality": "kunta-167", "capability": "colonoscopy",
                    "to_facility": "fi-hosp-kys", "share": 0.3}],
      "screening_factor": {"hva-972": 1.1} | {"*": 1.05},
      "arrival_factor": 1.0,
      "monitoring": true          # CancerFlow alerting: stalls detected at dwell threshold
    }"""
    s = copy.deepcopy(snap)
    for c in params.get("capacity", []):
        k = f"{c['facility_id']}|{c['capability']}"
        base = s.capacity.get(k, 0.0)
        s.capacity[k] = max(0.0, base * float(c.get("factor", 1.0)) + float(c.get("add_per_week", 0.0)))
        s.notes.append(f"capacity {k}: {base:.1f} -> {s.capacity[k]:.1f}/wk")
    for r in params.get("routing", []):
        targets = [m for m in s.munis if (r.get("from_municipality") == m) or (r.get("from_county") == s.munis[m]["county"])]
        cap, to, share = r["capability"], r["to_facility"], float(r["share"])
        if f"{to}|{cap}" not in s.capacity:
            raise ValueError(f"{to} has no {cap} capacity")
        for m in targets:
            scaled = {f: w * (1 - share) for f, w in s.routing.get(f"{m}|{cap}", [])}
            scaled[to] = scaled.get(to, 0.0) + share          # shares still sum to 1
            s.routing[f"{m}|{cap}"] = [[f, w] for f, w in scaled.items() if w > 1e-9]
        s.notes.append(f"route {share:.0%} of {cap} from {r.get('from_county') or r.get('from_municipality')} -> {to}")
    for k, shares in (params.get("routing_set") or {}).items():     # full override (optimiser output)
        if shares:
            s.routing[k] = [[f, float(w)] for f, w in shares]
    if params.get("routing_set"):
        s.notes.append(f"optimised routing applied to {len(params['routing_set'])} municipality-capability pairs")
    def per_county(v):
        if v is True:
            return {c: True for c in s.counties}
        if isinstance(v, dict):
            return {c: True for c in s.counties if v.get(c) or v.get("*")}
        if isinstance(v, list):
            return {c: True for c in v if c in s.counties}
        return {}
    if params.get("reminders"):
        s.reminders = per_county(params["reminders"])
        s.notes.append(f"proactive reminders/follow-up calls in {len(s.reminders)} counties")
    if params.get("transport_support"):
        s.transport = per_county(params["transport_support"])
        s.notes.append(f"transport support for distant appointments in {len(s.transport)} counties")
    for pp in params.get("private_purchase", []) or []:
        fid, cap, wk = pp["facility_id"], pp.get("capability", "colonoscopy"), float(pp["per_week"])
        if fid not in s.facilities:
            raise ValueError(f"unknown private facility {fid}")
        elig = [c for c in pp.get("eligible_counties", []) if c in s.counties]
        if not elig:
            raise ValueError("private_purchase needs eligible_counties (private providers are never eligible by default)")
        k = f"{fid}|{cap}"
        s.capacity[k] = s.capacity.get(k, 0.0) + wk
        # share of each eligible municipality's demand OFFERED the private slot (patients may still decline)
        munis = [m for m, v in s.munis.items() if v["county"] in elig]
        from backend.opt.allocate import demand as _demand   # local import (allocate imports snapshot)
        d = _demand(s, cap)
        tot = sum(d[m] for m in munis) or 1.0
        share = float(pp.get("offer_share", min(0.9, wk / tot)))
        for m in munis:
            cur = {f: w * (1 - share) for f, w in s.routing.get(f"{m}|{cap}", [])}
            cur[fid] = cur.get(fid, 0.0) + share
            s.routing[f"{m}|{cap}"] = [[f, w] for f, w in cur.items() if w > 1e-9]
        s.purchases.append({"facility_id": fid, "capability": cap, "per_week": wk, "eligible_counties": elig,
                            "offer_share": round(share, 3), "cost_per_procedure": float(pp.get("cost_per_procedure", P.PRIVATE_COST_PER_COLONOSCOPY_EUR))})
        s.notes.append(f"private purchase: {wk:g} {cap}/wk at {s.facilities[fid]['name']} for {', '.join(elig)} (offered to {share:.0%})")
    sf = params.get("screening_factor") or {}
    s.screening_factor = {c: float(sf.get(c, sf.get("*", 1.0))) for c in s.counties}
    af = float(params.get("arrival_factor", 1.0))
    s.arrivals_per_week = {k: v * af for k, v in s.arrivals_per_week.items()}
    s.monitoring = bool(params.get("monitoring", False))
    if s.monitoring:
        s.notes.append("CancerFlow monitoring ON: stalls detected at dwell threshold + response time")
    return s
