"""End-to-end pathway scheduling instance: patients with REMAINING pathway stages, providers, daily capacity,
movable resources and investment options.

Uncertainty: a stage carries p_needed = P(stage needed | previous stages happened). For patients whose diagnosis
is not yet known, downstream stages (MDT, treatment) are probabilistic demand. The optimiser holds EXPECTED
capacity for them (approximate future reservation), never a confirmed booking.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

PATIENT, SAMPLE, MEETING = "patient", "sample", "meeting"   # who/what moves for a stage


@dataclass(frozen=True)
class StageSpec:
    key: str                  # "colonoscopy" | "pathology" | "mdt" | "treatment"
    capability: str
    mode: str                 # PATIENT (travel, acceptance, attendance) | SAMPLE (lab, shipping lag) | MEETING (no travel)
    duration_days: float      # from stage start until the next stage may start (incl. turnaround)
    p_needed: float = 1.0     # conditional probability the stage is needed
    is_treatment: bool = False


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    region: str
    kind: str = "public"      # "public" | "private"


@dataclass(frozen=True)
class Patient:
    pid: str
    region: str
    home: str                 # provider for PATIENT/MEETING stages by default
    home_lab: str             # default lab for SAMPLE stages
    priority: str             # "urgent" | "standard" (clinically supplied on the referral; never inferred)
    ready_day: float          # earliest start of the next stage (0 = today)
    deadline_day: float       # treatment-start target relative to today (e.g. referral + 42 d)
    stages: tuple             # remaining StageSpec, in clinical order (precedence)
    diagnosis_known: bool
    travel_min: dict = field(default_factory=dict)      # provider -> minutes
    accept_prob: dict = field(default_factory=dict)     # provider -> P(accepts an offer there); home = 1
    consent_transfer: bool = True
    p_attend: float = 0.93
    treat_home: str = ""      # treating centre for MDT + treatment (defaults to home)

    def home_for(self, stage: "StageSpec") -> str:
        if stage.mode == SAMPLE:
            return self.home_lab
        if stage.mode == MEETING or stage.is_treatment:
            return self.treat_home or self.home
        return self.home

    def p_stage(self, j: int) -> float:
        """Unconditional probability that remaining stage j is needed."""
        p = 1.0
        for s in self.stages[: j + 1]:
            p *= s.p_needed
        return p


@dataclass(frozen=True)
class MovableResource:
    """An existing resource that can be relocated (e.g. a visiting surgeon's operating day). Moving it REMOVES the
    same capacity from the origin; the destination gains units x (1 - setup_loss)."""
    id: str
    capability: str
    origin: str
    destinations: tuple
    units_per_week: int
    setup_loss: float = 0.0
    burden: float = 2.0       # staff travel / disruption per moved week


@dataclass(frozen=True)
class InvestmentOption:
    id: str
    provider: str
    capability: str
    units_per_week: int
    eur_per_week: float       # ASSUMPTION
    max_units: int = 2


@dataclass
class Instance:
    providers: dict                     # id -> Provider
    capacity: dict                      # (provider, capability, day) -> int  (daily, existing resources only)
    patients: list
    horizon_days: int = 63
    near_term_days: int = 14            # daily buckets; weekly after
    ship_lag_days: float = 1.0          # extra days when a sample goes to a non-home lab
    movable: list = field(default_factory=list)
    investments: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def buckets(self) -> list[tuple[int, int]]:
        b = [(d, 1) for d in range(self.near_term_days)]
        d = self.near_term_days
        while d < self.horizon_days:
            b.append((d, min(7, self.horizon_days - d)))
            d += 7
        return b

    def bucket_capacity(self, provider: str, capability: str, start: int, length: int) -> int:
        return sum(self.capacity.get((provider, capability, d), 0) for d in range(start, start + length))

    def capabilities(self) -> set:
        return {(h, c) for (h, c, _), v in self.capacity.items() if v > 0}

    def digest(self) -> str:
        blob = json.dumps({"cap": sorted((list(k), v) for k, v in self.capacity.items()),
                           "patients": [asdict(p) for p in self.patients],
                           "movable": [asdict(m) for m in self.movable]}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
