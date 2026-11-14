"""Offline tests for fetch.opera — fake earthaccess/rasterio/ingest modules."""
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import opera

REPO = Path(__file__).resolve().parents[2]

TARGET = {
    "case": "rs_2024",
    "bbox": [-52.5, -30.5, -50.5, -29.0],
    "start": "2024-05-02",
    "end": "2024-05-31",
}

WTR_URL = ("https://podaac.example/OPERA_L3_DSWx-HLS_T20JLL_"
           "20240503T134821Z_B01_WTR.tif")


def _fake(native_id, date, cloud=None, links=None):
    g = {
        "meta": {"native-id": native_id},
        "umm": {
            "TemporalExtent": {
                "RangeDateTime": {"BeginningDateTime": f"{date}T13:48:21Z"}
            }
        },
    }
    if cloud is not None:
        g["umm"]["CloudCover"] = cloud
    if links:
        g["umm"]["RelatedUrls"] = [{"URL": u} for u in links]
    return g


def _opera_id(day="20240503"):
    return (f"OPERA_L3_DSWx-HLS_T20JLL_{day}T134821Z_20240505T000000Z_"
            "L8_30_v1.0")


def _fake_ea(granules, calls):
    def _download(files, path):
        calls.append(("download", list(files)))
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
        out = []
        for f in files:
            name = (str(f).rsplit("/", 1)[-1] if isinstance(f, str)
                    else "OPERA_granule_B01_WTR.tif")
            p = d / name
            p.write_bytes(b"tif")
            out.append(str(p))
        return out

    return types.SimpleNamespace(
        search_data=lambda **kw: calls.append(("search", kw)) or granules,
        login=lambda **kw: calls.append(("login", kw)) or object(),
        download=_download,
    )


def _fake_rasterio(calls):
    rio = types.ModuleType("rasterio")

    class _DS:
        def __init__(self, path, mode="r", **_kw):
            self._path = Path(path)
            self._mode = mode
            self.crs = "EPSG:32722"
            self.profile = {
                "driver": "GTiff", "dtype": "uint8", "count": 1,
                "width": 8, "height": 8, "crs": self.crs,
                "transform": "t0",
            }

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            if self._mode == "w":
                self._path.write_bytes(b"cog")
            return False

        def write(self, data):
            calls.append(("write", tuple(data.shape)))

    rio.open = lambda path, mode="r", **kw: _DS(path, mode, **kw)
    rmask = types.ModuleType("rasterio.mask")
    rmask.mask = lambda src, geoms, crop=True: (
        calls.append(("mask", geoms))
        or (np.zeros((1, 4, 4), dtype="uint8"), "t1")
    )
    rwarp = types.ModuleType("rasterio.warp")
    rwarp.transform_bounds = lambda *a: tuple(a[2:6])
    return {
        "rasterio": rio,
        "rasterio.mask": rmask,
        "rasterio.warp": rwarp,
    }


def _fake_ingest(calls):
    mod = types.ModuleType("fetch.ingest")
    mod.stage_to_gcs = lambda local_path, *, bucket, case, name: (
        calls.append(("stage", local_path, bucket, case, name))
        or f"gs://{bucket}/{case}/opera/{name}.tif")
    mod.ingest_asset = (
        lambda gcs_uri, *, asset_id, kind="image", force=False: (
            calls.append(("ingest", gcs_uri, asset_id, kind))
            or {"id": asset_id}))
    mod.append_manifest = lambda case, entry: (
        calls.append(("manifest", case, entry)) or Path("assets.json"))
    return mod


