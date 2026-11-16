#!/usr/bin/env bash
# Shared logic of the tipping-point data jobs (static inputs, rolling state,
# fallback restores, checkout handling). One home for logic that the GitHub
# workflows and the VPS runner both need. See docs/DEPLOY-VPS.md, section
# "Running without GitHub Actions".
#
#   sourced by:  pipeline-run.sh, watchdog.sh and the tests
#   run by:      bash deploy/vps/pipeline-lib.sh <command> [args]   (workflows)
#                pipeline-lib.sh init-checkout [DIR]               (VPS install)
#
# SAFETY: this host also runs unrelated services of another company ("FE
# services"), currently stopped. Nothing here starts, stops, restarts, removes
# or reconfigures a container, volume, network, database, Caddy route, systemd
# unit, cron entry, user or file that does not belong to tipping-point, and it
# reads no data of any of them. It never runs a prune command and never uses a
# wildcard container or volume operation. Its only write targets are the
# pipeline checkout (PIPELINE_CHECKOUT, default /opt/tipping-point), the
# pipeline state directory (default /var/lib/tipping-point-pipeline) and, on a
# GitHub runner, the runner's own checkout. It runs no docker command at all.
set -Eeuo pipefail

PIPELINE_REPO_URL=${PIPELINE_REPO_URL:-https://github.com/r2l2pj/tipping-point.git}
# Used only with PIPELINE_PUSH_AUTH=ssh (the deploy key alternative). The default,
# token push, goes to the HTTPS origin URL above.
PIPELINE_PUSH_URL=${PIPELINE_PUSH_URL:-git@github.com:r2l2pj/tipping-point.git}
# Same default as live/static_fetch.py and the workflows.
STATIC_RELEASE_BASE=${STATIC_RELEASE_BASE:-https://github.com/r2l2pj/tipping-point/releases/download/static-v1}
# Pinned like .github/workflows/now.yml (astral-sh/setup-uv version).
# shellcheck disable=SC2034  # read by pipeline-run.sh
PIPELINE_UV_VERSION=0.13.8
# The runbook installs uv here, a directory that belongs to this pipeline alone, and
# nothing is written to /usr/local/bin. The runner puts it first on PATH for its own
# commands; no other program on the host sees it.
PIPELINE_UV_DIR=${PIPELINE_UV_DIR:-/usr/local/lib/tipping-point-pipeline/uv}

pl_log() { echo "pipeline: $*" >&2; }

pl_die() {
  echo "pipeline: ERROR: $*" >&2
  exit 1
}

# GitHub annotation on a runner, plain text elsewhere.
pl_notice() {
  if [[ "${GITHUB_ACTIONS:-}" == true ]]; then echo "::notice::$*"; else echo "NOTICE: $*" >&2; fi
}

pl_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# pl_copy_atomic SRC DEST: copy through DEST.part, then rename; never leaves a
# half-written DEST.
pl_copy_atomic() {
  if cp -p "$1" "$2.part"; then
    mv -f "$2.part" "$2"
  else
    rm -f "$2.part"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# static inputs (the static-v1 Release assets, sha256-checked)
#
# One line per asset: <release asset>|<destination under the checkout>|<sha256>.
# Lines starting with # are comments. Cache names (the directory under the
# state dir) play the role of the actions/cache keys in now.yml: bump the name
# when the workflow bumps its key.

pl_static_table() {
  case "${1:-}" in
    response)
      cat <<'TABLE'
gpw_popcount_2020_30sec.tif|data/now/static/gpw/gpw_popcount_2020_30sec.tif|76dea7254b5b83147b9791fd2017df73ae8fb511eadaf00701b7dfa94dcbc203
lc_type1_2025.tif|data/now/static/mcd12q1/lc_type1_2025.tif|5606cd09ecf8ca00df62c1e9b39a89a5e1c4a9595c8f853555e324c497a1b68b
gsw_occurrence_2021.tif|data/now/static/jrc_gsw/gsw_occurrence_2021.tif|a31b98c96856b1541b7a998bbbbcc00326d6bb61cdb8055245199d3ac9cf3c04
gsw_seasonality_2021.tif|data/now/static/jrc_gsw/gsw_seasonality_2021.tif|30cda153f75a687afd2421ef05337a5f392931a575431377bcf3ead65215c307
hand_m.tif|data/static/south_brazil/terrain/hand_m.tif|5445278f146e65dca6cfae21bf6d31a97dbaf79cac3fb8230a22eb39635a993d
# IBGE API is unreachable from GitHub runners: static copy
municipalities_now.geojson|data/now/static/ibge/municipalities_now.geojson|7a003eb3aac3e52985152134c32bd4a35398ae015e8c58a220a7fb1593b33d1f
# R2 90 m HAND (falls back to hand_m.tif) and R4 steep-slope mask
hand_90m.tif|data/static/south_brazil/terrain/hand_90m.tif|40bc5963e6fc2d727364f8b198837402098c965d545345c478705fd2c5f7bca1
steep15_1as.tif|data/now/static/nasadem/steep15_1as.tif|32bab1f708743854997773bef0d345f9fbaa2eee63fd20074c46ad9dc29711c9
TABLE
      ;;
    p-trigger)
      cat <<'TABLE'
# lead-ml real model, ML v2 (L40, 2026-11-15): T_ML retrained without RS (L32)
p_trigger_xgb_v2.json|ml/models/p_trigger_xgb.json|a342d61698d60590d3e0e4d8b19fb0a0daf56a938bcf92958311fb2493e3b595
p_trigger_xgb_v2.meta.json|ml/models/p_trigger_xgb.meta.json|6b84d5729bf3daf7730770ce17d563d5ff2331ce4a718e2e9290265bc3c79f31
ml_static_now_v2.tif|data/static/south_brazil/ml/ml_static_now.tif|a0070babee891e38e69d80ce148ec13884c51ab2e87abca24f0b460786b5cda7
TABLE
      ;;
    *) pl_die "unknown static input set: ${1:-}" ;;
  esac
}

