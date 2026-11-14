"""Pathway packs: declarative YAML definitions of a cancer pathway (stages, transitions, clocks, rules)."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

PACK_DIR = Path(__file__).resolve().parents[2] / "packs"

Tag = Literal["OBSERVED", "REFERENCE", "EXTERNAL_PROXY", "DERIVED", "CALIBRATED", "SYNTHETIC", "SIMULATED", "HYPOTHESIS"]
Actor = Literal["doctor", "patient", "system", "generator"]


class Sourced(BaseModel):
    value: float
    tag: Tag
    source: str
    year: Optional[int] = None


class Stage(BaseModel):
    id: str = ""
    label: str
    level: str
    actors: list[Actor] = ["doctor"]
    next: list[str] = []
    terminal: bool = False
    capability: Optional[str] = None
    responsible: Optional[str] = None
    treatment_start: bool = False
    max_dwell_days: Optional[Sourced] = None


class Target(BaseModel):
    days: float
    tag: Tag
    source: str
    note: str = ""


class Clock(BaseModel):
    id: str = ""
    label: str
    start: list[str]
    stop: list[str]
    cancel_on: list[str] = []
    targets: list[Target] = []

    @property
    def primary(self) -> Optional[Target]:
        return self.targets[0] if self.targets else None


class EntryRoute(BaseModel):
    first_stage: str
    label: str


class Pack(BaseModel):
    id: str
    version: int
    title: str
    icd10: list[str] = []
    sources: dict[str, dict] = {}
    programme_stats: dict[str, Sourced] = {}
    entry_routes: dict[str, EntryRoute]
    stages: dict[str, Stage]
    universal_next: list[str] = []
    universal_stages: dict[str, Stage] = {}
    clocks: dict[str, Clock] = {}
    alerts: dict = Field(default_factory=lambda: {"warn_fraction": 0.75})
    sha256: str = ""

    @model_validator(mode="after")
    def _link_and_check(self):
        self.stages = {**self.stages, **self.universal_stages}
        for sid, s in self.stages.items():
            s.id = sid
        for cid, c in self.clocks.items():
            c.id = cid
        known = set(self.stages)
        errs = []
        for sid, s in self.stages.items():
            for n in s.next:
                if n not in known:
                    errs.append(f"stage {sid}: unknown next '{n}'")
            if not s.terminal and not s.next:
                errs.append(f"stage {sid}: non-terminal stage has no next")
        for r, e in self.entry_routes.items():
            if e.first_stage not in known:
                errs.append(f"route {r}: unknown first_stage {e.first_stage}")
        for cid, c in self.clocks.items():
            for x in c.start + c.stop + c.cancel_on:
                if x not in known:
                    errs.append(f"clock {cid}: unknown stage {x}")
        all_src = set(self.sources)
        for cid, c in self.clocks.items():
            for t in c.targets:
                if t.source not in all_src:
                    errs.append(f"clock {cid}: unknown source {t.source}")
        if errs:
            raise ValueError("invalid pack:\n  " + "\n  ".join(errs))
        return self

    def allowed_next(self, current: Optional[str], entry_route: str) -> list[str]:
        if current is None:
            return [self.entry_routes[entry_route].first_stage]
        st = self.stages[current]
        if st.terminal:
            return []
        return list(dict.fromkeys(st.next + self.universal_next))

    def treatment_stages(self) -> list[str]:
        return [s for s, v in self.stages.items() if v.treatment_start]


def load_pack_file(path: Path) -> Pack:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    p = Pack(**data)
    p.sha256 = hashlib.sha256(raw.encode()).hexdigest()
    return p


@lru_cache(maxsize=None)
def get_pack(pack_id: str) -> Pack:
    f = PACK_DIR / f"{pack_id}.yaml"
    if not f.exists():
        raise KeyError(f"unknown pack {pack_id}")
    return load_pack_file(f)


def list_packs() -> list[Pack]:
    return [get_pack(f.stem) for f in sorted(PACK_DIR.glob("*.yaml"))]


def pack_definition_json(p: Pack) -> str:
    return json.dumps(p.model_dump(mode="json"), ensure_ascii=False)
