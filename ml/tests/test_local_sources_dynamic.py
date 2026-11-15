"""Offline tests for ml.local.sources_dynamic — mocked HTTP, synthetic arrays.

No network: CMR/OPeNDAP/POWER/S3 are monkeypatched. nc4 bytes are real
in-memory HDF5 built with h5py (same parse path as the wire).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re

import h5py
import numpy as np
import pytest
from affine import Affine

from ml.local import settings
from ml.local import sources_dynamic as sd
from ml.local import sources_static as ss

BBOX = (-51.5, -29.6, -50.5, -28.9)          # settings.SMOKE_BBOX
NY, NX = 8, 11                              # imerg window for BBOX


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return tmp_path


class FakeResp:
    def __init__(self, content=b"", payload=None, status=200, text=""):
        self.status_code = status
        self.content = content
        self._payload = payload
        self.text = text or (payload if isinstance(payload, str) else "")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _imerg_nc4(field2d: np.ndarray, monthly: bool = False) -> bytes:
    """(ny, nx) slab -> nc4 bytes; monthly files nest var under /Grid/."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        f.create_dataset("Grid/precipitation" if monthly else "precipitation",
                         data=np.asarray(field2d, np.float32).T[None])
    return buf.getvalue()


def _smap_nc4(r0: int, r1: int, c0: int, c1: int,
              row_lats: np.ndarray, col_lons: np.ndarray,
              var_base: dict[str, float] | None = None,
              coords: bool = True, fill_at: bool = False) -> bytes:
    """Synthetic SPL4SMGP subset: each var = global row + col/100."""
    ny, nx = r1 - r0 + 1, c1 - c0 + 1
    rr = r0 + np.arange(ny)[:, None]
    cc = c0 + np.arange(nx)[None, :]
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        g = f.create_group("Geophysical_Data")
        for i, v in enumerate(sd.SMAP_VARS):
            base = (var_base or {}).get(v, 0.0)
            a = (rr + cc / 100.0 + base).astype(np.float32)
            if fill_at and v == "sm_rootzone":
                a[0, 0] = -9999.0
            g.create_dataset(v, data=a)
        if coords:
            f.create_dataset("cell_lat",
                             data=np.repeat(row_lats[r0:r1 + 1, None],
                                            nx, axis=1).astype(np.float32))
            f.create_dataset("cell_lon",
                             data=np.repeat(col_lons[None, c0:c1 + 1],
                                            ny, axis=0).astype(np.float32))
    return buf.getvalue()


def _smap_lmc_nc4(r0, r1, c0, c1, row_lats, col_lons) -> bytes:
    ny, nx = r1 - r0 + 1, c1 - c0 + 1
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        g = f.create_group("Land-Model-Constants_Data")
        g.create_dataset("mwrtm_clay",
                         data=np.full((ny, nx), 0.31, np.float32))
        g.create_dataset("mwrtm_sand",
                         data=np.full((ny, nx), 0.42, np.float32))
        g.create_dataset("clsm_poros",
                         data=np.full((ny, nx), 0.53, np.float32))
        f.create_dataset("cell_lat",
                         data=np.repeat(row_lats[r0:r1 + 1, None],
                                        nx, axis=1).astype(np.float32))
        f.create_dataset("cell_lon",
                         data=np.repeat(col_lons[None, c0:c1 + 1],
                                        ny, axis=0).astype(np.float32))
    return buf.getvalue()


_LMC_DMR = """<Dataset xmlns="http://xml.opendap.org/ns/DAP/4.0#">
  <Group name="Land-Model-Constants_Data">
    <Float32 name="mwrtm_clay"/><Float32 name="mwrtm_sand"/>
    <Float32 name="clsm_poros"/>
  </Group></Dataset>"""

# synthetic EASE-2 axes: 30 rows (lat -30..-27.4 desc... actually asc here
# keeps _nearest honest), 40 cols
_ROW_LATS = np.linspace(-30.0, -27.4, 30)
_COL_LONS = np.linspace(-52.5, -49.0, 40)


