"""live.staleness (L13b): fresh/stale/broken Now manifests, stdin + file."""
import datetime as dt
import io
import json

from live import staleness

NOW = dt.datetime(2026, 11, 15, 12, 0, 0, tzinfo=dt.UTC)


def test_fresh_manifest_is_ok():
    m = {"updated_at": "2026-11-15T09:00:00Z"}
    assert staleness.stale_error(m, NOW, 21600) is None


def test_stale_manifest_message():
    m = {"updated_at": "2026-11-15T02:00:00Z"}
    err = staleness.stale_error(m, NOW, 21600)
    assert err == ("Now data stale: updated_at 2026-11-15T02:00:00Z is "
                   "10.0 h old (> 6 h, L13b)")


def test_boundary_is_stale_only_past_max_age():
    m = {"updated_at": "2026-11-15T06:00:00Z"}          # exactly 6 h
    assert staleness.stale_error(m, NOW, 21600) is None
    m2 = {"updated_at": "2026-11-15T05:59:59Z"}
    assert "stale" in staleness.stale_error(m2, NOW, 21600)


def test_missing_updated_at_fails():
    for m in ({}, {"updated_at": ""}, {"updated_at": None}, {"updated_at": 42},
              "not-a-dict"):
        err = staleness.stale_error(m, NOW, 21600)
        assert err and "updated_at" in err


def test_invalid_updated_at_fails():
    err = staleness.stale_error({"updated_at": "not a ts"}, NOW, 21600)
    assert "not a timestamp" in err and "not a ts" in err


def test_updated_at_without_tz_treated_as_utc():
    m = {"updated_at": "2026-11-15T10:30:00"}
    assert staleness.stale_error(m, NOW, 21600) is None
    assert "10.0 h old (> 5.5 h" in staleness.stale_error(
        {"updated_at": "2026-11-15T02:00:00"}, NOW, 19800)  # 5.5 h max


def test_main_fresh_file(tmp_path, capsys):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"updated_at": dt.datetime.now(dt.UTC)
                             .isoformat().replace("+00:00", "Z")}))
    assert staleness.main([str(p), "--max-age-s", "21600"]) == 0
    assert "::error::" not in capsys.readouterr().out


def test_main_stale_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin",
                        io.StringIO('{"updated_at": "2020-01-01T00:00:00Z"}'))
    assert staleness.main(["--max-age-s", "21600"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::Now data stale: updated_at "
                          "2020-01-01T00:00:00Z")
    assert "L13b" in out


def test_main_bad_json_fails(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{oops"))
    assert staleness.main([]) == 1
    assert "::error::cannot read Now manifest" in capsys.readouterr().out


def test_main_stale_after_s_env(monkeypatch, capsys):
    monkeypatch.setenv("STALE_AFTER_S", "60")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
        "updated_at": (dt.datetime.now(dt.UTC)
                       - dt.timedelta(seconds=120)).isoformat()})))
    assert staleness.main([]) == 1          # 120 s > 60 s
    assert "::error::Now data stale" in capsys.readouterr().out
