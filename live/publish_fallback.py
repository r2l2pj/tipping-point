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


def git(*args: str, cwd: Path = ROOT, allowed: tuple[int, ...] = (0,)):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode not in allowed:
        operation = "commit" if "commit" in args else args[0]
        raise SystemExit(f"ERROR: git {operation} failed (exit {result.returncode})")
    return result


APP_FACING_SUFFIXES = {".json", ".geojson", ".png", ".tif", ".tiff", ".md"}


def _app_facing(path: Path, source: Path) -> bool:
    """L26: only app-facing files go to the fallback branch — manifests,
    frames, COGs, geojson, metrics. Raw granules and intermediates
    (zip/hgt/hdf/nc/npz, anything under a raw/ dir) never do."""
    rel = path.relative_to(source)
    return "raw" not in rel.parts and path.suffix.lower() in APP_FACING_SUFFIXES


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


def sync_data(source: Path, target: Path, max_mb: float) -> int:
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
