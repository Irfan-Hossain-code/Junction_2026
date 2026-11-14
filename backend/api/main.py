"""CancerFlow API.  Run:  uvicorn backend.api.main:app --reload --port 8000   (docs at /docs)

Auth (proof of concept): POST /auth/login {user_id} -> send header `X-User-Id: <id>` on every request.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from backend.db.conn import connect  # noqa: F401  (loads .env)
from backend.pathways.pack import get_pack, list_packs
from backend.services import pathways as svc

pool: Optional[ConnectionPool] = None
_ws_clients: set[WebSocket] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool, _loop
    _loop = asyncio.get_running_loop()
    pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=6, open=True,
                          kwargs={"options": "-c search_path=cf,public", "connect_timeout": 15})
    with pool.connection() as c:
        for p in list_packs():
            svc.ensure_pack(c, p)
    yield
    pool.close()


app = FastAPI(title="CancerFlow API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def get_conn():
    with pool.connection() as conn:   # commits on success, rolls back on exception
        yield conn


def broadcast(msg: dict) -> None:
    """Fire-and-forget push to all websocket clients (safe from sync endpoints)."""
    if not _loop or not _ws_clients:
        return
    for ws in list(_ws_clients):
        asyncio.run_coroutine_threadsafe(ws.send_json(msg), _loop)


def current_user(x_user_id: Optional[str] = Header(default=None), conn=Depends(get_conn)) -> dict:
    if not x_user_id:
        raise HTTPException(401, "missing X-User-Id header (POST /auth/login first)")
    try:
        return svc.get_user(conn, x_user_id)
    except svc.NotFound:
        raise HTTPException(401, f"unknown user {x_user_id}")


def _err(e: Exception):
    if isinstance(e, svc.NotFound):
        raise HTTPException(404, str(e))
    if isinstance(e, (svc.Conflict, KeyError)):
        raise HTTPException(409, str(e))
    raise e


# ------------------------------------------------------------------ models
class LoginIn(BaseModel):
    user_id: str


class PatientIn(BaseModel):
    display_name: str
    birth_year: Optional[int] = None
    sex: Optional[Literal["F", "M", "X"]] = None
    home_region_id: Optional[str] = None
    lon: Optional[float] = None
    lat: Optional[float] = None


class PathwayIn(BaseModel):
    patient_id: str
    pack_id: str = "colorectal"
    entry_route: str = Field(description="'screening' or 'symptomatic' for colorectal")
    opened_at: Optional[datetime] = None
    facility_id: Optional[str] = None


class EventIn(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, description="client-generated; re-sending the same id is a no-op")
    type: Literal["STAGE_ENTERED", "NOTE", "RESULT_RECORDED", "APPOINTMENT_BOOKED", "APPOINTMENT_STATUS", "ALERT_ACKNOWLEDGED"]
    stage: Optional[str] = None
    facility_id: Optional[str] = None
    occurred_at: Optional[datetime] = None
    payload: dict[str, Any] = {}


class ClockIn(BaseModel):
    offset_days: float = Field(description="demo clock offset from real time, in days (0 = real time)")


# ------------------------------------------------------------------ meta
@app.get("/health")
def health(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"ok": True, "now": svc.now(conn).isoformat()}


@app.get("/packs")
def packs():
    return [{"id": p.id, "version": p.version, "title": p.title, "entry_routes": {k: v.label for k, v in p.entry_routes.items()}}
            for p in list_packs()]


@app.get("/packs/{pack_id}")
def pack(pack_id: str):
    try:
        return get_pack(pack_id).model_dump(mode="json")
    except KeyError as e:
        raise HTTPException(404, str(e))


# ------------------------------------------------------------------ auth (demo)
@app.get("/auth/users")
def users(role: Optional[Literal["doctor", "patient"]] = None, conn=Depends(get_conn)):
    q = "SELECT u.*, f.name AS facility_name FROM app_user u LEFT JOIN facility f ON f.id=u.facility_id"
    rows = conn.cursor(row_factory=dict_row).execute(q + (" WHERE u.role=%s" if role else "") + " ORDER BY u.role, u.display_name",
                                                     (role,) if role else ()).fetchall()
    return rows


@app.post("/auth/login")
def login(body: LoginIn, conn=Depends(get_conn)):
    try:
        u = svc.get_user(conn, body.user_id)
    except svc.NotFound:
        raise HTTPException(404, f"unknown user {body.user_id}")
    return {"user": u, "header": {"X-User-Id": u["id"]}}


@app.get("/me")
def me(user=Depends(current_user)):
    return user


@app.get("/me/pathways")
def my_pathways(user=Depends(current_user), conn=Depends(get_conn)):
    if user["role"] == "patient":
        return svc.list_pathways(conn, patient_id=user["patient_id"])
    return svc.list_pathways(conn, facility_id=user["facility_id"], status="active")


@app.get("/worklist")
def worklist(user=Depends(current_user), conn=Depends(get_conn)):
    """Doctor view: active pathways touching my facility + open alerts I am responsible for."""
    if user["role"] != "doctor":
        raise HTTPException(403, "doctors only")
    return {"pathways": svc.list_pathways(conn, facility_id=user["facility_id"], status="active"),
            "alerts": svc.list_alerts(conn, facility_id=user["facility_id"])}


# ------------------------------------------------------------------ patients & pathways
@app.post("/patients", status_code=201)
def create_patient(body: PatientIn, user=Depends(current_user), conn=Depends(get_conn)):
    if user["role"] != "doctor":
        raise HTTPException(403, "doctors only")
    return svc.create_patient(conn, body.display_name, body.birth_year, body.sex, body.home_region_id, lon=body.lon, lat=body.lat)


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str, conn=Depends(get_conn)):
    try:
        return svc.get_patient(conn, patient_id)
    except Exception as e:
        _err(e)


@app.get("/patients/{patient_id}/pathways")
def patient_pathways(patient_id: str, conn=Depends(get_conn)):
    return svc.list_pathways(conn, patient_id=patient_id)


@app.post("/pathways", status_code=201)
def create_pathway(body: PathwayIn, user=Depends(current_user), conn=Depends(get_conn)):
    try:
        st = svc.create_pathway(conn, body.patient_id, body.pack_id, body.entry_route, user,
                                opened_at=body.opened_at, facility_id=body.facility_id)
    except Exception as e:
        _err(e)
    broadcast({"kind": "pathway_created", "pathway_id": st["id"], "stage": st["current_stage"]["id"]})
    return st


@app.get("/pathways")
def pathways(facility_id: Optional[str] = None, status: Optional[Literal["active", "closed"]] = None,
             stage: Optional[str] = None, patient_id: Optional[str] = None,
             limit: int = Query(200, le=2000), conn=Depends(get_conn)):
    return svc.list_pathways(conn, patient_id=patient_id, facility_id=facility_id, status=status, stage=stage, limit=limit)


@app.get("/pathways/{pathway_id}")
def pathway(pathway_id: uuid.UUID, conn=Depends(get_conn)):
    try:
        return svc.get_state(conn, pathway_id)
    except Exception as e:
        _err(e)


@app.post("/pathways/{pathway_id}/events", status_code=201)
def post_event(pathway_id: uuid.UUID, body: EventIn, user=Depends(current_user), conn=Depends(get_conn)):
    if user["role"] == "patient":
        owner = conn.execute("SELECT patient_id FROM pathway WHERE id=%s", (pathway_id,)).fetchone()
        if not owner or owner[0] != user["patient_id"]:
            raise HTTPException(403, "patients may only post to their own pathways")
    try:
        res = svc.append_event(conn, pathway_id, body.model_dump(), user)
        st = svc.get_state(conn, pathway_id)
    except Exception as e:
        _err(e)
    if not res["duplicate"]:
        broadcast({"kind": "event", "pathway_id": str(pathway_id), "type": body.type, "stage": body.stage,
                   "current_stage": st["current_stage"]["id"] if st["current_stage"] else None,
                   "open_alerts": len([a for a in st["alerts"] if a["status"] != "resolved"])})
    return {"result": res, "state": st}


# ------------------------------------------------------------------ alerts
@app.get("/alerts")
def alerts(status: Optional[Literal["open", "acknowledged", "resolved"]] = None, facility_id: Optional[str] = None,
           severity: Optional[Literal["info", "warning", "critical"]] = None, conn=Depends(get_conn)):
    return svc.list_alerts(conn, status=status, facility_id=facility_id, severity=severity)


@app.post("/alerts/{alert_id}/acknowledge")
def ack(alert_id: uuid.UUID, user=Depends(current_user), conn=Depends(get_conn)):
    row = conn.execute("SELECT pathway_id FROM alert WHERE id=%s", (alert_id,)).fetchone()
    if not row:
        raise HTTPException(404, "alert not found")
    svc.append_event(conn, row[0], {"id": uuid.uuid4(), "type": "ALERT_ACKNOWLEDGED",
                                    "payload": {"alert_id": str(alert_id)}}, user)
    return svc.get_state(conn, row[0])


# ------------------------------------------------------------------ reference data
@app.get("/facilities")
def facilities(capability: Optional[str] = None, region_id: Optional[str] = None, kind: Optional[str] = None,
               conn=Depends(get_conn)):
    q = ["""SELECT f.id, f.name, f.kind, f.region_id, f.yta_id, f.address, f.tag::text AS tag, f.source_id,
                   ST_X(f.geom::geometry) AS lon, ST_Y(f.geom::geometry) AS lat,
                   coalesce((SELECT json_agg(json_build_object('capability', c.capability, 'tag', c.tag, 'source_id', c.source_id))
                             FROM facility_capability c WHERE c.facility_id=f.id), '[]') AS capabilities
            FROM facility f WHERE true"""]
    a: list = []
    if capability:
        q.append("AND EXISTS (SELECT 1 FROM facility_capability c WHERE c.facility_id=f.id AND c.capability=%s)"); a.append(capability)
    if region_id:
        q.append("AND f.region_id=%s"); a.append(region_id)
    if kind:
        q.append("AND f.kind=%s"); a.append(kind)
    return conn.cursor(row_factory=dict_row).execute(" ".join(q) + " ORDER BY f.name", a).fetchall()


@app.get("/regions")
def regions(kind: Optional[str] = None, conn=Depends(get_conn)):
    q = """SELECT id, kind, name_fi, name_en, parent_id, sotkanet_id, population,
                  ST_X(centroid::geometry) AS lon, ST_Y(centroid::geometry) AS lat FROM region"""
    return conn.cursor(row_factory=dict_row).execute(q + (" WHERE kind=%s" if kind else "") + " ORDER BY id",
                                                     (kind,) if kind else ()).fetchall()


# ------------------------------------------------------------------ demo clock / admin
@app.get("/clock")
def clock(conn=Depends(get_conn)):
    return {"now": svc.now(conn).isoformat()}


@app.post("/admin/clock")
def set_clock(body: ClockIn, conn=Depends(get_conn)):
    t = svc.set_clock_offset(conn, body.offset_days)
    n = svc.scan_all(conn, t)
    broadcast({"kind": "clock", "now": t.isoformat(), "rescanned": n})
    return {"now": t.isoformat(), "rescanned_pathways": n}


@app.post("/admin/scan-alerts")
def scan(conn=Depends(get_conn)):
    n = svc.scan_all(conn)
    broadcast({"kind": "alerts_rescanned", "pathways": n})
    return {"rescanned_pathways": n}


# ------------------------------------------------------------------ live stream
@app.websocket("/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()   # keepalive; clients don't need to send anything
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
