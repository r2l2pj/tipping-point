#!/usr/bin/env bash
# Tests for pipeline-run.sh: refusals, the single-writer guard, publishing off by
# default, locking, the order and failure rules of the Now job, state that
# survives a fresh checkout, the push credential (token default, ssh alternative)
# and where it is visible. uv, ssh, gh are stubs; git talks to a local
# repository. No Docker, no network, no root.
# shellcheck disable=SC2154,SC2329,SC2031  # tl_run sets out and rc; stubs are called by the sourced code; env prefixes
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup

origin="$TL_TMP/origin.git"
tl_make_origin "$origin"
export PIPELINE_REPO_URL="file://$origin"
export PIPELINE_PUSH_URL="file://$origin"
export PIPELINE_CHECKOUT="$TL_TMP/checkout"
export PIPELINE_STATE_DIR="$TL_TMP/state"
export PIPELINE_MIN_FREE_MB=1
export PIPELINE_RAW_BASE="file://$TL_TMP/raw"
export GITHUB_REPOSITORY=o/r
export PIPELINE_GITHUB_API="file://$TL_TMP/api"
export UV_LOG="$TL_TMP/uv.log"
tok="ghp_TESTSECRET0123456789abcdef"
fx="$TL_TMP/api/repos/o/r/actions/workflows"
mkdir -p "$fx" "$PIPELINE_STATE_DIR"
set_wf() { printf '{\n  "state": "%s"\n}\n' "$2" >"$fx/$1.yml"; }
unset PIPELINE_PUBLISH GCP_SA_KEY_B64 GCP_SA_KEY GOOGLE_APPLICATION_CREDENTIALS NOW_P_TRIGGER UV_FAIL_ON \
  UV_WRITE_DATA STATIC_FAIL GH_TOKEN FALLBACK_KEEP FALLBACK_ALLOW_DROP PIPELINE_LOCK_WAIT_S PIPELINE_PUSH_AUTH || true

tl_stub uv <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == --version ]]; then echo "uv 0.13.8"; exit 0; fi
prev=no; dswx=no
[[ -f data/now/prev_classes.npz ]] && prev=yes
[[ -f data/now/response/dswx_state.npz ]] && dswx=yes
printf '%s | KEEP=%s DROP=%s GCP=%s CRED=%s NOWP=%s PREV=%s DSWX=%s TOK=%s CFG=%s\n' "$*" "${FALLBACK_KEEP-unset}" \
  "${FALLBACK_ALLOW_DROP-unset}" "${GCP_SA_KEY:+set}" "${GOOGLE_APPLICATION_CREDENTIALS:+set}" \
  "${NOW_P_TRIGGER-unset}" "$prev" "$dswx" "${GH_TOKEN:+set}" "${GIT_CONFIG_COUNT-unset}" >>"$UV_LOG"
# one line per call (some arguments span lines): the step kind, the token and the git settings
kind=step; [[ "$*" == *live/publish_fallback.py* ]] && kind=publish
printf '%s TOK=%s CFG=%s\n' "$kind" "${GH_TOKEN:+set}" "${GIT_CONFIG_COUNT-unset}" >>"$UV_LOG.env"
if [[ -n "${UV_FAIL_ON:-}" && "$*" == *"$UV_FAIL_ON"* ]]; then echo "uv stub: failing $*" >&2; exit 1; fi
zip() { python3 -c 'import sys, zipfile; z = zipfile.ZipFile(sys.argv[1], "w"); z.writestr("a", "1"); z.close()' "$1"; }
case "$*" in
  *"live/jobs.py now"*) mkdir -p data/now && zip data/now/prev_classes.npz ;;
  *13_response_dswx.py*) mkdir -p data/now/response && zip data/now/response/dswx_state.npz ;;
  *"live/jobs.py nisar"* | *"live/jobs.py validation"* | *"live/jobs.py reference"*)
    if [[ -n "${UV_WRITE_DATA:-}" ]]; then mkdir -p data && echo '{}' >data/job_output.json; fi
    ;;
