"""Care-app helpers that bridge one patient's pathway to the digital twin (read-only).

GET /care/pathways/{id}/capacity
    For the patient's next capacity-limited step (e.g. colonoscopy): how loaded their default hospital is in the
    latest baseline simulation, and nearby alternatives with shorter simulated queues, with travel time from the
    patient's home municipality. Purely advisory: nothing is booked or moved.

Every number keeps its epistemic tag: load/queues SIMULATED (on SYNTHETIC capacity), travel DERIVED (straight-line
x1.3 at 70 km/h, see travel_time.method).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from backend.api.twin import base_snapshot, get_conn_dep, latest_run, metrics_of
from backend.pathways.pack import get_pack
from backend.services import pathways as svc

router = APIRouter(prefix="/care", tags=["care"])

EXIT_STAGES = {"DECEASED", "WITHDRAWN", "DECLINED", "SCREENING_NO_RESPONSE"}
MAX_EXTRA_TRAVEL_MIN = 150   # don't suggest hospitals much farther than the default one
MIN_WAIT_SAVING_DAYS = 3     # only suggest when the simulated queue is clearly shorter
BUSY_UTILISATION = 0.85      # above this the default hospital counts as busy


def _metric(m: dict, name: str):
    v = (m or {}).get(name)
    return None if v is None else v["mean"]


@router.get("/pathways/{pathway_id}/capacity")
def pathway_capacity(pathway_id: uuid.UUID, conn=Depends(get_conn_dep)):
    try:
        st = svc.get_state(conn, pathway_id)
    except svc.NotFound as e:
        raise HTTPException(404, str(e))

    if st["status"] == "closed" or not st["current_stage"]:
        return {"capability": None, "reason": "pathway closed"}

    snap = base_snapshot(conn)
    run_id = latest_run(conn, baseline=True)
    fac_metrics = (metrics_of(conn, run_id) if run_id else {}).get("facility", {})
    simulated_caps = {k.split("|", 1)[1] for k, m in fac_metrics.items() if (_metric(m, "utilisation") or 0) > 0}

    # The nearest UPCOMING step that uses a capacity-limited service the simulation actually models: breadth-first
    # along the pack's transitions, so "referral sent" looks ahead to colonoscopy. (Entering a stage means it has
    # happened, so the current stage itself is not a candidate.)
    pack = get_pack(st["pack_id"])
    cur_id = st["current_stage"]["id"]
    target, steps_ahead = None, 0
    frontier = [n for n in (pack.stages[cur_id].next if cur_id in pack.stages else []) if n not in EXIT_STAGES]
    seen = {cur_id}
    for depth in range(1, 6):
        for sid in frontier:
            s = pack.stages.get(sid)
            if s and s.capability in simulated_caps and sid not in EXIT_STAGES:
                target, steps_ahead = {"id": sid, "label": s.label}, depth
                break
        if target:
            break
        seen.update(frontier)
        frontier = [n for sid in frontier if sid in pack.stages for n in pack.stages[sid].next
                    if n not in seen and n not in EXIT_STAGES]
    if target is None:
        return {"capability": None, "reason": "no simulated capacity-limited step ahead"}
    cap = pack.stages[target["id"]].capability

    row = conn.execute("SELECT home_region_id FROM patient WHERE id=%s", (st["patient"]["id"],)).fetchone()
    muni = row[0] if row else None

    def option(fid: str) -> dict:
        f = snap.facilities.get(fid, {})
        m = fac_metrics.get(f"{fid}|{cap}", {})
        return {"facility_id": fid, "name": f.get("name", fid), "county": f.get("county"),
                "utilisation": _metric(m, "utilisation"), "mean_wait_days": _metric(m, "mean_wait_days"),
                "capacity_per_week": snap.capacity.get(f"{fid}|{cap}"),
                "travel_min": snap.travel.get(f"{muni}|{fid}") if muni else None}

    # current hospital: an appointment already booked for this step wins, else the default (nearest) route
    booked = next((a for a in st["appointments"] if a["stage"] == target["id"] and a["status"] == "booked"), None)
    current_id = (booked or {}).get("facility_id") or (snap.home.get(f"{muni}|{cap}") if muni else None)
    if not current_id:
        return {"capability": cap, "stage": target, "reason": "patient has no home municipality on record"}
    current = option(current_id)

    alts = []
    for k in snap.capacity:
        fid, c = k.split("|", 1)
        if c != cap or fid == current_id or snap.facilities.get(fid, {}).get("kind") == "private_clinic":
            continue
        o = option(fid)
        if o["travel_min"] is None or o["mean_wait_days"] is None:
            continue
        if current["travel_min"] is not None and o["travel_min"] > current["travel_min"] + MAX_EXTRA_TRAVEL_MIN:
            continue
        alts.append(o)
    alts.sort(key=lambda o: (o["mean_wait_days"], o["travel_min"]))
    alts = alts[:3]

    suggestion = None
    cw, cu = current["mean_wait_days"], current["utilisation"]
    if alts and cw is not None:
        best = alts[0]
        busy = (cu is not None and cu >= BUSY_UTILISATION)
        if cw - best["mean_wait_days"] >= MIN_WAIT_SAVING_DAYS and busy:
            suggestion = {"facility_id": best["facility_id"],
                          "wait_saving_days": round(cw - best["mean_wait_days"], 1),
                          "extra_travel_min": round(best["travel_min"] - (current["travel_min"] or 0))}

    return {"capability": cap, "stage": target, "steps_ahead": steps_ahead, "run_id": run_id,
            "booked": bool(booked), "current": current, "alternatives": alts, "suggestion": suggestion,
            "tags": {"load": "SIMULATED", "capacity": "SYNTHETIC", "travel": "DERIVED"},
            "thresholds": {"busy_utilisation": BUSY_UTILISATION, "min_wait_saving_days": MIN_WAIT_SAVING_DAYS,
                           "max_extra_travel_min": MAX_EXTRA_TRAVEL_MIN}}
