"""Network-level anomaly detection on simulation runs (deterministic rules, explicit thresholds).

Every alarm: type, severity, location (+ map coordinates), affected nodes, triggering metric, value, threshold,
evidence, duration and a suggested intervention. Nothing is generated for visual effect: if no metric crosses
a threshold, there is no alarm.
"""
from __future__ import annotations

import math
from typing import Optional

from backend.pathways.pack import get_pack
from backend.sim.snapshot import Snapshot

PACK = get_pack("colorectal")
CAP = {"colonoscopy": "colonoscopy", "ct_imaging": "CT staging", "mdt_crc": "MDT meetings", "crc_surgery": "colorectal surgery",
       "oncology": "oncology", "radiotherapy": "radiotherapy"}
WINDOW = "275-day simulated measurement window (after 90-day warm-up)"

T = {  # thresholds (ASSUMPTIONS, tunable)
    "overload_util": 0.95, "overload_util_critical": 0.99, "queue_trend": 0.5, "queue_min": 5,
    "dna_warn": 0.10, "dna_crit": 0.15, "decline_warn": 0.40, "decline_min_offers": 0.5,
    "stage_ratio": 1.5, "stage_abs_days": 3.0, "lost_warn": 0.02, "imbalance_spare_util": 0.75,
    "imbalance_max_min": 150, "downstream_wait_days": 2.0, "downstream_wait_ratio": 1.5, "throughput_drop": 0.2,
}


