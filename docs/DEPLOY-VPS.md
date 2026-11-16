# Deploy on a VPS (L39)

Production moves from Vercel to our own VPS. The app is one Next.js 16
server (`next start`) in a Docker image. **Data does not move**: the server
reads the published files from the `fallback` branch of
`r2l2pj/tipping-point` (GitHub raw), then the pipeline's SQLite
`docs` table (`/app/.data/tipping_point.sqlite`, refreshed from the fallback
branch every ≤ 3 h while GitHub answers — keep the `/app/.data` volume), then
the copy bundled in the image (`public/data`). Neon is no longer read (L53). The 3-hourly data job keeps
publishing there; a data update never needs an app redeploy.

## 1. Build and run

Needs Docker (or Podman) and internet at build time (bun packages, Google
Fonts are downloaded by `next build` and then served by the app itself).
The build fits in 4 GB (it was built here under a 4 GB cap); plan ~0.5 GB
for the running server. On a small
VPS, build on another machine and `docker save | ssh vps docker load`.

```bash
# L53 monorepo: the app is in app/ of the data repo; the Dockerfile and
# .dockerignore sit at the repo ROOT, so build from the root as before.
# (The repo was renamed from r2l2pj/tipping-point-data on 2026-11-15; the old
# name redirects.)
git clone --single-branch --filter=blob:none https://github.com/r2l2pj/tipping-point.git && cd tipping-point   # blobless: skips the 7 GB COG history
git checkout main
docker build -t tipping-point-app \
  .
docker run -d --name tp-app --restart unless-stopped \
  -p 127.0.0.1:3000:3000 \
  --env-file /etc/tipping-point/app.env \
  -v tp-data:/app/.data \
  tipping-point-app
docker ps            # STATUS shows (healthy) after ~20 s
curl -s localhost:3000/api/health      # {"ok":true}
```

The image: bun installs and builds (same lockfile and build as Vercel, the
data sync step skips and keeps the committed `public/data`), then Node 24
runs `next start` as the non-root `node` user. `HEALTHCHECK` calls
`/api/health?live=1` (liveness: 200 `{"ok":true}` whenever the server
answers). Plain `/api/health` is what the uptime monitor and the GitHub
Actions keepalive (`.github/workflows/keepalive.yml`, every 10 minutes,
`https://tippingpoint.l17s.dev/api/health`) poll: 200 `{"ok":true}`, or 503
`{"ok":false}` = **server up but live data older than 6 h** (two missed
3-hourly runs) or unreadable. A failed ping fails the workflow run and GitHub
emails the repo owner. The body never carries the timestamp or any data.

Update: `git pull && docker build … && docker rm -f tp-app && docker run …`
(a few seconds of downtime; fine for one instance).

Podman works the same (`podman build --format docker …`: the default OCI
format drops `HEALTHCHECK`).

### Verified locally (2026-11-15, fix/cc-feedback 09bc8fe, Podman 6.1.4)

```bash
podman build --format docker --memory 4g -t tipping-point-app:smoke .   # 84 s cold, exit 0; image 1.09 GB
podman run -d --name tp-smoke --memory 1g -p 127.0.0.1:3125:3000 tipping-point-app:smoke
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" http://127.0.0.1:3125/<path>
podman healthcheck run tp-smoke; podman inspect tp-smoke --format "{{.State.Health.Status}}"
podman rm -f tp-smoke
```

| Check | Result |
| --- | --- |
| `/api/health` | 200 `{"ok":true}` (503 `{"ok":false}` when the live data is older than 6 h); `?live=1` = liveness |
| `/` | 200, 282 KB (first request 2.8 s, cold cache) |
| `/alerts` | 200, 280 KB |
| `/?case=rs_2024`, `/?case=wa_2025` | 200, 291 KB / 297 KB; RS page shows the onset row ("36.5% of 16,027") |
| Healthcheck | `healthy` |
| Runtime | Node v24.22.3, uid 1000 (`node`), ~150 MB RSS idle |
| SQLite fallback | `node:sqlite` opens and writes `/app/.data` |

## 2. Environment

Put runtime variables in `/etc/tipping-point/app.env` (mode 600, root-owned,
never in the repo). `NEXT_PUBLIC_*` are inlined into the browser bundle, so
they are **build** arguments, not runtime env.

