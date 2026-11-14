"""MPC foundation tests: synthetic, no DB, no simulation workers."""
import os

from backend.mpc.coordinator import MPCCoordinator, enabled
from backend.mpc.forecast import DownstreamForecast
from backend.mpc.problem import MPCConfig, evaluate, greedy, solve
from backend.mpc.state import MPCState, PatientState, SlotState

# Hospital A (home): colonoscopy on day 2, but its downstream MDT/surgery queues take ~40 d.
# Hospital B: colonoscopy on day 6, downstream ~10 d. Target 42 d, patient already waited 5 d.
FC = DownstreamForecast({"A": (40.0, 0.05), "B": (10.0, 0.05)})


def patient(pid="p1", priority="standard", elapsed=5.0, consent=True, accept_b=0.9, home="A", needs_auth=True):
    return PatientState(pid, "colonoscopy", priority, elapsed, 42.0, home, ("A", "B"),
                        {"A": 20.0, "B": 60.0}, {"A": 1.0, "B": accept_b}, consent, needs_auth)


def state(patients, slots, version=1, horizon=28):
    return MPCState("2026-11-15", version, tuple(patients), tuple(SlotState(*s) for s in slots), horizon)


SLOTS = [("A", "colonoscopy", 2, 1), ("B", "colonoscopy", 6, 1)]


def test_whole_pathway_beats_locally_attractive_slot():
    st = state([patient()], SLOTS)
    plan, opts, defer = solve(st, FC, MPCConfig())
    myopic = greedy(st, opts, MPCConfig(), local_only=False)       # earliest slot anywhere
    assert myopic["p1"] == ("A", 2)                                  # locally attractive: 3 days sooner
    assert plan.assignment["p1"] == ("B", 6)                         # MPC: later colonoscopy, much earlier treatment
    m_mpc, m_myo = evaluate(st, opts, defer, plan.assignment, MPCConfig()), evaluate(st, opts, defer, myopic, MPCConfig())
    assert m_myo["expected_target_violations"] > 0.9                 # 5 + 2 + 40 = 47 d > 42 d
    assert m_mpc["L1_weighted_expected_violation_days"] < m_myo["L1_weighted_expected_violation_days"]
    assert m_mpc["mean_expected_rtt_days"] < m_myo["mean_expected_rtt_days"]


def test_lexicographic_burden_cannot_override_clinical_tier():
    cfg = MPCConfig(transfer_cost=1000.0, admin_cost=1000.0)        # absurdly expensive transfer
    plan, _, _ = solve(state([patient()], SLOTS), FC, cfg)
    assert plan.assignment["p1"] == ("B", 6)                         # L3 never trades away L1
    assert plan.status == "lexicographic-optimal" and set(plan.tiers) == {"L1", "L2", "L3"}


def test_low_acceptance_keeps_patient_local():
    plan, _, _ = solve(state([patient(accept_b=0.05)], SLOTS), FC, MPCConfig())
    assert plan.assignment["p1"] == ("A", 2)                         # an offer the patient will decline is worse


def test_no_consent_means_no_transfer_offer():
    plan, _, _ = solve(state([patient(consent=False)], SLOTS), FC, MPCConfig())
    assert plan.assignment["p1"][0] == "A"


def test_urgent_priority_and_mandatory_time_limit():
    slots = [("A", "colonoscopy", 1, 1), ("A", "colonoscopy", 20, 1)]
    fc = DownstreamForecast({"A": (15.0, 0.05)})
    pts = [patient("u", "urgent", elapsed=1.0, home="A"), patient("s", "standard", elapsed=10.0, home="A")]
    plan, _, _ = solve(state(pts, slots), fc, MPCConfig(urgent_max_days=7))
    assert plan.assignment["u"] == ("A", 1) and plan.assignment["s"] == ("A", 20)


def test_capacity_is_hard():
    pts = [patient(f"p{i}") for i in range(3)]
    plan, _, _ = solve(state(pts, SLOTS), FC, MPCConfig())
    used = [a for a in plan.assignment.values() if a]
    assert len(used) == 2 and len(set(used)) == 2                    # one patient per slot, third is deferred


def test_budget_exhausted_gives_feasible_fallback():
    plan, _, _ = solve(state([patient(), patient("p2")], SLOTS), FC, MPCConfig(budget_s=0.0))
    assert plan.status == "fallback-greedy"
    assert all(a is None or a in (("A", 2), ("B", 6)) for a in plan.assignment.values())


