#!/usr/bin/env python3
"""Refresh the app's local SQLite copy on the VPS from the published snapshot.

The 3-hourly data job publishes data/tipping_point.sqlite to the `fallback`
branch. The app reads that file from /app/.data (the `tipping-point-data`
volume) when GitHub raw does not answer. This job keeps the volume copy
current from a host cron entry, so the copy does not depend on app traffic,
on the app deployment workflow, or on the app container being up.

Scope, on purpose: it reads one URL and replaces ONE file,
tipping_point.sqlite, inside the `tipping-point-data` volume. It does not
create a volume, and it does not start, stop, restart or change any
container, network, route or database. It touches nothing that belongs to
another service on the host.

Standard library only (Python 3.8+). Exit 0: updated, unchanged or kept a
newer copy. Exit 1: failed, the previous copy stays in place.
"""
import argparse
import fcntl
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

URL = "https://raw.githubusercontent.com/r2l2pj/tipping-point/fallback/data/tipping_point.sqlite"
VOLUME = "tipping-point-data"
NAME = "tipping_point.sqlite"
TMP_PREFIX = "." + NAME + ".cron."
MAX_BYTES = 100 * 1024 * 1024  # the builder fails above 50 MB
MAGIC = b"SQLite format 3\x00"
STALE_TMP_SECONDS = 3600


class Refused(Exception):
    """The download is not installed; the previous copy stays."""


def volume_dir(volume):
    """Mountpoint of an EXISTING volume. `inspect` never creates one."""
    try:
        out = subprocess.run(
            ["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", volume],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        ).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused("cannot find docker volume %s: %s" % (volume, exc))
    if not out or not os.path.isabs(out):
        raise Refused("docker volume %s has no mountpoint" % volume)
    return out


def download(url, dest_dir, timeout):
    """Stream the URL into a temp file next to the target (same filesystem)."""
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=dest_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            with urllib.request.urlopen(url, timeout=timeout) as res:
                total = 0
                while True:
                    chunk = res.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise Refused("download is larger than %d bytes" % MAX_BYTES)
                    out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except Refused:
        os.unlink(tmp)
        raise
    except Exception as exc:  # network, HTTP status, disk
        os.unlink(tmp)
        raise Refused("download failed: %s" % exc)
    return tmp


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_db(path):
    """Check the file is a usable snapshot. Returns (docs, newest Now run)."""
    with open(path, "rb") as fh:
        if fh.read(len(MAGIC)) != MAGIC:
            raise Refused("not a SQLite file")
    try:
        # immutable=1: read without creating journal or lock files beside it
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % urllib.request.pathname2url(path), uri=True)
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise Refused("integrity check failed")
            docs = con.execute("SELECT count(*) FROM docs").fetchone()[0]
            newest = con.execute("SELECT max(updated_at) FROM runs WHERE kind = 'now'").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise Refused("unreadable database: %s" % exc)
    if not docs:
        raise Refused("docs table is empty")
    return docs, newest or ""


def remove_stale_tmp(dest_dir, now):
    """Drop this job's own temp files left by a killed run. Nothing else."""
    for name in os.listdir(dest_dir):
        full = os.path.join(dest_dir, name)
        if name.startswith(TMP_PREFIX) and os.path.isfile(full) and not os.path.islink(full):
            if now - os.stat(full).st_mtime > STALE_TMP_SECONDS:
                os.unlink(full)


def refresh(url, dest_dir, timeout=120, dry_run=False):
    """Returns a one-line status. Raises Refused when nothing was installed."""
    if not os.path.isdir(dest_dir):
        raise Refused("target directory %s does not exist" % dest_dir)
    target = os.path.join(dest_dir, NAME)
    lock = os.open(dest_dir, os.O_RDONLY)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "skipped: another refresh is running"
        remove_stale_tmp(dest_dir, time.time())
        tmp = download(url, dest_dir, timeout)
        try:
            docs, newest = inspect_db(tmp)
            if os.path.exists(target):
                if sha256(tmp) == sha256(target):
                    return "unchanged: %d docs, newest run %s" % (docs, newest or "unknown")
                try:
                    _, current = inspect_db(target)
                except Refused:
                    current = ""  # a broken current copy is replaced
                if current and newest < current:
                    return "kept current copy: download run %s is older than %s" % (newest or "unknown", current)
            if dry_run:
                return "dry run: would install %d docs, newest run %s" % (docs, newest or "unknown")
            owner = os.stat(dest_dir)
            os.chmod(tmp, 0o644)
            if os.geteuid() == 0:
                os.chown(tmp, owner.st_uid, owner.st_gid)  # the app's user owns the directory
            os.replace(tmp, target)
            return "updated: %d docs, newest run %s" % (docs, newest or "unknown")
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    finally:
        os.close(lock)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=URL)
    ap.add_argument("--volume", default=VOLUME, help="existing docker volume (default: %(default)s)")
    ap.add_argument("--dest-dir", help="write here instead of resolving --volume (tests, non-docker hosts)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--dry-run", action="store_true", help="download and check, install nothing")
    args = ap.parse_args(argv)
    try:
        dest = args.dest_dir or volume_dir(args.volume)
        print(refresh(args.url, dest, args.timeout, args.dry_run))
        return 0
    except Refused as exc:
        print("FAILED, previous copy kept: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
