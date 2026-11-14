"""End-to-end run_now: fake fetchers + synthetic static -> honest artifacts.

SMAP gaps must surface as nodata (transparent PNGs, NaN in the float TPI COGs,
`nodata_km2` in summary) — never filled with a fake 0.7 wetness.
"""
import json

import numpy as np
import pytest
import rasterio
import xarray as xr

import live.fetch
import live.publish
import live.run_now
from live.grid import dst_lats_lons, pixel_area_km2, regrid_points
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

    fc_times = np.arange("2026-11-15", "2026-11-19", dtype="datetime64[D]")
    monkeypatch.setattr(
        live.fetch, "fetch_geosfp_daily",
        lambda bounds, issued=None: (
            _fake_da(fc_times, 50.0), "2026-11-14T00:00:00Z"))

    # grid.regrid_points nearest-fills source NaN; the production gap path is
    # exercised by keeping dst pixels whose nearest SMAP source is a gap.
    def regrid_keep_nan(src_lats, src_lons, values, dlats, dlons, method="linear"):
        out = regrid_points(src_lats, src_lons, values, dlats, dlons, method=method)
        gap = regrid_points(src_lats, src_lons,
                            np.isfinite(values).astype(float),
                            dlats, dlons, method="nearest")
        out[gap < 0.5] = np.nan
        return out
    monkeypatch.setattr(live.run_now, "regrid_points", regrid_keep_nan)

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
        "--no-nisar", "--no-upload",
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
    assert summary["forecast_source"] == "geos-fp"
    assert summary["nodata_km2"]["slide"] > 0
    assert summary["nodata_km2"]["flood"] > 0
    lats, lons = dst_lats_lons(BOUNDS)
    total_km2 = float((pixel_area_km2(lats) * np.ones((lats.size, lons.size))).sum())
    classes_total = sum(summary["classes_km2"].values())
    assert classes_total < total_km2
    assert classes_total + summary["nodata_km2"]["slide"] == pytest.approx(total_km2)

    # every expected artifact exists
    for rel in [
        "summary.json", "alerts.json", "prev_classes.npz",
        "geojson/critical_areas.geojson", "manifest.json",
        "cog/tpi_slide_today.tif", "cog/tpi_slide_d1.tif",
        "cog/tpi_flood_today.tif", "cog/tpi_flood_d1.tif",
        *[f"png/{k}_d{i}.png" for k in ("tpi_slide", "tpi_flood") for i in range(4)],
    ]:
        assert (out_dir / rel).exists(), rel
    assert json.loads((out_dir / "manifest.json").read_text())["schema"] == "tipping-point/now@2"
