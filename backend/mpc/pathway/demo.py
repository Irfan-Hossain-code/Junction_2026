"""Illustrative East-Finland pathway instance (SYNTHETIC numbers, real place names/coordinates).

Premise (spec §7): the hospitals' colonoscopy units feed a SHARED regional pathology service whose daily capacity
is the binding downstream constraint; a second lab (Jyväskylä) has spare capacity but a shipping lag. MDT meets
weekly; surgery is scarce in Joensuu. Patient behaviour (acceptance, attendance) reuses the national agent model
(backend.sim.model.sample_agent). Every capacity, probability and cost here is an ASSUMPTION for illustration.
"""
from __future__ import annotations

import math

import numpy as np

from backend.mpc.pathway.instance import (MEETING, PATIENT, SAMPLE, Instance, InvestmentOption, MovableResource, Patient,
                                          Provider, StageSpec)

COORD = {"PKKS": (29.76, 62.60), "KYS": (27.68, 62.89), "SAV": (28.88, 61.87),
         "LAB_E": (27.68, 62.89), "LAB_C": (25.75, 62.24)}            # OSM hospital locations (rounded)
REGION = {"PKKS": "North Karelia", "KYS": "North Savo", "SAV": "South Savo"}

COLONOSCOPY = StageSpec("colonoscopy", "colonoscopy", PATIENT, 1.0)
PATHOLOGY = lambda p=1.0: StageSpec("pathology", "pathology", SAMPLE, 5.0, p)        # 5 d turnaround (ASSUMPTION)
MDT = lambda p=1.0: StageSpec("mdt", "mdt", MEETING, 3.0, p)
TREAT = StageSpec("treatment", "treatment", PATIENT, 0.0, 1.0, is_treatment=True)
P_BIOPSY, P_CANCER_GIVEN_HISTOLOGY = 0.7, 0.3                                      # ASSUMPTIONS (not calibrated)


def _minutes(a, b):
    (x1, y1), (x2, y2) = COORD[a], COORD[b]
    la1, la2 = math.radians(y1), math.radians(y2)
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(x2 - x1) / 2) ** 2
    km = 6371 * 2 * math.asin(math.sqrt(h)) * 1.3                    # road factor, as in the national travel model
    return km / 70 * 60


def _accept(agent, extra_min, t_f):
    """Same acceptance utility as the national agent model (backend.sim.model.Model.p_accept_alternative)."""
    base = agent["p_accept_alt"] + (1 - agent["p_accept_alt"]) * math.exp(-max(0.0, extra_min) / 30)
    u = -agent["travel_aversion"] * max(0.0, extra_min) / 60 + (0.5 if t_f <= agent["max_travel_min"] else -2.0)
    return base / (1 + math.exp(-(1 + u)))


def build_demo(seed: int = 7, n_patients: int = 60, pathology_per_day: int = 2, pkks_surgery_days: tuple = (0, 2),
               pkks_colonoscopy_per_day: int = 1) -> Instance:
    from backend.sim.model import sample_agent
    rng = np.random.default_rng(seed)
    providers = {"PKKS": Provider("PKKS", "North Karelia Central Hospital (Joensuu)", "North Karelia"),
                 "KYS": Provider("KYS", "Kuopio University Hospital", "North Savo"),
                 "SAV": Provider("SAV", "Savonlinna Hospital", "South Savo"),
                 "LAB_E": Provider("LAB_E", "Eastern Finland pathology (shared service)", "North Savo"),
                 "LAB_C": Provider("LAB_C", "Central Finland pathology (Jyväskylä)", "Central Finland")}
    H = 63
    cap = {}
    for d in range(H + 400):
        wd = d % 7
        if wd >= 5:
            continue
        cap["PKKS", "colonoscopy", d], cap["KYS", "colonoscopy", d], cap["SAV", "colonoscopy", d] = pkks_colonoscopy_per_day, 3, 1
        cap["LAB_E", "pathology", d], cap["LAB_C", "pathology", d] = pathology_per_day, 2
        if wd == 1:
            cap["PKKS", "mdt", d] = 6
        if wd == 3:
            cap["KYS", "mdt", d] = 8
        if wd in pkks_surgery_days:
            cap["PKKS", "treatment", d] = 1
        if wd <= 3:
            cap["KYS", "treatment", d] = 1
    homes = ["PKKS"] * 25 + ["KYS"] * 25 + ["SAV"] * 10
    patients = []
    for i in range(n_patients):
        home = homes[i % len(homes)]
        u = rng.random()
        agent = sample_agent(rng, "symptomatic")
        if u < 0.5:
            stages, known, elapsed = (COLONOSCOPY, PATHOLOGY(P_BIOPSY), MDT(P_CANCER_GIVEN_HISTOLOGY), TREAT), False, rng.uniform(5, 20)
        elif u < 0.8:
            stages, known, elapsed = (PATHOLOGY(1.0), MDT(P_CANCER_GIVEN_HISTOLOGY), TREAT), False, rng.uniform(12, 28)
        else:
            stages, known, elapsed = (MDT(1.0), TREAT), True, rng.uniform(22, 34)
        travel = {h: (15.0 if h == home else _minutes(home, h) + rng.normal(0, 10)) for h in ("PKKS", "KYS", "SAV")}
        acc = {h: (1.0 if h == home else round(_accept(agent, travel[h] - travel[home], travel[h]), 3)) for h in travel}
        patients.append(Patient(
            pid=f"EF-{i + 1:03d}", region=REGION[home], home=home, home_lab="LAB_E",
            priority="urgent" if agent["urgent"] else "standard",        # from the referral's symptom category
            ready_day=float(rng.integers(0, 4)), deadline_day=round(42 - elapsed, 1), stages=stages, diagnosis_known=known,
            travel_min={k: round(v, 1) for k, v in travel.items()}, accept_prob=acc,
            consent_transfer=bool(rng.random() < 0.85), p_attend=round(agent["p_attend"], 3),
            treat_home="KYS" if home == "SAV" else home))           # Savonlinna: MDT + surgery at the university hospital
    movable = [MovableResource("kys_visiting_surgeon", "treatment", "KYS", ("PKKS",), units_per_week=2, setup_loss=0.0, burden=2.0)]
    investments = [InvestmentOption("pathology_lab_e", "LAB_E", "pathology", 5, 3000.0, 2),
                   InvestmentOption("colonoscopy_pkks", "PKKS", "colonoscopy", 5, 2500.0, 2),
                   InvestmentOption("surgery_pkks", "PKKS", "treatment", 1, 4000.0, 2)]
    return Instance(providers, cap, patients, horizon_days=H, near_term_days=14, ship_lag_days=2.0,
                    movable=movable, investments=investments,
                    meta={"name": "East Finland illustrative pathway instance", "seed": seed,
                          "tags": {"places": "OBSERVED (OSM)", "capacities/probabilities/costs": "ASSUMPTION (illustrative)",
                                   "patients": "SYNTHETIC agents (national behaviour model)"},
                          "assumptions": {"p_biopsy": P_BIOPSY, "p_cancer_given_histology": P_CANCER_GIVEN_HISTOLOGY,
                                          "pathology_turnaround_days": 5, "ship_lag_days": 2, "pathology_per_day_shared": pathology_per_day,
                                          "investment_eur_per_week": {o.id: o.eur_per_week for o in investments}}})
