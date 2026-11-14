"""Synthetic patient population + demo users + hand-crafted demo stories.

    python -m backend.generator.population --n 600 --seed 42      # wipes operational data, regenerates

Every patient/event is SYNTHETIC (actor_role='generator'). Transitions are validated with the same pure
engine as the API. Regional speed is tied to REAL access indicators (Sotkanet, DERIVED multipliers):
  primary-care delay  x m_pc(county)   from [6411] % GP non-urgent visits waiting > 7 d
  specialist delay    x m_sp(county)   from [3336] waited > 6 months per 10k (all specialties)
Stage durations are lognormal with documented median assumptions (HYPOTHESIS) -> see DURATIONS.
"""
from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from psycopg.rows import dict_row

from backend.db.conn import connect
from backend.pathways.engine import check_transition, evaluate
from backend.pathways.pack import get_pack
from backend.services.pathways import ensure_pack, now as db_now

PACK = get_pack("colorectal")

from backend.sim.params import DURATIONS, MULT_HI, MULT_LO  # shared with the SimPy simulator
# branch probabilities (HYPOTHESIS; screening sample is ENRICHED for FIT-positives so the demo has flow)
BRANCH = {
    "SCREENING_INVITED": [("FIT_RETURNED", 0.735), ("SCREENING_NO_RESPONSE", 0.265)],   # participation OBSERVED (FCR 2024)
    "FIT_RETURNED": [("FIT_POSITIVE", 0.60), ("FIT_NEGATIVE", 0.40)],                   # enriched (true rate 4.5 %)
    "FIT_POSITIVE": [("NURSE_ASSESSMENT", 0.97), ("DECLINED", 0.03)],
    "NURSE_ASSESSMENT": [("COLONOSCOPY", 0.93), ("ALT_EXAMINATION", 0.04), ("DECLINED", 0.03)],
    "SYMPTOMS_REPORTED": [("GP_VISIT", 1.0)],
    "GP_VISIT": [("REFERRAL_SENT", 0.85), ("CANCER_EXCLUDED", 0.15)],
    "REFERRAL_SENT": [("REFERRAL_RECEIVED", 1.0)],
    "REFERRAL_RECEIVED": [("SPECIALIST_ASSESSMENT", 0.4), ("COLONOSCOPY", 0.6)],
    "SPECIALIST_ASSESSMENT": [("COLONOSCOPY", 0.85), ("ALT_EXAMINATION", 0.05), ("CANCER_EXCLUDED", 0.10)],
    "COLONOSCOPY": [("HISTOLOGY", 0.55), ("CANCER_EXCLUDED", 0.30), ("POLYP_SURVEILLANCE", 0.15)],
    "ALT_EXAMINATION": [("COLONOSCOPY", 0.6), ("CANCER_EXCLUDED", 0.4)],
    "HISTOLOGY": [("CANCER_CONFIRMED", 0.35), ("POLYP_SURVEILLANCE", 0.45), ("CANCER_EXCLUDED", 0.20)],
    "CANCER_CONFIRMED": [("STAGING", 1.0)],
    "STAGING": [("MDT_DECISION", 1.0)],
    "MDT_DECISION": [("SURGERY", 0.62), ("NEOADJUVANT_THERAPY", 0.15), ("SYSTEMIC_THERAPY", 0.15), ("PALLIATIVE_CARE", 0.08)],
    "NEOADJUVANT_THERAPY": [("SURGERY", 1.0)],
    "SURGERY": [("ADJUVANT_THERAPY", 0.35), ("FOLLOW_UP", 0.65)],
    "SYSTEMIC_THERAPY": [("FOLLOW_UP", 0.6), ("PALLIATIVE_CARE", 0.3), ("SURGERY", 0.1)],
    "ADJUVANT_THERAPY": [("FOLLOW_UP", 1.0)],
}
CRACK_STAGES = {"FIT_POSITIVE": 0.05, "REFERRAL_SENT": 0.04, "HISTOLOGY": 0.04, "NURSE_ASSESSMENT": 0.03}
# which facility type records each stage
WHERE = {"primary": "hc", "screening_programme": None}
CAP_FOR_STAGE = {"NURSE_ASSESSMENT": "colonoscopy", "REFERRAL_RECEIVED": "colonoscopy", "SPECIALIST_ASSESSMENT": "colonoscopy",
                 "CANCER_CONFIRMED": "pathology", "MDT_DECISION": "mdt_crc", "FOLLOW_UP": "oncology"}