| Variable | When | Value |
| --- | --- | --- |
| `DATA_BASE_URL` | runtime | **Leave unset.** Unset already reads the GitHub raw `fallback` branch first (`https://raw.githubusercontent.com/r2l2pj/tipping-point/fallback/data/`), then the SQLite docs copy in `/app/.data`, then the bundled copy. Setting it replaces GitHub as the only remote (tests pin it unreachable). `DATA_SQLITE` (optional) points at another SQLite file; `DATA_SQLITE_REFRESH=0` stops the 3-hourly download. |
| `GEE_ENABLED` | runtime | **Unset** (L25: no Earth Engine at runtime). `GEE_PROJECT`, `GCP_SA_KEY` stay unset too. |
| `APP_URL` | runtime | The new public URL, e.g. `https://tipping-point.example.org` (no trailing slash). |
| `PORT` | runtime | Default `3000`. Match the `-p` mapping and the proxy. |
| `ALERTS_PAUSED` | runtime | Optional `1` pauses the public alerts panel. |
| `NEXT_PUBLIC_REFRESH_HOURS` | **build arg** | Live-run period in hours, default 3. |

## 3. Reverse proxy and TLS

Keep the app private (`127.0.0.1` for a host proxy, or a private Docker
network for a containerized proxy) and put a proxy with TLS in front. The
pages **stream** (Partial Prerendering + Suspense): the proxy must not buffer
responses, or the page waits for the slowest data read.

### Caddy (automatic Let's Encrypt)

The current VPS runs `lucaslorentz/caddy-docker-proxy`, not a host Caddyfile.
`tipping-point-app` is healthy on the private `caddy_proxy` network at port
3000, with its SQLite volume mounted at `/app/.data`. DNS for
`tippingpoint.l17s.dev` points to this VPS. To restore the live route without
restarting the app or touching its volume, use the existing `node:24-slim`
image for a small, portless label carrier:

```bash
docker run -d --name tipping-point-route --restart unless-stopped \
  --network caddy_proxy --user node --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 64m --pids-limit 32 \
  --label caddy=tippingpoint.l17s.dev \
  --label caddy.reverse_proxy=tipping-point-app:3000 \
  --label caddy.reverse_proxy.flush_interval=-1 \
  --label caddy_ingress_network=caddy_proxy \
  node:24-slim node -e 'setInterval(() => {}, 1073741824)'
docker inspect tipping-point-route --format '{{.State.Status}} {{json .Config.Labels}}'
docker inspect tipping-point-app --format '{{.State.Health.Status}}'
curl -fsS -o /dev/null https://tippingpoint.l17s.dev/api/health
curl -fsS -o /dev/null https://tippingpoint.l17s.dev/
```

The route container survives Docker restarts, but its creation is still a
manual VPS step; merging this documentation does not create it. Keep the
container name `tipping-point-app` stable across deploys so Docker DNS follows
the replacement. Do not simultaneously set `caddy=tippingpoint.l17s.dev` on
the app in a deploy workflow: choose one owner for the hostname. If moving
the labels to the app, first ensure the replacement app is healthy, then
remove the helper with `docker rm -f tipping-point-route` and validate HTTPS;
restore the helper if the replacement route fails. Publishing the site does
not change `APP_URL`; configure it separately.

For a standalone Caddy installation instead, use a Caddyfile:

```caddyfile
tipping-point.example.org {
	encode zstd gzip
	reverse_proxy 127.0.0.1:3000 {
		flush_interval -1
	}
}
```

### nginx (+ certbot)

```nginx
server {
    listen 443 ssl http2;
    server_name tipping-point.example.org;
    ssl_certificate     /etc/letsencrypt/live/tipping-point.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tipping-point.example.org/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;          # streaming (PPR / Suspense)
        proxy_read_timeout 60s;
    }
}
server { listen 80; server_name tipping-point.example.org; return 301 https://$host$request_uri; }
```

`sudo certbot --nginx -d tipping-point.example.org` issues and renews the
certificate.

## 4. Caching without the Vercel CDN

- `/_next/static/*` carries `public, max-age=31536000, immutable` from Next
  itself: browsers cache it; the proxy may too.
- Pages are a prerendered shell plus data streamed per request (Partial
  Prerendering): the data part renders on the server for every view. That
  is cheap: the data reads go through Next's `"use cache"`
  (in memory, minutes), so GitHub raw is hit at most every few minutes per
  file, not per visitor. A restart empties that cache; the first views after
  it are slower.
- `s-maxage` headers exist only on the Earth Engine API routes, which are
  off (`GEE_ENABLED` unset). Without a CDN they do nothing.
- Map PNG frames load straight from GitHub raw in the browser, not
  through the VPS, so the VPS bandwidth stays small.
- One instance only. Several instances would need a shared cache handler
  and the same `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` at build time (Next
  self-hosting guide).

## 5. What was Vercel-only (audit, 2026-11-15)

