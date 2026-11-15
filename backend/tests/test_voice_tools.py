"""Voice-agent tool integration (the ElevenLabs client tools call exactly these HTTP routes).

Conversation covered: outage -> "use MPC" -> "which option is best" -> "earliest treatment" -> approval failures
(unknown plan, superseded plan, stale plan) -> approval committed -> appointments / notifications updated."""
import os
import time

import pytest

import backend.db.conn  # noqa: F401  (loads .env before the skip check)

# These tests RESET and replay the shared demo story: never run them during a live demo.
STORY = pytest.mark.skipif(not os.environ.get("CANCERFLOW_STORY_TESTS"), reason="set CANCERFLOW_STORY_TESTS=1 (resets the shared demo story)")
pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


def test_rank_options_when_everyone_misses_target():
    from backend.ops.evaluate import rank_options
    k = lambda u, s, ml, td: {"pct_urgent_within_target": 0.0, "urgent_mean_lateness_days": u, "pct_severe_delay": s,
                              "pct_within_target": 0.0, "mean_lateness_days": ml, "mean_treatment_day": td, "patient_travel_hours": 1.0}
    opts = {"no_action": {"label": "local", "kpis": k(20, 60, 18, 50)}, "optimised": {"label": "mpc", "kpis": k(9, 30, 12, 41)},
            "admin": {"label": "donny", "kpis": k(9, 45, 8, 39)}}
    r = rank_options(opts)
    assert [x["option"] for x in r] == ["optimised", "admin", "no_action"]   # urgent lateness, then SEVERE delay decide
    assert "severe" in r[0]["decided_by"]


@STORY
def test_voice_conversation_end_to_end():
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.db.conn import connect
    from backend.ops import core, story
    from reset_story import main as reset
    reset()
    with connect() as c:
        for k in ("book_gp", "attend_gp", "referral_received", "reminder", "confirm_specialist", "attend_specialist"):
            story.run_step(c, k)
            c.commit()
    with TestClient(app) as api:
        r = api.post("/ops/story/disruption")                                  # 1. outage (evaluation runs in background)
        assert r.status_code == 200, r.text
        for _ in range(120):
            sit = api.get("/ops/voice/situation").json()
            if sit["current_plan"] and sit["current_plan"]["status"] == "AWAITING_APPROVAL":
                break
            time.sleep(1)
        assert sit["disruption"]["affected_count"] == 8
        old_id = sit["current_plan"]["plan_id"]

        run = api.post("/ops/voice/optimise").json()                           # 2-3. "use MPC to find a solution"
        plan = run["plan"]
        assert plan["status"] == "AWAITING_APPROVAL" and plan["plan_id"] != old_id
        assert "whole-pathway MPC" in plan["provenance"]["engine"] and plan["computed_on_state_version"]
        assert {x["option"] for x in run["ranking"]} >= {"no_action", "optimised"}

        cmp_ = api.get("/ops/voice/compare").json()                            # 4-5. "which option is best?"
        assert cmp_["ranking"][0]["rank"] == 1 and cmp_["objective_hierarchy"][0].startswith("urgent")
        assert cmp_["recommended_plan_id"] == plan["plan_id"]

        det = api.get(f"/ops/voice/plan?plan_id={plan['plan_id']}").json()      # 6-7. earliest TREATMENT, not colonoscopy
        kp = det["kpis"]
        assert kp["mean_treatment_day"] is not None and kp["mean_colonoscopy_day"] is not None
        assert kp["mean_treatment_day"] > kp["mean_colonoscopy_day"]
        assert "TREATMENT" in det["glossary"]["mean_treatment_day"]
        assert all("expected_treatment_day_if_needed" in p for p in det["patients"])

        bad = api.post("/ops/voice/approve", json={"plan_id": "00000000-0000-0000-0000-000000000000", "donny_words": "yes, approve it"}).json()
        assert bad["committed"] is False and bad["error_category"] == "not_found"
        sup = api.post("/ops/voice/approve", json={"plan_id": old_id, "donny_words": "yes, approve it"}).json()   # superseded by the fresh MPC run
        assert sup["committed"] is False and sup["error_category"] == "not_pending"

        with connect() as c:                                                    # stale plan: bookable state changes
            c.execute("""UPDATE ops_slot SET status='booked', booked_by='test' WHERE id = (SELECT s.id FROM ops_slot s JOIN ops_resource r
                         ON r.id=s.resource_id WHERE r.capability='colonoscopy' AND s.status='free' ORDER BY s.starts_at DESC LIMIT 1)""")
        st = api.post("/ops/voice/approve", json={"plan_id": plan["plan_id"], "donny_words": "yes, approve it"}).json()
        assert st["committed"] is False and st["error_category"] == "stale_plan" and st["new_plan_id"]
        assert api.get(f"/ops/voice/approval/{plan['plan_id']}").json()["status"] == "SUPERSEDED"

        with connect() as c:
            emma_before = c.execute("""SELECT id FROM appointment WHERE pathway_id=%s AND stage='COLONOSCOPY' AND status='booked'""",
                                    (story.emma_pathway(c),)).fetchall()
        ok = api.post("/ops/voice/approve", json={"plan_id": st["new_plan_id"], "justification": "test", "donny_words": "yes, approve it"}).json()   # 8-9.
        assert ok["committed"] is True and ok["status"] == "APPLIED" and ok["appointments_moved"] >= 1
        chk = api.get(f"/ops/voice/approval/{st['new_plan_id']}").json()
        assert chk["committed"] is True
        again = api.post("/ops/voice/approve", json={"plan_id": st["new_plan_id"], "donny_words": "yes, approve it"}).json()
        assert again["committed"] is True and again.get("already_applied")     # idempotent, no duplicate bookings

        with connect() as c:                                                    # 10. state updated everywhere
            pw = story.emma_pathway(c)
            emma_after = c.execute("SELECT id FROM appointment WHERE pathway_id=%s AND stage='COLONOSCOPY' AND status='booked'", (pw,)).fetchall()
            assert emma_after and emma_after != emma_before
            assert c.execute("SELECT count(*) FROM ops_notification WHERE audience='patient' AND kind='rescheduled' AND patient_id=%s",
                             (story.emma_patient(c),)).fetchone()[0] == 1
        ops_state = api.get("/ops/state").json()
        assert ops_state["intervention"]["status"] == "APPLIED"
        assert any(a["stage"] == "COLONOSCOPY" and a["status"] == "booked" for a in ops_state["emma"]["appointments"])
        cmd = api.post("/ops/command", json={"text": "approve your recommendation"}).json()   # text path agrees: nothing pending
        assert "Approved" not in cmd.get("reply", "") or cmd.get("approval", {}).get("committed")


