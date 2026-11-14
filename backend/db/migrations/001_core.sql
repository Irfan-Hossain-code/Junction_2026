-- CancerFlow core schema (001)
-- Two worlds:
--   operational : patients, pathways, append-only event log, derived state/alerts/appointments (written by the care app)
--   analytical  : sources, regions, indicators, facilities, capacity, scenarios, simulation results (read by the twin)
-- Every factual value carries an epistemic tag + source. See docs/CANCERFLOW.md §3.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS cf;
SET search_path = cf, public;

CREATE TYPE epistemic_tag AS ENUM (
  'OBSERVED', 'REFERENCE', 'EXTERNAL_PROXY', 'DERIVED',
  'CALIBRATED', 'SYNTHETIC', 'SIMULATED', 'HYPOTHESIS'
);

-- ------------------------------------------------------------------ provenance
CREATE TABLE source (
  id           text PRIMARY KEY,                 -- e.g. 'FI-1', 'DK-1', 'sotkanet', 'osm'
  title        text NOT NULL,
  publisher    text,
  url          text,
  accessed_on  date,
  kind         text NOT NULL CHECK (kind IN ('statistic','guideline','law','report','dataset','literature','model','generator','mentor')),
  note         text
);

-- ------------------------------------------------------------------ geography
CREATE TABLE region (
  id          text PRIMARY KEY,                  -- 'hva-972' (wellbeing county, Sotkanet id), 'yta-kys', 'fi'
  kind        text NOT NULL CHECK (kind IN ('country','yta','wellbeing_county','municipality')),
  name_fi     text NOT NULL,
  name_en     text,
  parent_id   text REFERENCES region(id),
  sotkanet_id int UNIQUE,
  population  int,
  centroid    geography(Point, 4326),
  boundary    geography(MultiPolygon, 4326),
  source_id   text REFERENCES source(id)
);

-- ------------------------------------------------------------------ indicators (public statistics)
CREATE TABLE indicator (
  id          text PRIMARY KEY,                  -- 'sotkanet:6103'
  title       text NOT NULL,
  unit        text,
  higher_is_better boolean,
  source_id   text NOT NULL REFERENCES source(id)
);

CREATE TABLE indicator_value (
  indicator_id text NOT NULL REFERENCES indicator(id),
  region_id    text NOT NULL REFERENCES region(id),
  year         int  NOT NULL,
  gender       text NOT NULL DEFAULT 'total',
  value        double precision NOT NULL,
  tag          epistemic_tag NOT NULL DEFAULT 'OBSERVED' CHECK (tag IN ('OBSERVED','DERIVED')),
  PRIMARY KEY (indicator_id, region_id, year, gender)
);

-- ------------------------------------------------------------------ facilities
CREATE TABLE capability (
  code        text PRIMARY KEY,                  -- 'colonoscopy', 'crc_surgery', 'radiotherapy', 'mdt_crc', ...
  description text NOT NULL
);

CREATE TABLE facility (
  id          text PRIMARY KEY,                  -- 'fi-hosp-kys'
  name        text NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('health_center','regional_hospital','central_hospital','university_hospital','private_clinic','other')),
  region_id   text REFERENCES region(id),        -- wellbeing county
  yta_id      text REFERENCES region(id),
  address     text,
  geom        geography(Point, 4326) NOT NULL,
  tag         epistemic_tag NOT NULL CHECK (tag IN ('OBSERVED','SYNTHETIC')),
  source_id   text NOT NULL REFERENCES source(id),
  external_ref text                              -- e.g. OSM id
);
CREATE INDEX facility_geom_idx ON facility USING gist (geom);

CREATE TABLE facility_capability (
  facility_id text NOT NULL REFERENCES facility(id) ON DELETE CASCADE,
  capability  text NOT NULL REFERENCES capability(code),
  tag         epistemic_tag NOT NULL CHECK (tag IN ('OBSERVED','REFERENCE','HYPOTHESIS','SYNTHETIC')),
  source_id   text NOT NULL REFERENCES source(id),
  note        text,
  PRIMARY KEY (facility_id, capability)
);

