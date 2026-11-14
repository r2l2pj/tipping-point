# GEE service account verification — 2026-11-14

**Verdict: DOES NOT WORK (yet).** Key file is valid and the SA can mint OAuth
tokens, but Cloud project `hermes-fesolucoes` is not usable by Earth Engine.

## What was tested

- Env: `GOOGLE_APPLICATION_CREDENTIALS=/home/lucascoelho/code/r2l2pj/hermes-fesolucoes-6d8c3342a65f.json`, `GEE_PROJECT=hermes-fesolucoes` (key file never printed/copied/moved).
- `earthengine-api 1.7.52` already in repo `.venv` — no uv needed.
- `ee.ServiceAccountCredentials('r2l2pj-nasa@hermes-fesolucoes.iam.gserviceaccount.com', <key>)` → constructed OK.
- `creds.refresh()` → **OK**. Google accepted the key; SA exists, private key valid.
- `ee.Initialize(creds, project='hermes-fesolucoes')` → **FAILED**:

```
EEException: Permission 'earthengine.computations.create' denied on resource
'projects/hermes-fesolucoes' (or it may not exist).
```

- REST probe (`AuthorizedSession` against `earthengine.googleapis.com/v1`):
  - `GET /projects/hermes-fesolucoes` → **404**
  - `GET /projects/hermes-fesolucoes/assets` → **404** "Asset ... does not exist"

## Diagnosis

404 on `projects/hermes-fesolucoes` from the EE API means Earth Engine has no
registered project by that name. Two possible causes, first most likely:

1. **Project not registered with Earth Engine.** Fix: a project owner/editor
   registers it at https://code.earthengine.google.com/register (noncommercial)
   or enables EE for commercial use in the Cloud console, then grants
   `r2l2pj-nasa@...` the `roles/earthengine.writer` role on the project.
2. SA lacks the EE IAM role on a registered project — less likely given the 404
   (an unregistered project and a permission-denied project both deny
   `computations.create`, but only the unregistered one 404s on the resource).

## IMERG V07 latest image date

**Not obtainable** — all compute calls die at `ee.Initialize`, so
`NASA/GPM_L3/IMERG_V07` could not be queried. "Modo Agora" freshness is
unverified until the project is registered. Retry this script after
registration to get the date.

## Repo change

- `.env.example`: added commented `GCP_SA_KEY` / `GOOGLE_APPLICATION_CREDENTIALS`
  documentation lines (key path stays outside the repo).