| Item | Off Vercel |
| --- | --- |
| `vercel.json` (`git.deploymentEnabled`, `ignoreCommand`) | Ignored. It only controlled Vercel's auto-deploys; no crons, rewrites or headers in it. The VPS has no auto-deploy: redeploy by hand or from CI (section 1). |
| `export const maxDuration` in 10 API routes | Ignored by `next start` (no function time limit on a VPS). Harmless. |
| Edge runtime, `@vercel/*` packages, Vercel KV/Blob, `VERCEL_URL` | None used. |
| `next/image` (needs `sharp` self-hosted) | Not used. |
| `next/font/google` | Downloaded at **build** time → the build host needs internet; fonts are then served by the app. |
| Streaming / PPR | Works with `next start`; the proxy must not buffer (section 3). |
| Data cache (`"use cache"`) | In memory per process (section 4). |
| Hard-coded `*.vercel.app` URLs | Only in docs (`docs/DEPLOY.md`, `docs/PLAN.md`) and submission links — update to `APP_URL`. Nothing in `src/`. |
| GitHub Actions of the data repo | Unchanged: they publish data, they never call the app. |

Nothing in the app breaks off Vercel; the only required actions are TLS,
and the env file.

## 6. SQLite refresh from host cron (fallback)

The app refreshes `/app/.data/tipping_point.sqlite` itself, but only when a
request arrives and the copy is older than 3 h. A host cron entry makes the
refresh independent of traffic, of the app container and of the app
deployment workflow: if a deployment fails or is disabled, the data copy on
the VPS still follows the 3-hourly data job.

`deploy/vps/refresh_sqlite.py` (Python 3.8+, standard library only) downloads
`data/tipping_point.sqlite` from the `fallback` branch every 30 minutes and
replaces the volume copy only when all of these hold:

- the download is a SQLite file, passes `PRAGMA quick_check`, and its `docs`
  table is not empty;
- its content differs from the current copy;
- its newest Now run is not older than the current copy's (a stale CDN answer
  never rolls the data back).

The replacement is one atomic rename, so the app never reads a partial file.
The app opens the file again when its modification time changes; no restart
is needed. On any failure the previous copy stays and the run exits 1.

**Scope.** The job reads one public URL and writes one file,
`tipping_point.sqlite`, in the existing `tipping-point-data` volume. It does
not create a volume, and it does not start, stop, restart or change any
container, network, Caddy route or database. It does not touch FE services
or their data. It fails, and changes nothing, when the volume does not exist.

Install (VPS administrator, from a reviewed checkout):

```sh
sudo install -o root -g root -m 0755 deploy/vps/refresh_sqlite.py \
  /usr/local/sbin/tipping-point-sqlite-refresh
sudo /usr/local/sbin/tipping-point-sqlite-refresh --dry-run   # downloads and checks, installs nothing
sudo /usr/local/sbin/tipping-point-sqlite-refresh             # "updated: ..." or "unchanged: ..."
sudo install -o root -g root -m 0644 deploy/vps/tipping-point-sqlite.cron \
  /etc/cron.d/tipping-point-sqlite
```

Check: `journalctl -t tipping-point-sqlite -n 20` shows one line per run
(`updated`, `unchanged`, `kept current copy` or `FAILED, previous copy
kept`). Remove: delete `/etc/cron.d/tipping-point-sqlite`; the app's own
refresh keeps working.

The automatic app deployment keeps the `tipping-point-data` volume, so the
two do not interfere: a deployment replaces the container, this job replaces
the data file.

## 7. Running without GitHub Actions

Everything the GitHub workflows do can run on this VPS from files in
`deploy/vps/`: the data jobs, the watchdog and the keepalive. It does not deploy
the app. Nothing here replaces a workflow by itself. Each piece is installed disabled,
checked by hand, and switched on at a cutover, one writer at a time.

> **Safety rule.** This host also runs unrelated services of another company
> (the "FE services"), currently stopped. Nothing in this section may start,
> stop, restart, remove, reconfigure or read data of any container, volume,
> network, database, Caddy route, systemd unit, cron entry, user or file that
> does not belong to tipping-point. No `docker system prune`, no
> `docker builder prune`, no wildcard container or volume operation, no global
> package upgrade. Every destructive command names its exact tipping-point
> target. Every install step only adds files. The one manager-wide command is
> `systemctl daemon-reload`, which re-reads unit files and restarts nothing.

### 7.1 Inventory of the workflows

