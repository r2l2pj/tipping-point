#!/usr/bin/env bash
# Static checks of the systemd units and scripts in deploy/vps: unit shape,
# schedules and limits against the GitHub workflows they replace, parity of the
# job steps (same entry points, same order), secret names, cache keys and the uv
# pin, and the safety rules (no prune, no wildcard container operation, no unit
# control, no sudo, ASCII only).
# No Docker, no network, no root. systemd-analyze runs only when it exists.
# shellcheck disable=SC2154  # tl_run sets out and rc
# shellcheck disable=SC2016  # runbook text and shell blocks are searched literally, $ signs included
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
wf="$root/.github/workflows"
units="$here/systemd"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup

# uget FILE SECTION KEY: first value of KEY in [SECTION], comments ignored.
uget() {
  awk -v sec="[$2]" -v key="$3" '
    /^[#;]/ { next }
    /^\[/ { cur = $0; next }
    cur == sec {
      i = index($0, "=")
      if (i > 0 && substr($0, 1, i - 1) == key) { print substr($0, i + 1); exit }
    }' "$1"
}
# uall FILE SECTION KEY: every value of KEY, one per line.
uall() {
  awk -v sec="[$2]" -v key="$3" '
    /^[#;]/ { next }
    /^\[/ { cur = $0; next }
    cur == sec {
      i = index($0, "=")
      if (i > 0 && substr($0, 1, i - 1) == key) print substr($0, i + 1)
    }' "$1"
}
has_section() { grep -q "^\[$2\]\$" "$1"; }

# --- 1. the unit set is exactly the intended one ----------------------------------------------
want="tipping-point-app-keepalive.service
tipping-point-app-keepalive.timer
tipping-point-by-hand@.service
tipping-point-nisar.service
tipping-point-nisar.timer
tipping-point-notify@.service
tipping-point-now-backup.service
tipping-point-now-backup.timer
tipping-point-now.service
tipping-point-now.timer
tipping-point-reference.service
tipping-point-reference.timer
tipping-point-stale-alarm.service
tipping-point-stale-alarm.timer
tipping-point-validation.service
tipping-point-validation.timer"
tl_eq "$(find "$units" -type f -exec basename {} \; | LC_ALL=C sort)" "$(printf '%s\n' "$want" | LC_ALL=C sort)" "unit files"

# --- 2. every service ----------------------------------------------------------------------------
for f in "$units"/*.service; do
  name=$(basename "$f" .service)
  tl_eq "$(uget "$f" Service Type)" oneshot "$name Type"
  tl_eq "$(uget "$f" Service User)" tipping-pipeline "$name User"
  tl_eq "$(uget "$f" Service Group)" tipping-pipeline "$name Group"
  if has_section "$f" Install; then tl_fail "$name has an [Install] section (only timers are enabled)"; fi
  [[ -z "$(uall "$f" Service Restart)" ]] || tl_fail "$name sets Restart= (a oneshot job must not loop)"
  exec_line=$(uget "$f" Service ExecStart)
  script=${exec_line%% *}
  case "$script" in /usr/local/lib/tipping-point-pipeline/*) ;; *) tl_fail "$name ExecStart is not under /usr/local/lib/tipping-point-pipeline: $exec_line" ;; esac
  [[ -x "$here/$(basename "$script")" ]] || tl_fail "$name: $(basename "$script") is not an executable file in deploy/vps"
  tl_eq "$(uget "$f" Service SyslogIdentifier)" "${name%@}" "$name journal identifier"
  timeout_s=$(uget "$f" Service TimeoutStartSec)
  [[ "$timeout_s" =~ ^[0-9]+$ && "$timeout_s" -gt 0 ]] || tl_fail "$name has no numeric TimeoutStartSec"
  for key in Nice MemoryMax TasksMax; do
    [[ -n "$(uget "$f" Service $key)" ]] || tl_fail "$name sets no $key"
  done
  [[ "$(uget "$f" Service Nice)" =~ ^[0-9]+$ ]] || tl_fail "$name Nice is not a positive number"
  tl_eq "$(uget "$f" Service UMask)" 0027 "$name UMask"
  tl_eq "$(uget "$f" Service ProtectHome)" true "$name ProtectHome"
  tl_eq "$(uget "$f" Service PrivateTmp)" true "$name PrivateTmp"
  case "$name" in
    tipping-point-notify@ | tipping-point-by-hand@) ;;
    *)
      tl_eq "$(uall "$f" Unit OnFailure)" "tipping-point-notify@%n.service" "$name OnFailure"
      ;;
  esac
done
for name in now now-backup nisar validation reference; do
  f="$units/tipping-point-$name.service"
  tl_eq "$(uget "$f" Service EnvironmentFile)" /etc/tipping-point/pipeline.env "$name env file"
  tl_eq "$(uget "$f" Service IOSchedulingClass)" best-effort "$name IO class"
  tl_eq "$(uget "$f" Service CPUQuota)" 200% "$name CPUQuota"
  tl_eq "$(uget "$f" Service MemoryMax)" 4G "$name MemoryMax"
  # a host-wide memory shortage picks a data job before the app or another company's service
  tl_eq "$(uget "$f" Service OOMScoreAdjust)" 500 "$name OOMScoreAdjust"
  tl_eq "$(uget "$f" Service NoNewPrivileges)" true "$name NoNewPrivileges"
  tl_eq "$(uget "$f" Service ProtectSystem)" strict "$name ProtectSystem"
  tl_eq "$(uget "$f" Service ReadWritePaths)" "/var/lib/tipping-point-pipeline /opt/tipping-point" "$name writable paths"
  tl_eq "$(uget "$f" Service PIPELINE_STATE_DIR)" "" "state dir is set through Environment="
  tl_has "$(uall "$f" Service Environment)" "PIPELINE_STATE_DIR=/var/lib/tipping-point-pipeline" "$name state dir"
  tl_has "$(uall "$f" Service Environment)" "PIPELINE_CHECKOUT=/opt/tipping-point" "$name checkout"
done
# the notify unit never starts another notice, and only the notify unit has an instance
n="$units/tipping-point-notify@.service"
tl_eq "$(uall "$n" Unit OnFailure)" "" "notify has no OnFailure"
tl_eq "$(uget "$n" Service ExecStart)" "/usr/local/lib/tipping-point-pipeline/notify.sh %i" "notify passes the instance"
tl_eq "$(uget "$n" Service NoNewPrivileges)" true "notify NoNewPrivileges"
# notify has its own optional env file with the webhook URL only: it must not read the
# file that holds the data tokens
tl_eq "$(uget "$n" Service EnvironmentFile)" "-/etc/tipping-point/notify.env" "notify env file is its own and optional"
# the by-hand unit runs the same script as the data jobs, as the same user, with the same
# environment file; it has no timer, no OnFailure and no [Install] section
b="$units/tipping-point-by-hand@.service"
tl_eq "$(uget "$b" Service EnvironmentFile)" /etc/tipping-point/pipeline.env "by-hand env file"
tl_eq "$(uget "$b" Service ExecStart)" "/usr/local/lib/tipping-point-pipeline/pipeline-run.sh --by-hand %i" "by-hand passes the instance"
tl_eq "$(uall "$b" Unit OnFailure)" "" "by-hand has no OnFailure (the result is shown at once)"
tl_eq "$(uget "$b" Service WorkingDirectory)" /var/lib/tipping-point-pipeline "by-hand working directory"
tl_eq "$(uget "$b" Service NoNewPrivileges)" true "by-hand NoNewPrivileges"
tl_eq "$(uget "$b" Service ProtectSystem)" strict "by-hand ProtectSystem"
tl_eq "$(uget "$b" Service ReadWritePaths)" "/var/lib/tipping-point-pipeline /opt/tipping-point" "by-hand writable paths"
tl_has "$(uall "$b" Service Environment)" "HOME=/var/lib/tipping-point-pipeline" "by-hand HOME"
tl_has "$(uall "$b" Service Environment)" "PIPELINE_STATE_DIR=/var/lib/tipping-point-pipeline" "by-hand state dir"
tl_has "$(uall "$b" Service Environment)" "PIPELINE_CHECKOUT=/opt/tipping-point" "by-hand checkout"
[[ ! -e "$units/tipping-point-by-hand@.timer" ]] || tl_fail "the by-hand unit must have no timer"
# the units without secrets do not read the secrets file
for name in stale-alarm app-keepalive notify@; do
  case "$(uget "$units/tipping-point-$name.service" Service EnvironmentFile)" in
    /etc/tipping-point/pipeline.env | -/etc/tipping-point/pipeline.env) tl_fail "$name reads the secrets file" ;;
    *) ;;
  esac
done
# only the five job units and the by-hand unit read pipeline.env
tl_eq "$(grep -l 'pipeline\.env' "$units"/*.service | xargs -n1 basename | LC_ALL=C sort | tr '\n' ' ')" \
  "tipping-point-by-hand@.service tipping-point-nisar.service tipping-point-now-backup.service tipping-point-now.service tipping-point-reference.service tipping-point-validation.service " \
  "the units that mention the secrets file"

# --- 3. every timer --------------------------------------------------------------------------------
for f in "$units"/*.timer; do
  name=$(basename "$f" .timer)
  tl_eq "$(uget "$f" Timer Persistent)" true "$name Persistent"
  tl_eq "$(uget "$f" Timer Unit)" "$name.service" "$name unit"
  [[ -f "$units/$name.service" ]] || tl_fail "$name.timer has no service"
  tl_eq "$(uget "$f" Install WantedBy)" timers.target "$name WantedBy"
  cal=$(uget "$f" Timer OnCalendar)
  case "$cal" in *" UTC") ;; *) tl_fail "$name OnCalendar has no explicit UTC: $cal" ;; esac
  [[ -n "$(uget "$f" Timer AccuracySec)" ]] || tl_fail "$name has no AccuracySec"
done

# --- 4. schedules and run limits against the workflows they replace ----------------------------------
# workflow | cron string of the workflow | timer | OnCalendar
while IFS='|' read -r workflow cron timer cal; do
  grep -q -F "cron: \"$cron\"" "$wf/$workflow" || tl_fail "$workflow no longer has cron '$cron' (update the timer and this test)"
  tl_eq "$(uget "$units/$timer.timer" Timer OnCalendar)" "$cal" "$timer schedule"
done <<'TABLE'
now.yml|17 */3 * * *|tipping-point-now|*-*-* 00,03,06,09,12,15,18,21:17:00 UTC
nisar.yml|30 6 * * *|tipping-point-nisar|*-*-* 06:30:00 UTC
validation.yml|0 7 * * *|tipping-point-validation|*-*-* 07:00:00 UTC
reference.yml|0 8 * * 1|tipping-point-reference|Mon *-*-* 08:00:00 UTC
now-watchdog.yml|53 * * * *|tipping-point-now-backup|*-*-* *:41,53:00 UTC
now-watchdog.yml|41 * * * *|tipping-point-now-backup|*-*-* *:41,53:00 UTC
app-keepalive.yml|*/10 * * * *|tipping-point-app-keepalive|*-*-* *:0/10:00 UTC
TABLE
# the stale alarm runs at the minute of the first watchdog cron
tl_eq "$(uget "$units/tipping-point-stale-alarm.timer" Timer OnCalendar)" "*-*-* *:41:00 UTC" "stale alarm schedule"
# the backup run uses the workflow's own threshold
tl_has "$(uget "$units/tipping-point-now-backup.service" Service ExecStart)" "--if-stale 12600 now" "backup threshold"
grep -q '12600' "$wf/now-watchdog.yml" || tl_fail "the watchdog workflow lost its 12600 s threshold (update the backup unit)"
tl_has "$(grep 'STALE_AFTER_S:' "$wf/now-watchdog.yml")" "'21600'" "stale alarm age in the workflow"
tl_has "$(cat "$here/watchdog.sh")" "21600" "stale alarm age in the script"

# job run limits: workflow minutes equal the runner seconds; the unit limit covers lock wait plus run
# shellcheck source=deploy/vps/pipeline-run.sh
source "$here/pipeline-run.sh"
trap - ERR
set +E
for job in now nisar validation reference; do
  minutes=$(grep -m1 '^    timeout-minutes:' "$wf/$job.yml" | sed 's/.*: *//')
  tl_eq "$(run_job_timeout_s "$job")" "$((minutes * 60))" "$job run limit equals timeout-minutes of $job.yml"
  unit_limit=$(uget "$units/tipping-point-$job.service" Service TimeoutStartSec)
  [[ "$unit_limit" -ge $((minutes * 60 + 3300)) ]] || tl_fail "$job TimeoutStartSec $unit_limit does not cover lock wait plus run"
done
[[ "$(uget "$units/tipping-point-now-backup.service" Service TimeoutStartSec)" -ge 2700 ]] || tl_fail "backup TimeoutStartSec below the Now run limit"
tl_eq "$(grep -c 'timeout --kill-after=30 12m uv run python -m live.nisar_stack' "$here/pipeline-run.sh")" 1 "nisar_stack step limit, with a kill-after"
# the first cold run may need more than the workflow's limit: the setting is empty by default, the
# default limits stay the workflow's, and the free-disk guard has room for a cold run and an app build
tl_eq "${PIPELINE_TIMEOUT_S:-}" "" "no run limit override by default"
tl_eq "$(run_job_limit_s now)" 2700 "the default run limit of now"
tl_eq "$PIPELINE_MIN_FREE_MB" 10240 "free-disk guard default (MB)"
# the Now job must keep the local path, which writes no Google Earth Engine asset (without it
# gee/now_tpi.py deletes and re-exports 16 assets); the runbook's write-free trial rests on it
tl_eq "$(grep -c 'NOW_LOCAL=1 uv run python live/jobs.py now' "$here/pipeline-run.sh")" 1 "Now job runs with NOW_LOCAL=1"
tl_has "$(grep -A8 'Run now job' "$wf/now.yml")" 'NOW_LOCAL: "1"' "now.yml runs the Now job with NOW_LOCAL=1"
tl_has "$(grep -A3 'name: NISAR frame stack' "$wf/now.yml")" "timeout-minutes: 12" "nisar_stack step limit in now.yml"
# the Google key is optional for the jobs that do not use Earth Engine: now (NOW_LOCAL=1 skips the
# path in live/jobs.py) and nisar (search only). Which jobs need it is one list in the runner
tl_has "$(grep -B1 -A6 'NOW_LOCAL' "$here/../../live/jobs.py" | head -20)" "skips the GEE asset path" "live/jobs.py still says NOW_LOCAL=1 skips Earth Engine"
tl_eq "$(grep -c 'pipeline.fetch.nisar' "$here/../../live/jobs.py")" 1 "nisar calls pipeline.fetch.nisar once"
tl_lacks "$(grep -B2 -A6 'pipeline.fetch.nisar' "$here/../../live/jobs.py")" "--execute" "nisar never passes --execute (no raster ingest)"
for j in now nisar; do
  tl_run out rc bash -c "source '$here/pipeline-run.sh' && run_gcp_needed $j"
  tl_eq "$rc" 1 "the Google key is not needed by $j"
done
for j in validation reference; do
  tl_run out rc bash -c "source '$here/pipeline-run.sh' && run_gcp_needed $j"
  tl_eq "$rc" 0 "the Google key is needed by $j"
done

# --- 5. parity with the workflows --------------------------------------------------------------------
# step_parity.py reduces a workflow and the runner function that replaces it to the same ordered list
# of entry points (the commands that do the work; helper functions of the runner are resolved to the
# commands in their bodies) and compares the two lists. A missing call, an extra call and a different
# order fail. A `run:` line that starts python, uv, pytest, curl, bash, git or another tool in a form
# it does not understand fails too, so a new kind of step cannot pass unnoticed.
parity() { # WORKFLOW_FILE RUNNER_FILE LIB_FILE FUNCTION JOB MIN_ENTRIES
  python3 "$here/step_parity.py" --workflow "$1" --runner "$2" --lib "$3" --function "$4" --job "$5" --min-entries "$6"
}
tl_run out rc parity "$wf/now.yml" "$here/pipeline-run.sh" "$here/pipeline-lib.sh" job_now now 20
tl_eq "$rc" 0 "now.yml and job_now do the same steps in the same order: $out"
tl_has "$out" "24 entry points in the same order" "now.yml has 24 entry points"
for job in nisar validation reference; do
  tl_run out rc parity "$wf/$job.yml" "$here/pipeline-run.sh" "$here/pipeline-lib.sh" job_data "$job" 5
  tl_eq "$rc" 0 "$job.yml and job_data ($job) do the same steps in the same order: $out"
done

# The checker itself, on copies: each mutation must fail it and the unmutated copy must pass.
par="$TL_TMP/parity"
mutate() { # FILE OLD NEW: replace the first exact text; the test fails when the text is not there
  python3 - "$1" "$2" "$3" <<'PY'
import sys
path, old, new = sys.argv[1:4]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
if old not in text:
    sys.exit("mutation text not found: " + old)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text.replace(old, new, 1))
PY
}
fresh() { rm -rf "$par"; mkdir -p "$par"; cp "$wf"/now.yml "$wf"/nisar.yml "$wf"/validation.yml "$wf"/reference.yml "$par/"; cp "$here/pipeline-run.sh" "$here/pipeline-lib.sh" "$par/"; }
pnow() { parity "$par/now.yml" "$par/pipeline-run.sh" "$par/pipeline-lib.sh" job_now now 20; }
pdata() { parity "$par/$1.yml" "$par/pipeline-run.sh" "$par/pipeline-lib.sh" job_data "$1" 5; }
fresh
tl_run out rc pnow
tl_eq "$rc" 0 "unmutated copy passes: $out"
# the runner forgets a call
fresh; mutate "$par/pipeline-run.sh" "  be places places
" ""
tl_run out rc pnow
tl_eq "$rc" 1 "a deleted call fails"
tl_has "$out" "live.places" "the deleted call is named"
# the call stays but its function does nothing
fresh; mutate "$par/pipeline-run.sh" 'PYTHONPATH=".:pipeline" uv run python -m live.places --now-dir data/now' ":"
tl_run out rc pnow
tl_eq "$rc" 1 "an emptied function body fails"
# two steps swapped in the runner
fresh; mutate "$par/pipeline-run.sh" "  be nisar-flood-index nisar_tpi
  be places places
" "  be places places
  be nisar-flood-index nisar_tpi
"
tl_run out rc pnow
tl_eq "$rc" 1 "two swapped steps fail"
tl_has "$out" "differ at entry" "the first different entry is shown"
# two commands swapped inside one helper function
fresh; mutate "$par/pipeline-run.sh" "    uv run python -m live.now_history --now-dir data/now &&
    uv run python tools/build_manifest.py" "    uv run python tools/build_manifest.py &&
    uv run python -m live.now_history --now-dir data/now"
tl_run out rc pnow
tl_eq "$rc" 1 "a swap inside a helper function fails"
# the runner runs a step the workflow does not have
fresh; mutate "$par/pipeline-run.sh" "  begin finalize-manifest
" "  begin finalize-manifest
  uv run python tools/extra.py
"
tl_run out rc pnow
tl_eq "$rc" 1 "an extra runner step fails"
# the workflow gains steps
fresh; printf '      - name: extra\n        run: python3 tools/x.py\n' >>"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "a new python3 step in the workflow fails"
tl_has "$out" "does not understand" "an unknown python form is named"
fresh; printf '      - name: extra\n        run: uv run pytest -q\n' >>"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "a new uv run pytest step in the workflow fails"
fresh; printf '      - name: extra\n        run: curl -fsS https://example.invalid/hook\n' >>"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "a new curl step in the workflow fails"
fresh; printf '      - name: extra\n        run: |\n          set -e\n          cd data && bash ../tools/x.sh\n' >>"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "a tool inside a multi-line step fails"
fresh; printf '      - name: extra\n        run: env FOO=1 python3 tools/x.py\n' >>"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "a tool behind env fails"
fresh; printf '      - name: extra\n        run: uv run --with x python tools/x.py\n' >>"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "an unknown uv run form fails"
# a workflow step is deleted, renamed or moved
fresh; mutate "$par/now.yml" "uv run python -m live.places --now-dir data/now" "echo places"
tl_run out rc pnow
tl_eq "$rc" 1 "a step removed from the workflow fails"
fresh; mutate "$par/now.yml" "uv run python -m live.places --now-dir data/now" "uv run python -m live.places --now-dir data/other"
tl_run out rc pnow
tl_eq "$rc" 1 "a changed argument fails"
# the data jobs: another job body, and a runner arm that is gone
fresh; mutate "$par/nisar.yml" "uv run python live/jobs.py nisar" "uv run python live/jobs.py nisar --extra"
tl_run out rc pdata nisar
tl_eq "$rc" 1 "a changed data job command fails"
fresh; mutate "$par/pipeline-run.sh" "    nisar) uv run python live/jobs.py nisar ;;
" ""
tl_run out rc pdata nisar
tl_eq "$rc" 1 "a missing case arm of a data job fails"
fresh; printf '      - name: extra\n        run: python tools/x.py\n' >>"$par/reference.yml"
tl_run out rc pdata reference
tl_eq "$rc" 1 "a new python step in reference.yml fails"
# a parser that finds nothing must not pass
fresh; : >"$par/now.yml"
tl_run out rc pnow
tl_eq "$rc" 1 "an empty workflow fails"
tl_run out rc parity "$par/now.yml" "$par/pipeline-run.sh" "$par/pipeline-lib.sh" no_such_function now 1
tl_eq "$rc" 1 "a missing runner function fails"
# secret and variable names are in the env example (the GCP key travels as base64)
example=$(cat "$here/pipeline.env.example")
names=$(grep -ho '\(secrets\|vars\)\.[A-Z_0-9]*' "$wf/now.yml" "$wf/nisar.yml" "$wf/validation.yml" "$wf/reference.yml" | sed 's/^[a-z]*\.//' | LC_ALL=C sort -u)
for s in $names; do
  case "$s" in
    GITHUB_TOKEN) s=GH_TOKEN ;;
    GCP_SA_KEY) s=GCP_SA_KEY_B64 ;;
    *) ;;
  esac
  tl_has "$example" "
