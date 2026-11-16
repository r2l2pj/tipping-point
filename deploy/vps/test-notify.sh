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

# --- 7. the test notice has its own path: it never throttles a real notice -------------------
export NOTIFY_WEBHOOK_URL="$hook"
rm -f "$PIPELINE_STATE_DIR"/notify/*.last
tn=tipping-point-notice-test.service
n "$tn"
tl_eq "$rc" 0 "test notice rc: $out"
tl_has "$out" "TEST: tipping-point VPS: TEST notice" "test notice is marked as a test"
tl_lacks "$out" "FAILED:" "a test notice is not a FAILED line"
tl_has "$(cat "$CURL_BODY")" "No unit failed" "the posted text says no unit failed"
[[ -e "$CURL_LOG" ]] || tl_fail "the test notice was not posted"
[[ ! -e "$SYSTEMCTL_LOG" || "$(grep -c "$tn" "$SYSTEMCTL_LOG" || true)" == 0 ]] || tl_fail "the test notice asked systemd about a unit"
tl_eq "$(find "$PIPELINE_STATE_DIR/notify" -name '*.last' | wc -l | tr -d ' ')" 0 "the test notice wrote no throttle stamp"
# a second test notice goes out too (no throttle), and the real notice right after it is not delayed
n "$tn"
[[ -e "$CURL_LOG" ]] || tl_fail "a repeated test notice was throttled"
n tipping-point-now.service
tl_eq "$rc" 0 "real notice after a test notice rc"
[[ -e "$CURL_LOG" ]] || tl_fail "a real notice was suppressed after a test notice"
tl_has "$(cat "$CURL_BODY")" "tipping-point-now.service failed" "the real notice text"
# a real stamp is left alone by a test notice
before=$(cat "$PIPELINE_STATE_DIR/notify/tipping-point-now.service.last")
n "$tn"
tl_eq "$(cat "$PIPELINE_STATE_DIR/notify/tipping-point-now.service.last")" "$before" "the test notice leaves a real stamp alone"
# no webhook: says so, sends nothing, still succeeds
NOTIFY_WEBHOOK_URL='' n "$tn"
tl_eq "$rc" 0 "test notice without a webhook rc"
tl_has "$out" "nothing was sent" "no webhook is reported"
[[ ! -e "$CURL_LOG" ]] || tl_fail "curl ran without a webhook URL"
# a failed POST fails the test notice, so the operator sees it
CURL_RC=7 n "$tn"
tl_eq "$rc" 1 "a failed test post fails the unit"
tl_has "$out" "webhook POST failed" "the failure is named"
# other names that look similar are real units, throttled as before
rm -f "$PIPELINE_STATE_DIR"/notify/*.last
n tipping-point-notice-test2.service
tl_has "$out" "FAILED:" "only the exact test name is a test notice"

# --- 8. on a unit-like PATH: only system tools, no uv, no private directory -----------------------
# The notify unit sets no PATH. PATH here holds symlinks to the tools the script needs (and the two
# stubs for systemctl and curl), nothing else, so a command that lives only in a private directory
# would fail here the way it would under systemd.
svc="$TL_TMP/svcbin"
mkdir -p "$svc"
for c in bash head hostname date tr cut mkdir mktemp rm cat; do
  if p=$(command -v "$c"); then ln -sf "$p" "$svc/$c"; fi
done
ln -sf "$TL_BIN/systemctl" "$svc/systemctl"
ln -sf "$TL_BIN/curl" "$svc/curl"
real_bash=$(command -v bash)
rm -f "$PIPELINE_STATE_DIR"/notify/*.last "$CURL_LOG" "$CURL_BODY"
tl_run out rc env PATH="$svc" "$real_bash" "$here/notify.sh" tipping-point-stale-alarm.service
tl_eq "$rc" 0 "a real notice on a unit-like PATH: $out"
tl_has "$out" "FAILED: tipping-point VPS: tipping-point-stale-alarm.service failed" "the real notice text on a unit-like PATH"
tl_has "$(cat "$CURL_BODY")" "result=exit-code, exit=1" "systemctl answers reach the notice on a unit-like PATH"
rm -f "$CURL_LOG" "$CURL_BODY"
tl_run out rc env PATH="$svc" "$real_bash" "$here/notify.sh" "$tn"
tl_eq "$rc" 0 "the test notice on a unit-like PATH: $out"
[[ -e "$CURL_LOG" ]] || tl_fail "the test notice was not posted on a unit-like PATH"

echo "notify tests passed"
