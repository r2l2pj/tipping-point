#!/usr/bin/env bash
# Failure notice for one tipping-point systemd unit. Started by
# tipping-point-notify@.service through OnFailure= of the pipeline, watchdog,
# keepalive and stale-alarm units. See docs/DEPLOY-VPS.md, "Running without
# GitHub Actions".
#
#   notify.sh tipping-point-<name>.service
#   notify.sh tipping-point-notice-test.service     a test notice (see below)
#
# It always writes one line to the journal (the failed unit is the primary
# signal: `systemctl is-failed <unit>`, `journalctl -u <unit>`). When NOTIFY_WEBHOOK_URL
# is set in /etc/tipping-point/notify.env (a file of its own, with no other
# secret) it also sends one short plain-text
# HTTPS POST. Unset means no network call at all. Nothing else is notified:
# there is no mail, chat or other channel here. The same unit is announced at
# most once per NOTIFY_MIN_INTERVAL_S (default 3600) so a repeating failure
# does not flood the receiver.
#
# The test notice (the runbook starts tipping-point-notify@tipping-point-notice-test.service.service
# once, to prove the webhook) says plainly that no unit failed. It is sent every time, it
# reads and writes no throttle stamp, and so it never delays a real notice. A failed
# POST makes the unit fail, so the operator sees it.
#
# SAFETY: this host also runs unrelated services of another company ("FE
# services"), currently stopped. This script reads the status of units named
# tipping-point-*.service only (it refuses any other name), changes no unit,
# container, volume, network, database, Caddy route, cron entry or user, and
# writes only its throttle stamps under PIPELINE_STATE_DIR/notify and one
# temporary curl config file there.
set -Eeuo pipefail
umask 077

PIPELINE_STATE_DIR=${PIPELINE_STATE_DIR:-/var/lib/tipping-point-pipeline}
NOTIFY_MIN_INTERVAL_S=${NOTIFY_MIN_INTERVAL_S:-3600}
NOTIFY_TEST_UNIT=tipping-point-notice-test.service

nt_log() { echo "notify: $*" >&2; }

# Only our own service units.
nt_valid_unit() {
  [[ "${1:-}" =~ ^tipping-point-[a-z0-9-]+\.service$ ]]
}

# nt_text UNIT: one line, ASCII, at most 300 characters.
nt_text() {
  local unit=$1 result="" code="" host line
  if command -v systemctl >/dev/null 2>&1; then
    result=$(systemctl show -p Result --value "$unit" 2>/dev/null | head -n 1 || true)
    code=$(systemctl show -p ExecMainStatus --value "$unit" 2>/dev/null | head -n 1 || true)
  fi
  host=$(hostname 2>/dev/null || echo vps)
  line="tipping-point VPS: $unit failed on $host at $(date -u +%Y-%m-%dT%H:%MZ)"
  if [[ -n "$result" ]]; then line="$line (result=$result"; fi
  if [[ -n "$result" && -n "$code" ]]; then line="$line, exit=$code"; fi
  if [[ -n "$result" ]]; then line="$line)"; fi
  line="$line. See: journalctl -u $unit"
  printf '%s' "$line" | tr -c '[:print:]' ' ' | cut -c 1-300
}

# nt_test_text: one line, ASCII, for the test notice.
nt_test_text() {
  local host
  host=$(hostname 2>/dev/null || echo vps)
  printf '%s' "tipping-point VPS: TEST notice from $host at $(date -u +%Y-%m-%dT%H:%MZ). No unit failed; this only proves the webhook." |
    tr -c '[:print:]' ' ' | cut -c 1-300
}

# nt_throttled UNIT: true when this unit was announced less than the interval ago.
nt_throttled() {
  local stamp="$PIPELINE_STATE_DIR/notify/$1.last" last
  [[ "$NOTIFY_MIN_INTERVAL_S" =~ ^[0-9]+$ ]] || return 1
  [[ -r "$stamp" ]] || return 1
  last=$(<"$stamp")
  [[ "$last" =~ ^[0-9]+$ ]] || return 1
  (( $(date +%s) - last < NOTIFY_MIN_INTERVAL_S ))
}

nt_stamp() {
  mkdir -p "$PIPELINE_STATE_DIR/notify"
  date +%s >"$PIPELINE_STATE_DIR/notify/$1.last"
}

# nt_post TEXT: HTTPS POST of the text to NOTIFY_WEBHOOK_URL. The URL goes to
# curl through a config file, so it never shows in the process list.
nt_post() {
  local text=$1 url=${NOTIFY_WEBHOOK_URL:-} cfg rc=0
  if [[ -z "$url" ]]; then return 1; fi
  if [[ "$url" != https://* || "$url" == *[\"\\\ ]* || "$url" == *$'\n'* ]]; then
    nt_log "NOTIFY_WEBHOOK_URL is not a plain https:// URL; not sent"
    return 1
  fi
  mkdir -p "$PIPELINE_STATE_DIR/notify"
  cfg=$(mktemp "$PIPELINE_STATE_DIR/notify/curl.XXXXXXXX")
  printf 'url = "%s"\n' "$url" >"$cfg"
  printf '%s' "$text" |
    curl -fsS --max-time 20 --retry 1 --retry-delay 5 -K "$cfg" \
      -H 'Content-Type: text/plain; charset=utf-8' --data-binary @- >/dev/null || rc=$?
  rm -f "$cfg"
  if (( rc )); then
    nt_log "webhook POST failed (curl exit $rc); it is tried again at the next failure"
    return 1
  fi
}

nt_main() {
  local unit=${1:-}
  if (( $# != 1 )) || ! nt_valid_unit "$unit"; then
    echo "usage: notify.sh tipping-point-<name>.service" >&2
    exit 2
  fi
  local text
  if [[ "$unit" == "$NOTIFY_TEST_UNIT" ]]; then
    text=$(nt_test_text)
    echo "TEST: $text"
    if [[ -z "${NOTIFY_WEBHOOK_URL:-}" ]]; then
      nt_log "NOTIFY_WEBHOOK_URL is not set: nothing was sent"
      return 0
    fi
    nt_post "$text" || return 1
    nt_log "test notice sent; no throttle stamp was written or read"
    return 0
  fi
  text=$(nt_text "$unit")
  echo "FAILED: $text"
  if [[ -z "${NOTIFY_WEBHOOK_URL:-}" ]]; then return 0; fi
  if nt_throttled "$unit"; then
    nt_log "webhook skipped for $unit: announced less than ${NOTIFY_MIN_INTERVAL_S}s ago"
    return 0
  fi
  # the stamp is written only after a delivered notice
  if nt_post "$text"; then nt_stamp "$unit"; fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  nt_main "$@"
fi
