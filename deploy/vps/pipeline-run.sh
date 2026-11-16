#!/usr/bin/env bash
# Run one tipping-point data job on the VPS: the same commands as the GitHub
# workflow of the same name (now.yml, nisar.yml, validation.yml, reference.yml),
# in the same order, with the same best-effort steps. Installed root-owned as
# /usr/local/lib/tipping-point-pipeline/pipeline-run.sh and started by the
# systemd units in deploy/vps/systemd/ as the unprivileged user
# tipping-pipeline. See docs/DEPLOY-VPS.md, "Running without GitHub Actions".
#
#   pipeline-run.sh now|nisar|validation|reference
#   pipeline-run.sh --preflight <job>     check the setup, run nothing
#   pipeline-run.sh --if-stale N <job>    backup run: do nothing when the job
#                                         succeeded less than N seconds ago or
#                                         another pipeline job is running
#
# Publishing is OFF unless PIPELINE_PUBLISH=1 (/etc/tipping-point/pipeline.env).
# Off means: the whole job runs, the snapshot is built and checked against the
# current fallback branch (live/publish_fallback.py --no-push), and nothing is
# committed, pushed or uploaded. With publishing on, the run refuses to start
# while the GitHub workflow of the same name is still active (single writer).
#
# The push credential is an HTTPS token (GH_TOKEN, the default) or an SSH deploy
# key (PIPELINE_PUSH_AUTH=ssh). It reaches the publish step only, never the data
# steps, and never a URL, .git/config, command line or log (run_with_push_auth).
# --preflight proves it with git push --dry-run, which writes nothing.
#
# SAFETY: this host also runs unrelated services of another company ("FE
# services"), currently stopped. This script starts, stops, restarts, removes
# or reconfigures nothing that does not belong to tipping-point, and it reads no
# data of any other container, volume, network, database, Caddy route, systemd
# unit, cron entry, user or file. It runs no docker command, no prune and no
# wildcard operation. Its write targets are exactly: the pipeline checkout
# (PIPELINE_CHECKOUT, /opt/tipping-point), the pipeline state directory
# (PIPELINE_STATE_DIR, /var/lib/tipping-point-pipeline), the fallback branch of
# the tipping-point repository (only when PIPELINE_PUBLISH=1) and, for the
# optional release-asset upload, its fallback-assets release.
set -Eeuo pipefail
umask 027

run_script=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
run_dir=$(dirname "$run_script")
# shellcheck source=deploy/vps/pipeline-lib.sh
source "$run_dir/pipeline-lib.sh"

PIPELINE_CHECKOUT=${PIPELINE_CHECKOUT:-/opt/tipping-point}
PIPELINE_STATE_DIR=${PIPELINE_STATE_DIR:-/var/lib/tipping-point-pipeline}
PIPELINE_BRANCH=${PIPELINE_BRANCH:-main}
PIPELINE_PUBLISH=${PIPELINE_PUBLISH:-0}
# token: push over HTTPS with GH_TOKEN. ssh: push with the deploy key.
PIPELINE_PUSH_AUTH=${PIPELINE_PUSH_AUTH:-token}
# Seconds to wait for the lock another pipeline job holds (0: do not wait).
PIPELINE_LOCK_WAIT_S=${PIPELINE_LOCK_WAIT_S:-3300}
PIPELINE_MIN_FREE_MB=${PIPELINE_MIN_FREE_MB:-3072}

CURRENT_STEP=""
PL_PUSH_TOKEN=""
degraded=""
dswx_restored=0
dswx_saved=0
run_done=0
sa_file=""
run_job_name=""

run_usage() {
  echo "usage: pipeline-run.sh [--preflight] [--if-stale SECONDS] now|nisar|validation|reference" >&2
}

# Workflow timeout-minutes: now 45, the others 60.
run_job_timeout_s() {
  case "$1" in
    now) echo 2700 ;;
    nisar | validation | reference) echo 3600 ;;
    *) return 1 ;;
  esac
}

