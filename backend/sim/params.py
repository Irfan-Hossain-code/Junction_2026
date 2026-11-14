"""Shared model parameters for the synthetic generator AND the SimPy simulator.

Every number here is either OBSERVED (cited) or an explicit modelling ASSUMPTION (HYPOTHESIS).
The twin UI shows this file's content (via /twin/assumptions) next to every simulated result.
"""

# ---------------------------------------------------------------- OBSERVED (Finnish Cancer Registry 2024 cohort)
SCREENING_PARTICIPATION = 0.735       # FI-SCR-STATS
SCREENING_FIT_POSITIVE = 0.045        # FI-SCR-STATS
SCREENING_COLONOSCOPIES_PER_YEAR = 10244   # FI-SCR-STATS

# ---------------------------------------------------------------- ASSUMPTIONS (HYPOTHESIS, tune with mentors)
SYMPTOMATIC_TO_SCREENING_RATIO = 2.0  # symptomatic colonoscopy referrals per screening colonoscopy
CRC_CASES_PER_YEAR = 4000             # order of magnitude for Finland (verify vs Cancer Registry)
P_CANCER = {"screening": 0.06, "symptomatic": 0.16}   # chosen so total ≈ CRC_CASES_PER_YEAR
TARGET_UTILISATION = 0.85             # capacity model: capacity = demand / 0.85

# lognormal median days for NON-resource steps (sigma 0.5). 'pc'/'sp' scaled by county multipliers
DURATIONS = {
    "SCREENING_INVITED": (14, None), "FIT_RETURNED": (5, None), "FIT_POSITIVE": (6, "sp"),
    "NURSE_ASSESSMENT": (14, "sp"), "SYMPTOMS_REPORTED": (5, "pc"), "GP_VISIT": (1, "pc"),
    "REFERRAL_SENT": (1, None), "REFERRAL_RECEIVED": (12, "sp"), "SPECIALIST_ASSESSMENT": (7, "sp"),
    "COLONOSCOPY": (0.2, None), "ALT_EXAMINATION": (5, "sp"), "HISTOLOGY": (6, "sp"),
    "CANCER_CONFIRMED": (3, None), "STAGING": (6, "sp"), "MDT_DECISION": (12, "sp"),
    "NEOADJUVANT_THERAPY": (45, None), "SURGERY": (25, "sp"), "SYSTEMIC_THERAPY": (60, None),
    "ADJUVANT_THERAPY": (90, None),
}
SIGMA = 0.5

# "cracks": probability a step stalls (nobody acts) and how long until someone notices
CRACK_P = {"FIT_POSITIVE": 0.05, "REFERRAL_SENT": 0.04, "HISTOLOGY": 0.04, "NURSE_ASSESSMENT": 0.03}
STALL_MEDIAN_DAYS_UNMONITORED = 35.0  # ASSUMPTION: discovered when patient chases / next contact
ALERT_RESPONSE_DAYS = 2.0             # with CancerFlow monitoring: detected at dwell threshold + 2 d response

# treatment mix after MDT (ASSUMPTION)
TREATMENT_MIX = [("SURGERY", "crc_surgery", 0.62), ("NEOADJUVANT_THERAPY", "radiotherapy", 0.15),
                 ("SYSTEMIC_THERAPY", "oncology", 0.15), ("PALLIATIVE_CARE", None, 0.08)]

# county multiplier range mapped from real indicators (min -> LO, max -> HI)
MULT_LO, MULT_HI = 0.85, 1.20   # deliberately conservative: source indicators cover ALL specialties

# weekly per-patient service structure: n parallel slots, service time = n*7/capacity days
SLOT_SIZE = 5.0

# Shared resources: colorectal patients use only a SHARE of e.g. CT or radiotherapy capacity. Modelling the CRC share
# as its own tiny queue overstates waits (no pooling). So: pooled capacity = CRC capacity x POOL, and other cancer
# types are simulated as background Poisson load at BACKGROUND_UTILISATION of their share. (ASSUMPTION)
POOL = {"colonoscopy": 1, "pathology": 1, "mdt_crc": 1, "crc_surgery": 2, "ct_imaging": 10, "oncology": 6, "radiotherapy": 8}
BACKGROUND_UTILISATION = 0.80

# =====================================================================================================
# BEHAVIOURAL PATIENT AGENTS  (all ASSUMPTIONS / HYPOTHESIS: illustrative archetypes, not medical facts)
# =====================================================================================================
# Each patient draws CONTINUOUS parameters around one archetype's means (beta for probabilities,
# lognormal for travel tolerance), so profiles overlap. Urgency comes from the referral's symptom
# category, never from demographics.
ARCHETYPES = {
    "engaged_flexible":   {"share": 0.45, "max_travel_min": 150, "travel_aversion": 0.6, "wait_aversion": 1.0,
                           "p_attend": 0.97, "p_accept_alt": 0.80, "reminder_gain": 0.5, "care_seeking_days": 5},
    "travel_constrained": {"share": 0.35, "max_travel_min": 60,  "travel_aversion": 2.0, "wait_aversion": 0.7,
                           "p_attend": 0.93, "p_accept_alt": 0.35, "reminder_gain": 0.5, "care_seeking_days": 8},
    "low_follow_through": {"share": 0.20, "max_travel_min": 90,  "travel_aversion": 1.2, "wait_aversion": 0.5,
                           "p_attend": 0.82, "p_accept_alt": 0.50, "reminder_gain": 0.6, "care_seeking_days": 15},
}
BETA_CONCENTRATION = 30.0          # spread of individual probabilities around the archetype mean
TRAVEL_SIGMA = 0.30                # lognormal spread of individual max travel tolerance

SCREENING_AGE = (56, 74)           # screening programme invitation ages (verify against programme rules)
SYMPTOMATIC_AGE_MEAN, SYMPTOMATIC_AGE_SD = 70, 11
# symptom category -> probability the GP marks the referral URGENT (ASSUMPTION)
SYMPTOMS = {"rectal_bleeding": (0.35, 0.40), "bowel_habit_change": (0.30, 0.25),
            "iron_deficiency_anaemia": (0.20, 0.50), "abdominal_pain_weight_loss": (0.15, 0.40)}   # (share, p_urgent)
URGENT_PRIORITY, STANDARD_PRIORITY = 0, 1   # SimPy PriorityResource: lower = earlier

# appointment mechanics
REBOOK_AFTER_DECLINE_DAYS = 5.0    # median days to get a new (home) booking after declining an offer
REBOOK_AFTER_DNA_DAYS = 14.0       # median days to rebook after a missed appointment
MAX_DNA_BEFORE_LOST = 2            # after this many no-shows the patient is lost to follow-up (without monitoring)
TRAVEL_DNA_PENALTY_PER_HOUR = 0.03 # attendance drops 3 pp per hour of travel beyond the first hour
TRANSPORT_SUPPORT_RELIEF = 0.6     # transport support removes 60 % of the perceived travel burden
SAMPLE_EVERY = 10                  # store every 10th patient's full journey (replication 0) for inspection

# private purchasing pilot (SYNTHETIC)
PRIVATE_COST_PER_COLONOSCOPY_EUR = 650   # ASSUMPTION for the purchasing scenario
PRIVATE_PILOT_FACILITY = "fi-pc-osm-n8792578075"   # Mehiläinen Joensuu (location OBSERVED, endoscopy capacity HYPOTHESIS)

# capacity planning absorbs a typical no-show rate (units overbook / plan around DNAs). ASSUMPTION
EXPECTED_DNA_FOR_PLANNING = 0.08
