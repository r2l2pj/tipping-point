#!/usr/bin/env bash
# Static checks of the systemd units and scripts in deploy/vps: unit shape,
# schedules and limits against the GitHub workflows they replace, parity of the
# job commands, secret names, cache keys and the uv pin, and the safety rules
# (no prune, no wildcard container operation, no unit control, ASCII only).
# No Docker, no network, no root. systemd-analyze runs only when it exists.
# shellcheck disable=SC2154  # tl_run sets out and rc
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
    tipping-point-notify@) ;;
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
# the units without secrets do not read the secrets file
for name in stale-alarm app-keepalive notify@; do
  case "$(uget "$units/tipping-point-$name.service" Service EnvironmentFile)" in
    /etc/tipping-point/pipeline.env | -/etc/tipping-point/pipeline.env) tl_fail "$name reads the secrets file" ;;
    *) ;;
  esac
done
# only the five job units read pipeline.env
tl_eq "$(grep -l 'pipeline\.env' "$units"/*.service | xargs -n1 basename | LC_ALL=C sort | tr '\n' ' ')" \
  "tipping-point-nisar.service tipping-point-now-backup.service tipping-point-now.service tipping-point-reference.service tipping-point-validation.service " \
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
tl_eq "$(grep -c 'timeout 12m uv run python -m live.nisar_stack' "$here/pipeline-run.sh")" 1 "nisar_stack step limit"
# the Now job must keep the local path, which writes no Google Earth Engine asset (without it
# gee/now_tpi.py deletes and re-exports 16 assets); the runbook's write-free trial rests on it
tl_eq "$(grep -c 'NOW_LOCAL=1 uv run python live/jobs.py now' "$here/pipeline-run.sh")" 1 "Now job runs with NOW_LOCAL=1"
tl_has "$(grep -A8 'Run now job' "$wf/now.yml")" 'NOW_LOCAL: "1"' "now.yml runs the Now job with NOW_LOCAL=1"
tl_has "$(grep -A3 'name: NISAR frame stack' "$wf/now.yml")" "timeout-minutes: 12" "nisar_stack step limit in now.yml"

# --- 5. parity with the workflows --------------------------------------------------------------------
# every python entry point a data workflow runs must also be run by the host runner, so a step
# added to a workflow cannot be forgotten on the host
for w in now.yml nisar.yml validation.yml reference.yml; do
  while read -r target; do
    [[ -n "$target" ]] || continue
    grep -q -F -- "$target" "$here/pipeline-run.sh" ||
      tl_fail "$w runs '$target' but pipeline-run.sh does not: add the step to the host runner"
  done < <(grep -o 'uv run python [^"]*' "$wf/$w" | sed -E 's/^uv run python (-m |-c )?//; s/ .*//' | LC_ALL=C sort -u)
done
# every `uv run python ...` command of the four data workflows is in the runner
count=0
for job in now nisar validation reference; do
  while IFS= read -r cmd; do
    cmd=${cmd%% &&}
    [[ -n "$cmd" ]] || continue
    count=$((count + 1))
    tl_has "$(cat "$here/pipeline-run.sh")" "$cmd" "runner lacks the command of $job.yml"
  done < <(sed -n 's/^ *\(run: \)\{0,1\} *\(uv run python .*\)$/\2/p' "$wf/$job.yml")
done
[[ "$count" -ge 15 ]] || tl_fail "found only $count uv commands in the workflows (parser broken?)"
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

# --- 6b. no app watcher and no sudo here (the app deploy is not automated on the VPS) ---------------
# The pipeline user has no sudo rule and calls no sudo. This test names the words it forbids.
for f in "$here"/* "$units"/*; do
  [[ -f "$f" ]] || continue
  case "$(basename "$f")" in test-units.sh) continue ;; *) ;; esac
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
hits=$(printf '%s\n' "$section7" | grep -n -i -E 'poller|sudoers|visudo|NOPASSWD' || true)
[[ -z "$hits" ]] || tl_fail "section 7 of the runbook mentions the app watcher or a sudo rule: $hits"
tl_has "$section7" "NOT automated on the VPS" "section 7 says plainly that app deploys are not automated"

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