$s=" "pipeline.env.example lacks $s"
done
tl_has "$names" EARTHDATA_TOKEN "parser found the secrets"
# the example holds names only
while IFS= read -r line; do
  case "$line" in
    "" | "#"*) ;;
    PIPELINE_PUBLISH=0) ;;
    *=) ;;
    *) tl_fail "pipeline.env.example has a value: $line" ;;
  esac
done <"$here/pipeline.env.example"
tl_has "$example" "PIPELINE_PUBLISH=0" "publishing is off in the example"
# the notify env file holds the webhook setting and nothing else, with no value
tl_eq "$(grep -v -E '^[[:space:]]*(#|$)' "$here/notify.env.example")" "NOTIFY_WEBHOOK_URL=" "notify.env.example holds one empty name"
tl_eq "$(stat -c '%a' "$here/notify.env.example" 2>/dev/null || stat -f '%Lp' "$here/notify.env.example")" 644 "notify.env.example is a plain readable example"
# the push credential is only a name in the example, and the alternative is a comment
tl_has "$example" "
GH_TOKEN=" "the push token name is in pipeline.env.example"
tl_lacks "$(grep -v -E '^[[:space:]]*#' "$here/pipeline.env.example")" "PIPELINE_PUSH_AUTH" "ssh mode is only a commented alternative"
# the app watcher only builds and deploys: no check script is left, and nothing mentions one
[[ ! -e "$here/app-check.sh" && ! -e "$here/test-app-check.sh" ]] || tl_fail "app-check files are still there"
leftover=$(grep -rn -E 'app-check|tipping-point-app-check' "$here" "$units" "$here/../../.github/workflows/vps-tools-ci.yml" 2>/dev/null | grep -v '/test-units.sh:' || true)
[[ -z "$leftover" ]] || tl_fail "app-check is still mentioned: $leftover"
# uv lives in a directory of this pipeline, never in a shared bin directory
# shellcheck disable=SC2016  # the text is searched literally
tl_has "$(cat "$here/pipeline-lib.sh")" 'PIPELINE_UV_DIR=${PIPELINE_UV_DIR:-/usr/local/lib/tipping-point-pipeline/uv}' "uv directory default"
# cache names
while IFS= read -r key; do
  tl_has "$(cat "$here/pipeline-lib.sh")" "$key" "pipeline-lib.sh lacks the cache name $key"
