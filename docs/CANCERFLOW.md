# CancerFlow: shared design doc

> **Living document.** Update it whenever a decision is made. Newest decisions go at the top of §2.
> Owners: tech lead (architecture, maths) · Claude Code (implementation) · Shams (patient/doctor app)
> Last updated: 2026-11-14 (Sat). Judging: Sun 2026-11-15 14:00.

---

## 1. Product in one paragraph

There is one backend and there are two front-ends.
- **Care-continuity app** (Shams). Patients and clinicians see where each pathway stands. Its job is to make sure **no patient falls through the cracks**: overdue steps, unresolved referrals and missing follow-ups are all flagged.
- **National network twin** (tech lead + Claude). An interactive, zoomable network running from the whole country down to wellbeing services counties, then individual hospitals, then synthetic patients. It shows routing, how resources are divided, and where the bottlenecks are, and it lets you compare scenarios.

Both front-ends read the **same event stream and pathway state machine**. Neither app keeps its own model of a pathway.

## 2. Decision log

| # | Date | Decision | Why |
|---|---|---|---|
| D11 | 11-14 | Simulation writes only aggregate `sim_metric` rows. It never writes events into the operational log | Keeps fake simulated events out of the care app |
| D10 | 11-14 | Schema is SQL-first (`backend/db/migrations/*.sql`) on Aiven PostgreSQL 18 + PostGIS, schema `cf`. Event log is append-only, enforced by a DB trigger | Shams can read the schema directly. Corrections are new events, which gives a full audit trail. |
| D9 | 11-14 | Facilities and capabilities: everything public (OSM, hospital and county websites). Staff and capacity: simulated only where not published anywhere, tagged `SYNTHETIC` or `CALIBRATED` (enforced by DB CHECK) | Allowed by the organisers. Keeps real and simulated data separate. |
| D8 | 11-14 | The care app **creates events** (GP observations and decisions, patient actions). The backend validates each one against the pathway pack's state machine | Shams's app is the source of operational truth |
| D7 | 11-14 | Auth: demo login with a doctor/patient toggle, no passwords. Stack: Python (FastAPI) backend, TypeScript front-ends | Proof of concept |
| D6 | 11-14 | Where Finland has no per-stage target, use Denmark's colorectal cancer pathway times as the reference, **labelled as Danish** | AstraZeneca mentor (Medical Affairs): target intervals should be in *weeks*, but Finland has not set them yet. Denmark publishes them and measures compliance by region (§5). |
| D5 | 11-14 | Every number carries an epistemic tag (§3), enforced in code, not by convention | This is the judges' data-quality criterion (20%) and our core design principle |
| D4 | 11-14 | Cancer types are **pathway packs**: one declarative file each (§6). Adding a cancer type = one new pack + data bindings | The requirement is that a new type can be added "with one prompt" |
| D3 | 11-14 | Start with **colorectal** cancer | Best public data per county: screening participation [6103] and bowel cancer 5-year survival [3627]. Danish targets exist for it. |
| D2 | 11-14 | Facility-level granularity, with zoom: national → university-hospital area (YTA) → county → facility → patient | Routing and capacity decisions happen at hospital level |
| D1 | 11-14 | Two front-ends, one backend and event model | Prevents the two apps from drifting apart |

## 3. Epistemic tags (mandatory on every value)

| Tag | Meaning | Example |
|---|---|---|
| `OBSERVED` | Published statistic, with source and indicator id | Colorectal screening in North Karelia: 73 % (Sotkanet 6103, 2024) |
| `REFERENCE` | Official target or norm, with source | Finland: diagnosis + treatment within 6 weeks of referral |
| `EXTERNAL_PROXY` | Value taken from another country as a stand-in | Denmark: referral → surgery 37 days |
| `DERIVED` | Deterministic calculation from tagged inputs | County shortfall against the 80 % target |
| `CALIBRATED` | Model parameter fitted to reproduce OBSERVED aggregates, reported as a range | Colonoscopy capacity at hospital X: 30–55 per week |
| `SYNTHETIC` | Generated entity, such as a patient or event | Patient S-0042 |
| `SIMULATED` | Output of a simulation run, with confidence interval and number of replications | Median referral → treatment time of 44 d (39–50) |
| `HYPOTHESIS` | A possible explanation, not established | "Lower survival may reflect later stage at diagnosis" |

The UI must show the tag on every number. The LLM may only quote numbers that are present in the tagged output it was given.

