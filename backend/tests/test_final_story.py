"""Final integration tests (spec section E). The story test resets ONLY the demo namespace (reset_story.py) and runs
Emma's 17 steps end to end on the shared database (a few minutes)."""
import os

import pytest

from backend.db.conn import connect
from backend.ops import core, evaluate, interventions, story
from backend.services import pathways as svc

# These tests RESET and replay the shared demo story: never run them during a live demo.
STORY = pytest.mark.skipif(not os.environ.get("CANCERFLOW_STORY_TESTS"), reason="set CANCERFLOW_STORY_TESTS=1 (resets the shared demo story)")
pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


def _baseline(c):
    return str(c.execute("""SELECT r.id FROM sim_run r JOIN scenario s ON s.id=r.scenario_id
                            WHERE s.is_baseline AND r.status='done' ORDER BY r.finished_at DESC LIMIT 1""").fetchone()[0])


def test_1_staging_alarm_is_not_answered_with_colonoscopy():
    from backend.api import twin_chat as T
    from backend.api.twin_ai import _ctx
    with connect() as c:
        run = _baseline(c)
        kainuu = c.execute("SELECT id FROM region WHERE name_en ILIKE 'Kainuu%%'").fetchone()[0]
        alarm = next((a for a in _ctx(c, "county", kainuu, run, None)["anomalies"] if a["type"] == "stalled_transition"), None)
        if not alarm:
            pytest.skip("no staging alarm in this baseline run")
        r = T.evaluate(T.EvalIn(scope="county", id=kainuu, run_id=run, alarm_id=alarm["id"]), c)
        assert r["asked"]["resource"] == "ct_imaging" and r["asked"]["stage"] == "STAGING"
        assert r["status"] == "not_applicable"                      # CT idle: process delay, not capacity
        assert "colonoscopy" not in str(r.get("with_investment", ""))


def test_2_recommendation_provenance():
    from backend.api.twin_ai import _ctx
    with connect() as c:
        recs = _ctx(c, "county", "hva-973", _baseline(c), None)["recommendations"]
    assert recs and all(r["evidence"]["status"] in ("suggested", "optimizer_estimated", "simulation_validated") for r in recs)
    for r in recs:
        if r["evidence"]["status"] == "optimizer_estimated":
            assert "overload_after_per_week" in r["evidence"] and r["id"] == "reroute"
        if r["evidence"]["status"] == "simulation_validated":
            assert r["evidence"].get("run_id")
        if r["id"] in ("monitoring", "capacity", "private") and r["evidence"]["status"] != "simulation_validated":
            assert r["evidence"]["status"] == "suggested"           # never labelled as optimised without a computation


def _counts(c):
    return {"other_pathways": c.execute("SELECT count(*) FROM pathway WHERE patient_id NOT LIKE 'OPS-%%'").fetchone()[0],
            "scenarios": c.execute("SELECT count(*) FROM scenario").fetchone()[0],
            "sim_runs": c.execute("SELECT count(*) FROM sim_run").fetchone()[0]}


def _initial(c):
    st = story.state(c)
    return {"free": c.execute("SELECT count(*) FROM ops_slot WHERE status='free'").fetchone()[0],
            "booked": c.execute("SELECT count(*) FROM ops_slot WHERE status='booked'").fetchone()[0],
            "notes": c.execute("SELECT count(*) FROM ops_notification").fetchone()[0],
            "ivs": c.execute("SELECT count(*) FROM ops_intervention").fetchone()[0],
            "day": round(core.story_day(c), 1), "done": st["done"], "emma_pathway": st["emma_pathway"], "cohort": len(st["cohort"])}


@STORY
def test_10_reset_is_idempotent_and_scoped():
    from reset_story import main
    with connect() as c:
        before = _counts(c)
    main()
    with connect() as c:
        a = _initial(c)
    main()
    with connect() as c:
        b = _initial(c)
        after = _counts(c)
    assert a == b and a["done"] == [] and a["emma_pathway"] is None and a["ivs"] == 0 and a["notes"] == 0
    assert after == before                                          # national scenarios, runs, other patients untouched


