"""Offline tests for pipeline/fetch/gfs.py — stub ee + ImageCollection."""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch import gfs

MS = 3_600_000


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).timestamp() * 1000)


class StubList(list):
    def distinct(self):
        return StubList(dict.fromkeys(self))

    def getInfo(self):
        return list(self)


class StubImage:
    def __init__(self, members=(), bands=None, projection=None):
        self.members = list(members)
        self.bands = bands
        self.names = None
        self.url_params = None
        self._projection = projection

    def clip(self, aoi):
        return self

    def projection(self):
        return self._projection

    def toFloat(self):
        return self

    def rename(self, names):
        self.names = list(names)
        return self

    def getDownloadURL(self, params):
        self.url_params = params
        return "stub://gfs-download"


class StubProjection:
    """Mirrors GFS0P25's real projection: WKT only (no crs code), but
    getInfo() carries the 0.25 deg transform."""

    def __init__(self, transform):
        self.transform = transform

    def getInfo(self):
        # Mirrors the real getInfo: 'wkt' key, no 'crs' code.
        return {"wkt": 'GEOGCS["unknown"]', "transform": self.transform}


class StubCollection:
    """Rows: dicts with creation_time (ms), forecast_hours, value."""

    def __init__(self, rows):
        self.rows = list(rows)

    def filterDate(self, start, end):
        lo, hi = ms(start + "T00:00:00Z"), ms(end + "T00:00:00Z")
        return StubCollection(
            r for r in self.rows if lo <= r["creation_time"] < hi)

    def filter(self, pred):
        op, prop, val = pred
        fns = {"eq": lambda r: r[prop] == val,
               "gt": lambda r: r[prop] > val,
               "lte": lambda r: r[prop] <= val,
               "inList": lambda r: r[prop] in val}
        return StubCollection(r for r in self.rows if fns[op](r))

    def select(self, band):
        return self

    def first(self):
        return StubImage(members=self.rows[:1],
                         projection=StubProjection(
                             [0.25, 0, -180.125, 0, -0.25, 90.125]))

    def sum(self):
        return StubImage(members=self.rows,
                         bands=sum(r["value"] for r in self.rows))

    def aggregate_array(self, prop):
        return StubList(r[prop] for r in self.rows)


def make_ee(col):
    return types.SimpleNamespace(
        ImageCollection=lambda name: col,
        List=lambda v: v if isinstance(v, StubList) else StubList(v),
        Image=types.SimpleNamespace(
            cat=lambda bands: StubImage(bands=bands)),
        Geometry=types.SimpleNamespace(
            Rectangle=lambda bbox: ("rect", tuple(bbox))),
        Filter=types.SimpleNamespace(
            eq=lambda p, v: ("eq", p, v),
            gt=lambda p, v: ("gt", p, v),
            lte=lambda p, v: ("lte", p, v),
            inList=lambda p, v: ("inList", p, list(v))),
    )


def rows_for_run(creation_ms, hours):
    return [{"creation_time": creation_ms, "forecast_hours": h, "value": 1.0}
            for h in hours]


def test_select_run_prefers_00z():
    d = ms("2024-04-27T00:00:00Z")
    col = StubCollection(rows_for_run(d + 6 * MS, [1]) +
                         rows_for_run(d, [1]) + rows_for_run(d + 12 * MS, [1]))
    ee = make_ee(col)
    assert gfs.select_run(col, ee, dt.date(2024, 4, 27)) == d


def test_select_run_falls_back_to_latest():
    d = ms("2024-04-27T00:00:00Z")
    col = StubCollection(rows_for_run(d + 6 * MS, [1]) +
                         rows_for_run(d + 18 * MS, [1]) +
                         rows_for_run(d + 30 * MS, [1]))  # next day: excluded
    ee = make_ee(col)
    assert gfs.select_run(col, ee, dt.date(2024, 4, 27)) == d + 18 * MS


