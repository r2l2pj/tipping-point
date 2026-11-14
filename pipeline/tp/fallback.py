"""Static fallback copies and missing live renders from canonical GEE assets.

Per ruling L10 there is no Cloud Storage bucket and no staging prefix:
everything is written locally under the data root; lead-live's workflows
publish ``data/`` itself to the orphan ``fallback`` branch.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

CASES = ("rs_2024", "wa_2025")
LAYER_BANDS = {"tpi_slide": "TPI_slide", "tpi_flood": "TPI_flood", "r": "R", "s": "S"}
MAX_COG_BYTES = 10 * 1024 * 1024


def _layer_band(name: str) -> str:
    band = LAYER_BANDS.get(name.rsplit("_d", 1)[0])
    if band is None:
        raise ValueError(f"unrecognized layer prefix for {name!r}")
    return band


def _file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"missing expected input: {path}")
    return path


def _references(node: dict):
    for layer in node.get("layers", []):
        for field in ("png", "cog"):
            if value := layer.get(field):
                yield value
    yield from (value for value in node.get("products", {}).values() if value)


def _reference(base: Path, value: str) -> Path:
    rel = PurePosixPath(value.split("#", 1)[0])
    if rel.is_absolute() or ".." in rel.parts or "\\" in value:
        raise ValueError("manifest reference must stay within its data directory")
    return _file(base / rel)


def plan(root: str | Path, *, now: bool = False, case: str | None = None) -> list[str]:
    if now == (case is not None) or (case is not None and case not in CASES):
        raise ValueError("select either now or a canonical case")
    root = Path(root)
    group = root / ("now" if now else case)
    manifest = _file(root / "manifest.json")
    doc = json.loads(manifest.read_text())
    if now:
        local_manifest = _file(group / "manifest.json")
        node = json.loads(local_manifest.read_text())
        for rel in ("summary.json", "alerts.json", "geojson/critical_areas.geojson"):
            _file(group / rel)
        for value in _references(node):
            _reference(group, value)
    else:
        node = doc["cases"][case]
        for value in _references(node):
            _reference(root, value)
        _file(root / "metrics.json")
    if not group.is_dir():
        raise FileNotFoundError(f"missing expected input directory: {group}")
    files = sorted(p for p in group.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"no producer outputs under {group}")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("fallback inputs must be regular files within data root")
    manifests = [p for p in files if p.name == "manifest.json"]
    files = [p for p in files if p not in manifests]
    if not now:
        files.append(root / "metrics.json")
    files.extend(manifests)
    files.append(manifest)
    return [p.relative_to(root).as_posix() for p in files]


def now_plan(root: str | Path) -> list[str]:
    root = Path(root)
    _file(root / "manifest.json")
    _file(root / "now/alerts.json")
    node = json.loads(_file(root / "now/manifest.json").read_text())
    if not node.get("layers"):
        raise ValueError("now manifest must list derived TPI layers")
    for layer in node["layers"]:
        _layer_band(layer["id"])
    expected = {"now/summary.json", "now/geojson/critical_areas.geojson"}
    for value in _references(node):
        rel = PurePosixPath(value.split("#", 1)[0])
        if rel.is_absolute() or ".." in rel.parts or "\\" in value:
            raise ValueError("invalid now manifest reference")
        expected.add(f"now/{rel}")
    existing = {p.relative_to(root).as_posix() for p in (root / "now").rglob("*") if p.is_file()}
    for name in expected | existing:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("fallback inputs must stay within data root")
    names = sorted((expected | existing) - {"now/manifest.json"})
    return [*names, "now/manifest.json", "manifest.json"]


def _download(url: str, limit: int | None = None) -> bytes | None:
    import requests

    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if limit is not None and length is not None and int(length) > limit:
            return None
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=1 << 20):
            total += len(chunk)
            if limit is not None and total > limit:
                return None
            chunks.append(chunk)
    return b"".join(chunks)


def _write_cog(image, region, path: Path, *, fetch, max_bytes: int,
               scale: int = 1000) -> bool:
    url = image.getDownloadURL({
        "name": path.stem, "region": region, "scale": scale,
        "crs": "EPSG:4326", "format": "GEO_TIFF"})
    data = fetch(url, limit=max_bytes)
    if data is None:
        print(f"fallback: skip {path.name}: COG over {max_bytes}-byte cap", file=sys.stderr)
        return False
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member = next((n for n in archive.namelist() if n.endswith(".tif")), None)
            if member is None:
                print(f"fallback: skip {path.name}: download had no GeoTIFF member", file=sys.stderr)
                return False
            data = archive.read(member)
    path.write_bytes(data)
    return True


def render_now(root: str | Path, *, ee_client=None, fetch=None, fetch_png=None,
               max_bytes: int = MAX_COG_BYTES) -> list[str]:
    from tp.config import thresholds

    cuts = thresholds()["tpi_classes"]
    root = Path(root)
    entries = now_plan(root)
    if all((root / rel).is_file() for rel in entries):
        return entries
    if ee_client is None:
        from gee import common

        common.init()
        ee_client = common.ee
    if fetch is None:
        fetch = _download
    if fetch_png is None:
        fetch_png = lambda url: _download(url)
    ee = ee_client
    node = json.loads((root / "now/manifest.json").read_text())
    bounds = node["bounds"]
    region = ee.Geometry.Rectangle(bounds)
    images, classes, layers, stamps = {}, {}, {}, {}
    for layer in node["layers"]:
        name = layer["id"]
        band = _layer_band(name)
        if not layer.get("asset"):
            raise ValueError(f"missing required GEE asset reference for {name}")
        image = ee.Image(layer["asset"]).select(band)
        metadata = image.toDictionary(["data_date", "updated_at"]).getInfo()
        if not metadata.get("data_date") or not metadata.get("updated_at"):
            raise ValueError(f"missing required asset timestamps for {name}")
        stamps[name] = metadata
        images[name] = image
        if band in ("TPI_slide", "TPI_flood"):
            classes[name] = image.gte(cuts[0]).add(image.gte(cuts[1])).add(image.gte(cuts[2])).rename("class").toInt()
        layers[name] = layer
    for name in ("tpi_slide_d0", "tpi_flood_d0", "tpi_slide_d1", "tpi_flood_d1"):
        if name not in images:
            raise ValueError(f"missing required manifest layer: {name}")
    for name, image in images.items():
        layer = layers[name]
        if layer.get("png"):
            path = root / "now" / layer["png"]
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                url = classes.get(name, image).getThumbURL({
                    "region": region, "crs": "EPSG:3857", "dimensions": 2048,
                    "format": "png", "min": 0, "max": 3 if name in classes else 1,
                    "palette": ["ffffb2", "fecc5c", "fd8d3c", "bd0026"]})
                path.write_bytes(fetch_png(url))
        if layer.get("cog"):
            path = root / "now" / layer["cog"]
            if not path.is_file():
                if not str(layer["cog"]).endswith(".tif"):
                    raise ValueError("GEE COG output must use .tif suffix")
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_cog(image.toFloat(), region, path, fetch=fetch, max_bytes=max_bytes)
    labels = ["Baixo", "Moderado", "Alto", "Crítico"]
    area = ee.Image.pixelArea().divide(1e6)

    def km2(mask):
        values = area.updateMask(mask).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=region, scale=1000, maxPixels=1e9).getInfo()
        return float(next(iter(values.values())) or 0)

    summary_path = root / "now/summary.json"
    if not summary_path.is_file():
        slide = images["tpi_slide_d0"].gt(cuts[2])
        flood = images["tpi_flood_d0"].gt(cuts[2])
        population = (ee.ImageCollection("CIESIN/GPWv411/GPW_Population_Count")
                      .sort("system:time_start", False).first().select("population_count"))

        def people(mask):
            values = population.updateMask(mask).reduceRegion(
                reducer=ee.Reducer.sum(), geometry=region, scale=1000, maxPixels=1e9).getInfo()
            value = next(iter(values.values()), None)
            return float(value) if value is not None else None

        exposed = people(slide.Or(flood))
        flooded = people(flood)
        worst = classes["tpi_slide_d0"].max(classes["tpi_flood_d0"])
        summary = {"generated_at": max(stamp["updated_at"] for stamp in stamps.values()),
                   "forecast_source": "geos-fp", "asset_dates": stamps,
                   "data_dates": node.get("data_dates", {}),
                   "critical_km2": {"slide": km2(slide), "flood": km2(flood), "any": km2(slide.Or(flood))},
                   "classes_km2": {label: km2(worst.eq(i)) for i, label in enumerate(labels)},
                   "people_critical": exposed, "people_exposed": exposed,
                   "people_flooded": flooded,
                   "_null_reasons": {key: "GPW has no population coverage in the selected zone"
                                     for key, value in {"people_critical": exposed,
                                                        "people_exposed": exposed,
                                                        "people_flooded": flooded}.items()
                                     if value is None}}
        summary_path.write_text(json.dumps(summary))
    critical_path = root / "now/geojson/critical_areas.geojson"
    if not critical_path.is_file():
        features = []
        for name in ("tpi_slide_d0", "tpi_flood_d0", "tpi_slide_d1", "tpi_flood_d1"):
            cls = classes[name]
            collection = cls.updateMask(images[name].gt(cuts[2])).reduceToVectors(
                geometry=region, scale=1000, geometryType="polygon", eightConnected=True,
                labelProperty="class", maxPixels=1e9, geometryInNativeProjection=False).getInfo()
            for feature in collection["features"]:
                props = feature["properties"]
                props.update(hazard="slide" if "slide" in name else "flood",
                             date=stamps[name]["data_date"], label=labels[int(props["class"])])
                features.append(feature)
        critical_path.parent.mkdir(parents=True, exist_ok=True)
        critical_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return entries
