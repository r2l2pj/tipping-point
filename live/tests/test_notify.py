"""live.notify — Telegram subscriber alerts: send rules (upgrade only,
6 h rate limit, quiet 23h-6h BRT unless Crítico), message text (G2:
"Tipping Point (não oficial)" + Defesa Civil footer), manifest gating."""
import datetime as dt
import json
import sqlite3
from unittest.mock import Mock

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin

from live import notify

NOON = dt.datetime(2026, 11, 15, 15, 0, tzinfo=dt.UTC)   # 12:00 BRT
NIGHT = dt.datetime(2026, 11, 15, 4, 0, tzinfo=dt.UTC)   # 01:00 BRT — quiet
PT = (-51.95, -29.95)                                   # subscriber lon/lat


def _manifest(paused=False, verified=True):
    layers = [
        {"id": f"tpi_{kind}_d{k}", "date": f"2026-11-{15 + k}",
         "basis": "observed+forecast" if k == 0 else "forecast",
         "obs_through": "2026-11-14", "gfs_issued": "2026-11-15T00:00:00Z"}
        for kind in ("slide", "flood") for k in (0, 1)
    ]
    return {
        "schema": "tipping-point/now@2", "mode": "live",
        "updated_at": "2026-11-15T15:00:00Z",
        "bounds": [-58, -34, -48, -22.5],
        "verified": verified, "alerts_paused": paused,
        "data_dates": {"imerg": "2026-11-14T23Z",
                       "smap": "SMAP_L4_SM_gph_20261110T223000_Vv8040_001",
                       "gfs_issued": "2026-11-15T00:00:00Z",
                       "imerg_obs_through": "2026-11-14",
                       "forecast_from": "2026-11-15"},
        "layers": layers,
    }


def _write_world(out_dir, values, paused=False, verified=True):
    """values: {(hazard, day): tpi float} -> 10x10 COGs over the subscriber
    cell, municipal boundaries containing PT, and the manifest."""
    cog = out_dir / "cog"
    cog.mkdir(parents=True, exist_ok=True)
    tr = from_origin(-52.0, -29.9, 0.01, 0.01)   # covers -52..-51.9 x -29.9..-30
    names = {("landslide", 0): "tpi_slide_today.tif",
             ("flood", 0): "tpi_flood_today.tif",
             ("landslide", 1): "tpi_slide_d1.tif",
             ("flood", 1): "tpi_flood_d1.tif"}
    for key, name in names.items():
        arr = np.full((10, 10), values.get(key, 0.05), dtype=np.float32)
        with rasterio.open(cog / name, "w", driver="GTiff", height=10,
                           width=10, count=1, dtype="float32",
                           crs="EPSG:4326", transform=tr,
                           nodata=float("nan")) as ds:
            ds.write(arr, 1)
    gj = out_dir / "geojson"
    gj.mkdir(exist_ok=True)
    sq = [[[-52.0, -30.0], [-51.9, -30.0], [-51.9, -29.9],
           [-52.0, -29.9], [-52.0, -30.0]]]
    (gj / "municipal_boundaries.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": sq},
            "properties": {"name": "Santo Amaro da Imperatriz",
                           "uf": "SC"}}]}))
    (out_dir / "manifest.json").write_text(
        json.dumps(_manifest(paused=paused, verified=verified)))


