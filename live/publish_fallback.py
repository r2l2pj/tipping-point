"""Publish small data/ files as a single orphan fallback snapshot.

Manifest-last is guaranteed by the producers: export_fallback writes
manifest.json last inside data/. This script only snapshots their output.
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def git(*args: str, cwd: Path | None = None, allowed: tuple[int, ...] = (0,)):
    # ROOT resolved per call (not a def-time default) so tests can point it
    # at a sandbox repo
    result = subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True,
                            text=True, check=False)
    if result.returncode not in allowed:
        operation = "commit" if "commit" in args else args[0]
        raise SystemExit(f"ERROR: git {operation} failed (exit {result.returncode})")
    return result


APP_FACING_SUFFIXES = {".json", ".geojson", ".png", ".tif", ".tiff", ".md", ".sqlite"}


def _app_facing(path: Path, source: Path) -> bool:
    """L26: only app-facing files go to the fallback branch — manifests,
    frames, COGs, geojson, metrics. Raw granules and intermediates
    (zip/hgt/hdf/nc/npz, anything under a raw/ dir) never do, nor do
    the job's static model inputs (any static/ dir: climatology, terrain,
    GPW, MCD12Q1 — hosted on the static-v1 Release, never read by the app)."""
    rel = path.relative_to(source)
    return (not {"raw", "static"} & set(rel.parts)
            and path.suffix.lower() in APP_FACING_SUFFIXES)


def upload_release_assets(paths: list[Path], source: Path) -> None:
    """L26: >max_mb files go to a `fallback-assets` GitHub release instead
    of the branch. Best-effort: needs `gh` + a push-capable token."""
    if not paths or not shutil.which("gh"):
        return
    subprocess.run(["gh", "release", "create", "fallback-assets", "--latest=false",
                    "--title", "fallback data assets", "--notes",
                    "Oversized app-facing files referenced by the fallback branch."],
                   cwd=source, capture_output=True, text=True, check=False)
    for path in paths:
        rel = path.relative_to(source).as_posix()
        subprocess.run(["gh", "release", "upload", "fallback-assets", str(path),
                        "--clobber", "--name", rel.replace("/", "__")],
                       cwd=source, capture_output=True, text=True, check=False)


# Rolling state only the Now job writes (live/now_history.py). Every other
# job (nisar, validation, reference) snapshots a checkout without it, so a
# plain mirror would wipe it — carry the fallback copy over instead.
CARRY_OVER = ("now/history", "now/nisar_stack")


def _keep_always() -> tuple[str, ...]:
    """FALLBACK_KEEP (space-separated data/-relative dirs): kept from the
    current fallback even when this job's checkout has them. The non-Now jobs
    set FALLBACK_KEEP=now — their data/now is main's committed (stale) copy,
    and only the Now job may replace the live Now outputs."""
    import os
    return tuple(os.environ.get("FALLBACK_KEEP", "").split())


def sync_data(source: Path, target: Path, max_mb: float) -> int:
    keep = []
    always = _keep_always()
    with tempfile.TemporaryDirectory(prefix="fallback-keep-") as stash:
        for rel in (*always, *(r for r in CARRY_OVER if not any(r.startswith(a + "/") for a in always))):
            if (rel in always or not (source / rel).exists()) and (target / rel).is_dir():
                shutil.move(str(target / rel), str(Path(stash) / rel.replace("/", "__")))
                keep.append(rel)
        released = _sync_data(source, target, max_mb)
        for rel in keep:
            if (target / rel).exists():          # the mirror wrote this job's copy: drop it
                shutil.rmtree(target / rel)
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(stash) / rel.replace("/", "__")), str(target / rel))
            print(f"snapshot: kept {rel} from the current fallback (not written by this job)")
    return released


def _sync_data(source: Path, target: Path, max_mb: float) -> int:
    excluded, released = set(), []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if not _app_facing(path, source):
            excluded.add(rel)
        elif path.stat().st_size > max_mb * 1024 * 1024:
            excluded.add(rel)
            released.append(path)
            print(f"WARNING: {rel} over {max_mb:g} MB (-> release asset)")
    upload_release_assets(released, source)
    if rsync := shutil.which("rsync"):
        result = subprocess.run(
            [rsync, "-a", "--delete", "--delete-excluded",
             *(f"--exclude=/{path.as_posix()}" for path in sorted(excluded)),
             f"{source}/", f"{target}/"], capture_output=True, text=True, check=False)
        if result.returncode:
            raise SystemExit(f"ERROR: rsync failed (exit {result.returncode})")
    else:
        def ignore(directory, names):
            return [name for name in names
                    if (Path(directory) / name).relative_to(source) in excluded]
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=ignore)
    print(f"snapshot: excluded {len(excluded)} files "
          f"({len(released)} oversized)")
    return len(released)


def _rel_app_facing(rel: str) -> bool:
    parts = Path(rel).parts
    return (rel.startswith("data/") and not {"raw", "static"} & set(parts)
            and Path(rel).suffix.lower() in APP_FACING_SUFFIXES)


def main_head_paths() -> set[str]:
    """data/ paths on main HEAD: origin/main freshly fetched (main may have
    moved since this CI checkout — the targeted-publish race), else HEAD."""
    git("fetch", "origin", "main", allowed=tuple(range(256)))
    for ref in ("origin/main", "HEAD"):
        res = git("ls-tree", "-r", "--name-only", ref, "--", "data",
                  allowed=tuple(range(256)))
        if res.returncode == 0:
            return set(res.stdout.split())
    return set()


def dropped_paths(worktree: Path, on_main: set[str]) -> list[str]:
    """App-facing paths the staged snapshot deletes from the current
    fallback that main HEAD also has (conductor rule 2026-11-15: never
    wipe a file that is committed on main). FALLBACK_ALLOW_DROP: space-
    separated globs for an intended removal."""
    import fnmatch
    import os
    res = git("diff", "--cached", "--name-only", "--diff-filter=D", cwd=worktree,
              allowed=(0, 1))
    allow = os.environ.get("FALLBACK_ALLOW_DROP", "").split()
    return sorted(rel for rel in res.stdout.split()
                  if _rel_app_facing(rel) and rel in on_main
                  and not any(fnmatch.fnmatch(rel, g) for g in allow))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-mb", type=float, default=50)
    args = parser.parse_args(argv)
    if not 0 < args.max_mb < float("inf"):
        parser.error("--max-mb must be positive and finite")
    if args.dry_run:
        print(f"dry-run: snapshot {DATA} to origin fallback (cap {args.max_mb:g} MB)")
        return
    if not DATA.is_dir():
        raise SystemExit("ERROR: data/ directory is missing; refusing to publish")
    git("fetch", "origin", "fallback", allowed=tuple(range(256)))
    exists = git("rev-parse", "--verify", "origin/fallback", allowed=(0, 1, 128)).returncode == 0
    with tempfile.TemporaryDirectory(prefix="fallback-") as temp:
        worktree = Path(temp) / "wb"
        try:
            if exists:
                git("worktree", "add", str(worktree), "fallback")
            else:
                git("worktree", "add", str(worktree), "--detach", "HEAD")
                git("checkout", "--orphan", "fallback", cwd=worktree)
                git("rm", "-rf", ".", cwd=worktree, allowed=tuple(range(256)))
            sync_data(DATA, worktree / "data", args.max_mb)
            git("add", "-A", cwd=worktree)
            if exists:
                dropped = dropped_paths(worktree, main_head_paths())
                if dropped:
                    for rel in dropped:
                        print(f"DROPPED (on fallback and main HEAD): {rel}")
                    raise SystemExit(
                        f"ERROR: snapshot would drop {len(dropped)} app-facing "
                        "path(s) that main HEAD has; refusing to publish")
            if git("diff", "--cached", "--quiet", cwd=worktree, allowed=(0, 1)).returncode == 0:
                print("no changes")
                return
            timestamp = dt.datetime.now(dt.UTC).isoformat()
            amend = ["--amend", "--reset-author"] if exists else []
            git("-c", "user.name=github-actions[bot]", "-c",
                "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                "commit", *amend, "-m", f"fallback: data snapshot {timestamp} [skip ci]",
                cwd=worktree)
            git("push", "-f", "origin", "fallback", cwd=worktree)
        finally:
            git("worktree", "remove", "--force", str(worktree))


if __name__ == "__main__":
    main()
