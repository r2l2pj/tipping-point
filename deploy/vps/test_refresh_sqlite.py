#!/usr/bin/env python3
"""Tests for refresh_sqlite.py. Standard library only:

    python3 deploy/vps/test_refresh_sqlite.py
"""
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refresh_sqlite as rs  # noqa: E402


def make_db(path, run="2026-11-15T12:17:00Z", docs=2):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE docs (path TEXT PRIMARY KEY, bytes INTEGER, sha256 TEXT, encoding TEXT, body BLOB)")
    con.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, kind TEXT, updated_at TEXT, verified INTEGER, data_dates TEXT)")
    for i in range(docs):
        con.execute("INSERT INTO docs VALUES (?,?,?,?,?)", ("now/%d.json" % i, 2, "x", None, b"{}"))
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (run, "now", run, 1, None))
    con.commit()
    con.close()
    return path


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.src = root / "src"
        self.dest = root / "volume"
        self.src.mkdir()
        self.dest.mkdir()
        self.remote = self.src / "remote.sqlite"
        self.target = self.dest / rs.NAME

    def tearDown(self):
        self._tmp.cleanup()

    def run_refresh(self, **kw):
        return rs.refresh(self.remote.as_uri(), str(self.dest), **kw)

    def test_installs_first_copy_and_leaves_no_temp_file(self):
        make_db(self.remote)
        self.assertTrue(self.run_refresh().startswith("updated"))
        self.assertEqual(self.target.read_bytes(), self.remote.read_bytes())
        self.assertEqual(os.listdir(str(self.dest)), [rs.NAME])
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o644)

    def test_same_content_is_unchanged_and_keeps_the_file(self):
        make_db(self.remote)
        self.run_refresh()
        before = self.target.stat()
        self.assertTrue(self.run_refresh().startswith("unchanged"))
        after = self.target.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_newer_run_replaces_the_copy(self):
        make_db(self.target, run="2026-11-15T09:17:00Z")
        make_db(self.remote, run="2026-11-15T12:17:00Z")
        self.assertTrue(self.run_refresh().startswith("updated"))
        self.assertEqual(rs.inspect_db(str(self.target))[1], "2026-11-15T12:17:00Z")

    def test_older_download_never_replaces_a_newer_copy(self):
        make_db(self.target, run="2026-11-15T12:17:00Z")
        make_db(self.remote, run="2026-11-15T09:17:00Z")
        self.assertTrue(self.run_refresh().startswith("kept current copy"))
        self.assertEqual(rs.inspect_db(str(self.target))[1], "2026-11-15T12:17:00Z")

    def test_bad_downloads_are_refused_and_the_copy_is_kept(self):
        make_db(self.target)
        good = self.target.read_bytes()
        empty_docs = make_db(self.src / "empty.sqlite", docs=0).read_bytes()
        truncated = make_db(self.src / "full.sqlite", docs=400).read_bytes()[:6000]
        for name, body in (("html", b"<html>404</html>"), ("empty file", b""),
                           ("no docs", empty_docs), ("truncated", truncated)):
            with self.subTest(name):
                self.remote.write_bytes(body)
                with self.assertRaises(rs.Refused):
                    self.run_refresh()
                self.assertEqual(self.target.read_bytes(), good)
                self.assertEqual(os.listdir(str(self.dest)), [rs.NAME])

    def test_unreachable_url_is_refused(self):
        make_db(self.target)
        with self.assertRaises(rs.Refused):
            self.run_refresh()  # remote file does not exist
        self.assertEqual(os.listdir(str(self.dest)), [rs.NAME])

    def test_broken_current_copy_is_replaced(self):
        self.target.write_bytes(b"garbage")
        make_db(self.remote)
        self.assertTrue(self.run_refresh().startswith("updated"))

    def test_dry_run_installs_nothing(self):
        make_db(self.remote)
        self.assertTrue(self.run_refresh(dry_run=True).startswith("dry run"))
        self.assertEqual(os.listdir(str(self.dest)), [])

    def test_missing_directory_is_refused_not_created(self):
        gone = self.dest / "nope"
        make_db(self.remote)
        with self.assertRaises(rs.Refused):
            rs.refresh(self.remote.as_uri(), str(gone))
        self.assertFalse(gone.exists())

    def test_only_this_jobs_stale_temp_files_are_removed(self):
        make_db(self.remote)
        stale = self.dest / (rs.TMP_PREFIX + "old")
        fresh = self.dest / (rs.TMP_PREFIX + "new")
        other = self.dest / "tipping_point.sqlite.tmp"  # the app's own refresh temp file
        for f in (stale, fresh, other):
            f.write_bytes(b"x")
        old = time.time() - 2 * rs.STALE_TMP_SECONDS
        os.utime(str(stale), (old, old))
        os.utime(str(other), (old, old))
        self.run_refresh()
        self.assertEqual(sorted(os.listdir(str(self.dest))),
                         sorted([rs.NAME, fresh.name, other.name]))

    def test_main_reports_failure_with_exit_1(self):
        self.assertEqual(rs.main(["--url", self.remote.as_uri(), "--dest-dir", str(self.dest)]), 1)
        make_db(self.remote)
        self.assertEqual(rs.main(["--url", self.remote.as_uri(), "--dest-dir", str(self.dest)]), 0)


if __name__ == "__main__":
    unittest.main()
