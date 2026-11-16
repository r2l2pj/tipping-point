#!/usr/bin/env bash
# Stale-data alarm for the published Now data: the second half of
# .github/workflows/now-watchdog.yml ("Fail when the published Now data is
# stale"). It reads data/now/manifest.json from the fallback branch with git
# (no CDN) and runs the same checker, `python -m live.staleness`. A stale or
# unreadable manifest exits 1, so the systemd unit fails and its OnFailure
# notice runs. See docs/DEPLOY-VPS.md, "Running without GitHub Actions".
#
# The first half of the workflow (dispatch now.yml when the last success is
# older than 3.5 h) is the backup timer: pipeline-run.sh --if-stale 12600 now.
#
#   watchdog.sh [MAX_AGE_S]        default $STALE_AFTER_S or 21600 (6 h)
#
# SAFETY: this host also runs unrelated services of another company ("FE
# services"), currently stopped. This script only reads. It fetches the
# fallback branch of the tipping-point repository into its own bare scratch
# repository under PIPELINE_STATE_DIR/watchdog.git (never the pipeline
# checkout, so it cannot disturb a running job) and starts no container, unit
# or other service.
set -Eeuo pipefail

PIPELINE_STATE_DIR=${PIPELINE_STATE_DIR:-/var/lib/tipping-point-pipeline}
PIPELINE_CHECKOUT=${PIPELINE_CHECKOUT:-/opt/tipping-point}
PIPELINE_REPO_URL=${PIPELINE_REPO_URL:-https://github.com/r2l2pj/tipping-point.git}
WATCHDOG_UV_PYTHON=${WATCHDOG_UV_PYTHON:-3.12}

wd_log() { echo "watchdog: $*" >&2; }

# wd_manifest: print the published Now manifest.
wd_manifest() {
  local repo="$PIPELINE_STATE_DIR/watchdog.git"
  if [[ ! -d "$repo" ]]; then
    git init -q --bare "$repo"
    git -C "$repo" remote add origin "$PIPELINE_REPO_URL"
    git -C "$repo" config remote.origin.promisor true
    git -C "$repo" config remote.origin.partialclonefilter blob:none
  fi
  [[ "$(git -C "$repo" config --get remote.origin.url)" == "$PIPELINE_REPO_URL" ]] ||
    { wd_log "$repo has another origin; refusing to use it"; return 1; }
  # one commit, no file contents: only the manifest blob is fetched below
  git -C "$repo" fetch -q --depth 1 --filter=blob:none origin fallback ||
    { wd_log "cannot fetch the fallback branch"; return 1; }
  git -C "$repo" show FETCH_HEAD:data/now/manifest.json
}

wd_main() {
  local max_age=${1:-${STALE_AFTER_S:-21600}}
  [[ "$max_age" =~ ^[0-9]+$ ]] || { echo "usage: watchdog.sh [MAX_AGE_S]" >&2; exit 2; }
  [[ -d "$PIPELINE_STATE_DIR" ]] || { wd_log "$PIPELINE_STATE_DIR does not exist"; exit 1; }
  export UV_CACHE_DIR=${UV_CACHE_DIR:-$PIPELINE_STATE_DIR/uv-cache}
  export UV_PYTHON_INSTALL_DIR=${UV_PYTHON_INSTALL_DIR:-$PIPELINE_STATE_DIR/uv-python}
  export GIT_TERMINAL_PROMPT=0
  local manifest
  manifest=$(mktemp "$PIPELINE_STATE_DIR/watchdog-manifest.XXXXXXXX")
  # shellcheck disable=SC2064  # expand the file name now
  trap "rm -f '$manifest'" EXIT
  if ! wd_manifest >"$manifest"; then
    echo "watchdog: STALE: cannot read data/now/manifest.json from the fallback branch" >&2
    exit 1
  fi
  # live/staleness.py needs Python 3.11+; uv supplies it, no project sync
  if (cd "$PIPELINE_CHECKOUT" && PYTHONPATH="$PIPELINE_CHECKOUT" \
    uv run --no-project --python "$WATCHDOG_UV_PYTHON" python -m live.staleness \
    --max-age-s "$max_age" "$manifest"); then
    :
  else
    echo "watchdog: STALE: the published Now data is older than ${max_age}s or unreadable (or the checker could not run)" >&2
    exit 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  wd_main "$@"
fi