| Workflow | Trigger | Runs | Secrets and variables | State kept outside the repo | Writes | Limit |
|---|---|---|---|---|---|---|
| `now.yml` | cron `17 */3 * * *`, manual; concurrency `now` | token check, R1-R4, `live/jobs.py now`, p_trigger (if variable), nisar_next, nisar_stack, places, manifest, history, sqlite, publish | `EARTHDATA_TOKEN`, `GCP_SA_KEY`, `GEE_PROJECT`, `NOW_P_TRIGGER`, `GITHUB_TOKEN` | `actions/cache`: `static-v1-response-4`, `dswx-state-<run>`, `prev-classes-v2-<run>`, `clim-imerg-0p1-v1`; `fallback` branch (history, nisar_stack, previous sqlite) | `fallback` branch (force-pushed orphan snapshot of `data/`), release assets over 50 MB | 45 min, nisar_stack step 12 min |
| `now-watchdog.yml` | cron `41 * * * *` and `53 * * * *` | dispatches `now.yml` when the last success is older than 12600 s and none is queued; then fails when the published manifest is older than 21600 s | `GITHUB_TOKEN` (`actions: write`) | Actions run list, manifest on `fallback` | nothing; GitHub emails on failure | 5 min |
| `nisar.yml` | cron `30 6 * * *`, manual | `live/jobs.py nisar` (`pipeline.fetch.nisar`, search-only default) | `EARTHDATA_TOKEN`, `GCP_SA_KEY`, `GEE_PROJECT`, `GITHUB_TOKEN` | none | no GEE write found in the code; `fallback` (keeps `data/now`) only when `data/` changed | 60 min |
| `validation.yml` | cron `0 7 * * *`, manual | `live/jobs.py validation` (`gee.opera`, `pipeline.fetch.swot`) | same as `nisar.yml` | none | GEE table assets (SWOT, created when missing), `data/metrics.json`; `fallback` only when `data/` changed | 60 min |
| `reference.yml` | cron `0 8 * * 1`, manual | `live/jobs.py reference` (`pipeline.fetch.reference`) | same as `nisar.yml` | none | GEE table assets (created when missing, never overwritten), `data/<case>/assets.json`; `fallback` only when `data/` changed | 60 min |
| `app-keepalive.yml` | cron `*/10 * * * *` | `curl` on `/api/health` | none | none | nothing; GitHub emails on failure | 2 min |
| `app-ci.yml` | push and pull request on the app paths | bun install, lint, test, build | none | none | nothing | none set |
| `vps-tools-ci.yml` | push and pull request on `deploy/vps` | tests of the files in this section | none | none | nothing | 10 min |

**App deploys are NOT automated on the VPS by this change.** The app is built
and replaced by hand, as in section 1. `app-ci.yml` stays on GitHub for pull
request checks. The pipeline user needs no sudo rule and has none: nothing in
this section grants one.

Actions-only features and what replaces them on the VPS:

| Actions feature | Replacement |
|---|---|
| `schedule` (GitHub drops runs under load) | systemd timers, `OnCalendar` in UTC, `Persistent=true` (one catch-up run after downtime). The backup timer replaces the dispatch of `now-watchdog.yml`. |
| `concurrency` groups | one `flock` (`pipeline.lock` in the state directory): one data job at a time, a job waits up to 55 min, the backup run never waits |
| `timeout-minutes` | `timeout` inside `pipeline-run.sh` (same minutes) and `TimeoutStartSec` outside |
| `actions/cache` | `/var/lib/tipping-point-pipeline/cache`: the same names without the run id, checked on restore and save. The first run starts empty (see 7.9). |
| `actions/checkout` | `/opt/tipping-point`, reset to `origin/main` and cleaned at the start of every run |
| `setup-python`, `setup-uv` | `uv` 0.13.8 installed once into `/usr/local/lib/tipping-point-pipeline/uv/` (not `/usr/local/bin`); the runner puts that directory first on its own `PATH`. Python 3.12, the venv and the uv cache live in the state directory |
| `GITHUB_TOKEN` push (`contents: write`) | `GH_TOKEN` in `pipeline.env`: an HTTPS token with Contents read and write, handed to the publish step only (7.3, step 6). The SSH deploy key is the documented alternative (`PIPELINE_PUSH_AUTH=ssh`). Release assets over 50 MB need `gh` and the token. |
| `github.repository`, `GITHUB_ENV`, `GITHUB_OUTPUT` | constants and shell variables in the scripts |
| secrets and variables | `/etc/tipping-point/pipeline.env` (root, 0600), read by systemd; the webhook URL alone is in `/etc/tipping-point/notify.env` |
| preinstalled `gcloud` | not needed: no step calls it after the key step, the Python code reads the key file. The key is decoded to a 0600 temporary file for the run. |
| email on a failed run | the unit fails, the journal has the log, `tipping-point-notify@` writes a line and posts to `NOTIFY_WEBHOOK_URL` (from `notify.env`) when set |

### 7.2 What is installed where

| Path | Owner | Content |
|---|---|---|
| `/usr/local/lib/tipping-point-pipeline/` | root | `pipeline-lib.sh`, `pipeline-run.sh`, `notify.sh`, `keepalive.sh`, `watchdog.sh`, and `uv/uv` (the only uv the pipeline uses) |
| `/etc/systemd/system/tipping-point-*` | root | the units in `deploy/vps/systemd/` |
| `/etc/tipping-point/pipeline.env` (0600), `notify.env` (0600) | root | settings and secrets (names only in the repository); the data tokens are in `pipeline.env` only |
| `/opt/tipping-point` | `tipping-pipeline` | dedicated blobless checkout; reset on every run, never edit by hand |
| `/var/lib/tipping-point-pipeline` | `tipping-pipeline` | state: `cache/`, `status/`, `venv/`, `uv-cache/`, `uv-python/`, `ssh/`, `notify/`, `watchdog.git/`, `pipeline.lock` |

