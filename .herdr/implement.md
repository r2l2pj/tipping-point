# implement — gee-verify (t5s)

## Changed

- `.env.example`: added commented lines documenting `GCP_SA_KEY` /
  `GOOGLE_APPLICATION_CREDENTIALS` usage (EDL_USER/EDL_PASS/GEE_PROJECT already
  present). No credential material touched — key file stayed at
  `/home/lucascoelho/code/r2l2pj/hermes-fesolucoes-6d8c3342a65f.json`, never
  printed/copied/moved/committed.
- `.herdr/implement-gee-verify.md`: findings report.

## Commands run

1. `cat .env.example` / venv check → `earthengine-api 1.7.52`, `google-auth` OK
   (no uv needed; uv available at mise path as fallback).
2. `GOOGLE_APPLICATION_CREDENTIALS=... GEE_PROJECT=hermes-fesolucoes
   .venv/bin/python /tmp/gee_verify.py`:
   - `ee.ServiceAccountCredentials`: OK
   - `ee.Initialize` FAILED: `EEException Permission
     'earthengine.computations.create' denied on resource
     'projects/hermes-fesolucoes' (or it may not exist).`
3. `.venv/bin/python /tmp/gee_probe.py` (extra diagnostic):
   - SA OAuth token refresh: OK → key is valid, SA exists.
   - `GET earthengine.googleapis.com/v1/projects/hermes-fesolucoes` → 404
   - `GET .../assets` → 404 NOT_FOUND

## Result

Service account credentials are valid but the GEE project is NOT registered
with Earth Engine (404 on the EE project resource). IMERG V07 latest-date
query could not run because `ee.Initialize` fails. Full details:
`.herdr/implement-gee-verify.md`.
