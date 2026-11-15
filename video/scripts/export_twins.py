"""Export the three living twins' headline result for the video (READ-ONLY, via the API).

    python video/scripts/export_twins.py        # env API=http://localhost:8000

Metric: national referral_to_treatment.p_within_target (mean over replications) of each twin's latest stored run.
Writes video/src/data/twins.json. If the twins are regenerated (python -m backend.sim.twins), re-run this.
"""
import json
import os
import urllib.request
from pathlib import Path

API = os.environ.get("API", "http://localhost:8000")
PREFIX = {"baseline": "Twin 1", "disruption": "Twin 2", "recovery": "Twin 3"}   # the three living twins (python -m backend.sim.twins)
OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "twins.json"


def get(path):
    with urllib.request.urlopen(API + path, timeout=60) as r:
        return json.load(r)


def main():
    sc = get("/twin/scenarios")
    sc = sc if isinstance(sc, list) else next(v for v in sc.values() if isinstance(v, list))
    out, runs = {}, {}
    for key in ("baseline", "disruption", "recovery"):
        s = next(x for x in sc if x["name"].startswith(PREFIX[key]))
        run = get(f"/twin/runs/{s['latest_run']}")
        v = run["metrics"]["national"]["national"]["referral_to_treatment.p_within_target"]["mean"]
        out[key] = round(v * 100, 1)
        runs[key] = {"scenario": s["name"], "run": s["latest_run"]}
    out["metric"] = "Patients starting treatment within the target time (Finland)"
    out["source"] = ("National twin, stored runs: " + " / ".join(r["scenario"].split(":")[0] for r in runs.values())
                     + ". Synthetic patients and capacity; simulated outcomes.")
    out["runs"] = runs
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: baseline {out['baseline']} %, disruption {out['disruption']} %, recovery {out['recovery']} %")


if __name__ == "__main__":
    main()