-- capacity is never public in Finland -> only fitted or synthetic, always as a range
CREATE TABLE facility_capacity (
  facility_id text NOT NULL REFERENCES facility(id) ON DELETE CASCADE,
  capability  text NOT NULL REFERENCES capability(code),
  per_week_low  double precision NOT NULL CHECK (per_week_low >= 0),
  per_week_high double precision NOT NULL CHECK (per_week_high >= per_week_low),
  tag         epistemic_tag NOT NULL CHECK (tag IN ('CALIBRATED','SYNTHETIC')),
  source_id   text NOT NULL REFERENCES source(id),
  valid_from  date NOT NULL DEFAULT current_date,
  PRIMARY KEY (facility_id, capability, valid_from)
);

CREATE TABLE staff (
  facility_id text NOT NULL REFERENCES facility(id) ON DELETE CASCADE,
  role        text NOT NULL,                     -- 'gastroenterologist', 'colorectal_surgeon', 'oncologist', 'endoscopy_nurse', ...
  fte         double precision NOT NULL CHECK (fte >= 0),
  tag         epistemic_tag NOT NULL CHECK (tag IN ('OBSERVED','SYNTHETIC')),
  source_id   text NOT NULL REFERENCES source(id),
  PRIMARY KEY (facility_id, role)
);

CREATE TABLE travel_time (
  origin_region_id text NOT NULL REFERENCES region(id),   -- municipality centroid
  facility_id      text NOT NULL REFERENCES facility(id) ON DELETE CASCADE,
  minutes          double precision NOT NULL,
  km               double precision,
  method           text NOT NULL,                          -- 'osrm', 'haversine*1.3'
  tag              epistemic_tag NOT NULL CHECK (tag IN ('DERIVED')),
  PRIMARY KEY (origin_region_id, facility_id)
);

-- ------------------------------------------------------------------ pathway packs (YAML in repo is source of truth)
CREATE TABLE pathway_pack (
  id          text NOT NULL,                     -- 'colorectal'
  version     int  NOT NULL,
  sha256      text NOT NULL,
  definition  jsonb NOT NULL,
  loaded_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, version)
);

-- ------------------------------------------------------------------ operational world
CREATE TABLE patient (
  id            text PRIMARY KEY,                -- 'S-0042'
  display_name  text NOT NULL,
  birth_year    int,
  sex           text CHECK (sex IN ('F','M','X')),
  home_region_id text REFERENCES region(id),     -- municipality
  home_geom     geography(Point, 4326),
  tag           epistemic_tag NOT NULL DEFAULT 'SYNTHETIC' CHECK (tag = 'SYNTHETIC'),  -- no real patient data, ever
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
  id          text PRIMARY KEY,                  -- demo login: pick a user, no password (proof of concept)
  role        text NOT NULL CHECK (role IN ('doctor','patient')),
  display_name text NOT NULL,
  patient_id  text REFERENCES patient(id),
  facility_id text REFERENCES facility(id),
  staff_role  text,
  CHECK ((role = 'patient' AND patient_id IS NOT NULL) OR (role = 'doctor' AND facility_id IS NOT NULL))
);

CREATE TABLE pathway (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id    text NOT NULL REFERENCES patient(id),
  pack_id       text NOT NULL,
  pack_version  int  NOT NULL,
  entry_route   text NOT NULL,
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed')),
  -- projection fields (rebuildable from events)
  current_stage text,
  last_seq      int  NOT NULL DEFAULT 0,
  opened_at     timestamptz NOT NULL,
  closed_at     timestamptz,
  FOREIGN KEY (pack_id, pack_version) REFERENCES pathway_pack(id, version)
);
CREATE INDEX pathway_patient_idx ON pathway(patient_id);