def test_select_run_empty_day_raises():
    col = StubCollection(rows_for_run(ms("2024-04-28T00:00:00Z"), [1]))
    with pytest.raises(RuntimeError, match="no GFS run"):
        gfs.select_run(col, make_ee(col), dt.date(2024, 4, 27))


def test_day_bucket_boundaries_00z():
    """00Z run: day k sums 6-h bucket images (%6==0) valid in (24(k-1), 24k]."""
    d = ms("2024-04-27T00:00:00Z")
    hours = [0, 1, 6, 12, 18, 24, 25, 30, 42, 48, 49, 72, 73]
    col = StubCollection(rows_for_run(d, hours))
    ee = make_ee(col)
    img = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 27))
    assert sorted(r["forecast_hours"] for r in img.members) == \
        [6, 12, 18, 24]
    img2 = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 28))
    assert sorted(r["forecast_hours"] for r in img2.members) == [30, 42, 48]
    img3 = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 29))
    assert sorted(r["forecast_hours"] for r in img3.members) == [72]


def test_day_sum_no_double_count():
    """Conductor's measured reset pattern (2024-04-29 00Z run): hourly
    accumulations reset at each 6h boundary — summing all hours gives
    29.3 mm where the true day total is 8.9 mm. Only %6==0 images sum."""
    d = ms("2024-04-29T00:00:00Z")
    vals = {1: 0.004, 2: 0.04, 3: 0.08, 4: 0.10, 5: 0.22, 6: 0.71,
            7: 0.9, 8: 1.1, 9: 1.3, 10: 1.5, 11: 1.7, 12: 2.9,
            13: 0.6, 14: 0.8, 15: 1.0, 16: 1.2, 17: 1.4, 18: 3.1,
            19: 0.4, 20: 0.5, 21: 0.6, 22: 0.7, 23: 0.8, 24: 2.2}
    rows = [{"creation_time": d, "forecast_hours": h, "value": v}
            for h, v in vals.items()]
    col = StubCollection(rows)
    ee = make_ee(col)
    img = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 29))
    assert img.bands == pytest.approx(vals[6] + vals[12] + vals[18]
                                      + vals[24])
    assert img.bands != pytest.approx(sum(vals.values()))


def test_day_sum_rejects_unaligned_edge():
    d = ms("2024-04-27T00:00:00Z")
    col = StubCollection(rows_for_run(d, [6]))
    ee = make_ee(col)
    with pytest.raises(ValueError, match="6h forecast bucket"):
        gfs.day_sum(col, ee, ("rect", ()), d + 2 * 60 * 60 * 1000,
                    dt.date(2024, 4, 27))


def test_day_bucket_boundaries_nonzero_cycle():
    """06Z run on D: day k = [D+k-1, D+k) maps to forecast hours (18, 42]
    etc. — the same UTC days shifted by the cycle offset."""
    d = ms("2024-04-27T06:00:00Z")
    hours = [0, 6, 18, 19, 24, 42, 43, 66]
    col = StubCollection(rows_for_run(d, hours))
    ee = make_ee(col)
    day1 = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 27))
    # fh 0 has no precip band -> gt(0) drops it even though 0 % 6 == 0
    assert sorted(r["forecast_hours"] for r in day1.members) == [6, 18]
    day2 = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 28))
    assert sorted(r["forecast_hours"] for r in day2.members) == [24, 42]
    day3 = gfs.day_sum(col, ee, ("rect", ()), d, dt.date(2024, 4, 29))
    assert sorted(r["forecast_hours"] for r in day3.members) == [66]


def test_forecast_image_bands():
    d = ms("2024-04-27T00:00:00Z")
    col = StubCollection(rows_for_run(d, [6, 12, 30, 54]))
    ee = make_ee(col)
    img = gfs.forecast_image(col, ee, ("rect", ()), d,
                             dt.date(2024, 4, 27))
    assert img.names == ["d1", "d2", "d3"]
    assert [len(b.members) for b in img.bands] == [2, 1, 1]