def _clear_creds(monkeypatch):
    for var in ("EARTHDATA_TOKEN", "EDL_USER", "EDL_PASS",
                "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def test_resolve_target_event():
    t = opera.resolve_target(event_id="rs-2024-05")
    assert t == {
        "case": "rs_2024",
        "bbox": [-52.5, -30.5, -50.5, -29.0],
        "start": "2024-05-02",   # reference_date
        "end": "2024-05-31",     # window.end
    }


def test_resolve_target_case_and_overrides():
    t = opera.resolve_target(case_id="rs_2024")
    assert t["start"] == "2024-05-02"          # event_date
    assert t["end"] == "2024-05-07"            # + window_after_days
    t = opera.resolve_target(
        event_id="rs-2024-05", case_id="custom",
        start="2024-04-30", end="2024-05-10")
    assert t["case"] == "custom"               # --case overrides name only
    assert t["bbox"] == TARGET["bbox"]         # aoi still from the event
    assert (t["start"], t["end"]) == ("2024-04-30", "2024-05-10")
    with pytest.raises(ValueError):
        opera.resolve_target()


def test_wtr_link_picks_b01():
    g = _fake(_opera_id(), "2024-05-03", links=[
        "https://x/OPERA_B02_BWTR.tif",
        "https://x/OPERA_B05_WTR-1.tif",
        "https://x/OPERA_B03_CONF.tif",
        WTR_URL,
    ])
    assert opera.wtr_link(g) == WTR_URL
    assert opera.wtr_link(_fake("n", "2024-05-03")) is None
    assert opera._wtr_rank("A_B02_BWTR.tif") is None
    assert opera._wtr_rank("A_B05_WTR-1.tif") is None
    assert opera._wtr_rank("A_B01_WTR.tif") == 0


def test_opera_date_native_id_fallback():
    g = _fake(_opera_id("20240503"), "ignored")
    del g["umm"]["TemporalExtent"]
    rec = opera.granule_record(g)
    assert rec["date"] == "2024-05-03"
    assert opera.asset_name(rec) == "opera_dswx_20240503"


def test_search_granules_no_auth(monkeypatch):
    """CMR search runs unauthenticated and sorted; login is never called."""
    calls = []
    granules = [
        _fake(_opera_id("20240510"), "2024-05-10", 80.0),
        _fake(_opera_id("20240503"), "2024-05-03", 5.0, links=[WTR_URL]),
        _fake(_opera_id("20240507"), "2024-05-07"),  # no cloud -> last
    ]
    monkeypatch.setitem(
        sys.modules, "earthaccess", _fake_ea(granules, calls))
    out = opera.search_granules(TARGET, limit=2)
    assert [g["meta"]["native-id"] for g in out] == [
        _opera_id("20240503"), _opera_id("20240510")]
    kind, kw = calls[0]
    assert kind == "search"
    assert kw["short_name"] == "OPERA_L3_DSWX-HLS_V1"
    assert kw["bounding_box"] == tuple(TARGET["bbox"])
    assert kw["temporal"] == ("2024-05-02", "2024-05-31")
    assert not any(c[0] == "login" for c in calls)


def test_fetch_scenes_no_download_needs_nothing(monkeypatch):
    """Search-only path: no creds, no ingest module, no rasterio."""
    calls = []
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_ea(
        [_fake(_opera_id(), "2024-05-03", 5.0, links=[WTR_URL])], calls))
    _clear_creds(monkeypatch)
    recs = opera.fetch_scenes(TARGET, download=False)
    assert recs[0]["local_path"] is None
    assert recs[0]["wtr"] == WTR_URL
    assert recs[0]["date"] == "2024-05-03"
    assert not any(c[0] == "login" for c in calls)


