"""End-to-end run_now: fake fetchers + synthetic static -> honest artifacts.

SMAP gaps must surface as nodata (transparent PNGs, NaN in the float TPI COGs,
`nodata_km2` in summary) — never filled with a fake 0.7 wetness.
"""
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import xarray as xr

import live.fetch
import live.publish
from live.grid import dst_lats_lons, pixel_area_km2
from live.run_now import parse_args, run

BOUNDS = (-52.5, -30.0, -52.0, -29.5)   # ~0.5 deg box inside Rio Grande do Sul
DATE = "2026-11-14"


def _fake_da(times, fill):
    """(time, lat, lon) DataArray whose coords cover the dst grid."""
    w, s, e, n = BOUNDS
    lats = np.arange(s - 0.2, n + 0.2, 0.05)
    lons = np.arange(w - 0.2, e + 0.2, 0.05)
    data = np.full((len(times), lats.size, lons.size), fill)
    return xr.DataArray(
        data, dims=("time", "lat", "lon"),
        coords={"time": times, "lat": lats, "lon": lons})


def _write_static(static_dir):
    lats, lons = dst_lats_lons(BOUNDS)
    h, w = lats.size, lons.size
    np.savez(
        static_dir / "sul_static.npz",
        lats=lats, lons=lons,
        p90=np.full((12, h, w), 50.0),
        p95=np.full((12, h, w), 80.0),
        p99=np.full((12, h, w), 120.0),
        t_slide=np.full((h, w), 0.5),
        t_flood=np.full((h, w), 0.3),
        slope_deg=np.full((h, w), 20.0),
        hand_m=np.full((h, w), 8.0),
        pop=np.full((h, w), 1000.0),
    )

from live import now_geojson  # noqa: E402