esac
exit 0
EOF
tl_stub ssh <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
tl_stub gh <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
real_git=$(command -v git)
# a spy in front of git: logs every call (arguments and the credential settings in
# the environment) and can fail a push or a fetch of the fallback branch
tl_stub git-spy <<EOF
#!/usr/bin/env bash
printf 'ARGV %s\n' "\$*" >>"\$GIT_SPY_LOG"
printf 'ENV GIT_CONFIG_COUNT=%s KEY0=%s VAL0=%s KEY1=%s VAL1=%s SSH=%s GH_TOKEN=%s\n' "\${GIT_CONFIG_COUNT-unset}" \
  "\${GIT_CONFIG_KEY_0-}" "\${GIT_CONFIG_VALUE_0-}" "\${GIT_CONFIG_KEY_1-}" "\${GIT_CONFIG_VALUE_1-}" \
  "\${GIT_SSH_COMMAND:+set}" "\${GH_TOKEN:+set}" >>"\$GIT_SPY_LOG"
if [[ -n "\${GIT_FAIL_PUSH:-}" && "\$*" == *push* ]]; then
  echo "remote: invalid credentials \${PL_PUSH_TOKEN-none}" >&2
  exit 128
fi
if [[ -n "\${GIT_FAIL_FALLBACK_FETCH:-}" && ( "\$*" == *fetch*fallback* || "\$*" == *ls-remote* ) ]]; then
  echo "git spy: simulated network failure" >&2
  exit 128
fi
exec "$real_git" "\$@"
EOF
spy_on() { cp "$TL_BIN/git-spy" "$TL_BIN/git"; hash -r; }
spy_off() { rm -f "$TL_BIN/git"; hash -r; }
export GIT_SPY_LOG="$TL_TMP/git-spy.log"
# origin_sum: a checksum of every file of the origin repository (a dry run must change none)
origin_sum() { (cd "$origin" && find . -type f | LC_ALL=C sort | xargs cksum | cksum); }

# --- 1. argument and privilege refusals -----------------------------------------------
tl_run out rc bash "$here/pipeline-run.sh"
tl_eq "$rc" 2 "no job"
tl_run out rc bash "$here/pipeline-run.sh" bogus
tl_eq "$rc" 2 "unknown job"
tl_run out rc bash "$here/pipeline-run.sh" --bogus now
tl_eq "$rc" 2 "unknown option"
tl_run out rc bash "$here/pipeline-run.sh" --if-stale now
tl_eq "$rc" 2 "--if-stale without a number"
tl_run out rc bash "$here/pipeline-run.sh" now nisar
tl_eq "$rc" 2 "two jobs"
# shellcheck source=deploy/vps/pipeline-run.sh
source "$here/pipeline-run.sh"
trap - ERR
set +E
tl_run out rc run_check_not_root 0
tl_eq "$rc" 1 "root is refused"
tl_has "$out" "refusing to run as root" "root refusal message"
tl_run out rc run_check_not_root 1000
tl_eq "$rc" 0 "a normal user is accepted"
tl_eq "$(run_job_timeout_s now)" 2700 "now timeout is the workflow's 45 min"
tl_eq "$(run_job_timeout_s nisar)" 3600 "nisar timeout"
tl_eq "$(run_job_timeout_s validation)" 3600 "validation timeout"
tl_eq "$(run_job_timeout_s reference)" 3600 "reference timeout"