## 4. Colorectal pathway (v0, conceptual; to be validated with mentors)

Entry routes: **A) screening** (FIT stool test invitation) or **B) symptomatic** (GP visit).

```
A: INVITED → FIT_DONE → FIT_POSITIVE ─┐
B: SYMPTOMS → GP_VISIT → REFERRAL_SENT┴→ REFERRAL_RECEIVED → COLONOSCOPY → HISTOLOGY
   → STAGING (CT; MRI if rectal) → MDT_DECISION → TREATMENT_START
     (surgery | neoadjuvant chemo-radiotherapy | systemic therapy | palliative care)
   → [ADJUVANT_START] → FOLLOW_UP
Terminal states: CANCER_EXCLUDED (benign or negative result), DECLINED, DECEASED
```

Each stage has: allowed next stages, the responsible organisation level (primary care, specialist, university hospital), the facility capability it needs (e.g. `colonoscopy`, `crc_surgery`, `radiotherapy`), and any clocks that start or stop there.

## 5. Target clocks (sources verified 2026-11-14)

| Clock | Finland | Denmark (B12 colorectal) | Tag |
|---|---|---|---|
| Referral received → treatment start | **6 weeks (42 d)**, diagnosis and treatment combined [FI-1] | **37 d** surgery, **37 d** medical oncology, **41 d** radiotherapy (OF4) [DK-1] | REFERENCE / EXTERNAL_PROXY |
| Imaging and tissue analysis | **≤ 1 week** [FI-1] | n/a | REFERENCE |
| Surgery → adjuvant therapy start | **≤ 4 weeks** [FI-1] | n/a | REFERENCE |
| Referral received → investigation starts | none | **9 d** (patient brochure, from search snippet; fetching the full document failed) [DK-2] | EXTERNAL_PROXY (verify) |
| Positive FIT → colonoscopy | **unknown**. Ask mentors / the Cancer Registry screening unit | none | TODO |
| Legal maximum (all specialties) | Assessment of need ≤ 3 weeks from referral; treatment ≤ 6 months (Health Care Act 1326/2010 §52) | n/a | REFERENCE (verify wording) |

**Real compliance data we can calibrate against: Denmark 2025, colorectal, share of patients treated within the standard time [DK-1]**

| Region | Surgery % (n) | Medical oncology % (n) | Radiotherapy % (n) |
|---|---|---|---|
| Nordjylland | 80 (321) | 62 (92) | 44 (52) |
| Midtjylland | 79 (584) | 74 (136) | 60 (67) |
| Syddanmark | 82 (661) | 80 (170) | 67 (55) |
| Hovedstaden | 73 (760) | 79 (272) | 72 (103) |
| Sjælland | 70 (395) | 74 (172) | 60 (53) |
| **Denmark** | **77 (2,721)** | **75 (842)** | **62 (330)** |

What this means for us: even Denmark, with fixed targets and monitoring, treats only about 75 % of patients within the standard time and varies about 12 pp between regions. That gives a realistic **prior** for the shape of the waiting-time distribution. It must be shown as EXTERNAL_PROXY, never as Finnish data.

## 6. Pathway pack spec (how a new cancer type is added)

```yaml
# packs/colorectal.yaml  (sketch, not final)
id: colorectal
icd10: [C18, C19, C20]
entry_routes: [screening_fit, symptomatic]
stages:
  - id: COLONOSCOPY
    requires_capability: colonoscopy
    level: specialist
    next: [HISTOLOGY, CANCER_EXCLUDED]
clocks:
  - id: referral_to_treatment
    start: REFERRAL_RECEIVED
    stop: TREATMENT_START
    targets:
      - {value_days: 42, tag: REFERENCE, source: FI-1}
      - {value_days: 37, tag: EXTERNAL_PROXY, source: DK-1, applies_to: surgery}
data_bindings:            # public indicators the twin shows / calibrates to
  screening_participation: {sotkanet: 6103}
  survival_5y: {sotkanet: 3627}
```

Adding lung cancer = writing `packs/lung.yaml`, adding its capability tags to the facility registry, and adding its sources. No engine code changes.

## 7. Shared contract with the care-continuity app (draft for Shams)