CREATE TABLE event (
  id           uuid PRIMARY KEY,                 -- client-generated -> retries are idempotent
  pathway_id   uuid NOT NULL REFERENCES pathway(id),
  seq          int  NOT NULL,                    -- per-pathway order, assigned by backend
  type         text NOT NULL,
  stage        text,
  facility_id  text REFERENCES facility(id),
  actor_user_id text REFERENCES app_user(id),
  actor_role   text NOT NULL CHECK (actor_role IN ('doctor','patient','system','generator')),
  occurred_at  timestamptz NOT NULL,
  recorded_at  timestamptz NOT NULL DEFAULT now(),
  payload      jsonb NOT NULL DEFAULT '{}',
  tag          epistemic_tag NOT NULL DEFAULT 'SYNTHETIC' CHECK (tag = 'SYNTHETIC'),
  UNIQUE (pathway_id, seq)
);
CREATE INDEX event_pathway_idx ON event(pathway_id, seq);
CREATE INDEX event_occurred_idx ON event(occurred_at);

-- append-only: corrections are new events, never edits
CREATE FUNCTION forbid_event_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'event log is append-only (% blocked)', TG_OP;
END $$;
CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON event
  FOR EACH ROW EXECUTE FUNCTION forbid_event_mutation();

-- projections maintained by the backend (rebuildable from event log)
CREATE TABLE pathway_clock (
  pathway_id   uuid NOT NULL REFERENCES pathway(id) ON DELETE CASCADE,
  clock_id     text NOT NULL,
  started_at   timestamptz,
  stopped_at   timestamptz,
  PRIMARY KEY (pathway_id, clock_id)
);

CREATE TABLE appointment (
  id           uuid PRIMARY KEY,
  pathway_id   uuid NOT NULL REFERENCES pathway(id) ON DELETE CASCADE,
  stage        text NOT NULL,
  facility_id  text REFERENCES facility(id),
  scheduled_for timestamptz NOT NULL,
  status       text NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','attended','cancelled','missed')),
  source_event uuid NOT NULL REFERENCES event(id)
);

CREATE TABLE alert (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pathway_id   uuid NOT NULL REFERENCES pathway(id) ON DELETE CASCADE,
  rule_id      text NOT NULL,                    -- e.g. 'colorectal.fit_positive_no_colonoscopy'
  severity     text NOT NULL CHECK (severity IN ('info','warning','critical')),
  status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open','acknowledged','resolved')),
  message      text NOT NULL,
  responsible_facility_id text REFERENCES facility(id),
  responsible_role text,
  due_at       timestamptz,
  evidence     jsonb NOT NULL DEFAULT '{}',      -- clock, elapsed, target, target source id
  raised_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz
);
CREATE INDEX alert_open_idx ON alert(status, responsible_facility_id);
-- at most one unresolved alert per (pathway, rule): re-running the rules engine is idempotent
CREATE UNIQUE INDEX alert_one_unresolved ON alert(pathway_id, rule_id) WHERE status <> 'resolved';

-- ------------------------------------------------------------------ analytical world
CREATE TABLE scenario (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  description text,
  pack_id     text NOT NULL,
  scope_region_id text REFERENCES region(id),
  params      jsonb NOT NULL DEFAULT '{}',       -- interventions: capacity deltas, rerouting rules, participation changes
  is_baseline boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sim_run (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scenario_id  uuid NOT NULL REFERENCES scenario(id) ON DELETE CASCADE,
  model_version text NOT NULL,
  seed         bigint NOT NULL,
  replications int NOT NULL CHECK (replications > 0),
  horizon_days int NOT NULL,
  assumptions  jsonb NOT NULL DEFAULT '{}',      -- every non-observed input, with its tag + source
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  started_at   timestamptz,
  finished_at  timestamptz
);

CREATE TABLE sim_metric (
  run_id     uuid NOT NULL REFERENCES sim_run(id) ON DELETE CASCADE,
  scope_kind text NOT NULL CHECK (scope_kind IN ('national','region','facility','edge','stage')),
  scope_id   text NOT NULL,                      -- region id, facility id, 'facA->facB', stage id
  metric     text NOT NULL,                      -- 'p_within_target', 'median_wait_days', 'utilisation', 'flow_per_week'
  mean       double precision NOT NULL,
  ci_low     double precision,
  ci_high    double precision,
  tag        epistemic_tag NOT NULL DEFAULT 'SIMULATED' CHECK (tag = 'SIMULATED'),
  PRIMARY KEY (run_id, scope_kind, scope_id, metric)
);
