"""Operational services: patients, pathways, append-only events, projections, alerts.

All functions take an open psycopg connection and never commit; the caller owns the transaction.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from psycopg.rows import dict_row

from backend.pathways.engine import TransitionError, check_transition, evaluate
from backend.pathways.pack import Pack, get_pack, pack_definition_json

EVENT_TYPES = {"STAGE_ENTERED", "NOTE", "RESULT_RECORDED", "APPOINTMENT_BOOKED", "APPOINTMENT_STATUS", "ALERT_ACKNOWLEDGED"}


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


# ----------------------------------------------------------------- clock
def now(conn) -> datetime:
    off = conn.execute("SELECT value FROM app_setting WHERE key='clock_offset_days'").fetchone()
    days = float(off[0]) if off else 0.0
    return datetime.now(timezone.utc) + timedelta(days=days)


def set_clock_offset(conn, days: float) -> datetime:
    conn.execute("""INSERT INTO app_setting(key,value) VALUES ('clock_offset_days', %s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps(days),))
    return now(conn)


# ----------------------------------------------------------------- packs
def ensure_pack(conn, p: Pack) -> None:
    conn.execute("""INSERT INTO pathway_pack(id,version,sha256,definition) VALUES (%s,%s,%s,%s::jsonb)
                    ON CONFLICT (id,version) DO UPDATE SET sha256=EXCLUDED.sha256, definition=EXCLUDED.definition
                    WHERE pathway_pack.sha256 <> EXCLUDED.sha256""",
                 (p.id, p.version, p.sha256, pack_definition_json(p)))


# ----------------------------------------------------------------- users
def get_user(conn, user_id: str) -> dict:
    u = conn.cursor(row_factory=dict_row).execute("SELECT * FROM app_user WHERE id=%s", (user_id,)).fetchone()
    if not u:
        raise NotFound(f"user {user_id}")
    return u


# ----------------------------------------------------------------- patients & pathways
def create_patient(conn, display_name: str, birth_year: Optional[int], sex: Optional[str],
                   home_region_id: Optional[str] = None, patient_id: Optional[str] = None,
                   lon: Optional[float] = None, lat: Optional[float] = None) -> dict:
    pid = patient_id or f"S-{uuid.uuid4().hex[:8].upper()}"
    geom = f"POINT({lon} {lat})" if lon is not None and lat is not None else None
    conn.execute("""INSERT INTO patient(id,display_name,birth_year,sex,home_region_id,home_geom)
                    VALUES (%s,%s,%s,%s,%s, CASE WHEN %s::text IS NULL THEN NULL ELSE ST_GeogFromText(%s) END)""",
                 (pid, display_name, birth_year, sex, home_region_id, geom, geom))
    return get_patient(conn, pid)


def get_patient(conn, pid: str) -> dict:
    r = conn.cursor(row_factory=dict_row).execute(
        """SELECT id, display_name, birth_year, sex, home_region_id, tag::text AS tag,
                  ST_X(home_geom::geometry) AS lon, ST_Y(home_geom::geometry) AS lat
           FROM patient WHERE id=%s""", (pid,)).fetchone()
    if not r:
        raise NotFound(f"patient {pid}")
    return r


def create_pathway(conn, patient_id: str, pack_id: str, entry_route: str, actor: dict,
                   opened_at: Optional[datetime] = None, facility_id: Optional[str] = None,
                   pathway_id: Optional[uuid.UUID] = None) -> dict:
    p = get_pack(pack_id)
    if entry_route not in p.entry_routes:
        raise Conflict(f"unknown entry_route {entry_route}; options {list(p.entry_routes)}")
    get_patient(conn, patient_id)
    ensure_pack(conn, p)
    t = opened_at or now(conn)
    pw = pathway_id or uuid.uuid4()
    conn.execute("""INSERT INTO pathway(id,patient_id,pack_id,pack_version,entry_route,opened_at)
                    VALUES (%s,%s,%s,%s,%s,%s)""", (pw, patient_id, p.id, p.version, entry_route, t))
    append_event(conn, pw, {"id": uuid.uuid4(), "type": "STAGE_ENTERED",
                            "stage": p.entry_routes[entry_route].first_stage,
                            "occurred_at": t, "facility_id": facility_id, "payload": {}}, actor)
    return get_state(conn, pw)