done < <(grep -o 'key: [a-z0-9-]*' "$wf/now.yml" | sed 's/key: //; s/-$//' | LC_ALL=C sort -u)
# the uv pin
pin=$(grep -h -A3 'setup-uv' "$wf/now.yml" | sed -n 's/.*version: "\(.*\)"/\1/p' | head -n 1)
tl_eq "$PIPELINE_UV_VERSION" "$pin" "runner uv pin equals the workflows"
for job in nisar validation reference; do
  tl_has "$(grep -A3 'setup-uv' "$wf/$job.yml")" "version: \"$pin\"" "$job.yml pins the same uv"
done

# --- 6. safety: scripts, units and examples (not the tests) -----------------------------------------
new_files="$here/pipeline-lib.sh $here/pipeline-run.sh $here/notify.sh $here/keepalive.sh $here/watchdog.sh"
new_files="$new_files $here/pipeline.env.example $here/notify.env.example $units/*"
for f in $new_files; do
  [[ "$f" == *.example || "$f" == *.timer ]] && continue # settings files and timers run nothing
  # the statement wraps over comment lines: join them before looking for the phrase
  flat=$(tr '\n' ' ' <"$f" | sed 's/# */ /g; s/  */ /g')
  tl_has "$flat" 'unrelated services of another company ("FE services")' "$(basename "$f") has no safety statement"
