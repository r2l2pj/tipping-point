"""P(trigger) wiring with a synthetic smoke model (never published)."""
import datetime as dt
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from live import now_manifest, p_trigger

BOUNDS = (-52.0, -30.2, -51.8, -30.0)          # 20 x 20 cells at 0.01 deg
D = dt.date(2026, 11, 15)


def _smoke_model(tmp_path, horizons=None):
    from xgboost import XGBClassifier

    from ml.local import models as M
    cols = list(p_trigger.STATIC_BANDS) + list(p_trigger.TEXTURE_BANDS) + list(
        p_trigger.SOIL_VARS) + ["rain_24h", "rain_72h", "rain_7d", "rain_24h_rel",
                                "rain_72h_rel", "doy_sin", "doy_cos"]
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, len(cols)))
    y = (X[:, cols.index("rain_72h")] > 0.5).astype(int)
    m = XGBClassifier(n_estimators=5, max_depth=2).fit(X, y)
    return M.save_xgb(m, tmp_path / "p_trigger_xgb.json",
                      {"features": cols, "horizons": horizons or p_trigger.HORIZONS,
                       "name": "smoke", "version": "test"})


def _static(tmp_path):
    names = list(p_trigger.STATIC_BANDS) + list(p_trigger.TEXTURE_BANDS) + [
        "p99_daily", "p99_3day"]
    arr = np.ones((len(names), 20, 20), dtype="float32")
    arr[0, 0, 0] = np.nan                       # elevation hole -> NaN output
    p = tmp_path / "ml_static_now.tif"
    with rasterio.open(p, "w", driver="GTiff", width=20, height=20, count=len(names),
                       dtype="float32", crs="EPSG:4326", nodata=np.nan,
                       transform=from_origin(-52.0, -30.0, 0.01, 0.01)) as ds:
        ds.write(arr)
        for i, n in enumerate(names, start=1):
            ds.set_band_description(i, n)
    return p


def _obs(out_dir):
    from pipeline.fetch.imerg import write_cog
    lats = np.arange(-30.25, -29.9, 0.1)
    lons = np.arange(-52.05, -51.7, 0.1)
    for k in range(1, 8):
        d = D - dt.timedelta(days=k)
        write_cog(out_dir / "cog" / "imerg_early" / f"imerg_early_{d:%Y%m%d}.tif",
                  np.full((lats.size, lons.size), 5.0 * k, dtype="float32"), lats, lons)


