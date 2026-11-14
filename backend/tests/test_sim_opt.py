"""Simulator + optimiser on a tiny hand-made network (no DB)."""
from datetime import timedelta

from backend.opt.allocate import optimise
from backend.pathways.engine import check_transition
from backend.pathways.pack import get_pack
from backend.sim.model import T0, Model, score
from backend.sim.snapshot import Snapshot, apply_scenario

CAPS = ["colonoscopy", "ct_imaging", "mdt_crc", "crc_surgery", "oncology", "radiotherapy"]


def tiny(cap_a=6.0, cap_b=30.0) -> Snapshot:
    munis = {"m1": {"county": "c1", "pop": 100.0, "name": "M1"}, "m2": {"county": "c2", "pop": 100.0, "name": "M2"}}
    counties = {c: {"name": c, "yta": "y1", "m_pc": 1.0, "m_sp": 1.0, "cap_factor": 1.0, "pop": 100.0} for c in ("c1", "c2")}
    facs = {"A": {"name": "A", "kind": "central_hospital", "county": "c1", "yta": "y1", "lon": 0, "lat": 0},
            "B": {"name": "B", "kind": "university_hospital", "county": "c2", "yta": "y1", "lon": 1, "lat": 1}}
    capacity = {f"A|{c}": (cap_a if c == "colonoscopy" else 5.0) for c in CAPS}
    capacity.update({f"B|{c}": (cap_b if c == "colonoscopy" else 5.0) for c in CAPS})
    routing = {f"m1|{c}": [["A", 1.0]] for c in CAPS} | {f"m2|{c}": [["B", 1.0]] for c in CAPS}
    travel = {"m1|A": 10.0, "m1|B": 90.0, "m2|A": 90.0, "m2|B": 10.0}
    return Snapshot(munis, counties, facs, capacity, routing, travel, {"screening": 6.0, "symptomatic": 12.0})


def test_simulated_histories_are_valid_pack_transitions():
    m = Model(tiny(), seed=3, horizon=200, warmup=30).run()
    pack = get_pack("colorectal")
    for p in m.patients[:300]:
        cur = None
        for stage, _ in p.hist:
            if cur is not None:
                check_transition(pack, p.route, cur, stage, "generator", None, T0 + timedelta(days=1))
            cur = stage


def test_overloaded_hospital_has_longer_waits():
    r = score(Model(tiny(cap_a=8.0), seed=1, horizon=300, warmup=60).run())
    assert r["facility"]["A|colonoscopy"]["mean_wait_days"] > r["facility"]["B|colonoscopy"]["mean_wait_days"]


def test_optimiser_removes_overload_by_rerouting():
    plan = optimise(tiny(cap_a=6.0, cap_b=40.0), "colonoscopy", budget_units=0, max_travel_min=180)
    assert plan["overload_before"] > 0 and plan["overload_after"] < 1e-6
    assert any(c["municipality"] == "m1" for c in plan["routing_changes"])


def test_optimiser_prefers_capacity_when_budget_allows_and_travel_is_costly():
    plan = optimise(tiny(cap_a=6.0, cap_b=40.0), "colonoscopy", budget_units=3, w_move=500)
    assert plan["capacity_additions"] and plan["capacity_additions"][0]["facility_id"] == "A"


def test_scenario_is_pure():
    s = tiny()
    s2 = apply_scenario(s, {"capacity": [{"facility_id": "A", "capability": "colonoscopy", "factor": 2}], "monitoring": True})
    assert s.capacity["A|colonoscopy"] == 6.0 and s2.capacity["A|colonoscopy"] == 12.0 and s2.monitoring and not s.monitoring


def test_monitoring_shortens_stalls():
    base = score(Model(tiny(cap_b=60, cap_a=60), seed=5, horizon=300, warmup=60).run())["region"]["national"]
    mon = score(Model(apply_scenario(tiny(cap_b=60, cap_a=60), {"monitoring": True}), seed=5, horizon=300, warmup=60).run())["region"]["national"]
    assert mon["stall_days.mean"] < base["stall_days.mean"] / 2
