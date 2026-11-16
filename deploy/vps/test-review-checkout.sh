#!/usr/bin/env bash
# The "reviewed checkout" recipe of the runbook (docs/DEPLOY-VPS.md, 7.3 step 0), run against a
# local repository: the commands are read out of the runbook, so the text and the test cannot
# drift apart. The recipe must give exactly the named commit, with deploy/ and docs/ (and the
# top-level files) and without the data tree, and without downloading the contents of the data
# files. A later commit must not change what is installed.
# No network, no root. Needs git with partial clone support (2.27 or newer).
# shellcheck disable=SC2016  # the runbook text is searched literally, $ signs included
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
doc="$here/../../docs/DEPLOY-VPS.md"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup

# --- the recipe, as the runbook gives it ------------------------------------------------------------
section7=$(sed -n '/^## 7\. Running without GitHub Actions/,$p' "$doc")
recipe=$(printf '%s\n' "$section7" | grep -E '^(git clone --filter=blob:none --no-checkout |cd "\$REVIEW_DIR"$|git sparse-checkout set |git checkout --detach )' || true)
tl_eq "$(printf '%s\n' "$recipe" | grep -c .)" 4 "the runbook has the four recipe commands: $recipe"
tl_has "$recipe" 'git clone --filter=blob:none --no-checkout "$REVIEW_URL" "$REVIEW_DIR"' "clone without contents and without checkout"
tl_has "$recipe" 'git sparse-checkout set deploy docs' "sparse checkout of deploy and docs"
tl_has "$recipe" 'git checkout --detach "$REVIEWED_SHA"' "the named commit is checked out"
tl_has "$section7" 'REVIEW_URL=https://github.com/r2l2pj/tipping-point.git' "the recipe clones the tipping-point repository"
tl_has "$section7" '[ "$(git rev-parse HEAD)" = "$REVIEWED_SHA" ]' "the recipe proves HEAD is the named commit"

# --- an origin that serves partial clones: two commits, a data tree in each -----------------------
origin="$TL_TMP/origin"
git init -q -b main "$origin"
git -C "$origin" config uploadpack.allowFilter true
git -C "$origin" config uploadpack.allowAnySHA1InWant true
mkdir -p "$origin/deploy/vps" "$origin/docs" "$origin/data/now" "$origin/live"
echo "first" >"$origin/deploy/vps/pipeline-run.sh"
echo "docs" >"$origin/docs/DEPLOY-VPS.md"
echo "top" >"$origin/README.md"
echo "job" >"$origin/live/jobs.py"
# a "data file" large enough to tell whether its contents were downloaded
head -c 300000 /dev/zero | tr '\0' 'd' >"$origin/data/now/big.bin"
tl_git "$origin" add -A
tl_git "$origin" commit -q -m one
sha1=$(git -C "$origin" rev-parse HEAD)
bigblob=$(git -C "$origin" rev-parse "HEAD:data/now/big.bin")
echo "second" >"$origin/deploy/vps/pipeline-run.sh"
echo "later" >"$origin/deploy/vps/later.sh"
tl_git "$origin" add -A
tl_git "$origin" commit -q -m two
sha2=$(git -C "$origin" rev-parse HEAD)
[[ "$sha1" != "$sha2" ]] || tl_fail "test origin has one commit"

# --- run the recipe lines from the runbook ---------------------------------------------------------
REVIEW_URL="file://$origin"
REVIEW_DIR="$TL_TMP/reviewed"
REVIEWED_SHA=$sha1
export REVIEW_URL REVIEW_DIR REVIEWED_SHA
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  # each line is a plain command of the runbook; "cd" has to run in this shell
  eval "$line" >/dev/null 2>&1 || tl_fail "recipe line failed: $line"
done <<<"$recipe"

tl_eq "$(git rev-parse HEAD)" "$sha1" "HEAD is the named commit, not the newest one"
tl_eq "$(cat deploy/vps/pipeline-run.sh)" first "the files are those of the named commit"
[[ ! -e deploy/vps/later.sh ]] || tl_fail "a later commit leaked into the checkout"
[[ -f docs/DEPLOY-VPS.md && -f README.md ]] || tl_fail "docs and the top-level files are missing"
[[ ! -e data ]] || tl_fail "the data tree is checked out"
[[ ! -e live ]] || tl_fail "live/ is checked out (the recipe asks for deploy and docs only)"
tl_eq "$(git sparse-checkout list | LC_ALL=C sort | tr '\n' ' ')" "deploy docs " "sparse checkout list"
# the contents of the data file were not downloaded: the blob is still missing here
missing=$(git rev-list --objects --all --missing=print | grep '^?' || true)
tl_has "$missing" "?$bigblob" "the data file contents were not downloaded"
pack_kb=$(git count-objects -v | sed -n 's/^size-pack: //p')
[[ "$pack_kb" -lt 200 ]] || tl_fail "the clone holds ${pack_kb} KB of objects: the data file was downloaded"
# a commit that does not exist is a loud failure, not a silent checkout of something else
if git checkout --detach 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then tl_fail "an unknown commit was accepted"; fi
tl_eq "$(git rev-parse HEAD)" "$sha1" "a refused checkout leaves HEAD where it was"
# the placeholder of the runbook is refused the same way
if git checkout --detach PASTE_THE_40_HEX_COMMIT_HERE >/dev/null 2>&1; then tl_fail "the placeholder was accepted"; fi
tl_has "$section7" 'REVIEWED_SHA=PASTE_THE_40_HEX_COMMIT_HERE' "the runbook placeholder is the one tested"

echo "review checkout tests passed"