@pytest.fixture
def fake_now(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    _write_static(static_dir)
    out_dir = tmp_path / "now_out"

    imerg_times = np.arange("2026-11-08", "2026-11-15", dtype="datetime64[D]")
    monkeypatch.setattr(
        live.fetch, "fetch_imerg_daily",
        lambda bounds, end, days=7: (
            _fake_da(imerg_times, 200.0), "2026-11-14T00Z"))

    # SMAP point grid with a NaN hole in the NW corner (a real data gap)
    sm_lats, sm_lons = np.meshgrid(
        np.linspace(BOUNDS[1], BOUNDS[3], 21),
        np.linspace(BOUNDS[0], BOUNDS[2], 21), indexing="ij")
    sm_vals = np.full(sm_lats.shape, 0.6)
    sm_vals[:7, :7] = np.nan
    monkeypatch.setattr(
        live.fetch, "fetch_smap_l4",
        lambda bounds, days_back=10: (sm_lats, sm_lons, sm_vals, "SPL4SMGP-test"))

    fc_times = np.arange("2026-11-14", "2026-11-18", dtype="datetime64[D]")  # D..D+3
    monkeypatch.setattr(
        live.fetch, "fetch_gfs_daily",
        lambda bounds, issued=None: (
            _fake_da(fc_times, 50.0), "2026-11-14T00:00:00Z"))

    # hermetic: reference layers (IBGE fetch, MCD12Q1 read) are no-ops here
    monkeypatch.setattr(now_geojson, "ensure_municipal_boundaries",
                        lambda gj_dir, bounds: None)
    monkeypatch.setattr(now_geojson, "ensure_permanent_water",
                        lambda gj_dir, bounds: None)

    # No regrid patch needed: run() builds a nearest-source coverage mask, so
    # the NaN hole in the fake SMAP grid becomes real nodata on the dst grid.

    written = []
    real_write = live.publish.write_outputs

    def spy(out, files, manifest):
        w = real_write(out, files, manifest)
        written.extend(p.name for p in w)
        return w
    monkeypatch.setattr(live.publish, "write_outputs", spy)

    args = parse_args([
        "--date", DATE,
        "--bounds", *map(str, BOUNDS),
        "--static-dir", str(static_dir),
        "--out-dir", str(out_dir),
        "--no-nisar",
    ])
    return args, out_dir, written


def test_run_now_end_to_end(fake_now):
    args, out_dir, written = fake_now
    manifest = run(args)

    # manifest.json is the LAST file written
    assert written[-1] == "manifest.json"
    assert manifest["schema"] == "tipping-point/now@2"
    assert manifest["mode"] == "live" and len(manifest["layers"]) == 8

    # layer cog fields: float COGs only for today + D+1
    by_id = {l["id"]: l for l in manifest["layers"]}
    assert by_id["tpi_slide_d0"]["cog"] == "cog/tpi_slide_today.tif"
    assert by_id["tpi_slide_d1"]["cog"] == "cog/tpi_slide_d1.tif"
    assert by_id["tpi_flood_d0"]["cog"] == "cog/tpi_flood_today.tif"
    assert by_id["tpi_flood_d1"]["cog"] == "cog/tpi_flood_d1.tif"
    for k in (2, 3):
        assert by_id[f"tpi_slide_d{k}"]["cog"] is None
        assert by_id[f"tpi_flood_d{k}"]["cog"] is None

    # the float TPI COG opens as EPSG:4326 float32 with values in [0,1] or NaN
    with rasterio.open(out_dir / "cog" / "tpi_slide_today.tif") as src:
        assert src.crs.to_epsg() == 4326
        assert src.dtypes[0] == "float32"
        band = src.read(1)
    finite = np.isfinite(band)
    assert finite.any() and (~finite).any()      # the SMAP hole is NaN
    assert ((band[finite] >= 0.0) & (band[finite] <= 1.0)).all()

    # summary: NaN region excluded from classes_km2, counted in nodata_km2
    summary = json.loads((out_dir / "summary.json").read_text())
    assert summary["forecast_source"] == "noaa-gfs"
    assert summary["nodata_km2"]["slide"] > 0
    assert summary["nodata_km2"]["flood"] > 0
    lats, lons = dst_lats_lons(BOUNDS)
    total_km2 = float((pixel_area_km2(lats) * np.ones((lats.size, lons.size))).sum())
    for hz, key in (("landslide", "slide"), ("flood", "flood")):
        classes = summary["classes_km2"][hz]
        classes_total = sum(classes.values())
        assert classes_total < total_km2
        assert classes_total + summary["nodata_km2"][key] == pytest.approx(total_km2)
        # A8: critical_km2 = Crítico only; alert_km2 = Alto or Crítico; both
        # come from the same classes_km2 computation, so they agree
        assert summary["critical_km2"][key] == pytest.approx(classes["Crítico"])
        assert summary["alert_km2"][key] == pytest.approx(
            classes["Alto"] + classes["Crítico"])
        assert summary["critical_km2"][key] <= summary["alert_km2"][key]
    for k in ("critical_km2", "alert_km2"):
        assert summary[k]["any"] <= summary[k]["slide"] + summary[k]["flood"] + 1e-9
        assert summary[k]["any"] <= summary[k]["any_today_or_d1"] + 1e-9
    assert summary["stats_domain"]

    # every expected artifact exists
    for rel in [
        "summary.json", "alerts.json",
        "geojson/critical_areas.geojson", "manifest.json",
        "cog/tpi_slide_today.tif", "cog/tpi_slide_d1.tif",
        "cog/tpi_flood_today.tif", "cog/tpi_flood_d1.tif",
        *[f"png/{k}_d{i}.png" for k in ("tpi_slide", "tpi_flood") for i in range(4)],
    ]:
        assert (out_dir / rel).exists(), rel
    m = json.loads((out_dir / "manifest.json").read_text())
    assert m["schema"] == "tipping-point/now@2"
    # fixture has no municipal boundaries -> checks fail -> alerts paused,
    # candidates withheld, and the alert baseline NOT advanced (a paused
    # run must not swallow the upgrades it suppressed)
    assert m["alerts_paused"] is True and m["verified"] is False
    assert m["checks"]["municipalities_loaded"] is False
    assert m["checks"]["clim_source_ok"] is False      # legacy clim never verifies
    a = json.loads((out_dir / "alerts.json").read_text())
    assert a["paused"] is True and a["candidates"] == []
    assert not (out_dir / "prev_classes.npz").exists()
    # honesty rule 2: D+0 is observed to D-1 + GFS for D, and says so
    d0 = next(l for l in m["layers"] if l["id"] == "tpi_flood_d0")
    assert d0["partly_forecast"] is True and d0["forecast"] is True
    assert d0["basis"] == "observed+forecast" and d0["obs_through"] == "2026-11-13"
    assert "NOAA GFS forecast for Nov 14" in d0["basis_label"]


def test_no_upload_flag_removed():
    """S19: no bucket anywhere — run_now has no GCS upload path or flag."""
    import live.run_now
    with pytest.raises(SystemExit):
        parse_args(["--no-upload"])
    assert not hasattr(live.publish, "upload_gcs")
    assert "NOW_GCS_BUCKET" not in Path(live.run_now.__file__).read_text()


def test_write_imerg_cogs_lat_first(tmp_path):
    import rasterio
    import xarray as xr
    from live.run_now import write_imerg_cogs
    lons = np.arange(-57.95, -56.0, 0.1)      # 20 lon
    lats = np.arange(-33.95, -32.0, 0.1)      # 20 lat, ascending (south-up)
    vals = np.zeros((2, lons.size, lats.size - 5), dtype=np.float32)  # (time, lon, lat)
    lats = lats[:15]
    vals[:, 0, -1] = 7.0                      # west-most lon, north-most lat
    da = xr.DataArray(vals, dims=("time", "lon", "lat"), coords={
        "time": np.array(["2026-11-13", "2026-11-14"], dtype="datetime64[ns]"),
        "lon": lons, "lat": lats})
    paths = write_imerg_cogs(da, tmp_path)
    assert [p.name for p in paths] == ["imerg_early_20261113.tif", "imerg_early_20261114.tif"]
    with rasterio.open(paths[1]) as ds:
        assert ds.shape == (15, 20)           # rows = lat
        assert ds.read(1)[0, 0] == 7.0        # north-west corner
        assert abs(ds.bounds.left - (-58.0)) < 1e-6


def test_imerg_cogs_match_source_catches_transposed(tmp_path):
    import rasterio
    import xarray as xr
    from rasterio.transform import from_origin
    from live.run_now import imerg_cogs_match_source, write_imerg_cogs
    lats = np.arange(-33.95, -22.5, 0.1)          # 115 lat
    lons = np.arange(-57.95, -48.0, 0.1)          # 100 lon
    rng = np.random.default_rng(1)
    vals = rng.gamma(0.5, 8.0, (1, lons.size, lats.size)).astype(np.float32)
    da = xr.DataArray(vals, dims=("time", "lon", "lat"), coords={
        "time": np.array(["2026-11-13"], dtype="datetime64[ns]"),
        "lon": lons, "lat": lats})
    good = write_imerg_cogs(da, tmp_path / "good")
    assert imerg_cogs_match_source(da, good)            # >= 3 points agree
    # the legacy bug: lon-first array written on a lat-first transform
    bad = tmp_path / "bad.tif"
    with rasterio.open(bad, "w", driver="GTiff", width=lats.size, height=lons.size,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-58.0, -22.5, 0.1, 0.1)) as ds:
        ds.write(vals[0], 1)
    assert not imerg_cogs_match_source(da, [bad])


