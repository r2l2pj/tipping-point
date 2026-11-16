#!/usr/bin/env bash
# Tests for deploy-app.sh. No Docker and no network: the script is sourced and
# docker is a stub; git runs against a repository created here.
set -euo pipefail
script="$(cd "$(dirname "$0")" && pwd)/deploy-app.sh"
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# --- argument and privilege checks (the script run as a program) -------------
for bad in invalid 000000000000000000000000000000000000000g; do
  if bash "$script" "$bad" >/dev/null 2>&1; then fail "accepted invalid SHA: $bad"; fi
done
if bash "$script" >/dev/null 2>&1; then fail "accepted missing SHA"; fi
# A well-formed SHA must still be rejected when not invoked as root.
if [[ "$EUID" -ne 0 ]] && bash "$script" 0000000000000000000000000000000000000000 >/dev/null 2>&1; then
  fail "allowed an unprivileged deploy"
fi
bash -n "$script"

# --- functions (the script sourced; main does not run) -----------------------
# shellcheck source=deploy/vps/deploy-app.sh
source "$script"
trap - ERR
set +E

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
g() { git -C "$tmp/src" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false "$@"; }
git init -q -b main "$tmp/src"
g config uploadpack.allowFilter true
g config uploadpack.allowAnySHA1InWant true
mkdir -p "$tmp/src/app" "$tmp/src/data"
echo a >"$tmp/src/app/a.txt"
echo big >"$tmp/src/data/big.tif"
echo FROM >"$tmp/src/Dockerfile"
echo '**' >"$tmp/src/.dockerignore"
g add -A && g commit -q -m one
c1=$(g rev-parse HEAD)
echo b >"$tmp/src/app/a.txt" && g commit -q -am two
c2=$(g rev-parse HEAD)
echo c >"$tmp/src/data/big.tif" && g commit -q -am "three: data only, never runs app-ci"
c3=$(g rev-parse HEAD)
g switch -q -c side "$c1"
echo x >"$tmp/src/app/a.txt" && g commit -q -am side
side=$(g rev-parse HEAD)
g switch -q main
repo_url="file://$tmp/src"

# 1. A tested commit deploys although main has moved on (c3 is newer than c2),
#    and only the build inputs are checked out.
fetch_source "$c2" "$tmp/r1" || fail "fetch_source refused a commit that is on main"
[[ "$(git -C "$tmp/r1" rev-parse HEAD)" == "$c2" ]] || fail "wrong commit checked out"
[[ "$(cat "$tmp/r1/app/a.txt")" == b ]] || fail "app/ not checked out at the commit"
[[ -f "$tmp/r1/Dockerfile" && -f "$tmp/r1/.dockerignore" ]] || fail "build inputs missing"
[[ ! -e "$tmp/r1/data" ]] || fail "data/ was checked out; the checkout must stay sparse"

# 2. A commit that is not on main is refused.
if fetch_source "$side" "$tmp/r2" 2>/dev/null; then fail "deployed a commit that is not on main"; fi
if fetch_source 0000000000000000000000000000000000000001 "$tmp/r3" 2>/dev/null; then
  fail "deployed an unknown commit"
fi

# 3. Order against the running container's image tag.
running=""
docker() {
  case "$1 $2" in
    "inspect -f") echo "$running" ;;
    "image ls") printf '%s\n' "${images[@]}" ;;
    "image rm") removed+=("$3") ;;
    *) fail "unexpected docker call: $*" ;;
  esac
}
order() {
  running=$1
  deploy_order "$2" "$tmp/r1"
}
[[ "$(order tipping-point-app "$c2")" == deploy ]] || fail "hand-started container must be replaced"
[[ "$(order "tipping-point-app:$c1" "$c2")" == deploy ]] || fail "newer commit must deploy"
[[ "$(order "tipping-point-app:$c2" "$c2")" == same ]] || fail "same commit must be a no-op"
[[ "$(order "tipping-point-app:$c3" "$c2")" == older ]] || fail "older commit must not replace a newer one"
[[ "$(order "tipping-point-app:$side" "$c2")" == deploy ]] || fail "running commit off main must be replaced"

# 4. Pruning removes only earlier commit-tagged images of this app.
images=("tipping-point-app:$c1" "tipping-point-app:$c2" "tipping-point-app:latest" "tipping-point-app:<none>")
removed=()
prune_images "tipping-point-app:$c2"
[[ "${removed[*]}" == "tipping-point-app:$c1" ]] || fail "pruned the wrong images: ${removed[*]}"

echo "deploy-app tests passed"
