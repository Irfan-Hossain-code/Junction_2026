"""Receding-horizon MPC coordinator (recommendation-only, disabled by default).

    observe(state) -> step(trigger) -> recommendations -> (humans act / patients respond) -> observe(...) -> ...

* Versioned state: every observe() bumps the version; a result computed on an older version is STALE and is never
  published (concurrent solves are safe: compute outside the lock, publish inside it with a version check).
* Triggers: "scheduled" (national, all waiting patients) and "event" (local: only patients whose eligible
  facilities intersect the affected facilities; everyone else's standing recommendations keep their capacity).
* Bounded compute: MPCConfig.budget_s; on timeout/infeasibility a greedy, always-feasible plan is used.
* Hierarchy: the EXISTING strategic optimiser (backend.opt.allocate.optimise, weekly flows) can supply preferred
  facilities per patient; the operational MIP treats them only as a lowest-tier (L3) nudge.
* Feature flag: CANCERFLOW_MPC=1 (default off).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Optional

from backend.mpc.escalation import EscalationChannel, LogChannel
from backend.mpc.forecast import DownstreamForecast
from backend.mpc.problem import MPCConfig, Plan, evaluate, greedy, solve
from backend.mpc.state import MPCState


def enabled() -> bool:
    return os.environ.get("CANCERFLOW_MPC", "0").lower() in ("1", "true", "yes", "on")


@dataclass
class MPCResult:
    state_version: int
    trigger: str
    plan: Plan
    recommendations: list
    predicted: dict                  # {"mpc": metrics, "myopic_baseline": metrics, "difference": ...}
    published: bool = False
    stale: bool = False
    escalations: list = field(default_factory=list)


class MPCCoordinator:
    def __init__(self, forecast: DownstreamForecast, cfg: MPCConfig | None = None,
                 escalation: EscalationChannel | None = None):
        self.fc = forecast
        self.cfg = cfg or MPCConfig()
        self.escalation = escalation or LogChannel()
        self._lock = threading.Lock()
        self._state: Optional[MPCState] = None
        self._published: Optional[MPCResult] = None
        self._preferred: dict = {}

    # ------------------------------------------------------------------ observe
    def observe(self, state: MPCState) -> int:
        with self._lock:
            v = (self._state.version + 1) if self._state else max(1, state.version)
            self._state = replace(state, version=v)
            return v

    @property
    def version(self) -> int:
        return self._state.version if self._state else 0

    def set_strategic_plan(self, preferred: dict) -> None:
        """pid -> iterable of facilities preferred by the weekly strategic plan (existing OR-Tools flow model)."""
        self._preferred = {k: tuple(v) for k, v in preferred.items()}

    @staticmethod
    def strategic_preferences(snap, state: MPCState, capability: str = "colonoscopy", **opt_kwargs) -> dict:
        """Call the EXISTING optimiser unchanged and translate its municipality routing shares into per-patient
        preferred facilities. (Requires patients' region/home; facility shares > 10 % count as preferred.)"""
        from backend.opt.allocate import optimise
        plan = optimise(snap, capability, **{"budget_units": 0, "behavioral": True, **opt_kwargs})
        rs = plan["scenario_params"]["routing_set"]
        by_home: dict = {}
        for k, shares in rs.items():
            m = k.split("|")[0]
            home = snap.home.get(k)
            by_home.setdefault(home, set()).update(f for f, w in shares if w > 0.1)
        return {p.pid: tuple(by_home.get(p.home_facility, ())) for p in state.patients}

    # ------------------------------------------------------------------ step
    def step(self, trigger: str = "scheduled", affected_facilities: tuple = ()) -> MPCResult:
        with self._lock:
            if self._state is None:
                raise RuntimeError("observe() a state first")
            state, version = self._state, self._state.version
            standing = self._published.plan.assignment if (self._published and self._published.state_version) else {}
        fixed_use: dict = {}
        if trigger == "event" and affected_facilities:
            aff = set(affected_facilities)
            local = tuple(p for p in state.patients if aff & set((p.home_facility,) + tuple(p.eligible)))
            keep = {p.pid for p in state.patients} - {p.pid for p in local}
            for pid in keep:                                           # others keep their standing slots
                a = standing.get(pid)
                if a:
                    cap = next(p.next_capability for p in state.patients if p.pid == pid)
                    fixed_use[(a[0], cap, a[1])] = fixed_use.get((a[0], cap, a[1]), 0) + 1
            sub = replace(state, patients=local)
        else:
            sub = state
        plan, opts, defer = solve(sub, self.fc, self.cfg, self._preferred, fixed_use)
        base = greedy(sub, opts, self.cfg, local_only=True)
        m_mpc, m_base = evaluate(sub, opts, defer, plan.assignment, self.cfg), evaluate(sub, opts, defer, base, self.cfg)
        recs, esc = self._recommend(sub, plan, opts, defer)
        res = MPCResult(version, trigger, plan, recs, {
            "mpc": m_mpc, "myopic_baseline": m_base,
            "difference": {k: round(m_mpc[k] - m_base[k], 3) for k in m_mpc},
            "tag": "PREDICTED by the MPC's own forecast model (not yet verified in SimPy)"}, escalations=esc)
        return self.publish(res)

    def publish(self, res: MPCResult) -> MPCResult:
        with self._lock:
            if res.state_version != self.version:
                res.stale = True                                       # state moved on while we were solving
                return res
            if res.trigger == "event" and self._published:
                merged = dict(self._published.plan.assignment)
                merged.update(res.plan.assignment)
                res.plan = replace(res.plan, assignment=merged)
            res.published = True
            self._published = res
        for a in res.escalations:
            a["notified"] = self.escalation.notify(a)
        return res

    # ------------------------------------------------------------------ recommendations
    def _recommend(self, state: MPCState, plan: Plan, opts, defer) -> tuple[list, list]:
        idx = {(o.p.pid, o.facility, o.day): o for o in opts}
        recs, esc = [], []
        for p in state.patients:
            a = plan.assignment.get(p.pid)
            o = idx.get((p.pid, a[0], a[1])) if a else None
            if a is None:
                r = {"pid": p.pid, "action": "defer", "status": "recommended",
                     "note": "no feasible slot within horizon; re-planned next cycle"}
            elif a[0] == p.home_facility:
                r = {"pid": p.pid, "action": "book", "facility": a[0], "day": a[1], "status": "recommended"}
            else:
                r = {"pid": p.pid, "action": "offer_transfer", "facility": a[0], "day": a[1], "from": p.home_facility,
                     "status": "requires_authorisation" if p.transfer_needs_authorisation else "awaiting_patient_consent",
                     "p_accept": round(p.accept_prob.get(a[0], 0.0), 3)}
            r.update({"priority": p.priority, "expected_violation_days": round(o.ev_violation if o else defer[p.pid][0], 2),
                      "p_target_violation": round(o.p_violation if o else defer[p.pid][1], 3),
                      "expected_rtt_days": round(o.e_completion if o else defer[p.pid][2], 1)})
            # urgent: escalate if routine review would come too late or the safety rule cannot be met
            reason = None
            if p.priority == "urgent":
                if p.pid in plan.urgent_infeasible:
                    reason = "mandatory urgent time limit cannot be met with available capacity"
                elif a is not None and a[1] < self.cfg.review_delay_days:
                    reason = "slot expires before the next routine reviewer acts"
                elif r["p_target_violation"] > 0.5:
                    reason = "target violation more likely than not even under the best plan"
            if reason:
                r["status"] = "escalate_on_call"
                r["escalation_reason"] = reason
                esc.append(r)
            recs.append(r)
        return recs, esc

    def latest(self) -> Optional[MPCResult]:
        return self._published
