"""The East Finland operations world for the final demo (all SYNTHETIC, deterministic).

Real places (OSM hospitals), assumed resources and slot capacity, background occupancy, and eight named patient
agents whose colonoscopies sit on North Karelia Central Hospital's Endoscopy Unit 2 in the disruption window:
Emma (the story patient) plus seven others competing for the same capacity.

Why colonoscopy, not MRI: in the colorectal pathway colonoscopy is THE diagnostic step (pelvic MRI only stages a
confirmed rectal cancer). Endoscopy capacity is what the brain and the national simulator model, so the disruption
hits real modelled state. MRI still appears later in Emma's story as her staging examination.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

FI = timezone(timedelta(hours=2))          # Finnish time in November (EET), fixed so no tzdata dependency
DAYS = 35                                   # slot calendar length (story days 0..34)

FAC = {"PKKS": "fi-hosp-pkks", "KYS": "fi-hosp-kys", "SAV": "fi-hosp-savonlinna", "MKS": "fi-hosp-mks",
       "NOVA": "fi-hosp-nova"}
FAC_COORD = {"fi-hosp-pkks": (29.7786, 62.5917), "fi-hosp-kys": (27.6479, 62.8977), "fi-hosp-savonlinna": (28.8524, 61.8714),
             "fi-hosp-mks": (27.2782, 61.6918), "fi-hosp-nova": (25.7040, 62.2303)}
FAC_SHORT = {"fi-hosp-pkks": "Joensuu (PKKS)", "fi-hosp-kys": "Kuopio (KYS)", "fi-hosp-savonlinna": "Savonlinna",
             "fi-hosp-mks": "Mikkeli", "fi-hosp-nova": "Jyväskylä (Nova)"}
FAC_COUNTY = {"fi-hosp-pkks": "North Karelia", "fi-hosp-kys": "North Savo", "fi-hosp-savonlinna": "South Savo",
              "fi-hosp-mks": "South Savo", "fi-hosp-nova": "Central Finland"}
HOME_FACILITY = "fi-hosp-pkks"
GP_FACILITY = "fi-hc-osm-n10101862641"      # Niinivaaran terveysasema, Joensuu (OSM)

# id, facility, capability, name, slots/day, background occupancy by day: list of (until_day, share)
RESOURCES = [
    ("res-pkks-endo1", "fi-hosp-pkks", "colonoscopy", "Endoscopy Unit 1", 4, [(14, 1.0), (DAYS, 0.75)]),
    ("res-pkks-endo2", "fi-hosp-pkks", "colonoscopy", "Endoscopy Unit 2", 4, [(12, 1.0), (DAYS, 0.7)]),
    ("res-kys-endo", "fi-hosp-kys", "colonoscopy", "KYS Endoscopy", 6, [(12, 0.84), (DAYS, 0.7)]),
    ("res-sav-endo", "fi-hosp-savonlinna", "colonoscopy", "Savonlinna Endoscopy", 2, [(DAYS, 0.5)]),
    ("res-mks-endo", "fi-hosp-mks", "colonoscopy", "Mikkeli Endoscopy", 3, [(DAYS, 0.45)]),
    ("res-nova-endo", "fi-hosp-nova", "colonoscopy", "Nova Endoscopy", 4, [(DAYS, 0.4)]),
    ("res-pkks-mri", "fi-hosp-pkks", "mri", "MRI Unit (staging)", 6, [(DAYS, 0.6)]),
]
DISRUPTED_RESOURCE = "res-pkks-endo2"
STORY = {                                    # story day offsets (from T0 = day 0, 00:00 Finnish time)
    "symptoms": (0, 9.0), "gp": (1, 10.0), "referral_received": (2, 9.0), "specialist": (4, 13.0),
    "colonoscopy": (7, 0), "disruption": (6, 8.0), "disruption_end": (8, 8.0),
}

# Persona patients. Behaviour parameters follow the national agent archetypes (backend/sim/params.py);
# constraints are their persistent circumstances. Clinical priority comes from the referral, never the persona.
PERSONAS = [
    dict(pid="OPS-EMMA", user="pat-emma", name="Emma Nieminen", birth_year=1980, sex="F", muni="Joensuu", lonlat=(29.745, 62.603),
         archetype="engaged_flexible", priority="urgent", referral_days_ago=None, slot=None,
         occupation="physiotherapist", household="two children (8 and 11), partner works shifts", transport="own car",
         constraints=[{"kind": "childcare", "text": "needs to arrange school pick-up on weekdays", "travel_limit_min": 180}],
         tone="practical, a little anxious, polite", p_accept_alt=0.8, travel_aversion=0.6, max_travel_min=180, p_attend=0.95),
    dict(pid="OPS-0001", name="Pekka Hyvönen", birth_year=1955, sex="M", muni="Lieksa", lonlat=(30.024, 63.318),
         archetype="travel_constrained", priority="urgent", referral_days_ago=22, slot=(6, 0),
         occupation="retired electrician", household="cares for his wife, who has dementia", transport="drives short distances only",
         constraints=[{"kind": "caregiver", "text": "cannot leave his wife alone overnight or for a long day", "travel_limit_min": 100}],
         tone="gruff but kind, few words", p_accept_alt=0.35, travel_aversion=2.0, max_travel_min=100, p_attend=0.93),
    dict(pid="OPS-0002", name="Sari Karjalainen", birth_year=1974, sex="F", muni="Kitee", lonlat=(30.138, 62.098),
         archetype="engaged_flexible", priority="standard", referral_days_ago=15, slot=(6, 1),
         occupation="nurse, rotating shifts", household="lives with partner", transport="own car",
         constraints=[], tone="brisk, cooperative", p_accept_alt=0.85, travel_aversion=0.5, max_travel_min=200, p_attend=0.97),
    dict(pid="OPS-0003", name="Jari Turunen", birth_year=1962, sex="M", muni="Outokumpu", lonlat=(29.018, 62.726),
         archetype="travel_constrained", priority="urgent", referral_days_ago=25, slot=(6, 2),
         occupation="unemployed factory worker", household="lives alone", transport="no car, relies on the bus",
         constraints=[{"kind": "transport", "text": "has no car; long trips are only possible with arranged transport", "travel_limit_min": 150}],
         tone="hesitant, worried about cost", p_accept_alt=0.4, travel_aversion=1.6, max_travel_min=150, p_attend=0.88),
    dict(pid="OPS-0004", name="Aili Räsänen", birth_year=1947, sex="F", muni="Ilomantsi", lonlat=(30.930, 62.672),
         archetype="travel_constrained", priority="standard", referral_days_ago=12, slot=(6, 3),
         occupation="retired teacher", household="lives alone; daughter in Joensuu", transport="daughter drives her",
         constraints=[{"kind": "escort", "text": "needs her daughter to accompany her; prefers the hospital she knows", "travel_limit_min": 90}],
         tone="warm, formal, a bit anxious about new places", p_accept_alt=0.3, travel_aversion=2.2, max_travel_min=90, p_attend=0.95),
    dict(pid="OPS-0005", name="Mikko Pesonen", birth_year=1981, sex="M", muni="Joensuu", lonlat=(29.770, 62.611),
         archetype="engaged_flexible", priority="standard", referral_days_ago=14, slot=(7, 1),
         occupation="software developer, works remotely", household="single", transport="own car",
         constraints=[], tone="casual, wants it done fast", p_accept_alt=0.9, travel_aversion=0.4, max_travel_min=240, p_attend=0.97),
    dict(pid="OPS-0006", name="Leena Ikonen", birth_year=1968, sex="F", muni="Kontiolahti", lonlat=(29.848, 62.766),
         archetype="low_follow_through", priority="standard", referral_days_ago=10, slot=(7, 2),
         occupation="upper-secondary school teacher", household="two teenagers", transport="own car",
         constraints=[{"kind": "schedule", "text": "is invigilating matriculation exams for the next three days and cannot travel before they are over",
                       "earliest_story_day": 9}],
         tone="friendly, busy, negotiates", p_accept_alt=0.55, travel_aversion=1.0, max_travel_min=150, p_attend=0.86),
    dict(pid="OPS-0007", name="Tapio Kärkkäinen", birth_year=1959, sex="M", muni="Nurmes", lonlat=(29.133, 63.543),
         archetype="engaged_flexible", priority="urgent", referral_days_ago=27, slot=(7, 3),
         occupation="retired farmer", household="lives with his wife", transport="own car",
         constraints=[{"kind": "preference", "text": "has family in Kuopio and would rather not drive south to Mikkeli or Savonlinna",
                       "avoid": ["fi-hosp-mks", "fi-hosp-savonlinna"]}],
         tone="cheerful, talkative", p_accept_alt=0.75, travel_aversion=0.7, max_travel_min=200, p_attend=0.95),
]


def minutes(a: tuple, b: tuple) -> float:
    """Straight-line x1.3 at 70 km/h: the same DERIVED travel model as the national twin."""
    (x1, y1), (x2, y2) = a, b
    la1, la2 = math.radians(y1), math.radians(y2)
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(x2 - x1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h)) * 1.3 / 70 * 60


def travel_min(persona: dict, facility_id: str) -> float:
    return round(minutes(tuple(persona["lonlat"]), FAC_COORD[facility_id]), 0)


def accept_prob(persona: dict, facility_id: str) -> float:
    """P(accepts an offer at facility) - national agent acceptance utility + the persona's persistent constraints."""
    if facility_id == HOME_FACILITY:
        return 1.0
    t, t0 = travel_min(persona, facility_id), travel_min(persona, HOME_FACILITY)
    extra = max(0.0, t - t0)
    base = persona["p_accept_alt"] + (1 - persona["p_accept_alt"]) * math.exp(-extra / 30)
    u = -persona["travel_aversion"] * extra / 60 + (0.5 if t <= persona["max_travel_min"] else -2.0)
    p = base / (1 + math.exp(-(1 + u)))
    for c in persona.get("constraints", []):
        if facility_id in c.get("avoid", []):
            p *= 0.15
        if c.get("travel_limit_min") and t > c["travel_limit_min"]:
            p *= 0.25
    return round(float(min(1.0, max(0.0, p))), 3)


def slot_time(t0: datetime, day: int, idx: int, per_day: int) -> datetime:
    """Slot idx of a day: evenly spread over 08:00-15:00 Finnish time."""
    step = 7.0 / max(1, per_day)
    return t0 + timedelta(days=day, hours=8 + idx * step)


def background_mask(res_id: str, per_day: int, plan: list, seed: int = 2026) -> dict:
    """(day, idx) -> booked? Deterministic background occupancy from the share profile."""
    rng = np.random.default_rng([seed, int.from_bytes(res_id.encode()[:8].ljust(8, b"_"), "little") % (2 ** 31)])
    out = {}
    for d in range(DAYS):
        share = next(s for until, s in plan if d < until)
        n = int(round(share * per_day))
        order = rng.permutation(per_day)
        booked = set(order[:n].tolist())
        for i in range(per_day):
            out[d, i] = i in booked
    return out
