# CancerFlow demo video (Remotion)

A 1920×1080, 30 fps, about 4:55 video for Junction 2026. The animated scenes are code (React + SVG in [Remotion](https://remotion.dev)), so they render the same way every time. Irfan's recordings drop into named slots.

This folder is self-contained. It doesn't import from or change `care-app/`, `twin-ui/` or `backend/`.

## Truth sources (nothing is invented)
| On screen | Comes from | Refresh with |
|---|---|---|
| Every date, appointment, facility and event type in Emma's story | `src/data/story.json`: director run 19 (`ops_story_event`) plus Emma's append-only pathway events. Read-only export. | `.venv/bin/python video/scripts/export_story.py [--run N]` |
| Notification texts (phone and care team) | Copied from `backend/ops/story.py`, `interventions.py` and `followup.py` (source noted per message in `src/data/messages.ts`), filled with the run's values | the same export |
| The 8 patients on Endoscopy Unit 2 and their slots, the outage and the map positions | `backend/ops/world.py` (deterministic) and `/twin/geo/counties` | `.venv/bin/python video/scripts/export_map.py` |
| 73.4 % / 36.6 % / 53.3 % | `/twin/runs/<latest run>` of Twin 1/2/3, `referral_to_treatment.p_within_target`, labelled **SIMULATED** | `python3 video/scripts/export_twins.py` |
| Phone screens | real screenshots in `public/screens/` (see below). Until a screenshot exists, the phone shows a rebuild of the care-app inbox using the real message texts, stamped *"recreated UI"* | `scripts/capture_screens.mjs` |

## Scenes
Every scene is its own composition (`npm run studio` → *Scenes*), and **Full** is the assembly. Order, durations, narration text, clip and audio file names, and the caption switch all live in **`src/config.ts`**. Beat timings for each animated scene are at the top of that scene's file (`B2`, `B3`, `B5`, `B6`, `B7`).

| # | Scene | Type | Length |
|---|---|---|---|
| S1 | We cured cancer | **clip** `s1-intro.mp4` + logo overlay at `LOGO_AT` | 50 s |
| S2 | The beginning: symptoms → GP → urgent referral → specialist booked → reminder → Confirm | animated | 34 s |
| S3 | The disruption: colonoscopy on Unit 2 → schedule board → Unit 2 offline 48 h → East Finland map, 8 affected (4 urgent) | animated | 17 s |
| S4 | Donny and the AI | **clip** `s4-donny-call.mp4` + lower third | 45 s |
| S5 | Recovery: rescheduled to Savonlinna → confirm → missed → care team alert → append-only log → **clinician authorises** a new slot | animated | 30 s |
| S6 | Diagnosis and treatment: 29 Nov colonoscopy → 4 Dec cancer confirmed → 6 Dec staging → 8 Dec MDT → 17 Dec surgery, 31 days from referral, then the director's event log | animated | 22 s |
| S7 | National twin: Emma's lane among many → Finland map → **clip** `s7-twin.mp4` (screen recording) + SIMULATED results overlay → back to Emma | animated + clip | 60 s |
| S8 | Because every day matters | **clip** `s8-ending.mp4` + end card | 40 s |

## What Irfan records
Put the files in `public/clips/` and `public/audio/` with exactly these names. Missing files render as labelled placeholders, so the video always builds.

| File | What | Notes |
|---|---|---|
| `clips/s1-intro.mp4` | To camera, the approved intro (≈50 s) | Set `durationSec` in `config.ts` to the real length. Set `LOGO_AT` to the second you say "Meet CancerFlow". |
| `clips/s4-donny-call.mp4` | Donny at the laptop, phone rings, the live ElevenLabs call over the twin (≈45 s) | Screen-record the twin (Operations panel: comparison table, approval) together with the camera, or cut between them before dropping in. Numbers must be what the screen showed. |
| `clips/s7-twin.mp4` | Screen recording of the twin: Baseline → Disruption → MPC Recovery, Δ view, an optimiser or comparison view (≈45 s) | Starts at 9.8 s into S7. Use `clip.startFromSec` to trim. |
| `clips/s8-ending.mp4` | To camera, the approved closing (≈36 s) | The last `END_CARD_SEC` (5 s) is covered by the end card |
| `audio/s2-beginning.mp3`, `s3-disruption.mp3`, `s5-recovery.mp3`, `s6-diagnosis.mp3`, `s7-twin.mp3` | Voice-over for the animated scenes. Lines and timings are in `config.ts` | Then set `CAPTIONS = false` (or keep captions as subtitles). Retime the line `at` values or beats if the read is faster or slower. |

## Real phone screenshots
```bash
# read-only: one screenshot of whatever the app shows right now
node scripts/capture_screens.mjs snap phone-00-start
# full run: RESETS Emma's demo story, steps all 17 director steps, screenshots the phone and the clinician view after each
node scripts/capture_screens.mjs story --yes-reset-story
python reset_story.py          # afterwards (repo root): restore the demo start state
```
`story` mode writes to the shared demo DB, exactly like the director's Reset and run buttons. Don't run it while someone is rehearsing. It needs the backend on :8000 and the care app on :5174.

## Run and render (inside WSL)
```bash
cd video && npm install
sh scripts/setup-local-libs.sh       # one-time, no sudo: headless Chrome + NSS libs + emoji font into .local-libs/
npm run studio                       # preview, scrub and edit each scene live
npm run render                       # out/cancerflow-demo.mp4 (whole video)
sh scripts/render.sh S5-Recovery out/s5.mp4         # one scene
sh scripts/render.sh --still S3-Disruption out/s3.png --frame=300
```
A full render takes several minutes on the WSL VM (concurrency 4, see `remotion.config.ts`).

## Known gaps
- `pre_procedure` (check-in request) isn't shown. The director dates it the evening before the rebooked slot, which is earlier than the outage, so on a timeline it reads as out of order.
- The lanes at the start of S7 are an illustrative motif ("hundreds of patients") and are marked *illustrative*. All other visuals are data-driven.
