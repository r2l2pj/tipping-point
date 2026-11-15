"""Hermetic tests for tools/rs_uruguai_nisar.py pure functions + the
rs_uruguai_2026 manifest node."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tools import rs_uruguai_nisar as ts
from tp import manifest


class TestChannelMask:
    def test_drops_small_components(self):
        occ = np.zeros((100, 100), np.float32)
        occ[10:14, 10:14] = 80.0           # 16 px * 3600 m2 = 0.058 km2 -> dropped
        occ[50:80, 20:60] = 90.0           # 1200 px = 4.32 km2 -> kept
        m = ts.channel_mask(occ, 60.0)
        assert m[50, 30] and not m[11, 11]
        assert m.sum() == 1200

    def test_empty(self):
        assert not ts.channel_mask(np.zeros((10, 10), np.float32), 60.0).any()


class TestCorridor:
    def test_distance(self):
        channel = np.zeros((200, 100), bool)
        channel[100, :] = True                     # horizontal channel row
        c = ts.corridor_mask(channel, 60.0, corridor_m=3000.0)
        assert c[100, 0]
        assert c[100 + 50, 0]                      # exactly 3000 m -> inside
        assert not c[100 + 51, 0]                  # 3060 m -> outside
        assert not c[0, 0]                         # 6000 m away vertically

    def test_split_new_water(self):
        flood = np.ones((20, 20), bool)
        perm = np.zeros((20, 20), bool)
        perm[:, :5] = True                         # permanent water west edge
        corr = np.zeros((20, 20), bool)
        corr[:, 5:12] = True                       # corridor band
        cand, flagged = ts.split_new_water(flood, perm, corr)
        assert cand.sum() == 20 * 7                # cols 5..11
        assert flagged.sum() == 20 * 8             # cols 12..19
        assert not (cand & flagged).any()
        assert not (cand[:, :5].any())             # permanent water excluded


class TestGeodesic:
    def test_km2(self):
        from shapely.geometry import box

        g = box(-57.0, -29.5, -56.99, -29.49)      # ~1.1 km x 1.1 km at -29.5
        a = ts.geodesic_km2(g)
        assert 0.9 < a < 1.3                       # cos(lat)*111.3*110.6 ~ 1.07


class TestIndexUpsert:
    def test_swipe_ids_replace_by_id(self, tmp_path):
        idx = tmp_path / "_index.json"
        idx.write_text('{"layers": [{"id": "nisar_pre", "date": "2026-09-30", '
                       '"png": "old.png"}, {"id": "nisar_hh_20261024", '
                       '"date": "2026-10-24", "png": "frame.png"}]}')
        ts._upsert_index(idx, [
            {"id": "nisar_pre", "date": "2026-10-24", "png": "new.png"},
            {"id": "nisar_hh_20261024", "date": "2026-10-24", "png": "f2.png"},
        ])
        import json

        layers = json.loads(idx.read_text())["layers"]
        assert [l["id"] for l in layers] == ["nisar_pre", "nisar_hh_20261024"]
        assert layers[0]["png"] == "new.png"


class TestManifestNode:
    def test_case_node(self, tmp_path):
        root = tmp_path / "data"
        (root / "rs_uruguai_2026" / "png").mkdir(parents=True)
        (root / "rs_uruguai_2026" / "png" / "_index.json").write_text(
            '{"layers": [{"id": "nisar_hh_20261105", "date": "2026-11-05", '
            '"polarization": "HH", "png": "rs_uruguai_2026/png/'
            'nisar_hh_20261105.png", "bounds": [-57.2, -29.8, -56.9, -29.1], '
            '"kind": "nisar_frame"}]}')
        (root / "metrics.json").write_text('{"cases": {}}')
        node = manifest.build_case_node("rs_uruguai_2026", root)
        assert node["event_id"] == "rs_uruguai_2026"
        assert node["mode"] == "replay"
        assert node["bounds"] == [-58.0, -30.6, -55.3, -28.2]   # Fronteira Oeste reach (2026-11-15)
        assert node["metrics"] == "metrics.json#rs_uruguai_2026"
        frame = next(l for l in node["layers"] if l["id"] == "nisar_hh_20261105")
        assert frame["kind"] == "nisar_frame"
        assert frame["bounds"] == [-57.2, -29.8, -56.9, -29.1]
        assert node["products"]["flood_candidates"] is None
        assert "flood_confirmed" in node["_null_reasons"]