def test_p_trigger_frames_and_manifest_flag(tmp_path, monkeypatch):
    out = tmp_path / "now"
    _obs(out)
    soil = {v: np.full((20, 20), 0.3) for v in p_trigger.SOIL_VARS}
    layers = p_trigger.run(out, D, bounds=BOUNDS, model_path=_smoke_model(tmp_path),
                           static_path=_static(tmp_path),
                           rain_fc=np.full((14, 20, 20), 10.0),
                           gfs_issued="2026-11-15T00:00:00Z", soil=soil)
    assert [l["id"] for l in layers] == [f"p_trigger_{h}" for h in p_trigger.HORIZONS]
    l24 = layers[0]
    assert l24["basis"] == "observed+forecast" and l24["obs_through"] == "2026-11-14"
    assert l24["forecast_days"] == ["2026-11-15"] and layers[-1]["low_skill"] is True
    assert "issued 2026-11-15 00:00 UTC" in l24["basis_label"]
    with rasterio.open(out / "cog" / "p_trigger_24h.tif") as ds:
        p = ds.read(1)
    assert np.isnan(p[0, 0]) and np.nanmin(p) >= 0 and np.nanmax(p) <= 1
    with rasterio.open(out / "cog" / "p_trigger_14d.tif") as ds:
        p14 = ds.read(1)
    assert np.nanmean(p14) >= np.nanmean(p) - 1e-6      # survival: P grows with h
    assert (out / "png" / "p_trigger_14d.png").stat().st_size > 0

    m = {"layers": [{"id": "tpi_flood_d0"}], "products": {}}
    (out / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setenv("NOW_P_TRIGGER", "0")            # OFF: no layers listed
    got = now_manifest.finalize(out)
    assert got["p_trigger_enabled"] is False
    assert [l["id"] for l in got["layers"]] == ["tpi_flood_d0"]
    monkeypatch.setenv("NOW_P_TRIGGER", "1")            # ON: frames appended
    got = now_manifest.finalize(out)
    assert got["p_trigger_enabled"] is True
    assert [l["id"] for l in got["layers"]][1:] == [l["id"] for l in layers]


def test_p_trigger_main_is_noop_when_off(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NOW_P_TRIGGER", raising=False)
    assert p_trigger.main(["--out-dir", str(tmp_path)]) == 0
    assert "OFF" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())


def test_smap_granule_pick_prefers_1030z():
    from live.fetch import _pick_smap_granule
    g = lambda i: {"meta": {"native-id": f"SMAP_L4_SM_gph_{i}_Vv8013_001"}}
    res = [g("20261113T103000"), g("20261114T103000"), g("20261114T223000")]
    assert _pick_smap_granule(res, "T103000")["meta"]["native-id"].startswith(
        "SMAP_L4_SM_gph_20261114T103000")
    assert "T223000" in _pick_smap_granule(res, None)["meta"]["native-id"]
    with pytest.raises(RuntimeError):
        _pick_smap_granule([g("20261114T223000")], "T103000")


def test_serve_combine_multiplies_by_t_ml(tmp_path):
    """lead-ml meta 'serve_combine': P_live_H = p_trigger_H x t_ml."""
    import rasterio
    from ml.local import models as M
    mp = _smoke_model(tmp_path)
    model, meta = M.load_xgb(mp)
    meta.update({"serve_combine": "P_live_H = p_trigger_H x t_ml",
                 "serve_label": "Relative likelihood of a rain-triggered landslide within H (when x where), 0-1"})
    mp.with_suffix(".meta.json").write_text(json.dumps(meta))
    sp = _static(tmp_path)
    with rasterio.open(sp) as ds:
        arr, prof, names = ds.read(), ds.profile, list(ds.descriptions)
    prof.update(count=len(names) + 1)
    sp2 = tmp_path / "static_tml.tif"
    with rasterio.open(sp2, "w", **prof) as ds:
        ds.write(np.concatenate([arr, np.full((1, 20, 20), 0.5, "float32")]))
        for i, n in enumerate(names + ["t_ml"], start=1):
            ds.set_band_description(i, n)
    kw = dict(bounds=BOUNDS, rain_fc=np.full((14, 20, 20), 10.0),
              gfs_issued="2026-11-15T00:00:00Z",
              soil={v: np.full((20, 20), 0.3) for v in p_trigger.SOIL_VARS})
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    _obs(out_a); _obs(out_b)
    with pytest.raises(ValueError, match="t_ml"):        # combine needs the band
        p_trigger.run(out_a, D, model_path=mp, static_path=sp, **kw)
    layers = p_trigger.run(out_b, D, model_path=mp, static_path=sp2, **kw)
    with rasterio.open(out_b / "cog" / "p_trigger_24h.tif") as ds:
        pb = ds.read(1)
    assert layers[0]["legend"]["type"] == "relative_index"
    assert layers[0]["title"].startswith("Relative likelihood of a rain-triggered landslide within 24h")
    assert np.nanmax(pb) <= 0.5 + 1e-6


def test_frames_monotone_in_horizon(tmp_path):
    """P(trigger within H) never decreases with H (lead-ml 5baed62)."""
    import rasterio
    out = tmp_path / "now"
    _obs(out)
    p_trigger.run(out, D, bounds=BOUNDS, model_path=_smoke_model(tmp_path),
                  static_path=_static(tmp_path), rain_fc=np.full((14, 20, 20), 10.0),
                  gfs_issued="2026-11-15T00:00:00Z",
                  soil={v: np.full((20, 20), 0.3) for v in p_trigger.SOIL_VARS})
    prev = None
    for h in p_trigger.HORIZONS:
        with rasterio.open(out / "cog" / f"p_trigger_{h}.tif") as ds:
            p = ds.read(1)
        if prev is not None:
            ok = np.isfinite(p) & np.isfinite(prev)
            assert (p[ok] >= prev[ok] - 1e-7).all(), h
        prev = p


def test_horizons_follow_model_meta_and_stale_frames_go(tmp_path):
    """ML v2 serves 24h/72h/7d only: frames follow the meta, v1's 48h/14d
    files left in the out dir are removed, 7d stays low_skill."""
    out = tmp_path / "now"
    _obs(out)
    for rel in ("cog/p_trigger_48h.tif", "png/p_trigger_14d.png"):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(b"stale")
    soil = {v: np.full((20, 20), 0.3) for v in p_trigger.SOIL_VARS}
    v2 = {"24h": 1, "72h": 3, "7d": 7}
    layers = p_trigger.run(out, D, bounds=BOUNDS,
                           model_path=_smoke_model(tmp_path, v2),
                           static_path=_static(tmp_path),
                           rain_fc=np.full((7, 20, 20), 10.0),
                           gfs_issued="2026-11-15T00:00:00Z", soil=soil)
    assert [l["id"] for l in layers] == ["p_trigger_24h", "p_trigger_72h", "p_trigger_7d"]
    assert [l["low_skill"] for l in layers] == [False, False, True]
    assert sorted(p.name for p in (out / "cog").glob("p_trigger_*")) == [
        "p_trigger_24h.tif", "p_trigger_72h.tif", "p_trigger_7d.tif"]
    assert not (out / "png" / "p_trigger_14d.png").exists()
