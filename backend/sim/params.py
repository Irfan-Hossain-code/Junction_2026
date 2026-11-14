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
