"""Operations layer: pure, DB-free checks (fast; no LLM, no network)."""
from backend.ops import agents, nl
from backend.ops.world import HOME_FACILITY, PERSONAS, accept_prob, background_mask, travel_min


def _p(name):
    p = next(x for x in PERSONAS if x["name"].startswith(name))
    return {**p, "patient_id": p["pid"], "accept_prob": {f: accept_prob(p, f) for f in
                                                         ("fi-hosp-kys", "fi-hosp-mks", "fi-hosp-savonlinna", HOME_FACILITY)}}


def test_home_is_always_acceptable_and_constraints_bite():
    pekka = _p("Pekka")
    assert accept_prob(pekka, HOME_FACILITY) == 1.0
    assert pekka["accept_prob"]["fi-hosp-kys"] < 0.1          # caregiver: cannot leave his wife for a long day
    tapio = _p("Tapio")
    assert tapio["accept_prob"]["fi-hosp-kys"] > tapio["accept_prob"]["fi-hosp-mks"]   # prefers Kuopio, avoids Mikkeli


def test_decisions_are_deterministic_and_persona_driven():
    leena = _p("Leena")
    off = {"facility_id": "fi-hosp-kys", "facility": "Kuopio", "story_day": 7, "travel_min": 120, "transfer": True}
    d1, d2 = agents.decide(leena, off, "k1"), agents.decide(leena, off, "k1")
    assert d1 == d2
    assert d1["decision"] == "counter" and d1["request"]["earliest_story_day"] == 9   # exam week -> asks for later
    sari = _p("Sari")
    home = {"facility_id": HOME_FACILITY, "facility": "Joensuu", "story_day": 7, "travel_min": 5, "transfer": False}
    assert agents.decide(sari, home, "k2")["decision"] == "accept"


def test_slot_occupancy_is_reproducible():
    a = background_mask("res-kys-endo", 6, [(12, 0.84), (35, 0.7)])
    b = background_mask("res-kys-endo", 6, [(12, 0.84), (35, 0.7)])
    assert a == b and sum(a[0, i] for i in range(6)) == 5          # 84 % of 6 slots booked on day 0


def test_rule_parser_handles_simple_intents_without_llm():
    assert nl._regex("Approve your recommendation")["action"] == "approve"
    assert nl._regex("reject that")["action"] == "reject"
    r = nl._regex("move half to Kuopio and half to Mikkeli")
    assert r["action"] == "propose" and [a["facility"] for a in r["allocations"]] == ["Kuopio", "Mikkeli"]
    assert all(a["share"] == 0.5 for a in r["allocations"])


def test_explain_uses_only_evaluation_numbers():
    iv = {"id": "x", "status": "AWAITING_APPROVAL", "proposal": {"rows": [], "by_facility": {"Kuopio (KYS)": 4}},
          "evaluation": {"options": {"admin": {"kpis": {"pct_within_target": 64.5}}, "optimised": {"kpis": {"pct_within_target": 77.5}},
                                     "no_action": {"kpis": {"pct_within_target": 41.7}}},
                         "review": {"verdict": "worse_than_recommendation", "warnings": [{"text": "Kuopio overloaded"}]}}}
    ex = nl.explain(iv)
    assert ex["verdict"] == "worse_than_recommendation"
    assert "64.5" in ex["bullets"][0] and "77.5" in ex["bullets"][0]
    assert travel_min(_p("Emma"), HOME_FACILITY) < 15