def test_critical_matches_classes_when_flood_only():
    """lead-app bug 2026-11-15: critical flood 1024 km2 while classes_km2
    (landslide only) said Crítico 0. Per-hazard classes now back critical."""
    from live.run_now import class_areas_km2, critical_from_classes
    area = np.full((4, 1), 2.0)
    flood = np.array([[3, 3, 2, 0]] * 4)
    slide = np.zeros((4, 4), dtype=int)
    valid = np.ones((4, 4), bool)
    fl, sl = class_areas_km2(flood, valid, area), class_areas_km2(slide, valid, area)
    assert critical_from_classes(fl) == 24.0 and fl["Crítico"] == 16.0
    assert critical_from_classes(sl) == 0.0 and sl["Baixo"] == 32.0


def test_frame_basis_labels_forecast_honestly():
    import datetime as dt
    from live.run_now import frame_basis
    D = dt.date(2026, 11, 15)
    b0 = frame_basis(D, D, "2026-11-15T00:00:00Z")
    assert b0["basis"] == "observed+forecast"
    assert b0["obs_through"] == "2026-11-14" and b0["forecast_days"] == ["2026-11-15"]
    assert b0["basis_label"] == ("observed to Nov 14 + NOAA GFS forecast for Nov 15 "
                                 "(issued 2026-11-15 00:00 UTC)")
    b3 = frame_basis(D + dt.timedelta(days=3), D, "2026-11-15T00:00:00Z")
    assert b3["basis"] == "forecast" and b3["obs_through"] is None
    bo = frame_basis(D - dt.timedelta(days=1), D, "2026-11-15T00:00:00Z")
    assert bo["basis"] == "observed" and bo["gfs_issued"] is None


def test_certainty_capped_by_basis():
    from live.run_now import certainty_for
    assert certainty_for("observed") == "high"
    assert certainty_for("observed+forecast") == "medium"
    assert certainty_for("forecast") == "low"


def test_fill_smap_gaps_within_one_cell_only():
    from live.run_now import fill_smap_gaps
    # SMAP sources every 0.09 deg (~9-10 km) along one row; the middle one is a gap
    src_lons = np.array([-52.0, -51.91, -51.82, -51.73, -51.64])
    src_lats = np.full(5, -30.0)
    src_vals = np.array([0.5, 0.6, np.nan, 0.8, 0.9])
    dst_lons = np.arange(-52.0, -51.6, 0.01)
    dst_lats = np.array([-30.0])
    sm = np.full((1, dst_lons.size), 0.7)
    sm[0, 15:25] = np.nan                       # pixels nearest the gap source
    sm[0, 39] = np.nan                          # far east, nearest valid ~4 km
    land = np.ones(sm.shape, dtype=bool)
    land[0, 24] = False                         # a water pixel is never filled
    out, filled = fill_smap_gaps(sm, src_lats, src_lons, src_vals, dst_lats, dst_lons,
                                 allow=land)
    assert filled[0, 15:24].all() and np.isfinite(out[0, 15:24]).all()
    assert not filled[0, 24] and np.isnan(out[0, 24])
    assert not fill_smap_gaps(sm, src_lats, src_lons, src_vals, dst_lats, dst_lons)[1].any()
    assert set(np.round(out[0, 15:24], 2)) <= {0.6, 0.8}   # neighbours' values
    far, filled2 = fill_smap_gaps(sm, src_lats, src_lons, src_vals, dst_lats, dst_lons,
                                  max_km=1.0, allow=land)
    assert not filled2[0, 18:22].any() and np.isnan(far[0, 18:22]).all()