def _fake_smap_http(fill_at: bool = False):
    """_dap_get fake bound to the synthetic axes."""

    def dap_get(base, ce, timeout=300):
        if "cell_lat[0:1623][0:0]" in ce:
            buf = io.BytesIO()
            with h5py.File(buf, "w") as f:
                f.create_dataset(
                    "cell_lat", data=_ROW_LATS[:, None].astype(np.float32))
            return buf.getvalue()
        if "cell_lon[0:0][0:3855]" in ce:
            buf = io.BytesIO()
            with h5py.File(buf, "w") as f:
                f.create_dataset(
                    "cell_lon", data=_COL_LONS[None, :].astype(np.float32))
            return buf.getvalue()
        m = re.search(r"\[(\d+):(\d+)\]\[(\d+):(\d+)\]", ce)
        r0, r1, c0, c1 = (int(x) for x in m.groups())
        coords = "cell_lat" in ce
        if "lmc" in base:
            return _smap_lmc_nc4(r0, r1, c0, c1, _ROW_LATS, _COL_LONS)
        return _smap_nc4(r0, r1, c0, c1, _ROW_LATS, _COL_LONS,
                         coords=coords, fill_at=fill_at)

    return dap_get


def _cmr_resp(names: list[str]) -> FakeResp:
    return FakeResp(payload={"feed": {"entry": [{"title": t}
                                                for t in names]}})


def test_imerg_window_and_axes():
    lon0, lon1, lat0, lat1 = sd._imerg_window(BBOX)
    assert (lon0, lon1, lat0, lat1) == (1285, 1295, 604, 611)
    lats, lons = sd._imerg_axes(lon0, lon1, lat0, lat1)
    assert lats[0] == pytest.approx(-29.55)
    assert lats[-1] == pytest.approx(-28.85)
    assert lons[0] == pytest.approx(-51.45)
    assert lons[-1] == pytest.approx(-50.45)


def _imerg_cmr(days):
    return _cmr_resp([f"GPM_3IMERGDE.07:3B-DAY-E.MS.MRG.3IMERG."
                      f"{d:%Y%m%d}-S000000-E235959.V07B.nc4"
                      for d in days])


def _patch_imerg(monkeypatch, day_value):
    """_dap_get returns a slab whose value is <day of month>."""
    def dap_get(url, ce, timeout=300):
        m = re.search(r"3IMERG\.(\d{8})-S", url)
        v = float(day_value(dt.datetime.strptime(m.group(1), "%Y%m%d")
                            .date()))
        return _imerg_nc4(np.full((NY, NX), v, np.float32))

    def http_get(url, params=None, **kw):
        if "cmr" in url:
            # honour the requested temporal window
            start_s, end_s = params["temporal"].split(",")
            s = dt.date.fromisoformat(start_s[:10])
            e = dt.date.fromisoformat(end_s[:10]) - dt.timedelta(days=1)
            days = list(sd._daterange(s, e))
            return _imerg_cmr(days)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(sd, "_dap_get", dap_get)
    monkeypatch.setattr(ss, "_http_get", http_get)


