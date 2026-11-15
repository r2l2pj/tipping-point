"""One-shot Neon-to-SQLite copy for the VPS subscriber store."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from requests.exceptions import RequestException

from live import alerts

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db" / "schema.sql"
SELECT = (
    "SELECT chat_id, lat, lon, consent, last_class, last_alert_ts, "
    "current_label, data_date, created_at FROM subscribers"
)
INSERT = (
    "INSERT INTO subscribers (chat_id, lat, lon, consent, last_class, "
    "last_alert_ts, current_label, data_date, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _consent(value) -> int:
    if isinstance(value, str):
        return int(value.strip().lower() in {"1", "t", "true", "yes"})
    return int(bool(value))


def migrate(target: Path | str) -> int:
    """Copy every subscriber row into an empty SQLite database atomically."""
    rows = alerts._neon_query(SELECT)
    if not rows:
        raise ValueError("source table is empty; refusing migration")

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target, timeout=5) as db:
        db.execute("PRAGMA busy_timeout = 5000")
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA.read_text(encoding="utf-8"))
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]:
            raise ValueError("target database is not empty; refusing to overwrite")
        db.executemany(INSERT, [(
            str(row["chat_id"]), row.get("lat"), row.get("lon"),
            _consent(row.get("consent")), row.get("last_class"),
            row.get("last_alert_ts"), row.get("current_label"),
            row.get("data_date"), row.get("created_at"),
        ) for row in rows])
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True,
                        help="SQLite file on the VPS volume")
    args = parser.parse_args(argv)
    try:
        count = migrate(args.target)
    except (KeyError, OSError, RequestException, RuntimeError, TypeError,
            ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Migration failed: {exc}\n")
    print(f"Copied {count} subscriber rows into SQLite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
