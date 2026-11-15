"""live/nisar_stack.py — rolling per-pass NISAR frame stack for Now (hermetic)."""
import json
from pathlib import Path

import numpy as np

from live import nisar_stack as ns
from tp.response_nisar import TargetGrid

BBOX = (-58.0, -34.0, -48.0, -22.5)


def _g(track, look, frame, day, mode="4005_DHDH_A", lon=-52.0):
    gid = (f"NISAR_L2_PR_GCOV_035_{track:03d}_{look}_{frame}_{mode}_"
           f"{day.replace('-', '')}T211603_{day.replace('-', '')}T211610_P05036_N_P_J_001")
    return {"granule_id": gid, "track": str(track), "look": look, "frame": str(frame),
            "start": f"{day} 21:16:03+00:00", "h5_url": f"https://x/{gid}.h5",
            "footprint": f"POLYGON(({lon} -29, {lon + 1} -29, {lon + 1} -28, {lon} -28, {lon} -29))"}


def _fake_read(url, term, bbox, token):
    # 20 x 20 cells of 400 m in UTM 22S around lon -52, lat -28.5
    grid = TargetGrid(32722, 400000.0, 6850000.0, 400.0, 20, 20)
    db = np.full((20, 20), -10.0 if term == "HHHH" else -20.0, np.float32)
    db[:5] = np.nan
    return db, grid


def test_plan_skips_mixed_mode_and_keeps_newest():
    from shapely.geometry import box
    gs = [_g(24, "D", 101, d) for d in ("2026-10-01", "2026-10-13", "2026-10-25", "2026-11-06")]
    gs += [_g(24, "D", 102, "2026-11-06"), _g(10, "D", 100, "2026-11-05", mode="2005_DHDH_M"),
           _g(31, "A", 162, "2026-10-26", lon=-70.0)]           # outside the domain
    plan = ns.plan_passes(gs, box(*BBOX), keep=3)
    assert set(plan) == {(24, "D", "2026-11-06"), (24, "D", "2026-10-25"), (24, "D", "2026-10-13")}
    assert [g["frame"] for g in plan[(24, "D", "2026-11-06")]] == ["101", "102"]


def test_update_renders_rolls_and_respects_budget(tmp_path: Path):
    now = tmp_path / "now"
    gs = [_g(24, "D", 101, d) for d in ("2026-10-13", "2026-10-25", "2026-11-06")]
    doc = ns.update(now, BBOX, gs, None, keep=2, max_granules=1, read=_fake_read)
    assert [lay["pass"] for lay in doc["layers"]] == ["024D_2026-11-06"] * 2   # budget: 1 granule
    assert doc["pending_passes"] == ["024D_2026-10-25"]
    lay = next(x for x in doc["layers"] if x["polarization"] == "HH")
    assert lay["id"] == "nisar_hh_20261106_024D" and lay["kind"] == "nisar_frame"
    assert lay["track"] == "024D" and lay["mode"] == "4005_DHDH_A" and lay["sensor"] == "NISAR"
    assert isinstance(lay["legend"], dict) and lay["legend"]["unit"] == "dB"
    assert 0 < lay["coverage_pct"] <= 100
    assert (now / lay["png"]).is_file()
    w, s, e, n = lay["bounds"]
    assert w < e and s < n
    # next run fills the deferred pass; a newer pass rolls the oldest out
    ns.update(now, BBOX, gs, None, keep=2, max_granules=5, read=_fake_read)
    gs.append(_g(24, "D", 101, "2026-11-18"))
    doc = ns.update(now, BBOX, gs, None, keep=2, max_granules=5, read=_fake_read)
    assert sorted({x["pass"] for x in doc["layers"]}) == ["024D_2026-11-06", "024D_2026-11-18"]
    pngs = sorted(p.name for p in (now / "nisar_stack" / "png").glob("*.png"))
    assert pngs == sorted(Path(x["png"]).name for x in doc["layers"])
    assert not (now / "nisar_stack" / "png" / "nisar_hh_20261025_024D.png").exists()


def test_now_manifest_merges_stack(tmp_path: Path):
    from live import now_manifest as nm
    now = tmp_path / "now"
    ns.update(now, BBOX, [_g(24, "D", 101, "2026-11-06")], None, read=_fake_read)
    m = {"layers": [{"id": "tpi_flood_d0", "png": "png/x.png"},
                    {"id": "nisar_hh_20261001_024D", "png": "nisar_stack/png/gone.png"}]}
    nm.merge_nisar_layers(m, now)
    ids = [lay["id"] for lay in m["layers"]]
    assert ids[0] == "tpi_flood_d0"
    assert {"nisar_hh_20261106_024D", "nisar_hv_20261106_024D"} <= set(ids)
    assert "nisar_hh_20261001_024D" not in ids
    assert json.loads((now / "nisar_stack" / "layers.json").read_text())["schema"] == ns.SCHEMA