# --- 2. preflight: setup problems are refused before anything runs --------------------
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "no checkout yet"
tl_has "$out" "not a git checkout" "missing checkout message"
bash "$here/pipeline-lib.sh" init-checkout "$PIPELINE_CHECKOUT" >/dev/null 2>&1
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "preflight without a push token"
tl_has "$out" "push check: FAIL (auth=token): GH_TOKEN is empty" "missing token message"
before_sum=$(origin_sum)
GH_TOKEN=$tok tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "preflight ok: $out"
tl_has "$out" "publishing is OFF" "publishing is off by default"
tl_has "$out" "push check: PASS (auth=token)" "push check passes"
tl_has "$out" "nothing was written" "push check says it wrote nothing"
tl_eq "$(origin_sum)" "$before_sum" "the push check changed nothing in origin"
[[ ! -e "$UV_LOG" ]] || tl_fail "preflight ran a job step"
# the pipeline's own uv directory comes first on PATH, and only for the runner
mkdir -p "$TL_TMP/own-uv"
printf '#!/usr/bin/env bash\necho "uv 9.9.9"\n' >"$TL_TMP/own-uv/uv"
chmod +x "$TL_TMP/own-uv/uv"
PIPELINE_UV_DIR="$TL_TMP/own-uv" GH_TOKEN=$tok tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "preflight with the pipeline's own uv: $out"
tl_has "$out" "uv is 'uv 9.9.9'" "the uv in the pipeline directory was used"
PIPELINE_UV_DIR="$TL_TMP/no-such-dir" GH_TOKEN=$tok tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "preflight without the pipeline's uv directory: $out"
tl_lacks "$out" "uv 9.9.9" "a missing directory leaves PATH alone"
PIPELINE_STATE_DIR="$TL_TMP/no-state" tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "missing state dir refused"
PIPELINE_MIN_FREE_MB=99999999 tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "low disk refused"
tl_has "$out" "MB free" "disk message"
# a shell that is not our checkout is refused (cannot be reset by mistake)
mkdir -p "$TL_TMP/foreign"
git init -q "$TL_TMP/foreign"
PIPELINE_CHECKOUT="$TL_TMP/foreign" tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "foreign repository refused"
tl_has "$out" "expected $PIPELINE_REPO_URL" "foreign repository message"

# --- 3. the single-writer guard: publishing needs everything in place -----------------
export PIPELINE_PUBLISH=1
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "publish on without a token"
tl_has "$out" "GH_TOKEN is empty" "token message"
export GH_TOKEN=$tok
set_wf now active
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "publish on while the GitHub workflow is active"
tl_has "$out" "still active" "single-writer message"
set_wf now disabled_manually
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "publish on with the workflow disabled: $out"
tl_has "$out" "push check: PASS" "push check before a publishing run"
tl_lacks "$out" "WARNING: gh or GH_TOKEN is missing" "token and gh present: no release asset warning"
rm -f "$fx/now.yml"
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "unknown workflow state only warns"
tl_has "$out" "could not read the state" "unknown state warning"
set_wf now disabled_manually
# a stored push URL would push twice (to it and to the runner's): refused
git -C "$PIPELINE_CHECKOUT" config remote.origin.pushurl "git@example.invalid:x/y.git"
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "a stored push URL is refused"
tl_has "$out" "remote.origin.pushurl" "stored push URL message"
git -C "$PIPELINE_CHECKOUT" config --unset-all remote.origin.pushurl
set_wf nisar active
tl_run out rc bash "$here/pipeline-run.sh" --preflight nisar
tl_eq "$rc" 1 "each job checks its own workflow"
set_wf nisar disabled_manually
# a bad auth mode is refused
PIPELINE_PUSH_AUTH=password tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "unknown push mode refused"
tl_has "$out" "PIPELINE_PUSH_AUTH must be token or ssh" "push mode message"

# 3b. the push check fails clearly, and the output never shows the token
spy_on
rm -f "$GIT_SPY_LOG"
GIT_FAIL_PUSH=1 tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "a rejected push fails the preflight"
tl_has "$out" "push check: FAIL (auth=token): git push --dry-run failed (exit 128)" "FAIL line"
tl_lacks "$out" "$tok" "the token is masked in the failure output"
tl_has "$out" "invalid credentials ***" "masked token in the git message"
# the fetch of fallback fails and the branch cannot be shown absent: FAIL, not PASS
GIT_FAIL_FALLBACK_FETCH=1 tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "an unreachable origin fails the preflight"
tl_has "$out" "cannot fetch origin/fallback and cannot tell" "unreachable origin message"
spy_off

