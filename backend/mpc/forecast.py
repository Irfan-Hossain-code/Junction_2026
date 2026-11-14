"""Downstream forecasts: expected remaining time from completing the next step at facility f to treatment start.

This is what makes the controller WHOLE-PATHWAY: an early colonoscopy at a hospital whose MDT/surgery queues are
long can be worse than a later one elsewhere. Forecasts are distributions (samples), never reservations: no
downstream treatment appointment is booked by the MPC.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class DownstreamForecast:
    """remaining_days[f] = (median, sigma) of a lognormal for time from the next step at f to treatment start."""
    remaining_days: dict = field(default_factory=dict)
    default: tuple = (25.0, 0.4)
    source: str = "static"

    def samples(self, facility: str, n: int, rng: np.random.Generator) -> np.ndarray:
        med, sig = self.remaining_days.get(facility, self.default)
        return rng.lognormal(np.log(max(med, 0.1)), sig, size=n)

    @classmethod
    def from_sim_metrics(cls, conn, run_id: str, capability_chain=("ct_imaging", "mdt_crc", "crc_surgery")) -> "DownstreamForecast":
        """Use a stored SIMULATED run: national stage dwell times + each facility's downstream queue waits."""
        from backend.services.twin_context import metrics_of
        M = metrics_of(conn, run_id)
        nat = M.get("national", {}).get("national", {})
        base = sum((nat.get(f"stage_days.{s}") or {"mean": 0})["mean"]
                   for s in ("HISTOLOGY", "CANCER_CONFIRMED", "STAGING", "MDT_DECISION"))
        rem = {}
        for k, m in M.get("facility", {}).items():
            fac, cap = k.split("|")
            if cap in capability_chain:
                rem.setdefault(fac, base)
                rem[fac] += (m.get("mean_wait_days") or {"mean": 0})["mean"]
        return cls({f: (v, 0.35) for f, v in rem.items()}, default=(base or 25.0, 0.4), source=f"sim_run:{run_id}")
