"""Finalize data/now/manifest.json after the response steps (R1-R3).

now.yml order: run_now (TPI, writes manifest) -> R1 dswx -> R2 nisar ->
R3 summary -> this module -> publish. It re-reads the manifest, merges the
R3 fragment (response/manifest_node.json: data_dates/products/_null_reasons
key-wise, `response` replaced), declares every optional product whose file
exists, drops every product whose file is missing (never advertise a 404),
and rewrites manifest.json LAST (atomic rename).

    python -m live.now_manifest [--out-dir data/now]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# product key -> path relative to data/now/ (contract agreed with lead-app
# t17n and lead-analysis t17p, 2026-11-15)
OPTIONAL_PRODUCTS = {
    "priority_zones": "geojson/priority_zones.geojson",
    "municipal_boundaries": "geojson/municipal_boundaries.geojson",
    "permanent_water": "geojson/permanent_water.geojson",
    "flood_dswx": "response/flood_dswx.geojson",
    "dswx_meta": "response/dswx_meta.json",
    "flood_nisar": "response/flood_nisar.geojson",
    "nisar_meta": "response/nisar_meta.json",
    "response_summary": "response/summary.json",
}
FRAGMENT = "response/manifest_node.json"
MERGE_KEYS = ("data_dates", "products", "_null_reasons")


def prune_products(manifest: dict, out_dir: Path) -> dict:
    """Drop product keys whose file does not exist under out_dir."""
    products = manifest.get("products") or {}
    for key, rel in list(products.items()):
        if not isinstance(rel, str) or not (out_dir / rel).is_file():
            del products[key]
    manifest["products"] = products
    return manifest


OBSERVED_SOURCES = ("response/flood_dswx.geojson",
                    "response/flood_nisar.geojson")


def observed_floods(out_dir: Path) -> list[dict]:
    """R1/R2 polygons grouped per (source, municipality): OBSERVATIONS of
    flood candidates (satellite water), never TPI alerts — no class, no
    emergency wording. Sorted by people then area."""
    from shapely.geometry import shape

    from live.now_geojson import municipality_lookup

    where = municipality_lookup(out_dir / "geojson")
    groups: dict[tuple, dict] = {}
    for rel in OBSERVED_SOURCES:
        p = out_dir / rel
        if not p.is_file():
            continue
        for f in json.loads(p.read_text()).get("features", []):
            pr = f.get("properties") or {}
            try:
                pt = shape(f["geometry"]).representative_point()
            except (KeyError, ValueError, TypeError):
                continue
            muni = where(pt.x, pt.y) or {"municipality": None, "uf": None}
            key = (pr.get("source"), muni["municipality"], muni["uf"])
            g = groups.setdefault(key, {
                "kind": "observed", "hazard": "flood",
                "label": "observed flood candidate", "status": "candidate",
                "source": pr.get("source"), **muni,
                "acquired": None, "area_km2": 0.0, "people": None,
                "polygons": 0, "centroid": [round(pt.x, 4), round(pt.y, 4)],
                "_best": -1.0})
            area = float(pr.get("area_km2") or 0.0)
            g["area_km2"] += area
            g["polygons"] += 1
            if pr.get("people") is not None:
                g["people"] = (g["people"] or 0) + float(pr["people"])
            acq = pr.get("acquired")
            if acq and (g["acquired"] is None or acq > g["acquired"]):
                g["acquired"] = acq
            if area > g["_best"]:   # label the group at its largest polygon
                g["_best"], g["centroid"] = area, [round(pt.x, 4), round(pt.y, 4)]
    out = []
    for g in groups.values():
        g.pop("_best")
        g["area_km2"] = round(g["area_km2"], 3)
        if g["people"] is not None:
            g["people"] = int(round(g["people"]))
        out.append(g)
    out.sort(key=lambda g: (-(g["people"] or 0), -g["area_km2"]))
    return out


def add_observed_to_alerts(out_dir: Path) -> None:
    apath = out_dir / "alerts.json"
    if not apath.is_file():
        return
    alerts = json.loads(apath.read_text())
    alerts["observed"] = observed_floods(out_dir)
    alerts["observed_note"] = ("satellite flood-water candidates (OPERA "
                               "DSWx-S1, NISAR GCOV) - observations, not "
                               "TPI alerts")
    tmp = apath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(alerts, ensure_ascii=False, indent=2))
    os.replace(tmp, apath)


def finalize(out_dir: Path) -> dict:
    add_observed_to_alerts(out_dir)
    mpath = out_dir / "manifest.json"
    manifest = json.loads(mpath.read_text())
    frag_path = out_dir / FRAGMENT
    if frag_path.is_file():
        try:
            frag = json.loads(frag_path.read_text())
        except ValueError as exc:
            print(f"[now_manifest] bad {FRAGMENT}: {exc}", file=sys.stderr)
            frag = {}
        for key in MERGE_KEYS:
            if isinstance(frag.get(key), dict):
                manifest.setdefault(key, {})
                manifest[key] = {**(manifest[key] or {}), **frag[key]}
        if isinstance(frag.get("response"), dict):
            manifest["response"] = frag["response"]
    products = manifest.setdefault("products", {})
    for key, rel in OPTIONAL_PRODUCTS.items():
        products.setdefault(key, rel)
    prune_products(manifest, out_dir)
    tmp = mpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    os.replace(tmp, mpath)
    print(f"[now_manifest] products: {sorted(manifest['products'])}")
    return manifest


def main(argv=None) -> int:
    from live.config import NOW_DIR

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", type=Path, default=Path(NOW_DIR))
    args = ap.parse_args(argv)
    finalize(args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