done
forbidden='docker +(system|builder|volume|container|image|network|buildx) +prune|docker +(rm|stop|kill|restart|rmi|start|pause|unpause|exec|cp|update|commit) .*(\$\(|`|xargs)|xargs +.*docker|docker +(ps|images|container +ls|volume +ls|network +ls|image +ls|volume +rm|network +rm|network +connect|compose)|docker +-H|apt(-get)? +|dnf +|yum +|pip3? +install|npm +(i|install) +-g|snap +|crontab|useradd|userdel|usermod|groupadd|visudo|chmod +-R|chown +-R +/ |rm +-[a-z]*r[a-z]* +/ |rm +-[a-z]*r[a-z]* +/\*|caddy|Caddyfile|docker\.sock|--privileged|--network|--volumes-from|-v +/'
for f in $new_files; do
  [[ "$f" == *.example ]] && continue
  # comment lines may name what is forbidden
  hits=$(grep -n -E "$forbidden" "$f" | grep -v -E '^[0-9]+:[[:space:]]*(#|;)' || true)
  [[ -z "$hits" ]] || tl_fail "forbidden operation in $(basename "$f"): $hits"
done
# the only unit control a script may run is `systemctl show`
for f in $new_files; do
  [[ "$f" == *.example || "$f" == *.service || "$f" == *.timer ]] && continue
  bad=$(grep -n 'systemctl' "$f" | grep -v -E '^[0-9]+:[[:space:]]*#|systemctl show |command -v systemctl' || true)
  [[ -z "$bad" ]] || tl_fail "$(basename "$f") controls units: $bad"
