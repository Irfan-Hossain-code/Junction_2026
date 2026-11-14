"""End-to-end pathway optimiser: the engineering tests from the strategy report (§15). Small synthetic instances."""
from dataclasses import replace

from backend.mpc.pathway.heuristics import schedule_greedy
from backend.mpc.pathway.instance import (MEETING, PATIENT, SAMPLE, Instance, InvestmentOption, MovableResource, Patient,
                                          Provider, StageSpec)
from backend.mpc.pathway.replay import kpis, realised_capacity, replay
from backend.mpc.pathway.solver import Strategy, solve
from backend.mpc.pathway.validate import validate

COL = StageSpec("colonoscopy", "colonoscopy", PATIENT, 1.0)
PATH = StageSpec("pathology", "pathology", SAMPLE, 3.0)
MDT = StageSpec("mdt", "mdt", MEETING, 1.0)
TREAT = StageSpec("treatment", "treatment", PATIENT, 0.0, is_treatment=True)
PROV = {k: Provider(k, k, r) for k, r in [("A", "R1"), ("B", "R2"), ("LAB1", "R1"), ("LAB2", "R2")]}


def inst(n=6, lab1=1, lab2=2, col_a=3, col_b=3, treat_a=2, treat_b=2, urgent=(), unknown=False, movable=(), invest=(),
         accept_b=0.9, consent=True, horizon=35):
    cap = {}
    for d in range(horizon + 400):
        cap["A", "colonoscopy", d], cap["B", "colonoscopy", d] = col_a, col_b
        cap["LAB1", "pathology", d], cap["LAB2", "pathology", d] = lab1, lab2
        cap["A", "mdt", d] = cap["B", "mdt", d] = 5
        cap["A", "treatment", d], cap["B", "treatment", d] = treat_a, treat_b
    pts = []
    for i in range(n):
        stages = (COL, replace(PATH, p_needed=0.7) if unknown else PATH, replace(MDT, p_needed=0.3) if unknown else MDT, TREAT)
        pts.append(Patient(f"P{i}", "R1", "A", "LAB1", "urgent" if i in urgent else "standard", 0.0, 12.0, stages, not unknown,
                           {"A": 10.0, "B": 90.0}, {"A": 1.0, "B": accept_b}, consent, 0.999))
    return Instance(PROV, cap, pts, horizon_days=horizon, near_term_days=14, ship_lag_days=1.0,
                    movable=list(movable), investments=list(invest))


NATIONAL = Strategy("B", allow_patient_transfer=True, allow_sample_routing=True, time_limit_s=20)


def mean_kpi(i, plan, k, reps=8):
    return sum(kpis(i, replay(i, plan, r), plan)[k] for r in range(reps)) / reps


def test_1_downstream_bottleneck_diagnostics_first_does_not_advance_treatment():
    i = inst(n=6, lab1=1, lab2=2, col_a=1, col_b=3)
    diag = solve(i, Strategy("diag", allow_patient_transfer=True, objective="diagnostics", time_limit_s=20))
    fifo = schedule_greedy(i, "fifo")
    nat = solve(i, NATIONAL)
    assert mean_kpi(i, diag, "mean_colonoscopy_day") < mean_kpi(i, fifo, "mean_colonoscopy_day")   # local metric improves
    assert mean_kpi(i, diag, "mean_treatment_day") >= mean_kpi(i, fifo, "mean_treatment_day") - 0.5  # ...treatment does not
    assert mean_kpi(i, nat, "mean_treatment_day") < mean_kpi(i, diag, "mean_treatment_day") - 0.5   # pathway optimiser does


def test_2_clinical_priority_urgent_first_under_contention():
    i = inst(n=4, lab1=1, lab2=0, urgent=(3,))
    plan = solve(i, Strategy("A", time_limit_s=20))
    t = {pid: a.start_day for (pid, j), a in plan.assignments.items() if a.stage == "treatment"}
    assert t["P3"] == min(t.values())


def test_3_capacity_conservation_no_overbooking():
    i = inst(n=8)
    for plan in (schedule_greedy(i, "fifo"), solve(i, NATIONAL)):
        assert validate(i, plan, None) == [] or all("capacity" not in v for v in validate(i, plan, None))
        assert all(replay(i, plan, r)["overbooked_slots"] == 0 for r in range(5))


def test_4_patient_transfer_consumes_destination_capacity():
    i = inst(n=6, col_a=1, col_b=1)
    plan = solve(i, NATIONAL)
    moved = [a for (pid, j), a in plan.assignments.items() if a.stage == "colonoscopy" and a.provider == "B"]
    assert moved
    per_day = {}
    for a in moved:
        per_day[a.start_day] = per_day.get(a.start_day, 0) + 1
    assert all(v <= 1 for v in per_day.values())                   # B's single daily slot is not exceeded


