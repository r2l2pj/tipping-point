#!/usr/bin/env bash
# The real live/publish_fallback.py, run from a persistent VPS-style checkout
# against a local "origin": publishing off (--no-push) changes nothing and still
# runs the drop guard; publishing on pushes one orphan commit with the VPS
# identity, starts from the fetched fallback (not a stale local branch) and
# keeps the live Now outputs for the other jobs; a fallback branch that cannot be
# fetched is never replaced by a snapshot of main's stale files; the push token is
# on no command line and not in .git/config. No network, no root.
# shellcheck disable=SC2154,SC2329  # tl_run sets out and rc; stubs are called by the code under test
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
# shellcheck source=deploy/vps/testlib.sh
source "$here/testlib.sh"
tl_setup

origin="$TL_TMP/origin.git"
tl_make_origin "$origin"
# the script under test is the one in this repository, placed in the origin's main
mkdir -p "$origin.work/live"
cp "$repo_root/live/publish_fallback.py" "$origin.work/live/publish_fallback.py"
tl_git "$origin.work" add -A
tl_git "$origin.work" commit -q -m "add publisher"
tl_git "$origin.work" push -q origin main

export PIPELINE_REPO_URL="file://$origin"
export PIPELINE_STATE_DIR="$TL_TMP/state"
mkdir -p "$PIPELINE_STATE_DIR/tmp"
export TMPDIR="$PIPELINE_STATE_DIR/tmp"
repo="$TL_TMP/checkout"
bash "$here/pipeline-lib.sh" init-checkout "$repo" >/dev/null 2>&1
# shellcheck source=deploy/vps/pipeline-lib.sh
source "$here/pipeline-lib.sh"
trap - ERR
set +E
publish() { (cd "$repo" && python3 live/publish_fallback.py "$@"); }
fb() { git -C "$origin" rev-parse fallback; }
update() { pl_checkout_update "$repo" main >/dev/null 2>&1; }
export FALLBACK_KEEP=now

# --- 1. publishing off: the snapshot is built and compared, nothing leaves ------------
update
echo '{"new": true}' >"$repo/data/new.json"
echo '{"on": "main", "v": 2}' >"$repo/data/manifest.json"
before=$(fb)
tl_run out rc publish --no-push
tl_eq "$rc" 0 "no-push rc: $out"
tl_has "$out" "no-push: snapshot differs from the current fallback: 1 added, 1 modified, 0 deleted" "change summary"
tl_has "$out" "no-push: nothing committed, pushed or uploaded" "no-push closing line"
tl_eq "$(fb)" "$before" "origin fallback unchanged by --no-push"
tl_eq "$(git -C "$repo" rev-parse refs/heads/fallback)" "$(git -C "$repo" rev-parse refs/remotes/origin/fallback)" "no local commit made"
[[ -z "$(git -C "$repo" worktree list --porcelain | grep -c fallback- | grep -v '^0$' || true)" ]] || tl_fail "worktree left behind"

# --- 2. the drop guard still runs with publishing off ----------------------------------
update
rm -f "$repo/data/manifest.json"
tl_run out rc publish --no-push
tl_eq "$rc" 1 "the drop guard refuses with --no-push"
tl_has "$out" "DROPPED (on fallback and main HEAD): data/manifest.json" "guard names the path"
tl_eq "$(fb)" "$before" "origin fallback unchanged by a refused run"

# --- 3. publishing on: one orphan commit, VPS identity, live Now kept -----------------
update
echo '{"new": true}' >"$repo/data/new.json"
echo '{"on": "main", "v": 2}' >"$repo/data/manifest.json"
FALLBACK_GIT_NAME=tipping-point-vps FALLBACK_GIT_EMAIL=vps@example.invalid tl_run out rc publish
tl_eq "$rc" 0 "publish rc: $out"
[[ "$(fb)" != "$before" ]] || tl_fail "origin fallback did not change"
tl_eq "$(git -C "$origin" rev-list --count fallback)" 1 "fallback stays one orphan commit"
tl_eq "$(git -C "$origin" log -1 --format='%an <%ae>' fallback)" "tipping-point-vps <vps@example.invalid>" "commit identity"
tl_eq "$(git -C "$origin" log -1 --format='%cn' fallback)" tipping-point-vps "committer identity"
tl_eq "$(git -C "$origin" show fallback:data/new.json)" '{"new": true}' "new file published"
tl_eq "$(git -C "$origin" show fallback:data/now/history/run1/summary.json)" h1 "history kept for a non-Now job"
tl_eq "$(git -C "$origin" show fallback:data/now/manifest.json)" '{"updated_at": "2026-11-15T00:00:00Z"}' "live Now manifest kept"
tl_run out rc publish
tl_eq "$rc" 0 "second publish rc: $out"
tl_has "$out" "no changes" "an unchanged snapshot is not pushed again"

# --- 4. a stale local fallback branch is never the base --------------------------------
# someone else advances origin/fallback; this checkout keeps its old local
# `fallback` branch (no checkout update in between, so nothing deletes it)
git -C "$repo" rev-parse --verify -q refs/heads/fallback >/dev/null || tl_fail "test premise: no local fallback branch"
other="$TL_TMP/other"
git clone -q "$origin" "$other"
tl_git "$other" checkout -q fallback
echo '{"other": 1}' >"$other/data/now/marker.json"
tl_git "$other" add -A
tl_git "$other" commit -q --amend -m "fallback by someone else"
tl_git "$other" push -q -f origin fallback
echo '{"new": true, "v": 3}' >"$repo/data/new.json"
tl_run out rc publish
tl_eq "$rc" 0 "publish after another writer rc: $out"
tl_eq "$(git -C "$origin" show fallback:data/now/marker.json)" '{"other": 1}' "the other writer's Now file was kept (base is origin/fallback)"
tl_eq "$(git -C "$origin" show fallback:data/new.json)" '{"new": true, "v": 3}' "this run's change published"

