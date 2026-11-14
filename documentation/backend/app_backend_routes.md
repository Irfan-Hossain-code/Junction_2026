# CancerFlow backend routes

Base URL (local): `http://localhost:8000`. Live interactive docs: `/docs`. OpenAPI JSON: `/openapi.json`.
Start: `uvicorn backend.api.main:app --reload --port 8000`

**Auth (proof of concept).** Pick a user with `GET /auth/users?role=doctor|patient`, then call `POST /auth/login`. Send `X-User-Id: <user_id>` on every call that needs a user. There are no passwords.

**Errors.** `401` missing or unknown user · `403` wrong role or not your pathway · `404` not found · `409` business-rule violation (invalid stage transition, closed pathway, unknown type). The body is always `{"detail": "<human-readable reason>"}`.

**Time.** All timestamps are ISO-8601 UTC. The backend has a **demo clock** (`GET /clock`, `POST /admin/clock`). "Now" on the server can be fast-forwarded, which makes alerts appear.

---

## Auth / me

### `GET /auth/users?role=doctor`
```json
[{"id":"dr-kys-endo","role":"doctor","display_name":"Dr. Aino Laine","patient_id":null,
  "facility_id":"fi-hosp-kys","staff_role":"gastroenterologist","facility_name":"Kuopio University Hospital"}]
```

### `POST /auth/login`
```json
// request
{"user_id": "dr-kys-endo"}
// response
{"user": {"id":"dr-kys-endo","role":"doctor", "...": "..."}, "header": {"X-User-Id": "dr-kys-endo"}}
```

### `GET /me` → the user row. `GET /me/pathways` → patient: their own pathways; doctor: active pathways at their facility (same shape as `GET /pathways`).

### `GET /worklist` (doctor only)
Active pathways that touched the doctor's facility, plus the open alerts the facility is responsible for, critical first.
```json
{"pathways": [ /* PathwaySummary */ ], "alerts": [ /* Alert */ ]}
```

---

## Pathways

### `POST /pathways` (create; doctor)
Opens a pathway and records its first stage automatically: `SCREENING_INVITED` for `screening`, `SYMPTOMS_REPORTED` for `symptomatic`.
```json
// request
{"patient_id":"S-0042","pack_id":"colorectal","entry_route":"symptomatic",
 "opened_at":"2026-11-01T09:00:00Z","facility_id":"fi-hc-kuopio-1"}
// response 201 -> PathwayState (below)
```

### `GET /pathways?facility_id=&status=active|closed&stage=&patient_id=&limit=`
PathwaySummary list, sorted by alert severity:
```json
[{"id":"6f1c…","patient_id":"S-0042","patient_name":"Matti V.","pack_id":"colorectal","entry_route":"symptomatic",
  "status":"active","current_stage":"REFERRAL_SENT","current_stage_label":"Referral sent to specialist care",
  "opened_at":"2026-11-01T09:00:00+00:00","closed_at":null,"last_facility_id":"fi-hc-kuopio-1",
  "open_alerts":1,"max_alert_severity":"warning"}]
```

### `GET /pathways/{id}` → **PathwayState** (the main object the app renders)
```json
{
  "id":"6f1c…","patient":{"id":"S-0042","display_name":"Matti V.","birth_year":1958,"sex":"M","tag":"SYNTHETIC","lon":27.6,"lat":62.9},
  "pack_id":"colorectal","pack_version":1,"entry_route":"symptomatic","status":"active","as_of":"2026-11-14T15:00:00+00:00",
  "current_stage":{"id":"REFERRAL_SENT","label":"Referral sent to specialist care","level":"primary","capability":null,"actors":["doctor"]},
  "allowed_next":[{"id":"REFERRAL_RECEIVED","label":"Referral received by hospital","level":"specialist","capability":null,"actors":["doctor"]},
                  {"id":"DECEASED","...":"..."},{"id":"WITHDRAWN","...":"..."}],
  "timeline":[{"id":"…","seq":1,"type":"STAGE_ENTERED","stage":"SYMPTOMS_REPORTED","stage_label":"Symptoms reported",
               "facility_id":null,"actor_user_id":"pat-0042","actor_role":"patient",
               "occurred_at":"2026-11-01T09:00:00+00:00","recorded_at":"…","payload":{},"tag":"SYNTHETIC"}],
  "clocks":[{"id":"referral_to_treatment","label":"Referral → treatment start","status":"not_started","severity":null,
             "started_at":null,"stopped_at":null,"due_at":null,"elapsed_days":null,
             "targets":[{"days":42,"tag":"REFERENCE","source":"FI-1","note":"diagnosis + treatment within 6 weeks of referral"},
                        {"days":37,"tag":"EXTERNAL_PROXY","source":"DK-1","note":"Denmark B12 surgery/medical standard time"}]}],
  "alerts":[{"id":"…","rule_id":"colorectal.dwell.REFERRAL_SENT","severity":"warning","status":"open",
             "message":"No progress for 9 d at 'Referral sent to specialist care' (threshold 5 d, HYPOTHESIS). Verify the next step has been arranged.",
             "responsible_role":"gp","responsible_facility_id":"fi-hc-kuopio-1","due_at":"…",
             "evidence":{"stage":"REFERRAL_SENT","dwell_days":9.0,"threshold":{"value":5,"tag":"HYPOTHESIS","source":"CF-ASSUMPTION"}},
             "raised_at":"…","resolved_at":null}],
  "appointments":[{"id":"…","stage":"COLONOSCOPY","facility_id":"fi-hosp-kys","scheduled_for":"2026-11-20T08:00:00+00:00","status":"booked"}]
}
```
`allowed_next` is what the UI should offer as buttons. Clock `status` is one of `not_started | running | met | missed | cancelled`. While a clock is `running`, `severity` is `warning` or `critical`.

