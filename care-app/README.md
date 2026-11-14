# CancerFlow · Care-continuity app

The patient and clinician app: **no patient falls through the cracks.** It reads and writes the same pathway state machine as the national twin through the backend's operational API (`documentation/backend/app_backend_routes.md`). It never makes pathway decisions itself. Every step is validated by the backend against `packs/colorectal.yaml`.

React 18 + TypeScript + Vite. No other runtime dependencies.

```bash
# backend (repo root)
uvicorn backend.api.main:app --port 8000
# care app (run inside WSL / Linux / mac; node 18+)
cd care-app && npm install && npm run dev        # http://localhost:5174
VITE_API=https://api.example.com npm run dev     # if the API runs elsewhere
npm run build                                    # static files in dist/ (relative paths, host anywhere)
```
If `VITE_API` is not set, the app calls port 8000 on the same host as the page. That means a phone on the same Wi-Fi can open `http://<laptop-ip>:5174`.

## Screens
| Route | Who | What |
|---|---|---|
| `#/` (logged out) | everyone | Demo login: a Patient / Clinician toggle, then pick a user. No passwords. The user id is sent as `X-User-Id`. |
| `#/` | patient | Phone-first, calm light theme (follows the phone's dark mode). Shows where they are in plain English, a 6-phase journey stepper, upcoming appointments, what happens next, and waiting-time clocks against the targets. If a step is overdue, it says the care team has already been notified. Patients can send a message, confirm their FIT sample was sent, or decline further examinations (after a confirmation step). |
| `#/worklist` | clinician | Dark theme matching the national twin. Shows alert counts, then "Needs attention" (critical alerts first, each with Acknowledge and Open buttons) and every active pathway at the clinician's facility (search, filter by alerts). There's also **+ New pathway**, which registers a synthetic patient and opens a pathway. |
| `#/p/<id>` | clinician | Record the next step (only stages the pack allows; a 409 rejection shows the backend's reason). Closing exits sit behind a confirmation. Also: target clocks, alerts, appointments (mark attended / missed / cancelled), and tools to add a note, record a result or book an appointment. The full append-only timeline is shown too. |

- **Live:** every screen refreshes itself from `WS /stream`, so a step recorded in one tab shows up in another.
- **Demo clock** (🕒 in the top bar): +7 / +14 / +21 days or reset (`POST /admin/clock`). It is **global for everyone on the backend**, so reset it after a demo.
- **Retries are safe:** event ids are client-generated UUIDs, one per user action.
- Every patient is shown as `SYNTHETIC`. Targets carry their `REFERENCE` (Finland) or `EXTERNAL_PROXY` (Denmark) tag, and dwell thresholds carry `HYPOTHESIS`.

## Demo story (2 minutes)
1. Log in as clinician **`nurse-pkks-scr`**. Matti Virtanen's colonoscopy is critically overdue at the top of the list.
2. Open the pathway and record **Colonoscopy**. The alert resolves and the timeline grows.
   ⚠️ The event log is append-only, so this permanently moves Matti's story on in the shared database. Rehearse on a pathway made with **+ New pathway** and save Matti for the real demo.
3. In a second window (phone width), log in as patient **Matti Virtanen**. He sees the new stage, explained in plain English.
4. Use 🕒 **+14 days**: new warnings appear across the worklist. **Reset** afterwards.
