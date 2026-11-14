"""CancerFlow on Modal: one public URL for the API, the care app and the national twin.

    /             landing page linking both apps
    /app/care/    care-continuity app (care-app/dist)
    /app/twin/    national twin UI   (twin-ui/dist)
    everything else: the existing FastAPI backend (backend.api.main:app), unchanged

Deploy with `bash deploy/deploy.sh` (it builds both front-ends first). See deploy/README.md.
Secrets come from the Modal secret "cancerflow" (DATABASE_URL, VERDA_*), never from the image.
"""
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/root/app"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(ROOT / "backend" / "requirements.txt"))
    .add_local_dir(ROOT / "backend", f"{REMOTE}/backend", ignore=["**/__pycache__", "tests", "ingest/raw"])
    .add_local_dir(ROOT / "packs", f"{REMOTE}/packs")
    .add_local_dir(ROOT / "care-app" / "dist", f"{REMOTE}/web/care")
    .add_local_dir(ROOT / "twin-ui" / "dist", f"{REMOTE}/web/twin")
)

app = modal.App("cancerflow", image=image)

LANDING = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CancerFlow</title>
<style>
 :root{color-scheme:light dark;--bg:#f4f7fa;--card:#fff;--text:#17212f;--muted:#677689;--border:#e1e7ef;--accent:#0d9488}
 @media (prefers-color-scheme:dark){:root{--bg:#0f1419;--card:#171e26;--text:#e8edf2;--muted:#8b98a6;--border:#26313d;--accent:#2dd4bf}}
 body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
 main{max-width:760px;margin:0 auto;padding:10vh 16px 40px}
 h1{margin:0 0 6px;font-size:2rem} p{color:var(--muted);margin:0 0 28px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
 a.card{display:block;text-decoration:none;color:inherit;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px}
 a.card:hover{border-color:var(--accent)} a.card b{display:block;font-size:1.15rem;margin-bottom:4px;color:var(--accent)}
 small{display:block;margin-top:28px;color:var(--muted)}
</style></head><body><main>
<h1>CancerFlow</h1>
<p>A national digital twin of Finland's colorectal cancer pathway, plus the care app that makes sure no patient falls through the cracks.</p>
<div class="grid">
 <a class="card" href="app/care/"><b>Care app &rarr;</b>Patients and clinicians: where each pathway stands, what is overdue, what happens next.</a>
 <a class="card" href="app/twin/"><b>National twin &rarr;</b>Counties, hospitals and patient flows, with simulation and intervention studio.</a>
</div>
<small>All patients are synthetic demo data. API docs: <a href="docs">/docs</a></small>
</main></body></html>"""


@app.function(
    secrets=[modal.Secret.from_name("cancerflow")],
    cpu=4.0,              # the twin's live simulation runs 4 worker processes
    memory=4096,
    min_containers=1,     # always warm: no cold start for judges
    max_containers=1,     # WS /stream broadcasts from in-process memory; one container keeps live updates consistent
    timeout=900,
)
@modal.concurrent(max_inputs=200)
@modal.asgi_app(label="cancerflow")
def web():
    import sys

    sys.path.insert(0, REMOTE)
    from fastapi.responses import HTMLResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    from backend.api.main import app as api

    @api.get("/", include_in_schema=False)
    def landing():
        return HTMLResponse(LANDING)

    # Without the trailing slash, relative asset paths would resolve one level too high.
    @api.get("/app/care", include_in_schema=False)
    def care_slash():
        return RedirectResponse("/app/care/")

    @api.get("/app/twin", include_in_schema=False)
    def twin_slash():
        return RedirectResponse("/app/twin/")

    api.mount("/app/care", StaticFiles(directory=f"{REMOTE}/web/care", html=True), name="care")
    api.mount("/app/twin", StaticFiles(directory=f"{REMOTE}/web/twin", html=True), name="twin")
    return api