# 3c. the token is nowhere but the environment of the push: not in the output,
# a command line, .git/config, the state dir, or the URL
spy_on
rm -f "$GIT_SPY_LOG"
before_sum=$(origin_sum)
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "preflight with the spy: $out"
tl_lacks "$out" "$tok" "the token is not in the output"
spy_log=$(cat "$GIT_SPY_LOG")
tl_lacks "$spy_log" "$tok" "the token is on no git command line and not in its credential settings"
tl_has "$spy_log" "ARGV push --dry-run --force origin refs/remotes/origin/fallback:refs/heads/fallback" "the dry run pushes the current fallback ref"
tl_has "$spy_log" "KEY1=credential.https://github.com.helper" "the credential helper is set for github.com only"
tl_lacks "$spy_log" "KEY0=remote.origin.pushurl" "token mode adds no push URL: origin's own HTTPS URL is used"
tl_eq "$(origin_sum)" "$before_sum" "origin unchanged by the dry run"
if grep -r -F -- "$tok" "$PIPELINE_CHECKOUT/.git" "$PIPELINE_STATE_DIR" >/dev/null 2>&1; then tl_fail "the token is stored on disk"; fi
if grep -F -- "$tok" "$PIPELINE_CHECKOUT/.git/config" >/dev/null; then tl_fail "the token is in .git/config"; fi
spy_off
# the credential helper really delivers the token to github.com, and to no other host
PL_PUSH_TOKEN=$tok PIPELINE_PUSH_AUTH=token tl_run out rc run_with_push_auth bash -c \
  "printf 'protocol=https\nhost=github.com\npath=r2l2pj/tipping-point.git\n\n' | git credential fill"
tl_eq "$rc" 0 "git credential fill for github.com: $out"
tl_has "$out" "username=x-access-token" "helper user name"
tl_has "$out" "password=$tok" "helper password"
PL_PUSH_TOKEN=$tok PIPELINE_PUSH_AUTH=token GIT_TERMINAL_PROMPT=0 tl_run out rc run_with_push_auth bash -c \
  "printf 'protocol=https\nhost=example.invalid\n\n' | git credential fill"
tl_eq "$rc" 128 "no credential for another host"
tl_lacks "$out" "$tok" "the token is not offered to another host"
# ssh: the deploy key alternative uses the SSH push URL and the pinned host key
# shellcheck disable=SC2016  # the text expands in the inner shell
GH_TOKEN="" PL_PUSH_TOKEN="" PIPELINE_PUSH_AUTH=ssh tl_run out rc run_with_push_auth bash -c \
  'echo "$GIT_CONFIG_KEY_0=$GIT_CONFIG_VALUE_0"; echo "ssh=$GIT_SSH_COMMAND"; echo "gh=${GH_TOKEN:-none}"'
tl_has "$out" "remote.origin.pushurl=$PIPELINE_PUSH_URL" "ssh mode: push URL for this run only"
tl_has "$out" "UserKnownHostsFile=$PIPELINE_STATE_DIR/ssh/known_hosts" "ssh mode: pinned host keys"
tl_has "$out" "gh=none" "ssh mode without a token: no GH_TOKEN"

# 3d. ssh mode: the deploy key and the pinned host key are checked first
export PIPELINE_PUSH_AUTH=ssh
unset GH_TOKEN
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "ssh mode without a deploy key"
tl_has "$out" "deploy key" "deploy key message"
mkdir -p "$PIPELINE_STATE_DIR/ssh"
echo key >"$PIPELINE_STATE_DIR/ssh/id_ed25519"
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 1 "ssh mode without pinned host keys"
tl_has "$out" "known_hosts" "known_hosts message"
echo "github.com ssh-ed25519 AAAA" >"$PIPELINE_STATE_DIR/ssh/known_hosts"
tl_run out rc bash "$here/pipeline-run.sh" --preflight now
tl_eq "$rc" 0 "ssh mode with key and host keys: $out"
tl_has "$out" "push check: PASS (auth=ssh)" "ssh push check"
tl_has "$out" "WARNING: gh or GH_TOKEN is missing" "release asset warning in ssh mode without a token"
export PIPELINE_PUSH_AUTH=token # the sourced runner reads this shell's variable
unset GH_TOKEN
export PIPELINE_PUBLISH=0