FIRST = ["Aino", "Eino", "Helmi", "Väinö", "Sanni", "Onni", "Kerttu", "Toivo", "Lilja", "Veikko", "Maija", "Pentti",
         "Raili", "Seppo", "Tuula", "Jorma", "Anneli", "Kalevi", "Ritva", "Markku", "Leena", "Juhani", "Pirjo", "Timo"]
LAST = ["Virtanen", "Korhonen", "Mäkinen", "Nieminen", "Mäkelä", "Hämäläinen", "Laine", "Heikkinen", "Koskinen",
        "Järvinen", "Lehtonen", "Lehtinen", "Saarinen", "Salminen", "Heinonen", "Niemi", "Heikkilä", "Kinnunen"]


@dataclass
class Muni:
    id: str
    county: str
    pop: float
    lon: float
    lat: float
    hc: str | None
    nearest: dict          # capability -> facility id


def load_world(conn):
    cur = conn.cursor(row_factory=dict_row)
    munis = cur.execute("""
        SELECT m.id, m.parent_id AS county, coalesce(m.population,0) AS pop,
               ST_X(m.centroid::geometry) AS lon, ST_Y(m.centroid::geometry) AS lat,
               (SELECT f.id FROM facility f WHERE f.kind='health_center' ORDER BY f.geom <-> m.centroid LIMIT 1) AS hc
        FROM region m WHERE m.kind='municipality' AND m.parent_id <> 'hva-956'""").fetchall()
    near = cur.execute("""
        SELECT DISTINCT ON (t.origin_region_id, fc.capability) t.origin_region_id AS m, fc.capability AS cap, t.facility_id AS f
        FROM travel_time t JOIN facility_capability fc ON fc.facility_id=t.facility_id
        ORDER BY t.origin_region_id, fc.capability, t.minutes""").fetchall()
    nmap: dict = {}
    for r in near:
        nmap.setdefault(r["m"], {})[r["cap"]] = r["f"]
    out = [Muni(m["id"], m["county"], float(m["pop"]), m["lon"], m["lat"], m["hc"], nmap.get(m["id"], {})) for m in munis]
    # county multipliers from REAL indicators (latest year per county)
    def latest(ind):
        rows = conn.execute("""SELECT DISTINCT ON (region_id) region_id, value FROM indicator_value
                               WHERE indicator_id=%s AND gender='total' AND region_id LIKE 'hva-%%'
                               ORDER BY region_id, year DESC""", (f"sotkanet:{ind}",)).fetchall()
        return dict(rows)
    def mult(vals, lo=MULT_LO, hi=MULT_HI):
        if not vals:
            return {}
        a, b = min(vals.values()), max(vals.values())
        return {k: lo + (hi - lo) * ((v - a) / (b - a) if b > a else 0.5) for k, v in vals.items()}
    m_pc, m_sp = mult(latest(6411)), mult(latest(3336))
    return out, m_pc, m_sp


def ln(rng, median, sigma=0.5):
    return float(rng.lognormal(np.log(max(median, 0.05)), sigma))


def facility_for(stage, muni: Muni):
    st = PACK.stages[stage]
    if st.level == "primary":
        return muni.hc
    if st.level == "screening_programme":
        return None
    cap = st.capability or CAP_FOR_STAGE.get(stage) or "colonoscopy"
    return muni.nearest.get(cap) or muni.nearest.get("colonoscopy")


def simulate_one(rng, route, start: datetime, muni: Muni, m_pc, m_sp, as_of: datetime, crack_ok=True):
    """Walk the pack from the entry stage until the next event would be in the future. Returns [(stage, t, fac)]."""
    stage = PACK.entry_routes[route].first_stage
    t = start
    out = [(stage, t, facility_for(stage, muni))]
    while not PACK.stages[stage].terminal:
        if crack_ok and stage in CRACK_STAGES and rng.random() < CRACK_STAGES[stage]:
            break                                   # a crack: nothing ever recorded after this
        med, scale = DURATIONS.get(stage, (5, None))
        k = m_pc.get(muni.county, 1.0) if scale == "pc" else m_sp.get(muni.county, 1.0) if scale == "sp" else 1.0
        dt = ln(rng, med * k)
        nxt_t = t + timedelta(days=dt)
        if nxt_t > as_of:
            break
        opts = BRANCH[stage]
        stage = opts[rng.choice(len(opts), p=np.array([p for _, p in opts]) / sum(p for _, p in opts))][0]
        t = nxt_t
        out.append((stage, t, facility_for(stage, muni)))
    return out


