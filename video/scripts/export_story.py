"""Export a completed Emma story run from the shared database into video/src/data/story.json (READ-ONLY).

    .venv/bin/python video/scripts/export_story.py            # latest run that reached 'treatment'
    .venv/bin/python video/scripts/export_story.py --run 19

The video never invents dates or events: every date on screen comes from this file, which is built from
  - ops_story_event (the 17-step director log: step, event type, simulated time, message), and
  - Emma's append-only pathway event log (appointments booked / cancelled / missed / attended, stages).
Steps completed inside the apps (Donny approving, Emma confirming) are not always in the director log, so their
time and detail are taken from the pathway events they produced.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.db.conn import connect          # noqa: E402
from backend.ops.story import EVENT_TYPE, STEPS  # noqa: E402
from backend.ops.world import FAC_SHORT, FI, GP_FACILITY, PERSONAS, RESOURCES, travel_min  # noqa: E402

OUT = ROOT / "video" / "src" / "data" / "story.json"
RES_NAME = {r[0]: r[3] for r in RESOURCES}


def fmt(dt):
    return dt.astimezone(FI).isoformat() if dt else None


def _slots_from(msg):
    m = re.search(r"(\d+) replacement slots", msg or "")
    return int(m.group(1)) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int)
    a = ap.parse_args()
    with connect() as c:
        c.execute("SET TRANSACTION READ ONLY")
        run = a.run or c.execute("SELECT max(run) FROM ops_story_event WHERE step='treatment' AND status='completed'").fetchone()[0]
        log = {r[0]: {"sim_at": fmt(r[1]), "message": r[2]} for r in c.execute(
            "SELECT step, sim_at, message FROM ops_story_event WHERE run=%s AND status='completed' ORDER BY id", (run,)).fetchall()}
        pw = c.execute("SELECT id FROM pathway WHERE patient_id=%s", (f"OPS-EMMA-R{run}",)).fetchone()[0]
        evs = [dict(type=r[0], stage=r[1], at=r[2], payload=r[3] or {}) for r in c.execute(
            "SELECT type, stage, occurred_at, payload FROM event WHERE pathway_id=%s ORDER BY occurred_at, seq", (pw,)).fetchall()]
        appts = [dict(id=str(r[0]), stage=r[1], status=r[2], at=fmt(r[3]), facility=FAC_SHORT.get(r[4]) or ("Niinivaara health station" if r[4] == GP_FACILITY else r[4]), facility_id=r[4])
                 for r in c.execute("SELECT id, stage, status, scheduled_for, facility_id FROM appointment WHERE pathway_id=%s ORDER BY scheduled_for",
                                    (pw,)).fetchall()]

    # pathway-derived detail for steps that may have been completed in the apps
    cancel = next((e for e in evs if e["type"] == "APPOINTMENT_STATUS" and e["payload"].get("status") == "cancelled"), None)
    rebook = next((e for e in evs if e["type"] == "APPOINTMENT_BOOKED" and cancel and e["payload"].get("replaces") == cancel["payload"]["appointment_id"]), None)
    missed = next((e for e in evs if e["type"] == "APPOINTMENT_STATUS" and e["payload"].get("status") == "missed"), None)
    for key, ev in (("disruption", cancel), ("recover", rebook)):
        if key not in log and ev:
            log[key] = {"sim_at": fmt(ev["at"]), "message": None}

    def appt(stage, status):
        return next((x for x in appts if x["stage"] == stage and x["status"] == status), None)

    cohort = [p for p in PERSONAS if p["pid"] != "OPS-EMMA"]
    data = {
        "source": f"ops_story_event run {run} + pathway {pw} (read-only export)",
        "run": run,
        "tags": {"patients": "SYNTHETIC", "outcomes": "SIMULATED"},
        "people": {"patient": "Emma Nieminen", "gp": "Dr. Anna Kettunen (GP, Niinivaara)",
                   "specialist": "Dr. Ville Hakkarainen (gastroenterology)", "admin": "Donny Lehtonen (regional care coordinator)"},
        "places": {"gp": "Niinivaara health station, Joensuu", "home": "North Karelia Central Hospital (PKKS), Joensuu",
                   "disrupted_resource": RES_NAME["res-pkks-endo2"]},
        "outage": {"resource": RES_NAME["res-pkks-endo2"], "hours": 48,
                   # deterministic from backend/ops/world.py: Emma + the cohort patients booked on Unit 2 in the window
                   "affected": 1 + len(cohort),
                   "urgent": 1 + sum(1 for p in cohort if p["priority"] == "urgent"),
                   "others": [{"name": p["name"], "muni": p["muni"], "priority": p["priority"]} for p in cohort]},
        "appointments": appts,
        "rebook_after_outage": {"facility": FAC_SHORT.get(rebook["payload"]["facility_id"]), "resource": RES_NAME.get(rebook["payload"].get("resource_id")),
                                "at": fmt(datetime.fromisoformat(rebook["payload"]["scheduled_for"])),
                                # same DERIVED travel model the rescheduling notification uses (interventions.py)
                                "travel_min": travel_min(PERSONAS[0], rebook["payload"]["facility_id"])} if rebook else None,
        "replacement_slots_evaluated": _slots_from(log.get("miss", {}).get("message")),
        "missed_at": fmt(missed["at"]) if missed else None,
        "steps": [{"key": k, "label": l, "actor": act, "event": EVENT_TYPE[k], **log.get(k, {"sim_at": None, "message": None})}
                  for k, l, act, _ in STEPS],
    }
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} from run {run}: {sum(1 for s in data['steps'] if s['sim_at'])}/17 steps dated")


if __name__ == "__main__":
    main()
