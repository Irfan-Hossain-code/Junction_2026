"""SimPy discrete-event model of the colorectal pathway on a Snapshot.

Patients arrive (Poisson, by municipality ~ population) on two routes, take non-resource delays (lognormal,
county-scaled), occasionally STALL (a 'crack'), and queue for capacity-limited hospital resources
(colonoscopy, pathology-free, CT staging, MDT, treatment). Stage histories are scored with the SAME
pathway engine clocks the operational system uses (backend.pathways.engine.evaluate_clocks).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import simpy

from backend.pathways.engine import StageEntry, evaluate_clocks
from backend.pathways.pack import get_pack
from backend.sim import params as P
from backend.sim.snapshot import Snapshot

PACK = get_pack("colorectal")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
ADMIN_FRACTION = 0.4   # share of a stage's median that is admin/transit; the rest emerges from queueing
RTT_BINS = [(0, 14), (14, 28), (28, 42), (42, 56), (56, 70), (70, float("inf"))]


@dataclass
class PatientRec:
    route: str
    muni: str
    county: str
    hist: list = field(default_factory=list)       # (stage, day)
    facilities: dict = field(default_factory=dict) # capability -> facility
    stalls: list = field(default_factory=list)     # (stage, days)


class Station:
    """One (facility, capability). per_week = CRC share of capacity; pooled with other cancers via background load."""

    def __init__(self, env, fac, cap, per_week):
        self.fac, self.cap = fac, cap
        self.per_week = per_week                       # CRC share (what scenarios change)
        self.pool = P.POOL.get(cap, 1)
        pooled = per_week * self.pool
        self.n = max(1, int(round(pooled / P.SLOT_SIZE)))
        self.svc = self.n * 7.0 / pooled if pooled > 0 else 1e9
        self.bg_rate_per_day = (self.pool - 1) * per_week * P.BACKGROUND_UTILISATION / 7.0
        self.res = simpy.Resource(env, capacity=self.n)
        self.busy = 0.0                                # CRC busy time only
        self.waits: list[tuple[float, float]] = []     # (t_start, wait) for CRC patients
        self.served = 0


class Model:
    def __init__(self, snap: Snapshot, seed: int, horizon: float = 365, warmup: float = 90):
        self.s, self.h, self.w = snap, horizon, warmup
        self.rng = np.random.default_rng(seed)
        self.env = simpy.Environment()
        self.st = {k: Station(self.env, *k.split("|"), v) for k, v in snap.capacity.items() if v > 0}
        mids = list(snap.munis)
        pops = np.array([snap.munis[m]["pop"] for m in mids], dtype=float)
        self.mids, self.mw = mids, pops / pops.sum()
        self.patients: list[PatientRec] = []
        self.dwell = {k: v.max_dwell_days.value for k, v in PACK.stages.items() if v.max_dwell_days}

    # ------------------------------------------------------------------ helpers
    def ln(self, median):
        return float(self.rng.lognormal(np.log(max(median, 0.02)), P.SIGMA))

    def mult(self, county, scale):
        c = self.s.counties.get(county, {})
        return c.get("m_pc", 1.0) if scale == "pc" else c.get("m_sp", 1.0) if scale == "sp" else 1.0

    def delay(self, p: PatientRec, stage, admin_only=False):
        med, scale = P.DURATIONS.get(stage, (3, None))
        d = self.ln(med * self.mult(p.county, scale) * (ADMIN_FRACTION if admin_only else 1.0))
        if stage in P.CRACK_P and self.rng.random() < P.CRACK_P[stage]:
            if self.s.monitoring and stage in self.dwell:
                stall = max(0.0, self.dwell[stage] - d) + P.ALERT_RESPONSE_DAYS
            else:
                stall = self.ln(P.STALL_MEDIAN_DAYS_UNMONITORED)
            p.stalls.append((stage, stall))
            d += stall
        return self.env.timeout(d)

    def enter(self, p, stage):
        p.hist.append((stage, self.env.now))

    def station(self, p, cap):
        opts = self.s.routing.get(f"{p.muni}|{cap}")
        if not opts:
            return None
        if len(opts) == 1:
            fac = opts[0][0]
        else:
            w = np.array([o[1] for o in opts], dtype=float)
            fac = opts[self.rng.choice(len(opts), p=w / w.sum())][0]
        p.facilities[cap] = fac
        return self.st.get(f"{fac}|{cap}")

    def use(self, p, cap, stage):
        st = self.station(p, cap)
        if st is None:                       # no capacity anywhere -> treated as long external referral
            yield self.env.timeout(self.ln(30))
            self.enter(p, stage)
            return
        t_req = self.env.now
        with st.res.request() as req:
            yield req
            if t_req >= self.w:
                st.waits.append((t_req, self.env.now - t_req))
            self.enter(p, stage)              # stage = moment the service actually happens
            svc = st.svc
            if self.env.now >= self.w:
                st.busy += min(svc, self.h - self.env.now)
                st.served += 1
            yield self.env.timeout(svc)

    # ------------------------------------------------------------------ patient process
    def patient(self, p: PatientRec):
        if p.route == "screening":
            self.enter(p, "FIT_POSITIVE")
            yield self.delay(p, "FIT_POSITIVE")
            self.enter(p, "NURSE_ASSESSMENT")
            yield self.delay(p, "NURSE_ASSESSMENT", admin_only=True)
        else:
            self.enter(p, "REFERRAL_SENT")
            yield self.delay(p, "REFERRAL_SENT")
            self.enter(p, "REFERRAL_RECEIVED")
            yield self.delay(p, "REFERRAL_RECEIVED", admin_only=True)
        yield from self.use(p, "colonoscopy", "COLONOSCOPY")
        if self.rng.random() >= P.P_CANCER[p.route]:
            self.enter(p, "CANCER_EXCLUDED")
            return
        self.enter(p, "HISTOLOGY")
        yield self.delay(p, "HISTOLOGY")
        self.enter(p, "CANCER_CONFIRMED")
        yield self.delay(p, "CANCER_CONFIRMED")
        yield from self.use(p, "ct_imaging", "STAGING")
        yield self.delay(p, "STAGING", admin_only=True)
        yield from self.use(p, "mdt_crc", "MDT_DECISION")
        yield self.delay(p, "MDT_DECISION", admin_only=True)
        r = self.rng.random()
        acc = 0.0
        for stage, cap, prob in P.TREATMENT_MIX:
            acc += prob
            if r < acc:
                break
        if cap is None:
            self.enter(p, stage)
        else:
            yield from self.use(p, cap, stage)

    def arrivals(self, route):
        lam = self.s.arrivals_per_week[route] / 7.0
        sf = self.s.screening_factor
        while True:
            # thinning handles per-county screening-participation multipliers
            lam_max = lam * (max(sf.values()) if (route == "screening" and sf) else 1.0)
            yield self.env.timeout(self.rng.exponential(1.0 / lam_max))
            m = self.mids[self.rng.choice(len(self.mids), p=self.mw)]
            county = self.s.munis[m]["county"]
            if route == "screening" and sf:
                if self.rng.random() > sf.get(county, 1.0) / max(sf.values()):
                    continue
            p = PatientRec(route, m, county)
            self.patients.append(p)
            self.env.process(self.patient(p))

    def background(self, st: Station):
        """Other cancer types sharing the resource (not tracked as patients)."""
        def job():
            with st.res.request() as req:
                yield req
                yield self.env.timeout(st.svc)
        while True:
            yield self.env.timeout(self.rng.exponential(1.0 / st.bg_rate_per_day))
            self.env.process(job())

    def run(self):
        for r in ("screening", "symptomatic"):
            self.env.process(self.arrivals(r))
        for st in self.st.values():
            if st.bg_rate_per_day > 0:
                self.env.process(self.background(st))
        self.env.run(until=self.h)
        return self


# ---------------------------------------------------------------------- scoring
def score(model: Model, tail_days: float = 75) -> dict:
    """Per-replication metrics. Cohort = clocks started in [warmup, horizon - tail_days]."""
    as_of = T0 + timedelta(days=model.h)
    lo, hi = model.w, model.h - tail_days
    agg = defaultdict(lambda: defaultdict(list))   # scope -> metric -> values
    for p in model.patients:
        hist = [StageEntry(s, T0 + timedelta(days=t)) for s, t in p.hist]
        for c in evaluate_clocks(PACK, hist, as_of):
            if c.started_at is None or c.id not in ("referral_to_treatment", "fit_positive_to_colonoscopy"):
                continue
            t0 = (c.started_at - T0).total_seconds() / 86400
            if not (lo <= t0 <= hi):
                continue
            target = c.targets[0]["days"]
            if c.status == "met":
                ok = 1.0
            elif c.status == "missed" or (c.status == "running" and c.elapsed_days > target):
                ok = 0.0
            else:
                continue                                   # censored or cancelled
            for scope in ("national", p.county):
                agg[scope][f"{c.id}.within_target"].append(ok)
                if c.status in ("met", "missed"):
                    agg[scope][f"{c.id}.days"].append(c.elapsed_days)
                    if c.id == "referral_to_treatment":
                        b = next(lo_ for lo_, hi_ in RTT_BINS if c.elapsed_days < hi_)
                        agg[scope]["rtt_bins"].append(b)
        # time spent in each stage (until the next recorded stage), for cohort patients
        if p.hist and lo <= p.hist[0][1] <= hi:
            for (s1, t1), (_, t2) in zip(p.hist, p.hist[1:]):
                for scope in ("national", p.county):
                    agg[scope][f"stage:{s1}"].append(t2 - t1)
        if p.stalls and lo <= p.hist[0][1] <= hi:
            for scope in ("national", p.county):
                agg[scope]["stall_days"].append(sum(d for _, d in p.stalls))
    out = {"region": {}, "facility": {}, "edge": {}}
    for scope, mets in agg.items():
        row = {}
        for k, v in mets.items():
            if k == "rtt_bins":
                for lo_, _ in RTT_BINS:
                    row[f"rtt_hist.{lo_}"] = float(np.mean([x == lo_ for x in v]))
            elif k.startswith("stage:"):
                row[f"stage_days.{k[6:]}"] = float(np.mean(v))
            elif k.endswith(".within_target"):
                row[k.replace(".within_target", ".p_within_target")] = float(np.mean(v))
                row[k.replace(".within_target", ".n")] = float(len(v))
            elif k.endswith(".days"):
                row[k.replace(".days", ".median_days")] = float(np.median(v))
                row[k.replace(".days", ".p90_days")] = float(np.percentile(v, 90))
            else:
                row["stall_days.mean"] = float(np.mean(v))
                row["stalls.n"] = float(len(v))
        out["region"][scope] = row
    window = model.h - model.w
    for k, st in model.st.items():
        ws = [w for t, w in st.waits]
        # utilisation of the CRC share of capacity (CRC busy time / CRC share of server-time)
        crc_server_time = st.n * window / st.pool
        out["facility"][k] = {"utilisation": st.busy / crc_server_time, "mean_wait_days": float(np.mean(ws)) if ws else 0.0,
                              "p90_wait_days": float(np.percentile(ws, 90)) if ws else 0.0,
                              "throughput_per_week": st.served / (window / 7), "queue_end": float(len(st.res.queue)),
                              "capacity_per_week": st.per_week}
    flows = defaultdict(float)
    for p in model.patients:
        if p.hist and p.hist[0][1] >= model.w:
            for cap, fac in p.facilities.items():
                flows[f"{p.county}->{fac}|{cap}"] += 1
    out["edge"] = {k: {"flow_per_week": v / (window / 7)} for k, v in flows.items()}
    return out