done
# the data side runs no docker command at all (comments may say so)
docker_calls=$(grep -n -w 'docker' "$here/pipeline-lib.sh" "$here/pipeline-run.sh" "$here/notify.sh" "$here/keepalive.sh" "$here/watchdog.sh" | grep -v -E ':[0-9]+:[[:space:]]*#' || true)
[[ -z "$docker_calls" ]] || tl_fail "a data-side script mentions docker outside a comment: $docker_calls"

# --- 6a. commands that are not on the unit's PATH ------------------------------------------------------
# The units set no PATH, so a command in a private directory is found only when the script adds that
# directory first. uv is the only such command: pipeline-run.sh and watchdog.sh add it through
# pl_uv_path before their first uv call, and keepalive.sh and notify.sh never call it.
# pipeline-run.sh: run_main (the entry of every mode, also --locked) adds the directory before it
# starts the job; watchdog.sh: wd_main adds it before its uv call
main_start=$(grep -n -E '^run_main\(\) \{' "$here/pipeline-run.sh" | head -n 1 | cut -d: -f1 || true)
helper=$(grep -n -E '^[[:space:]]*pl_uv_path$' "$here/pipeline-run.sh" | head -n 1 | cut -d: -f1 || true)
job_exec=$(grep -n -E '^[[:space:]]*exec timeout ' "$here/pipeline-run.sh" | head -n 1 | cut -d: -f1 || true)
[[ -n "$main_start" && -n "$helper" && -n "$job_exec" ]] || tl_fail "pipeline-run.sh: run_main, pl_uv_path or the job exec not found"
[[ "$helper" -gt "$main_start" && "$helper" -lt "$job_exec" ]] ||
  tl_fail "pipeline-run.sh must call pl_uv_path in run_main before it starts the job (main $main_start, helper $helper, exec $job_exec)"
