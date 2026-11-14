from datetime import datetime, timedelta, timezone

import pytest

from backend.pathways.engine import TransitionError, check_transition, evaluate
from backend.pathways.pack import get_pack, list_packs

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
P = get_pack("colorectal")


def ev(seq, stage, days, fac=None):
    return {"seq": seq, "type": "STAGE_ENTERED", "stage": stage, "occurred_at": T0 + timedelta(days=days), "facility_id": fac}


def run(stages, as_of_days, route="screening"):
    events = [ev(i + 1, s, d) for i, (s, d) in enumerate(stages)]
    return evaluate(P, route, events, T0 + timedelta(days=as_of_days))


def test_all_packs_load_and_validate():
    assert [p.id for p in list_packs()] == ["colorectal"]


def test_screening_route_goes_via_nurse_not_gp():
    assert P.stages["FIT_POSITIVE"].next == ["NURSE_ASSESSMENT", "DECLINED"]
    with pytest.raises(TransitionError):
        check_transition(P, "screening", "FIT_POSITIVE", "COLONOSCOPY", "doctor", None, T0)
    with pytest.raises(TransitionError):
        check_transition(P, "screening", "FIT_POSITIVE", "GP_VISIT", "doctor", None, T0)


def test_first_stage_must_match_entry_route():
    check_transition(P, "symptomatic", None, "SYMPTOMS_REPORTED", "patient", None, T0)
    with pytest.raises(TransitionError):
        check_transition(P, "symptomatic", None, "SCREENING_INVITED", "doctor", None, T0)


def test_patient_cannot_record_clinical_stage():
    with pytest.raises(TransitionError):
        check_transition(P, "symptomatic", "SYMPTOMS_REPORTED", "GP_VISIT", "patient", None, T0)


def test_time_cannot_go_backwards():
    with pytest.raises(TransitionError):
        check_transition(P, "symptomatic", "SYMPTOMS_REPORTED", "GP_VISIT", "doctor", T0, T0 - timedelta(days=1))


def test_terminal_stage_closes_pathway():
    s = run([("SCREENING_INVITED", 0), ("FIT_RETURNED", 10), ("FIT_NEGATIVE", 14)], 20)
    assert s.closed and s.allowed_next == [] and s.alerts == []


def test_lost_referral_raises_dwell_alert():
    s = run([("SYMPTOMS_REPORTED", 0), ("GP_VISIT", 3), ("REFERRAL_SENT", 3)], 3 + 9, route="symptomatic")
    rules = {a.rule_id: a for a in s.alerts}
    a = rules["colorectal.dwell.REFERRAL_SENT"]
    assert a.severity == "warning" and a.responsible_role == "gp"


def test_fit_positive_clock_breach_is_critical():
    s = run([("SCREENING_INVITED", 0), ("FIT_RETURNED", 5), ("FIT_POSITIVE", 8), ("NURSE_ASSESSMENT", 12)], 8 + 30)
    c = {c.id: c for c in s.clocks}["fit_positive_to_colonoscopy"]
    assert c.status == "running" and c.severity == "critical"
    assert "colorectal.clock.fit_positive_to_colonoscopy" in {a.rule_id for a in s.alerts}


def test_referral_to_treatment_met_and_missed():
    base = [("SYMPTOMS_REPORTED", 0), ("GP_VISIT", 2), ("REFERRAL_SENT", 2), ("REFERRAL_RECEIVED", 3),
            ("COLONOSCOPY", 10), ("HISTOLOGY", 10), ("CANCER_CONFIRMED", 15), ("STAGING", 18), ("MDT_DECISION", 25)]
    met = run(base + [("SURGERY", 40)], 41, "symptomatic")
    missed = run(base + [("SURGERY", 50)], 51, "symptomatic")
    get = lambda s: {c.id: c for c in s.clocks}["referral_to_treatment"]
    assert get(met).status == "met" and get(met).elapsed_days == pytest.approx(37)
    assert get(missed).status == "missed"


def test_clock_cancelled_when_cancer_excluded():
    s = run([("SYMPTOMS_REPORTED", 0), ("GP_VISIT", 1), ("REFERRAL_SENT", 1), ("REFERRAL_RECEIVED", 2),
             ("COLONOSCOPY", 9), ("CANCER_EXCLUDED", 9)], 60, "symptomatic")
    assert {c.id: c for c in s.clocks}["referral_to_treatment"].status == "cancelled"
    assert s.closed


def test_universal_stage_reachable():
    check_transition(P, "screening", "NURSE_ASSESSMENT", "DECEASED", "doctor", None, T0)
