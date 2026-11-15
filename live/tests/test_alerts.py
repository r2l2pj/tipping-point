"""Alert-candidate rules: upgrade to Alto/Crítico on today or D+1; quiet hours
23h-6h hold everything except Crítico."""
import datetime as dt

import numpy as np
from rasterio.transform import from_origin

from live.alerts import emit_alerts

TR = from_origin(-52.0, -29.0, 0.01, 0.01)
NOON = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)
NIGHT = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.UTC)


def _classes(fill=0):
    return np.full((10, 10), fill, dtype=np.uint8)


def test_upgrade_to_alto_emits_candidate():
    today = _classes()
    today[3:6, 3:6] = 2
    out = emit_alerts(_classes(), today, _classes(), NOON, TR)
    assert len(out["candidates"]) == 1
    c = out["candidates"][0]
    assert c["class"] == 2 and c["label"] == "Alto" and c["hold"] is False
    assert c["pixels"] == 9


def test_upgrade_only_on_d1_still_emits():
    d1 = _classes()
    d1[0:2, 0:2] = 3
    out = emit_alerts(_classes(), _classes(), d1, NOON, TR)
    assert out["candidates"][0]["class"] == 3


def test_no_upgrade_no_candidates():
    today = _classes()
    today[3:6, 3:6] = 2
    out = emit_alerts(today, today, _classes(), NOON, TR)
    assert out["candidates"] == []


def test_quiet_hours_hold_alto_but_not_critico():
    today = _classes()
    today[1:3, 1:3] = 2
    today[6:9, 6:9] = 3
    out = emit_alerts(None, today, _classes(), NIGHT, TR)
    assert out["quiet_hours"] is True
    by_class = {c["class"]: c for c in out["candidates"]}
    assert by_class[2]["hold"] is True
    assert by_class[3]["hold"] is False


def test_hazard_alerts_tag_and_cluster():
    from live.alerts import emit_hazard_alerts
    flood = _classes()
    flood[0, 0] = flood[0, 2] = flood[2, 1] = 2      # scatter within 3 px
    flood[9, 9] = 3                                   # far away
    slide_d1 = _classes()
    slide_d1[5, 5] = 2
    out = emit_hazard_alerts(
        None, {"landslide": (_classes(), slide_d1), "flood": (flood, _classes())},
        NOON, TR, cluster_px=3)
    cands = out["candidates"]
    assert sorted(c["hazard"] for c in cands) == ["flood", "flood", "landslide"]
    assert cands[0]["class"] == 3 and cands[0]["hazard"] == "flood"  # Crítico first
    scatter = [c for c in cands if c["hazard"] == "flood" and c["class"] == 2]
    assert len(scatter) == 1 and scatter[0]["pixels"] == 3
    slide = [c for c in cands if c["hazard"] == "landslide"][0]
    assert slide["when"] == "d1" and slide["area_km2"] > 0
    assert slide["class_today"] == 0
    w, s, e, n = slide["bbox"]
    assert w < slide["centroid"][0] < e and s < slide["centroid"][1] < n


def test_hazard_alerts_prev_per_hazard_suppresses_repeats():
    from live.alerts import emit_hazard_alerts
    flood = _classes()
    flood[3:6, 3:6] = 2
    out = emit_hazard_alerts({"flood": flood}, {"flood": (flood, _classes())},
                             NOON, TR)
    assert out["candidates"] == []


def test_no_high_or_critical_means_no_tpi_alerts():
    """0 km2 Alto/Crítico (classes <= Moderado everywhere) => 0 TPI alerts."""
    from live.alerts import emit_hazard_alerts
    moderate = _classes(1)
    out = emit_hazard_alerts(None, {"landslide": (moderate, moderate),
                                    "flood": (_classes(), moderate)}, NOON, TR)
    assert out["candidates"] == []


