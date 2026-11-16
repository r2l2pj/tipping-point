#!/usr/bin/env bash
# Tests for keepalive.sh and watchdog.sh. curl and uv are stubs or file:// URLs;
# git reads a local repository. No network, no root.
# shellcheck disable=SC2154  # tl_run sets out and rc
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup

# --- keepalive: the same request as the workflow, exit 1 on failure --------------------
export CURL_LOG="$TL_TMP/curl.log"
tl_stub curl <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$CURL_LOG"
exit "${CURL_RC:-0}"
STUB
tl_run out rc bash "$here/keepalive.sh"
tl_eq "$rc" 0 "keepalive ok"
tl_has "$(cat "$CURL_LOG")" "-fsS --max-time 30 --retry 2 --retry-delay 10 -o /dev/null https://tippingpoint.l17s.dev/api/health" "same request as app-keepalive.yml"
CURL_RC=22 tl_run out rc bash "$here/keepalive.sh"
tl_eq "$rc" 1 "a failed health check fails the unit"
tl_has "$out" "keepalive: FAILED" "failure message"
APP_HEALTH_URL="http://example.invalid/x" tl_run out rc bash "$here/keepalive.sh"
tl_eq "$rc" 2 "plain http refused"
wf_line=$(grep -F 'curl -fsS' "$here/../../.github/workflows/app-keepalive.yml")
tl_has "$wf_line" "--max-time 30 --retry 2 --retry-delay 10 https://tippingpoint.l17s.dev/api/health" "workflow still has the request the script copies"

# --- watchdog: reads the manifest from the fallback branch, runs the checker -----------
origin="$TL_TMP/origin.git"
tl_make_origin "$origin"
export PIPELINE_STATE_DIR="$TL_TMP/state" PIPELINE_CHECKOUT="$TL_TMP/checkout"
export PIPELINE_REPO_URL="file://$origin"
mkdir -p "$PIPELINE_STATE_DIR" "$PIPELINE_CHECKOUT"
export UV_LOG="$TL_TMP/uv.log"
tl_stub uv <<'STUB'
#!/usr/bin/env bash
# stands for: uv run --no-project --python 3.12 python -m live.staleness --max-age-s N FILE
echo "$*" >>"$UV_LOG"
file=${*: -1}
cp "$file" "$UV_LOG.manifest"
exit "${UV_RC:-0}"
STUB
tl_run out rc bash "$here/watchdog.sh"
tl_eq "$rc" 0 "watchdog ok: $out"
log=$(cat "$UV_LOG")
tl_has "$log" "run --no-project --python 3.12 python -m live.staleness --max-age-s 21600" "same checker, 6 h default"
tl_eq "$(cat "$UV_LOG.manifest")" '{"updated_at": "2026-11-15T00:00:00Z"}' "the manifest of the fallback branch reaches the checker"
[[ -z "$(find "$PIPELINE_STATE_DIR" -maxdepth 1 -name 'watchdog-manifest.*')" ]] || tl_fail "temporary manifest left behind"
tl_run out rc bash "$here/watchdog.sh" 7200
tl_has "$(tail -n 1 "$UV_LOG")" "--max-age-s 7200" "age limit argument"
UV_RC=1 tl_run out rc bash "$here/watchdog.sh"
tl_eq "$rc" 1 "a stale manifest fails the unit"
tl_has "$out" "watchdog: STALE" "stale message"
tl_run out rc bash "$here/watchdog.sh" abc
tl_eq "$rc" 2 "bad age refused"
# the fallback branch is unreachable or has no manifest: that is stale too
mkdir -p "$TL_TMP/state2"
PIPELINE_REPO_URL="file://$TL_TMP/missing.git" PIPELINE_STATE_DIR="$TL_TMP/state2" tl_run out rc bash "$here/watchdog.sh"
tl_eq "$rc" 1 "unreachable fallback is reported"
tl_has "$out" "STALE: cannot read" "unreachable message"
# the scratch repository refuses a foreign origin
git -C "$PIPELINE_STATE_DIR/watchdog.git" config remote.origin.url "file://$TL_TMP/other.git"
tl_run out rc bash "$here/watchdog.sh"
tl_eq "$rc" 1 "scratch repository with another origin refused"
# it never touches the pipeline checkout
[[ -z "$(find "$PIPELINE_CHECKOUT" -mindepth 1 | head -n 1)" ]] || tl_fail "watchdog wrote into the pipeline checkout"

echo "keepalive and watchdog tests passed"
