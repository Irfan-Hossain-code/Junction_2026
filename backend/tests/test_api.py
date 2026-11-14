"""End-to-end API flow on the real DB, inside ONE transaction that is rolled back (event log stays clean)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api import main
from backend.db.conn import connect
from backend.db.migrate import migrate


@pytest.fixture(scope="module")
def client():
    migrate()
    conn = connect()
    conn.autocommit = False

    def _conn():
        yield conn

    main.app.dependency_overrides[main.get_conn] = _conn
    with TestClient(main.app) as c:
        conn.execute("INSERT INTO source(id,title,kind) VALUES ('t-api','test','generator') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO region(id,kind,name_fi) VALUES ('t-api-reg','wellbeing_county','T') ON CONFLICT DO NOTHING")
        conn.execute("""INSERT INTO facility(id,name,kind,region_id,geom,tag,source_id)
                        VALUES ('t-api-hc','Test HC','health_center','t-api-reg',ST_GeogFromText('POINT(25 62)'),'OBSERVED','t-api'),
                               ('t-api-hosp','Test Hosp','central_hospital','t-api-reg',ST_GeogFromText('POINT(25.1 62.1)'),'OBSERVED','t-api')""")
        conn.execute("INSERT INTO patient(id,display_name) VALUES ('S-TAPI','Test Patient')")
        conn.execute("""INSERT INTO app_user(id,role,display_name,facility_id) VALUES ('t-gp','doctor','Dr GP','t-api-hc'),
                                                                                      ('t-sp','doctor','Dr Spec','t-api-hosp')""")
        conn.execute("INSERT INTO app_user(id,role,display_name,patient_id) VALUES ('t-pat','patient','Pat','S-TAPI')")
        c.conn = conn
        yield c
    main.app.dependency_overrides.clear()
    conn.rollback()
    conn.close()


GP, SP, PAT = ({"X-User-Id": u} for u in ("t-gp", "t-sp", "t-pat"))


def stage(c, pid, s, hdr, **kw):
    return c.post(f"/pathways/{pid}/events", headers=hdr, json={"type": "STAGE_ENTERED", "stage": s, **kw})


def test_full_symptomatic_flow(client):
    c = client
    assert c.get("/health").json()["ok"]
    assert c.post("/auth/login", json={"user_id": "t-gp"}).status_code == 200
    r = c.post("/pathways", headers=GP, json={"patient_id": "S-TAPI", "entry_route": "symptomatic",
                                              "opened_at": "2026-09-01T09:00:00Z"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["current_stage"]["id"] == "SYMPTOMS_REPORTED"

    # patient cannot record a clinical stage
    assert stage(c, pid, "GP_VISIT", PAT, occurred_at="2026-09-02T09:00:00Z").status_code == 409
    # GP records visit + referral
    assert stage(c, pid, "GP_VISIT", GP, occurred_at="2026-09-02T09:00:00Z").status_code == 201
    r = stage(c, pid, "REFERRAL_SENT", GP, occurred_at="2026-09-02T10:00:00Z")
    st = r.json()["state"]
    assert st["current_stage"]["id"] == "REFERRAL_SENT"
    assert st["timeline"][-1]["facility_id"] == "t-api-hc"          # defaults to doctor's facility
    # invalid jump is rejected with 409 and an explanation
    bad = stage(c, pid, "SURGERY", SP, occurred_at="2026-09-03T10:00:00Z")
    assert bad.status_code == 409 and "not allowed" in bad.json()["detail"]

    # referral never received -> real time is months later -> dwell alert exists
    st = c.get(f"/pathways/{pid}").json()
    assert any(a["rule_id"] == "colorectal.dwell.REFERRAL_SENT" for a in st["alerts"])
    assert any(a["pathway_id"] == pid for a in c.get("/alerts", params={"facility_id": "t-api-hc"}).json())
    wl = c.get("/worklist", headers=GP).json()
    assert any(p["id"] == pid for p in wl["pathways"])

    # hospital receives it -> dwell alert resolves
    stage(c, pid, "REFERRAL_RECEIVED", SP, occurred_at="2026-09-04T10:00:00Z")
    st = c.get(f"/pathways/{pid}").json()
    dwell = [a for a in st["alerts"] if a["rule_id"] == "colorectal.dwell.REFERRAL_SENT"]
    assert dwell and dwell[0]["status"] == "resolved"


def test_idempotent_event(client):
    c = client
    pid = c.post("/pathways", headers=GP, json={"patient_id": "S-TAPI", "entry_route": "symptomatic",
                                                "opened_at": "2026-10-01T09:00:00Z"}).json()["id"]
    eid = str(uuid.uuid4())
    body = {"id": eid, "type": "NOTE", "payload": {"text": "called patient"}}
    a = c.post(f"/pathways/{pid}/events", headers=GP, json=body).json()
    b = c.post(f"/pathways/{pid}/events", headers=GP, json=body).json()
    assert a["result"]["duplicate"] is False and b["result"]["duplicate"] is True
    assert sum(e["id"] == eid for e in b["state"]["timeline"]) == 1


def test_patient_sees_only_own_pathways(client):
    c = client
    mine = c.get("/me/pathways", headers=PAT).json()
    assert mine and all(p["patient_id"] == "S-TAPI" for p in mine)


def test_appointment_and_ack(client):
    c = client
    pid = c.post("/pathways", headers=GP, json={"patient_id": "S-TAPI", "entry_route": "symptomatic",
                                                "opened_at": "2026-08-01T09:00:00Z"}).json()["id"]
    r = c.post(f"/pathways/{pid}/events", headers=GP, json={
        "type": "APPOINTMENT_BOOKED", "payload": {"stage": "GP_VISIT", "scheduled_for": "2026-08-05T10:00:00Z"}})
    assert r.json()["state"]["appointments"][0]["stage"] == "GP_VISIT"
    alert = next(a for a in r.json()["state"]["alerts"] if a["status"] == "open")
    st = c.post(f"/alerts/{alert['id']}/acknowledge", headers=GP).json()
    assert next(a for a in st["alerts"] if a["id"] == alert["id"])["status"] == "acknowledged"
