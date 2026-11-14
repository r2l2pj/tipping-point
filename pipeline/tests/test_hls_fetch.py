"""Offline tests for tp.hls_fetch — fake CMR granule dicts, no network/creds."""
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from tp import hls_fetch
from tp.hls_fetch import (
    _cloud_cover,
    _collection_ref,
    _granule_date,
    granule_record,
    resolve_window,
    sort_granules,
)

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "pipeline" / "scripts" / "10_hls_fetch.py"

FAKE_EVENT = {
    "id": "wa-2025-12",
    "reference_date": "2025-12-10",
    "window": {"start": "2025-11-01", "end": "2026-01-31"},
    "aoi": {"bbox": [-122.6, 47.0, -121.5, 48.5]},
}


def _fake(native_id, date, cloud_cover=None, attrs=None):
    g = {
        "meta": {"native-id": native_id},
        "umm": {
            "GranuleUR": f"{native_id}.ur",
            "TemporalExtent": {
                "RangeDateTime": {"BeginningDateTime": f"{date}T18:39:21.000Z"}
            },
        },
    }
    if cloud_cover is not None:
        g["umm"]["CloudCover"] = cloud_cover
    if attrs:
        g["umm"]["AdditionalAttributes"] = attrs
    return g


def test_collection_ref():
    assert _collection_ref("HLSL30_2.0") == ("HLSL30", "2.0")
    assert _collection_ref("HLSS30_2.0") == ("HLSS30", "2.0")
    assert _collection_ref("HLSL30") == ("HLSL30", None)
    # non-version suffix stays part of the short name
    assert _collection_ref("NISAR_L2_GCOV_BETA_V1") == (
        "NISAR_L2_GCOV_BETA_V1", None,
    )


def test_granule_record_fields():
    g = _fake("HLS.S30.T10TFJ.2025350T183921.v2.0", "2025-12-16", 12.5)
    rec = granule_record(g)
    assert rec == {
        "granule": "HLS.S30.T10TFJ.2025350T183921.v2.0",
        "date": "2025-12-16",
        "cloud_cover": 12.5,
        "local_path": None,
    }
    assert granule_record(g, "data/raw/hls/x")["local_path"] == "data/raw/hls/x"


def test_cloud_cover_additional_attributes():
    g = _fake(
        "HLS.L30.T10TFJ.2025350T182251.v2.0", "2025-12-16",
        attrs=[{"Name": "CLOUD_COVERAGE", "Values": ["34"]}],
    )
    assert _cloud_cover(g) == 34.0
    assert _cloud_cover(_fake("x", "2025-12-16")) is None


def test_granule_date_native_id_fallback():
    g = _fake("HLS.S30.T10TFJ.2025350T183921.v2.0", "ignored")
    del g["umm"]["TemporalExtent"]
    assert _granule_date(g) == "2025-12-16"  # 2025 day-of-year 350


def test_sort_granules_by_cloud_cover():
    g_hi = _fake("HLS.S30.T10TFJ.2025340T183921.v2.0", "2025-12-06", 80.0)
    g_lo = _fake("HLS.L30.T10TFJ.2025350T182251.v2.0", "2025-12-16", 5.0)
    g_none = _fake("HLS.S30.T10TFJ.2025345T183921.v2.0", "2025-12-11")
    out = sort_granules([g_hi, g_none, g_lo])
    assert [r["granule"] for r in map(granule_record, out)] == [
        "HLS.L30.T10TFJ.2025350T182251.v2.0",   # 5% first
        "HLS.S30.T10TFJ.2025340T183921.v2.0",   # 80%
        "HLS.S30.T10TFJ.2025345T183921.v2.0",   # missing -> last
    ]


def test_resolve_window_defaults():
    assert resolve_window(FAKE_EVENT) == ("2025-12-10", "2026-01-31")
    assert resolve_window(FAKE_EVENT, after="2025-12-16") == (
        "2025-12-16", "2026-01-31",
    )
    assert resolve_window(FAKE_EVENT, before="2025-12-31") == (
        "2025-12-10", "2025-12-31",
    )


def _poison_login(monkeypatch):
    def _boom(**_):
        raise AssertionError("login should not be called on the search path")

    monkeypatch.setattr(hls_fetch, "_edl_login", _boom)


def _fake_earthaccess(granules, calls):
    ns = types.SimpleNamespace(
        session=types.SimpleNamespace(headers={}),
        login=lambda **kw: "AUTH",
        search_datasets=lambda **kw: [object()],
        search_data=lambda short_name, **kw: granules.get(short_name, []),
        download=lambda gs, path: calls.append(path) or [Path(path)],
    )
    ns.get_requests_https_session = lambda: ns.session
    return ns