def _tiff_bytes(count=3, h=4, w=5):
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.MemoryFile() as mem:
        with mem.open(
                driver="GTiff", height=h, width=w, count=count,
                dtype="float64", crs="EPSG:4326",
                transform=from_origin(-52.5, -29.0, 0.25, 0.25)) as dst:
            dst.write(np.arange(count * h * w,
                                dtype=np.float64).reshape(count, h, w))
        return mem.read()


def test_download_url_uses_native_grid():
    """Source WKT + crs_transform — no `scale` resample (the GFS
    projection has no crs code, only WKT on a sphere datum; EPSG:4326
    would reproject and shift cells)."""
    transform = [0.25, 0, -180.125, 0, -0.25, 90.125]
    img = StubImage()
    gfs._download_url(img, ("rect", ()), "stem", 'GEOGCS["unknown"]',
                      transform)
    params = img.url_params
    assert params["crs"] == 'GEOGCS["unknown"]'
    assert params["crs_transform"] == transform
    assert "scale" not in params


def test_fetch_writes_cog_and_sidecar(tmp_path, monkeypatch):
    d = ms("2024-04-27T00:00:00Z")
    col = StubCollection(rows_for_run(d, [6, 12, 30, 54, 78]))
    ee = make_ee(col)
    monkeypatch.setattr(gfs, "_case_cfg", lambda c: ("rs_2024",
                                                   [-52.5, -30.5, -50.5, -29.0]))
    seen = {}

    def fake_fetch(url, limit=None):
        seen["url"] = url
        return _tiff_bytes()

    manifest = gfs.fetch("rs_2024", dt.date(2024, 4, 27),
                         out_root=tmp_path, ee_module=ee, col=col,
                         fetch=fake_fetch)
    cog = Path(manifest["cog"])
    assert cog == tmp_path / "data/rs_2024/forecast_rain" / \
        "rs_2024_gfs_20240427.tif"
    assert seen["url"] == "stub://gfs-download"

    import rasterio
    with rasterio.open(cog) as src:
        assert src.count == 3 and src.dtypes[0] == "float32"
        assert src.crs.to_string() == "EPSG:4326"
        assert src.descriptions == ("d1", "d2", "d3")
        assert src.transform.e < 0  # north-up
        assert src.tags()["label"] == "forecast: NOAA GFS (issued 2024-04-27)"

    sidecar = json.loads(cog.with_suffix(".json").read_text())
    assert sidecar["creation_time"] == "2024-04-27T00:00:00Z"
    assert sidecar["source"] == "NOAA GFS via GEE"
    assert sidecar["collection"] == "NOAA/GFS0P25"
    assert sidecar["days"]["d2"] == ["2024-04-28", "2024-04-29"]
    assert "25 km" in sidecar["native_resolution"]


def test_list_runs():
    d = ms("2024-04-27T00:00:00Z")
    col = StubCollection(
        rows_for_run(d, [1, 2]) + rows_for_run(d + 6 * MS, [1]) +
        rows_for_run(d + 24 * MS, [1]) + rows_for_run(d + 48 * MS, [1]))
    runs = gfs.list_runs("rs_2024", dt.date(2024, 4, 27),
                         dt.date(2024, 4, 28),
                         ee_module=make_ee(col), col=col)
    assert runs == ["2024-04-27T00:00:00Z", "2024-04-27T06:00:00Z",
                    "2024-04-28T00:00:00Z"]


def test_dry_run_plan_offline(monkeypatch):
    monkeypatch.setattr(gfs, "_case_cfg", lambda c: ("rs_2024", [0, 0, 1, 1]))
    doc = gfs.plan("rs_2024", dt.date(2024, 4, 27))
    assert doc["collection"] == "NOAA/GFS0P25"
    assert doc["cog"].endswith("rs_2024_gfs_20240427.tif")
    assert doc["label"] == "forecast: NOAA GFS (issued 2024-04-27)"
