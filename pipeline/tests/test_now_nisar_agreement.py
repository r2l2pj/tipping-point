"""tools/now_nisar_agreement.py — Now agreement from the published COG bands (hermetic)."""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import now_nisar_agreement as nna


def _cog(path, bands: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    a0 = next(iter(bands.values()))
    with rasterio.open(path, "w", driver="GTiff", height=a0.shape[0], width=a0.shape[1], count=len(bands),
                       dtype="float32", crs="EPSG:4326", transform=from_origin(-55, -28, 0.01, 0.01)) as d:
        for i, (k, a) in enumerate(bands.items(), start=1):
            d.write(a.astype(np.float32), i)
            d.set_band_description(i, k)


def test_scores_observed_cells_and_states_limits(tmp_path):
    now = tmp_path / "now"
    water = np.zeros((10, 10))
    water[0, :4] = 1
    observed = np.zeros((10, 10))
    observed[:5] = 1
    tpi = np.full((10, 10), 0.1)
    tpi[0, :2] = 0.7
    _cog(now / "cog" / "tpi_flood_nisar_today.tif", {"tpi": np.maximum(tpi, 0.8 * water), "water": water,
                                                     "observed": observed})
    _cog(now / "cog" / "tpi_flood_today.tif", {"tpi": tpi})
    (now / "summary.json").write_text(json.dumps({"nisar_water": {"pass_date": "2026-11-10", "age_days": 5,
                                                                  "pass_track": "024D"}}))
    r = nna.run(now)
    assert r["status"] == "scored" and r["water_cells"] == 4 and r["observed_cells"] == 50
    assert r["observed"]["critico"]["hit_pct"] == 50.0 and "ratio_vs_terrain" not in r["observed"]["critico"]
    assert r["pass_age_days"] == 5 and r["forecast"]["lead_1d"]["null_reason"] == nna.LEAD_REASON


def test_two_band_cog_is_pending(tmp_path):
    now = tmp_path / "now"
    z = np.zeros((4, 4))
    _cog(now / "cog" / "tpi_flood_nisar_today.tif", {"tpi": z, "water": z})
    r = nna.run(now)
    assert r["status"] == "pending" and "observed" in r["null_reasons"][0]


def test_lead_from_history_archive(tmp_path):
    now = tmp_path / "now"
    water = np.zeros((10, 10))
    water[0, :4] = 1
    obs = np.ones((10, 10))
    tpi = np.full((10, 10), 0.1)
    _cog(now / "cog" / "tpi_flood_nisar_today.tif", {"tpi": tpi, "water": water, "observed": obs})
    _cog(now / "cog" / "tpi_flood_today.tif", {"tpi": tpi})
    (now / "summary.json").write_text(json.dumps({"nisar_water": {"pass_date": "2026-11-17"}}))
    fc = tpi.copy()
    fc[0, :4] = 0.9
    _cog(now / "history" / "20261116T090000Z" / "cog" / "tpi_flood_d1.tif", {"2026-11-17": fc})
    r = nna.run(now)
    assert r["forecast"]["lead_1d"]["critico"]["hit_pct"] == 100.0
    assert r["forecast"]["lead_2d"]["null_reason"] == nna.LEAD_REASON


def test_lead_from_uint8_scaled_archive(tmp_path):
    now = tmp_path / "now"
    water = np.zeros((10, 10))
    water[0, :4] = 1
    obs = np.ones((10, 10))
    tpi = np.full((10, 10), 0.1)
    _cog(now / "cog" / "tpi_flood_nisar_today.tif", {"tpi": tpi, "water": water, "observed": obs})
    _cog(now / "cog" / "tpi_flood_today.tif", {"tpi": tpi})
    (now / "summary.json").write_text(json.dumps({"nisar_water": {"pass_date": "2026-11-17"}}))
    q = np.full((10, 10), 25, np.uint8)       # 0.1
    q[0, :2] = 200                             # 0.8 -> Critical
    q[0, 2:4] = 255                            # nodata -> miss
    path = now / "history" / "20261116T090000Z" / "cog" / "tpi_flood_d1.tif"
    path.parent.mkdir(parents=True)
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="uint8", nodata=255,
                       crs="EPSG:4326", transform=from_origin(-55, -28, 0.01, 0.01)) as d:
        d.write(q, 1)
        d.set_band_description(1, "2026-11-17")
        d.scales = (0.004,)
    r = nna.run(now)
    assert r["forecast"]["lead_1d"]["critico"]["hit_pct"] == 50.0
