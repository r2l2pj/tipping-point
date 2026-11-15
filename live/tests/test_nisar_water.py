"""L61 live/nisar_water.py + live/now_nisar_tpi.py (hermetic, synthetic frames)."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
from rasterio.transform import from_bounds

from live import nisar_stack as ns
from live import nisar_water as nw

B = (-52.0, -30.0, -51.0, -29.0)        # 1 x 1 deg


def _frame(tmp: Path, day: str, db: np.ndarray, pol="HH", track="024D") -> dict:
    tr = from_bounds(*B, db.shape[1], db.shape[0])
    png, bounds = ns.gray_png(db.astype(np.float32), tr, pol)
    rel = f"nisar_stack/png/nisar_{pol.lower()}_{day.replace('-', '')}_{track}.png"
    (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp / rel).write_bytes(png)
    return {"id": Path(rel).stem, "date": day, "kind": "nisar_frame", "polarization": pol,
            "track": track, "png": rel, "bounds": bounds, "legend": ns.LEGEND[pol],
            "stretch_db": {"db_min": ns.DB_MIN, "db_max": ns.DB_MAX}}


def test_gray_db_roundtrip_matches_stack_encoding():
    db = np.array([-25.0, -18.0, -10.0, 0.0])
    g = np.clip(1 + (db - ns.DB_MIN) / (ns.DB_MAX - ns.DB_MIN) * 254, 1, 255).astype(np.uint8)
    back = nw.gray_to_db(g, ns.DB_MIN, ns.DB_MAX)
    assert np.allclose(back, db, atol=0.1)
    assert np.isnan(nw.gray_to_db(np.array([0]), -25, 0))[0]


def test_passes_never_future_and_at_most_12_days():
    fr = [{"date": d} for d in ("2026-10-01", "2026-10-10", "2026-10-20")]
    got = [e["date"] for e in nw.passes_for(fr, dt.date(2026, 10, 15))]
    assert got == ["2026-10-10"]                   # 10-01 too old, 10-20 is in the future
    assert nw.passes_for(fr, dt.date(2026, 9, 30)) == []


def test_water_mask_combine_and_agreement(tmp_path: Path):
    db = np.full((40, 40), -8.0)
    db[:, :20] = -22.0                             # west half dark = water
    e_old = _frame(tmp_path, "2026-10-05", np.full((40, 40), -8.0))
    e_new = _frame(tmp_path, "2026-10-10", db)
    frames = nw.hh_frames([e_old, e_new, dict(e_new, id="x_hv", polarization="HV")])
    assert [e["date"] for e in frames] == ["2026-10-05", "2026-10-10"]
    shape, tr = (20, 20), from_bounds(*B, 20, 20)
    perm = np.zeros(shape, bool)
    perm[0, 0] = True
    water, _observed, used = nw.water_mask(frames, dt.date(2026, 10, 12), tmp_path, shape, tr, perm)
    assert [u["date"] for u in used] == ["2026-10-05", "2026-10-10"]
    assert water[:, :8].sum() >= 8 * 19 and not water[:, 12:].any()    # latest pass wins
    assert not water[0, 0]                                              # permanent water excluded
    tpi = np.full(shape, 0.2, np.float32)
    tpi[:, :5] = 0.7
    comb = nw.combine(tpi, water)
    assert np.isclose(comb[5, 7], 0.8) and np.isclose(comb[5, 2], 0.8) and np.isclose(comb[5, 15], 0.2)
    fp = np.zeros(shape, bool)
    fp[:, :5] = True
    ag = nw.agreement(tpi, water, np.ones(shape), fp)
    assert ag["critical"]["hit_pct"] == round(100 * (water[:, :5].sum()) / water.sum(), 1)
    assert ag["critical"]["area_share_pct"] == 25.0
    assert ag["outside_floodplain_pct"] > 50


def test_now_step_end_to_end(tmp_path: Path):
    now = tmp_path / "now"
    db = np.full((40, 40), -8.0)
    db[10:30, 10:30] = -21.0
    e = _frame(now, "2026-11-10", db)
    (now / "nisar_stack" / "layers.json").write_text(json.dumps({"layers": [e]}))
    flood = np.full((4, 50, 50), 0.1, np.float16)
    np.savez(now / "places_tpi.npz", flood=flood, slide=flood,
             dates=np.array(["2026-11-15", "2026-11-16", "2026-11-17", "2026-11-23"]),
             bounds=np.array(B))
    (now / "summary.json").write_text("{}")
    from live import now_nisar_tpi as nt
    res = nt.run(now)
    lay = json.loads((now / "nisar_tpi" / "layers.json").read_text())["layers"]
    ids = [x["id"] for x in lay]
    assert ids[:2] == ["nisar_water", "tpi_flood_nisar_d0"] and "tpi_flood_nisar_d3" not in ids  # d3: pass > 12 d old
    assert res["summary"]["pass_date"] == "2026-11-10" and res["summary"]["age_days"] == 5
    assert (now / "png" / "tpi_flood_nisar_d0.png").is_file() and (now / "cog" / "tpi_flood_nisar_today.tif").is_file()
    assert json.loads((now / "summary.json").read_text())["nisar_water"]["water_km2"] > 0


def test_now_step_masks_ocean_with_land(tmp_path: Path):
    now = tmp_path / "now"
    db = np.full((40, 40), -24.0)                 # all dark (ocean-like)
    e = _frame(now, "2026-11-10", db)
    (now / "nisar_stack" / "layers.json").write_text(json.dumps({"layers": [e]}))
    fl = np.full((4, 50, 50), 0.1, np.float16)
    np.savez(now / "places_tpi.npz", flood=fl, slide=fl,
             dates=np.array(["2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"]), bounds=np.array(B))
    (now / "geojson").mkdir(parents=True)
    land = {"type": "Polygon", "coordinates": [[[-52.0, -30.0], [-51.5, -30.0], [-51.5, -29.0], [-52.0, -29.0], [-52.0, -30.0]]]}
    (now / "geojson" / "municipal_boundaries.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": land, "properties": {}}]}))
    from live import now_nisar_tpi as nt
    res = nt.run(now)
    w = np.load(now / "nisar_tpi" / "water.npz")["water"]
    assert w[:, :20].mean() > 0.9 and not w[:, 30:].any()     # only the land half
    assert res["summary"]["water_km2"] > 0