def test_run_checks_pause_alerts():
    from live.run_now import run_checks
    ok = run_checks({"slope_p99_ok": True, "t_slide_ok": True}, 0)
    assert ok["passed"] is True
    assert run_checks({"slope_p99_ok": False, "t_slide_ok": True}, 0)["passed"] is False
    assert run_checks({"slope_p99_ok": True, "t_slide_ok": True}, 3)["passed"] is False
    assert run_checks(None, 0)["passed"] is False      # unchecked terrain


def test_municipality_lookup(tmp_path):
    import json as _json
    from live.now_geojson import municipality_lookup
    sq = [[[-52.0, -30.0], [-51.0, -30.0], [-51.0, -29.0], [-52.0, -29.0], [-52.0, -30.0]]]
    (tmp_path / "municipal_boundaries.geojson").write_text(_json.dumps({
        "type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": sq},
            "properties": {"name": "Porto Alegre",
                           "url": "https://servicodados.ibge.gov.br/api/v3/malhas/estados/RS"}}]}))
    where = municipality_lookup(tmp_path)
    assert where(-51.5, -29.5) == {"municipality": "Porto Alegre", "uf": "RS"}
    assert where(-50.95, -29.5)["municipality"] == "Porto Alegre"   # 5 km off: snaps
    assert where(-45.0, -29.5) is None                               # far: none
    assert municipality_lookup(tmp_path / "nope")(-51.5, -29.5) is None


def test_when_follows_the_day_of_the_worst_class():
    """Alto today but Crítico only on D+1 -> class 3, when 'd1'."""
    from live.alerts import emit_hazard_alerts
    today, d1 = _classes(), _classes()
    today[3:6, 3:6] = 2
    d1[3:6, 3:6] = 3
    (c,) = emit_hazard_alerts(None, {"landslide": (today, d1)}, NOON, TR)["candidates"]
    assert c["class"] == 3 and c["class_today"] == 2 and c["when"] == "d1"


def test_persisting_critico_stays_active_without_notification():
    """Conductor 2026-11-15: a Crítico area that persists must stay in the
    public 'active' list (first_issued_at kept) even though no new
    notification is sent for it."""
    from live.alerts import active_alerts, emit_hazard_alerts, keyed_active
    flood = _classes()
    flood[3:6, 3:6] = 3
    layers = {"flood": (flood, _classes())}
    t1 = NOON
    t2 = NOON + _dt.timedelta(hours=3)
    # run 1: upgrade -> notification + active
    n1 = emit_hazard_alerts(None, layers, t1, TR)["candidates"]
    a1, _ = active_alerts(layers, t1, TR, None)
    for a in a1:
        a.update({"municipality": "Florianópolis", "uf": "SC"})
    s1 = keyed_active(a1, {}, "2026-11-14T12:00:00Z")
    assert len(n1) == 1 and len(a1) == 1
    # run 2: same classes -> no notification, still active, first_issued kept
    n2 = emit_hazard_alerts({"flood": flood}, layers, t2, TR)["candidates"]
    a2, _ = active_alerts(layers, t2, TR, None)
    for a in a2:
        a.update({"municipality": "Florianópolis", "uf": "SC"})
    s2 = keyed_active(a2, s1, "2026-11-14T15:00:00Z")
    assert n2 == []
    assert len(a2) == 1 and a2[0]["class"] == 3 and a2[0]["hazard"] == "flood"
    assert a2[0]["first_issued_at"] == "2026-11-14T12:00:00Z"
    assert a2[0]["last_confirmed_at"] == "2026-11-14T15:00:00Z"
    assert "hold" not in a2[0]
    # run 3: area gone -> not active, dropped from state
    a3, _ = active_alerts({"flood": (_classes(), _classes())}, t2, TR, None)
    assert a3 == [] and keyed_active(a3, s2, "x") == {}


# --------------------------------------------------------------------------
# L12b Neon subscriber store — exercised against a real fake HTTPS server
# (stdlib http.server + self-signed cert on an ephemeral port), never with
# mocked requests. The cert is trusted via REQUESTS_CA_BUNDLE.

import datetime as _dt
import ipaddress
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from live import alerts


