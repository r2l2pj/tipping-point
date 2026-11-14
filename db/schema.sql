-- Subscribers store — portable DDL (SQLite + Postgres compatible types).
-- chat_id is the primary key; consent gates reads (LGPD); lat/lon are written
-- only by the bot side, never from the alerts job.
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id TEXT PRIMARY KEY,
    lat NUMERIC,
    lon NUMERIC,
    consent BOOLEAN,
    last_class INTEGER,
    last_alert_ts TEXT,
    current_label TEXT,
    data_date TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