def test_fetch_imerg_daily_and_sample(data_dir, monkeypatch):
    _patch_imerg(monkeypatch, lambda d: d.day)   # day-of-month mm
    out = sd.fetch_imerg_daily(dt.date(2024, 5, 1), dt.date(2024, 5, 3),
                               bbox=BBOX)
    assert out.exists()
    imerg = sd.ImergDaily.load(BBOX)
    assert list(imerg.dates) == [np.datetime64(f"2024-05-0{d}")
                                 for d in (1, 2, 3)]
    lon = np.array([-51.0, -51.2])
    lat = np.array([-29.3, -29.45])
    v = imerg.daily(lon, lat, [dt.date(2024, 5, 2)] * 2)
    np.testing.assert_allclose(v, [2.0, 2.0])
    # missing day -> NaN
    v = imerg.daily(lon, lat, [dt.date(2024, 5, 9)] * 2)
    assert np.isnan(v).all()
    # window sum: days 1+2+3 = 6 mm
    s = imerg.window_sum(lon, lat, [dt.date(2024, 5, 3)] * 2, 3)
    np.testing.assert_allclose(s, [6.0, 6.0])
    # outside grid -> NaN
    s = imerg.daily([0.0], [0.0], [dt.date(2024, 5, 1)])
    assert np.isnan(s).all()
    arr, tr = imerg.grid(dt.date(2024, 5, 1))
    assert arr.shape == (NY, NX) and arr[0, 0] == pytest.approx(1.0)
    assert tr.a == pytest.approx(0.1) and tr.e == pytest.approx(0.1)
    assert tr.c == pytest.approx(-51.5) and tr.f == pytest.approx(-29.6)
    # resumable: second fetch adds nothing new
    out2 = sd.fetch_imerg_daily(dt.date(2024, 5, 1), dt.date(2024, 5, 3),
                                bbox=BBOX)
    assert out2.exists()


def test_window_sum_missing_day_is_nan(data_dir, monkeypatch):
    _patch_imerg(monkeypatch, lambda d: 1.0)
    sd.fetch_imerg_daily(dt.date(2024, 5, 1), dt.date(2024, 5, 2),
                         bbox=BBOX)
    sd.fetch_imerg_daily(dt.date(2024, 5, 4), dt.date(2024, 5, 4),
                         bbox=BBOX)   # day 3 absent
    imerg = sd.ImergDaily.load(BBOX)
    # end=05-04 ndays=3 covers days 2,3,4 — day 3 missing -> NaN
    s = imerg.window_sum([-51.0], [-29.3], [dt.date(2024, 5, 4)], 3)
    assert np.isnan(s[0])
    s = imerg.window_sum([-51.0], [-29.3], [dt.date(2024, 5, 4)], 2)
    assert np.isnan(s[0])               # days 3+4 — 3 still missing
    s = imerg.window_sum([-51.0], [-29.3], [dt.date(2024, 5, 4)], 1)
    assert s[0] == pytest.approx(1.0)
    s = imerg.window_sum([-51.0], [-29.3], [dt.date(2024, 5, 2)], 2)
    assert s[0] == pytest.approx(2.0)   # days 1+2 present


def test_rain_clim_and_features(data_dir, monkeypatch):
    _patch_imerg(monkeypatch, lambda d: d.day)      # day-of-month mm
    sd.fetch_imerg_daily(dt.date(2024, 4, 27), dt.date(2024, 5, 3),
                         bbox=BBOX)               # 7 days: 27..3
    series = [27, 28, 29, 30, 1, 2, 3]
    r3 = [series[i - 2] + series[i - 1] + series[i]
          for i in range(2, 7)]                   # [84, 87, 60, 33, 6]
    out = sd.build_rain_climatology(years=(2024, 2024), bbox=BBOX)
    assert out.exists()
    clim = sd.RainClim.load(BBOX)
    p99 = clim.p99_daily([-51.0], [-29.3])
    assert p99[0] == pytest.approx(np.nanpercentile(series, 99))
    p993 = clim.p99_3day([-51.0], [-29.3])
    assert p993[0] == pytest.approx(np.nanpercentile(r3, 99))
    # all ends (doy 120..124) sit within +-15 d of doy 122
    p90, p95, p99 = clim.pctl_3day([-51.0], [-29.3], [122])
    np.testing.assert_allclose(
        [p90[0], p95[0], p99[0]],
        [np.nanpercentile(r3, q) for q in (90, 95, 99)])
    df = sd.rain_features([-51.0, -51.2], [-29.3, -29.45],
                          [dt.date(2024, 5, 3)] * 2,
                          imerg=sd.ImergDaily.load(BBOX), clim=clim)
    assert list(df.columns) == settings.RAIN
    np.testing.assert_allclose(df["rain_24h"], [3.0, 3.0])
    np.testing.assert_allclose(df["rain_72h"], [6.0, 6.0])
    np.testing.assert_allclose(df["rain_7d"], [120.0, 120.0])
    np.testing.assert_allclose(
        df["rain_24h_rel"], [3.0 / np.nanpercentile(series, 99)] * 2)
    np.testing.assert_allclose(
        df["rain_72h_rel"], [6.0 / np.nanpercentile(r3, 99)] * 2)


