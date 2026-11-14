"""Run replications of the SimPy model in parallel, aggregate mean + 95 % CI, persist as SIMULATED metrics.

    python -m backend.sim.runner --reps 4            # baseline run, stored in DB
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

import numpy as np
from scipy import stats

from backend.sim import params as P
from backend.sim.model import Model, score
from backend.sim.snapshot import Snapshot, apply_scenario, build_snapshot

MODEL_VERSION = "crc-des-0.3"


def _one(args):
    snap, seed, horizon, warmup = args
    return score(Model(snap, seed=seed, horizon=horizon, warmup=warmup).run())


def _ci(vals):
    a = np.asarray(vals, dtype=float)
    m = float(a.mean())
    if len(a) < 2:
        return m, None, None
    h = float(stats.t.ppf(0.975, len(a) - 1) * a.std(ddof=1) / np.sqrt(len(a)))
    return m, m - h, m + h


def aggregate(reps: list[dict]) -> list[dict]:
    """-> rows {scope_kind, scope_id, metric, mean, ci_low, ci_high}"""
    acc = defaultdict(list)
    kinds = {"region": "region", "facility": "facility", "edge": "edge"}
    for r in reps:
        for kind, scopes in r.items():
            for sid, mets in scopes.items():
                for k, v in mets.items():
                    acc[(kinds[kind] if sid != "national" else "national", sid, k)].append(v)
    rows = []
    for (kind, sid, k), vals in acc.items():
        # missing in some reps (e.g. no stalls) -> pad zeros for count/flow metrics only
        if len(vals) < len(reps) and (k.endswith(".n") or k == "flow_per_week"):
            vals = vals + [0.0] * (len(reps) - len(vals))
        m, lo, hi = _ci(vals)
        rows.append({"scope_kind": kind, "scope_id": sid, "metric": k, "mean": m, "ci_low": lo, "ci_high": hi})
    return rows


def run_snapshot(snap: Snapshot, reps: int = 4, seed: int = 1000, horizon: int = 365, warmup: int = 90,
                 parallel: bool = True) -> tuple[list[dict], float]:
    t = time.time()
    jobs = [(snap, seed + i, horizon, warmup) for i in range(reps)]
    if parallel and reps > 1:
        with ProcessPoolExecutor(max_workers=min(reps, 6)) as ex:
            res = list(ex.map(_one, jobs))
    else:
        res = [_one(j) for j in jobs]
    return aggregate(res), time.time() - t


def assumptions(snap: Snapshot, scenario_params: dict) -> dict:
    keep = ["SCREENING_PARTICIPATION", "SCREENING_FIT_POSITIVE", "SCREENING_COLONOSCOPIES_PER_YEAR",
            "SYMPTOMATIC_TO_SCREENING_RATIO", "CRC_CASES_PER_YEAR", "P_CANCER", "TARGET_UTILISATION", "CRACK_P",
            "STALL_MEDIAN_DAYS_UNMONITORED", "ALERT_RESPONSE_DAYS", "TREATMENT_MIX", "MULT_LO", "MULT_HI", "POOL",
            "BACKGROUND_UTILISATION"]
    return {"snapshot_digest": snap.digest(), "scenario_params": scenario_params, "notes": snap.notes,
            "params": {k: getattr(P, k) for k in keep},
            "tags": {"capacity": "SYNTHETIC (demand-matched) x heuristic county factor",
                     "arrivals": "OBSERVED screening volume (FCR 2024) x ASSUMED symptomatic ratio",
                     "durations": "HYPOTHESIS (params.DURATIONS)", "outputs": "SIMULATED"}}


def persist(conn, scenario_id, snap: Snapshot, scenario_params: dict, rows: list[dict], reps, seed, horizon, warmup,
            started: datetime) -> str:
    rid = conn.execute("""INSERT INTO sim_run(scenario_id,model_version,seed,replications,horizon_days,assumptions,status,started_at,finished_at)
                          VALUES (%s,%s,%s,%s,%s,%s::jsonb,'done',%s,now()) RETURNING id""",
                       (scenario_id, MODEL_VERSION, seed, reps, horizon, json.dumps(assumptions(snap, scenario_params), default=str),
                        started)).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO sim_metric(run_id,scope_kind,scope_id,metric,mean,ci_low,ci_high)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        [(rid, r["scope_kind"], r["scope_id"], r["metric"], r["mean"], r["ci_low"], r["ci_high"]) for r in rows])
    return str(rid)


def run_scenario(conn, scenario_id, base: Snapshot | None = None, reps=4, seed=1000, horizon=365, warmup=90) -> dict:
    sc = conn.execute("SELECT params FROM scenario WHERE id=%s", (scenario_id,)).fetchone()
    if not sc:
        raise KeyError(f"scenario {scenario_id}")
    base = base or build_snapshot(conn)
    snap = apply_scenario(base, sc[0] or {})
    started = datetime.now(timezone.utc)
    rows, secs = run_snapshot(snap, reps, seed, horizon, warmup)
    rid = persist(conn, scenario_id, snap, sc[0] or {}, rows, reps, seed, horizon, warmup, started)
    return {"run_id": rid, "seconds": round(secs, 1), "metrics": len(rows)}


def ensure_baseline(conn) -> str:
    row = conn.execute("SELECT id FROM scenario WHERE is_baseline LIMIT 1").fetchone()
    if row:
        return str(row[0])
    return str(conn.execute("""INSERT INTO scenario(name,description,pack_id,params,is_baseline)
                               VALUES ('Baseline','Current network: nearest capable hospital, synthetic capacity','colorectal','{}',true)
                               RETURNING id""").fetchone()[0])


if __name__ == "__main__":
    from backend.db.conn import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=4)
    a = ap.parse_args()
    with connect() as conn:
        sid = ensure_baseline(conn)
        conn.commit()
        out = run_scenario(conn, sid, reps=a.reps)
        print(out)