# Cache directory names, one per actions/cache key of now.yml.
# shellcheck disable=SC2034  # read by pipeline-run.sh
PL_CACHE_STATIC_RESPONSE=static-v1-response-4
# shellcheck disable=SC2034  # read by pipeline-run.sh
PL_CACHE_STATIC_PTRIGGER=static-v1-p-trigger
# shellcheck disable=SC2034  # read by pipeline-run.sh
PL_CACHE_CLIM=clim-imerg-0p1-v1
# shellcheck disable=SC2034  # read by pipeline-run.sh
PL_CACHE_DSWX=dswx-state
# shellcheck disable=SC2034  # read by pipeline-run.sh
PL_CACHE_PREV_CLASSES=prev-classes-v2

# pl_static_get ASSET DEST SHA256 [CACHE_DIR]   (DEST is relative to the cwd)
# Same rule as the workflow: download only when DEST is missing or empty, then
# check the sha256 either way. Callers may run this under `||`, where errexit is
# off, so every failure path returns explicitly. With a cache directory, a missing DEST is first
# restored from it, and a verified file is saved into it.
pl_static_get() {
  local asset=$1 dest=$2 sha=$3 cache=${4:-} actual from_cache=0 downloaded=0
  if [[ ! -s "$dest" && -n "$cache" && -s "$cache/$dest" ]]; then
    mkdir -p "$(dirname "$dest")"
    pl_copy_atomic "$cache/$dest" "$dest" && from_cache=1
  fi
  if [[ ! -s "$dest" ]]; then
    mkdir -p "$(dirname "$dest")"
    if ! curl -fsSL --retry 3 -o "$dest.part" "$STATIC_RELEASE_BASE/$asset"; then
      rm -f "$dest.part"
      pl_log "download failed: $asset"
      return 1
    fi
    mv -f "$dest.part" "$dest"
    downloaded=1
  fi
  actual=$(pl_sha256 "$dest")
  if [[ "$actual" != "$sha" ]]; then
    pl_log "$dest: sha256 mismatch (expected $sha, got $actual)"
    if (( from_cache )); then
      # a damaged cache copy: drop it and fetch a fresh one once
      rm -f "$dest" "$cache/$dest"
      pl_static_get "$asset" "$dest" "$sha" "$cache"
      return
    fi
    if (( downloaded )); then rm -f "$dest"; fi
    return 1
  fi
  echo "$dest: OK"
  if [[ -n "$cache" && ! -s "$cache/$dest" ]]; then
    mkdir -p "$(dirname "$cache/$dest")"
    pl_copy_atomic "$dest" "$cache/$dest" ||
      pl_log "cannot cache $dest (the file itself is fine)"
  fi
  return 0
}

