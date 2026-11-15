import sqlite3

import pytest

from scripts import migrate_neon_to_sqlite as migration

ROWS = [{
    "chat_id": "chat-1",
    "lat": -29.25,
    "lon": -51.88,
    "consent": True,
    "last_class": 2,
    "last_alert_ts": "2026-11-15T15:00:00Z",
    "current_label": "Alto — enchente, hoje",
    "data_date": "2026-11-15",
    "created_at": "2026-11-01T10:00:00Z",
}, {
    "chat_id": "chat-2",
    "lat": -30.03,
    "lon": -51.23,
    "consent": False,
    "last_class": 0,
    "last_alert_ts": None,
    "current_label": None,
    "data_date": None,
    "created_at": "2026-11-02T10:00:00Z",
}]


def test_migrate_copies_all_subscriber_rows_and_state(monkeypatch, tmp_path):
    monkeypatch.setattr(migration.alerts, "_neon_query", lambda query: ROWS)
    target = tmp_path / "subscribers.sqlite"

    copied = migration.migrate(target)

    with sqlite3.connect(target) as db:
        rows = db.execute(
            "SELECT chat_id, lat, lon, consent, last_class, last_alert_ts, "
            "current_label, data_date, created_at FROM subscribers ORDER BY chat_id"
        ).fetchall()
    assert copied == 2
    assert rows[0] == (
        "chat-1", -29.25, -51.88, 1, 2, "2026-11-15T15:00:00Z",
        "Alto — enchente, hoje", "2026-11-15", "2026-11-01T10:00:00Z",
    )
    assert rows[1] == (
        "chat-2", -30.03, -51.23, 0, 0, None, None, None,
        "2026-11-02T10:00:00Z",
    )


def test_migrate_refuses_to_overwrite_existing_subscribers(monkeypatch, tmp_path):
    monkeypatch.setattr(migration.alerts, "_neon_query", lambda query: ROWS)
    target = tmp_path / "subscribers.sqlite"
    migration.migrate(target)

    with pytest.raises(ValueError, match="target database is not empty"):
        migration.migrate(target)

    with sqlite3.connect(target) as db:
        assert db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0] == 2


def test_migrate_rejects_empty_source_without_creating_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(migration.alerts, "_neon_query", lambda query: [])
    target = tmp_path / "subscribers.sqlite"

    with pytest.raises(ValueError, match="source table is empty"):
        migration.migrate(target)

    assert not target.exists()