@STORY
def test_3_to_11_full_story():
    with connect() as c:
        st = story.state(c)
        assert st and st["done"] == [], "run after reset"
    run = lambda k: _step(k)
    for k in ("book_gp", "attend_gp", "referral_received", "reminder"):
        run(k)
    with pytest.raises(svc.Conflict):                               # director refuses out-of-order steps
        with connect() as c:
            story.run_step(c, "treatment")
    for k in ("confirm_specialist", "attend_specialist"):
        run(k)
    with connect() as c:                                            # Test 3: normal pathway
        s = svc.get_state(c, story.emma_pathway(c))
        assert s["current_stage"]["id"] == "SPECIALIST_ASSESSMENT"
        assert any(a["stage"] == "COLONOSCOPY" and a["status"] == "booked" for a in s["appointments"])
        appts_before = {a["id"]: a["status"] for a in s["appointments"]}
        core.set_story_clock(c, 6, 7.5)
        d = interventions.trigger_resource_down(c, "res-pkks-endo2", 48, background=False)
        story_state = story.state(c); story_state["done"].append("disruption"); story.save(c, story_state)
    with connect() as c:                                            # Test 4: outage changes capacity + invalidates bookings
        assert c.execute("SELECT status FROM ops_resource WHERE id='res-pkks-endo2'").fetchone()[0] == "offline"
        assert c.execute("SELECT count(*) FROM ops_slot WHERE resource_id='res-pkks-endo2' AND status='blocked'").fetchone()[0] >= 8
        assert d["summary"]["affected_count"] == 8
        iv = interventions.current(c)
        assert iv["status"] == "AWAITING_APPROVAL" and iv["proposal"]["controller"]["level"] == "global"   # Test 5: escalated
        assert iv["proposal"]["state_version"] == evaluate.state_version(c)                             # computed on CURRENT state
        s = svc.get_state(c, story.emma_pathway(c))                 # Test 6: nothing executed before approval
        assert {a["id"]: a["status"] for a in s["appointments"]} == appts_before
        # stale-plan protection: change the bookable state, approval must be refused
        slot = c.execute("""SELECT s.id FROM ops_slot s JOIN ops_resource r ON r.id=s.resource_id WHERE r.capability='colonoscopy'
                            AND s.status='free' ORDER BY s.starts_at DESC LIMIT 1""").fetchone()[0]
        c.execute("UPDATE ops_slot SET status='booked', booked_by='test' WHERE id=%s", (slot,))
        with pytest.raises(svc.Conflict):
            interventions.approve(c, iv["id"], "admin-donny", "test", background_agents=False)
        c.rollback()
    with connect() as c:
        iv = interventions.current(c)
        interventions.approve(c, iv["id"], "admin-donny", "test", background_agents=False)
    with connect() as c:                                            # Test 8: no overbooking
        assert c.execute("""SELECT count(*) FROM (SELECT appointment_id FROM ops_slot WHERE appointment_id IS NOT NULL
                            GROUP BY appointment_id HAVING count(*) > 1) x""").fetchone()[0] == 0
        assert c.execute("""SELECT count(*) FROM ops_slot s JOIN appointment a ON a.id=s.appointment_id
                            WHERE a.status='booked' AND s.status <> 'booked'""").fetchone()[0] == 0
        n_appts = c.execute("SELECT count(*) FROM appointment").fetchone()[0]
        n_notes = c.execute("SELECT count(*) FROM ops_notification").fetchone()[0]
        interventions.approve(c, iv["id"], "admin-donny", "repeat", background_agents=False)   # repeated approval: no-op
        assert c.execute("SELECT count(*) FROM appointment").fetchone()[0] == n_appts
        assert c.execute("SELECT count(*) FROM ops_notification").fetchone()[0] == n_notes
    for k in ("confirm_new", "pre_procedure", "miss"):
        run(k)
    with connect() as c:                                            # Test 7: no-show -> follow-up with outer check
        fu = c.execute("SELECT status, proposal FROM ops_intervention WHERE source='followup' ORDER BY created_at DESC LIMIT 1").fetchone()
        assert fu[0] == "AWAITING_APPROVAL" and fu[1]["controller"]["level"] in ("local", "global")
        assert all("p_on_time" in o for o in fu[1]["options"])
        assert svc.get_state(c, story.emma_pathway(c))["status"] == "active"   # never silently closed
    for k in ("followup_rebook", "attend_colonoscopy", "histology", "staging", "mdt", "treatment"):
        run(k)
    with connect() as c:                                            # Test 9 + 11: one state everywhere, story complete
        ov = story.overview(c)
        assert ov["next"] is None and all(s["done"] for s in ov["steps"])
        s = svc.get_state(c, story.emma_pathway(c))
        mine = svc.list_pathways(c, patient_id=story.emma_patient(c))
        assert s["current_stage"]["id"] == "SURGERY" and mine[0]["current_stage"] == "SURGERY"
        from backend.api.ops import state as ops_state
        assert ops_state(c)["emma"]["stage"]["id"] == "SURGERY"
        assert c.execute("SELECT count(*) FROM ops_story_event WHERE run=%s AND status='completed'", (story.state(c)["run"],)).fetchone()[0] >= 15


def _step(k):
    with connect() as c:
        return story.run_step(c, k)
