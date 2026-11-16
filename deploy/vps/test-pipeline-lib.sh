#!/usr/bin/env bash
# Tests for pipeline-lib.sh: static inputs, state restore and save, restores from
# the fallback branch, the pipeline checkout. No Docker, no network, no root.
# shellcheck disable=SC2154,SC2329  # tl_run sets out and rc; stubs are called by the sourced code
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup
# shellcheck source=deploy/vps/pipeline-lib.sh
source "$here/pipeline-lib.sh"
trap - ERR
set +E

export PIPELINE_MIN_FREE_MB=1

# --- 1. the static table ------------------------------------------------------
resp=$(pl_static_table response)
ptrig=$(pl_static_table p-trigger)
rows_resp=$(printf '%s\n' "$resp" | grep -vc '^#' || true)
rows_pt=$(printf '%s\n' "$ptrig" | grep -vc '^#' || true)
tl_eq "$rows_resp" 8 "response asset count"
tl_eq "$rows_pt" 3 "p-trigger asset count"
while IFS='|' read -r asset dest sha; do
  [[ -n "$asset" && "$asset" != \#* ]] || continue
  [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || tl_fail "bad sha256 for $asset"
  [[ "$dest" =~ ^(data|ml)/[A-Za-z0-9_./-]+$ && "$dest" != *..* ]] || tl_fail "destination outside data/ or ml/: $dest"
done <<<"$(printf '%s\n%s\n' "$resp" "$ptrig")"
dups=$(printf '%s\n%s\n' "$resp" "$ptrig" | grep -v '^#' | cut -d'|' -f2 | sort | uniq -d)
tl_eq "$dups" "" "duplicate destinations"
tl_run out rc pl_static_table nonsense
tl_eq "$rc" 1 "unknown static set is refused"

# --- 2. static_get: download, verify, cache --------------------------------------
rel="$TL_TMP/release"
mkdir -p "$rel"
printf 'payload-one\n' >"$rel/one.bin"
sha1=$(pl_sha256 "$rel/one.bin")
export STATIC_RELEASE_BASE="file://$rel"
work="$TL_TMP/work"
mkdir -p "$work"
cd "$work"

# 2a. missing: downloaded and verified
tl_run out rc pl_static_get one.bin data/s/one.bin "$sha1"
tl_eq "$rc" 0 "download ok rc"
tl_has "$out" "data/s/one.bin: OK" "download ok message"
tl_eq "$(cat data/s/one.bin)" payload-one "downloaded content"
[[ ! -e data/s/one.bin.part ]] || tl_fail "partial file left behind"
# 2b. present: no download (the release file is gone, so a download would fail)
rm -f "$rel/one.bin"
tl_run out rc pl_static_get one.bin data/s/one.bin "$sha1"
tl_eq "$rc" 0 "present file is not downloaded again"
# 2c. a wrong download fails and leaves nothing behind
printf 'tampered\n' >"$rel/two.bin"
tl_run out rc pl_static_get two.bin data/s/two.bin "$sha1"
tl_eq "$rc" 1 "sha256 mismatch fails"
tl_has "$out" "sha256 mismatch" "mismatch message"
[[ ! -e data/s/two.bin ]] || tl_fail "a file that failed its checksum was kept"
# 2d. an unreachable asset fails cleanly
tl_run out rc pl_static_get gone.bin data/s/gone.bin "$sha1"
tl_eq "$rc" 1 "missing asset fails"
[[ ! -e data/s/gone.bin && ! -e data/s/gone.bin.part ]] || tl_fail "partial file kept for a failed download"
# 2e. cache: a verified file is saved, a later run restores it without the network
printf 'payload-one\n' >"$rel/one.bin"
cache="$TL_TMP/cache-static"
rm -rf data
tl_run out rc pl_static_get one.bin data/s/one.bin "$sha1" "$cache"
tl_eq "$rc" 0 "cached download rc"
[[ -s "$cache/data/s/one.bin" ]] || tl_fail "verified file not saved to the cache"
rm -rf data "$rel/one.bin"
tl_run out rc pl_static_get one.bin data/s/one.bin "$sha1" "$cache"
tl_eq "$rc" 0 "restored from the cache without the network"
tl_eq "$(cat data/s/one.bin)" payload-one "restored content"
# 2f. a damaged cache copy is dropped and fetched fresh
printf 'payload-one\n' >"$rel/one.bin"
rm -rf data
printf 'damaged\n' >"$cache/data/s/one.bin"
tl_run out rc pl_static_get one.bin data/s/one.bin "$sha1" "$cache"
tl_eq "$rc" 0 "damaged cache copy is replaced"
tl_eq "$(cat data/s/one.bin)" payload-one "content after a damaged cache"
tl_eq "$(cat "$cache/data/s/one.bin")" payload-one "cache repaired"
# 2g. the set loop stops at the first failure
pl_static_table() { printf '# comment\ntwo.bin|data/s/two.bin|%s\none.bin|data/s/one2.bin|%s\n' "$sha1" "$sha1"; }
rm -rf data
tl_run out rc pl_static_inputs custom
tl_eq "$rc" 1 "a failing asset fails the set"
[[ ! -e data/s/one2.bin ]] || tl_fail "set continued after a failure"
printf 'payload-one\n' >"$rel/two.bin"
tl_run out rc pl_static_inputs custom
tl_eq "$rc" 0 "set ok"
tl_has "$out" "data/s/one2.bin: OK" "set processed every row"
unset -f pl_static_table
# shellcheck source=deploy/vps/pipeline-lib.sh
source "$here/pipeline-lib.sh"
trap - ERR
set +E
cd "$TL_TMP"

# --- 3. state: restore and save round trip -----------------------------------------
root="$TL_TMP/root"
cdir="$TL_TMP/cache-state"
mkdir -p "$root"
tl_zip "$root/data/now/prev_classes.npz"
tl_run out rc pl_cache_save "$root" "$cdir" data/now/prev_classes.npz npz
tl_eq "$rc" 0 "save valid npz"
[[ -s "$cdir/data/now/prev_classes.npz" ]] || tl_fail "state not saved"
# a truncated archive is refused and the previous copy stays
head -c 10 "$root/data/now/prev_classes.npz" >"$root/data/now/bad.npz"
tl_run out rc pl_cache_save "$root" "$cdir" data/now/bad.npz npz
tl_eq "$rc" 1 "truncated npz is refused"
[[ ! -e "$cdir/data/now/bad.npz" ]] || tl_fail "truncated npz was saved"
cp "$cdir/data/now/prev_classes.npz" "$TL_TMP/good-before.npz"
head -c 10 "$root/data/now/prev_classes.npz" >"$root/data/now/prev_classes.npz.new"
mv "$root/data/now/prev_classes.npz.new" "$root/data/now/prev_classes.npz"
tl_run out rc pl_cache_save "$root" "$cdir" data/now/prev_classes.npz npz
tl_eq "$rc" 1 "a damaged state file does not replace the saved one"
cmp -s "$cdir/data/now/prev_classes.npz" "$TL_TMP/good-before.npz" || tl_fail "previous state was overwritten by a damaged file"
# nothing to save is not an error; a symlink is never saved
rm -f "$root/data/now/prev_classes.npz"
tl_run out rc pl_cache_save "$root" "$cdir" data/now/prev_classes.npz npz
tl_eq "$rc" 0 "missing file is not an error"
ln -s /etc/hosts "$root/data/now/link.bin"
tl_run out rc pl_cache_save "$root" "$cdir" data/now/link.bin file
tl_eq "$rc" 0 "symlink skipped"
[[ ! -e "$cdir/data/now/link.bin" ]] || tl_fail "a symlink was saved"
# restore: replace mode overwrites, keep mode does not, no cache is fine
tl_run out rc pl_cache_restore "$root" "$cdir" data/now/prev_classes.npz replace
tl_eq "$rc" 0 "restore rc"
cmp -s "$root/data/now/prev_classes.npz" "$TL_TMP/good-before.npz" || tl_fail "state not restored"
echo newer >"$root/data/now/prev_classes.npz"
tl_run out rc pl_cache_restore "$root" "$cdir" data/now/prev_classes.npz keep
tl_eq "$(cat "$root/data/now/prev_classes.npz")" newer "keep mode must not overwrite"
tl_run out rc pl_cache_restore "$root" "$cdir" data/now/prev_classes.npz replace
cmp -s "$root/data/now/prev_classes.npz" "$TL_TMP/good-before.npz" || tl_fail "replace mode did not overwrite"
tl_run out rc pl_cache_restore "$root" "$cdir" data/never/saved.npz replace
tl_eq "$rc" 0 "first run: nothing cached"
tl_has "$out" "no cached data/never/saved.npz" "first-run notice"

# --- 4. a remote with main and fallback -------------------------------------------
origin="$TL_TMP/origin.git"
tl_make_origin "$origin"
export PIPELINE_REPO_URL="file://$origin"
export PIPELINE_PUSH_URL="file://$origin"
repo="$TL_TMP/checkout"

# 4a. init creates a blobless, non-sparse clone that fetches both branches
tl_run out rc pl_init_checkout "$repo"
tl_eq "$rc" 0 "init rc: $out"
tl_eq "$(git -C "$repo" config --get remote.origin.promisor)" true "promisor"
tl_eq "$(git -C "$repo" config --get remote.origin.partialclonefilter)" blob:none "filter"
tl_eq "$(git -C "$repo" config --get-all remote.origin.pushurl || true)" "" "no push URL is stored (the runner sets it per run)"
tl_eq "$(git -C "$repo" config --get core.logAllRefUpdates)" false "no reflog growth"
refspecs=$(git -C "$repo" config --get-all remote.origin.fetch)
tl_has "$refspecs" "refs/remotes/origin/main" "main refspec"
tl_has "$refspecs" "refs/remotes/origin/fallback" "fallback refspec"
[[ "$(git -C "$repo" config --get core.sparseCheckout || echo false)" != true ]] || tl_fail "checkout is sparse"
[[ -f "$repo/data/manifest.json" && -f "$repo/live/jobs.py" ]] || tl_fail "full tree not checked out"
# 4b. init is repeatable, and it removes a stored push URL (a stale one would push twice)
tl_run out rc pl_init_checkout "$repo"
tl_eq "$rc" 0 "init twice"
git -C "$repo" config remote.origin.pushurl "git@example.invalid:x/y.git"
tl_run out rc pl_validate_checkout "$repo"
tl_eq "$rc" 1 "a stored push URL is refused"
tl_has "$out" "remote.origin.pushurl" "push URL message"
tl_run out rc pl_init_checkout "$repo"
tl_eq "$rc" 0 "init after a stored push URL"
tl_eq "$(git -C "$repo" config --get-all remote.origin.pushurl || true)" "" "init removed the stored push URL"
# 4c. refusals
tl_run out rc pl_init_checkout relative/path
tl_eq "$rc" 1 "relative path refused"
tl_run out rc pl_init_checkout /
tl_eq "$rc" 1 "root path refused"
mkdir -p "$TL_TMP/occupied"
echo x >"$TL_TMP/occupied/file"
tl_run out rc pl_init_checkout "$TL_TMP/occupied"
tl_eq "$rc" 1 "non-empty directory refused"
tl_has "$out" "not empty" "non-empty message"
[[ ! -e "$TL_TMP/occupied/.git" ]] || tl_fail "git init ran in a foreign directory"
tl_run out rc pl_validate_checkout "$TL_TMP/nowhere"
tl_eq "$rc" 1 "missing checkout refused"
tl_run out rc pl_validate_checkout "$TL_TMP/occupied"
tl_eq "$rc" 1 "directory without .git refused"
other="$TL_TMP/other"
git clone -q "$origin" "$other"
tl_run out rc pl_validate_checkout "$other"
tl_eq "$rc" 1 "clone of another URL form refused"
tl_has "$out" "expected $PIPELINE_REPO_URL" "origin mismatch message"
git -C "$repo" config remote.origin.url "file://$TL_TMP/elsewhere.git"
tl_run out rc pl_init_checkout "$repo"
tl_eq "$rc" 1 "init refuses an existing checkout with another origin"
tl_run out rc pl_checkout_update "$repo" main
tl_eq "$rc" 1 "update refuses an existing checkout with another origin"
git -C "$repo" config remote.origin.url "$PIPELINE_REPO_URL"
git -C "$repo" config core.sparseCheckout true
tl_run out rc pl_validate_checkout "$repo"
tl_eq "$rc" 1 "sparse checkout refused"
git -C "$repo" config core.sparseCheckout false
git -C "$repo" config --replace-all remote.origin.fetch '+refs/heads/main:refs/remotes/origin/main'
tl_run out rc pl_validate_checkout "$repo"
tl_eq "$rc" 1 "checkout that does not fetch fallback refused"
git -C "$repo" config --add remote.origin.fetch '+refs/heads/fallback:refs/remotes/origin/fallback'
tl_run out rc pl_validate_checkout "$repo"
tl_eq "$rc" 0 "valid checkout accepted"

# 4d. update: new main commit, untracked and ignored files and leftovers are gone
w2="$origin.work"
echo two >"$w2/data/two.json"
tl_git "$w2" add -A
tl_git "$w2" commit -q -m "main two"
tl_git "$w2" push -q origin main
echo junk >"$repo/untracked.txt"
mkdir -p "$repo/ignored"
echo junk >"$repo/ignored/file"
echo modified >"$repo/live/jobs.py"
git -C "$repo" branch -f fallback origin/main >/dev/null 2>&1
git -C "$repo" worktree add -q --detach "$TL_TMP/stale-wt" HEAD
# the scratch directory is derived from the state dir; a stale PIPELINE_TMP must not matter
export PIPELINE_STATE_DIR="$TL_TMP/state-lib"
mkdir -p "$PIPELINE_STATE_DIR/tmp/fallback-abc" "$PIPELINE_STATE_DIR/tmp/fallback-keep-xyz" \
  "$PIPELINE_STATE_DIR/tmp/keep-me" "$TL_TMP/victim/fallback-victim"
ln -s "$TL_TMP/victim/fallback-victim" "$PIPELINE_STATE_DIR/tmp/fallback-link"
export PIPELINE_TMP="$TL_TMP/victim"
# the remote-tracking ref of fallback exists before the update (a fetch made it)
git -C "$repo" fetch -q origin fallback
git -C "$repo" rev-parse --verify -q refs/remotes/origin/fallback >/dev/null || tl_fail "test premise: no origin/fallback ref"
tl_run out rc pl_checkout_update "$repo" main
tl_eq "$rc" 0 "update rc: $out"
tl_eq "$(git -C "$repo" rev-parse HEAD)" "$(git -C "$origin" rev-parse main)" "update reached origin/main"
[[ -f "$repo/data/two.json" ]] || tl_fail "new commit not checked out"
[[ ! -e "$repo/untracked.txt" && ! -e "$repo/ignored" ]] || tl_fail "untracked or ignored files survived"
tl_eq "$(cat "$repo/live/jobs.py")" "# stub" "local modification survived"
[[ ! -e "$TL_TMP/stale-wt" ]] || tl_fail "stale worktree not removed"
[[ ! -d "$PIPELINE_STATE_DIR/tmp/fallback-abc" && ! -d "$PIPELINE_STATE_DIR/tmp/fallback-keep-xyz" ]] || tl_fail "leftover fallback temp dirs not removed"
[[ -d "$PIPELINE_STATE_DIR/tmp/keep-me" ]] || tl_fail "an unrelated temp dir was removed"
[[ -d "$TL_TMP/victim/fallback-victim" ]] || tl_fail "a fallback-* directory outside the state dir was removed"
[[ -L "$PIPELINE_STATE_DIR/tmp/fallback-link" && -d "$TL_TMP/victim/fallback-victim" ]] || tl_fail "a symlink was followed"
if git -C "$repo" rev-parse --verify -q refs/heads/fallback >/dev/null; then tl_fail "stale local fallback branch kept"; fi
# the tracking ref is deleted, so a failed fetch cannot leave a stale base behind
if git -C "$repo" rev-parse --verify -q refs/remotes/origin/fallback >/dev/null; then tl_fail "origin/fallback tracking ref survived the update"; fi
# a tmp directory that resolves outside the state dir is left alone, with a warning
mkdir -p "$TL_TMP/state-link" "$TL_TMP/outside/fallback-out"
ln -s "$TL_TMP/outside" "$TL_TMP/state-link/tmp"
PIPELINE_STATE_DIR="$TL_TMP/state-link" tl_run out rc pl_scratch_clean
tl_eq "$rc" 0 "linked scratch dir: rc"
tl_has "$out" "outside the state directory" "linked scratch dir warning"
[[ -d "$TL_TMP/outside/fallback-out" ]] || tl_fail "deleted outside the state directory"
# no state dir (the library run by hand): nothing is deleted anywhere
mkdir -p "$TL_TMP/by-hand/fallback-x"
( unset PIPELINE_STATE_DIR; PIPELINE_TMP="$TL_TMP/by-hand"; pl_scratch_clean )
[[ -d "$TL_TMP/by-hand/fallback-x" ]] || tl_fail "pl_scratch_clean deleted something without a state dir"
unset PIPELINE_TMP PIPELINE_STATE_DIR

# 4e. state survives the update (the actions/cache replacement)
cd "$repo"
tl_zip "$repo/data/now/response/dswx_state.npz"
tl_run out rc pl_cache_save "$repo" "$cdir" data/now/response/dswx_state.npz npz
tl_eq "$rc" 0 "save state in the checkout"
tl_run out rc pl_checkout_update "$repo" main
[[ ! -e "$repo/data/now/response/dswx_state.npz" ]] || tl_fail "state was not cleaned with the tree (test premise)"
tl_run out rc pl_cache_restore "$repo" "$cdir" data/now/response/dswx_state.npz replace
[[ -s "$repo/data/now/response/dswx_state.npz" ]] || tl_fail "state did not survive a checkout update"
pl_valid_zip "$repo/data/now/response/dswx_state.npz" || tl_fail "restored state is not a valid archive"

# 4f. restore from the fallback branch
tl_run out rc pl_restore_fallback data/now/history data/now/nisar_stack
tl_eq "$rc" 0 "fallback restore rc: $out"
tl_eq "$(cat "$repo/data/now/history/run1/summary.json")" h1 "history restored"
tl_eq "$(cat "$repo/data/now/nisar_stack/frame.json")" s1 "nisar stack restored"
tl_run out rc pl_restore_fallback data/now/not_there
tl_eq "$rc" 0 "a missing path is not an error"
tl_has "$out" "no data/now/not_there on fallback yet" "missing path notice"
# the branch really does not exist yet: a notice, exit 0 (the first run starts the history)
nofb="$TL_TMP/nofb.git"
git clone -q --bare "$origin" "$nofb"
git -C "$nofb" update-ref -d refs/heads/fallback
git -C "$repo" remote set-url origin "file://$nofb"
tl_run out rc pl_restore_fallback data/now/history
tl_eq "$rc" 0 "an absent fallback branch is not an error: $out"
tl_has "$out" "no fallback branch yet" "absent branch notice"
# the fetch fails and the branch cannot be shown absent: exit 1, never a silent empty restore
git -C "$repo" remote set-url origin "file://$TL_TMP/no-such-remote.git"
tl_run out rc pl_restore_fallback data/now/history
tl_eq "$rc" 1 "a failed fetch is an error"
tl_has "$out" "cannot fetch the fallback branch" "failed fetch message"
tl_lacks "$out" "no fallback branch yet" "a failed fetch is not reported as an absent branch"
git -C "$repo" remote set-url origin "$PIPELINE_REPO_URL"
# the branch exists but the files cannot be extracted: exit 1
real_git=$(command -v git)
tl_stub git <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == archive ]]; then echo "git stub: archive failed" >&2; exit 1; fi
exec "$real_git" "\$@"
EOF
hash -r # bash remembers where git was; look it up again
rm -rf "$repo/data/now/history"
tl_run out rc pl_restore_fallback data/now/history
tl_eq "$rc" 1 "an extraction failure is an error: $out"
tl_has "$out" "cannot extract data/now/history" "extraction failure message"
rm -f "$TL_BIN/git"
hash -r
tl_run out rc pl_restore_fallback data/now/history
tl_eq "$rc" 0 "and the same restore works again"

# --- 5. previous sqlite and the workflow state ---------------------------------------
cd "$TL_TMP"
mkdir -p "$TL_TMP/sq" "$TL_TMP/raw/o/r/fallback/data"
cd "$TL_TMP/sq"
export PIPELINE_RAW_BASE="file://$TL_TMP/raw" GITHUB_REPOSITORY=o/r
tl_run out rc pl_prev_sqlite
tl_eq "$rc" 0 "no previous sqlite is fine"
tl_has "$out" "no previous sqlite on fallback" "no sqlite message"
[[ ! -e data/tipping_point.sqlite && ! -e data/tipping_point.sqlite.part ]] || tl_fail "partial sqlite left behind"
printf 'SQLite format 3\n' >"$TL_TMP/raw/o/r/fallback/data/tipping_point.sqlite"
tl_run out rc pl_prev_sqlite
tl_eq "$(cat data/tipping_point.sqlite)" "SQLite format 3" "previous sqlite downloaded"
cd "$TL_TMP"
fx="$TL_TMP/api/repos/o/r/actions/workflows"
mkdir -p "$fx"
export PIPELINE_GITHUB_API="file://$TL_TMP/api"
printf '{\n  "id": 1,\n  "state": "active"\n}\n' >"$fx/now.yml"
printf '{\n  "id": 2,\n  "state": "disabled_manually"\n}\n' >"$fx/nisar.yml"
tl_eq "$(pl_workflow_state now.yml)" active "active workflow"
tl_eq "$(pl_workflow_state nisar.yml)" disabled_manually "disabled workflow"
tl_eq "$(pl_workflow_state unknown.yml)" unknown "unreadable workflow state"

# --- 6. the program entry point ------------------------------------------------------
tl_run out rc bash "$here/pipeline-lib.sh"
tl_eq "$rc" 2 "usage exit code"
tl_run out rc bash "$here/pipeline-lib.sh" static-inputs nonsense
tl_eq "$rc" 1 "unknown set exits 1"
bash -n "$here/pipeline-lib.sh"

# --- 7. the calls of now.yml: shallow clone, Actions variables, the program form ---------
wfc="$TL_TMP/wfclone"
git clone -q --depth=1 "$PIPELINE_REPO_URL" "$wfc"
cd "$wfc"
tl_eq "$(git rev-parse --is-shallow-repository)" true "test premise: the workflow checkout is shallow"
tl_run out rc env GITHUB_ACTIONS=true bash "$here/pipeline-lib.sh" restore-fallback data/now/history
tl_eq "$rc" 0 "workflow restore rc: $out"
tl_eq "$(cat data/now/history/run1/summary.json)" h1 "workflow restore of the history"
tl_run out rc env GITHUB_ACTIONS=true bash "$here/pipeline-lib.sh" restore-fallback data/now/not_there
tl_eq "$rc" 0 "workflow restore of a missing path rc"
tl_has "$out" "::notice::no data/now/not_there on fallback yet - starts here" "Actions notice annotation"
# a fetch that fails while the branch exists stops the workflow step (bash -e), so
# the history and stack steps never start from an empty restore
git remote set-url origin "file://$TL_TMP/no-such-remote.git"
tl_run out rc bash -e -c "bash '$here/pipeline-lib.sh' restore-fallback data/now/history; echo STEP-WENT-ON"
tl_eq "$rc" 1 "workflow restore with a failed fetch fails the step"
tl_lacks "$out" "STEP-WENT-ON" "the step stopped at the failed restore"
git remote set-url origin "file://$nofb"
tl_run out rc env GITHUB_ACTIONS=true bash "$here/pipeline-lib.sh" restore-fallback data/now/history
tl_eq "$rc" 0 "workflow restore with no fallback branch yet: $out"
tl_has "$out" "::notice::no fallback branch yet" "annotation for an absent branch"
git remote set-url origin "$PIPELINE_REPO_URL"
tl_run out rc env GITHUB_REPOSITORY=o/r bash "$here/pipeline-lib.sh" prev-sqlite
tl_eq "$rc" 0 "workflow prev-sqlite rc: $out"
tl_eq "$(cat data/tipping_point.sqlite)" "SQLite format 3" "workflow prev-sqlite result"
# a file with the wrong content fails the step, like sha256sum -c did
first=$(pl_static_table response | grep -v '^#' | head -n 1 | cut -d'|' -f2)
mkdir -p "$(dirname "$first")"
echo wrong >"$first"
tl_run out rc bash "$here/pipeline-lib.sh" static-inputs response
tl_eq "$rc" 1 "wrong static input fails the step"
tl_has "$out" "sha256 mismatch" "mismatch message"
cd "$TL_TMP"

echo "pipeline-lib tests passed"
