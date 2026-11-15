"""Operations-layer primitives: demo clock anchored to story day 0, actors, slots, notifications, broadcasting."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from psycopg.rows import dict_row

from backend.ops.world import FAC_SHORT, FI
from backend.services import pathways as svc

NS = uuid.UUID("6f1c0c8e-2a4b-4c41-9a51-0c0ffee00042")      # namespace for deterministic (idempotent) ids

GP_USER = "gp-ops-joensuu"
NURSE_USER = "nurse-pkks-scr"
SURGEON_USER = "dr-pkks-surg"
ENDO_USER = "dr-pkks-endo"
COORD_USER = "admin-donny"


def did(*parts) -> uuid.UUID:
    """Deterministic UUID for an operational action: re-running the same action never duplicates it."""
    return uuid.uuid5(NS, "|".join(str(p) for p in parts))


# ------------------------------------------------------------------ settings / clock
def get_setting(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM app_setting WHERE key=%s", (key,)).fetchone()
    return r[0] if r else default


def set_setting(conn, key: str, value) -> None:
    conn.execute("""INSERT INTO app_setting(key,value) VALUES (%s,%s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (key, json.dumps(value, default=str)))


def t0(conn) -> datetime:
    v = get_setting(conn, "ops_t0")
    if not v:
        raise LookupError("operations demo not initialised: POST /ops/demo/reset")
    return datetime.fromisoformat(v)


def story_time(conn, day: float, hour: float = 0.0) -> datetime:
    return t0(conn) + timedelta(days=day, hours=hour)


def story_day(conn, at: Optional[datetime] = None) -> float:
    at = at or svc.now(conn)
    return (at - t0(conn)).total_seconds() / 86400


def set_story_clock(conn, day: float, hour: float) -> datetime:
    """Move the demo clock so that 'now' = story day + hour (Finnish time). Rescans only demo pathways (fast)."""
    target = story_time(conn, day, hour)
    offset = (target - datetime.now(timezone.utc)).total_seconds() / 86400
    svc.set_clock_offset(conn, offset)
    for (pid,) in conn.execute("""SELECT p.id FROM pathway p WHERE p.status='active' AND p.patient_id LIKE 'OPS-%%'""").fetchall():
        svc.refresh_projections(conn, pid)
    from backend.ops.interventions import resolve_expired
    resolve_expired(conn)
    emit({"kind": "clock", "now": svc.now(conn).isoformat()})
    return svc.now(conn)


def fmt_time(at: datetime) -> str:
    return at.astimezone(FI).strftime("%a %d %b %H:%M")


def actor(conn, user_id: str) -> dict:
    return svc.get_user(conn, user_id)


def post(conn, pathway_id, ev: dict, user_id: str) -> dict:
    """Append one care event as a real user (same validation path as the care app). Idempotent on ev['id']."""
    res = svc.append_event(conn, pathway_id, ev, actor(conn, user_id))
    if not res.get("duplicate"):
        emit({"kind": "event", "pathway_id": str(pathway_id), "type": ev["type"], "stage": ev.get("stage")})
    return res


# ------------------------------------------------------------------ live updates
def emit(msg: dict) -> None:
    """Broadcast on WS /stream (both apps listen). Imported lazily: the API module owns the client set."""
    try:
        from backend.api.main import broadcast
        broadcast(msg)
    except Exception:
        pass


# ------------------------------------------------------------------ notifications
def notify(conn, *, key: str, audience: str, kind: str, title: str, body: str = "", severity: str = "info",
           patient_id: str | None = None, facility_id: str | None = None, pathway_id=None, payload: dict | None = None,
           at: datetime | None = None) -> bool:
    """Insert once per dedupe key. Returns True if this call created it."""
    r = conn.execute("""INSERT INTO ops_notification(dedupe_key,audience,patient_id,facility_id,pathway_id,severity,kind,title,body,payload,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
                     (key, audience, patient_id, facility_id, pathway_id, severity, kind, title, body,
                      json.dumps(payload or {}, default=str), at or svc.now(conn))).fetchone()
    if r:
        emit({"kind": "notification", "audience": audience, "patient_id": patient_id, "facility_id": facility_id,
              "pathway_id": str(pathway_id) if pathway_id else None, "title": title})
    return bool(r)


def notifications(conn, audience=None, patient_id=None, facility_id=None, limit=60) -> list[dict]:
    q, a = ["SELECT * FROM ops_notification WHERE true"], []
    if audience:
        q.append("AND audience = ANY(%s)"); a.append(audience if isinstance(audience, list) else [audience])
    if patient_id:
        q.append("AND patient_id=%s"); a.append(patient_id)
    if facility_id:
        q.append("AND facility_id=%s"); a.append(facility_id)
    q.append("ORDER BY created_at DESC, id LIMIT %s"); a.append(limit)
    rows = conn.cursor(row_factory=dict_row).execute(" ".join(q), a).fetchall()
    for r in rows:
        r["id"] = str(r["id"])
        r["pathway_id"] = str(r["pathway_id"]) if r["pathway_id"] else None
    return rows


# ------------------------------------------------------------------ slots
def free_slots(conn, resource_id: str, after: datetime, before: Optional[datetime] = None, limit: int = 200) -> list[dict]:
    q = """SELECT s.id, s.starts_at, s.resource_id, r.facility_id FROM ops_slot s JOIN ops_resource r ON r.id=s.resource_id
           WHERE s.resource_id=%s AND s.status='free' AND s.starts_at >= %s""" + (" AND s.starts_at < %s" if before else "") + \
        " ORDER BY s.starts_at LIMIT %s"
    args = (resource_id, after, before, limit) if before else (resource_id, after, limit)
    return conn.cursor(row_factory=dict_row).execute(q, args).fetchall()


def book_slot(conn, slot_id, pathway_id, appointment_id, booked_by: str) -> None:
    n = conn.execute("""UPDATE ops_slot SET status='booked', pathway_id=%s, appointment_id=%s, booked_by=%s
                        WHERE id=%s AND status='free'""", (pathway_id, appointment_id, booked_by, slot_id)).rowcount
    if n != 1:
        raise svc.Conflict("slot is no longer free")


def release_slot_of(conn, appointment_id) -> None:
    conn.execute("""UPDATE ops_slot SET status = CASE WHEN status='blocked' THEN 'blocked' ELSE 'free' END,
                    pathway_id=NULL, appointment_id=NULL, booked_by=NULL WHERE appointment_id=%s""", (appointment_id,))


def facility_label(fid: str) -> str:
    return FAC_SHORT.get(fid, fid)
