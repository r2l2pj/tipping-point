"""Landslide scar candidates from OPERA DIST-ALERT-HLS (see tp.response_dist).

Now mode (default, every run after R1-R3):
    PYTHONPATH=pipeline python pipeline/scripts/16_response_dist.py
  -> data/now/response/landslide_dist.geojson + dist_meta.json (meta last),
     window = the last 30 days ending today (UTC).

Replay mode:
    PYTHONPATH=pipeline python pipeline/scripts/16_response_dist.py --case rs_2024
    PYTHONPATH=pipeline python pipeline/scripts/16_response_dist.py --case wa_2025
  -> data/<case>/geojson/landslide_candidates.geojson (the manifest's
     landslide_candidates product) + metrics.json scar_* keys, validated
     against data/<case>/geojson/landslide_catalog.geojson (hit rate within
     100 m / 500 m, precision proxy within 500 m).

Static inputs (static-v1 Release; gitignored): data/now/static/nasadem/
steep15_1as.tif, data/now/static/mcd12q1/lc_type1_2025.tif,
data/now/static/gpw/gpw_popcount_2020_30sec.tif and (now mode)
data/now/static/ibge/municipalities_now.geojson. Those rasters are clipped to
the Brazil domain; wa_2025 uses CASE_GATES instead: static/cache/wa/
steep15_wa.tif (slope > 15 deg, derived from the wa_2025 T_slide COG — GEE
NASADEM) and lc_type1_2025.tif (MCD12Q1 h09v04 via tools/convert_mcd12q1.py
--bbox), plus the case GPW count COG. Needs EARTHDATA_TOKEN.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from tp import response_dist as rd  # noqa: E402
from tp.config import REPO_ROOT, now_bounds  # noqa: E402

STATIC = REPO_ROOT / "data/now/static"
CASE_WINDOWS = {"rs_2024": (dt.date(2024, 4, 27), dt.date(2024, 6, 15)),
                # the storm plus the first cloud-free looks (~12 weeks; the
                # latest granule per tile is still read, only first-detection
                # dates are windowed)
                "wa_2025": (dt.date(2025, 12, 5), dt.date(2026, 2, 28))}
# gate rasters that differ per case; CLI flags always win
CASE_GATES = {"wa_2025": {
    "steep": REPO_ROOT / "static/cache/wa/steep15_wa.tif",
    "landcover": REPO_ROOT / "static/cache/wa/lc_type1_2025.tif",
    "gpw": REPO_ROOT / "data/wa_2025/cog/wa_2025_pop_gpw_20251210.tif"}}
CASE_STEEP = {"rs_2024": 'slope >= 15 deg (NASADEM 1")',
              "wa_2025": "slope > 15 deg (NASADEM via GEE T_slide 90 m)"}


def _points(fc: dict) -> list[tuple[float, float]]:
    out = []
    for f in fc.get("features") or []:
        g = f.get("geometry") or {}
        if g.get("type") == "Point":
            out.append(tuple(g["coordinates"][:2]))
        elif g.get("type") == "MultiPoint":
            out.extend(tuple(c[:2]) for c in g["coordinates"])
    return out


def _utm_epsg(bounds) -> int:
    """UTM zone EPSG for the bbox centre (hit-rate distances in metres)."""
    w, s, e, n = bounds
    return (32600 if (s + n) / 2 >= 0 else 32700) + int(((w + e) / 2 + 180) // 6) + 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", choices=sorted(CASE_WINDOWS))
    ap.add_argument("--start", type=dt.date.fromisoformat)
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--days", type=int, default=30, help="now mode window length")
    ap.add_argument("--steep", type=Path)
    ap.add_argument("--landcover", type=Path)
    ap.add_argument("--gpw", type=Path)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    gates = CASE_GATES.get(args.case or "", {})
    steep = args.steep or gates.get("steep") or STATIC / "nasadem/steep15_1as.tif"
    landcover = args.landcover or gates.get("landcover") or STATIC / "mcd12q1/lc_type1_2025.tif"
    gpw = args.gpw or gates.get("gpw") or STATIC / "gpw/gpw_popcount_2020_30sec.tif"
    for p in (steep, landcover):
        if not p.is_file():
            sys.exit(f"missing gate raster {p} (static-v1 Release / CASE_GATES cache)")

    if args.case:
        import yaml

        cfg = yaml.safe_load((REPO_ROOT / "config.yaml").read_text())
        bounds = tuple(cfg["cases"][args.case]["aoi"])
        start, end = CASE_WINDOWS[args.case]
        start, end = args.start or start, args.end or end
        case_dir = REPO_ROOT / "data" / args.case
        munis = json.loads((case_dir / "geojson/municipal_boundaries.geojson").read_text())
    else:
        bounds = now_bounds()
        end = args.end or dt.datetime.now(dt.UTC).date()
        start = args.start or end - dt.timedelta(days=args.days)
        mpath = STATIC / "ibge/municipalities_now.geojson"
        munis = json.loads(mpath.read_text()) if mpath.is_file() else None

    fc, meta = rd.run(bounds, start, end, steep, landcover,
                      gpw_path=gpw if gpw.is_file() else None,
                      munis_fc=munis, workers=args.workers)
    if munis is None:
        meta["_null_reasons"]["municipality"] = "municipal boundaries file absent"

    if args.case:
        out = case_dir / "geojson/landslide_candidates.geojson"
        rd.write_json(out, fc)
        # same size budget/rules as publish_case, then validate what is published
        from tp.bundle import GEOJSON_COMPACT, _compact_geojson

        _compact_geojson(out, GEOJSON_COMPACT["landslide_candidates"])
        fc = json.loads(out.read_text())
        inv = case_dir / "geojson/landslide_catalog.geojson"
        val = rd.hit_rates(_points(json.loads(inv.read_text())), fc,
                           epsg=_utm_epsg(bounds)) if inv.is_file() else None
        from tp.metrics import merge_metrics

        flat = {"scar_source": rd.SOURCE, "scar_window": meta["window"],
                "scar_candidates_n": meta["features"], "scar_candidates_km2": meta["candidates_km2"],
                "scar_people": meta["people"],
                "scar_hit_100m_pct": val and val["hit_100m_pct"],
                "scar_hit_500m_pct": val and val["hit_500m_pct"],
                "scar_precision_500m_pct": val and val["precision_500m_pct"],
                "scar_inventory_n": val and val["n_points"],
                "scar_method": ("OPERA DIST-ALERT-HLS VEG-DIST-STATUS in {5,6,8} (>=50 % loss), "
                                f"first detection in window, {CASE_STEEP[args.case]}, not cropland/water "
                                "(MCD12Q1 2025), 3 px..1 km2; candidates, never confirmed")}
        reasons = {} if val else {k: "no landslide inventory for this case"
                                  for k in ("scar_hit_100m_pct", "scar_hit_500m_pct", "scar_precision_500m_pct",
                                            "scar_inventory_n")}
        merge_metrics(args.case, flat, reasons=reasons, path=REPO_ROOT / "data/metrics.json")
        print(json.dumps({**{k: meta[k] for k in ("window", "n_tiles", "n_tiles_failed", "features",
                                                   "candidates_km2", "people", "excluded")},
                          "validation": val}, indent=2))
    else:
        resp = REPO_ROOT / "data/now/response"
        outline = STATIC / "brazil_outline.geojson"
        if outline.is_file():
            from shapely.geometry import shape
            from shapely.prepared import prep

            br = [prep(shape(f["geometry"])) for f in json.loads(outline.read_text())["features"]]
            keep = [f for f in fc["features"]
                    if any(b.contains(shape(f["geometry"]).representative_point()) for b in br)]
            meta["excluded"]["outside_brazil_polygons"] = len(fc["features"]) - len(keep)
            fc["features"] = keep
            meta["features"] = len(keep)
            meta["candidates_km2"] = round(sum(f["properties"]["area_km2"] for f in keep), 3)
            ppl = [f["properties"]["people"] for f in keep if f["properties"]["people"] is not None]
            meta["people"] = round(sum(ppl), 1) if ppl else None
        else:
            meta["_null_reasons"]["brazil_clip"] = "Brazil outline absent: candidates not clipped"
        meta["latency_note"] = ("DIST-ALERT-HLS is published ~1-2 weeks after acquisition; "
                                "latest_acquired is the newest granule CMR had at run time")
        meta["caveat"] = ("vegetation loss on steep slopes outside a rain event is mostly forestry "
                          "harvest / fire; RS 2024 validation (event window) found 55 % of "
                          "candidates within 500 m of an inventoried landslide")
        rd.write_json(resp / "landslide_dist.geojson", fc)
        meta.pop("tiles", None)
        (resp / "dist_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")  # meta last
        print(json.dumps({k: meta[k] for k in ("window", "latest_acquired", "n_tiles", "n_tiles_failed",
                                               "features", "candidates_km2", "people", "excluded",
                                               "_null_reasons")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