def test_5_resource_relocation_reduces_origin_capacity():
    mv = MovableResource("surgeon", "treatment", "B", ("A",), units_per_week=2)
    i = inst(n=2, movable=(mv,), horizon=35)
    plan = solve(i, NATIONAL)
    plan.moves = [{"resource": "surgeon", "to": "A", "bucket_start": 14, "units": 2}]
    cap = realised_capacity(i, plan)
    week = range(14, 21)
    assert sum(cap["B", "treatment", d] for d in week) == sum(i.capacity["B", "treatment", d] for d in week) - 2
    assert sum(cap["A", "treatment", d] for d in week) == sum(i.capacity["A", "treatment", d] for d in week) + 2


def test_6_precedence_never_violated_and_validator_catches_it():
    i = inst(n=5)
    plan = solve(i, NATIONAL)
    assert not [v for v in validate(i, plan, NATIONAL) if v.startswith("precedence")]
    a = plan.assignments["P0", 3]
    plan.assignments["P0", 3] = replace(a, start_day=0.0)          # treatment before diagnosis
    assert any(v.startswith("precedence") for v in validate(i, plan, NATIONAL))


def test_7_declined_transfer_is_never_counted_as_accepted():
    i = inst(n=6, col_a=1, col_b=3, accept_b=0.0)
    plan = solve(i, Strategy("B", allow_patient_transfer=True, min_accept=0.0, time_limit_s=20))
    for r in range(5):
        out = replay(i, plan, r)["patients"]
        assert sum(o["transfers"] for o in out.values()) == 0       # acceptance 0 -> served at home


def test_8_replanning_after_capacity_loss_is_feasible():
    i = inst(n=6)
    for d in range(0, 10):
        i.capacity["LAB1", "pathology", d] = 0                     # lab outage
    plan = solve(i, NATIONAL)
    assert plan.status in ("optimal", "partial") and validate(i, plan, NATIONAL) == []
    assert all(a.provider != "LAB1" or a.start_day >= 10 for a in plan.assignments.values() if a.stage == "pathology")


def test_9_and_10_benchmark_is_consistent_and_auditable():
    from backend.mpc.pathway import benchmark as bm
    import backend.mpc.pathway.demo as demo
    orig = demo.build_demo
    bm.build_demo = lambda seed: inst(n=6, col_a=1, col_b=3, lab1=1, lab2=2)
    try:
        doc = bm.run(seed=1, reps=3, time_limit=10)
    finally:
        bm.build_demo = orig
    assert set(doc["strategies"]) >= {"baseline_fifo", "priority_greedy", "diagnostics_first", "A_local_pathway",
                                      "B_national_pathway", "C_resource_movement"}
    assert doc["instance"]["digest"] and doc["evaluation"]["replications"] == 3
    for v in doc["strategies"].values():
        assert v["constraint_violations"] == [] and "predicted_tiers" in v and "kpis" in v
    assert doc["recommendation"]["constraint_checks"] and doc["recommendation"]["rejected_alternatives"] is not None


def test_11_unknown_diagnosis_is_never_a_confirmed_treatment_booking():
    i = inst(n=4, unknown=True)
    plan = solve(i, NATIONAL)
    tr = [a for a in plan.assignments.values() if a.stage == "treatment"]
    assert tr and all(a.tentative and a.p_needed < 0.5 for a in tr)
    a = plan.assignments["P0", 3]
    plan.assignments["P0", 3] = replace(a, tentative=False)
    assert any(v.startswith("unknown pathway") for v in validate(i, plan, NATIONAL))


def test_12_no_hidden_spending_in_existing_resource_strategies():
    inv = InvestmentOption("lab_plus", "LAB1", "pathology", 5, 1000.0, 2)
    i = inst(n=6, invest=(inv,))
    plan = solve(i, NATIONAL)
    assert plan.investments == {} and validate(i, plan, NATIONAL) == []
    plan.investments = {"lab_plus": 1}
    assert any("hidden spending" in v for v in validate(i, plan, NATIONAL))
    funded = solve(i, Strategy("D", allow_patient_transfer=True, allow_sample_routing=True, investment_budget_eur=1000 * 5, time_limit_s=20))
    assert sum(1000 * (i.horizon_days / 7) * u for u in funded.investments.values()) <= 1000 * 5 + 1e-6