# --- 5. a fallback branch that cannot be fetched is never overwritten ----------------
# The tracking ref is gone after a checkout update, so "no origin/fallback" alone
# must not be read as "no fallback branch". git is a spy that can fail the fetch of
# the fallback branch (and, separately, the question to origin).
real_git=$(command -v git)
export GIT_SPY_LOG="$TL_TMP/git-spy.log"
tl_stub git <<EOF
#!/usr/bin/env bash
printf 'ARGV %s\n' "\$*" >>"\$GIT_SPY_LOG"
if [[ -n "\${GIT_FAIL_FETCH:-}" && "\$*" == *fetch*fallback* ]]; then echo "git spy: fetch failed" >&2; exit 128; fi
if [[ -n "\${GIT_FAIL_LSREMOTE:-}" && "\$*" == *ls-remote* ]]; then echo "git spy: ls-remote failed" >&2; exit 128; fi
exec "$real_git" "\$@"
EOF
hash -r
update
before=$(fb)
rm -f "$GIT_SPY_LOG"
echo '{"stale": "main copy"}' >"$repo/data/stale.json"
GIT_FAIL_FETCH=1 tl_run out rc publish
tl_eq "$rc" 1 "a failed fetch with an existing branch stops the publish"
tl_has "$out" "origin has a fallback branch but it could not be fetched" "clear error"
tl_eq "$(fb)" "$before" "origin fallback unchanged after a failed fetch"
tl_lacks "$(cat "$GIT_SPY_LOG")" "ARGV push" "no push was tried"
tl_eq "$(git -C "$origin" rev-list --count fallback)" 1 "history of fallback intact"
# the same when origin cannot be asked either
rm -f "$GIT_SPY_LOG"
GIT_FAIL_FETCH=1 GIT_FAIL_LSREMOTE=1 tl_run out rc publish
tl_eq "$rc" 1 "a failed fetch and an unreadable origin stop the publish"
tl_has "$out" "cannot tell whether origin has a fallback branch" "cannot-tell error"
tl_eq "$(fb)" "$before" "origin fallback unchanged when origin cannot be asked"
tl_eq "$(git -C "$repo" worktree list --porcelain | grep -c '^worktree ')" 1 "no worktree left behind by refused runs"

# the branch is really absent (a new origin): the first publish creates it
nofb="$TL_TMP/nofb.git"
git clone -q --bare "$origin" "$nofb"
git -C "$nofb" update-ref -d refs/heads/fallback
git -C "$repo" remote set-url origin "file://$nofb"
git -C "$repo" update-ref -d refs/remotes/origin/fallback || true
rm -f "$GIT_SPY_LOG"
tl_run out rc publish
tl_eq "$rc" 0 "first publish with no fallback branch: $out"
tl_eq "$(git -C "$nofb" rev-list --count fallback)" 1 "the first publish made one orphan commit"
tl_eq "$(git -C "$nofb" show fallback:data/stale.json)" '{"stale": "main copy"}' "snapshot content published"
tl_has "$(cat "$GIT_SPY_LOG")" "ls-remote --exit-code origin refs/heads/fallback" "origin was asked before the orphan was built"
git -C "$repo" remote set-url origin "file://$origin"
rm -f "$TL_BIN/git"
hash -r

# --- 6. the push token: on no command line, not in .git/config, not in the output ------
# shellcheck source=deploy/vps/pipeline-run.sh
source "$here/pipeline-run.sh"
trap - ERR
set +E
tok="ghp_TESTSECRET0123456789abcdef"
tl_stub git <<EOF
#!/usr/bin/env bash
printf 'ARGV %s\n' "\$*" >>"\$GIT_SPY_LOG"
printf 'ENV COUNT=%s KEY1=%s\n' "\${GIT_CONFIG_COUNT-unset}" "\${GIT_CONFIG_KEY_1-}" >>"\$GIT_SPY_LOG"
exec "$real_git" "\$@"
EOF
hash -r
update
echo '{"token": "test"}' >"$repo/data/token-test.json"
rm -f "$GIT_SPY_LOG"
before=$(fb)
PL_PUSH_TOKEN=$tok PIPELINE_PUSH_AUTH=token tl_run out rc run_with_push_auth bash -c "cd '$repo' && python3 live/publish_fallback.py"
tl_eq "$rc" 0 "publish through the push auth: $out"
[[ "$(fb)" != "$before" ]] || tl_fail "the publish did not reach origin"
tl_lacks "$out" "$tok" "the token is not in the output"
tl_lacks "$(cat "$GIT_SPY_LOG")" "$tok" "the token is on no git command line or credential setting"
tl_has "$(cat "$GIT_SPY_LOG")" "ENV COUNT=2 KEY1=credential.https://github.com.helper" "the push ran with the credential helper"
tl_has "$(cat "$GIT_SPY_LOG")" "ARGV push -f origin fallback" "the push goes to origin as configured"
if grep -r -F -- "$tok" "$repo/.git" "$PIPELINE_STATE_DIR" >/dev/null 2>&1; then tl_fail "the token is stored on disk"; fi
rm -f "$TL_BIN/git"
hash -r

echo "publish tests passed"