def test_stale_results_are_never_published():
    c = MPCCoordinator(FC, MPCConfig())
    c.observe(state([patient()], SLOTS))
    orig = FC.samples

    def racing_samples(f, n, rng):                                   # new state arrives while solving
        if c.version == 1:
            c.observe(state([patient(elapsed=6.0)], SLOTS))
        return orig(f, n, rng)
    c.fc = DownstreamForecast(FC.remaining_days)
    c.fc.samples = racing_samples
    r = c.step()
    assert r.stale and not r.published and c.latest() is None
    c.fc = FC
    r2 = c.step()
    assert r2.published and r2.state_version == 2 and c.latest() is r2


def test_event_trigger_reoptimises_locally_and_respects_standing_bookings():
    slots = [("A", "colonoscopy", 2, 1), ("B", "colonoscopy", 6, 1), ("C", "colonoscopy", 3, 1)]
    fc = DownstreamForecast({"A": (40.0, 0.05), "B": (10.0, 0.05), "C": (12.0, 0.05)})
    q = PatientState("q", "colonoscopy", "standard", 5.0, 42.0, "C", ("C",), {"C": 10.0}, {"C": 1.0})
    c = MPCCoordinator(fc, MPCConfig())
    c.observe(state([patient(), q], slots))
    first = c.step("scheduled")
    assert first.plan.assignment == {"p1": ("B", 6), "q": ("C", 3)}
    c.observe(state([patient(elapsed=7.0), q], slots))
    local = c.step("event", affected_facilities=("A",))
    assert local.published and {r["pid"] for r in local.recommendations} == {"p1"}
    assert c.latest().plan.assignment["q"] == ("C", 3)               # untouched patient keeps the standing booking


def test_recommendations_need_human_authorisation_and_urgent_escalates():
    c = MPCCoordinator(FC, MPCConfig(review_delay_days=3.0))
    c.observe(state([patient("u", "urgent", elapsed=1.0)], [("A", "colonoscopy", 1, 1)]))
    r = c.step()
    rec = r.recommendations[0]
    assert rec["status"] == "escalate_on_call" and "reviewer" in rec["escalation_reason"]
    assert r.escalations and c.escalation.sent[0]["pid"] == "u"
    c2 = MPCCoordinator(FC, MPCConfig())
    c2.observe(state([patient()], SLOTS))
    rec2 = c2.step().recommendations[0]
    assert rec2["action"] == "offer_transfer" and rec2["status"] == "requires_authorisation"


def test_feature_flag_off_by_default():
    os.environ.pop("CANCERFLOW_MPC", None)
    assert enabled() is False
    from backend.api.main import app
    assert not any(getattr(r, "path", "").startswith("/mpc") for r in app.routes)


def test_clinical_tier_never_worse_than_myopic_on_random_instances():
    import numpy as np
    for seed in range(6):
        rng = np.random.default_rng(seed)
        facs = ("A", "B", "C")
        fc = DownstreamForecast({f: (float(rng.uniform(5, 45)), 0.3) for f in facs})
        pts = [PatientState(f"p{i}", "colonoscopy", "urgent" if rng.random() < 0.3 else "standard", float(rng.uniform(0, 30)), 42.0,
                            str(rng.choice(facs)), facs, {f: float(rng.uniform(10, 150)) for f in facs},
                            {f: float(rng.uniform(0.2, 1.0)) for f in facs}, bool(rng.random() < 0.8))
               for i in range(10)]
        pts = [PatientState(p.pid, p.next_capability, p.priority, p.elapsed_days, p.target_days, p.home_facility, p.eligible,
                            p.travel_min, {**p.accept_prob, p.home_facility: 1.0}, p.consent_to_transfer) for p in pts]
        slots = [(f, "colonoscopy", d, int(rng.integers(0, 2))) for f in facs for d in range(0, 28, 2)]
        st = state(pts, slots)
        plan, opts, defer = solve(st, fc, MPCConfig())
        cfg = MPCConfig()
        for base in (greedy(st, opts, cfg, local_only=True), greedy(st, opts, cfg, local_only=False)):
            assert evaluate(st, opts, defer, plan.assignment, cfg)["L1_weighted_expected_violation_days"] <= \
                evaluate(st, opts, defer, base, cfg)["L1_weighted_expected_violation_days"] + 1e-3