def _self_signed(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=1))
            .not_valid_after(_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName("localhost"),
                 x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_pem = tmp_path / "cert.pem"
    key_pem = tmp_path / "key.pem"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return cert_pem, key_pem


class _NeonFake(HTTPServer):
    def __init__(self, rows, cert_pem, key_pem, status=200):
        self.rows = rows
        self.status = status
        self.requests = []  # (method, path, headers, body)
        handler = self._make_handler()
        super().__init__(("127.0.0.1", 0), handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_pem, key_pem)
        self.socket = ctx.wrap_socket(self.socket, server_side=True)

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                server.requests.append(
                    (self.command, self.path, dict(self.headers), body))
                if server.status == 200:
                    fields = list(server.rows[0]) if server.rows else []
                    data = json.dumps({
                        "fields": [{"name": f} for f in fields],
                        "rows": [[r.get(f) for f in fields]
                                 for r in server.rows],
                        "rowCount": len(server.rows),
                    }).encode()
                else:
                    data = b'{"err": "boom"}'
                self.send_response(server.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler


@pytest.fixture
def neon(monkeypatch, tmp_path):
    servers = []
    cert_pem, key_pem = _self_signed(tmp_path)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert_pem))

    def start(rows, status=200):
        srv = _NeonFake(rows, cert_pem, key_pem, status)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        conn = f"postgresql://u:p@localhost:{srv.server_port}/db"
        monkeypatch.setenv("DATABASE_URL", conn)
        servers.append(srv)
        return srv, conn

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


_ROWS = [
    {"chat_id": "inside", "lat": -30.0, "lon": -51.5, "last_class": 0},
    {"chat_id": "outside", "lat": -35.0, "lon": -60.0, "last_class": 1},
    {"chat_id": "same", "lat": -30.0, "lon": -51.5, "last_class": 3},
]

_REGION = {
    "class": 3,
    "label": "Crítico",
    "kind": "flood",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-52.0, -30.5], [-51.0, -30.5],
                         [-51.0, -29.5], [-52.0, -29.5], [-52.0, -30.5]]],
    },
}


def test_subscribers_post_to_sql_endpoint(neon):
    srv, conn = neon(_ROWS)
    rows = alerts.subscribers()
    assert rows == _ROWS
    method, path, headers, body = srv.requests[0]
    assert method == "POST"
    assert path == "/sql"
    assert headers.get("Neon-Connection-String") == conn
    payload = json.loads(body)
    assert payload["params"] == []
    q = payload["query"].lower()
    assert "select chat_id, lat, lon, last_class" in q
    assert "from subscribers" in q and "consent = true" in q