def _seed_db(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE subscribers (chat_id TEXT PRIMARY KEY, lat NUMERIC,"
        " lon NUMERIC, consent BOOLEAN, last_class INTEGER,"
        " last_alert_ts TEXT, current_label TEXT, data_date TEXT,"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP);")
    for r in rows:
        conn.execute(
            "INSERT INTO subscribers (chat_id, lat, lon, consent,"
            " last_class, last_alert_ts) VALUES (?, ?, ?, 1, ?, ?)",
            (r["chat_id"], r["lat"], r["lon"], r.get("last_class", 0),
             r.get("last_alert_ts")))
    conn.commit()
    conn.close()


def _world(monkeypatch, tmp_path, rows, values, **manifest_kw):
    db = tmp_path / "subs.sqlite"
    _seed_db(db, rows)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:{db}")
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    _write_world(tmp_path, values, **manifest_kw)
    post = Mock(return_value=Mock(status_code=200))
    post.return_value.json.return_value = {"ok": True}
    monkeypatch.setattr(requests, "post", post)
    return db, post


def _row(db, chat_id="a"):
    with sqlite3.connect(db) as conn:
        keys = ("last_class", "last_alert_ts", "current_label", "data_date")
        vals = conn.execute(
            "SELECT last_class, last_alert_ts, current_label, data_date "
            "FROM subscribers WHERE chat_id = ?", (chat_id,)).fetchone()
    return dict(zip(keys, vals))


def test_upgrade_critico_sends_once_with_g2_text(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_URL", "https://app.example")
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 1): 0.7})          # Crítico only on D+1
    out = notify.run(tmp_path, now=NOON)
    assert out["sent"] == 1
    post.assert_called_once()
    assert post.call_args.args == (
        "https://api.telegram.org/bottest-token/sendMessage",)
    assert post.call_args.kwargs["timeout"] == 30
    payload = post.call_args.kwargs["json"]
    assert payload["chat_id"] == "a"
    text = payload["text"]
    assert "Tipping Point (não oficial)" in text
    assert "CRÍTICO" in text and "deslizamento" in text
    assert "Santo Amaro da Imperatriz, SC" in text
    assert "sua região" in text
    assert "amanhã, 16/11" in text
    assert "NOAA GFS emitida 15/11 00:00 UTC" in text
    assert "IMERG até 14/11" in text and "SMAP de 10/11" in text
    assert "Ver no mapa: https://app.example" in text
    assert text.endswith("Siga as orientações da Defesa Civil. SMS 40199.")
    assert "Defesa Civil •" not in text
    assert "sua casa" not in text
    row = _row(db)
    assert row["last_class"] == 3
    assert row["last_alert_ts"] == "2026-11-15T15:00:00Z"
    assert row["current_label"] == "Crítico — deslizamento, amanhã"
    assert row["data_date"] == "2026-11-14"


def test_second_run_within_6h_does_not_resend(monkeypatch, tmp_path):
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 2,
          "last_alert_ts": "2026-11-15T12:00:00Z"}],  # 3 h ago
        {("flood", 0): 0.7})
    out = notify.run(tmp_path, now=NOON)
    assert out["sent"] == 0
    post.assert_not_called()
    assert _row(db)["last_class"] == 2        # held back for the next run
    # 7 h after the last alert the upgrade finally goes out
    out = notify.run(tmp_path, now=NOON + dt.timedelta(hours=7))  # 19:00 BRT
    assert out["sent"] == 1 and post.call_count == 1
    assert _row(db)["last_class"] == 3


def test_quiet_hours_defer_alto_but_send_critico(monkeypatch, tmp_path):
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 0): 0.5})              # Alto
    notify.run(tmp_path, now=NIGHT)
    post.assert_not_called()
    assert _row(db)["last_class"] == 0        # unchanged -> retried daytime
    # held-back upgrade goes out once the quiet window ends
    notify.run(tmp_path, now=NIGHT + dt.timedelta(hours=6))  # 07:00 BRT
    assert post.call_count == 1 and "ALTO" in post.call_args.kwargs["json"]["text"]
    # a Crítico upgrade is never held, even at night (next day so the 6 h
    # rate limit from the Alto send does not interfere)
    _write_world(tmp_path, {("landslide", 0): 0.7})
    notify.run(tmp_path, now=NIGHT + dt.timedelta(days=1))
    assert post.call_count == 2 and "CRÍTICO" in post.call_args.kwargs["json"]["text"]
    assert _row(db)["last_class"] == 3


def test_drop_sends_risco_reduzido(monkeypatch, tmp_path):
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 3}],
        {})
    notify.run(tmp_path, now=NOON)
    post.assert_called_once()
    text = post.call_args.kwargs["json"]["text"]
    assert "Tipping Point (não oficial)" in text
    assert "caiu para Baixo" in text
    assert "Santo Amaro da Imperatriz" in text
    assert "Dados de 14/11" in text
    assert text.endswith("Siga as orientações da Defesa Civil. SMS 40199.")
    assert "Defesa Civil •" not in text
    assert _row(db)["last_class"] == 0