### `POST /pathways/{id}/events` (doctor or the pathway's own patient)
```json
// change stage
{"id":"<uuid v4, client-generated>","type":"STAGE_ENTERED","stage":"GP_VISIT",
 "occurred_at":"2026-11-03T10:00:00Z","facility_id":"fi-hc-kuopio-1","payload":{"note":"rectal bleeding, refer"}}
// other types
{"type":"NOTE","payload":{"text":"called patient"}}
{"type":"RESULT_RECORDED","payload":{"kind":"FIT","value":"positive"}}
{"type":"APPOINTMENT_BOOKED","payload":{"stage":"COLONOSCOPY","scheduled_for":"2026-11-20T08:00:00Z","facility_id":"fi-hosp-kys"}}
{"type":"APPOINTMENT_STATUS","payload":{"appointment_id":"<uuid>","status":"attended|cancelled|missed"}}
```
Response `201`: `{"result":{"duplicate":false,"event_id":"…","seq":4},"state":<PathwayState>}`.
- `facility_id` defaults to the doctor's facility.
- Re-sending the same `id` returns `duplicate: true` and changes nothing, so retries are safe.
- Invalid transition → `409 {"detail":"transition FIT_POSITIVE -> COLONOSCOPY not allowed; allowed: ['NURSE_ASSESSMENT','DECLINED','DECEASED','WITHDRAWN']"}`.
- Patients may record only stages whose `actors` include `patient` (`SYMPTOMS_REPORTED`, `FIT_RETURNED`, `DECLINED`), plus `NOTE`.

---

## Alerts

### `GET /alerts?status=open|acknowledged|resolved&facility_id=&severity=`
The default returns everything that is not resolved. Each Alert also includes `pathway_id`, `patient_id`, `patient_name` and `current_stage`.

### `POST /alerts/{id}/acknowledge` → writes an `ALERT_ACKNOWLEDGED` event (audit trail) and returns PathwayState.
Alerts **resolve automatically** once the condition clears, for example when the next stage is recorded.

Rule IDs: `colorectal.clock.<clock_id>` (target time approaching or exceeded) · `colorectal.dwell.<STAGE>` (no progress at a stage).

---

## Reference data
- `GET /packs`, `GET /packs/{id}`: the full pathway definition (stages, transitions, clocks, sources).
- `GET /facilities?capability=colonoscopy&region_id=&kind=`: facilities with `lon/lat`, `tag`, `source_id` and a capability list.
- `GET /regions?kind=wellbeing_county|yta|municipality|country`

## Demo clock / admin
- `GET /clock` → `{"now": "..."}`
- `POST /admin/clock {"offset_days": 21}`: fast-forwards server time by 21 days and rescans every pathway's alerts.
- `POST /admin/scan-alerts`: rescans at the current time.

## Live updates
`WS /stream` pushes JSON messages:
`{"kind":"event","pathway_id":"…","type":"STAGE_ENTERED","stage":"GP_VISIT","current_stage":"GP_VISIT","open_alerts":0}` ·
`{"kind":"pathway_created",…}` · `{"kind":"clock","now":"…","rescanned":123}`.
On `event`, refetch `GET /pathways/{id}`.