# --- 4. lock, timeout and status through the real entry point -------------------------
rm -f "$UV_LOG" "$UV_LOG.env"
UV_FAIL_ON="sync --frozen" tl_run out rc bash "$here/pipeline-run.sh" now
tl_eq "$rc" 1 "a failing step fails the job"
tl_has "$out" "step: checkout-update" "checkout updated under the lock"
tl_eq "$(git -C "$PIPELINE_CHECKOUT" rev-parse HEAD)" "$(git -C "$origin" rev-parse main)" "checkout is at main"
status="$PIPELINE_STATE_DIR/status"
tl_has "$(cat "$status/now.last_result")" "failed (exit 1) at step: uv-sync" "failure recorded with its step"
[[ ! -e "$status/now.last_success" ]] || tl_fail "a failed run recorded a success"
[[ -e "$status/now.last_attempt" ]] || tl_fail "attempt not recorded"
# a held lock: no overlap
mkdir -p "$TL_TMP/hold"
( exec 9>"$PIPELINE_STATE_DIR/pipeline.lock"; flock 9; : >"$TL_TMP/hold/held"; exec sleep 30 ) &
holder=$!
for _ in $(seq 1 50); do [[ -e "$TL_TMP/hold/held" ]] && break; sleep 0.1; done
[[ -e "$TL_TMP/hold/held" ]] || tl_fail "lock holder did not start"
rm -f "$UV_LOG" "$UV_LOG.env"
PIPELINE_LOCK_WAIT_S=0 tl_run out rc bash "$here/pipeline-run.sh" now
tl_eq "$rc" 75 "no overlap: busy exit code"
tl_has "$out" "another pipeline job is running" "busy message"
PIPELINE_LOCK_WAIT_S=1 tl_run out rc bash "$here/pipeline-run.sh" nisar
tl_eq "$rc" 75 "waiting for the lock times out"
tl_has "$out" "still holds the lock" "wait timeout message"
tl_run out rc bash "$here/pipeline-run.sh" --if-stale 12600 now
tl_eq "$rc" 0 "backup run never queues behind a running job"
tl_has "$out" "another pipeline job is running; nothing to do" "backup busy message"
[[ ! -e "$UV_LOG" ]] || tl_fail "a job step ran while the lock was held"
kill "$holder" 2>/dev/null || true
wait "$holder" 2>/dev/null || true
# --if-stale: fresh is a no-op, stale runs
date +%s >"$status/now.last_success"
tl_run out rc bash "$here/pipeline-run.sh" --if-stale 12600 now
tl_eq "$rc" 0 "fresh success: backup does nothing"
tl_has "$out" "nothing to do" "fresh message"
[[ ! -e "$UV_LOG" ]] || tl_fail "backup ran although the last success was fresh"
echo $(( $(date +%s) - 20000 )) >"$status/now.last_success"
UV_FAIL_ON="sync --frozen" tl_run out rc bash "$here/pipeline-run.sh" --if-stale 12600 now
tl_eq "$rc" 1 "stale success: backup runs the job"
tl_has "$out" "step: uv-sync" "backup started the job"
echo garbage >"$status/now.last_success"
UV_FAIL_ON="sync --frozen" tl_run out rc bash "$here/pipeline-run.sh" --if-stale 12600 now
tl_eq "$rc" 1 "an unreadable stamp counts as stale"
rm -f "$status/now.last_success" "$UV_LOG"

# --- 5. the Now job, step by step (run_locked, in this shell) -------------------------
cd "$TL_TMP"
pl_static_inputs() { echo "static-inputs $*" >>"$UV_LOG"; [[ -z "${STATIC_FAIL:-}" ]]; }
# run_job JOB: run_locked the way the program runs it, with errexit on.
run_job() { tl_rune out rc run_locked "$@"; }
cache="$PIPELINE_STATE_DIR/cache"

# 5a. publishing off: the whole job runs, the publish step is --no-push
rm -rf "$cache"
rm -f "$UV_LOG" "$UV_LOG.env"
run_job now
tl_eq "$rc" 0 "now job rc: $out"
tl_order "$UV_LOG" "sync --frozen" "check_earthdata_token" "static-inputs response" \
  "run python live/jobs.py now" "13_response_dswx.py" "14_response_nisar.py" "15_response_summary.py" \
  "16_response_dist.py" "live.nisar_next" "live.nisar_stack" "live.places" "live.now_manifest" \
  "tools/build_manifest.py" "live.now_history" "tools/build_manifest.py" "tools/build_sqlite.py" \
  "live/publish_fallback.py --no-push"