Never install the root-owned copies from `/opt/tipping-point`: the pipeline
user can write there. Install from a reviewed checkout.

### 7.3 Install (additive)

Run from a reviewed checkout of the repository, as an administrator. Every step
only adds. A path that already exists is kept as it is when it is ours, and the
step stops when it is not. Nothing is overwritten except the root-owned copies of
the scripts and units named `tipping-point-*`, which you refresh on purpose.

```sh
# 1. user and directories. new_dir creates a missing directory, keeps one that already
#    belongs to OWNER as it is (no chown, no chmod), and stops on any other.
new_dir() { # OWNER MODE PATH
  if [ ! -e "$3" ]; then sudo install -d -o "$1" -g "$1" -m "$2" "$3"
  elif [ -d "$3" ] && [ "$(stat -c %U "$3")" = "$1" ]; then echo "kept: $3 (already $1's)"
  else echo "STOP: $3 exists and is not a directory of $1"; return 1; fi
}
if getent passwd tipping-pipeline; then
  echo "STOP: the user exists. Check it is ours (id tipping-pipeline, home, no docker group) before going on."
else
  sudo useradd --system --no-create-home --home-dir /var/lib/tipping-point-pipeline \
    --shell /usr/sbin/nologin --user-group tipping-pipeline
fi
new_dir tipping-pipeline 0750 /var/lib/tipping-point-pipeline
new_dir tipping-pipeline 0755 /opt/tipping-point         # stops if another owner holds it
new_dir root 0755 /etc/tipping-point
new_dir root 0755 /usr/local/lib/tipping-point-pipeline
id tipping-pipeline          # one group only; it must NOT be in the docker group

# 2. scripts (root-owned copies)
for f in pipeline-lib.sh pipeline-run.sh notify.sh keepalive.sh watchdog.sh; do
  sudo install -o root -g root -m 0755 "deploy/vps/$f" "/usr/local/lib/tipping-point-pipeline/$f"
done

# 3. uv 0.13.8 into a directory of this pipeline only. No other uv, and nothing in
#    /usr/local/bin, is touched. Any CPU other than x86_64 and aarch64 stops here.
(
  set -eu
  arch=$(uname -m)
  case "$arch" in x86_64 | aarch64) ;; *) echo "STOP: no uv build chosen for $arch" >&2; exit 1 ;; esac
  name="uv-$arch-unknown-linux-gnu"
  base=https://github.com/astral-sh/uv/releases/download/0.13.8
  work=$(mktemp -d)
  cd "$work"
  curl -fsSLO "$base/$name.tar.gz"
  curl -fsSLO "$base/$name.tar.gz.sha256"
  sha256sum -c "$name.tar.gz.sha256"
  tar -xzf "$name.tar.gz"
  sudo install -d -o root -g root -m 0755 /usr/local/lib/tipping-point-pipeline/uv
  sudo install -o root -g root -m 0755 "$name/uv" /usr/local/lib/tipping-point-pipeline/uv/uv
  cd /
  rm -rf "$work"
  /usr/local/lib/tipping-point-pipeline/uv/uv --version     # uv 0.13.8
)

# 4. settings and secrets: a file that exists is never replaced. Fill in the values (7.4).
[ -e /etc/tipping-point/pipeline.env ] || \
  sudo install -o root -g root -m 0600 deploy/vps/pipeline.env.example /etc/tipping-point/pipeline.env
[ -e /etc/tipping-point/notify.env ] || \
  sudo install -o root -g root -m 0600 deploy/vps/notify.env.example /etc/tipping-point/notify.env
sudoedit /etc/tipping-point/pipeline.env

# 5. the dedicated checkout (refuses a directory that is not empty or has another origin)
sudo -H -u tipping-pipeline bash /usr/local/lib/tipping-point-pipeline/pipeline-lib.sh \
  init-checkout /opt/tipping-point
```

**6. The push credential for the `fallback` branch.** The default is an HTTPS
token, because the fetch and the push then use the same network path (HTTPS to
`github.com`). Put it in `/etc/tipping-point/pipeline.env` as `GH_TOKEN=`: a
token for **r2l2pj/tipping-point** with Contents read and write (a fine-grained
token is enough). The owner believes the VPS may already hold one: ask the
owner for it, and do not look for one in files that are not tipping-point's.
If `fallback` has a ruleset against force-pushes, add the token's account to its
bypass list.

How the token is used: the runner removes it from the environment of every
data step and hands it only to the publish step (and to `gh` for release
assets). Git gets it through a credential helper set in `GIT_CONFIG_*`
environment variables for `https://github.com` only. It is never in the remote
URL, in a command line, in `.git/config` or in a log line (output that could
contain it is masked). The tests check this with a stub (`test-pipeline-run.sh`,
`test-publish.sh`); what they cannot show is an authenticated push to GitHub
itself. The environment of a running process is readable by the same user and
by root, which is the same trust as in 7.9.

