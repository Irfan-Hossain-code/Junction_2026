-- 004: operations layer (shared demo state for disruptions, interventions, notifications, patient agents).
-- Everything here is SYNTHETIC demo state. It never touches reference data and never rewrites the event log:
-- appointment changes still go through append-only events (APPOINTMENT_BOOKED / APPOINTMENT_STATUS).
SET search_path = cf, public;

-- A bookable diagnostic/treatment resource inside a real facility (e.g. "Endoscopy Unit 2").
CREATE TABLE ops_resource (
  id            text PRIMARY KEY,
  facility_id   text NOT NULL REFERENCES facility(id),
  capability    text NOT NULL,
  name          text NOT NULL,
  slots_per_day int  NOT NULL CHECK (slots_per_day >= 0),
  status        text NOT NULL DEFAULT 'online' CHECK (status IN ('online','offline')),
  tag           text NOT NULL DEFAULT 'SYNTHETIC' CHECK (tag = 'SYNTHETIC')
);

-- Concrete slots: real occupancy. booked + pathway_id NULL = background booking by a patient outside the demo cohort.
CREATE TABLE ops_slot (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id    text NOT NULL REFERENCES ops_resource(id) ON DELETE CASCADE,
  starts_at      timestamptz NOT NULL,
  status         text NOT NULL DEFAULT 'free' CHECK (status IN ('free','booked','blocked')),
  pathway_id     uuid REFERENCES pathway(id) ON DELETE SET NULL,
  appointment_id uuid,
  booked_by      text,                       -- 'background' | 'story' | 'intervention:<id>' | 'followup'
  UNIQUE (resource_id, starts_at)
);
CREATE INDEX ops_slot_res_time ON ops_slot(resource_id, starts_at);

CREATE TABLE ops_disruption (
  id          uuid PRIMARY KEY,
  kind        text NOT NULL,                 -- 'resource_down' | 'no_show' | ...
  resource_id text REFERENCES ops_resource(id) ON DELETE CASCADE,
  starts_at   timestamptz NOT NULL,
  ends_at     timestamptz NOT NULL,
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','resolved')),
  summary     jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Auditable intervention lifecycle. Nothing operational changes before status = APPROVED -> APPLIED.
CREATE TABLE ops_intervention (
  id            uuid PRIMARY KEY,
  disruption_id uuid REFERENCES ops_disruption(id) ON DELETE CASCADE,
  parent_id     uuid REFERENCES ops_intervention(id) ON DELETE CASCADE,
  source        text NOT NULL,               -- 'optimiser' | 'admin' | 'voice' | 'map' | 'replan' | 'followup'
  status        text NOT NULL CHECK (status IN ('DETECTED','PROPOSED','EVALUATED','AWAITING_APPROVAL',
                                                'APPROVED','APPLIED','REJECTED','FAILED','SUPERSEDED')),
  title         text NOT NULL DEFAULT '',
  proposal      jsonb NOT NULL DEFAULT '{}',
  evaluation    jsonb NOT NULL DEFAULT '{}',
  decided_by    text,
  justification text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  applied_at    timestamptz
);

CREATE TABLE ops_intervention_log (
  id              bigserial PRIMARY KEY,
  intervention_id uuid NOT NULL REFERENCES ops_intervention(id) ON DELETE CASCADE,
  status          text NOT NULL,
  actor           text,
  note            text,
  at              timestamptz NOT NULL DEFAULT now()
);

-- Simulated notifications to patients, receiving institutions, care teams and the administrator.
CREATE TABLE ops_notification (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dedupe_key  text UNIQUE,                   -- repeated approvals never duplicate a notification
  audience    text NOT NULL CHECK (audience IN ('patient','facility','care_team','admin')),
  patient_id  text REFERENCES patient(id) ON DELETE CASCADE,
  facility_id text REFERENCES facility(id),
  pathway_id  uuid REFERENCES pathway(id) ON DELETE CASCADE,
  severity    text NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warning','critical')),
  kind        text NOT NULL,
  title       text NOT NULL,
  body        text NOT NULL DEFAULT '',
  payload     jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL,
  read_at     timestamptz
);
CREATE INDEX ops_notification_patient ON ops_notification(patient_id, created_at DESC);

-- Persistent patient-agent persona + small interaction memory (SYNTHETIC people).
CREATE TABLE ops_patient_agent (
  patient_id text PRIMARY KEY REFERENCES patient(id) ON DELETE CASCADE,
  persona    jsonb NOT NULL,
  memory     jsonb NOT NULL DEFAULT '[]'
);

ALTER TABLE appointment ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;