def _load_pathway(conn, pathway_id, lock=False) -> dict:
    q = "SELECT * FROM pathway WHERE id=%s" + (" FOR UPDATE" if lock else "")
    pw = conn.cursor(row_factory=dict_row).execute(q, (pathway_id,)).fetchone()
    if not pw:
        raise NotFound(f"pathway {pathway_id}")
    return pw


def _load_events(conn, pathway_id) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        "SELECT * FROM event WHERE pathway_id=%s ORDER BY seq", (pathway_id,)).fetchall()


def append_event(conn, pathway_id, ev: dict, actor: dict) -> dict:
    """Validate + append one event, refresh projections and alerts. Idempotent on ev['id']."""
    eid = uuid.UUID(str(ev["id"]))
    dup = conn.execute("SELECT pathway_id FROM event WHERE id=%s", (eid,)).fetchone()
    if dup:
        if str(dup[0]) != str(pathway_id):
            raise Conflict(f"event id {eid} already used on another pathway")
        return {"duplicate": True, "event_id": str(eid)}
    if ev["type"] not in EVENT_TYPES:
        raise Conflict(f"unknown event type {ev['type']}; allowed {sorted(EVENT_TYPES)}")
    pw = _load_pathway(conn, pathway_id, lock=True)
    pack = get_pack(pw["pack_id"])
    events = _load_events(conn, pathway_id)
    occurred = ev.get("occurred_at") or now(conn)
    if isinstance(occurred, str):
        occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    role = actor["role"]
    fac = ev.get("facility_id") or (actor.get("facility_id") if role == "doctor" else None)

    if ev["type"] == "STAGE_ENTERED":
        hist = [e for e in events if e["type"] == "STAGE_ENTERED"]
        cur = hist[-1]["stage"] if hist else None
        last_at = hist[-1]["occurred_at"] if hist else None
        try:
            check_transition(pack, pw["entry_route"], cur, ev.get("stage") or "", role, last_at, occurred)
        except TransitionError as e:
            raise Conflict(str(e)) from e
    elif pw["status"] == "closed" and ev["type"] != "NOTE":
        raise Conflict("pathway is closed; only NOTE events allowed")

    seq = pw["last_seq"] + 1
    conn.execute("""INSERT INTO event(id,pathway_id,seq,type,stage,facility_id,actor_user_id,actor_role,occurred_at,payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                 (eid, pathway_id, seq, ev["type"], ev.get("stage"), fac, actor.get("id"), role, occurred,
                  json.dumps(ev.get("payload") or {}, default=str)))
    conn.execute("UPDATE pathway SET last_seq=%s WHERE id=%s", (seq, pathway_id))
    _apply_side_effects(conn, pathway_id, eid, ev, fac, occurred)
    refresh_projections(conn, pathway_id)
    return {"duplicate": False, "event_id": str(eid), "seq": seq}


def _apply_side_effects(conn, pathway_id, eid, ev, fac, occurred):
    pl = ev.get("payload") or {}
    if ev["type"] == "APPOINTMENT_BOOKED":
        conn.execute("""INSERT INTO appointment(id,pathway_id,stage,facility_id,scheduled_for,source_event)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                     (pl.get("appointment_id") or uuid.uuid4(), pathway_id, pl.get("stage") or ev.get("stage"),
                      pl.get("facility_id") or fac, pl["scheduled_for"], eid))
    elif ev["type"] == "APPOINTMENT_STATUS":
        conn.execute("UPDATE appointment SET status=%s WHERE id=%s AND pathway_id=%s",
                     (pl["status"], pl["appointment_id"], pathway_id))
    elif ev["type"] == "ALERT_ACKNOWLEDGED":
        conn.execute("UPDATE alert SET status='acknowledged' WHERE id=%s AND pathway_id=%s AND status='open'",
                     (pl["alert_id"], pathway_id))


def refresh_projections(conn, pathway_id, as_of: Optional[datetime] = None) -> None:
    pw = _load_pathway(conn, pathway_id)
    pack = get_pack(pw["pack_id"])
    st = evaluate(pack, pw["entry_route"], _load_events(conn, pathway_id), as_of or now(conn))
    closed_at = st.history[-1].at if st.closed else None
    conn.execute("""UPDATE pathway SET current_stage=%s, status=%s, closed_at=%s WHERE id=%s""",
                 (st.current_stage, "closed" if st.closed else "active", closed_at, pathway_id))
    for c in st.clocks:
        if c.started_at:
            conn.execute("""INSERT INTO pathway_clock(pathway_id,clock_id,started_at,stopped_at) VALUES (%s,%s,%s,%s)
                            ON CONFLICT (pathway_id,clock_id) DO UPDATE SET started_at=EXCLUDED.started_at, stopped_at=EXCLUDED.stopped_at""",
                         (pathway_id, c.id, c.started_at, c.stopped_at))
    sync_alerts(conn, pathway_id, st.alerts, as_of or now(conn))