Alternative, only when no token can be used: the SSH deploy key. Set
`PIPELINE_PUSH_AUTH=ssh` in `pipeline.env`, then:

```sh
new_dir tipping-pipeline 0700 /var/lib/tipping-point-pipeline/ssh
sudo -H -u tipping-pipeline ssh-keygen -t ed25519 -N '' -C 'tipping-point vps fallback publisher' \
  -f /var/lib/tipping-point-pipeline/ssh/id_ed25519
sudo cat /var/lib/tipping-point-pipeline/ssh/id_ed25519.pub
```

Add the printed public key in the repository settings, Deploy keys, with
**Allow write access**, title "tipping-point VPS pipeline". Then pin the GitHub
host keys and compare their fingerprints with the list GitHub publishes (a
second channel, by eye):

```sh
curl -fsS https://api.github.com/meta | python3 -c 'import json,sys
for k in json.load(sys.stdin)["ssh_keys"]: print("github.com", k)' \
  | sudo -H -u tipping-pipeline tee /var/lib/tipping-point-pipeline/ssh/known_hosts >/dev/null
sudo -H -u tipping-pipeline ssh-keygen -lf /var/lib/tipping-point-pipeline/ssh/known_hosts
```

In both modes the preflight (step 8) proves the credential without writing.

```sh
# 7. units: installed, not enabled
sudo install -o root -g root -m 0644 -t /etc/systemd/system deploy/vps/systemd/tipping-point-*
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/tipping-point-now.service
systemctl is-enabled tipping-point-now.timer       # prints: disabled

# 8. preflight: tools, checkout, disk, publish settings and the push check; runs no job
sudo -H -u tipping-pipeline env PIPELINE_STATE_DIR=/var/lib/tipping-point-pipeline \
  /usr/local/lib/tipping-point-pipeline/pipeline-run.sh --preflight now
sudo systemctl start 'tipping-point-notify@tipping-point-now.service.service'   # one test notice
journalctl -u 'tipping-point-notify@tipping-point-now.service.service' -n 5 --no-pager
```

The push check prints one line, `push check: PASS (auth=token): ...` or
`push check: FAIL (auth=token): ...` with the reason. It fetches `fallback`, then
runs `git push --dry-run --force` of the commit that `fallback` already has to
`refs/heads/fallback`. A dry run sends no objects and changes no ref. A PASS
means: GitHub is reachable the way the real push will reach it, the credential
was accepted, and the account may push to the repository. It does **not**
prove that a branch rule or ruleset will accept the real push (a force-push
block, required checks); GitHub applies those only to a real push. That GitHub
already rejects a wrong token at this stage, even for a public repository, is
expected but was not checked here: test it once with a wrong token and look for
FAIL. A failed preflight stops before any job runs.

### 7.4 Settings and secrets

GitHub secrets cannot be read back: take the values from their sources. Never
put a value in the repository, a chat or a log.

| Name in `pipeline.env` | Source | Needed |
|---|---|---|
| `EARTHDATA_TOKEN` | secret of the same name (`docs/EARTHDATA_TOKEN.md`) | all jobs |
| `GCP_SA_KEY_B64` | secret `GCP_SA_KEY`: `base64 < key.json \| tr -d '\n'` | all jobs (a missing key only warns, as in the workflows) |
| `GEE_PROJECT` | variable of the same name | all jobs |
| `NOW_P_TRIGGER` | variable of the same name; `1` turns the p_trigger step on | `now` |
| `PIPELINE_PUBLISH` | VPS only; `0` until the cutover, then `1` | all jobs |
| `GH_TOKEN` | the push credential: HTTPS token, Contents read and write (7.3, step 6). Also lets `gh` upload release assets over 50 MB | when `PIPELINE_PUBLISH=1` |
| `PIPELINE_PUSH_AUTH` | VPS only; unset means `token`. `ssh` selects the deploy key (7.3, step 6) | optional |

`notify.env` (own file, root, 0600) holds `NOTIFY_WEBHOOK_URL`: an optional
`https://` URL, one short plain-text POST per failed unit and hour. The notify
unit reads this file and not `pipeline.env`, so the data tokens never reach it.

### 7.5 Cutover and rollback

One writer at a time. `pipeline-run.sh` with `PIPELINE_PUBLISH=1` refuses to
start while the GitHub workflow of the same name is active.