def test_fetch_scenes_download_ingests(monkeypatch, tmp_path):
    """Auth only on download; WTR clip -> COG -> stage -> ingest -> manifest."""
    calls = []
    g = _fake(_opera_id(), "2024-05-03", 5.0, links=[WTR_URL])
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_ea([g], calls))
    for name, mod in _fake_rasterio(calls).items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "fetch.ingest", _fake_ingest(calls))
    monkeypatch.setattr(opera, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GEE_PROJECT", "proj")
    monkeypatch.setenv("NOW_GCS_BUCKET", "bkt")
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")

    recs = opera.fetch_scenes(TARGET, out_dir=tmp_path / "raw")
    rec = recs[0]
    asset_id = ("projects/proj/assets/tipping-point/rs_2024/opera/"
                "opera_dswx_20240503")
    assert ("login", {"strategy": "environment"}) in calls
    assert ("download", [WTR_URL]) in calls
    assert any(c[0] == "mask" for c in calls)             # clip ran
    stage = next(c for c in calls if c[0] == "stage")
    assert stage[2:] == ("bkt", "rs_2024", "opera_dswx_20240503")
    ingest = next(c for c in calls if c[0] == "ingest")
    assert ingest[1] == "gs://bkt/rs_2024/opera/opera_dswx_20240503.tif"
    assert ingest[2:] == (asset_id, "image")
    manifest = next(c for c in calls if c[0] == "manifest")
    assert manifest[1] == "rs_2024"
    assert manifest[2]["asset_id"] == asset_id
    assert manifest[2]["date"] == "2024-05-03"
    assert rec["status"] == "ingested"
    assert rec["asset_id"] == asset_id
    assert rec["local_path"].endswith("opera_dswx_20240503.tif")
    assert Path(rec["local_path"]).exists()


def test_fetch_scenes_dedup_via_assets_json(monkeypatch, tmp_path):
    """asset_id already in data/<case>/assets.json -> skipped, no auth."""
    calls = []
    asset_id = ("projects/proj/assets/tipping-point/rs_2024/opera/"
                "opera_dswx_20240503")
    (tmp_path / "rs_2024").mkdir(parents=True)
    (tmp_path / "rs_2024" / "assets.json").write_text(json.dumps(
        {"assets": [{"asset_id": asset_id}]}))
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_ea(
        [_fake(_opera_id(), "2024-05-03", 5.0, links=[WTR_URL])], calls))
    monkeypatch.setattr(opera, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GEE_PROJECT", "proj")
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")

    recs = opera.fetch_scenes(TARGET, out_dir=tmp_path / "raw")
    assert recs[0]["status"] == "exists"
    assert recs[0]["asset_id"] == asset_id
    kinds = {c[0] for c in calls}
    assert "login" not in kinds and "download" not in kinds
    assert "stage" not in kinds and "ingest" not in kinds


def test_download_without_creds_names_whats_missing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_ea(
        [_fake(_opera_id(), "2024-05-03", 5.0)], calls))
    monkeypatch.setattr(opera, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GEE_PROJECT", "proj")
    monkeypatch.setenv("NOW_GCS_BUCKET", "bkt")
    _clear_creds(monkeypatch)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    with pytest.raises(RuntimeError, match="EARTHDATA_TOKEN") as e:
        opera.fetch_scenes(TARGET, out_dir=tmp_path / "raw")
    assert "EDL_USER" in str(e.value)
    assert not any(c[0] == "login" for c in calls)


def test_partial_edl_falls_back_to_netrc(monkeypatch, tmp_path):
    """EDL_USER alone must not block the ~/.netrc fallback."""
    calls = []
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_ea([], calls))
    _clear_creds(monkeypatch)
    monkeypatch.setenv("EDL_USER", "u")            # EDL_PASS missing
    (tmp_path / ".netrc").write_text("machine urs.earthdata.nasa.gov")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    opera._edl_login()
    assert calls == [("login", {"strategy": "netrc"})]
    # partial pair must not leak an empty EARTHDATA_PASSWORD
    assert "EARTHDATA_PASSWORD" not in os.environ


def test_edl_pair_uses_environment(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "earthaccess", _fake_ea([], calls))
    _clear_creds(monkeypatch)
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    opera._edl_login()
    assert calls == [("login", {"strategy": "environment"})]
    assert os.environ["EARTHDATA_USERNAME"] == "u"
    assert os.environ["EARTHDATA_PASSWORD"] == "p"


def test_cli_dry_run():
    r = subprocess.run(
        [sys.executable, "-m", "pipeline.fetch.opera",
         "--case", "rs_2024", "--dry-run", "--limit", "3"],
        check=False, capture_output=True, text=True, cwd=REPO,
    )
    assert r.returncode == 0, r.stderr
    params = json.loads(r.stdout)
    assert params["short_name"] == "OPERA_L3_DSWX-HLS_V1"
    assert params["case"] == "rs_2024"
    assert params["bounding_box"] == TARGET["bbox"]
    assert params["temporal"] == ["2024-05-02", "2024-05-07"]
    assert params["limit"] == 3


def test_cli_requires_selector():
    r = subprocess.run(
        [sys.executable, "-m", "pipeline.fetch.opera"],
        check=False, capture_output=True, text=True, cwd=REPO,
    )
    assert r.returncode != 0
    assert "--event" in r.stderr or "--case" in r.stderr