def test_search_granules_no_creds(monkeypatch):
    """CMR search runs without EDL creds — login is download-only."""
    granules = {
        "HLSL30": [_fake("HLS.L30.T10TFJ.2025350T182251.v2.0", "2025-12-16", 80.0)],
        "HLSS30": [_fake("HLS.S30.T10TFJ.2025350T183921.v2.0", "2025-12-16", 5.0)],
    }
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_earthaccess(granules, []))
    _poison_login(monkeypatch)
    out = hls_fetch.search_granules(FAKE_EVENT)
    assert [g["meta"]["native-id"] for g in out] == [
        "HLS.S30.T10TFJ.2025350T183921.v2.0",  # 5% before 80%
        "HLS.L30.T10TFJ.2025350T182251.v2.0",
    ]


def test_fetch_scenes_download_logs_in(monkeypatch, tmp_path):
    """Login happens at download time; records get per-granule local_path."""
    calls = []
    granules = {
        "HLSL30": [_fake("HLS.L30.T10TFJ.2025350T182251.v2.0", "2025-12-16", 5.0)],
        "HLSS30": [],
    }
    monkeypatch.setitem(
        sys.modules, "earthaccess", _fake_earthaccess(granules, calls)
    )
    monkeypatch.setattr(hls_fetch, "_edl_login", lambda: calls.append("login"))
    recs = hls_fetch.fetch_scenes(FAKE_EVENT, limit=1, out_dir=tmp_path)
    assert calls[0] == "login"
    assert len(recs) == 1
    assert recs[0]["local_path"] == str(
        tmp_path / "wa-2025-12" / "HLS.L30.T10TFJ.2025350T182251.v2.0"
    )


def test_edl_login_uses_token(monkeypatch, tmp_path):
    """EARTHDATA_TOKEN -> earthaccess.login(strategy='environment')."""
    for var in (
        "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
        "NETRC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.netrc
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    calls = []
    fake = types.SimpleNamespace(
        login=lambda **kw: calls.append(kw) or "AUTH"
    )
    monkeypatch.setitem(sys.modules, "earthaccess", fake)
    assert hls_fetch._edl_login() == "AUTH"
    assert calls == [{"strategy": "environment"}]


def test_edl_login_no_creds_clean_error(monkeypatch, tmp_path):
    for var in (
        "EARTHDATA_TOKEN", "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
        "EARTHDATA_PASSWORD", "NETRC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="EARTHDATA_TOKEN"):
        hls_fetch._edl_login()


def test_fetch_scenes_attaches_bearer_header(monkeypatch, tmp_path):
    """EARTHDATA_TOKEN -> Bearer pinned on the earthaccess download session."""
    calls = []
    granules = {
        "HLSL30": [_fake("HLS.L30.T10TFJ.2025350T182251.v2.0", "2025-12-16", 5.0)],
        "HLSS30": [],
    }
    fake = _fake_earthaccess(granules, calls)
    monkeypatch.setitem(sys.modules, "earthaccess", fake)
    for var in (
        "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
        "NETRC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.netrc
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    hls_fetch.fetch_scenes(FAKE_EVENT, limit=1, out_dir=tmp_path)
    assert fake.session.headers["Authorization"] == "Bearer faketoken"


def test_fetch_scenes_no_download_no_creds(monkeypatch):
    granules = {
        "HLSL30": [],
        "HLSS30": [_fake("HLS.S30.T10TFJ.2025350T183921.v2.0", "2025-12-16", 5.0)],
    }
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_earthaccess(granules, []))
    _poison_login(monkeypatch)
    recs = hls_fetch.fetch_scenes(FAKE_EVENT, download=False)
    assert recs[0]["local_path"] is None
    assert recs[0]["granule"] == "HLS.S30.T10TFJ.2025350T183921.v2.0"


def test_script_help():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=False, capture_output=True, text=True, cwd=REPO,
    )
    assert r.returncode == 0
    assert "--event" in r.stdout and "--dry-run" in r.stdout


def test_script_dry_run_no_creds():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--event", "wa-2025-12",
         "--dry-run", "--limit", "3"],
        check=False, capture_output=True, text=True, cwd=REPO,
    )
    assert r.returncode == 0, r.stderr
    params = json.loads(r.stdout)
    assert params["short_names"] == ["HLSL30_2.0", "HLSS30_2.0"]
    assert params["temporal"] == ["2025-12-10", "2026-01-31"]
    assert params["bounding_box"] == [-122.6, 47.0, -121.5, 48.5]
    assert params["limit"] == 3
