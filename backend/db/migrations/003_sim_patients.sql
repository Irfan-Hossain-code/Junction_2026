-- 003: sampled synthetic patient-agent journeys per simulation run (analytical world, never operational)
SET search_path = cf, public;

CREATE TABLE sim_patient (
  run_id      uuid NOT NULL REFERENCES sim_run(id) ON DELETE CASCADE,
  pid         int  NOT NULL,                 -- same pid = same fictional person across scenarios (common random numbers)
  route       text NOT NULL,
  county      text,
  municipality text,
  archetype   text,
  attrs       jsonb NOT NULL,
  journey     jsonb NOT NULL,                -- events: stages, offers, declines, attendance, stalls, lost
  outcome     jsonb NOT NULL,                -- clocks, facilities, dna, lost
  tag         epistemic_tag NOT NULL DEFAULT 'SIMULATED' CHECK (tag IN ('SIMULATED','SYNTHETIC')),
  PRIMARY KEY (run_id, pid)
);
CREATE INDEX sim_patient_county_idx ON sim_patient(run_id, county);
