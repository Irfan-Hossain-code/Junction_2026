# Deploying CancerFlow (Modal)

Everything is served from **one Modal web endpoint**:

| Path | What |
|---|---|
| `/` | landing page |
| `/app/care/` | care-continuity app |
| `/app/twin/` | national twin UI |
| everything else | the FastAPI backend, unchanged (`/docs`, `/twin/*`, `/pathways`, `WS /stream`, …) |

The database stays on Aiven. Secrets live in a Modal secret, never in the image or git.

## One-time setup (inside WSL)
```bash
cd ~/hackathon/Junction_2026
.venv/bin/pip install modal
.venv/bin/modal setup                                          # browser login to the Modal account
.venv/bin/modal secret create cancerflow --from-dotenv .env    # DATABASE_URL, VERDA_* from your local .env
```

## Deploy / redeploy
```bash
bash deploy/deploy.sh
```
The script builds both front-ends with `VITE_API` set to the public URL (`https://<workspace>--cancerflow.modal.run`), then runs `modal deploy deploy/modal_app.py`. If your workspace name is different from the Modal profile name, set the URL yourself: `CANCERFLOW_URL=https://… bash deploy/deploy.sh`.

## Settings (`deploy/modal_app.py`)
- `cpu=4`, `memory=4096`: the twin's "Simulate intervention" runs 4 worker processes.
- `min_containers=1`: always warm, so judges get no cold start. **This costs credits while the app is deployed.** Run `modal app stop cancerflow` after judging.
- `max_containers=1`: live updates (`WS /stream`) are broadcast from process memory, so all users must share one container.

Logs: `.venv/bin/modal app logs cancerflow`. Dashboard: https://modal.com/apps
