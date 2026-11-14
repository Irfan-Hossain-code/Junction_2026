"""Hybrid agent-based + discrete-event (SimPy) model of the colorectal pathway on a Snapshot.

Patients are persistent behavioural AGENTS (age, symptom category -> urgency, archetype with continuous
travel tolerance / attendance / acceptance / reminder response). They arrive (Poisson, municipality ~ population),
see their GP (symptomatic) or the screening nurse (FIT+), receive colonoscopy OFFERS they may decline, may
NOT ATTEND (slot wasted), may be LOST to follow-up, and queue (urgent first) for capacity-limited stations.
Stage histories are scored with the SAME pathway-engine clocks the operational system uses.

Common random numbers: arrivals come from one stream and every patient's attributes + decisions from their own
stream keyed by (seed, patient id), so the same patient can be compared across scenarios.
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
ARCH_NAMES = list(P.ARCHETYPES)
_ARCH_CUM = np.cumsum([P.ARCHETYPES[a]["share"] for a in ARCH_NAMES]) / sum(P.ARCHETYPES[a]["share"] for a in ARCH_NAMES)
_SYM_NAMES = list(P.SYMPTOMS)
_SYM_CUM = np.cumsum([P.SYMPTOMS[s][0] for s in _SYM_NAMES]) / sum(P.SYMPTOMS[s][0] for s in _SYM_NAMES)


def _draw(cum, u: float) -> int:
    """Categorical draw from a precomputed cumulative table (rng.choice(p=...) rebuilds it every call: slow)."""
    return min(int(np.searchsorted(cum, u, side="right")), len(cum) - 1)


def _sig(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def _beta(rng, mean, k=P.BETA_CONCENTRATION):
    mean = min(max(mean, 0.01), 0.99)
    return float(rng.beta(mean * k, (1 - mean) * k))


def sample_agent(rng, route: str) -> dict:
    """Fictional patient. Archetype = starting point; every parameter varies continuously (ASSUMPTIONS)."""
    arch = ARCH_NAMES[_draw(_ARCH_CUM, rng.random())]
    A = P.ARCHETYPES[arch]
    if route == "screening":
        age = int(rng.integers(P.SCREENING_AGE[0], P.SCREENING_AGE[1] + 1))
        symptom, urgent = "screening_fit_positive", False
    else:
        age = int(np.clip(rng.normal(P.SYMPTOMATIC_AGE_MEAN, P.SYMPTOMATIC_AGE_SD), 35, 95))
        symptom = _SYM_NAMES[_draw(_SYM_CUM, rng.random())]
        urgent = bool(rng.random() < P.SYMPTOMS[symptom][1])      # urgency from SYMPTOM category, not demographics
    return {"archetype": arch, "age": age, "symptom": symptom, "urgent": urgent,
            "max_travel_min": float(A["max_travel_min"] * rng.lognormal(0, P.TRAVEL_SIGMA)),
            "travel_aversion": float(A["travel_aversion"] * rng.lognormal(0, 0.25)),
            "wait_aversion": float(A["wait_aversion"] * rng.lognormal(0, 0.25)),
            "p_attend": _beta(rng, A["p_attend"]), "p_accept_alt": _beta(rng, A["p_accept_alt"]),
            "reminder_gain": _beta(rng, A["reminder_gain"]),
            "care_seeking_days": float(A["care_seeking_days"] * rng.lognormal(0, 0.4)) if route == "symptomatic" else 0.0}


@dataclass
class PatientRec:
    pid: int
    route: str
    muni: str
    county: str
    rng: np.random.Generator
    attrs: dict
    hist: list = field(default_factory=list)       # (stage, day)
    facilities: dict = field(default_factory=dict) # capability -> facility actually used
    stalls: list = field(default_factory=list)     # (stage, days)
    events: list = field(default_factory=list)     # journey log: offers, declines, attendance, visits
    dna: int = 0
    lost: bool = False
    gp: str | None = None
    sample: bool = False                            # full journey log only for sampled patients (speed)
    n_offers: int = 0
    n_declined: int = 0
    n_appts: int = 0
    n_dna: int = 0


class Station:
    """One (facility, capability). per_week = CRC share of capacity; pooled with other cancers via background load."""

    def __init__(self, env, fac, cap, per_week):
        self.fac, self.cap = fac, cap
        self.per_week = per_week
        self.pool = P.POOL.get(cap, 1)
        pooled = per_week * self.pool
        self.n = max(1, int(round(pooled / P.SLOT_SIZE)))
        self.svc = self.n * 7.0 / pooled if pooled > 0 else 1e9
        self.bg_rate_per_day = (self.pool - 1) * per_week * P.BACKGROUND_UTILISATION / 7.0
        self.res = simpy.PriorityResource(env, capacity=self.n)
        self.busy = 0.0           # CRC busy time (incl. wasted no-show slots)
        self.wasted = 0.0         # slot time lost to no-shows
        self.waits: list[tuple[float, float]] = []
        self.served = 0
        self.offers = 0           # non-home offers made for this station
        self.declined = 0
        self.appointments = 0
        self.dnas = 0
        self.qsamples: list[tuple[float, int]] = []

    def est_wait(self) -> float:
        return (len(self.res.queue) + max(0, self.res.count - self.n + 1)) * self.svc / self.n


class Model:
    def __init__(self, snap: Snapshot, seed: int, horizon: float = 365, warmup: float = 90, collect_patients: bool = False):
        self.s, self.h, self.w, self.seed = snap, horizon, warmup, seed
        self.rng_arr = np.random.default_rng([seed, 1])
        self.rng_bg = np.random.default_rng([seed, 2])
        self.env = simpy.Environment()
        self.st = {k: Station(self.env, *k.split("|"), v) for k, v in snap.capacity.items() if v > 0}
        mids = list(snap.munis)
        pops = np.array([snap.munis[m]["pop"] for m in mids], dtype=float)
        self.mids, self.mw = mids, pops / pops.sum()
        self.mcum = np.cumsum(self.mw)
        self.patients: list[PatientRec] = []
        self.dwell = {k: v.max_dwell_days.value for k, v in PACK.stages.items() if v.max_dwell_days}
        self.collect = collect_patients
        self.next_pid = 0

    # ------------------------------------------------------------------ helpers
    def ln(self, p: PatientRec, median):
        return float(p.rng.lognormal(np.log(max(median, 0.02)), P.SIGMA))

    def mult(self, county, scale):
        c = self.s.counties.get(county, {})
        return c.get("m_pc", 1.0) if scale == "pc" else c.get("m_sp", 1.0) if scale == "sp" else 1.0

    def log(self, p, kind, **kw):
        if p.sample:
            p.events.append({"t": round(self.env.now, 2), "kind": kind, **kw})

    def delay(self, p: PatientRec, stage, admin_only=False):
        med, scale = P.DURATIONS.get(stage, (3, None))
        d = self.ln(p, med * self.mult(p.county, scale) * (ADMIN_FRACTION if admin_only else 1.0))
        if stage in P.CRACK_P and p.rng.random() < P.CRACK_P[stage]:
            if self.s.monitoring and stage in self.dwell:
                stall = max(0.0, self.dwell[stage] - d) + P.ALERT_RESPONSE_DAYS
            else:
                stall = self.ln(p, P.STALL_MEDIAN_DAYS_UNMONITORED)
            p.stalls.append((stage, stall))
            self.log(p, "stall", stage=stage, days=round(stall, 1), detected_by_monitoring=bool(self.s.monitoring and stage in self.dwell))
            d += stall
        return self.env.timeout(d)

    def enter(self, p, stage, facility=None):
        p.hist.append((stage, self.env.now))
        self.log(p, "stage", stage=stage, facility=facility)

    def pick(self, p, cap):
        opts = self.s.routing.get(f"{p.muni}|{cap}")
        if not opts:
            return None
        if len(opts) == 1:
            return opts[0][0]
        w = np.cumsum([o[1] for o in opts])
        return opts[_draw(w / w[-1], p.rng.random())][0]

    def travel(self, p, fac):
        return self.s.travel.get(f"{p.muni}|{fac}", 60.0)

    def p_accept_alternative(self, p, fac, home) -> float:
        """Behavioural acceptance of an offer at a NON-home provider (ASSUMPTION: logistic utility)."""
        a = p.attrs
        relief = P.TRANSPORT_SUPPORT_RELIEF if self.s.transport.get(p.county) else 0.0
        t_f, t_h = self.travel(p, fac), self.travel(p, home)
        st_f, st_h = self.st.get(f"{fac}|colonoscopy"), self.st.get(f"{home}|colonoscopy")
        w_f = st_f.est_wait() if st_f else 0.0
        w_h = st_h.est_wait() if st_h else 0.0
        urg = 1.6 if a["urgent"] else 1.0
        u = (a["wait_aversion"] * urg * (w_h - w_f) / 7.0
             - a["travel_aversion"] * (1 - relief) * max(0.0, t_f - t_h) / 60.0
             + (0.5 if t_f <= a["max_travel_min"] * (1 + relief) else -2.0))
        # reluctance towards an unfamiliar provider fades when it is not farther away than home (ASSUMPTION: 30-min scale)
        base = a["p_accept_alt"] + (1 - a["p_accept_alt"]) * np.exp(-max(0.0, t_f - t_h) / 30.0)
        return float(base * _sig(1.0 + u)), t_f, w_f, t_h, w_h

    def p_attend(self, p, fac) -> float:
        a = p.attrs
        relief = P.TRANSPORT_SUPPORT_RELIEF if self.s.transport.get(p.county) else 0.0
        pa = a["p_attend"]
        if self.s.reminders.get(p.county):
            pa = pa + (1 - pa) * a["reminder_gain"]
        pa -= P.TRAVEL_DNA_PENALTY_PER_HOUR * (1 - relief) * max(0.0, self.travel(p, fac) / 60.0 - 1.0)
        return float(min(max(pa, 0.05), 0.995))

    def serve(self, p, st: Station, stage, behavioural: bool):
        """Queue at a station (urgent first). Returns True if the patient was actually seen."""
        prio = P.URGENT_PRIORITY if p.attrs["urgent"] else P.STANDARD_PRIORITY
        t_req = self.env.now
        with st.res.request(priority=prio) as req:
            yield req
            in_window = self.env.now >= self.w
            if t_req >= self.w:
                st.waits.append((t_req, self.env.now - t_req))
            attended = True
            if behavioural:
                pa = self.p_attend(p, st.fac)
                attended = bool(p.rng.random() < pa)
                p.n_appts += 1
                p.n_dna += 0 if attended else 1
                if in_window:
                    st.appointments += 1
                    st.dnas += 0 if attended else 1
                self.log(p, "attendance", facility=st.fac, p_attend=round(pa, 3), attended=attended,
                         reminder=bool(self.s.reminders.get(p.county)))
            if attended:
                self.enter(p, stage, st.fac)
                if in_window:
                    st.served += 1
            if in_window:
                busy = min(st.svc, self.h - self.env.now)
                st.busy += busy
                if not attended:
                    st.wasted += busy
            yield self.env.timeout(st.svc)      # a no-show still consumes the slot
        return attended

    def use(self, p, cap, stage):
        fac = self.pick(p, cap)
        st = self.st.get(f"{fac}|{cap}") if fac else None
        if st is None:
            yield self.env.timeout(self.ln(p, 30))
            self.enter(p, stage)
            return True
        p.facilities[cap] = fac
        return (yield from self.serve(p, st, stage, behavioural=False))

    def book_colonoscopy(self, p):
        """Behavioural booking: offer -> accept/decline -> attend/no-show -> rebook / lost."""
        cap = "colonoscopy"
        home = self.s.home.get(f"{p.muni}|{cap}") or self.pick(p, cap)
        fac = self.pick(p, cap)
        if fac and home and fac != home and f"{home}|{cap}" in self.st:
            pacc, t_f, w_f, t_h, w_h = self.p_accept_alternative(p, fac, home)
            ok = bool(p.rng.random() < pacc)
            p.n_offers += 1
            p.n_declined += 0 if ok else 1
            st_f = self.st.get(f"{fac}|{cap}")
            if st_f and self.env.now >= self.w:
                st_f.offers += 1
                st_f.declined += 0 if ok else 1
            self.log(p, "offer", facility=fac, home=home, travel_min=round(t_f), home_travel_min=round(t_h),
                     est_wait_days=round(w_f, 1), home_wait_days=round(w_h, 1), p_accept=round(pacc, 3), accepted=ok)
            if not ok:
                yield self.env.timeout(self.ln(p, P.REBOOK_AFTER_DECLINE_DAYS))
                fac = home
        st = self.st.get(f"{fac}|{cap}") if fac else None
        if st is None:
            yield self.env.timeout(self.ln(p, 30))
            self.enter(p, "COLONOSCOPY")
            return True
        p.facilities[cap] = fac
        while True:
            seen = yield from self.serve(p, st, "COLONOSCOPY", behavioural=True)
            if seen:
                return True
            p.dna += 1
            if p.dna >= P.MAX_DNA_BEFORE_LOST and not self.s.monitoring:
                p.lost = True
                self.log(p, "lost_to_follow_up", after_dna=p.dna)
                return False
            gap = self.ln(p, P.REBOOK_AFTER_DNA_DAYS)
            if self.s.monitoring:      # CancerFlow alert: missed appointment triggers proactive rebooking call
                gap = min(gap, P.ALERT_RESPONSE_DAYS + 3)
            self.log(p, "rebook", days=round(gap, 1))
            yield self.env.timeout(gap)

    # ------------------------------------------------------------------ patient process
    def patient(self, p: PatientRec):
        if p.route == "screening":
            self.enter(p, "FIT_POSITIVE")
            yield self.delay(p, "FIT_POSITIVE")
            self.enter(p, "NURSE_ASSESSMENT", p.facilities.get("colonoscopy"))
            yield self.delay(p, "NURSE_ASSESSMENT", admin_only=True)
        else:
            p.gp = self.s.munis[p.muni].get("hc")
            self.enter(p, "GP_VISIT", p.gp)
            yield self.delay(p, "GP_VISIT")
            self.enter(p, "REFERRAL_SENT", p.gp)
            yield self.delay(p, "REFERRAL_SENT")
            self.enter(p, "REFERRAL_RECEIVED")
            yield self.delay(p, "REFERRAL_RECEIVED", admin_only=True)
        ok = yield from self.book_colonoscopy(p)
        if not ok:
            return
        if p.rng.random() >= P.P_CANCER[p.route]:
            self.enter(p, "CANCER_EXCLUDED")
            return
        self.enter(p, "HISTOLOGY", p.facilities.get("colonoscopy"))
        yield self.delay(p, "HISTOLOGY")
        self.enter(p, "CANCER_CONFIRMED")
        yield self.delay(p, "CANCER_CONFIRMED")
        yield from self.use(p, "ct_imaging", "STAGING")
        yield self.delay(p, "STAGING", admin_only=True)
        yield from self.use(p, "mdt_crc", "MDT_DECISION")
        yield self.delay(p, "MDT_DECISION", admin_only=True)
        r, acc = p.rng.random(), 0.0
        stage, cap = P.TREATMENT_MIX[-1][0], P.TREATMENT_MIX[-1][1]
        for s_, c_, prob in P.TREATMENT_MIX:
            acc += prob
            if r < acc:
                stage, cap = s_, c_
                break
        if cap is None:
            self.enter(p, stage)
        else:
            yield from self.use(p, cap, stage)

    def arrivals(self, route):
        lam = self.s.arrivals_per_week[route] / 7.0
        sf = self.s.screening_factor
        lam_max = lam * (max(sf.values()) if (route == "screening" and sf) else 1.0)
        rng = self.rng_arr
        while True:
            yield self.env.timeout(rng.exponential(1.0 / lam_max))
            m = self.mids[_draw(self.mcum, rng.random())]
            u = rng.random()                                   # always drawn -> streams stay paired
            county = self.s.munis[m]["county"]
            if route == "screening" and sf and u > sf.get(county, 1.0) / max(sf.values()):
                continue
            pid = self.next_pid = self.next_pid + 1
            prng = np.random.default_rng([self.seed, 1000 + pid])
            p = PatientRec(pid, route, m, county, prng, sample_agent(prng, route))
            p.sample = self.collect and pid % P.SAMPLE_EVERY == 0
            self.patients.append(p)
            self.env.process(self.patient(p))

    def background(self, st: Station):
        def job():
            with st.res.request(priority=P.STANDARD_PRIORITY) as req:
                yield req
                yield self.env.timeout(st.svc)
        while True:
            yield self.env.timeout(self.rng_bg.exponential(1.0 / st.bg_rate_per_day))
            self.env.process(job())

    def sampler(self):
        while True:
            yield self.env.timeout(7.0)
            for st in self.st.values():
                st.qsamples.append((self.env.now, len(st.res.queue)))

    def run(self):
        # screening stream first, then symptomatic (fixed order keeps the arrival stream identical across scenarios)
        for r in ("screening", "symptomatic"):
            self.env.process(self.arrivals(r))
        for st in self.st.values():
            if st.bg_rate_per_day > 0:
                self.env.process(self.background(st))
        self.env.process(self.sampler())
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
        scopes = ("national", p.county, "arch:" + p.attrs["archetype"])
        for c in evaluate_clocks(PACK, hist, as_of):
            if c.started_at is None or c.id not in ("referral_to_treatment", "fit_positive_to_colonoscopy"):
                continue
            t0 = (c.started_at - T0).total_seconds() / 86400
            if not (lo <= t0 <= hi):
                continue
            target = c.targets[0]["days"]
            if p.lost:
                ok = 0.0                                   # lost to follow-up = target missed (never silently censored)
            elif c.status == "met":
                ok = 1.0
            elif c.status == "missed" or (c.status == "running" and c.elapsed_days > target):
                ok = 0.0
            else:
                continue
            for scope in scopes:
                agg[scope][f"{c.id}.within_target"].append(ok)
                if c.status in ("met", "missed") and not p.lost:
                    agg[scope][f"{c.id}.days"].append(c.elapsed_days)
                    if c.id == "referral_to_treatment":
                        agg[scope]["rtt_bins"].append(next(lo_ for lo_, hi_ in RTT_BINS if c.elapsed_days < hi_))
        if p.hist and lo <= p.hist[0][1] <= hi:
            for (s1, t1), (_, t2) in zip(p.hist, p.hist[1:]):
                for scope in ("national", p.county):
                    agg[scope][f"stage:{s1}"].append(t2 - t1)
            for scope in scopes:
                agg[scope]["beh:patients"].append(1)
                agg[scope]["beh:alt_offers"].extend([1] * p.n_offers)
                agg[scope]["beh:alt_declined"].extend([1] * p.n_declined + [0] * (p.n_offers - p.n_declined))
                agg[scope]["beh:appointments"].extend([1] * p.n_appts)
                agg[scope]["beh:dna"].extend([1] * p.n_dna + [0] * (p.n_appts - p.n_dna))
                agg[scope]["beh:lost"].append(1.0 if p.lost else 0.0)
            if p.stalls:
                for scope in ("national", p.county):
                    agg[scope]["stall_days"].append(sum(d for _, d in p.stalls))
    window = model.h - model.w
    weeks = window / 7
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
            elif k == "stall_days":
                row["stall_days.mean"] = float(np.mean(v))
                row["stalls.n"] = float(len(v))
        b = mets
        if b.get("beh:patients"):
            n_off, n_app = len(b.get("beh:alt_offers", [])), len(b.get("beh:appointments", []))
            row["behaviour.alt_offers_per_week"] = n_off / weeks
            row["behaviour.alt_decline_rate"] = float(np.mean(b["beh:alt_declined"])) if n_off else 0.0
            row["behaviour.dna_rate"] = float(np.mean(b["beh:dna"])) if n_app else 0.0
            row["behaviour.lost_rate"] = float(np.mean(b["beh:lost"]))
            row["behaviour.patients_per_week"] = len(b["beh:patients"]) / weeks
        out["region"][scope] = row
    for k, st in model.st.items():
        ws = [w for t, w in st.waits]
        crc_server_time = st.n * window / st.pool
        qs = [(t, q) for t, q in st.qsamples if t >= model.w]
        trend = float(np.polyfit([t / 7 for t, _ in qs], [q for _, q in qs], 1)[0]) if len(qs) >= 4 else 0.0
        out["facility"][k] = {"utilisation": st.busy / crc_server_time, "mean_wait_days": float(np.mean(ws)) if ws else 0.0,
                              "p90_wait_days": float(np.percentile(ws, 90)) if ws else 0.0,
                              "throughput_per_week": st.served / weeks, "queue_end": float(len(st.res.queue)),
                              "capacity_per_week": st.per_week,
                              "queue_trend_per_week": trend, "queue_max": float(max((q for _, q in qs), default=0)),
                              "offers_per_week": st.offers / weeks,
                              "decline_rate": st.declined / st.offers if st.offers else 0.0,
                              "dna_rate": st.dnas / st.appointments if st.appointments else 0.0,
                              "wasted_slots_per_week": (st.wasted / st.svc) / weeks if st.svc < 1e8 else 0.0}
    flows = defaultdict(float)
    for p in model.patients:
        if p.hist and p.hist[0][1] >= model.w:
            for cap, fac in p.facilities.items():
                flows[f"{p.county}->{fac}|{cap}"] += 1
            if p.gp and "colonoscopy" in p.facilities:
                flows[f"hc:{p.gp}->{p.facilities['colonoscopy']}|colonoscopy"] += 1
    out["edge"] = {k: {"flow_per_week": v / weeks} for k, v in flows.items()}
    for f in model.s.purchases:
        st = model.st.get(f"{f['facility_id']}|{f['capability']}")
        if st:
            out["facility"][f"{f['facility_id']}|{f['capability']}"]["purchase_cost_eur_per_week"] = st.served / weeks * f["cost_per_procedure"]
    if model.collect:
        out["patients"] = [patient_record(p) for p in model.patients
                           if p.sample and p.hist and lo <= p.hist[0][1] <= hi]
    return out


def patient_record(p: PatientRec) -> dict:
    hist = [StageEntry(s, T0 + timedelta(days=t)) for s, t in p.hist]
    clocks = {c.id: {"status": c.status, "days": None if c.elapsed_days is None else round(c.elapsed_days, 1)}
              for c in evaluate_clocks(PACK, hist, T0 + timedelta(days=400)) if c.started_at}
    return {"pid": p.pid, "route": p.route, "municipality": p.muni, "county": p.county, "gp": p.gp,
            "attrs": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.attrs.items()},
            "facilities": p.facilities, "lost": p.lost, "dna": p.dna,
            "start_day": round(p.hist[0][1], 2), "events": p.events, "clocks": clocks}