def test_fetch_imerg_monthly(data_dir, monkeypatch):
    names = [f"GPM_3IMERGM.07:3B-MO.MS.MRG.3IMERG.2023{m:02d}01-S000000"
             f"-E235959.{m:02d}.V07B.HDF5" for m in (1, 2)]

    def dap_get(url, ce, timeout=300):
        # monthly mean rate mm/hr — jan 0.1, feb 0.2
        v = 0.1 if "202301" in url else 0.2
        return _imerg_nc4(np.full((NY, NX), v, np.float32), monthly=True)

    def http_get(url, params=None, **kw):
        if "cmr" in url:
            return _cmr_resp(names)
        raise AssertionError(url)

    monkeypatch.setattr(sd, "_dap_get", dap_get)
    monkeypatch.setattr(ss, "_http_get", http_get)
    out = sd.fetch_imerg_monthly(years=(2023, 2023), bbox=BBOX)
    z = dict(np.load(out))
    assert z["rain_mean"].shape == (NY, NX)
    assert z["rain_mean"][0, 0] == pytest.approx(
        (0.15 * 24 * 365.25), rel=1e-5)


def _seed_smap_axes(data_dir):
    p = data_dir / "smap" / "axes.npz"
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez(p, row_lats=_ROW_LATS, col_lons=_COL_LONS)


def test_smap_daily_sample_and_constants(data_dir, monkeypatch):
    _seed_smap_axes(data_dir)
    monkeypatch.setattr(sd, "_dap_get", _fake_smap_http(fill_at=True))
    names = [f"SMAP_L4_SM_gph_2024050{d}T103000_Vv8012_001.h5_u9loH5HD"
             for d in (1, 2)]
    monkeypatch.setattr(
        ss, "_http_get",
        lambda url, params=None, **kw: (_cmr_resp(names)
                                        if "cmr" in url else
                                        FakeResp(text=_LMC_DMR)))
    sd.fetch_smap_daily([dt.date(2024, 5, 1), dt.date(2024, 5, 2)],
                        bbox=BBOX)
    smap = sd.SmapDaily.load(BBOX)
    df = smap.sample([-51.0], [-29.3], [dt.date(2024, 5, 1)])
    assert list(df.columns) == list(sd.SMAP_VARS)
    # expected: row + col/100 at nearest cell
    r = int(sd._nearest(_ROW_LATS, [-29.3])[0])
    c = int(sd._nearest(_COL_LONS, [-51.0])[0])
    assert df["sm_surface"][0] == pytest.approx(r + c / 100.0)
    # fill -9999 -> NaN (server-sent fill at window corner r0,c0)
    z = dict(np.load(data_dir / "smap_daily" / sd._key(BBOX)
                     / "20240501.npz"))
    df = smap.sample([float(z["lons"][0])], [float(z["lats"][0])],
                     [dt.date(2024, 5, 1)])
    assert np.isnan(df["sm_rootzone"][0])

    out = sd.fetch_smap_constants(bbox=BBOX)
    z = dict(np.load(out))
    assert {"clay", "sand", "porosity"} <= set(z)
    tex = sd.soil_texture([-51.0], [-29.3], bbox=BBOX)
    assert list(tex.columns) == settings.SOIL_TEXTURE
    assert tex["clay"][0] == pytest.approx(0.31)
    assert tex["porosity"][0] == pytest.approx(0.53)