# pl_static_inputs SET [CACHE_DIR]   (run in the checkout root)
pl_static_inputs() {
  local set=${1:-} cache=${2:-} asset dest sha table
  table=$(pl_static_table "$set")
  while IFS='|' read -r asset dest sha; do
    [[ -n "$asset" && "$asset" != \#* ]] || continue
    pl_static_get "$asset" "$dest" "$sha" "$cache" || return 1
  done <<<"$table"
}

# ---------------------------------------------------------------------------
# rolling state and cached files (what actions/cache holds in now.yml)

# pl_valid_zip FILE: an .npz is a zip archive; refuse a truncated one.
pl_valid_zip() {
  python3 - "$1" <<'PY'
import sys
import zipfile

try:
    with zipfile.ZipFile(sys.argv[1]) as z:
        sys.exit(0 if z.testzip() is None else 1)
except (OSError, zipfile.BadZipFile):
    sys.exit(1)
PY
}

# pl_cache_restore ROOT CACHE_DIR REL MODE
# MODE replace: the cached copy always wins (rolling state).
# MODE keep:    only when REL is missing (immutable inputs).
pl_cache_restore() {
  local root=$1 cdir=$2 rel=$3 mode=$4
  if [[ ! -s "$cdir/$rel" ]]; then
    pl_log "no cached $rel in $cdir (first run, or the cache name was bumped)"
    return 0
  fi
  if [[ "$mode" == keep && -s "$root/$rel" ]]; then return 0; fi
  mkdir -p "$(dirname "$root/$rel")"
  pl_copy_atomic "$cdir/$rel" "$root/$rel" ||
    { pl_log "cannot restore $rel from $cdir"; return 1; }
  pl_log "restored $rel from $cdir"
}

# pl_cache_save ROOT CACHE_DIR REL KIND     KIND npz: refuse a truncated archive
pl_cache_save() {
  local root=$1 cdir=$2 rel=$3 kind=${4:-file} src
  src="$root/$rel"
  if [[ ! -f "$src" || -L "$src" || ! -s "$src" ]]; then
    pl_log "nothing to save: $rel"
    return 0
  fi
  if [[ "$kind" == npz ]] && ! pl_valid_zip "$src"; then
    pl_log "not saving $rel: it is not a complete .npz archive; the previous copy stays"
    return 1
  fi
  mkdir -p "$(dirname "$cdir/$rel")"
  pl_copy_atomic "$src" "$cdir/$rel" ||
    { pl_log "cannot save $rel to $cdir"; return 1; }
  pl_log "saved $rel to $cdir"
}

# ---------------------------------------------------------------------------
# restores from the published fallback branch (run in the checkout root)

# pl_remote_fallback_state: 0 the branch exists on origin, 2 it is really absent,
# 1 cannot tell. git ls-remote --exit-code answers 2 only when the server replied
# and has no such branch; any other failure (network, credentials) is "cannot tell".
pl_remote_fallback_state() {
  local rc=0
  git ls-remote --exit-code origin refs/heads/fallback >/dev/null 2>&1 || rc=$?
  case $rc in
    0) return 0 ;;
    2) return 2 ;;
    *) return 1 ;;
  esac
}

