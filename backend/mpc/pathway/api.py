"""/mpc/brain endpoints: the end-to-end pathway optimiser's counterfactual benchmark and recommendations.
Mounted only when CANCERFLOW_MPC=1 (with the rest of the MPC layer). Read-only; recommendations only."""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, HTTPException

from backend.mpc.pathway.benchmark import RESULTS, clean, run
from backend.services import llm

router = APIRouter(prefix="/mpc/brain", tags=["mpc brain (experimental)"])
CACHE = RESULTS / "east_finland.json"
_running = threading.Lock()


def _doc() -> dict:
    if not CACHE.exists():
        raise HTTPException(404, "no benchmark yet: POST /mpc/brain/run or `python -m backend.mpc.pathway.benchmark`")
    return clean(json.loads(CACHE.read_text(encoding="utf-8")))


@router.get("/benchmark")
def benchmark():
    """Cached counterfactual comparison (same patients, capacity and random streams for every strategy)."""
    return _doc()


@router.post("/run")
def rerun(reps: int = 30, time_limit: float = 40.0):
    """Recompute (about 2-3 min, single process). Only one run at a time."""
    if not _running.acquire(blocking=False):
        raise HTTPException(409, "a benchmark run is already in progress")
    try:
        doc = run(reps=reps, time_limit=time_limit, out=CACHE)
    finally:
        _running.release()
    return {"runtime_s": doc["runtime_s"], "recommended": doc["recommendation"]["recommended_strategy"]}


@router.get("/patient/{pid}")
def patient(pid: str):
    """For the care app: one synthetic patient's original vs recommended pathway (actions need approval)."""
    d = _doc()
    s = d.get("patient_story")
    acts = [a for a in d["recommendation"]["actions"] if a.get("patient") == pid]
    if s and s["pid"] == pid:
        return {**s, "actions": acts}
    if not acts:
        raise HTTPException(404, "no recommendation for this patient in the cached benchmark")
    return {"pid": pid, "actions": acts, "tag": "SYNTHETIC patient, recommendation only"}


EXPLAIN_SYSTEM = """You explain an optimisation recommendation to Finnish healthcare policymakers in at most 110 words.
Use ONLY numbers in the FACTS. Never invent benefits, costs or clinical reasons. Say outcomes are SIMULATED on an
illustrative synthetic instance. Structure: what to change, why it beats the alternatives (name the conventional
diagnostics-first approach and why it fails if the facts show it), what remains the bottleneck, and whether money
is needed. Plain text, no lists."""


@router.post("/explain")
def explain():
    d = _doc()
    S = d["strategies"]
    pick = lambda k: {m: S[k]["kpis"][m]["mean"] for m in ("pct_within_target", "breaches", "urgent_breaches",
                                                            "weighted_lateness_days", "mean_colonoscopy_day",
                                                            "mean_diagnosis_day", "mean_treatment_day", "transfers", "routed_samples")
                      if S[k]["kpis"].get(m)}
    facts = {"recommended": d["recommendation"]["recommended_strategy"], "action_counts": d["recommendation"]["summary_actions"],
             "strategies": {k: pick(k) for k in S},
             "rejected_because": d["recommendation"]["rejected_alternatives"],
             "remaining_bottlenecks": [f"{b['provider']} {b['capability']} {round(100 * b['planned_utilisation'])}%"
                                       for b in d["bottlenecks_after_best_plan"][:3]],
             "investment_ranked": [{"invest": i["chosen_investments"], "eur": i["spent_eur"], "breaches_avoided": i["breaches_avoided"]}
                                   for i in d["investment_pass"]]}
    text = (f"Recommended: {facts['recommended']} (simulated). Baseline FIFO {facts['strategies']['baseline_fifo'].get('pct_within_target')} % "
            f"within target vs {facts['strategies'][facts['recommended']].get('pct_within_target')} % with the recommended plan, "
            f"using existing resources only. Remaining bottlenecks: {', '.join(facts['remaining_bottlenecks'])}.")
    out = {"mode": "fallback", "text": text, "facts": facts}
    if llm.available():
        t = llm.chat([{"role": "system", "content": EXPLAIN_SYSTEM}, {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
                     max_tokens=900)
        if t and not llm.ungrounded_numbers(t, facts):
            out.update({"mode": "llm", "text": t})
        elif t:
            out["rejected"] = llm.ungrounded_numbers(t, facts)[:6]
    return out