def sync_alerts(conn, pathway_id, specs, at: datetime) -> None:
    cur = conn.cursor(row_factory=dict_row)
    existing = {r["rule_id"]: r for r in cur.execute(
        "SELECT id, rule_id, status FROM alert WHERE pathway_id=%s AND status <> 'resolved'", (pathway_id,))}
    firing = set()
    for a in specs:
        firing.add(a.rule_id)
        if a.rule_id in existing:
            conn.execute("""UPDATE alert SET severity=%s, message=%s, evidence=%s::jsonb, due_at=%s,
                              responsible_role=%s, responsible_facility_id=%s WHERE id=%s""",
                         (a.severity, a.message, json.dumps(a.evidence, default=str), a.due_at,
                          a.responsible_role, a.responsible_facility_id, existing[a.rule_id]["id"]))
        else:
            conn.execute("""INSERT INTO alert(pathway_id,rule_id,severity,message,responsible_facility_id,
                              responsible_role,due_at,evidence,raised_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                         (pathway_id, a.rule_id, a.severity, a.message, a.responsible_facility_id,
                          a.responsible_role, a.due_at, json.dumps(a.evidence, default=str), at))
    for rule, r in existing.items():
        if rule not in firing:
            conn.execute("UPDATE alert SET status='resolved', resolved_at=%s WHERE id=%s", (at, r["id"]))


def scan_all(conn, as_of: Optional[datetime] = None) -> int:
    ids = [r[0] for r in conn.execute("SELECT id FROM pathway WHERE status='active'")]
    for pid in ids:
        refresh_projections(conn, pid, as_of)
    return len(ids)


# ----------------------------------------------------------------- read models
def _iso(x):
    return x.isoformat() if isinstance(x, datetime) else x


def get_state(conn, pathway_id) -> dict[str, Any]:
    pw = _load_pathway(conn, pathway_id)
    pack = get_pack(pw["pack_id"])
    events = _load_events(conn, pathway_id)
    t = now(conn)
    st = evaluate(pack, pw["entry_route"], events, t)
    cur = conn.cursor(row_factory=dict_row)
    alerts = cur.execute("""SELECT id, rule_id, severity, status, message, responsible_role, responsible_facility_id,
                                   due_at, evidence, raised_at, resolved_at
                            FROM alert WHERE pathway_id=%s ORDER BY raised_at DESC""", (pathway_id,)).fetchall()
    appts = cur.execute("""SELECT id, stage, facility_id, scheduled_for, status, confirmed_at FROM appointment
                           WHERE pathway_id=%s ORDER BY scheduled_for""", (pathway_id,)).fetchall()
    stage_obj = lambda s: {"id": s, "label": pack.stages[s].label, "level": pack.stages[s].level,
                           "capability": pack.stages[s].capability, "actors": pack.stages[s].actors}
    return {
        "id": str(pw["id"]), "patient": get_patient(conn, pw["patient_id"]),
        "pack_id": pack.id, "pack_version": pw["pack_version"], "entry_route": pw["entry_route"],
        "status": "closed" if st.closed else "active", "as_of": t.isoformat(),
        "current_stage": stage_obj(st.current_stage) if st.current_stage else None,
        "allowed_next": [stage_obj(s) for s in st.allowed_next],
        "timeline": [{"id": str(e["id"]), "seq": e["seq"], "type": e["type"], "stage": e["stage"],
                      "stage_label": pack.stages[e["stage"]].label if e["stage"] in pack.stages else None,
                      "facility_id": e["facility_id"], "actor_user_id": e["actor_user_id"], "actor_role": e["actor_role"],
                      "occurred_at": _iso(e["occurred_at"]), "recorded_at": _iso(e["recorded_at"]),
                      "payload": e["payload"], "tag": "SYNTHETIC"} for e in events],
        "clocks": [{"id": c.id, "label": c.label, "status": c.status, "severity": c.severity,
                    "started_at": _iso(c.started_at), "stopped_at": _iso(c.stopped_at), "due_at": _iso(c.due_at),
                    "elapsed_days": None if c.elapsed_days is None else round(c.elapsed_days, 2),
                    "targets": c.targets} for c in st.clocks],
        "alerts": [{**a, "id": str(a["id"]), "due_at": _iso(a["due_at"]), "raised_at": _iso(a["raised_at"]),
                    "resolved_at": _iso(a["resolved_at"])} for a in alerts],
        "appointments": [{**a, "id": str(a["id"]), "scheduled_for": _iso(a["scheduled_for"])} for a in appts],
    }


def list_pathways(conn, patient_id=None, facility_id=None, status=None, stage=None, limit=200) -> list[dict]:
    q = ["""SELECT p.id, p.patient_id, pt.display_name, p.pack_id, p.entry_route, p.status, p.current_stage,
                   p.opened_at, p.closed_at,
                   (SELECT e.facility_id FROM event e WHERE e.pathway_id=p.id AND e.facility_id IS NOT NULL
                    ORDER BY e.seq DESC LIMIT 1) AS last_facility_id,
                   (SELECT count(*) FROM alert a WHERE a.pathway_id=p.id AND a.status<>'resolved') AS open_alerts,
                   (SELECT max(CASE a.severity WHEN 'critical' THEN 2 WHEN 'warning' THEN 1 ELSE 0 END)
                      FROM alert a WHERE a.pathway_id=p.id AND a.status<>'resolved') AS max_sev
            FROM pathway p JOIN patient pt ON pt.id=p.patient_id WHERE true"""]
    args: list = []
    if patient_id:
        q.append("AND p.patient_id=%s"); args.append(patient_id)
    if status:
        q.append("AND p.status=%s"); args.append(status)
    if stage:
        q.append("AND p.current_stage=%s"); args.append(stage)
    if facility_id:
        q.append("""AND (EXISTS (SELECT 1 FROM event e WHERE e.pathway_id=p.id AND e.facility_id=%s)
                     OR EXISTS (SELECT 1 FROM alert a WHERE a.pathway_id=p.id AND a.responsible_facility_id=%s))""")
        args += [facility_id, facility_id]
    q.append("ORDER BY max_sev DESC NULLS LAST, p.opened_at DESC LIMIT %s"); args.append(limit)
    rows = conn.cursor(row_factory=dict_row).execute(" ".join(q), args).fetchall()
    sev = {2: "critical", 1: "warning", 0: "info", None: None}
    out = []
    for r in rows:
        pack = get_pack(r["pack_id"])
        cs = r["current_stage"]
        out.append({"id": str(r["id"]), "patient_id": r["patient_id"], "patient_name": r["display_name"],
                    "pack_id": r["pack_id"], "entry_route": r["entry_route"], "status": r["status"],
                    "current_stage": cs, "current_stage_label": pack.stages[cs].label if cs in pack.stages else None,
                    "opened_at": _iso(r["opened_at"]), "closed_at": _iso(r["closed_at"]),
                    "last_facility_id": r["last_facility_id"], "open_alerts": r["open_alerts"],
                    "max_alert_severity": sev[r["max_sev"]]})
    return out


def list_alerts(conn, status=None, facility_id=None, severity=None, limit=500) -> list[dict]:
    q = ["""SELECT a.*, p.patient_id, pt.display_name AS patient_name, p.current_stage
            FROM alert a JOIN pathway p ON p.id=a.pathway_id JOIN patient pt ON pt.id=p.patient_id WHERE true"""]
    args: list = []
    if status:
        q.append("AND a.status=%s"); args.append(status)
    else:
        q.append("AND a.status<>'resolved'")
    if facility_id:
        q.append("AND a.responsible_facility_id=%s"); args.append(facility_id)
    if severity:
        q.append("AND a.severity=%s"); args.append(severity)
    q.append("ORDER BY (a.severity='critical') DESC, a.due_at NULLS LAST LIMIT %s"); args.append(limit)
    rows = conn.cursor(row_factory=dict_row).execute(" ".join(q), args).fetchall()
    return [{**r, "id": str(r["id"]), "pathway_id": str(r["pathway_id"]), "due_at": _iso(r["due_at"]),
             "raised_at": _iso(r["raised_at"]), "resolved_at": _iso(r["resolved_at"])} for r in rows]
