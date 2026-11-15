-- 005: story director event log (demo namespace only; SYNTHETIC). One row per attempted director step.
SET search_path = cf, public;

CREATE TABLE ops_story_event (
  id             bigserial PRIMARY KEY,
  run            int NOT NULL,
  step           text NOT NULL,
  type           text NOT NULL,                 -- e.g. appointment.booked, resource.outage_started
  status         text NOT NULL CHECK (status IN ('completed','failed')),
  correlation_id uuid NOT NULL,
  sim_at         timestamptz,
  actor          text,
  patient_id     text,
  plan_version   text,
  message        text,
  payload        jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ops_story_event_done ON ops_story_event(run, step) WHERE status = 'completed';
