#!/usr/bin/env bash
# Tests for notify.sh: journal line always, optional webhook only when set,
# https only, one notice per unit per interval, refusal of foreign units.
# systemctl and curl are stubs. No network, no root.
# shellcheck disable=SC2154  # tl_run sets out and rc
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup

export PIPELINE_STATE_DIR="$TL_TMP/state"
mkdir -p "$PIPELINE_STATE_DIR"
export CURL_LOG="$TL_TMP/curl.log" CURL_BODY="$TL_TMP/curl.body" SYSTEMCTL_LOG="$TL_TMP/systemctl.log"
unset NOTIFY_WEBHOOK_URL NOTIFY_MIN_INTERVAL_S CURL_RC || true

tl_stub systemctl <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$SYSTEMCTL_LOG"
case "$*" in
  "show -p Result --value "*) echo exit-code ;;
  "show -p ExecMainStatus --value "*) echo 1 ;;
  *) echo "unexpected systemctl call: $*" >&2; exit 99 ;;
esac
STUB
tl_stub curl <<'STUB'
#!/usr/bin/env bash
echo "curl $*" >>"$CURL_LOG"
while (( $# > 0 )); do
  if [[ "$1" == -K ]]; then
    shift
    { echo "config:"; cat "$1"; } >>"$CURL_LOG"
  fi
  shift
done
cat >"$CURL_BODY"
exit "${CURL_RC:-0}"
STUB
n() { rm -f "$CURL_LOG" "$CURL_BODY"; tl_run out rc bash "$here/notify.sh" "$@"; }

# --- 1. only our own service units -----------------------------------------------------
for bad in "" docker.service tipping-point-now tipping-point-.service "tipping-point-Now.service" \
  "tipping-point-now.timer" "../tipping-point-now.service" "tipping-point-a_b.service"; do
  n "$bad"
  tl_eq "$rc" 2 "refused unit name '$bad'"
done
tl_run out rc bash "$here/notify.sh" tipping-point-now.service extra
tl_eq "$rc" 2 "extra argument refused"
tl_run out rc bash "$here/notify.sh"
tl_eq "$rc" 2 "no argument refused"
[[ ! -e "$SYSTEMCTL_LOG" && ! -e "$CURL_LOG" ]] || tl_fail "a refused call ran a command"

# --- 2. no webhook: the journal line only, no network --------------------------------------
export NOTIFY_WEBHOOK_URL=
n tipping-point-now.service
tl_eq "$rc" 0 "no webhook rc"
tl_has "$out" "FAILED: tipping-point VPS: tipping-point-now.service failed on" "journal line"
tl_has "$out" "result=exit-code, exit=1" "result and exit code from systemd"
tl_has "$out" "journalctl -u tipping-point-now.service" "pointer to the logs"
[[ ! -e "$CURL_LOG" ]] || tl_fail "curl ran without a webhook URL"
case "$out" in *[![:print:][:space:]]*) tl_fail "non-ASCII output" ;; *) ;; esac

# --- 3. webhook: one short POST, URL not on the command line ---------------------------------
hook="https://hooks.example.invalid/T000/secret-token"
export NOTIFY_WEBHOOK_URL="$hook"
n tipping-point-now.service
tl_eq "$rc" 0 "webhook rc: $out"
log=$(cat "$CURL_LOG")
tl_has "$log" "--data-binary @-" "body from stdin"
tl_has "$log" "--max-time 20" "time limit"
tl_has "$log" "Content-Type: text/plain" "plain text"
tl_has "$log" "url = \"$hook\"" "URL given through the config file"
if grep '^curl ' "$CURL_LOG" | grep -q -F "secret-token"; then tl_fail "URL on the command line"; fi
body=$(cat "$CURL_BODY")
tl_has "$body" "tipping-point-now.service failed" "body text"
[[ ${#body} -le 300 ]] || tl_fail "body longer than 300 characters"
if find "$PIPELINE_STATE_DIR/notify" -name 'curl.*' | grep -q .; then tl_fail "curl config file left behind"; fi
[[ -s "$PIPELINE_STATE_DIR/notify/tipping-point-now.service.last" ]] || tl_fail "no throttle stamp after a delivered notice"

# --- 4. https only, no odd characters --------------------------------------------------------
for badurl in "http://hooks.example.invalid/x" "ftp://x" 'https://a.example/"x' "https://a.example/x y" 'https://a.example/\x'; do
  export NOTIFY_WEBHOOK_URL="$badurl"
  rm -f "$PIPELINE_STATE_DIR"/notify/*.last
  n tipping-point-now.service
  tl_eq "$rc" 0 "bad URL is not an error"
  tl_has "$out" "not a plain https:// URL" "bad URL message for $badurl"
  [[ ! -e "$CURL_LOG" ]] || tl_fail "curl ran for $badurl"
done
export NOTIFY_WEBHOOK_URL="$hook"

# --- 5. one notice per unit per interval ----------------------------------------------------
rm -f "$PIPELINE_STATE_DIR"/notify/*.last
n tipping-point-app-keepalive.service
[[ -e "$CURL_LOG" ]] || tl_fail "first notice not sent"
n tipping-point-app-keepalive.service
tl_has "$out" "webhook skipped" "repeat is throttled"
[[ ! -e "$CURL_LOG" ]] || tl_fail "repeat notice sent"
n tipping-point-stale-alarm.service
[[ -e "$CURL_LOG" ]] || tl_fail "another unit was throttled"
echo $(( $(date +%s) - 4000 )) >"$PIPELINE_STATE_DIR/notify/tipping-point-app-keepalive.service.last"
n tipping-point-app-keepalive.service
[[ -e "$CURL_LOG" ]] || tl_fail "an old stamp still throttles"
NOTIFY_MIN_INTERVAL_S=0 n tipping-point-app-keepalive.service
[[ -e "$CURL_LOG" ]] || tl_fail "interval 0 still throttles"
echo garbage >"$PIPELINE_STATE_DIR/notify/tipping-point-app-keepalive.service.last"
n tipping-point-app-keepalive.service
[[ -e "$CURL_LOG" ]] || tl_fail "a damaged stamp blocks notices"

# --- 6. a failed delivery is tried again at the next failure ------------------------------------
rm -f "$PIPELINE_STATE_DIR"/notify/*.last
CURL_RC=7 n tipping-point-now.service
tl_eq "$rc" 0 "a failing webhook does not fail the notifier"
tl_has "$out" "webhook POST failed" "delivery failure logged"
[[ ! -e "$PIPELINE_STATE_DIR/notify/tipping-point-now.service.last" ]] || tl_fail "stamp written for a failed delivery"
n tipping-point-now.service
[[ -e "$CURL_LOG" ]] || tl_fail "no retry after a failed delivery"

echo "notify tests passed"
