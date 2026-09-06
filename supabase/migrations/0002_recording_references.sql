-- 0002_recording_references.sql
-- Recordings are no longer stored/downloaded by the app: Vobiz keeps the audio
-- and we persist only its references on the calls row.
-- For fresh installs, 0001 already defines recording_id/recording_url (this file
-- is a no-op-safe ALTER for anyone who applied the original 0001 with
-- recording_key/recording_served_url).

alter table calls
  drop column if exists recording_key,
  drop column if exists recording_served_url,
  add column if not exists recording_id text,
  add column if not exists recording_url text;
