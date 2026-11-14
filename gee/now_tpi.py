"""Modo-Agora TPI stack in Earth Engine (L8).

    uv run python -m gee.now_tpi [--dry-run] [--project P]
    uv run python tools/now_tpi.py [--dry-run]            # now.yml calls this

Computes the 4-day Modo-Agora stack on the ingested `now/` asset
collections and exports one single-band Image per layer/day —
now/tpi_slide_d{0..3}, now/tpi_flood_d{0..3}, now/r_d{0..3},
now/s_d{0..3} — so the app can getMapId each asset directly.

Inputs (under projects/<GEE_PROJECT>/assets/tipping-point/):
  now/imerg_early — IMERG Early images tagged YYYYMMDD (daily mm) and
      YYYYMMDD_3day (rolling 3-day mm) by pipeline/fetch/imerg.py.
  now/smap_l4   — latest SMAP L4 sm_rootzone_wetness, tag YYYYMMDD.
  now/geosfp    — GEOS-FP forecast daily mm, tag = forecast VALID date.
  static/<domains.now.name>/clim_rain — ImageCollection of 366 images
      (prop `doy`, bands p90|p95|p99 = 3-day-sum mm percentiles).
  static/<domains.now.name>/{slope,hand} — terrain statics (deg/m, ~30 m).

d0 is observed: the newest IMERG 3-day accumulation + latest SMAP L4.
d1..d3 are GEOS-FP valid dates; their 3-day rain blends the (3-k) most
recent observed dailies with the first k forecast leads — the same
projected_rain3 window as live/tpi.py — and SMAP L4 is HELD at the
latest observation for every day (soil wetness is not forecast).
R/S/TPI reuse gee.tpi's rain_score/geomean and config.yaml thresholds.

Every exported image is stamped with data_date (YYYY-MM-DD of the
driving source) and updated_at (ISO UTC); metrics merge under
cases['now'] in data/metrics.json via tp.metrics.merge_metrics.

--dry-run prints the planned reads/writes: no ee.Initialize, works
without credentials.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

import ee
import tp.metrics as _metrics
from tp.config import clim_scale, now_bounds, now_name, thresholds

from . import common, tpi

FORECAST_DAYS = 3                     # d0 observed + d1..d3 GEOS-FP leads
FORECAST_SOURCE = "geos-fp"
SCALE_M = 1000                        # matches gee.tpi series scale (~1 km grid)

# `now/` input collections and `static/<domains.now.name>/` inputs (leaf names).
COLLECTIONS = ("imerg_early", "smap_l4", "geosfp")
STATICS = ("clim_rain", "slope", "hand")

# asset leaf prefix -> stack band exported into now/<prefix>_d{k}.
LAYER_BANDS = {"tpi_slide": "TPI_slide", "tpi_flood": "TPI_flood",
               "r": "R", "s": "S"}

_TAG_DATE_LEN = 8                     # YYYYMMDD


def _utcnow() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _tag_date(tag: str) -> str:
    """YYYYMMDD tag -> YYYY-MM-DD."""
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"


def _doy(tag: str) -> int:
    return date.fromisoformat(_tag_date(tag)).timetuple().tm_yday


def _root(project: str | None = None) -> str:
    """ASSET_ROOT with the project placeholder filled ('<GEE_PROJECT>'
    verbatim when no project is known — the dry-run path stays honest
    without creds)."""
    proj = project or common.GEE_PROJECT or "<GEE_PROJECT>"
    return common.ASSET_ROOT.format(GEE_PROJECT=proj)


def input_asset_ids(project: str | None = None) -> dict:
    """{leaf: assetId} for the now/ collections + static/<now domain> statics."""
    root = _root(project)
    return ({n: f"{root}/now/{n}" for n in COLLECTIONS}
            | {n: f"{root}/static/{now_name()}/{n}" for n in STATICS})


def derived_asset_ids(project: str | None = None) -> dict:
    """{name: assetId} for the 16 now/<layer>_d{k} output Images."""
    root = _root(project)
    return {f"{layer}_d{k}": f"{root}/now/{layer}_d{k}"
            for layer in LAYER_BANDS for k in range(FORECAST_DAYS + 1)}


def daily_tags(tags: list[str]) -> list[str]:
    """Sorted daily (non-_3day) tags."""
    return sorted(t for t in tags if not t.endswith("_3day"))


def select_obs_rain3(tags: list[str]) -> list[str]:
    """Tags summed for d0's 3-day rain: the newest *_3day product when
    present, else the 3 newest daily tags."""
    t3 = sorted(t for t in tags if t.endswith("_3day"))
    return [t3[-1]] if t3 else daily_tags(tags)[-3:]


def forecast_leads(tags: list[str]) -> list[str]:
    """[d1, d2, d3] valid-date tags — the FORECAST_DAYS newest, ascending.

    GEOS-FP tags are valid dates, so the furthest-out 3 are the latest
    run's D+1..D+3 leads.
    """
    return sorted(tags)[-FORECAST_DAYS:]


def day_plans(imerg_tags: list[str], smap_tags: list[str],
              geos_tags: list[str]) -> list[dict]:
    """Per-day source plan over collection tags — pure, unit-testable.

    One entry per k in 0..FORECAST_DAYS: {k, date_tag, doy, smap, rain3:
    [(collection, tag), ...]}. d0 sums select_obs_rain3; d_k blends the
    (3-k) most recent observed dailies with leads[0..k-1] (live/tpi.py's
    projected_rain3 window). SMAP L4 is held at the latest tag for every
    day — soil wetness is not forecast (documented contract).
    """
    daily = daily_tags(imerg_tags)
    leads = forecast_leads(geos_tags)
    if not daily and not any(t.endswith("_3day") for t in imerg_tags):
        raise ValueError("no IMERG Early assets under now/imerg_early")
    if len(leads) < FORECAST_DAYS:
        raise ValueError(
            f"now/geosfp needs {FORECAST_DAYS} valid-date tags, got {leads}")
    if not smap_tags:
        raise ValueError("no SMAP L4 assets under now/smap_l4")
    smap = max(smap_tags)
    plans = []
    for k in range(FORECAST_DAYS + 1):
        if k == 0:
            obs = select_obs_rain3(imerg_tags)
            date_tag = (daily[-1] if daily else obs[0][:-len("_3day")])
        else:
            obs = daily[-(3 - k):] if k < 3 else []
            date_tag = leads[k - 1]
        rain3 = ([("imerg_early", t) for t in obs]
                 + [("geosfp", t) for t in leads[:k]])
        plans.append({"k": k, "date_tag": date_tag, "doy": _doy(date_tag),
                      "smap": smap, "rain3": rain3})
    return plans


def collection_tags(coll_id: str) -> list[str]:
    """Sorted child tags (last path segment) of a now/ collection."""
    ids = ee.ImageCollection(coll_id).aggregate_array("system:id").getInfo() or []
    return sorted(str(i).rsplit("/", 1)[-1] for i in ids)


def _rain3_image(asset, sources) -> ee.Image:
    """First-band sum of the (collection, tag) source images."""
    imgs = [ee.Image(asset(f"now/{leaf}/{tag}")).select(0)
            for leaf, tag in sources]
    return (imgs[0] if len(imgs) == 1
            else ee.ImageCollection.fromImages(imgs).sum())


def terrain_scores(asset) -> tuple:
    """(T_slide, T_flood) in [0,1] from the static/<now domain> statics —
    gee.tpi.terrain_scores with slope/HAND read instead of derived."""
    thr = thresholds()
    slope = ee.Image(asset(f"static/{now_name()}/slope")).select(0)
    hand = ee.Image(asset(f"static/{now_name()}/hand")).select(0)
    t_slide = slope.unitScale(*thr["slope_range_deg"]).clamp(0, 1)
    t_flood = (ee.Image(1).subtract(hand.unitScale(*thr["hand_range_m"]))
               .clamp(0, 1).where(slope.gt(tpi.SLOPE_FLOOD_MAX_DEG), 0))
    return t_slide, t_flood


def day_stack(rain3, smap, clim, t_slide, t_flood):
    """4-band (R, S, TPI_slide, TPI_flood) image — gee.tpi.tpi_at's scoring
    against the precomputed DOY climatology (bands p90|p95|p99)."""
    thr = thresholds()
    scale = clim_scale()
    p90, p95, p99 = (clim.select(b).divide(scale)
                     for b in ("p90", "p95", "p99"))
    r = tpi.rain_score(rain3, p90, p95, p99)
    s = smap.select(0).unitScale(*thr["soil_wetness_range"]).clamp(0, 1)
    w = thr.get("tpi_weights")
    return (r.rename("R").addBands(s.rename("S"))
            .addBands(tpi.geomean(r, s, t_slide, w).rename("TPI_slide"))
            .addBands(tpi.geomean(r, s, t_flood, w).rename("TPI_flood")))


def compute(project: str) -> dict:
    """Build the 16 stamped layer Images; no exports, no getInfo beyond
    the tag listings. `project` is already resolved (common.init ran)."""
    asset = lambda name: common.asset_path(name, project)
    tags = {n: collection_tags(asset(f"now/{n}")) for n in COLLECTIONS}
    plans = day_plans(tags["imerg_early"], tags["smap_l4"], tags["geosfp"])
    aoi = ee.Geometry.Rectangle(list(now_bounds()))
    t_slide, t_flood = terrain_scores(asset)
    clim_col = ee.ImageCollection(asset(f"static/{now_name()}/clim_rain"))
    updated_at = _utcnow()
    images = {}
    for p in plans:
        rain3 = _rain3_image(asset, p["rain3"])
        smap = ee.Image(asset(f"now/smap_l4/{p['smap']}"))
        clim = clim_col.filter(ee.Filter.eq("doy", p["doy"])).first()
        stack = day_stack(rain3, smap, clim, t_slide, t_flood)
        stamp = {"data_date": _tag_date(p["date_tag"]),
                 "updated_at": updated_at}
        for layer, band in LAYER_BANDS.items():
            images[f"{layer}_d{p['k']}"] = (
                stack.select(band).toFloat().clip(aoi).set(stamp))
    return {"plans": plans,
            "dates": [_tag_date(p["date_tag"]) for p in plans],
            "images": images, "updated_at": updated_at, "aoi": aoi}


def export_images(images: dict, asset, aoi) -> list[dict]:
    """Start one toAsset export per layer/day Image; return the specs."""
    specs = []
    for name, img in images.items():
        spec = {"description": f"now_{name}",
                "assetId": asset(f"now/{name}")}
        try:
            ee.data.deleteAsset(spec["assetId"])
        except ee.EEException as exc:
            if "not found" not in str(exc).lower() and getattr(exc, "code", None) != 404:
                raise
        ee.batch.Export.image.toAsset(
            image=img, description=spec["description"],
            assetId=spec["assetId"], region=aoi, scale=SCALE_M).start()
        specs.append(spec)
    return specs


def _merge_now_metrics(flat: dict, collections: dict,
                       path: Path | None = None) -> dict:
    """merge_metrics under the explicitly supported live node cases['now']."""
    return _metrics.merge_metrics(
        "now", flat=flat, collections=collections, path=path)


def run(project: str | None = None, metrics_path: Path | None = None,
        merge: bool = True) -> dict:
    """Init EE, compute, export the 16 assets, merge metrics."""
    proj = common.init(project)
    asset = lambda name: common.asset_path(name, proj)
    result = compute(proj)
    specs = export_images(result["images"], asset, result["aoi"])
    flat = {"tpi_now_computed_at": result["updated_at"],
            "tpi_now_dates": result["dates"],
            "forecast_source": FORECAST_SOURCE}
    collections = {**input_asset_ids(proj), **derived_asset_ids(proj)}
    merged = (_merge_now_metrics(flat, collections, metrics_path)
              if merge else None)
    return {**result, "exports": specs, "metrics": flat, "merged": merged}


def plan(project: str | None = None) -> str:
    """--dry-run document: planned reads/writes, no ee calls at all."""
    root = _root(project)
    lines = [
        ("[now-tpi] d0=observed (IMERG Early 3d + SMAP L4 latest), "
         "d1..d3=GEOS-FP leads (SMAP L4 held at latest)"),
        "reads:"]
    for leaf in COLLECTIONS:
        lines.append(f"  collection {root}/now/{leaf} (tags -> date window)")
    for leaf in STATICS:
        lines.append(f"  {root}/static/{now_name()}/{leaf}")
    lines.append("writes (delete existing asset, then export one Image each, stamped "
                 "data_date + updated_at):")
    for aid in derived_asset_ids(project).values():
        lines.append(f"  {aid}")
    lines.append("metrics -> data/metrics.json cases['now']: "
                 "tpi_now_computed_at, tpi_now_dates (list of 4), "
                 f"forecast_source='{FORECAST_SOURCE}', collections{{...}}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="now_tpi", description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print planned reads/writes; no EE init or creds")
    ap.add_argument("--no-metrics", action="store_true",
                    help="export the assets but skip the metrics merge")
    ap.add_argument("--project", default=None,
                    help="GEE project (default: env GEE_PROJECT)")
    ap.add_argument("--metrics-path", type=Path, default=None,
                    help="metrics.json override (testing)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(plan(args.project))
        return 0
    out = run(project=args.project, metrics_path=args.metrics_path,
              merge=not args.no_metrics)
    print(json.dumps({"dates": out["dates"],
                      "exports": len(out["exports"]),
                      "metrics": out["metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