run_check_not_root() {
  (( ${1:-$EUID} != 0 )) || pl_die "refusing to run as root; the data jobs run as the unprivileged pipeline user"
}

run_check_tools() { # [1: the push credential is used in this run]
  local t
  for t in git curl tar python3 flock timeout uv; do
    command -v "$t" >/dev/null 2>&1 || pl_die "missing command: $t"
  done
  if [[ "${1:-0}" == 1 && "$PIPELINE_PUSH_AUTH" == ssh ]]; then
    command -v ssh >/dev/null 2>&1 || pl_die "missing command: ssh (needed to push with the deploy key)"
  fi
  if [[ "$(uv --version 2>/dev/null || true)" != *" $PIPELINE_UV_VERSION"* ]]; then
    pl_log "WARNING: uv is '$(uv --version 2>/dev/null || true)', the workflows pin $PIPELINE_UV_VERSION"
  fi
}

# The pipeline's own uv comes first on PATH, for this process and its children only.
run_uv_path() {
  if [[ -x "$PIPELINE_UV_DIR/uv" ]]; then PATH="$PIPELINE_UV_DIR:$PATH"; export PATH; fi
}

# Paths, directories and the environment of every command the job starts.
run_setup() {
  [[ "$PIPELINE_STATE_DIR" == /* && "$PIPELINE_STATE_DIR" != / ]] ||
    pl_die "PIPELINE_STATE_DIR must be an absolute path other than /"
  [[ "$PIPELINE_PUSH_AUTH" == token || "$PIPELINE_PUSH_AUTH" == ssh ]] ||
    pl_die "PIPELINE_PUSH_AUTH must be token or ssh, not '$PIPELINE_PUSH_AUTH'"
  [[ -d "$PIPELINE_STATE_DIR" && -O "$PIPELINE_STATE_DIR" ]] ||
    pl_die "$PIPELINE_STATE_DIR must exist and belong to $(id -un) (see the runbook)"
  mkdir -p "$PIPELINE_STATE_DIR/tmp" "$PIPELINE_STATE_DIR/cache" \
    "$PIPELINE_STATE_DIR/status" "$PIPELINE_STATE_DIR/notify"
  export TMPDIR="$PIPELINE_STATE_DIR/tmp"
  PL_CACHE_DIR="$PIPELINE_STATE_DIR/cache"
  export UV_PROJECT_ENVIRONMENT=${UV_PROJECT_ENVIRONMENT:-$PIPELINE_STATE_DIR/venv}
  export UV_CACHE_DIR=${UV_CACHE_DIR:-$PIPELINE_STATE_DIR/uv-cache}
  export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$PIPELINE_STATE_DIR/uv-python}
  export UV_PYTHON=${UV_PYTHON:-3.12}
  export UV_NO_PROGRESS=1
  export GIT_TERMINAL_PROMPT=0
  export FALLBACK_GIT_NAME=${FALLBACK_GIT_NAME:-tipping-point-vps}
  export FALLBACK_GIT_EMAIL=${FALLBACK_GIT_EMAIL:-tipping-point-vps@users.noreply.github.com}
  export GH_REPO=${GH_REPO:-r2l2pj/tipping-point}
  # The push token is kept in a shell variable that is not exported. run_locked
  # removes GH_TOKEN from the environment, so only the publish step gets it back.
  PL_PUSH_TOKEN=${GH_TOKEN:-}
}

run_check_disk() {
  local path free
  for path in "$PIPELINE_STATE_DIR" "$PIPELINE_CHECKOUT"; do
    free=$(pl_free_mb "$path")
    (( free >= PIPELINE_MIN_FREE_MB )) ||
      pl_die "only ${free} MB free under $path, need $PIPELINE_MIN_FREE_MB MB (PIPELINE_MIN_FREE_MB)"
  done
}

# Single writer: with publishing on, the GitHub workflow of this job must be
# disabled. "unknown" (GitHub cannot be asked) warns and goes on.
run_publish_guard() {
  local job=$1 state
  [[ "$PIPELINE_PUBLISH" == 1 ]] || return 0
  state=$(pl_workflow_state "$job.yml")
  case "$state" in
    active)
      pl_die "the GitHub workflow $job.yml is still active; two writers would publish. Disable it first (runbook: cutover)"
      ;;
    unknown) pl_log "WARNING: could not read the state of GitHub workflow $job.yml; publishing anyway" ;;
    *) pl_log "GitHub workflow $job.yml is $state" ;;
  esac
}

# run_with_push_auth COMMAND...: run COMMAND with the push credential in its own
# environment only (a subshell: nothing reaches the next step).
#   token: git asks a credential helper, which prints the token from the
#     environment. The token is in no URL, no .git/config, no command line and no
#     log, and only github.com is offered it.
#   ssh: the deploy key with a pinned host key, and the SSH push URL for origin.
# GH_TOKEN goes along when set: the gh release upload of oversized files uses it.
run_with_push_auth() {
  (
    if [[ -n "$PL_PUSH_TOKEN" ]]; then export GH_TOKEN="$PL_PUSH_TOKEN"; fi
    if [[ "$PIPELINE_PUSH_AUTH" == token ]]; then
      export PL_PUSH_TOKEN
      export GIT_CONFIG_COUNT=2
      export GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=
      export GIT_CONFIG_KEY_1=credential.https://github.com.helper
      # shellcheck disable=SC2016  # the helper text expands in the helper, not here
      export GIT_CONFIG_VALUE_1='!f() { test "$1" = get || exit 0; echo username=x-access-token; echo "password=$PL_PUSH_TOKEN"; }; f'
    else
      export GIT_CONFIG_COUNT=1
      export GIT_CONFIG_KEY_0=remote.origin.pushurl GIT_CONFIG_VALUE_0="$PIPELINE_PUSH_URL"
      export GIT_SSH_COMMAND="ssh -F /dev/null -i $PIPELINE_STATE_DIR/ssh/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$PIPELINE_STATE_DIR/ssh/known_hosts"
    fi
    "$@"
  )
}

# run_push_fail TEXT: one clear FAIL line for the push check.
run_push_fail() {
  pl_log "push check: FAIL (auth=$PIPELINE_PUSH_AUTH): $1"
  return 1
}

# run_check_push: can this credential push to refs/heads/fallback? Proved without
# writing: git push --dry-run --force of the current fallback commit to the same
# ref, which sends nothing. It shows that origin is reachable and that the
# credential is accepted for a push. It cannot show a branch rule that rejects the
# real push; only a real publish does.
run_check_push() {
  local repo=$PIPELINE_CHECKOUT src what out rc=0 state=0 key known
  case "$PIPELINE_PUSH_AUTH" in
    token)
      [[ -n "$PL_PUSH_TOKEN" ]] || { run_push_fail "GH_TOKEN is empty; set it in pipeline.env (or PIPELINE_PUSH_AUTH=ssh)"; return 1; }
      ;;
    ssh)
      key="$PIPELINE_STATE_DIR/ssh/id_ed25519"
      known="$PIPELINE_STATE_DIR/ssh/known_hosts"
      [[ -r "$key" ]] || { run_push_fail "the deploy key $key is missing or unreadable"; return 1; }
      [[ -s "$known" ]] || { run_push_fail "$known is missing; pin the GitHub host key (runbook)"; return 1; }
      ;;
  esac
  if git -C "$repo" fetch -q origin '+refs/heads/fallback:refs/remotes/origin/fallback'; then
    src=refs/remotes/origin/fallback
    what="the current fallback commit $(git -C "$repo" rev-parse --short=12 "$src")"
  else
    (cd "$repo" && pl_remote_fallback_state) || state=$?
    if (( state == 2 )); then
      src=HEAD
      what="HEAD (no fallback branch yet: this dry run would create it)"
    else
      run_push_fail "cannot fetch origin/fallback and cannot tell whether the branch exists"
      return 1
    fi
  fi
  out=$(cd "$repo" && run_with_push_auth git push --dry-run --force origin "$src:refs/heads/fallback" 2>&1) || rc=$?
  if (( rc )); then
    if [[ -n "$PL_PUSH_TOKEN" ]]; then out=${out//"$PL_PUSH_TOKEN"/***}; fi
    run_push_fail "git push --dry-run failed (exit $rc): $(printf '%s' "$out" | tail -n 3 | tr '\n' ' ')"
    return 1
  fi
  pl_log "push check: PASS (auth=$PIPELINE_PUSH_AUTH): git push --dry-run accepted $what for refs/heads/fallback; nothing was written"
}

# run_publish_preflight JOB CHECK_PUSH: CHECK_PUSH is 1 for --preflight; a run
# with publishing on always proves the push credential before the job starts.
run_publish_preflight() {
  local job=$1 check_push=${2:-0}
  if [[ "$PIPELINE_PUBLISH" != 1 ]]; then
    pl_log "publishing is OFF (PIPELINE_PUBLISH is '$PIPELINE_PUBLISH', not 1): the snapshot is built and checked, never pushed"
    if [[ "$check_push" == 1 ]]; then run_check_push || pl_die "the push check failed (see above)"; fi
    return 0
  fi
  run_check_push || pl_die "the push check failed (see above); not starting a job that could not publish"
  if ! command -v gh >/dev/null 2>&1 || [[ -z "$PL_PUSH_TOKEN" ]]; then
    pl_log "WARNING: gh or GH_TOKEN is missing: oversized files will not be uploaded as release assets (the branch push itself works)"
  fi
  run_publish_guard "$job"
}

# Atomic small file write: run_write_file PATH TEXT
run_write_file() {
  printf '%s\n' "$2" >"$1.tmp" && mv -f "$1.tmp" "$1"
}

run_status() { # job result_text success(0|1)
  local dir="$PIPELINE_STATE_DIR/status" now
  now=$(date +%s)
  run_write_file "$dir/$1.last_attempt" "$now" || true
  run_write_file "$dir/$1.last_result" "$2" || true
  if (( $3 )); then run_write_file "$dir/$1.last_success" "$now" || true; fi
}

# pl_b64d: base64 decode on GNU and BSD userlands.
run_b64d() {
  base64 -d 2>/dev/null || base64 -D
}

# The workflow writes GCP_SA_KEY to /tmp/sa.json and activates gcloud. The
# python code reads GCP_SA_KEY and GOOGLE_APPLICATION_CREDENTIALS itself and
# never calls gcloud, so no gcloud is needed here. The key arrives as
# GCP_SA_KEY_B64 (one line, so the systemd EnvironmentFile can carry it).
run_gcp_credentials() {
  if [[ -z "${GCP_SA_KEY_B64:-}" ]]; then
    pl_notice "GCP_SA_KEY_B64 is not set (only the nisar, validation and reference jobs need it)"
    return 0
  fi
  sa_file=$(mktemp "$TMPDIR/sa.XXXXXXXX")
  chmod 600 "$sa_file"
  if ! printf '%s' "$GCP_SA_KEY_B64" | run_b64d >"$sa_file" ||
    ! python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$sa_file" 2>/dev/null; then
    pl_die "GCP_SA_KEY_B64 is not base64 of a JSON key"
  fi
  export GOOGLE_APPLICATION_CREDENTIALS="$sa_file"
  GCP_SA_KEY=$(<"$sa_file")
  export GCP_SA_KEY
  unset GCP_SA_KEY_B64
}

begin() {
  CURRENT_STEP=$1
  pl_log "step: $1"
}

# Best-effort step (continue-on-error in the workflow): log, remember, go on.
be() {
  local name=$1 rc=0
  shift
  begin "$name (best-effort)"
  "$@" || rc=$?
  if (( rc )); then
    degraded="$degraded $name"
    pl_log "WARNING: step $name failed (exit $rc); continuing, like the workflow (continue-on-error)"
  fi
  return 0
}

run_on_exit() {
  local rc=$?
  trap - EXIT TERM INT HUP
  cd "$PIPELINE_CHECKOUT" 2>/dev/null || true
  # "Save R1 DSWx state" runs with if: always() in the workflow
  if [[ "$run_job_name" == now ]] && (( dswx_restored && ! dswx_saved )); then
    pl_cache_save "$PIPELINE_CHECKOUT" "$PL_CACHE_DIR/$PL_CACHE_DSWX" \
      data/now/response/dswx_state.npz npz || true
  fi
  if [[ -n "$sa_file" ]]; then rm -f "$sa_file"; fi
  # Some shells exit with status 0 on a fatal expansion error (set -u on bash 3.2):
  # a run that did not reach the end of the job body is never "ok".
  if (( rc == 0 && ! run_done )); then rc=1; fi
  if (( rc == 0 )); then
    if [[ -n "$degraded" ]]; then
      run_status "$run_job_name" "ok (best-effort steps failed:$degraded)" 1
      pl_log "$run_job_name finished: ok, with best-effort steps failed:$degraded"
    else
      run_status "$run_job_name" "ok" 1
      pl_log "$run_job_name finished: ok"
    fi
  else
    run_status "$run_job_name" "failed (exit $rc) at step: $CURRENT_STEP" 0
    pl_log "FAILED: $run_job_name exit $rc at step: $CURRENT_STEP"
  fi
  git -C "$PIPELINE_CHECKOUT" -c gc.pruneExpire=2.days.ago gc --auto -q >/dev/null 2>&1 || true
  exit "$rc"
}

# A warning when the installed copy of a script is not the one on main.
run_check_drift() {
  local f
  [[ "$run_dir" != "$PIPELINE_CHECKOUT/deploy/vps" ]] || return 0
  for f in pipeline-lib.sh pipeline-run.sh notify.sh watchdog.sh keepalive.sh; do
    [[ -f "$run_dir/$f" && -f "$PIPELINE_CHECKOUT/deploy/vps/$f" ]] || continue
    cmp -s "$run_dir/$f" "$PIPELINE_CHECKOUT/deploy/vps/$f" ||
      pl_log "WARNING: installed $f differs from deploy/vps/$f on $PIPELINE_BRANCH; re-install it (runbook)"
  done
}

run_publish() { # job
  case "$1" in
    # R2 deletes flood_nisar.geojson by design when no scene covers the window
    now) export FALLBACK_ALLOW_DROP=data/now/response/flood_nisar.geojson ;;
    # live Now outputs belong to the Now job: keep fallback's data/now
    *) export FALLBACK_KEEP=now ;;
  esac
  if [[ "$PIPELINE_PUBLISH" == 1 ]]; then
    run_with_push_auth uv run python live/publish_fallback.py
  else
    uv run python live/publish_fallback.py --no-push
  fi
}

# --- the best-effort steps of now.yml, one function each ---------------------

r1_dswx() {
  if [[ -f pipeline/scripts/13_response_dswx.py ]]; then
    PYTHONPATH=pipeline uv run python pipeline/scripts/13_response_dswx.py
  else pl_notice "R1 script not on this branch yet"; fi
}

r2_nisar() {
  if [[ -f pipeline/scripts/14_response_nisar.py ]]; then
    PYTHONPATH=pipeline uv run python pipeline/scripts/14_response_nisar.py
  else pl_notice "R2 script not on this branch yet"; fi
}

r3_summary() {
  if [[ -f pipeline/scripts/15_response_summary.py ]]; then
    PYTHONPATH=pipeline uv run python pipeline/scripts/15_response_summary.py
  else pl_notice "R3 script not on this branch yet"; fi
}

r4_dist() {
  if [[ -f pipeline/scripts/16_response_dist.py ]]; then
    PYTHONPATH=pipeline uv run python pipeline/scripts/16_response_dist.py
  else pl_notice "R4 script not on this branch yet"; fi
}

# OFF unless NOW_P_TRIGGER=1 (the repository variable of the same name).
p_trigger() {
  pl_static_inputs p-trigger "$PL_CACHE_DIR/$PL_CACHE_STATIC_PTRIGGER" &&
    NOW_P_TRIGGER=1 PYTHONPATH=pipeline uv run python -m live.p_trigger
}

nisar_next() {
  if [[ -f live/nisar_next.py ]]; then
    PYTHONPATH=".:pipeline" uv run python -m live.nisar_next --now-dir data/now
  else pl_notice "live/nisar_next.py not on this branch yet"; fi
}

# workflow timeout-minutes: 12 for this step
nisar_stack() {
  # `be` runs this under ||, where errexit is off: stop here when the restore
  # failed, so the step never starts from an empty stack
  pl_restore_fallback data/now/nisar_stack || return 1
  PYTHONPATH=".:pipeline" timeout 12m uv run python -m live.nisar_stack --now-dir data/now
}

nisar_tpi() {
  PYTHONPATH=".:pipeline" uv run python -m live.now_nisar_tpi --now-dir data/now
}

places() {
  PYTHONPATH=".:pipeline" uv run python -m live.places --now-dir data/now
}

# after now-history: the forecast leads come from the restored history archive
nisar_agreement() {
  PYTHONPATH=".:pipeline" uv run python tools/now_nisar_agreement.py --now-dir data/now
}

now_history() {
  pl_restore_fallback data/now/history &&
    uv run python -m live.now_history --now-dir data/now &&
    uv run python tools/build_manifest.py
}

sqlite_mirror() {
  pl_prev_sqlite &&
    PYTHONPATH=pipeline uv run python tools/build_sqlite.py
}

# --- the jobs ----------------------------------------------------------------

job_now() {
  local root=$PIPELINE_CHECKOUT cache=$PL_CACHE_DIR
  begin uv-sync
  uv sync --frozen
  begin gcp-credentials
  run_gcp_credentials
  begin earthdata-token
  uv run python -c "from live.fetch import check_earthdata_token; check_earthdata_token()"
  begin static-inputs
  pl_static_inputs response "$cache/$PL_CACHE_STATIC_RESPONSE"
  begin restore-state
  pl_cache_restore "$root" "$cache/$PL_CACHE_DSWX" data/now/response/dswx_state.npz replace
  dswx_restored=1
  pl_cache_restore "$root" "$cache/$PL_CACHE_PREV_CLASSES" data/now/prev_classes.npz replace
  pl_cache_restore "$root" "$cache/$PL_CACHE_CLIM" data/static/south_brazil/clim/clim_imerg_0p1.tif keep
  begin now-job
  NOW_LOCAL=1 uv run python live/jobs.py now
  be save-alert-classes pl_cache_save "$root" "$cache/$PL_CACHE_PREV_CLASSES" data/now/prev_classes.npz npz
  be r1-dswx r1_dswx
  be save-r1-dswx-state pl_cache_save "$root" "$cache/$PL_CACHE_DSWX" data/now/response/dswx_state.npz npz
  dswx_saved=1
  be r2-nisar-flood r2_nisar
  be r3-summary r3_summary
  be r4-dist r4_dist
  if [[ "${NOW_P_TRIGGER:-}" == 1 ]]; then be p-trigger p_trigger; fi
  be nisar-next-passes nisar_next
  be nisar-frame-stack nisar_stack
  be nisar-flood-index nisar_tpi
  be places places
  begin finalize-manifest
  NOW_P_TRIGGER="${NOW_P_TRIGGER:-}" uv run python -m live.now_manifest
  uv run python tools/build_manifest.py
  be now-history now_history
  be nisar-agreement nisar_agreement
  be sqlite-mirror sqlite_mirror
  begin publish
  run_publish_guard now
  run_publish now
  be save-climatology pl_cache_save "$root" "$cache/$PL_CACHE_CLIM" data/static/south_brazil/clim/clim_imerg_0p1.tif file
}

# nisar, validation, reference: run, then publish only if data/ changed
job_data() { # job
  local job=$1
  begin uv-sync
  uv sync --frozen
  begin gcp-credentials
  run_gcp_credentials
  begin earthdata-token
  uv run python -c "from live.fetch import check_earthdata_token; check_earthdata_token()"
  begin "$job-job"
  case "$job" in
    nisar) uv run python live/jobs.py nisar ;;
    validation) uv run python live/jobs.py validation ;;
    reference) uv run python live/jobs.py reference ;;
  esac
  begin detect-data
  if [[ -n "$(git status --porcelain -- data/)" ]]; then
    begin publish
    run_publish_guard "$job"
    run_publish "$job"
  else
    pl_log "$job wrote no data/ artifacts: nothing to publish"
  fi
}

# The job body, run under the lock and the timeout.
run_locked() { # job
  local job=$1
  run_job_name=$job
  trap 'exit 143' TERM INT HUP
  trap run_on_exit EXIT
  run_setup
  # no data step sees the push token; run_publish hands it to the publish step
  unset GH_TOKEN
  cd "$PIPELINE_CHECKOUT"
  begin checkout-update
  pl_checkout_update "$PIPELINE_CHECKOUT" "$PIPELINE_BRANCH"
  run_check_drift
  case "$job" in
    now) job_now ;;
    *) job_data "$job" ;;
  esac
  run_done=1
}

# run_is_fresh JOB MAX_AGE_S: true when the job succeeded less than MAX_AGE_S ago.
run_is_fresh() {
  local file="$PIPELINE_STATE_DIR/status/$1.last_success" last
  [[ -r "$file" ]] || return 1
  last=$(<"$file")
  [[ "$last" =~ ^[0-9]+$ ]] || return 1
  (( $(date +%s) - last < $2 ))
}

run_main() {
  local mode=run job stale_max=""
  while (( $# > 0 )); do
    case "$1" in
      --locked) mode=locked; shift ;;
      --preflight) mode=preflight; shift ;;
      --if-stale)
        [[ "${2:-}" =~ ^[0-9]+$ ]] || { run_usage; exit 2; }
        stale_max=$2
        shift 2
        ;;
      -*) run_usage; exit 2 ;;
      *) break ;;
    esac
  done
  if (( $# != 1 )); then run_usage; exit 2; fi
  job=$1
  run_job_timeout_s "$job" >/dev/null || { run_usage; exit 2; }
  run_check_not_root "$EUID"
  run_uv_path
  if [[ "$mode" == locked ]]; then
    run_locked "$job"
    return
  fi
  local check_push=0
  if [[ "$mode" == preflight ]]; then check_push=1; fi
  run_check_tools $(( check_push || PIPELINE_PUBLISH == 1 ))
  run_setup
  if [[ -n "$stale_max" ]] && run_is_fresh "$job" "$stale_max"; then
    pl_log "$job succeeded less than ${stale_max}s ago; nothing to do"
    return 0
  fi
  pl_validate_checkout "$PIPELINE_CHECKOUT"
  run_check_disk
  run_publish_preflight "$job" "$check_push"
  if [[ "$mode" == preflight ]]; then
    pl_log "preflight ok for $job"
    return 0
  fi
  # One pipeline job at a time: they share one checkout and one fallback branch.
  exec 9>"$PIPELINE_STATE_DIR/pipeline.lock"
  if [[ -n "$stale_max" ]]; then
    # backup run: never queue behind a running job, that job is the run
    flock -n 9 || { pl_log "another pipeline job is running; nothing to do"; return 0; }
    if run_is_fresh "$job" "$stale_max"; then
      pl_log "$job succeeded less than ${stale_max}s ago; nothing to do"
      return 0
    fi
  elif (( PIPELINE_LOCK_WAIT_S > 0 )); then
    flock -w "$PIPELINE_LOCK_WAIT_S" 9 || { pl_log "another pipeline job still holds the lock after ${PIPELINE_LOCK_WAIT_S}s"; exit 75; }
  else
    flock -n 9 || { pl_log "another pipeline job is running; nothing to do"; exit 75; }
  fi
  pl_log "$job: start (publishing is $([[ "$PIPELINE_PUBLISH" == 1 ]] && echo ON || echo OFF))"
  exec timeout --kill-after=60 "$(run_job_timeout_s "$job")" "$BASH" "$run_script" --locked "$job"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  run_main "$@"
fi
