"""rs_uruguai_2026 municipalities + places for the case AOI (config.yaml cases.rs_uruguai_2026.aoi).

Every IBGE municipality whose representative point lies inside the AOI:
  geojson/municipal_boundaries.geojson : full polygons (not clipped), pop = native 30" GPW
                                         v4.11 2020 unadjusted (exact cell fractions),
                                         teammate_candidates_km2 = flood_confirmed area inside
  geojson/places.geojson               : one point per municipality (representative point)

    PYTHONPATH=.:pipeline python tools/rs_uruguai_case_geo.py --munis <IBGE now geojson> --gpw <tif>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from pyproj import Geod
from shapely.geometry import box, mapping, shape

REPO = Path(__file__).resolve().parents[1]
CASE = "rs_uruguai_2026"


def main(argv=None) -> int:
    from tp.response_summary import load_gpw_native, people_geom

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--munis", type=Path, required=True)
    ap.add_argument("--gpw", type=Path, required=True)
    args = ap.parse_args(argv)
    aoi = yaml.safe_load((REPO / "config.yaml").read_text())["cases"][CASE]["aoi"]
    abox = box(*aoi)
    gj = REPO / "data" / CASE / "geojson"
    cand = []
    fc = gj / "flood_confirmed.geojson"
    if fc.exists():
        cand = [shape(f["geometry"]).buffer(0) for f in json.loads(fc.read_text())["features"]]
    geod = Geod(ellps="WGS84")
    gpw = load_gpw_native(args.gpw)
    feats, pts = [], []
    for f in json.loads(args.munis.read_text())["features"]:
        g = shape(f["geometry"]).buffer(0)
        rp = g.representative_point()
        if not abox.contains(rp):
            continue
        p = f["properties"]
        pop = people_geom(g, gpw)
        ck = sum(abs(geod.geometry_area_perimeter(g.intersection(c))[0]) for c in cand) / 1e6
        feats.append({"type": "Feature", "geometry": mapping(g), "properties": {
            "name": p["name"], "code": p["code"], "uf": p["uf"],
            "pop": None if pop is None else round(pop),
            "teammate_candidates_km2": round(ck, 3),
            "source": "IBGE", "license": p.get("license", "IBGE open data (attribution required)")}})
        pts.append({"type": "Feature", "geometry": mapping(rp), "properties": {
            "name": p["name"], "kind": "town", "code": p["code"], "uf": p["uf"],
            "pop": None if pop is None else round(pop)}})
    feats.sort(key=lambda x: x["properties"]["name"])
    pts.sort(key=lambda x: x["properties"]["name"])
    (gj / "municipal_boundaries.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")))
    (gj / "places.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": pts}, ensure_ascii=False, separators=(",", ":")))
    for x in feats:
        print(x["properties"]["name"], x["properties"]["pop"], x["properties"]["teammate_candidates_km2"])
    print(len(feats), "municipalities")
    return 0



def hand_low(hand90: Path, gsw: Path | None = None) -> dict:
    """png/hand_low.png (HAND <= 5 m, 90 m HAND, #009ebd @ alpha 140, EPSG:3857) over the
    AOI + the corridor shares vs the flood_confirmed candidates (geodesic areas)."""
    import numpy as np
    import rasterio
    from rasterio.features import rasterize
    from rasterio.windows import from_bounds

    from live.render import warp_to_3857

    aoi = yaml.safe_load((REPO / "config.yaml").read_text())["cases"][CASE]["aoi"]
    with rasterio.open(hand90) as src:
        win = from_bounds(*aoi, transform=src.transform).round_offsets().round_lengths()
        h = src.read(1, window=win).astype(np.float64)
        tr = src.window_transform(win)
        if src.nodata is not None:
            h[h == src.nodata] = np.nan
    land = np.isfinite(h)
    low5, low10 = land & (h <= 5.0), land & (h <= 10.0)
    m3857, _, bounds = warp_to_3857(low5.astype(np.uint8), tr)
    rgba = np.zeros((4, *m3857.shape), np.uint8)
    on = m3857.astype(bool)
    rgba[0][on], rgba[1][on], rgba[2][on], rgba[3][on] = 0, 158, 189, 140
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", width=rgba.shape[2], height=rgba.shape[1], count=4,
                     dtype="uint8") as dst:
            dst.write(rgba)
        (REPO / "data" / CASE / "png" / "hand_low.png").write_bytes(mf.read())
    lat = tr.f + (np.arange(h.shape[0]) + 0.5) * tr.e
    km2 = (abs(tr.a) * 111.32) * (abs(tr.e) * 110.574) * np.cos(np.deg2rad(lat))[:, None] * np.ones((1, h.shape[1]))
    fc = json.loads((REPO / "data" / CASE / "geojson" / "flood_confirmed.geojson").read_text())["features"]
    geoms = [shape(f["geometry"]) for f in fc]
    cand = rasterize(geoms, out_shape=h.shape, transform=tr, fill=0, default_value=1,
                     all_touched=False, dtype="uint8").astype(bool) & land
    out = {"bounds": bounds, "aoi_land_km2": round(float(km2[land].sum()), 1)}
    for name, m in (("hand_le_5m", low5), ("hand_le_10m", low10)):
        share = float(km2[cand & m].sum() / km2[cand].sum()) if cand.any() else None
        aoi_share = float(km2[m].sum() / km2[land].sum())
        out[name] = {"share": None if share is None else round(share, 3),
                     "aoi_share": round(aoi_share, 3),
                     "lift": None if share is None else round(share / aoi_share, 2)}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
