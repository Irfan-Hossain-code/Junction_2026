"""Schema invariants. Each test runs in a transaction that is rolled back -> DB stays clean."""
import uuid

import psycopg
import pytest

from backend.db.conn import connect
from backend.db.migrate import migrate


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    migrate()


@pytest.fixture
def db():
    conn = connect()
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def seed_minimal(db):
    db.execute("INSERT INTO source(id,title,kind) VALUES ('t-src','test','generator')")
    db.execute("INSERT INTO region(id,kind,name_fi) VALUES ('t-reg','wellbeing_county','Testi')")
    db.execute("""INSERT INTO facility(id,name,kind,region_id,geom,tag,source_id)
                  VALUES ('t-fac','Test hospital','central_hospital','t-reg',
                          ST_GeogFromText('POINT(25 62)'),'OBSERVED','t-src')""")
    db.execute("""INSERT INTO pathway_pack(id,version,sha256,definition)
                  VALUES ('t-pack',1,'x','{}')""")
    db.execute("INSERT INTO patient(id,display_name) VALUES ('S-T1','Test Patient')")
    pid = db.execute("""INSERT INTO pathway(patient_id,pack_id,pack_version,entry_route,opened_at)
                        VALUES ('S-T1','t-pack',1,'symptomatic',now()) RETURNING id""").fetchone()[0]
    return pid


def add_event(db, pid, seq):
    eid = uuid.uuid4()
    db.execute("""INSERT INTO event(id,pathway_id,seq,type,actor_role,occurred_at)
                  VALUES (%s,%s,%s,'STAGE_ENTERED','system',now())""", (eid, pid, seq))
    return eid


def test_event_log_is_append_only(db):
    pid = seed_minimal(db)
    eid = add_event(db, pid, 1)
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE event SET type='X' WHERE id=%s", (eid,))


def test_event_delete_blocked(db):
    pid = seed_minimal(db)
    eid = add_event(db, pid, 1)
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("DELETE FROM event WHERE id=%s", (eid,))


def test_event_seq_unique_per_pathway(db):
    pid = seed_minimal(db)
    add_event(db, pid, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_event(db, pid, 1)


def test_capacity_cannot_claim_to_be_observed(db):
    seed_minimal(db)
    db.execute("INSERT INTO capability(code,description) VALUES ('t-cap','test')")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("""INSERT INTO facility_capacity(facility_id,capability,per_week_low,per_week_high,tag,source_id)
                      VALUES ('t-fac','t-cap',1,2,'OBSERVED','t-src')""")


def test_patient_must_be_synthetic(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO patient(id,display_name,tag) VALUES ('S-T2','x','OBSERVED')")


def test_sim_metric_must_be_simulated(db):
    db.execute("INSERT INTO scenario(name,pack_id) VALUES ('t','t-pack')")
    sid = db.execute("SELECT id FROM scenario WHERE name='t'").fetchone()[0]
    rid = db.execute("""INSERT INTO sim_run(scenario_id,model_version,seed,replications,horizon_days)
                        VALUES (%s,'t',1,1,1) RETURNING id""", (sid,)).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("""INSERT INTO sim_metric(run_id,scope_kind,scope_id,metric,mean,tag)
                      VALUES (%s,'national','fi','m',1,'OBSERVED')""", (rid,))


def test_one_unresolved_alert_per_rule(db):
    pid = seed_minimal(db)
    db.execute("INSERT INTO alert(pathway_id,rule_id,severity,message) VALUES (%s,'r1','warning','a')", (pid,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO alert(pathway_id,rule_id,severity,message) VALUES (%s,'r1','warning','b')", (pid,))


def test_resolved_alert_allows_new_one(db):
    pid = seed_minimal(db)
    db.execute("INSERT INTO alert(pathway_id,rule_id,severity,message,status) VALUES (%s,'r1','warning','a','resolved')", (pid,))
    db.execute("INSERT INTO alert(pathway_id,rule_id,severity,message) VALUES (%s,'r1','warning','b')", (pid,))


def test_doctor_requires_facility(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO app_user(id,role,display_name) VALUES ('u1','doctor','Dr X')")