tl_eq "$(tl_count "$UV_LOG" "static-inputs p-trigger")" 0 "p_trigger is off by default"
pub=$(grep -F "live/publish_fallback.py" "$UV_LOG")
tl_has "$pub" "DROP=data/now/response/flood_nisar.geojson" "the Now job allows the R2 drop"
tl_has "$pub" "KEEP=unset" "the Now job replaces data/now"
tl_has "$(grep -F 'live/jobs.py now' "$UV_LOG")" "PREV=no DSWX=no" "first run has no saved state"
[[ -s "$cache/dswx-state/data/now/response/dswx_state.npz" ]] || tl_fail "DSWx state not saved"
[[ -s "$cache/prev-classes-v2/data/now/prev_classes.npz" ]] || tl_fail "alert classes not saved"
[[ -s "$PIPELINE_CHECKOUT/data/now/history/run1/summary.json" ]] || tl_fail "history not restored from fallback"
tl_eq "$(cat "$status/now.last_result")" ok "status ok"
[[ -s "$status/now.last_success" ]] || tl_fail "success not recorded"
[[ "$(tl_count "$UV_LOG" "live/publish_fallback.py")" == 1 ]] || tl_fail "publish ran more than once"

# 5b. second run: the saved state survives the fresh checkout
rm -f "$UV_LOG" "$UV_LOG.env"
run_job now
tl_eq "$rc" 0 "second now job rc: $out"
tl_has "$(grep -F 'live/jobs.py now' "$UV_LOG")" "PREV=yes DSWX=yes" "state restored after the checkout was reset"

# 5c. publishing on: the guard is checked again right before the push
PIPELINE_PUBLISH=1
set_wf now active
rm -f "$UV_LOG" "$UV_LOG.env"
run_job now
tl_eq "$rc" 1 "publish refused while the workflow is active"
tl_has "$out" "still active" "single-writer message at publish time"
tl_eq "$(tl_count "$UV_LOG" "live/publish_fallback.py")" 0 "nothing published"
set_wf now disabled_manually
rm -f "$UV_LOG" "$UV_LOG.env"
GH_TOKEN=$tok run_job now
tl_eq "$rc" 0 "publish on rc: $out"
pub=$(grep -F "live/publish_fallback.py" "$UV_LOG")
tl_lacks "$pub" "--no-push" "publishing on pushes"
# the push token reaches the publish step and no other step
tl_eq "$(grep -c '^publish TOK=set CFG=2$' "$UV_LOG.env" || true)" 1 "the publish step gets the token and the credential helper"
tl_eq "$(grep -c '^step TOK= CFG=unset$' "$UV_LOG.env" || true)" "$(grep -c '^step ' "$UV_LOG.env")" "no other step sees the token or git settings"
tl_lacks "$(cat "$UV_LOG")" "$tok" "the token value is on no command line"
tl_lacks "$out" "$tok" "the token is not in the job output"
# publishing off: not even the publish step sees a token
PIPELINE_PUBLISH=0
rm -f "$UV_LOG" "$UV_LOG.env"
GH_TOKEN=$tok run_job now
tl_eq "$rc" 0 "no-push run rc: $out"
tl_eq "$(grep -c -v ' TOK= CFG=unset$' "$UV_LOG.env" || true)" 0 "publishing off: no step sees the token"

# 5d. a best-effort step may fail; the job and the publish go on
rm -f "$UV_LOG" "$UV_LOG.env"
UV_FAIL_ON="14_response_nisar.py" run_job now
tl_eq "$rc" 0 "best-effort failure does not fail the job"
tl_has "$out" "WARNING: step r2-nisar-flood failed" "warning logged"
tl_has "$(cat "$status/now.last_result")" "best-effort steps failed: r2-nisar-flood" "degraded status"
tl_eq "$(tl_count "$UV_LOG" "live/publish_fallback.py")" 1 "publish still ran"

