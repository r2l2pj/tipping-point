#!/usr/bin/env bash
# Helpers shared by the deploy/vps shell tests. Source it; it runs nothing.
# No Docker, no network, no root: commands are stubs on PATH, git talks to
# repositories created in a temporary directory, curl reads file:// URLs.
# Written to run on bash 3.2 (macOS) and bash 5 (CI, Ubuntu).

TL_SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

tl_fail() {
  echo "FAIL: $*" >&2
  exit 1
}

tl_eq() { # actual expected message
  [[ "$1" == "$2" ]] || tl_fail "$3: expected '$2', got '$1'"
}

tl_has() { # text pattern message  (fixed string)
  case "$1" in *"$2"*) ;; *) tl_fail "$3: '$2' not found in: $1" ;; esac
}

tl_lacks() { # text pattern message  (fixed string)
  case "$1" in *"$2"*) tl_fail "$3: '$2' unexpectedly found in: $1" ;; *) ;; esac
}

# tl_setup: temporary directory, PATH with stubs first, shims for flock/timeout
# when the host has none.
tl_setup() {
  TL_TMP=$(mktemp -d)
  TL_BIN="$TL_TMP/bin"
  mkdir -p "$TL_BIN"
  # shellcheck disable=SC2064
  trap "rm -rf -- '$TL_TMP'" EXIT
  local c
  for c in flock timeout; do
    if ! command -v "$c" >/dev/null 2>&1; then cp "$TL_SELF_DIR/test-shims/$c" "$TL_BIN/$c"; fi
  done
  # python 3.11 adds datetime.UTC; older interpreters get it here
  mkdir -p "$TL_TMP/pyshim"
  printf 'import datetime\nif not hasattr(datetime, "UTC"):\n    datetime.UTC = datetime.timezone.utc\n' \
    >"$TL_TMP/pyshim/sitecustomize.py"
  export PYTHONPATH="$TL_TMP/pyshim${PYTHONPATH:+:$PYTHONPATH}"
  export PATH="$TL_BIN:$PATH"
  # no user configuration: commits get their identity from -c (see tl_git)
  export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
  unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
}

# tl_stub NAME: the script text arrives on stdin and becomes $TL_BIN/NAME.
tl_stub() {
  cat >"$TL_BIN/$1"
  chmod 755 "$TL_BIN/$1"
}

# tl_run VAR_FOR_OUTPUT VAR_FOR_RC command...: run, capture stdout+stderr and rc
# (no errexit trouble, no subshell variable loss).
tl_run() {
  local _tl_ov=$1 _tl_rv=$2 _tl_out _tl_rc=0
  shift 2
  _tl_out=$("$@" 2>&1) || _tl_rc=$?
  printf -v "$_tl_ov" '%s' "$_tl_out"
  printf -v "$_tl_rv" '%s' "$_tl_rc"
}

# tl_rune VAR_FOR_OUTPUT VAR_FOR_RC command...: like tl_run, but errexit is ON
# inside the command, as in a script's main flow. (Bash switches errexit off for
# everything called from `||`, which hides a failing step in tl_run.)
tl_rune() {
  local _tl_ov=$1 _tl_rv=$2 _tl_out _tl_rc
  shift 2
  set +e
  _tl_out=$( ( set -eE; "$@" ) 2>&1 )
  _tl_rc=$?
  set -e
  printf -v "$_tl_ov" '%s' "$_tl_out"
  printf -v "$_tl_rv" '%s' "$_tl_rc"
}

# tl_git DIR args...: git in DIR with a fixed identity and no signing.
tl_git() {
  local dir=$1
  shift
  git -C "$dir" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false "$@"
}

# tl_make_origin DIR: a bare repository that serves partial clones, with main
# (a small tree with files the runner looks for) and an orphan fallback branch.
tl_make_origin() {
  local dir=$1 work="$1.work"
  git init -q --bare -b main "$dir"
  git -C "$dir" config uploadpack.allowFilter true
  git -C "$dir" config uploadpack.allowAnySHA1InWant true
  git init -q -b main "$work"
  mkdir -p "$work/live" "$work/pipeline/scripts" "$work/data" "$work/tools"
  echo "# stub" >"$work/live/jobs.py"
  : >"$work/live/nisar_next.py"
  for n in 13_response_dswx 14_response_nisar 15_response_summary 16_response_dist; do
    : >"$work/pipeline/scripts/$n.py"
  done
  echo '{"on": "main"}' >"$work/data/manifest.json"
  echo ".venv/" >"$work/.gitignore"
  echo "ignored/" >>"$work/.gitignore"
  tl_git "$work" add -A
  tl_git "$work" commit -q -m "main one"
  tl_git "$work" remote add origin "$dir"
  tl_git "$work" push -q origin main
  # fallback: one orphan commit with a published snapshot
  tl_git "$work" checkout -q --orphan fallback
  tl_git "$work" rm -rfq .
  mkdir -p "$work/data/now/history/run1" "$work/data/now/nisar_stack"
  echo '{"updated_at": "2026-11-15T00:00:00Z"}' >"$work/data/now/manifest.json"
  echo h1 >"$work/data/now/history/run1/summary.json"
  echo s1 >"$work/data/now/nisar_stack/frame.json"
  echo '{"on": "fallback"}' >"$work/data/manifest.json"
  tl_git "$work" add -A
  tl_git "$work" commit -q -m "fallback one"
  tl_git "$work" push -q origin fallback
  tl_git "$work" checkout -q main
}

# tl_zip PATH: a small valid .npz-style zip archive.
tl_zip() {
  mkdir -p "$(dirname "$1")"
  python3 -c 'import sys, zipfile; z = zipfile.ZipFile(sys.argv[1], "w"); z.writestr("a.npy", "1"); z.close()' "$1"
}

# tl_order FILE PATTERN...: every fixed-string pattern appears in FILE on a
# later line than the one before it.
tl_order() {
  local f=$1 prev=0 pat n
  shift
  for pat in "$@"; do
    n=$(grep -n -F -- "$pat" "$f" | awk -F: -v p="$prev" '$1 > p {print $1; exit}')
    [[ -n "$n" ]] || tl_fail "'$pat' not found after line $prev of $f"
    prev=$n
  done
}

# tl_count FILE PATTERN: number of lines containing the fixed string.
tl_count() {
  grep -c -F -- "$2" "$1" || true
}
