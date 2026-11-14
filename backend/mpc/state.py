"""MPC state: an immutable, versioned snapshot of patients waiting for their next pathway step and of slot capacity.

Clinical priority and targets are INPUTS supplied by clinicians / the pathway pack. Nothing here derives
severity from demographics.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass(frozen=True)
class PatientState:
    pid: str
    next_capability: str                  # the next pathway step's resource (pathway dependency already resolved)
    priority: str                         # "urgent" | "standard"  (clinically supplied on the referral)
    elapsed_days: float                   # since the target clock started (e.g. referral received)
    target_days: float                    # applicable treatment target (pathway pack, e.g. 42 d REFERENCE)
    home_facility: str                    # where the patient is booked by default
    eligible: tuple = ()                  # facilities with the capability AND clinically appropriate
    travel_min: dict = field(default_factory=dict)        # facility -> minutes from home
    accept_prob: dict = field(default_factory=dict)       # facility -> P(patient accepts an offer there); home = 1
    consent_to_transfer: bool = True      # patient has agreed to be offered other providers
    transfer_needs_authorisation: bool = True             # moving a patient requires a human sign-off
    region: Optional[str] = None


@dataclass(frozen=True)
class SlotState:
    facility: str
    capability: str
    day: int                              # 0 = today (relative to the snapshot)
    free: int                             # unbooked appointment slots that day


@dataclass(frozen=True)
class MPCState:
    as_of: str
    version: int
    patients: tuple                       # tuple[PatientState]
    slots: tuple                          # tuple[SlotState]
    horizon_days: int = 28
    source: str = "synthetic"             # "synthetic" | "operational-db" | "simulator"

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()[:16]

    def capacity(self) -> dict:
        return {(s.facility, s.capability, s.day): s.free for s in self.slots if s.free > 0}


# ---------------------------------------------------------------- builder from the operational database (read-only)
WAITING_FOR_COLONOSCOPY = ("REFERRAL_RECEIVED", "SPECIALIST_ASSESSMENT", "NURSE_ASSESSMENT")


def from_operational_db(conn, snap, as_of, version: int, horizon_days: int = 28, capability: str = "colonoscopy",
                        max_travel_min: float = 180.0) -> MPCState:
    """Read-only: active pathways waiting for a colonoscopy + slot capacity derived from the (SYNTHETIC) weekly capacity.

    Priority is read from the referral event payload ({"priority": "urgent"}) when clinically recorded; otherwise
    'standard'. It is never inferred."""
    from backend.opt.allocate import expected_behaviour
    from backend.pathways.pack import get_pack
    pack = get_pack("colorectal")
    target = pack.clocks["referral_to_treatment"].primary.days
    rows = conn.execute("""
        SELECT p.id, pt.home_region_id, c.started_at,
               (SELECT e.payload->>'priority' FROM event e WHERE e.pathway_id=p.id AND e.payload ? 'priority'
                ORDER BY e.seq DESC LIMIT 1)
        FROM pathway p JOIN patient pt ON pt.id=p.patient_id
        LEFT JOIN pathway_clock c ON c.pathway_id=p.id AND c.clock_id='referral_to_treatment'
        WHERE p.status='active' AND p.current_stage = ANY(%s)""", (list(WAITING_FOR_COLONOSCOPY),)).fetchall()
    patients = []
    for pid, muni, started, prio in rows:
        home = snap.home.get(f"{muni}|{capability}")
        if not home or muni not in snap.munis:
            continue
        elig = tuple(f for f in snap.facilities if f"{f}|{capability}" in snap.capacity and snap.facilities[f]["kind"] != "private_clinic"
                     and (f == home or snap.travel.get(f"{muni}|{f}", 1e9) <= max_travel_min))
        tm = {f: snap.travel.get(f"{muni}|{f}", 999.0) for f in elig}
        acc = {f: (1.0 if f == home else expected_behaviour(snap, muni, f, home)[0]) for f in elig}
        el = 0.0 if started is None else max(0.0, (as_of - started).total_seconds() / 86400)
        patients.append(PatientState(str(pid), capability, prio if prio in ("urgent", "standard") else "standard", el, target,
                                     home, elig, tm, acc, region=snap.munis[muni]["county"]))
    slots = []
    for k, per_week in snap.capacity.items():
        f, cap = k.split("|")
        if cap != capability:
            continue
        for d in range(horizon_days):
            if (d % 7) < 5:                              # weekday clinics; SYNTHETIC capacity spread evenly
                slots.append(SlotState(f, cap, d, int(round(per_week / 5))))
    return MPCState(as_of=str(as_of), version=version, patients=tuple(patients), slots=tuple(slots),
                    horizon_days=horizon_days, source="operational-db")