def _v(d, k):
    x = (d or {}).get(k)
    return None if x is None else x["mean"]


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def detect(snap: Snapshot, M: dict, B: Optional[dict] = None) -> list[dict]:
    out: list[dict] = []
    fac = snap.facilities
    stations = {k: v for k, v in M.get("facility", {}).items() if k.split("|")[1] in CAP and k.split("|")[0] in fac}

    def loc_f(fid):
        f = fac[fid]
        return {"kind": "facility", "facility_id": fid, "county_id": f["county"], "name": f["name"], "lon": f["lon"], "lat": f["lat"]}

    def add(**kw):
        kw.setdefault("tag", "SIMULATED")
        kw.setdefault("duration", WINDOW)
        kw["id"] = f"{kw['type']}:{kw['location'].get('facility_id') or kw['location'].get('county_id')}:{kw.get('capability', '')}"
        out.append(kw)

    for k, m in stations.items():
        fid, cap = k.split("|")
        u, w, tr, q = _v(m, "utilisation") or 0, _v(m, "mean_wait_days") or 0, _v(m, "queue_trend_per_week") or 0, _v(m, "queue_end") or 0
        name = fac[fid]["name"]
        if u >= T["overload_util"]:
            sev = "critical" if (u >= T["overload_util_critical"] or tr >= T["queue_trend"]) else "warning"
            add(type="sustained_overload", severity=sev, capability=cap, location=loc_f(fid), nodes=[fid],
                title=f"{CAP[cap].capitalize()} at {name} running at {u:.0%}",
                metric="utilisation", value=round(u, 3), threshold=T["overload_util"],
                evidence={"utilisation": round(u, 3), "mean_wait_days": round(w, 1), "queue_trend_per_week": round(tr, 2)},
                suggestion=f"Add {CAP[cap]} sessions or redistribute referrals to hospitals with spare capacity; check patient acceptance first.")
        if tr >= T["queue_trend"] and q >= T["queue_min"]:
            add(type="growing_queue", severity="critical" if tr >= 2 * T["queue_trend"] else "warning", capability=cap,
                location=loc_f(fid), nodes=[fid], title=f"{CAP[cap].capitalize()} queue at {name} growing +{tr:.1f}/week",
                metric="queue_trend_per_week", value=round(tr, 2), threshold=T["queue_trend"],
                evidence={"queue_at_end": round(q), "queue_trend_per_week": round(tr, 2), "utilisation": round(u, 3)},
                suggestion="Demand exceeds sustainable throughput: the queue will keep growing without capacity or routing changes.")
        dna, appts = _v(m, "dna_rate") or 0, (_v(m, "throughput_per_week") or 0)
        if cap == "colonoscopy" and dna >= T["dna_warn"] and appts > 1:
            add(type="high_nonattendance", severity="critical" if dna >= T["dna_crit"] else "warning", capability=cap,
                location=loc_f(fid), nodes=[fid], title=f"{dna:.0%} of colonoscopy appointments at {name} are missed",
                metric="dna_rate", value=round(dna, 3), threshold=T["dna_warn"],
                evidence={"dna_rate": round(dna, 3), "wasted_slots_per_week": round(_v(m, "wasted_slots_per_week") or 0, 1)},
                suggestion="Proactive reminders / follow-up calls; transport support where travel is long; overbook cautiously.")
        offers, decl = _v(m, "offers_per_week") or 0, _v(m, "decline_rate") or 0
        if offers >= T["decline_min_offers"] and decl >= T["decline_warn"]:
            add(type="capacity_not_accepted", severity="warning", capability=cap, location=loc_f(fid), nodes=[fid],
                title=f"Spare {CAP[cap]} at {name} is offered but {decl:.0%} of patients decline",
                metric="decline_rate", value=round(decl, 3), threshold=T["decline_warn"],
                evidence={"offers_per_week": round(offers, 1), "decline_rate": round(decl, 3)},
                suggestion="Capacity exists on paper but is not reachable for patients: offer transport support or closer appointments.")

    # regional imbalance: overloaded station with spare capacity of the same kind nearby
    for k, m in stations.items():
        fid, cap = k.split("|")
        if (_v(m, "utilisation") or 0) < T["overload_util"]:
            continue
        spare = []
        for k2, m2 in stations.items():
            f2, c2 = k2.split("|")
            if c2 != cap or f2 == fid or fac[f2]["kind"] == "private_clinic":
                continue
            mins = _km((fac[fid]["lon"], fac[fid]["lat"]), (fac[f2]["lon"], fac[f2]["lat"])) * 1.3 / 70 * 60
            if mins <= T["imbalance_max_min"] and (_v(m2, "utilisation") or 1) <= T["imbalance_spare_util"]:
                spare.append((round(mins), f2, round(_v(m2, "utilisation"), 2)))
        if spare:
            spare.sort()
            add(type="regional_imbalance", severity="warning", capability=cap, location=loc_f(fid),
                nodes=[fid] + [s[1] for s in spare[:3]], title=f"{CAP[cap].capitalize()} overloaded at {fac[fid]['name']} while nearby units have spare capacity",
                metric="utilisation_gap", value=round((_v(m, "utilisation") or 0) - spare[0][2], 2), threshold=T["overload_util"] - T["imbalance_spare_util"],
                evidence={"spare_units": [{"facility": fac[s[1]]["name"], "travel_min": s[0], "utilisation": s[2]} for s in spare[:3]]},
                suggestion="Behaviour-aware re-routing (with transport support) towards the spare units listed.")

    # stalled transitions + lost to follow-up, per county
    nat = M.get("national", {}).get("national", {})
    for cid, reg in M.get("region", {}).items():
        if cid not in snap.counties:
            continue
        c = snap.counties[cid]
        worst = None
        for key, x in reg.items():
            if not key.startswith("stage_days."):
                continue
            s = key[len("stage_days."):]
            n = _v(nat, key)
            if n and x["mean"] >= T["stage_ratio"] * n and x["mean"] - n >= T["stage_abs_days"]:
                if worst is None or x["mean"] - n > worst[1] - worst[2]:
                    worst = (s, x["mean"], n)
        if worst:
            s, v, n = worst
            add(type="stalled_transition", severity="warning", location={"kind": "county", "county_id": cid, "name": c["name"]},
                nodes=[], counties=[cid], title=f"Patients in {c['name'].replace(' wellbeing services county', '')} stay {v:.1f} d at '{PACK.stages[s].label}' (national {n:.1f} d)",
                metric=f"stage_days.{s}", value=round(v, 1), threshold=round(T["stage_ratio"] * n, 1),
                evidence={"stage": s, "county_days": round(v, 1), "national_days": round(n, 1)},
                suggestion="Process delay, not capacity: review the hand-off at this stage (booking, nurse assessment, MDT scheduling).")
        lost = _v(reg, "behaviour.lost_rate") or 0
        if lost >= T["lost_warn"]:
            add(type="lost_to_follow_up", severity="warning", location={"kind": "county", "county_id": cid, "name": c["name"]},
                nodes=[], counties=[cid], title=f"{lost:.1%} of patients in {c['name'].replace(' wellbeing services county', '')} are lost to follow-up",
                metric="behaviour.lost_rate", value=round(lost, 3), threshold=T["lost_warn"],
                evidence={"lost_rate": round(lost, 3), "dna_rate": round(_v(reg, "behaviour.dna_rate") or 0, 3)},
                suggestion="Switch on stalled-step monitoring: missed appointments trigger proactive rebooking instead of silent drop-out.")

    # intervention side-effects (needs a base run)
    if B:
        for k, m in stations.items():
            b = B.get("facility", {}).get(k)
            if not b:
                continue
            fid, cap = k.split("|")
            w, bw = _v(m, "mean_wait_days") or 0, _v(b, "mean_wait_days") or 0
            if w - bw >= T["downstream_wait_days"] and w >= T["downstream_wait_ratio"] * max(bw, 0.1):
                add(type="downstream_bottleneck", severity="warning", capability=cap, location=loc_f(fid), nodes=[fid],
                    title=f"Intervention side-effect: {CAP[cap]} wait at {fac[fid]['name']} rose {bw:.1f} → {w:.1f} d",
                    metric="mean_wait_days", value=round(w, 1), threshold=round(bw + T["downstream_wait_days"], 1),
                    evidence={"wait_before": round(bw, 1), "wait_after": round(w, 1), "utilisation_after": round(_v(m, "utilisation") or 0, 3)},
                    suggestion="Upstream changes pushed more patients here; add capacity at this step or rebalance.")
            th, bth = _v(m, "throughput_per_week") or 0, _v(b, "throughput_per_week") or 0
            if bth > 1 and th < (1 - T["throughput_drop"]) * bth:
                add(type="throughput_drop", severity="info", capability=cap, location=loc_f(fid), nodes=[fid],
                    title=f"{CAP[cap].capitalize()} throughput at {fac[fid]['name']} fell {bth:.1f} → {th:.1f}/week",
                    metric="throughput_per_week", value=round(th, 1), threshold=round((1 - T["throughput_drop"]) * bth, 1),
                    evidence={"before": round(bth, 1), "after": round(th, 1)},
                    suggestion="Expected if patients were redirected elsewhere; otherwise investigate lost capacity.")
    rank = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda a: (rank[a["severity"]], -abs(a["value"] or 0)))
    return out