def test_alerts_paused_sends_nothing_and_changes_nothing(monkeypatch, tmp_path):
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 0): 0.9}, paused=True)
    out = notify.run(tmp_path, now=NOON)
    assert out["skipped"] == "alerts_paused" and out["sent"] == 0
    post.assert_not_called()
    row = _row(db)
    assert row["last_class"] == 0 and row["current_label"] is None


def test_unverified_manifest_sends_nothing(monkeypatch, tmp_path):
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 0): 0.9}, verified=False)
    out = notify.run(tmp_path, now=NOON)
    assert out["skipped"] == "unverified" and out["sent"] == 0
    post.assert_not_called()
    assert _row(db)["current_label"] is None


def test_no_token_updates_labels_only(monkeypatch, tmp_path, capsys):
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 0): 0.7})
    monkeypatch.delenv("TELEGRAM_TOKEN")
    out = notify.run(tmp_path, now=NOON)
    assert out["sent"] == 0 and out["updated"] == 1
    post.assert_not_called()
    row = _row(db)
    assert row["current_label"] == "Crítico — deslizamento, hoje"
    assert row["data_date"] == "2026-11-14"
    assert row["last_class"] == 0            # alert still owed for later
    assert "TELEGRAM_TOKEN unset" in capsys.readouterr().out


def test_store_unreachable_exits_zero(monkeypatch, tmp_path, capsys):
    _write_world(tmp_path, {("landslide", 0): 0.7})
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.invalid:5432/x")
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setattr(requests, "post",
                        Mock(side_effect=requests.ConnectionError("refused")))
    assert notify.main(["--out-dir", str(tmp_path)]) == 0
    assert "store unreachable" in capsys.readouterr().out


def test_missing_manifest_exits_zero(tmp_path, capsys):
    assert notify.main(["--out-dir", str(tmp_path)]) == 0
    assert "manifest unreadable" in capsys.readouterr().out


def test_out_of_extent_and_nodata_are_class_zero(monkeypatch, tmp_path):
    """Subscribers outside the grid get Baixo, not a crash; NaN = class 0."""
    db, _post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "far", "lat": -35.0, "lon": -60.0, "last_class": 0},
         {"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {})
    out = notify.run(tmp_path, now=NOON)
    assert out["updated"] == 2
    assert _row(db, "a")["current_label"].startswith("Baixo")


def test_smap_label_preferred_over_granule(monkeypatch, tmp_path):
    """data_dates.smap_label (new manifest shape) wins over the granule id."""
    _db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 0): 0.7})
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["data_dates"]["smap_label"] = "11/11"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    notify.run(tmp_path, now=NOON)
    text = post.call_args.kwargs["json"]["text"]
    assert "SMAP de 11/11" in text


def test_no_app_url_omits_map_line(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_URL", raising=False)
    _db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 0}],
        {("landslide", 0): 0.7})
    notify.run(tmp_path, now=NOON)
    assert "Ver no mapa" not in post.call_args.kwargs["json"]["text"]


def test_quiet_hours_hold_reduction_too(monkeypatch, tmp_path):
    """A drop held by quiet hours keeps last_class for the daytime retry."""
    db, post = _world(
        monkeypatch, tmp_path,
        [{"chat_id": "a", "lat": PT[1], "lon": PT[0], "last_class": 3}],
        {})
    notify.run(tmp_path, now=NIGHT)
    post.assert_not_called()
    assert _row(db)["last_class"] == 3
    notify.run(tmp_path, now=NIGHT + dt.timedelta(hours=6))
    assert "caiu para Baixo" in post.call_args.kwargs["json"]["text"]
    assert _row(db)["last_class"] == 0


def test_current_label_baixo_has_no_hazard():
    from live import notify
    assert notify._current_label(0, "landslide", 0) == "Baixo"
    assert notify._current_label(2, "flood", 1) == "Alto — enchente, amanhã"