1. **Run by hand, publishing off** (`PIPELINE_PUBLISH=0`). Only the `now` job
   is write-free this way. `PIPELINE_PUBLISH=0` stops the git push and the
   release upload, nothing else. By reading the code (not by a run), a trial
   of each job writes:

   | Job | What a trial writes | Trial while the GitHub workflow is active |
   |---|---|---|
   | `now` | only this host's checkout and state directory. It reads NASA data and Google Earth Engine assets (`NOW_LOCAL=1` skips the asset path, as `now.yml` does) and writes no asset. | yes |
   | `nisar` | nothing off the host: `jobs.py` calls `pipeline.fetch.nisar` without `--execute`, which only searches the ASF metadata. | no (not proven by a run) |
   | `validation` | `data/metrics.json` here, and `pipeline.fetch.swot` **creates a Google Earth Engine table asset** (and its folders) in `GEE_PROJECT` when the target is missing (`ingest_table`, never overwrites). | **no** |
   | `reference` | `data/<case>/assets.json` here, and `pipeline.fetch.reference` **creates Google Earth Engine table assets** when the manifest does not list them. | **no** |

   Do not start the `nisar`, `validation` or `reference` service while their
   GitHub workflow is active. Check them first with the dry run that
   `live/jobs.py` already has (it prints the step commands and runs none;
   run it after the first `now` trial has created the environment):
   ```sh
   sudo -H -u tipping-pipeline env PATH=/usr/local/lib/tipping-point-pipeline/uv:/usr/bin:/bin \
     UV_PROJECT_ENVIRONMENT=/var/lib/tipping-point-pipeline/venv UV_CACHE_DIR=/var/lib/tipping-point-pipeline/uv-cache \
     UV_PYTHON_INSTALL_DIR=/var/lib/tipping-point-pipeline/uv-python UV_PYTHON=3.12 \
     sh -c 'cd /opt/tipping-point && uv run python live/jobs.py validation --dry-run'
   ```
   Their first real run is the one after step 2 (their workflow disabled), with
   `PIPELINE_PUBLISH=0`: it may create the assets above once, which is what the
   workflow would have done.

   The `now` trial:
   ```sh
   sudo systemctl start --no-block tipping-point-now.service
   journalctl -fu tipping-point-now.service        # stop with Ctrl-C
   ```
   Expect `now finished: ok` and a line `no-push: snapshot differs from the
   current fallback: N added, M modified, K deleted`. Compare: no `DROPPED`
   line, no deleted app-facing file, and
   `sudo -u tipping-pipeline cat /opt/tipping-point/data/now/manifest.json`
   lists the same products as the published
   `https://raw.githubusercontent.com/r2l2pj/tipping-point/fallback/data/now/manifest.json`
   (the numbers differ by run time).
2. **Disable the GitHub data workflows** right after a scheduled `now.yml` run
   has finished, so the data is fresh for about three hours:
   ```sh
   for w in now.yml now-watchdog.yml nisar.yml validation.yml reference.yml; do
     gh workflow disable "$w" -R r2l2pj/tipping-point
   done
   gh run list -R r2l2pj/tipping-point --status in_progress    # wait until none of them is running
   ```
   `now-watchdog.yml` must go too: it dispatches `now.yml`, which would fail
   every hour once disabled. Leave `app-ci.yml` as it is
   (this change does not touch the app deploy), and leave `app-keepalive.yml` on: it reads one public URL, writes nothing, and
   is the only check that runs outside the VPS (see 7.9).
3. **Publish for real**: set `PIPELINE_PUBLISH=1` in `pipeline.env`, then check
   the setup once more with publishing on (it refuses while a GitHub workflow
   of that job is still active, and runs the push check of 7.3, step 8):
   ```sh
   sudo -H -u tipping-pipeline env PIPELINE_STATE_DIR=/var/lib/tipping-point-pipeline PIPELINE_PUBLISH=1 \
     /usr/local/lib/tipping-point-pipeline/pipeline-run.sh --preflight now
   sudo systemctl start --no-block tipping-point-now.service
   ```
   Check that `git ls-remote https://github.com/r2l2pj/tipping-point.git
   refs/heads/fallback` shows a new commit and that the app shows the new run.
   If the run stops with `origin has a fallback branch but it could not be
   fetched`, nothing was published and the old `fallback` is intact: fix the
   network or the token and start the service again.
4. **Enable the timers**, each by its exact name:
   ```sh
   sudo systemctl enable --now tipping-point-now.timer tipping-point-now-backup.timer \
     tipping-point-nisar.timer tipping-point-validation.timer tipping-point-reference.timer \
     tipping-point-stale-alarm.timer tipping-point-app-keepalive.timer
   ```

**Rollback, in reverse order.** (4) `sudo systemctl disable --now` the seven timers
named above (the unit names, one by one). A running job: `sudo systemctl stop tipping-point-now.service`
(or the unit of that job). (3) Set `PIPELINE_PUBLISH=0`. (2) `gh workflow
enable` the five workflows, then `gh workflow run now.yml -R
r2l2pj/tipping-point` to refresh the data at once. (1) Nothing to undo. The
GitHub secrets and variables stay in place for this reason. After a rollback of
more than 7 days, GitHub has evicted the unused caches, so the rolling state
restarts empty.

