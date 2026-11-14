-- 002: app settings (demo clock offset etc.)
SET search_path = cf, public;

CREATE TABLE app_setting (
  key   text PRIMARY KEY,
  value jsonb NOT NULL
);
INSERT INTO app_setting(key, value) VALUES ('clock_offset_days', '0'::jsonb);
