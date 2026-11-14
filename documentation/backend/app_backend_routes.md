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

---

## Digital twin (simulation + optimisation)
Everything here is **analytical**. It never writes to patients or events. Every simulated number has `tag: "SIMULATED"` and a 95 % CI over replications.

### `GET /twin/network?capability=colonoscopy&run_id=` (default: latest baseline run)
One call for the map:
```json
{"capability":"colonoscopy","run_id":"…",
 "national":{"indicators":{"sotkanet:6103":{"value":73.5,"year":2024,"tag":"OBSERVED","source":"sotkanet"}},
             "sim":{"referral_to_treatment.p_within_target":{"mean":0.834,"ci":[0.81,0.857],"tag":"SIMULATED"}}},
 "counties":[{"id":"hva-972","name":"North Karelia …","yta":"yta-846","population":162091,"lon":30.1,"lat":62.9,
              "indicators":{"sotkanet:3336":{"value":99.0,"year":2025,"tag":"OBSERVED"}},
              "sim":{"referral_to_treatment.p_within_target":{"mean":0.27,"ci":[0.07,0.46]}},
              "model":{"m_primary_care":1.05,"m_specialist":1.2,"capacity_factor":0.92,"tag":"DERIVED from sotkanet 6411/3336"}}],
 "facilities":[{"id":"fi-hosp-pkks","name":"North Karelia Central Hospital (Joensuu)","kind":"central_hospital","lon":29.7,"lat":62.6,
                "county":"hva-972","yta":"yta-846",
                "capabilities":[{"capability":"colonoscopy","capacity_per_week":15.1,"capacity_tag":"SYNTHETIC",
                                 "sim":{"utilisation":{"mean":0.99,"ci":[…]},"mean_wait_days":{"mean":11.7,"ci":[…]}}}],
                "live":{"active_pathways":8,"open_alerts":4,"critical_alerts":2,"tag":"SYNTHETIC"}}],
 "flows":[{"from_county":"hva-972","to_facility":"fi-hosp-pkks","capability":"colonoscopy","flow_per_week":13.2,"cross_county":false,"tag":"SIMULATED"}]}
```
Sim metric names: `referral_to_treatment.{p_within_target,median_days,p90_days,n}`, `fit_positive_to_colonoscopy.*`, `stall_days.mean`, `stalls.n`. Facility metrics: `utilisation`, `mean_wait_days`, `p90_wait_days`, `throughput_per_week`, `queue_end`, `capacity_per_week`.

### `GET /twin/geo/counties` → GeoJSON FeatureCollection of the 22 county boundaries (`properties.id` matches `counties[].id`).
### `GET /twin/assumptions` → every model parameter, every source and the tag legend.

### Scenarios
- `GET /twin/scenarios` → `[{"id","name","description","is_baseline","latest_run","params_summary"}]`
- `POST /twin/scenarios` `{"name":"NK +20% colonoscopy","params":{…}}` → `{"id"}`. Params schema:
```json
{"capacity":[{"facility_id":"fi-hosp-pkks","capability":"colonoscopy","factor":1.2}],
 "routing":[{"from_county":"hva-972","capability":"colonoscopy","to_facility":"fi-hosp-kys","share":0.3}],
 "routing_set":{"kunta-167|colonoscopy":[["fi-hosp-pkks",0.7],["fi-hosp-kys",0.3]]},
 "screening_factor":{"hva-972":1.1},
 "arrival_factor":1.0,
 "monitoring":true}
```
- `POST /twin/scenarios/{id}/run` `{"reps":4,"seed":1000,"horizon_days":365}` → `{"run_id","seconds","metrics"}` (~25 s for 4 reps)
- `POST /twin/baseline/run` re-runs the baseline.
- `GET /twin/runs/{run_id}` → run row (with `assumptions`) + all metrics.
- `GET /twin/compare?base_run=&alt_run=` → rows `{scope_kind, scope_id, metric, base, alt, delta, base_ci, alt_ci, ci_separated}`.

### `POST /twin/optimise` (OR-Tools MIP, then SimPy verification)
```json
// request
{"capability":"colonoscopy","budget_units":4,"unit_per_week":5,"max_travel_min":180,"rho":0.85,
 "w_equity":2,"w_move":20,"verify":true,"monitoring":true,"reps":4}
// response (abridged)
{"plan":{"status":"OPTIMAL","overload_before":11.37,"overload_after":0,
         "capacity_additions":[{"facility_id":"fi-hosp-pkks","capability":"colonoscopy","units":1,"add_per_week":5}],
         "routing_changes":[{"municipality":"kunta-541","name":"Nurmes","from":"fi-hosp-kaks","to":[["fi-hosp-kys",0.77]],"share_moved":0.88}],
         "utilisation":[{"facility_id":"fi-hosp-pkks","util_before":1.004,"util_after":0.759}],
         "county_travel_min_before":{…},"county_travel_min_after":{…}},
 "scenario_id":"…","run":{"run_id":"…","seconds":23.1},
 "comparison":{"rows":[{"scope_kind":"national","metric":"referral_to_treatment.p_within_target","base":0.834,"alt":0.878,"ci_separated":true}]}}
```
