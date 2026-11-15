"""Digital-twin API: network state, scenarios, simulation runs, optimisation (+ simulation verification)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from backend.opt.allocate import optimise
from backend.sim import params as P
from backend.sim.runner import ensure_baseline, run_scenario
from backend.sim.snapshot import CAPS, apply_scenario, build_snapshot

router = APIRouter(prefix="/twin", tags=["twin"])
_snap_lock = threading.Lock()
_snap_cache: dict[str, Any] = {"snap": None, "at": 0.0}


def get_conn_dep():
    from backend.api.main import get_conn   # late import to avoid cycle
    yield from get_conn()


def base_snapshot(conn, max_age_s: float = 600):
    with _snap_lock:
        if _snap_cache["snap"] is None or time.time() - _snap_cache["at"] > max_age_s:
            _snap_cache["snap"] = build_snapshot(conn)
            _snap_cache["at"] = time.time()
        return _snap_cache["snap"]


def latest_run(conn, scenario_id=None, baseline=False) -> Optional[str]:
    q = """SELECT r.id FROM sim_run r JOIN scenario s ON s.id=r.scenario_id WHERE r.status='done' """
    args: list = []
    if baseline:
        q += "AND s.is_baseline "
    if scenario_id:
        q += "AND s.id=%s "
        args.append(scenario_id)
    row = conn.execute(q + "ORDER BY r.finished_at DESC LIMIT 1", args).fetchone()
    return str(row[0]) if row else None


def metrics_of(conn, run_id) -> dict:
    out: dict = {}
    for r in conn.execute("SELECT scope_kind, scope_id, metric, mean, ci_low, ci_high FROM sim_metric WHERE run_id=%s", (run_id,)):
        out.setdefault(r[0], {}).setdefault(r[1], {})[r[2]] = {"mean": r[3], "ci": [r[4], r[5]], "tag": "SIMULATED"}
    return out


# ------------------------------------------------------------------ assumptions / geo
@router.get("/assumptions")
def assumptions(conn=Depends(get_conn_dep)):
    keep = [k for k in dir(P) if k.isupper()]
    srcs = conn.cursor(row_factory=dict_row).execute("SELECT * FROM source ORDER BY id").fetchall()
    return {"params": {k: getattr(P, k) for k in keep}, "sources": srcs,
            "tags": {"OBSERVED": "published statistic", "REFERENCE": "official target/norm", "EXTERNAL_PROXY": "other country",
                     "DERIVED": "deterministic calculation", "CALIBRATED": "fitted to observed aggregates",
                     "SYNTHETIC": "generated", "SIMULATED": "model output (mean, 95% CI over replications)",
                     "HYPOTHESIS": "assumption / unverified"}}


@router.get("/geo/counties")
def geo_counties(conn=Depends(get_conn_dep)):
    feats = []
    for r in conn.execute("""SELECT id, name_en, parent_id, population, ST_AsGeoJSON(boundary::geometry, 4)
                             FROM region WHERE kind='wellbeing_county' AND id <> 'hva-956'"""):
        feats.append({"type": "Feature", "id": r[0], "geometry": json.loads(r[4]),
                      "properties": {"id": r[0], "name": r[1], "yta": r[2], "population": r[3]}})
    return {"type": "FeatureCollection", "features": feats}


# ------------------------------------------------------------------ network
@router.get("/network")
def network(capability: str = "colonoscopy", run_id: Optional[str] = None, conn=Depends(get_conn_dep)):
    """Everything the twin map needs in one call: counties (real indicators + simulated KPIs), facilities
    (capacity, simulated utilisation/wait, live operational load), flows (county -> facility)."""
    if capability not in CAPS:
        raise HTTPException(400, f"capability must be one of {CAPS}")
    snap = base_snapshot(conn)
    run_id = run_id or latest_run(conn, baseline=True)
    M = metrics_of(conn, run_id) if run_id else {}
    cur = conn.cursor(row_factory=dict_row)
    ind = {}
    for r in cur.execute("""SELECT DISTINCT ON (indicator_id, region_id) indicator_id, region_id, year, value
                            FROM indicator_value WHERE gender='total' AND (region_id LIKE 'hva-%%' OR region_id='fi')
                              AND indicator_id IN ('sotkanet:6103','sotkanet:3627','sotkanet:3336','sotkanet:6411','sotkanet:13','sotkanet:3621')
                            ORDER BY indicator_id, region_id, year DESC"""):
        ind.setdefault(r["region_id"], {})[r["indicator_id"]] = {"value": r["value"], "year": r["year"], "tag": "OBSERVED", "source": "sotkanet"}
    cents = {r["id"]: r for r in cur.execute("""SELECT id, ST_X(centroid::geometry) lon, ST_Y(centroid::geometry) lat FROM region
                                                WHERE kind='wellbeing_county'""")}
    live_fac = {r["f"]: r for r in cur.execute("""
        SELECT f, count(*) FILTER (WHERE status='active') AS active_pathways,
               sum(open_alerts) AS open_alerts, sum(critical) AS critical_alerts FROM (
          SELECT p.id, p.status,
                 (SELECT e.facility_id FROM event e WHERE e.pathway_id=p.id AND e.facility_id IS NOT NULL ORDER BY seq DESC LIMIT 1) f,
                 (SELECT count(*) FROM alert a WHERE a.pathway_id=p.id AND a.status<>'resolved') open_alerts,
                 (SELECT count(*) FROM alert a WHERE a.pathway_id=p.id AND a.status<>'resolved' AND a.severity='critical') critical
          FROM pathway p) q WHERE f IS NOT NULL GROUP BY f""")}
    counties = []
    for cid, c in snap.counties.items():
        reg = M.get("region", {}).get(cid, {})
        counties.append({"id": cid, "name": c["name"], "yta": c["yta"], "population": c["pop"],
                         "lon": cents.get(cid, {}).get("lon"), "lat": cents.get(cid, {}).get("lat"),
                         "indicators": ind.get(cid, {}), "sim": reg,
                         "model": {"m_primary_care": c["m_pc"], "m_specialist": c["m_sp"], "capacity_factor": c["cap_factor"],
                                   "tag": "DERIVED from sotkanet 6411/3336"}})
    facilities = []
    for fid, f in snap.facilities.items():
        caps = []
        for cap in CAPS:
            k = f"{fid}|{cap}"
            if k in snap.capacity:
                caps.append({"capability": cap, "capacity_per_week": snap.capacity[k], "capacity_tag": "SYNTHETIC",
                             "sim": M.get("facility", {}).get(k, {})})
        live = live_fac.get(fid, {})
        facilities.append({"id": fid, **f, "capabilities": caps,
                           "live": {"active_pathways": live.get("active_pathways", 0), "open_alerts": int(live.get("open_alerts") or 0),
                                    "critical_alerts": int(live.get("critical_alerts") or 0), "tag": "SYNTHETIC"}})
    flows = []
    for k, v in M.get("edge", {}).items():
        src, rest = k.split("->")
        fac, cap = rest.split("|")
        if cap == capability:
            flows.append({"from_county": src, "to_facility": fac, "capability": cap, "flow_per_week": v["flow_per_week"]["mean"],
                          "cross_county": snap.facilities.get(fac, {}).get("county") != src, "tag": "SIMULATED"})
    return {"capability": capability, "run_id": run_id, "national": {"indicators": ind.get("fi", {}), "sim": M.get("national", {}).get("national", {})},
            "counties": counties, "facilities": facilities, "flows": flows}


# ------------------------------------------------------------------ scenarios & runs
class ScenarioIn(BaseModel):
    name: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict, description="see backend.sim.snapshot.apply_scenario docstring")


class RunIn(BaseModel):
    reps: int = Field(4, ge=1, le=12)
    seed: int = 1000
    horizon_days: int = Field(365, ge=120, le=730)


@router.get("/scenarios")
def scenarios(conn=Depends(get_conn_dep)):
    rows = conn.cursor(row_factory=dict_row).execute("""
        SELECT s.id, s.name, s.description, s.is_baseline, s.created_at, s.params,
               (SELECT r.id FROM sim_run r WHERE r.scenario_id=s.id AND r.status='done' ORDER BY r.finished_at DESC LIMIT 1) AS latest_run
        FROM scenario s ORDER BY s.is_baseline DESC, s.created_at DESC""").fetchall()
    for r in rows:
        r["id"], r["latest_run"] = str(r["id"]), (str(r["latest_run"]) if r["latest_run"] else None)
        p = r.pop("params") or {}
        r["twin"] = p.get("twin")
        r["params_summary"] = {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in p.items() if k != "plan_summary"}
    return rows


@router.post("/scenarios", status_code=201)
def create_scenario(body: ScenarioIn, conn=Depends(get_conn_dep)):
    try:
        apply_scenario(base_snapshot(conn), body.params)     # validate
    except Exception as e:
        raise HTTPException(400, f"invalid params: {e}")
    sid = conn.execute("INSERT INTO scenario(name,description,pack_id,params) VALUES (%s,%s,'colorectal',%s::jsonb) RETURNING id",
                       (body.name, body.description, json.dumps(body.params))).fetchone()[0]
    return {"id": str(sid)}


@router.post("/scenarios/{scenario_id}/run")
def run(scenario_id: uuid.UUID, body: RunIn = RunIn(), conn=Depends(get_conn_dep)):
    out = run_scenario(conn, scenario_id, base=base_snapshot(conn), reps=body.reps, seed=body.seed, horizon=body.horizon_days)
    return out


@router.post("/baseline/run")
def run_baseline(body: RunIn = RunIn(), conn=Depends(get_conn_dep)):
    sid = ensure_baseline(conn)
    return run_scenario(conn, sid, base=base_snapshot(conn), reps=body.reps, seed=body.seed, horizon=body.horizon_days)


@router.get("/runs/{run_id}")
def run_detail(run_id: uuid.UUID, conn=Depends(get_conn_dep)):
    r = conn.cursor(row_factory=dict_row).execute("SELECT * FROM sim_run WHERE id=%s", (run_id,)).fetchone()
    if not r:
        raise HTTPException(404, "run not found")
    return {"run": {**r, "id": str(r["id"]), "scenario_id": str(r["scenario_id"])}, "metrics": metrics_of(conn, run_id)}


@router.get("/compare")
def compare(base_run: uuid.UUID, alt_run: uuid.UUID, conn=Depends(get_conn_dep)):
    """Per-scope deltas for the KPIs the twin shows. A delta is flagged 'clear' when the 95 % CIs do not overlap."""
    A, B = metrics_of(conn, base_run), metrics_of(conn, alt_run)
    kpis = ["referral_to_treatment.p_within_target", "referral_to_treatment.median_days", "referral_to_treatment.p90_days",
            "fit_positive_to_colonoscopy.p_within_target", "stall_days.mean", "mean_wait_days", "utilisation"]
    out = []
    for kind in ("national", "region", "facility"):
        for sid in set(A.get(kind, {})) | set(B.get(kind, {})):
            for k in kpis:
                a, b = A.get(kind, {}).get(sid, {}).get(k), B.get(kind, {}).get(sid, {}).get(k)
                if not a or not b:
                    continue
                clear = None
                if None not in a["ci"] and None not in b["ci"]:
                    clear = a["ci"][1] < b["ci"][0] or b["ci"][1] < a["ci"][0]
                out.append({"scope_kind": kind, "scope_id": sid, "metric": k, "base": a["mean"], "alt": b["mean"],
                            "delta": b["mean"] - a["mean"], "base_ci": a["ci"], "alt_ci": b["ci"], "ci_separated": clear})
    return {"base_run": str(base_run), "alt_run": str(alt_run), "rows": out}


# ------------------------------------------------------------------ optimisation
class OptimiseIn(BaseModel):
    capability: str = "colonoscopy"
    budget_units: int = Field(4, ge=0, le=40)
    unit_per_week: float = Field(5.0, gt=0)
    max_travel_min: float = 180
    rho: float = Field(0.85, gt=0.3, le=1.0)
    w_equity: float = 2.0
    w_move: float = 20.0
    verify: bool = Field(True, description="create a scenario from the plan and simulate it")
    monitoring: bool = Field(False, description="also switch on CancerFlow stall monitoring in the verified scenario")
    reps: int = Field(4, ge=1, le=12)


@router.post("/optimise")
def optimise_route(body: OptimiseIn, conn=Depends(get_conn_dep)):
    snap = base_snapshot(conn)
    try:
        plan = optimise(snap, body.capability, budget_units=body.budget_units, unit_per_week=body.unit_per_week,
                        rho=body.rho, max_travel_min=body.max_travel_min, w_eq=body.w_equity, w_move=body.w_move)
    except Exception as e:
        raise HTTPException(400, str(e))
    out: dict[str, Any] = {"plan": {k: v for k, v in plan.items() if k != "scenario_params"}, "tag": "SIMULATED / model output"}
    if body.verify:
        params = {**plan["scenario_params"], "monitoring": body.monitoring}
        name = f"Optimised {body.capability}: {body.budget_units} units" + (" + monitoring" if body.monitoring else "")
        sid = conn.execute("INSERT INTO scenario(name,description,pack_id,params) VALUES (%s,%s,'colorectal',%s::jsonb) RETURNING id",
                           (name, "OR-Tools plan, verified by SimPy", json.dumps(params))).fetchone()[0]
        conn.commit()
        alt = run_scenario(conn, sid, base=snap, reps=body.reps)
        conn.commit()
        base_run = latest_run(conn, baseline=True)
        if base_run is None:
            bsid = ensure_baseline(conn)
            base_run = run_scenario(conn, bsid, base=snap, reps=body.reps)["run_id"]
            conn.commit()
        out.update({"scenario_id": str(sid), "run": alt, "comparison": compare(uuid.UUID(base_run), uuid.UUID(alt["run_id"]), conn)})
    return out
