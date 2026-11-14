"""Pure pathway engine: (pack, entry_route, events, as_of) -> state, clocks, alerts.

No DB access. The API, the alert scanner and the SimPy simulator all call this, so rules exist in ONE place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from backend.pathways.pack import Pack


class TransitionError(ValueError):
    pass


@dataclass
class StageEntry:
    stage: str
    at: datetime
    facility_id: Optional[str] = None


def stage_history(events: list[dict]) -> list[StageEntry]:
    out = []
    for e in sorted(events, key=lambda e: e["seq"]):
        if e["type"] == "STAGE_ENTERED":
            out.append(StageEntry(e["stage"], e["occurred_at"], e.get("facility_id")))
    return out


def check_transition(pack: Pack, entry_route: str, current: Optional[str], new_stage: str,
                     actor_role: str, last_at: Optional[datetime], occurred_at: datetime) -> None:
    if new_stage not in pack.stages:
        raise TransitionError(f"unknown stage '{new_stage}' for pack {pack.id}")
    if entry_route not in pack.entry_routes:
        raise TransitionError(f"unknown entry route '{entry_route}'")
    allowed = pack.allowed_next(current, entry_route)
    if current is not None and pack.stages[current].terminal:
        raise TransitionError(f"pathway is closed (terminal stage {current})")
    if new_stage not in allowed:
        raise TransitionError(f"transition {current} -> {new_stage} not allowed; allowed: {allowed}")
    if actor_role in ("doctor", "patient") and actor_role not in pack.stages[new_stage].actors:
        raise TransitionError(f"role '{actor_role}' may not record stage {new_stage}")
    if last_at is not None and occurred_at < last_at:
        raise TransitionError(f"occurred_at {occurred_at.isoformat()} is before previous stage ({last_at.isoformat()})")


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 86400.0


@dataclass
class ClockState:
    id: str
    label: str
    status: str                      # not_started | running | met | missed | cancelled
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    elapsed_days: Optional[float] = None
    targets: list[dict] = field(default_factory=list)
    severity: Optional[str] = None   # None | warning | critical (only while running)
    due_at: Optional[datetime] = None


def evaluate_clocks(pack: Pack, hist: list[StageEntry], as_of: datetime) -> list[ClockState]:
    warn = float(pack.alerts.get("warn_fraction", 0.75))
    out = []
    for c in pack.clocks.values():
        targets = [t.model_dump() for t in c.targets]
        start = next((h for h in hist if h.stage in c.start), None)
        if start is None:
            out.append(ClockState(c.id, c.label, "not_started", targets=targets))
            continue
        after = [h for h in hist if h.at >= start.at and h is not start]
        stop = next((h for h in after if h.stage in c.stop), None)
        cancel = next((h for h in after if h.stage in c.cancel_on), None)
        p = c.primary
        due = start.at + timedelta(days=p.days) if p else None
        if cancel and (stop is None or cancel.at < stop.at):
            out.append(ClockState(c.id, c.label, "cancelled", start.at, cancel.at, _days(start.at, cancel.at), targets))
            continue
        if stop:
            el = _days(start.at, stop.at)
            st = "met" if (p is None or el <= p.days) else "missed"
            out.append(ClockState(c.id, c.label, st, start.at, stop.at, el, targets, due_at=due))
            continue
        el = _days(start.at, as_of)
        sev = None
        if p:
            if el > p.days:
                sev = "critical"
            elif el >= warn * p.days:
                sev = "warning"
        out.append(ClockState(c.id, c.label, "running", start.at, None, el, targets, sev, due))
    return out


@dataclass
class AlertSpec:
    rule_id: str
    severity: str
    message: str
    responsible_role: Optional[str]
    responsible_facility_id: Optional[str]
    due_at: Optional[datetime]
    evidence: dict[str, Any]


def evaluate_alerts(pack: Pack, hist: list[StageEntry], clocks: list[ClockState], as_of: datetime) -> list[AlertSpec]:
    alerts: list[AlertSpec] = []
    cur = hist[-1] if hist else None
    resp_role = pack.stages[cur.stage].responsible if cur else None
    resp_fac = next((h.facility_id for h in reversed(hist) if h.facility_id), None)
    for c in clocks:
        if c.status != "running" or not c.severity:
            continue
        p = c.targets[0]
        verb = "exceeded" if c.severity == "critical" else "approaching"
        alerts.append(AlertSpec(
            rule_id=f"{pack.id}.clock.{c.id}", severity=c.severity,
            message=f"{c.label}: {c.elapsed_days:.0f} d elapsed, target {p['days']:.0f} d {verb} ({p['tag']}, {p['source']})",
            responsible_role=resp_role, responsible_facility_id=resp_fac, due_at=c.due_at,
            evidence={"clock": c.id, "elapsed_days": round(c.elapsed_days, 2), "target": p,
                      "started_at": c.started_at.isoformat()}))
    if cur:
        st = pack.stages[cur.stage]
        if st.max_dwell_days and not st.terminal:
            lim = st.max_dwell_days.value
            dwell = _days(cur.at, as_of)
            if dwell > lim:
                alerts.append(AlertSpec(
                    rule_id=f"{pack.id}.dwell.{cur.stage}",
                    severity="critical" if dwell > 2 * lim else "warning",
                    message=f"No progress for {dwell:.0f} d at '{st.label}' (threshold {lim:.0f} d, "
                            f"{st.max_dwell_days.tag}). Verify the next step has been arranged.",
                    responsible_role=st.responsible, responsible_facility_id=resp_fac,
                    due_at=cur.at + timedelta(days=lim),
                    evidence={"stage": cur.stage, "dwell_days": round(dwell, 2),
                              "threshold": st.max_dwell_days.model_dump()}))
    return alerts


@dataclass
class PathwayState:
    current_stage: Optional[str]
    closed: bool
    allowed_next: list[str]
    history: list[StageEntry]
    clocks: list[ClockState]
    alerts: list[AlertSpec]


def evaluate(pack: Pack, entry_route: str, events: list[dict], as_of: datetime) -> PathwayState:
    hist = stage_history(events)
    cur = hist[-1].stage if hist else None
    closed = bool(cur and pack.stages[cur].terminal)
    clocks = evaluate_clocks(pack, hist, as_of)
    alerts = [] if closed else evaluate_alerts(pack, hist, clocks, as_of)
    return PathwayState(cur, closed, pack.allowed_next(cur, entry_route), hist, clocks, alerts)
