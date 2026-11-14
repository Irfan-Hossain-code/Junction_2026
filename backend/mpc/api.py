"""Optional /mpc endpoints. Mounted ONLY when CANCERFLOW_MPC=1, so the existing demo API is unchanged by default."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.mpc.coordinator import MPCCoordinator
from backend.mpc.forecast import DownstreamForecast
from backend.mpc.problem import MPCConfig
from backend.mpc.state import from_operational_db

router = APIRouter(prefix="/mpc", tags=["mpc (experimental)"])
_coord: Optional[MPCCoordinator] = None


def _conn():
    from backend.api.main import get_conn
    yield from get_conn()


class TriggerIn(BaseModel):
    trigger: str = "scheduled"            # "scheduled" | "event"
    affected_facilities: list[str] = []
    budget_s: float = 5.0
    use_strategic_plan: bool = False


@router.get("/status")
def status():
    return {"enabled": True, "version": _coord.version if _coord else 0,
            "latest": None if not (_coord and _coord.latest()) else {
                "state_version": _coord.latest().state_version, "trigger": _coord.latest().trigger,
                "status": _coord.latest().plan.status, "predicted": _coord.latest().predicted}}


@router.post("/step")
def step(body: TriggerIn, conn=Depends(_conn)):
    """Observe the operational DB (read-only), solve, return RECOMMENDATIONS (nothing is booked or moved)."""
    global _coord
    from backend.api.twin import base_snapshot, latest_run
    snap = base_snapshot(conn)
    run = latest_run(conn, baseline=True)
    fc = DownstreamForecast.from_sim_metrics(conn, run) if run else DownstreamForecast()
    if _coord is None:
        _coord = MPCCoordinator(fc, MPCConfig(budget_s=body.budget_s))
    _coord.fc, _coord.cfg.budget_s = fc, body.budget_s
    state = from_operational_db(conn, snap, datetime.now(timezone.utc), version=_coord.version + 1)
    if not state.patients:
        raise HTTPException(409, "no active pathways waiting for the next step")
    _coord.observe(state)
    if body.use_strategic_plan:
        _coord.set_strategic_plan(MPCCoordinator.strategic_preferences(snap, state))
    r = _coord.step(body.trigger, tuple(body.affected_facilities))
    return {"state_version": r.state_version, "published": r.published, "stale": r.stale, "status": r.plan.status,
            "tiers": r.plan.tiers, "solve_ms": round(r.plan.solve_ms), "notes": r.plan.notes, "predicted": r.predicted,
            "recommendations": r.recommendations, "escalations": r.escalations,
            "forecast_source": fc.source, "tag": "RECOMMENDATION ONLY · SYNTHETIC capacity · forecast-based prediction"}