def test_soil_mean_and_climate(data_dir, monkeypatch):
    _seed_smap_axes(data_dir)
    monkeypatch.setattr(sd, "_dap_get", _fake_smap_http())
    names = [f"SMAP_L4_SM_gph_2024050{d}T103000_Vv8012_001.h5_u9loH5HD"
             for d in (1, 2)]

    def http_get(url, params=None, **kw):
        if "cmr" in url:
            return _cmr_resp(names)
        return FakeResp(text=_LMC_DMR)
    monkeypatch.setattr(ss, "_http_get", http_get)
    sd.fetch_smap_constants(bbox=BBOX)
    out = sd.build_soil_mean(years=(2024, 2024), every=1, bbox=BBOX)
    z = dict(np.load(out))
    assert z["soil_mean"].shape == (len(z["lats"]), len(z["lons"]))
    # slabs encode GLOBAL (row + col/100); window lats/lons are a subset
    # of the seeded axes — nearest global index gives the expected value.
    r = int(sd._nearest(_ROW_LATS, [-29.3])[0])
    c = int(sd._nearest(_COL_LONS, [-51.0])[0])
    i = int(sd._nearest(z["lats"], [-29.3])[0])
    j = int(sd._nearest(z["lons"], [-51.0])[0])
    assert z["soil_mean"][i, j] == pytest.approx(r + c / 100.0)
    # rain_mean present too -> climate() has both columns
    rm = data_dir / "imerg_monthly" / sd._key(BBOX) / "rain_mean.npz"
    rm.parent.mkdir(parents=True, exist_ok=True)
    lats, lons = sd._imerg_axes(1285, 1295, 604, 611)
    np.savez(rm, rain_mean=np.full((NY, NX), 1500.0, np.float32),
             lats=lats.astype(np.float32), lons=lons.astype(np.float32))
    c = sd.climate([-51.0], [-29.3], bbox=BBOX)
    assert list(c.columns) == settings.CLIMATE
    assert c["rain_mean"][0] == pytest.approx(1500.0)
    assert np.isfinite(c["soil_mean"][0])
    g = sd.climate_on_grid(Affine(0.05, 0, -51.5, 0, -0.05, -28.9),
                           (14, 20), bbox=BBOX)
    assert set(g) == {"rain_mean", "soil_mean", "clay", "sand", "porosity"}
    assert g["rain_mean"].shape == (14, 20)
    assert np.isfinite(g["clay"]).all()


def test_power_points(data_dir, monkeypatch):
    calls = []

    def power_get(params):
        calls.append(params)
        daily = {f"202405{d:02d}": float(d) for d in range(1, 4)}
        daily["20240504"] = -999.0                     # fill day
        return {"properties": {"parameter":
                               {"IMERG_PRECTOT": daily}}}

    monkeypatch.setattr(sd, "_power_get", power_get)
    out = sd.fetch_power_points([-51.0, -51.2, -51.0],
                                [-29.3, -29.45, -29.3])
    assert out.exists()
    assert len(calls) == 2                            # cell dedup
    pr = sd.PowerRain.load()
    v = pr.daily([-51.0], [-29.3], [dt.date(2024, 5, 2)])
    assert v[0] == pytest.approx(2.0)
    s = pr.window_sum([-51.0], [-29.3], [dt.date(2024, 5, 3)], 3)
    assert s[0] == pytest.approx(6.0)
    s = pr.window_sum([-51.0], [-29.3], [dt.date(2024, 5, 4)], 2)
    assert np.isnan(s[0])                             # fill -> NaN
    assert pr.p99_daily([-51.0], [-29.3])[0] == pytest.approx(2.98)
    p90, p95, p99 = pr.pctl_3day([-51.0], [-29.3], [123])
    np.testing.assert_allclose([p90[0], p95[0], p99[0]], 6.0)
    df = sd.rain_features([-51.0], [-29.3], [dt.date(2024, 5, 3)],
                          imerg=pr, clim=pr)
    assert list(df.columns) == settings.RAIN
    assert df["rain_72h"][0] == pytest.approx(6.0)
    # resumable top-up: stale cache refetches only the tail and merges
    # (files written < 24 h ago count as fresh -> backdate the cached cells)
    import os
    import time
    for f in (data_dir / "power").rglob("*.json"):
        os.utime(f, (time.time() - 2 * 86400,) * 2)
    sd.fetch_power_points([-51.0], [-29.3])
    assert len(calls) == 3
    assert calls[-1]["start"] == "20240505"
    pr._cells.clear()                               # drop memoized series
    v = pr.daily([-51.0], [-29.3], [dt.date(2024, 5, 2)])
    assert v[0] == pytest.approx(2.0)               # merge kept old days