# pl_restore_fallback PATH...   run in the checkout root.
# A branch or a path that does not exist yet is a notice (exit 0): the first run
# starts the history. A fetch or an extraction that fails while the branch exists,
# or while that cannot be told, exits 1 and says so. The callers must not go on to
# build and publish a snapshot on top of an empty restore (the caller steps stop
# on a non-zero exit: bash -e in the workflows, `||`/`&&` in pipeline-run.sh).
pl_restore_fallback() {
  local p depth="" rc=0 state=0 listing
  if [[ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo false)" == true ]]; then
    depth=--depth=1
  fi
  # shellcheck disable=SC2086  # depth is empty or one fixed option
  if git fetch -q $depth origin fallback; then
    for p in "$@"; do
      if ! listing=$(git ls-tree FETCH_HEAD -- "$p"); then
        pl_log "ERROR: cannot read $p on the fetched fallback branch"
        rc=1
      elif [[ -z "$listing" ]]; then
        pl_notice "no $p on fallback yet - starts here"
      elif ! git archive FETCH_HEAD "$p" | tar -x; then
        pl_log "ERROR: cannot extract $p from the fallback branch"
        rc=1
      fi
    done
    return "$rc"
  fi
  pl_remote_fallback_state || state=$?
  if (( state == 2 )); then
    pl_notice "no fallback branch yet - $* start here"
    return 0
  fi
  pl_log "ERROR: cannot fetch the fallback branch (it exists, or that cannot be told); $* not restored"
  return 1
}

# pl_prev_sqlite   start the sqlite build from the published database so its
# 'runs' table keeps one row per Now run (fallback is a single orphan commit)
pl_prev_sqlite() {
  local slug=${GITHUB_REPOSITORY:-r2l2pj/tipping-point}
  local base=${PIPELINE_RAW_BASE:-https://raw.githubusercontent.com}
  mkdir -p data
  if curl -fsSL -o data/tipping_point.sqlite.part "$base/$slug/fallback/data/tipping_point.sqlite"; then
    mv -f data/tipping_point.sqlite.part data/tipping_point.sqlite
  else
    rm -f data/tipping_point.sqlite.part
    echo "no previous sqlite on fallback - runs history starts here"
  fi
}

# ---------------------------------------------------------------------------
# the pipeline checkout (VPS only)

# pl_validate_checkout REPO: refuse anything that is not our own blobless,
# full-tree clone. The checkout is reset and cleaned on every run, so the
# target must be proven before git runs there.
pl_validate_checkout() {
  local repo=${1:-} top origin
  [[ "$repo" == /* && "$repo" != / ]] || pl_die "checkout path must be an absolute path other than /: '$repo'"
  [[ -d "$repo/.git" ]] || pl_die "$repo is not a git checkout (run pipeline-lib.sh init-checkout)"
  [[ -O "$repo" ]] || pl_die "$repo is not owned by $(id -un); refusing to modify it"
  top=$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null || true)
  [[ "$top" == "$(cd "$repo" && pwd -P)" ]] || pl_die "$repo is not the top of a git checkout"
  origin=$(git -C "$repo" config --get remote.origin.url || true)
  [[ "$origin" == "$PIPELINE_REPO_URL" ]] ||
    pl_die "$repo has origin '$origin', expected $PIPELINE_REPO_URL; refusing to modify it"
  if [[ "$(git -C "$repo" config --get core.sparseCheckout || true)" == true ]]; then
    pl_die "$repo is a sparse checkout; the data snapshot needs the full tree"
  fi
  git -C "$repo" config --get-all remote.origin.fetch | grep -F 'refs/remotes/origin/fallback' >/dev/null ||
    pl_die "$repo does not fetch origin/fallback (a publish would skip the drop guard); run init-checkout"
  [[ -z "$(git -C "$repo" config --get-all remote.origin.pushurl || true)" ]] ||
    pl_die "$repo has a remote.origin.pushurl; the runner sets the push URL itself, per push mode (run init-checkout)"
}

# pl_init_checkout REPO: create the blobless clone, or re-apply its settings.
pl_init_checkout() {
  local repo=${1:-} fetched=0
  [[ "$repo" == /* && "$repo" != / ]] || pl_die "checkout path must be an absolute path other than /: '$repo'"
  if [[ -e "$repo/.git" ]]; then
    [[ "$(git -C "$repo" config --get remote.origin.url || true)" == "$PIPELINE_REPO_URL" ]] ||
      pl_die "$repo exists with another origin; refusing to touch it"
  else
    mkdir -p "$repo"
    [[ -z "$(ls -A "$repo")" ]] || pl_die "$repo exists and is not empty; refusing to use it"
    git init -q "$repo"
    git -C "$repo" remote add origin "$PIPELINE_REPO_URL"
    fetched=1
  fi
  git -C "$repo" config remote.origin.promisor true
  git -C "$repo" config remote.origin.partialclonefilter blob:none
  git -C "$repo" config --replace-all remote.origin.fetch '+refs/heads/main:refs/remotes/origin/main'
  git -C "$repo" config --add remote.origin.fetch '+refs/heads/fallback:refs/remotes/origin/fallback'
  # The push URL and credential are given per run, through the environment of the
  # publish step (pipeline-run.sh), so nothing about them is stored here.
  git -C "$repo" config --unset-all remote.origin.pushurl || true
  git -C "$repo" config core.logAllRefUpdates false
  if (( fetched )); then
    git -C "$repo" fetch -q --filter=blob:none origin '+refs/heads/main:refs/remotes/origin/main'
    git -C "$repo" checkout -q --detach origin/main
  fi
  pl_validate_checkout "$repo"
  pl_log "checkout ready: $repo"
}

# pl_scratch_clean: remove what an interrupted publish left in the scratch
# directory, <state dir>/tmp (publish_fallback.py makes its worktree there, through
# TMPDIR). The directory comes from the state dir, not from the caller's
# environment, and only fallback-* directories directly inside it are removed. A
# state dir whose tmp resolves outside it (a symlink) is left alone.
pl_scratch_clean() {
  local state=${PIPELINE_STATE_DIR:-} real_state scratch d
  [[ "$state" == /* && "$state" != / && -d "$state/tmp" ]] || return 0
  real_state=$(cd "$state" && pwd -P)
  scratch=$(cd "$state/tmp" && pwd -P)
  if [[ "$scratch" != "$real_state/tmp" ]]; then
    pl_log "WARNING: $state/tmp resolves to $scratch, outside the state directory; not cleaning it"
    return 0
  fi
  while IFS= read -r d; do
    [[ "$d" == "$scratch"/fallback-* && -d "$d" && ! -L "$d" ]] || continue
    rm -rf -- "$d"
  done < <(find "$scratch" -maxdepth 1 -type d -name 'fallback-*')
}

# pl_checkout_update REPO [BRANCH]: the "actions/checkout" of the VPS. Resets
# the tree to origin/BRANCH, removes everything untracked or ignored (state
# lives in the state directory, not here), and clears what an interrupted
# publish may have left behind. The remote-tracking ref of fallback is deleted so
# that a failed fetch can never be mistaken for "the branch is what I fetched
# last time".
pl_checkout_update() {
  local repo=$1 branch=${2:-main} wt
  pl_validate_checkout "$repo"
  while IFS= read -r wt; do
    [[ -n "$wt" ]] || continue
    git -C "$repo" worktree remove --force "$wt" || true
  done < <(git -C "$repo" worktree list --porcelain | sed -n 's/^worktree //p' | tail -n +2)
  git -C "$repo" worktree prune
  pl_scratch_clean
  git -C "$repo" update-ref -d refs/remotes/origin/fallback || true
  git -C "$repo" fetch -q origin "+refs/heads/$branch:refs/remotes/origin/$branch"
  git -C "$repo" reset -q --hard
  git -C "$repo" clean -q -f -f -d -x
  git -C "$repo" checkout -q --detach "origin/$branch"
  git -C "$repo" reset -q --hard
  git -C "$repo" clean -q -f -f -d -x
  git -C "$repo" branch -D fallback >/dev/null 2>&1 || true
  pl_log "checkout at $(git -C "$repo" rev-parse --short=12 HEAD) (origin/$branch)"
}

# pl_free_mb PATH: free space in MB on the filesystem holding PATH.
pl_free_mb() {
  df -Pm "$1" | awk 'NR==2 {print $4}'
}

# pl_workflow_state NAME: the state of a GitHub workflow of this repository
# (active, disabled_manually, ...), read without credentials; "unknown" when
# GitHub cannot be asked. A public-repository read, nothing is changed.
pl_workflow_state() {
  local slug=${GITHUB_REPOSITORY:-r2l2pj/tipping-point}
  local base=${PIPELINE_GITHUB_API:-https://api.github.com} state
  state=$(curl -fsS --max-time 20 -H 'Accept: application/vnd.github+json' \
    "$base/repos/$slug/actions/workflows/$1" 2>/dev/null |
    sed -n 's/.*"state": *"\([a-z_]*\)".*/\1/p' | head -n 1 || true)
  echo "${state:-unknown}"
}

pl_main() {
  case "${1:-}" in
    static-inputs)
      shift
      pl_static_inputs "$@"
      ;;
    restore-fallback)
      shift
      pl_restore_fallback "$@"
      ;;
    prev-sqlite) pl_prev_sqlite ;;
    init-checkout)
      pl_init_checkout "${2:-${PIPELINE_CHECKOUT:-/opt/tipping-point}}"
      ;;
    *)
      echo "usage: pipeline-lib.sh static-inputs <response|p-trigger> [CACHE_DIR]" >&2
      echo "       pipeline-lib.sh restore-fallback <path>..." >&2
      echo "       pipeline-lib.sh prev-sqlite" >&2
      echo "       pipeline-lib.sh init-checkout [DIR]" >&2
      exit 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  pl_main "$@"
fi