# 5e. a required step fails: nothing after it runs, nothing is published, saved state stays
before=$(cksum <"$cache/prev-classes-v2/data/now/prev_classes.npz")
rm -f "$UV_LOG" "$UV_LOG.env"
UV_FAIL_ON="live/jobs.py now" run_job now
tl_eq "$rc" 1 "now job failure fails the run"
tl_eq "$(tl_count "$UV_LOG" "13_response_dswx.py")" 0 "later steps skipped"
tl_eq "$(tl_count "$UV_LOG" "live/publish_fallback.py")" 0 "failed run published nothing"
tl_has "$(cat "$status/now.last_result")" "failed (exit 1) at step: now-job" "failed step recorded"
tl_eq "$(cksum <"$cache/prev-classes-v2/data/now/prev_classes.npz")" "$before" "saved alert baseline untouched by a failed run"
[[ -s "$cache/dswx-state/data/now/response/dswx_state.npz" ]] || tl_fail "DSWx state lost by a failed run"

# 5f. static inputs failing stops the job before the model runs
rm -f "$UV_LOG" "$UV_LOG.env"
STATIC_FAIL=1 run_job now
tl_eq "$rc" 1 "static input failure fails the run"
tl_eq "$(tl_count "$UV_LOG" "live/jobs.py now")" 0 "job did not run without its inputs"
tl_has "$(cat "$status/now.last_result")" "static-inputs" "failed step recorded"

# 5g. P(trigger) step only when NOW_P_TRIGGER=1, and it is best-effort
rm -f "$UV_LOG" "$UV_LOG.env"
NOW_P_TRIGGER=1 UV_FAIL_ON="live.p_trigger" run_job now
tl_eq "$rc" 0 "p_trigger failure does not fail the job"
tl_order "$UV_LOG" "16_response_dist.py" "static-inputs p-trigger" "live.p_trigger" "live.nisar_next"
tl_has "$(grep -F 'live.p_trigger' "$UV_LOG")" "NOWP=1" "step sees NOW_P_TRIGGER=1"
tl_has "$(grep -F 'live.now_manifest' "$UV_LOG")" "NOWP=1" "manifest step sees the variable"

# 5h. GCP key: decoded to a private file for the run, gone afterwards
key='{"client_email": "sa@example.invalid", "private_key": "x"}'
rm -f "$UV_LOG" "$UV_LOG.env"
GCP_SA_KEY_B64=$(printf '%s' "$key" | base64 | tr -d '\n') run_job now
tl_eq "$rc" 0 "key run rc: $out"
tl_has "$(grep -F 'live/jobs.py now' "$UV_LOG")" "GCP=set CRED=set" "steps see the key and the file"
if ls "$PIPELINE_STATE_DIR"/tmp/sa.* >/dev/null 2>&1; then tl_fail "key file left behind"; fi
rm -f "$UV_LOG" "$UV_LOG.env"
run_job now
tl_has "$(grep -F 'live/jobs.py now' "$UV_LOG")" "GCP= CRED=" "no key: nothing exported"
tl_has "$out" "GCP_SA_KEY_B64 is not set" "missing key noticed"
GCP_SA_KEY_B64="not base64 at all %%%" run_job now
tl_eq "$rc" 1 "a bad key stops the run"
tl_has "$out" "not base64 of a JSON key" "bad key message"

# 5i. the other three jobs publish only when data/ changed, keep fallback's data/now
rm -f "$UV_LOG" "$UV_LOG.env"
run_job nisar
tl_eq "$rc" 0 "nisar without output rc: $out"
tl_has "$out" "nothing to publish" "no data, no publish"
tl_eq "$(tl_count "$UV_LOG" "live/publish_fallback.py")" 0 "published without changes"
for job in nisar validation reference; do
  rm -f "$UV_LOG" "$UV_LOG.env"
  UV_WRITE_DATA=1 run_job "$job"
  tl_eq "$rc" 0 "$job with output rc: $out"
  tl_has "$(cat "$UV_LOG")" "live/jobs.py $job" "$job job ran"
  pub=$(grep -F "live/publish_fallback.py" "$UV_LOG")
  tl_has "$pub" "--no-push" "$job: publishing off"
  tl_has "$pub" "KEEP=now" "$job keeps the live Now outputs"
  tl_has "$pub" "DROP=unset" "$job has no drop exception"