first_call=$(grep -n -E '(^|[[:space:]])uv run[[:space:]]' "$here/watchdog.sh" | grep -v -E '^[0-9]+:[[:space:]]*#' | head -n 1 | cut -d: -f1 || true)
helper=$(grep -n -E '^[[:space:]]*pl_uv_path$' "$here/watchdog.sh" | head -n 1 | cut -d: -f1 || true)
[[ -n "$first_call" && -n "$helper" ]] || tl_fail "watchdog.sh must call pl_uv_path before uv (uv line '$first_call', helper line '$helper')"
[[ "$helper" -lt "$first_call" ]] || tl_fail "watchdog.sh calls uv on line $first_call before pl_uv_path on line $helper"
for f in keepalive.sh notify.sh; do
  hits=$(grep -n -w 'uv' "$here/$f" | grep -v -E '^[0-9]+:[[:space:]]*#' || true)
  [[ -z "$hits" ]] || tl_fail "$f runs uv, which is not on the unit's PATH: $hits"
done
# every script a unit starts is in this directory, so its dependencies are the ones tested above
for f in "$units"/*.service; do
  script=$(uget "$f" Service ExecStart | awk '{print $1}')
  [[ -f "$here/$(basename "$script")" ]] || tl_fail "$(basename "$f") starts $script, which is not in deploy/vps"
  # no unit starts a bare command that needs the search path
  case "$script" in /*) ;; *) tl_fail "$(basename "$f") ExecStart is not an absolute path: $script" ;; esac
done

# --- 6b. no app watcher and no sudo here (the app deploy is not automated on the VPS) ---------------
# The pipeline user has no sudo rule and calls no sudo. This test names the words it forbids.
for f in "$here"/* "$units"/*; do
  [[ -f "$f" ]] || continue
  # these two name the words they forbid
  case "$(basename "$f")" in test-units.sh | step_parity.py) continue ;; *) ;; esac
  hits=$(grep -n -i -E 'poller|sudo' "$f" || true)
  [[ -z "$hits" ]] || tl_fail "app watcher or sudo text in $(basename "$f"): $hits"
done
for f in app-poller.sh test-app-poller.sh app-poller.env.example; do
  [[ ! -e "$here/$f" ]] || tl_fail "$f is back"
done
for f in "$units"/*poller*; do
  [[ ! -e "$f" ]] || tl_fail "a poller unit is back: $f"
done
# the runbook section 7 has no app watcher, no sudoers file and no sudo rule
section7=$(sed -n '/^## 7\. Running without GitHub Actions/,$p' "$here/../../docs/DEPLOY-VPS.md")
tl_has "$section7" "## 7. Running without GitHub Actions" "found section 7 of the runbook"
hits=$(printf '%s\n' "$section7" | grep -n -i -E 'poller|sudo|visudo|NOPASSWD' || true)
[[ -z "$hits" ]] || tl_fail "section 7 of the runbook mentions the app watcher or a sudo command (the operator is root, there is no sudo): $hits"
tl_has "$section7" "NOT automated on the VPS" "section 7 says plainly that app deploys are not automated"

# --- 6c. the runbook commands work for a root shell, and no secret is typed on a command line ---------
# every shell block of section 7 is written to a file and must parse
blocks="$TL_TMP/blocks"
mkdir -p "$blocks"
printf '%s\n' "$section7" | awk -v dir="$blocks" '
  /^[ ]*```sh[ ]*$/ { n++; match($0, /^[ ]*/); ind = RLENGTH; inb = 1; file = sprintf("%s/block-%02d.sh", dir, n); next }
  /^[ ]*```[ ]*$/ { if (inb) { inb = 0; close(file) }; next }
  inb { line = $0; i = 0; while (i < ind && substr(line, 1, 1) == " ") { line = substr(line, 2); i++ }; print line > file }'
nblocks=$(find "$blocks" -name 'block-*.sh' | wc -l | tr -d ' ')
[[ "$nblocks" -ge 10 ]] || tl_fail "found only $nblocks shell blocks in section 7 (parser broken?)"
for f in "$blocks"/block-*.sh; do
  bash -n "$f" || tl_fail "a shell block of section 7 does not parse: $(head -n 3 "$f")"
done
allcode=$(cat "$blocks"/block-*.sh)
# no secret value or name=value on a command line; no direct call of the runner (it cannot see pipeline.env)
hits=$(printf '%s\n' "$allcode" | grep -n -E '(GH_TOKEN|EARTHDATA_TOKEN|GCP_SA_KEY_B64|NOTIFY_WEBHOOK_URL|PIPELINE_PUBLISH)=' | grep -v -E '^[0-9]+:[[:space:]]*#|nano /etc/tipping-point/pipeline.env' || true)
[[ -z "$hits" ]] || tl_fail "a runbook command carries a setting or secret on its command line: $hits"
hits=$(printf '%s\n' "$allcode" | grep -n -E 'pipeline-run\.sh +(--|now|nisar|validation|reference)' || true)
[[ -z "$hits" ]] || tl_fail "a runbook command starts pipeline-run.sh from the shell (use tipping-point-by-hand@): $hits"
# runuser is used in exactly one place, the as_pipeline function, which sets HOME and leaves /root
tl_eq "$(printf '%s\n' "$allcode" | grep -c 'runuser -u tipping-pipeline --')" 1 "runuser appears once, in as_pipeline"
tl_has "$allcode" "( cd / && runuser -u tipping-pipeline -- env HOME=/var/lib/tipping-point-pipeline" "as_pipeline leaves the working directory and sets HOME"
# the prerequisite check of the runbook covers every tool the runner demands (uv comes from step 3)
runner_tools=$(sed -n 's/^  for t in \(.*\); do$/\1/p' "$here/pipeline-run.sh" | head -n 1)
[[ -n "$runner_tools" ]] || tl_fail "run_check_tools list not found in pipeline-run.sh"
for t in $runner_tools; do
  [[ "$t" == uv ]] && continue
  tl_has "$(printf '%s\n' "$allcode" | grep '^for c in ')" " $t " "the runbook prerequisite check lacks $t"
  case "$t" in flock) pkg=util-linux ;; timeout) pkg=coreutils ;; *) pkg=$t ;; esac
  tl_has "$(printf '%s\n' "$allcode" | grep -A1 'apt-get install --no-upgrade')" " $pkg " "the runbook package command lacks the package of $t ($pkg)"
done
# the install order and the by-hand checks of the runbook
tl_has "$allcode" "apt-get install --no-upgrade --no-install-recommends -y" "the prerequisites are one additive command"
hits=$(printf '%s\n' "$allcode" | grep -n -E 'apt(-get)? +(upgrade|dist-upgrade|full-upgrade)' || true)
[[ -z "$hits" ]] || tl_fail "the runbook upgrades packages: $hits"
tl_has "$allcode" "systemctl start tipping-point-by-hand@preflight-now.service" "the preflight runs through systemd"
tl_has "$allcode" "systemctl start tipping-point-by-hand@dry-run-validation.service" "the dry run runs through systemd"
tl_has "$allcode" 'systemctl start "tipping-point-by-hand@restore-$RESTORE_SHA.service"' "the restore runs through systemd"
tl_has "$allcode" "systemctl start 'tipping-point-notify@tipping-point-notice-test.service.service'" "the test notice has its own instance"
tl_lacks "$allcode" "tipping-point-notify@tipping-point-now.service" "the test notice does not use a real unit name"
tl_has "$allcode" "ls-remote origin refs/heads/fallback" "the live fallback commit is recorded before the cutover"
tl_has "$allcode" "/root/fallback-before-vps.txt" "the recorded commit is kept in a file"
tl_has "$allcode" "PIPELINE_TIMEOUT_S=10800" "the cold run raises the run limit"
tl_has "$allcode" "TimeoutStartSec=14400" "and the outer limit of the unit with it"
# the four steps of the install in order: reviewed checkout, scripts, units, preflight
pos() { printf '%s\n' "$allcode" | grep -n -F -- "$1" | head -n 1 | cut -d: -f1; }
[[ "$(pos 'git clone --filter=blob:none --no-checkout')" -lt "$(pos 'install -o root -g root -m 0755 "deploy/vps/$f"')" ]] || tl_fail "the reviewed checkout comes after the script install"
[[ "$(pos 'install -o root -g root -m 0755 "deploy/vps/$f"')" -lt "$(pos 'install -o root -g root -m 0644 -t /etc/systemd/system')" ]] || tl_fail "scripts come after the units"
[[ "$(pos 'install -o root -g root -m 0644 -t /etc/systemd/system')" -lt "$(pos 'systemctl start tipping-point-by-hand@preflight-now.service')" ]] || tl_fail "the preflight comes before the units are installed"
# the runbook names the facts the review asked for
tl_has "$section7" "The token must never appear on a command line or in shell history" "token rule"
tl_has "$section7" "Do not run a data job and an app build at the same time" "no data job during an app build"
tl_has "$section7" "OOMScoreAdjust=500" "the OOM score is documented"
tl_has "$section7" "Optional for \`now\` and \`nisar\`" "the runbook says the Google key is optional for now and nisar"
tl_has "$section7" "\`now\` needs no Google key" "the runbook says why now needs no key"
tl_has "$(cat "$here/pipeline.env.example")" "OPTIONAL for now and nisar" "the env example says the Google key is optional for now and nisar"
tl_has "$section7" "PIPELINE_MIN_FREE_MB" "the disk guard is documented"
tl_has "$section7" "With no \`NOTIFY_WEBHOOK_URL\` the" "the silent VPS without a webhook is said"
tl_has "$section7" "\`app-keepalive.yml\` reads \`/api/health\` and nothing else" "what the keepalive workflow can and cannot see"
tl_has "$section7" "git -C /opt/tipping-point ls-remote origin refs/heads/fallback" "the fallback commit is recorded as the pipeline user"
tl_has "$section7" "git sparse-checkout set deploy docs" "the reviewed checkout recipe is in the runbook"
# the timers the runbook enables and stops are the timers there are
doc_timers=$(printf '%s\n' "$allcode" | grep -o 'tipping-point-[a-z-]*\.timer' | LC_ALL=C sort -u | tr '\n' ' ')
unit_timers=$(find "$units" -name '*.timer' | sed 's#.*/##' | LC_ALL=C sort -u | tr '\n' ' ')
tl_eq "$doc_timers" "$unit_timers" "the timers in the runbook commands are the timers in deploy/vps/systemd"

# ASCII only (hyphen, straight quotes) in every file of deploy/vps, and no tool or vendor names
tab=$(printf '\t')
for f in "$here"/* "$units"/*; do
  [[ -f "$f" && "$f" != *.pyc ]] || continue
  if LC_ALL=C grep -n "[^ -~$tab]" "$f" >/dev/null; then tl_fail "non-ASCII character in $(basename "$f"): $(LC_ALL=C grep -n "[^ -~$tab]" "$f" | head -n 2)"; fi
  if grep -n -i -w -E '[c]laude|[a]nthropic|[o]penai|[c]hatgpt|[c]odex|[c]opilot|[g]emini|[j]ev|[j]evmap|[t]ypesafe|[p]rwait|[a]ssistant|[g]enerated by|[c]o-authored' "$f" >/dev/null; then
    tl_fail "tool or vendor name in $(basename "$f")"
  fi
done

# --- 7. systemd-analyze, only when the host has it (not on macOS) --------------------------------------
if command -v systemd-analyze >/dev/null 2>&1; then
  vdir="$TL_TMP/verify"
  mkdir -p "$vdir"
  for f in "$units"/*; do
    sed -e "s#/usr/local/lib/tipping-point-pipeline/#$here/#" -e '/^EnvironmentFile=/d' "$f" >"$vdir/$(basename "$f")"
  done
  vout=$(cd "$vdir" && systemd-analyze verify ./*.service ./*.timer 2>&1 || true)
  if printf '%s\n' "$vout" | grep -E 'Unknown (key|section|lvalue)|Failed to parse|Invalid argument|Bad (unit|message)' >/dev/null; then
    tl_fail "systemd-analyze found a syntax problem: $vout"
  fi
  echo "systemd-analyze verify: ran (only syntax problems fail this test; output below)"
  printf '%s\n' "$vout" | head -n 20
else
  echo "systemd-analyze verify: skipped (not installed on this host)"
fi

echo "unit tests passed"