**Event** (append-only; the pathway state is always computed from the events):
```json
{
  "event_id": "uuid",
  "patient_id": "S-0042",
  "pathway_id": "uuid",
  "pack": "colorectal",
  "type": "STAGE_ENTERED | STAGE_COMPLETED | REFERRAL_SENT | REFERRAL_RECEIVED | RESULT_RECORDED | APPOINTMENT_BOOKED | ALERT_RAISED | ALERT_RESOLVED",
  "stage": "COLONOSCOPY",
  "facility_id": "fi-hosp-…",
  "actor_role": "gp | specialist | nurse | system",
  "occurred_at": "2026-11-14T10:00:00Z",
  "recorded_at": "2026-11-14T10:05:00Z",
  "payload": {},
  "epistemic_tag": "SYNTHETIC"
}
```
**Derived per pathway (served by the backend):** current stage, completed stages, running clocks with elapsed time against target, open alerts (e.g. `FIT_POSITIVE` with no `COLONOSCOPY` booked after N days), and the responsible team.

**API sketch:** `GET /pathways/{id}` · `GET /patients/{id}/pathways` · `POST /events` · `GET /alerts?facility=…` · `WS /stream` (live events for both front-ends).

Answered (D7, D8): the demo login has a doctor/patient toggle. The app creates events. Stack is Python + TypeScript, so we'll generate TS types from the FastAPI OpenAPI schema.

### 7b. Database (implemented: `backend/db/migrations/001_core.sql`)

| World | Tables |
|---|---|
| Provenance | `source` |
| Geography / statistics | `region` (country, YTA, wellbeing county, municipality), `indicator`, `indicator_value` |
| Facilities | `facility` (PostGIS point), `capability`, `facility_capability`, `facility_capacity` (CALIBRATED/SYNTHETIC only, low–high range), `staff`, `travel_time` |
| Packs | `pathway_pack` (id, version, sha256, definition JSONB) |
| Operational (care app) | `patient` (SYNTHETIC only), `app_user` (doctor/patient), `pathway`, **`event` (append-only)**, `pathway_clock`, `appointment`, `alert` (max one unresolved per pathway+rule) |
| Analytical (twin) | `scenario`, `sim_run` (seed, replications, assumptions), `sim_metric` (SIMULATED only, with CI) |

Commands: `python -m backend.db.migrate` · `python -m pytest backend/tests` (9 invariant tests, all pass).
Secrets live in `.env`, which git ignores. A template is in `.env.example`.

## 8. Known data gaps (say these in the pitch)

- No public Finnish data on **diagnosis → treatment time**, at any level of detail. This is the Goal 3 indicator. *This is a finding.*
- No public **facility capacity or waiting times**. These will be CALIBRATED or SYNTHETIC.
- Waiting indicators in Sotkanet (6411, 13, 3336) cover **all specialties**.
- The Uusimaa counties report referral data jointly through HUS.
- Stage at diagnosis is reported for only about 40 % of cases (strategy p.30).

## 9. Open questions / TODO

- [ ] Target for positive FIT → colonoscopy (ask mentors; screening programme documentation)
- [ ] Verify the wording of Health Care Act §52 and the Danish 9-day interval
- [ ] Facility registry: which Finnish hospitals do colonoscopy, colorectal surgery, radiotherapy, oncology
- [ ] Agree the event schema with Shams (§7) **before either side builds**
- [ ] Mentors: is any Finnish diagnosis → treatment figure available, even informally?

## 10. Sources

- **FI-1**: STM report 2010:6 *Syövän hoidon kehittäminen vuosina 2010–2020*, recommendations summarised in THL Ohjaus 6/2014, p.3. http://urn.fi/URN:ISBN:978-952-302-185-3
- **FI-2**: Kansallinen syöpästrategia 2026–2035 (`data/Kansallinen_syopastrategia_2026_2035_FINAL.pdf`), Kuva 1 p.5
- **FI-3**: THL Sotkanet open API, https://sotkanet.fi/rest/1.1 (snapshot in `data_visualization/data/`)
- **DK-1**: Sundhedsdatastyrelsen, *Monitorering af kræftområdet, årsopgørelse 2025*, Tabel 2.12 (B12) and the regional overview table. https://cdn1.gopublic.dk/sundhedsdatastyrelsen/Media/639168548110422377/monitorering-af-kraeftomraadet-aarsopgoerelse-2025.pdf
- **DK-2**: Sundhedsstyrelsen, *Pakkeforløb for kræft i tyk- og endetarm* (2022), patient brochure. https://sst.dk/-/media/Udgivelser/2022/Pakkeforloeb-kraeft-i-tyk-og-endetarm/Patientpjece.ashx
