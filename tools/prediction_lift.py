"""L43: lift of every published "warned before onset" hit rate.

For the NOAA GFS 00Z run issued >= 24 h before the L37 onset (the same run the
hit rates use: issue = UTC date of onset - 24 h, frames depicting dates <= the
onset day, max over frames), compute the share of the scored DOMAIN that the
run put in Alto+ (TPI > 0.4) and Crítico (> 0.6):

    area_share = geodesic km² of domain cells above the cut / km² of domain cells
    lift       = hit rate / area_share

A lift of 1 means the forecast flagged the damage no more often than a random
location of the domain; > 1 means it concentrated on where damage happened.
Domain = finite-TPI cells of the case grid (the case AOI; RS includes lagoon
and urban cells where the TPI is defined) or, for the Copernicus EMS scores,
the cells inside the EMS AOI extents (data/rs_2024/geojson/flood_ems_aoi.geojson).

    PYTHONPATH=.:pipeline .venv/bin/python tools/prediction_lift.py [--write]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import warned_before as wb

CUTS = {"alto_plus": 0.4, "critico": 0.6}


def run_frames(cog: Path, case: str, hazard: str, anchor: dt.datetime,
               cog_suffix: str = ""):
    """(issue, max-over-frames TPI array, frame names, transform) for the run
    issued on the UTC date of anchor - 24 h (frames depicting <= anchor day)."""
    issue = (anchor - dt.timedelta(hours=24)).date()
    p = cog / f"{case}_tpi_gfs_{issue:%Y%m%d}{cog_suffix}.tif"
    with rasterio.open(p) as src:
        arrs, names = [], []
        for b, name in enumerate(src.descriptions, start=1):
            k = int(name[1])
            if name.endswith(f"_{hazard}") and issue + dt.timedelta(days=k) <= anchor.date():
                arrs.append(src.read(b).astype(np.float64))
                names.append(f"issued {issue} {name} -> {issue + dt.timedelta(days=k)}")
        return issue, wb._nanmax([a.ravel() for a in arrs]).reshape(arrs[0].shape), names, src.transform, src.crs


def cell_km2(tr, shape) -> np.ndarray:
    lat = tr.f + (np.arange(shape[0]) + 0.5) * tr.e
    return (abs(tr.a * tr.e) * wb.KM_PER_DEG ** 2 * np.cos(np.deg2rad(lat)))[:, None] * np.ones((1, shape[1]))


def area_shares(tpi: np.ndarray, km2: np.ndarray, domain: np.ndarray) -> dict:
    dom = domain & np.isfinite(tpi)
    tot = float(km2[dom].sum())
    out = {"domain_km2": round(tot, 1)}
    for name, cut in CUTS.items():
        out[f"{name}_km2"] = round(float(km2[dom & (tpi > cut)].sum()), 1)
        out[f"{name}_share_pct"] = round(100 * out[f"{name}_km2"] / tot, 2) if tot else None
    return out


def ems_domain(data_root: Path, tr, shape, crs) -> np.ndarray:
    from rasterio.features import rasterize
    from shapely.geometry import shape as shp

    fc = json.loads((data_root / "rs_2024" / "geojson" / "flood_ems_aoi.geojson").read_text())
    geoms = [shp(f["geometry"]) for f in fc["features"]]
    return rasterize(geoms, out_shape=shape, transform=tr, fill=0, default_value=1,
                     dtype="uint8").astype(bool)


def lift(hit, share):
    if hit is None or share in (None, 0):
        return None
    return round(hit / share, 2)


def run(data_root: Path, cog_suffix: str = "", cases: dict | None = None) -> dict:
    m = cases if cases is not None else json.loads(
        (data_root / "metrics.json").read_text())["cases"]
    rs, wa = m["rs_2024"], m["wa_2025"]
    on_rs = wb.ANCHORS["rs_2024"][0]["utc"]
    on_wa = wb.ANCHORS["wa_2025"][0]["utc"]
    out = {}

    # RS slide: whole case domain + EMS AOIs
    issue, tpi, names, tr, crs = run_frames(data_root / "rs_2024" / "cog", "rs_2024", "slide", on_rs, cog_suffix)
    km2 = cell_km2(tr, tpi.shape)
    whole = area_shares(tpi, km2, np.ones(tpi.shape, bool))
    ems = area_shares(tpi, km2, ems_domain(data_root, tr, tpi.shape, crs))
    lw = next(r for r in rs["landslide_ems"]["tpi_warned"]["gfs_by_lead"] if r["lead_h"] == 24)
    out["rs_inventory_slide"] = {
        "hit": {"alto_plus": rs["slide_warned_gfs_24h_pct"], "critico": rs["slide_warned_gfs_24h_critical_pct"]},
        "domain": "rs_2024 case grid (finite TPI_slide)", "area": whole}
    out["rs_ems_landslide_any_cell"] = {
        "hit": {"alto_plus": lw["any_cell"]["alto_plus"]["pct"], "critico": lw["any_cell"]["critico"]["pct"]},
        "domain": "EMSR720 AOI extents (Taquari valley)", "area": ems,
        "note": "any touched cell counts — inflates the hit vs an area share; use the centroid row for lift"}
    out["rs_ems_landslide_centroid"] = {
        "hit": {"alto_plus": lw["centroid_cell"]["alto_plus"]["pct"], "critico": lw["centroid_cell"]["critico"]["pct"]},
        "domain": "EMSR720 AOI extents (Taquari valley)", "area": ems}
    for v in (out["rs_inventory_slide"], out["rs_ems_landslide_any_cell"], out["rs_ems_landslide_centroid"]):
        v.update(issue=issue.isoformat(), frames=names, hazard="slide")

    # RS flood vs EMS flood (EMS AOIs)
    issue_f, tpi_f, names_f, tr_f, crs_f = run_frames(data_root / "rs_2024" / "cog", "rs_2024", "flood", on_rs, cog_suffix)
    km2f = cell_km2(tr_f, tpi_f.shape)
    g = rs["flood_ems"]["flagged_before_onset"]["gfs_issued"][issue_f.isoformat()]
    out["rs_ems_flood"] = {
        "hit": {"alto_plus": g["alto_or_critico_pct"], "critico": g["critico_pct"]},
        "domain": "EMSR720 AOI extents (Taquari valley)",
        "area": area_shares(tpi_f, km2f, ems_domain(data_root, tr_f, tpi_f.shape, crs_f)),
        "issue": issue_f.isoformat(), "frames": names_f, "hazard": "flood"}

    # WA flood (NISAR-mapped candidates), whole case grid
    issue_w, tpi_w, names_w, tr_w, _ = run_frames(data_root / "wa_2025" / "cog", "wa_2025", "flood", on_wa, cog_suffix)
    out["wa_nisar_flood"] = {
        "hit": {"alto_plus": wa["flood_warned_gfs_24h_pct"], "critico": wa["flood_warned_gfs_24h_critical_pct"]},
        "domain": "wa_2025 case grid (finite TPI_flood)",
        "area": area_shares(tpi_w, cell_km2(tr_w, tpi_w.shape), np.ones(tpi_w.shape, bool)),
        "issue": issue_w.isoformat(), "frames": names_w, "hazard": "flood"}

    for v in out.values():
        v["lift"] = {k: lift(v["hit"][k], v["area"][f"{k}_share_pct"]) for k in CUTS}
    return out


KEYS = {  # result row -> (case, flat key prefix)
    "rs_inventory_slide": ("rs_2024", "slide_warned_gfs_24h"),
    "rs_ems_landslide_any_cell": ("rs_2024", "landslide_ems_warned_gfs_24h"),
    "rs_ems_landslide_centroid": ("rs_2024", "landslide_ems_warned_gfs_24h_centroid"),
    "rs_ems_flood": ("rs_2024", "flood_ems_flagged_before_onset"),
    "wa_nisar_flood": ("wa_2025", "flood_warned_gfs_24h"),
}


def merge(data_root: Path, res: dict) -> None:
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    for row, (case, pre) in KEYS.items():
        r, b = res[row], m["cases"][case]
        b[f"{pre}_area_share_pct"] = r["area"]["alto_plus_share_pct"]
        b[f"{pre}_critical_area_share_pct"] = r["area"]["critico_share_pct"]
        b[f"{pre}_lift"] = r["lift"]["alto_plus"]
        b[f"{pre}_critical_lift"] = r["lift"]["critico"]
        for cut, key in (("alto_plus", f"{pre}_lift"), ("critico", f"{pre}_critical_lift")):
            if r["lift"][cut] is None:
                b.setdefault("_null_reasons", {})[key] = (
                    "undefined: the run flagged 0 % of the domain and 0 % of the damage "
                    f"(issue {r['issue']})")
        if row == "rs_ems_landslide_centroid":
            b[f"{pre}_pct"] = r["hit"]["alto_plus"]
            b[f"{pre}_critical_pct"] = r["hit"]["critico"]
    m["cases"]["rs_2024"]["prediction_lift"] = {
        "definition": ("lift = hit rate / area share, for the NOAA GFS 00Z run issued >= 24 h before "
                       "the L37 onset (frames depicting dates <= the onset day, max over frames); area "
                       "share = % of the scored domain (finite-TPI cells; geodesic km²) above the same cut. "
                       "Lift 1 = no better than a random location of the domain."),
        "rows": {k: v for k, v in res.items() if k.startswith("rs_")}}
    m["cases"]["wa_2025"]["prediction_lift"] = {
        "definition": m["cases"]["rs_2024"]["prediction_lift"]["definition"],
        "rows": {k: v for k, v in res.items() if k.startswith("wa_")}}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--cog-suffix", default="")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    if args.write and args.cog_suffix:
        raise SystemExit("--write merges under the v1 keys only; re-scored "
                         "(_v2) results are merged via tools/replay_v2_score.py")
    res = run(args.data_root, args.cog_suffix)
    for k, v in res.items():
        print(k, v["issue"], "hit", v["hit"], "share", {c: v["area"][f"{c}_share_pct"] for c in CUTS},
              "lift", v["lift"], "domain_km2", v["area"]["domain_km2"])
    if args.write:
        merge(args.data_root, res)
        print("merged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