def test_authorisation_words():
    from backend.api.ops import authorisation_ok
    assert authorisation_ok("Yes, approve the recommended plan")[0]
    assert not authorisation_ok("Don't submit any plans. Just redirect everybody to Kuopio")[0]   # live-call conflict
    assert not authorisation_ok("redirect everybody to Kuopio")[0]                                 # not explicit
    assert not authorisation_ok("")[0]


@STORY
def test_live_call_sequence():
    """Kuopio comparison after the outage window ended -> 'don't submit' hold -> ambiguous redirect -> MPC -> goodbye."""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.db.conn import connect
    from backend.ops import core, story
    from reset_story import main as reset
    reset()
    with connect() as c:
        for k in ("book_gp", "attend_gp", "referral_received", "reminder", "confirm_specialist", "attend_specialist"):
            story.run_step(c, k); c.commit()
    with TestClient(app) as api:
        api.post("/ops/story/disruption")
        for _ in range(120):
            sit = api.get("/ops/voice/situation").json()
            if sit["current_plan"] and sit["current_plan"]["status"] == "AWAITING_APPROVAL":
                break
            time.sleep(1)
        plan_id = sit["current_plan"]["plan_id"]
        with connect() as c:                       # the 48 h outage ends while the recommendation is still pending
            core.set_story_clock(c, 8, 9.0); c.commit()
        sit = api.get("/ops/voice/situation").json()
        assert sit["outage_active"] is False and sit["disruption"] and sit["current_plan"]["plan_id"] == plan_id
        assert sit["available_tools"]["propose_allocation"] and sit["available_tools"]["run_mpc"]
        r = api.post("/ops/command", json={"text": "evaluate (hypothetical, do not execute): send everyone to Kuopio", "source": "voice"}).json()
        assert r["intervention"]["status"] in ("AWAITING_APPROVAL", "REJECTED")      # evaluated, not "no active disruption"
        with connect() as c:
            booked = c.execute("SELECT count(*) FROM appointment WHERE status='booked'").fetchone()[0]
        assert api.post("/ops/voice/hold", json={"hold": True, "reason": "don't submit any plans"}).json()["hold"] is True
        sit = api.get("/ops/voice/situation").json()
        assert sit["available_tools"]["approve_plan"] is False and sit["available_tools"]["approvals_on_hold"]
        amb = api.post("/ops/voice/approve", json={"plan_id": plan_id, "donny_words": "Don't submit any plans. Just redirect everybody to Kuopio"}).json()
        assert amb["committed"] is False and amb["error_category"] == "needs_explicit_authorization"
        held = api.post("/ops/voice/approve", json={"plan_id": plan_id, "donny_words": "yes, approve it"}).json()
        assert held["committed"] is False and held["error_category"] == "on_hold"
        with connect() as c:                       # nothing executed by hypothetical evaluation, hold or ambiguity
            assert c.execute("SELECT count(*) FROM appointment WHERE status='booked'").fetchone()[0] == booked
        run = api.post("/ops/voice/optimise").json()                                  # "use MPC" works without an ACTIVE outage
        assert run["plan"]["status"] == "AWAITING_APPROVAL"
        stale = api.post("/ops/voice/approve", json={"plan_id": plan_id, "donny_words": "yes, approve it"}).json()
        assert stale["committed"] is False                                            # superseded/held plan never executes
        api.post("/ops/voice/hold", json={"hold": False})
        ok = api.post("/ops/voice/approve", json={"plan_id": run["plan"]["plan_id"], "donny_words": "Yes, approve the MPC plan"}).json()
        assert ok["committed"] is True