def test_smap_points(data_dir, monkeypatch):
    _seed_smap_axes(data_dir)
    monkeypatch.setattr(sd, "_dap_get", _fake_smap_http())
    names = [f"SMAP_L4_SM_gph_2024050{d}T103000_Vv8012_001.h5_u9loH5HD"
             for d in (1, 2)]
    monkeypatch.setattr(ss, "_http_get",
                        lambda url, params=None, **kw: _cmr_resp(names))
    sd.fetch_smap_points([-51.0, -51.2, -50.0], [-29.3, -29.45, -28.9],
                         [dt.date(2024, 5, 1), dt.date(2024, 5, 2)])
    sp = sd.SmapPoints.load()
    df = sp.sample([-51.0, -50.0], [-29.3, -28.9],
                   [dt.date(2024, 5, 1)] * 2)
    assert list(df.columns) == list(sd.SMAP_VARS)
    for i, (lo, la) in enumerate(((-51.0, -29.3), (-50.0, -28.9))):
        r = int(sd._nearest(_ROW_LATS, [la])[0])
        c = int(sd._nearest(_COL_LONS, [lo])[0])
        assert df["sm_rootzone"][i] == pytest.approx(r + c / 100.0)
    # unrequested point -> NaN
    df = sp.sample([-49.5], [-27.6], [dt.date(2024, 5, 1)])
    assert np.isnan(df["sm_surface"][0])


def _fake_gfs(monkeypatch, val=2.5, missing=()):
    lats = np.arange(-90.0, 90.0, 0.25)
    lons = np.arange(-179.75, 180.0, 0.25)
    arr = np.full((len(lats), len(lons)), val, np.float64)
    monkeypatch.setattr("live.fetch._gfs_pick_run",
                        lambda issued: (issued, 0))

    def apcp(day, hh, fh):
        if fh in missing:
            return None
        return arr, lats, lons
    monkeypatch.setattr("live.fetch._gfs_apcp_grid", apcp)


def test_gfs_daily_columns(data_dir, monkeypatch):
    _fake_gfs(monkeypatch, val=2.5, missing=(48,))
    sd.fetch_gfs([dt.date(2024, 5, 1)], bbox=BBOX, lead_days=4)
    g = sd.gfs_daily([-51.0], [-29.3], [dt.date(2024, 5, 1)], 4,
                     bbox=BBOX)
    assert g.shape == (1, 4)
    assert g[0, 0] == pytest.approx(10.0)     # fh 6..24 -> 4 x 2.5
    assert np.isnan(g[0, 1])                  # fh 48 missing
    assert g[0, 2] == pytest.approx(10.0)
    assert g[0, 3] == pytest.approx(10.0)


def test_gfs_pre_archive_nan(data_dir, monkeypatch):
    sd.fetch_gfs([dt.date(2020, 1, 1)], bbox=BBOX, lead_days=2)
    meta = json.loads((data_dir / "gfs" / sd._key(BBOX) / "20200101"
                       / "meta.json").read_text())
    assert "pre-archive" in meta["note"]
    g = sd.gfs_daily([-51.0], [-29.3], [dt.date(2020, 1, 1)], 2,
                     bbox=BBOX)
    assert np.isnan(g).all()


def test_power_cell_key():
    lo, la = sd._power_cell([-51.0], [-29.3])
    assert lo[0] == pytest.approx(-50.95)
    assert la[0] == pytest.approx(-29.25)


def test_as_dates():
    out = sd._as_dates([dt.date(2024, 5, 1), np.datetime64("2024-05-02")])
    assert list(out) == [np.datetime64("2024-05-01"),
                         np.datetime64("2024-05-02")]
    assert out.dtype == "datetime64[D]"
