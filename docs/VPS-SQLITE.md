# Subscriber SQLite on the VPS

This migrates only the Telegram `subscribers` table from Neon Postgres to the
existing Docker volume `tipping-point-data`. Neon Object Storage remains a
separate fallback for public map files; this runbook does not move it.

The app and a one-shot notifier container share
`/app/.data/tipping-point.sqlite`. The GitHub Actions runner sends only the
published manifest, four alert COGs, and municipal boundaries to the VPS over
SSH. It never receives subscriber records or the SQLite file. The notifier
runs locally on the VPS and has no published port.

Do not run the production cutover before both code PRs have been reviewed and
merged. The migration script refuses empty sources and non-empty SQLite
targets, and prints only the copied row count.

## Prepare the VPS

After the data-repository PR is merged, build the notifier image from the
reviewed `main` revision on the VPS (or another trusted build host that can
load the image onto the VPS):

```sh
git clone --depth 1 https://github.com/r2l2pj/tipping-point-data.git /opt/tipping-point-data-notifier
docker build -f /opt/tipping-point-data-notifier/deploy/vps/Dockerfile.notify \
  -t tipping-point-notifier:stable /opt/tipping-point-data-notifier
```

The image runs as UID 1000, matching the app container's SQLite volume owner.
It needs no network port; it only needs outbound HTTPS to Telegram.

Create a dedicated non-root SSH account named `tipping-notify`. Do not add it
to the Docker group and do not grant it read access to `app.env`. Install the
fixed-path wrapper and grant only that exact command through sudo:

```sh
sudo install -d -o root -g root -m 0755 /var/lib/tipping-point-notify
sudo install -d -o tipping-notify -g tipping-notify -m 0755 /var/lib/tipping-point-notify/inbox
sudo install -o root -g root -m 0755 \
  /opt/tipping-point-data-notifier/deploy/vps/run-notify.sh \
  /usr/local/sbin/tipping-point-notify
printf '%s\n' 'tipping-notify ALL=(root) NOPASSWD: /usr/local/sbin/tipping-point-notify' \
  | sudo tee /etc/sudoers.d/tipping-point-notify >/dev/null
sudo chmod 0440 /etc/sudoers.d/tipping-point-notify
sudo visudo -cf /etc/sudoers.d/tipping-point-notify
```

Create `/etc/tipping-point/notifier.env` as a root-owned mode-0600 file with
only `TELEGRAM_TOKEN` and, optionally, `APP_URL`. Do not reuse `app.env` here:
the worker must not inherit unrelated application credentials. Set or edit the
file through a secure host-side method, never in chat or CI logs.

Install the public half of a dedicated GitHub Actions SSH key in that account.
The account needs write access only to the inbox above; its parent must remain
root-owned so the wrapper mounts a stable, read-only host path. Keep the account
outside the Docker group. The root-owned wrapper runs a fixed image, mounts the
shared DB and that read-only parent, and reads the Telegram secret without
exposing it to the SSH account.

## Copy the subscriber rows

1. Schedule a short maintenance window and stop all subscriber writes to Neon:
   pause Telegram webhook delivery without dropping pending updates, stop the
   legacy notifier's subscriber updates, and wait for in-flight requests to
   finish. Keep writes paused until the app is serving the copied SQLite DB.
   A row count alone cannot catch a `/parar` or consent change made during the
   copy. Keep the current Neon URL in `/etc/tipping-point/app.env` for this step.
   Run the command from a host account that can read that root-owned file and
   use Docker:

```sh
docker run --rm --user 1000:1000 \
  --env-file /etc/tipping-point/app.env \
  --mount source=tipping-point-data,target=/app/.data \
  tipping-point-notifier:stable \
  scripts/migrate_neon_to_sqlite.py --target /app/.data/tipping-point.sqlite
```

2. Confirm the row count and subscriber fields against the paused Neon source;
   do not rely on row count alone. The script
   includes rows with `consent=false` and preserves coordinates, consent,
   alert state, labels, dates, and creation timestamps. It performs one SQLite
   transaction; it does not delete or update the Neon source.
3. Back up the resulting SQLite database off-host before switching traffic.
   Treat the backup as sensitive personal data (chat IDs and location points).
4. In `/etc/tipping-point/app.env`, replace the Neon `DATABASE_URL` with:
   `DATABASE_URL=sqlite:/app/.data/tipping-point.sqlite`.
5. Recreate/redeploy the app container with the reviewed deployment workflow so
   it loads the new runtime variable. Keep the `tipping-point-data` volume.
   Verify the SQLite-backed bot, then register the webhook on the VPS and only
   then resume queued updates and scheduled notifications. Do not reopen the
   old Neon webhook after SQLite starts accepting writes.

Do not copy the live `.sqlite` file with ordinary `cp` while either process is
writing; use SQLite's online backup API or take the backup during a controlled
maintenance window.

## Enable the VPS notifier

In the `r2l2pj/tipping-point-data` GitHub repository:

- Add Actions secrets `VPS_HOST`, `VPS_USER=tipping-notify`, `VPS_DEPLOY_KEY`,
  and `VPS_KNOWN_HOSTS`; `VPS_SSH_PORT` is optional and defaults to 22. Use the
  dedicated account and key above, and pin the host key. Do not reuse the app
  deployment identity or a root/Docker-group account.
- Leave repository variable `VPS_NOTIFIER_ENABLED` unset until the image,
  SQLite copy, app restart, and a test against non-production fixtures are
  verified.
- Set `VPS_NOTIFIER_ENABLED=1` to route the scheduled notifier to the VPS.
  This workflow mode requires the notifier image, SQLite data, and
  `/etc/tipping-point/notifier.env` to be ready first. The app env file holds
  the SQLite URL and webhook credentials; it is never copied to the GitHub
  runner. The root wrapper reads only the separate `notifier.env` file.
- After a scheduled run succeeds and alerts are verified, remove the data
  repository's legacy `DATABASE_URL` Actions secret. The Neon object-storage
  publish secrets are unrelated and remain separate.

The notifier step is `continue-on-error` so a delivery outage does not erase a
successful data refresh. Check its run log for the summary and any `[notify]`
errors; it must not print subscriber rows or credentials.

## Rollback caution

The Neon source is unchanged by the initial copy, but it stops receiving new
subscriber changes after cutover. Do not simply switch the app or workflow back
to Neon after SQLite has accepted new subscriptions, unsubscribes, or alert
updates: that could lose changes or revive a deleted subscriber. First preserve
the live SQLite database and reconcile its current rows into the chosen
recovery target, then switch the app and workflow together. No live database
migration or deployment is performed by opening the code PRs.