done

# 5k. the publish guard is checked again right before the push, for every job
PIPELINE_PUBLISH=1
for job in nisar validation reference; do
  set_wf "$job" active
  rm -f "$UV_LOG" "$UV_LOG.env"
  GH_TOKEN=$tok UV_WRITE_DATA=1 run_job "$job"
  tl_eq "$rc" 1 "$job: publish refused while its workflow is active"
  tl_has "$out" "still active" "$job: single-writer message at publish time"
  tl_eq "$(tl_count "$UV_LOG" "live/publish_fallback.py")" 0 "$job: nothing published"
  set_wf "$job" disabled_manually
  rm -f "$UV_LOG" "$UV_LOG.env"
  GH_TOKEN=$tok UV_WRITE_DATA=1 run_job "$job"
  tl_eq "$rc" 0 "$job: publish with the workflow disabled: $out"
  tl_eq "$(grep -c '^publish TOK=set CFG=2$' "$UV_LOG.env" || true)" 1 "$job: the publish step gets the token"
done
PIPELINE_PUBLISH=0

# 5l. a restore from fallback that fails stops the steps that need it: no frame
# stack and no history are built from an empty restore. (The helper runs under
# `be`, where errexit is off, so the steps must stop on their own.)
spy_on
rm -f "$UV_LOG" "$UV_LOG.env"
GIT_FAIL_FALLBACK_FETCH=1 run_job now
spy_off
tl_eq "$rc" 0 "restore failure is best-effort for the Now job: $out"
tl_eq "$(tl_count "$UV_LOG" "live.nisar_stack")" 0 "the frame stack did not start from an empty restore"
tl_eq "$(tl_count "$UV_LOG" "live.now_history")" 0 "the history did not start from an empty restore"
tl_has "$(cat "$status/now.last_result")" "nisar-frame-stack" "stack step marked failed"
tl_has "$(cat "$status/now.last_result")" "now-history" "history step marked failed"
tl_eq "$(tl_count "$UV_LOG" "live/publish_fallback.py")" 1 "the rest of the run went on"
[[ ! -e "$PIPELINE_CHECKOUT/data/now/history" ]] || tl_fail "an empty history directory was left to be published"
rm -f "$UV_LOG" "$UV_LOG.env"
run_job now
tl_eq "$rc" 0 "and the next run restores again: $out"
tl_eq "$(tl_count "$UV_LOG" "live.nisar_stack")" 1 "stack step runs after a good restore"
tl_eq "$(tl_count "$UV_LOG" "live.now_history")" 1 "history step runs after a good restore"

# 5m. a run that stops without finishing is never recorded as ok (bash 3.2 exits
# with status 0 on an unbound variable)
orig_r1=$(declare -f r1_dswx)
r1_dswx() { echo "$unbound_variable_for_the_test"; }
rm -f "$UV_LOG" "$UV_LOG.env"
run_job now
eval "$orig_r1"
tl_eq "$rc" 1 "a fatal shell error fails the run: $out"
tl_has "$(cat "$status/now.last_result")" "failed" "status says failed"
[[ ! -e "$UV_LOG" || "$(tl_count "$UV_LOG" "live/publish_fallback.py")" == 0 ]] || tl_fail "a run that died published"

# 5j. the installed copy is compared with main (drift warning)
inst="$TL_TMP/installed"
mkdir -p "$inst"
cp "$here"/pipeline-lib.sh "$here"/pipeline-run.sh "$inst/"
mkdir -p "$PIPELINE_CHECKOUT/deploy/vps"
cp "$here"/pipeline-lib.sh "$PIPELINE_CHECKOUT/deploy/vps/"
echo "# drift" >>"$inst/pipeline-lib.sh"
run_dir="$inst"
tl_run out rc run_check_drift
tl_has "$out" "installed pipeline-lib.sh differs" "drift warning"
run_dir="$here"

echo "pipeline-run tests passed"