def wipe_operational(conn):
    # TRUNCATE bypasses the row-level append-only trigger (deliberate: only used to reset SYNTHETIC demo data)
    conn.execute("TRUNCATE alert, appointment, pathway_clock, event, pathway, app_user, patient CASCADE")


def write_pathways(conn, items, as_of):
    """items: list of dict(patient, route, steps). Validates every transition with the engine, then bulk-inserts."""
    pat_rows, pw_rows, ev_rows, clock_rows, alert_rows, pw_upd = [], [], [], [], [], []
    for it in items:
        p = it["patient"]
        pat_rows.append((p["id"], p["name"], p["birth_year"], p["sex"], p["muni"], f"POINT({p['lon']} {p['lat']})"))
        pid = uuid.uuid4()
        steps = it["steps"]
        pw_rows.append((pid, p["id"], PACK.id, PACK.version, it["route"], steps[0][1]))
        cur, last_at, events = None, None, []
        for i, (stage, t, fac) in enumerate(steps, start=1):
            check_transition(PACK, it["route"], cur, stage, "generator", last_at, t)
            e = {"id": uuid.uuid4(), "seq": i, "type": "STAGE_ENTERED", "stage": stage, "occurred_at": t, "facility_id": fac}
            events.append(e)
            ev_rows.append((e["id"], pid, i, "STAGE_ENTERED", stage, fac, t))
            cur, last_at = stage, t
        st = evaluate(PACK, it["route"], events, as_of)
        pw_upd.append((st.current_stage, "closed" if st.closed else "active", steps[-1][1] if st.closed else None, len(steps), pid))
        for c in st.clocks:
            if c.started_at:
                clock_rows.append((pid, c.id, c.started_at, c.stopped_at))
        for a in st.alerts:
            alert_rows.append((pid, a.rule_id, a.severity, a.message, a.responsible_facility_id, a.responsible_role,
                               a.due_at, json.dumps(a.evidence, default=str), as_of))
        it["pathway_id"] = pid
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO patient(id,display_name,birth_year,sex,home_region_id,home_geom)
                           VALUES (%s,%s,%s,%s,%s,ST_GeogFromText(%s))""", pat_rows)
        cur.executemany("""INSERT INTO pathway(id,patient_id,pack_id,pack_version,entry_route,opened_at)
                           VALUES (%s,%s,%s,%s,%s,%s)""", pw_rows)
        cur.executemany("""INSERT INTO event(id,pathway_id,seq,type,stage,facility_id,actor_role,occurred_at)
                           VALUES (%s,%s,%s,%s,%s,%s,'generator',%s)""", ev_rows)
        cur.executemany("UPDATE pathway SET current_stage=%s, status=%s, closed_at=%s, last_seq=%s WHERE id=%s", pw_upd)
        cur.executemany("INSERT INTO pathway_clock(pathway_id,clock_id,started_at,stopped_at) VALUES (%s,%s,%s,%s)", clock_rows)
        cur.executemany("""INSERT INTO alert(pathway_id,rule_id,severity,message,responsible_facility_id,responsible_role,
                                             due_at,evidence,raised_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""", alert_rows)
    return len(ev_rows), len(alert_rows)


def make_patient(rng, i, muni: Muni, prefix="S"):
    return {"id": f"{prefix}-{i:04d}", "name": f"{rng.choice(FIRST)} {rng.choice(LAST)[0]}.",
            "birth_year": int(rng.integers(1951, 1975)), "sex": str(rng.choice(["F", "M"])), "muni": muni.id,
            "lon": muni.lon + float(rng.normal(0, 0.03)), "lat": muni.lat + float(rng.normal(0, 0.015))}


def demo_stories(rng, munis_by_id, as_of):
    """Hand-crafted pathways for the live demo (still SYNTHETIC), each showing one kind of 'crack'."""
    d = lambda days: as_of - timedelta(days=days)
    kuopio, joensuu, turku, helsinki = (munis_by_id[k] for k in ("kunta-297", "kunta-167", "kunta-853", "kunta-091"))
    stories = [
        ("D-0001", "Matti Virtanen", 1958, "M", joensuu, "screening",     # positive FIT, no colonoscopy for 33 days
         [("SCREENING_INVITED", 60), ("FIT_RETURNED", 45), ("FIT_POSITIVE", 40), ("NURSE_ASSESSMENT", 33)]),
        ("D-0002", "Aino Korhonen", 1962, "F", kuopio, "symptomatic",     # referral sent, never received (lost)
         [("SYMPTOMS_REPORTED", 20), ("GP_VISIT", 16), ("REFERRAL_SENT", 15)]),
        ("D-0003", "Eino Mäkinen", 1955, "M", turku, "symptomatic",       # on track
         [("SYMPTOMS_REPORTED", 30), ("GP_VISIT", 27), ("REFERRAL_SENT", 27), ("REFERRAL_RECEIVED", 26),
          ("COLONOSCOPY", 15), ("HISTOLOGY", 15), ("CANCER_CONFIRMED", 10), ("STAGING", 7), ("MDT_DECISION", 2)]),
        ("D-0004", "Helmi Laine", 1960, "F", helsinki, "screening",       # histology result pending too long
         [("SCREENING_INVITED", 50), ("FIT_RETURNED", 38), ("FIT_POSITIVE", 35), ("NURSE_ASSESSMENT", 30),
          ("COLONOSCOPY", 19), ("HISTOLOGY", 19)]),
    ]
    items = []
    for pid, name, by, sex, m, route, steps in stories:
        p = {"id": pid, "name": name, "birth_year": by, "sex": sex, "muni": m.id, "lon": m.lon, "lat": m.lat}
        items.append({"patient": p, "route": route, "steps": [(s, d(days), facility_for(s, m)) for s, days in steps]})
    return items


def seed_users(conn, stories):
    cur = conn.cursor()
    fac = lambda q: conn.execute(q).fetchone()[0]
    docs = [
        ("dr-kys-endo", "Dr. Aino Laine (gastroenterology)", "fi-hosp-kys", "gastroenterologist"),
        ("dr-pkks-surg", "Dr. Juha Nieminen (surgery)", "fi-hosp-pkks", "colorectal_surgeon"),
        ("dr-tyks-onc", "Dr. Sari Lehtonen (oncology)", "fi-hosp-tyks", "oncologist"),
        ("dr-hus-endo", "Dr. Mikko Salminen (endoscopy)", "fi-hosp-hus", "gastroenterologist"),
        ("nurse-pkks-scr", "Nurse Liisa Heikkinen (screening)", "fi-hosp-pkks", "screening_nurse"),
    ]
    # GPs at the health centres that the demo stories actually used
    for st in stories:
        hc = next((f for s, _, f in st["steps"] if f and f.startswith("fi-hc")), None)
        if hc and not any(d[2] == hc for d in docs):
            name = conn.execute("SELECT name FROM facility WHERE id=%s", (hc,)).fetchone()[0]
            docs.append((f"gp-{hc[-8:]}", f"Dr. GP ({name})", hc, "gp"))
    cur.executemany("INSERT INTO app_user(id,role,display_name,facility_id,staff_role) VALUES (%s,'doctor',%s,%s,%s)", docs)
    pats = [(f"pat-{s['patient']['id'].lower()}", s["patient"]["name"], s["patient"]["id"]) for s in stories]
    cur.executemany("INSERT INTO app_user(id,role,display_name,patient_id) VALUES (%s,'patient',%s,%s)", pats)
    return docs, pats


def main(n: int, seed: int, days_back: int = 150):
    rng = np.random.default_rng(seed)
    with connect() as conn:
        with conn.transaction():
            ensure_pack(conn, PACK)
            as_of = db_now(conn)
            munis, m_pc, m_sp = load_world(conn)
            w = np.array([m.pop for m in munis]); w = w / w.sum()
            items = []
            for i in range(n):
                m = munis[rng.choice(len(munis), p=w)]
                route = "screening" if rng.random() < 0.45 else "symptomatic"
                start = as_of - timedelta(days=float(rng.uniform(0, days_back)))
                items.append({"patient": make_patient(rng, i + 1, m), "route": route,
                              "steps": simulate_one(rng, route, start, m, m_pc, m_sp, as_of)})
            stories = demo_stories(rng, {m.id: m for m in munis}, as_of)
            wipe_operational(conn)
            ne, na = write_pathways(conn, items + stories, as_of)
            docs, pats = seed_users(conn, stories)
        print(f"generated {n} pathways + {len(stories)} demo stories, {ne} events, {na} open alerts, "
              f"{len(docs)} doctors, {len(pats)} patient logins (as_of {as_of:%Y-%m-%d})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    main(a.n, a.seed)