def test_update_flow_refreshes_all_rows(neon, monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    _freeze_now(monkeypatch, NOON)
    srv, _ = neon(_ROWS)
    n = alerts.update_subscribers_from_regions([_REGION], "2026-11-14")
    updates = [r for r in srv.requests if r[0] == "POST"
               and json.loads(r[3])["query"].lower().startswith("update")]
    # inside (0->3) and outside (1->0) change; same (3->3) does not.
    assert n == 3
    assert len(updates) == 3
    by_id = {}
    for _, _, _, body in updates:
        payload = json.loads(body)
        assert "update subscribers set" in payload["query"].lower()
        assert "where chat_id" in payload["query"].lower()
        by_id[payload["params"][4]] = payload["params"]
    inside = by_id["inside"]
    assert inside[0] == 3 and inside[2] == "Crítico"
    assert inside[3] == "2026-11-14" and inside[1] is None
    outside = by_id["outside"]
    assert outside[0] == 0 and outside[2] == ""


def test_subscribers_non200_raises(neon):
    neon(_ROWS, status=500)
    with pytest.raises(RuntimeError):
        alerts.subscribers()


def test_unrecognized_scheme_skips(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@h/db")
    assert alerts.update_subscribers_from_regions([_REGION], "2026-11-14") == 0


# --- sqlite backend (stdlib sqlite3 on a tmp file, no server) ---


def _seed_sqlite(path, rows):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE subscribers (chat_id TEXT PRIMARY KEY, lat NUMERIC,"
        " lon NUMERIC, consent BOOLEAN, last_class INTEGER,"
        " last_alert_ts TEXT, current_label TEXT, data_date TEXT,"
        " created_at TEXT DEFAULT CURRENT_TIMESTAMP);")
    for r in rows:
        conn.execute(
            "INSERT INTO subscribers (chat_id, lat, lon, consent, last_class)"
            " VALUES (?, ?, ?, 1, ?)",
            (r["chat_id"], r["lat"], r["lon"], r["last_class"]))
    conn.commit()
    conn.close()


def test_sqlite_backend_update_flow(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    _freeze_now(monkeypatch, NOON)
    db = tmp_path / "subs.sqlite"
    _seed_sqlite(db, _ROWS)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:{db}")
    n = alerts.update_subscribers_from_regions([_REGION], "2026-11-14")
    assert n == 3
    import sqlite3

    conn = sqlite3.connect(db)
    got = {r[0]: r for r in conn.execute(
        "SELECT chat_id, last_class, current_label, data_date,"
        " last_alert_ts FROM subscribers")}
    conn.close()
    assert got["inside"][1] == 3 and got["inside"][2] == "Crítico"
    assert got["inside"][3] == "2026-11-14"
    assert got["inside"][4] is None
    assert got["outside"][1] == 0 and got["outside"][2] == ""
    assert got["same"][1] == 3  # unchanged


def test_sqlite_default_path_and_schema_autocreate(monkeypatch, tmp_path):
    db = tmp_path / "fresh.sqlite"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(alerts, "_DEFAULT_DB", db)
    # empty auto-created store -> consenting rows none -> 0 updates, no HTTP
    assert alerts.update_subscribers_from_regions([_REGION], "2026-11-14") == 0
    assert db.exists()
    import sqlite3

    cols = [r[1] for r in sqlite3.connect(db).execute(
        "PRAGMA table_info(subscribers)")]
    assert cols == ["chat_id", "lat", "lon", "consent", "last_class",
                    "last_alert_ts", "current_label", "data_date",
                    "created_at"]


@pytest.mark.parametrize("hour,quiet", [(4, True), (23, False), (9, False)])
def test_quiet_hours_use_brasilia(hour, quiet, monkeypatch, tmp_path):
    from live import jobs

    when = NOON.replace(hour=hour)
    out = emit_alerts(None, _classes(2), _classes(), when, TR)
    assert out["quiet_hours"] is quiet
    assert out["candidates"][0]["hold"] is quiet
    _freeze_now(monkeypatch, when)
    monkeypatch.setattr(alerts, "critical_areas_from_assets", list)
    monkeypatch.setattr(alerts, "update_subscribers_from_regions", lambda *a, **k: 0)
    assert json.loads(jobs._refresh_alerts(tmp_path).read_text())["quiet_hours"] is quiet


def _freeze_now(monkeypatch, when):
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz)

    monkeypatch.setattr(alerts.dt, "datetime", Clock)


def test_assets_d0_d1_worst_class_updates_subscriber(monkeypatch, tmp_path):
    from unittest.mock import Mock

    _freeze_now(monkeypatch, NOON)

    ee = Mock()
    images = []

    def image(asset):
        images.append(asset)
        result = Mock()

        def gte(threshold):
            active = "slide_d0" in asset and threshold == 0.4 or "flood_d1" in asset
            result.selfMask.return_value.reduceToVectors.return_value.getInfo.return_value = {
                "features": [{"geometry": _REGION["geometry"]}] if active else []}
            return result

        result.gte.side_effect = gte
        return result

    ee.Image.side_effect = image
    regions = alerts.critical_areas_from_assets(ee)
    assert len(images) == 4
    assert {asset.rsplit("/", 1)[1] for asset in images} == {
        "tpi_slide_d0", "tpi_slide_d1", "tpi_flood_d0", "tpi_flood_d1"}
    db = tmp_path / "subs.sqlite"
    _seed_sqlite(db, _ROWS[:1])
    monkeypatch.setenv("DATABASE_URL", f"sqlite:{db}")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    assert alerts.update_subscribers_from_regions(regions, "2026-11-14") == 1
    import sqlite3

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT last_class, current_label FROM subscribers").fetchone() == (3, "Crítico")


@pytest.mark.parametrize("mode", ["success", "unset", "quiet", "http_error", "api_error", "exception"])
def test_telegram_delivery_and_timestamp(monkeypatch, tmp_path, capsys, mode):
    import sqlite3
    from unittest.mock import Mock

    import requests

    db = tmp_path / "subs.sqlite"
    _seed_sqlite(db, _ROWS)
    old_ts = "2026-11-13T12:00:00Z"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE subscribers SET last_alert_ts = ?", [old_ts])
        conn.execute("UPDATE subscribers SET consent = 0 WHERE chat_id = 'same'")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:{db}")
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    if mode == "unset":
        monkeypatch.delenv("TELEGRAM_TOKEN")
    when = NOON.replace(hour=4) if mode == "quiet" else NOON
    _freeze_now(monkeypatch, when)
    post = Mock(return_value=Mock(status_code=500 if mode == "http_error" else 200))
    post.return_value.json.return_value = {"ok": mode != "api_error"}
    if mode == "exception":
        post.side_effect = requests.ConnectionError("do not log token-bearing URLs")
    monkeypatch.setattr(requests, "post", post)
    assert alerts.update_subscribers_from_regions([_REGION], "2026-11-14") == 2
    with sqlite3.connect(db) as conn:
        got = {r[0]: r[1:] for r in conn.execute(
            "SELECT chat_id, last_class, current_label, data_date, last_alert_ts FROM subscribers")}
    assert got["inside"][:3] == (0 if mode == "quiet" else 3, "Crítico", "2026-11-14")
    assert got["inside"][3] == (when.isoformat().replace("+00:00", "Z") if mode == "success" else old_ts)
    assert got["outside"][3] == old_ts
    assert got["same"][2] is None
    if mode in ("unset", "quiet"):
        post.assert_not_called()
    else:
        post.assert_called_once()
        assert post.call_args.args == ("https://api.telegram.org/bottest-token/sendMessage",)
        payload = post.call_args.kwargs["json"]
        assert payload["chat_id"] == "inside"
        assert "Tipping Point (não oficial)" in payload["text"]
        assert payload["text"].endswith(
            "\nSiga as orientações da Defesa Civil. SMS 40199.")
        assert "Defesa Civil •" not in payload["text"]
    if mode == "unset":
        assert "TELEGRAM_TOKEN unset" in capsys.readouterr().out
    elif mode in ("http_error", "api_error", "exception"):
        assert "send failed" in capsys.readouterr().out


@pytest.mark.parametrize("risk_persists", [True, False])
def test_quiet_delivery_deferred_until_daytime(monkeypatch, tmp_path, risk_persists):
    import sqlite3
    from unittest.mock import Mock

    import requests

    db = tmp_path / "subs.sqlite"
    _seed_sqlite(db, _ROWS[:1])
    monkeypatch.setenv("DATABASE_URL", f"sqlite:{db}")
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    post = Mock(return_value=Mock(status_code=200))
    post.return_value.json.return_value = {"ok": True}
    monkeypatch.setattr(requests, "post", post)
    _freeze_now(monkeypatch, NOON.replace(hour=4))
    alerts.update_subscribers_from_regions([_REGION], "2026-11-14")
    post.assert_not_called()
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT last_class, current_label, last_alert_ts FROM subscribers").fetchone() == (0, "Crítico", None)
    _freeze_now(monkeypatch, NOON)
    alerts.update_subscribers_from_regions([_REGION] if risk_persists else [], "2026-11-14")
    assert post.call_count == int(risk_persists)
    alerts.update_subscribers_from_regions([_REGION] if risk_persists else [], "2026-11-14")
    assert post.call_count == int(risk_persists)