### 7.6 Checking each job

```sh
systemctl list-timers 'tipping-point-*'                 # next and last run of each enabled timer
systemctl list-units 'tipping-point-*' --state=failed   # failed tipping-point units only
journalctl -u tipping-point-now.service -n 100 --no-pager
sudo cat /var/lib/tipping-point-pipeline/status/now.last_result   # also .last_success, .last_attempt (epoch seconds)
```

| Job | Timer and service | Journal identifier |
|---|---|---|
| Now | `tipping-point-now` | `tipping-point-now` |
| Now backup (3.5 h) | `tipping-point-now-backup` | `tipping-point-now-backup` |
| NISAR, validation, reference | `tipping-point-nisar`, `-validation`, `-reference` | same names |
| Stale alarm (6 h) | `tipping-point-stale-alarm` | `tipping-point-stale-alarm` |
| Keepalive | `tipping-point-app-keepalive` | `tipping-point-app-keepalive` |

A failed unit stays visible with `systemctl status <unit>` until the next
good run, and `tipping-point-notify@` writes a `FAILED:` line to the journal.

### 7.7 Resource limits

Starting values, not measured on this host. Tune with `sudo systemctl edit
<unit>` (a drop-in under `/etc/systemd/system/<unit>.d/`).

| Unit | Nice, IO | CPU | Memory | Tasks |
|---|---|---|---|---|
| now, now-backup, nisar, validation, reference | 10, best-effort 7 | `CPUQuota=200%`, `CPUWeight=50` | `MemoryHigh=3500M`, `MemoryMax=4G` | 1024 |
| stale-alarm | 10 | none | `MemoryMax=512M` | 128 |
| app-keepalive, notify | 10 | none | `MemoryMax=128M` | 64 |

A data job stops with a clear message when less than `PIPELINE_MIN_FREE_MB`
(3072) is free under the state directory or the checkout.

**Verify on the first run.**
- `MemoryMax=4G` and `CPUQuota=200%` are guesses. After the first full `now`
  run: `systemctl show -p MemoryPeak -p CPUUsageNSec tipping-point-now.service`
  (`MemoryPeak` needs systemd 247 or newer; on older systemd read
  `systemctl status` while it runs). A peak near 4G means the job was close to
  being killed: raise the limit with a drop-in. A unit that was killed shows
  `Result=oom-kill` in `systemctl show -p Result`.

### 7.8 Assumptions about the host

Not verified on this host, only written for it: Ubuntu 22.04+ or Debian 12+,
systemd 245 or newer, git 2.27 or newer, bash 5, util-linux `flock`, GNU
`timeout`, `curl`, `tar`, `python3`, `sha256sum`, `uv` 0.13.8 (x86_64 or
aarch64 Linux), an `ssh` client only for `PIPELINE_PUSH_AUTH=ssh`, outbound
HTTPS to GitHub, NASA Earthdata and Google. RAM and disk are unmeasured: the
first run needs the full tree of `main` (the same files `actions/checkout`
fetches) plus Python 3.12 and the locked packages. The uv release files were
checked on 2026-11-15 with HTTP HEAD requests: the `.tar.gz` and the `.tar.gz.sha256`
of both architectures answer 200 after a redirect.

### 7.9 What is not covered

- **The app deploy.** Nothing here builds or deploys the app, and the pipeline
  user has no right to. `app-ci.yml` (pull request checks) stays on GitHub, and the app is
  deployed by hand as in section 1.
- **No alarm when the VPS itself is down.** The stale alarm and the keepalive
  run on the same host. `app-keepalive.yml` is the only outside check, so the
  cutover leaves it on; without it, use an outside monitor on the public health
  URL.
- **No channel besides the journal.** The optional webhook is the only push
  notice; there is no mail or chat.
- **Actions caches cannot be exported.** The first `now` run starts without the
  R1 DSWx state and the previous alert classes, like a cache miss on GitHub,
  and downloads the static inputs once.
- **Trust.** Code from `main` runs as `tipping-pipeline` with the job secrets.
  The push token (or deploy key) has write access: keep `main` protected, and
  if `fallback` has a ruleset against force-pushes, add the token's account (or
  the key) to its bypass list.
- **A failed `fallback` fetch publishes nothing.** The publish step asks origin
  whether `fallback` exists when its fetch fails, and stops unless the answer is
  "no such branch". The restore of `data/now/history` and `data/now/nisar_stack`
  from `fallback` fails the same way, and those steps are skipped (the GitHub
  workflow marks them `continue-on-error`, the VPS job keeps going).
- **Not tested on a real VPS:** systemd, the push to GitHub (token and SSH) and
  the first full run. The tests in `deploy/vps/test-*.sh` use stub commands.
