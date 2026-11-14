"""Static fallback copies and missing live renders from canonical GEE assets."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

CASES = ("rs_2024", "wa_2025")


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


def plan(root: str | Path, *, now: bool = False, case: str | None = None,
         prefix: str = "fallback/") -> list[tuple[Path, str]]:
    if now == (case is not None) or (case is not None and case not in CASES):
        raise ValueError("select either now or a canonical case")
    prefix = prefix.strip("/")
    if prefix and ("\\" in prefix or any(p in (".", "..", "") for p in prefix.split("/"))):
        raise ValueError("invalid fallback prefix")
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
    return [(p, "/".join(filter(None, (prefix, p.relative_to(root).as_posix()))))
            for p in files]


def now_plan(root: str | Path, *, prefix: str = "fallback/") -> list[tuple[Path, str]]:
    root = Path(root)
    _file(root / "manifest.json")
    _file(root / "now/alerts.json")
    node = json.loads(_file(root / "now/manifest.json").read_text())
    if not node.get("layers"):
        raise ValueError("now manifest must list derived TPI layers")
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
    prefix = prefix.strip("/")
    if prefix and ("\\" in prefix or any(p in (".", "..", "") for p in prefix.split("/"))):
        raise ValueError("invalid fallback prefix")
    return [(root / name, "/".join(filter(None, (prefix, name))))
            for name in [*names, "now/manifest.json", "manifest.json"]]


def _storage(bucket=None, client=None):
    from fetch import ingest

    bucket = (bucket or ingest.staging_bucket()).removeprefix("gs://").rstrip("/")
    ingest._validate_path_safe(bucket, "bucket")
    if client is None:
        ingest._require_creds()
        storage = ingest._import("google.cloud.storage")
        client = storage.Client()
    return bucket, client


def render_now(root: str | Path, *, bucket=None, prefix="fallback/", client=None,
               ee_client=None, fetch_png=None, sleep=None, max_polls=360):
    import tempfile
    import time

    from tp.config import thresholds

    cuts = thresholds()["tpi_classes"]
    root = Path(root)
    entries = now_plan(root, prefix=prefix)
    if all(path.is_file() for path, _ in entries):
        return entries
    bucket, client = _storage(bucket, client)
    if ee_client is None:
        from gee import common

        common.init()
        ee_client = common.ee
    if fetch_png is None:
        import requests

        def fetch_png(url):
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            return response.content
    sleep = sleep or time.sleep
    ee = ee_client
    node = json.loads((root / "now/manifest.json").read_text())
    bounds = node["bounds"]
    region = ee.Geometry.Rectangle(bounds)
    images, classes, layers, stamps = {}, {}, {}, {}
    for layer in node["layers"]:
        name = layer["id"]
        if not name.startswith(("tpi_slide_d", "tpi_flood_d")):
            continue
        if not layer.get("asset"):
            raise ValueError(f"missing required GEE asset reference for {name}")
        image = ee.Image(layer["asset"]).select(0)
        metadata = image.toDictionary(["data_date", "updated_at"]).getInfo()
        if not metadata.get("data_date") or not metadata.get("updated_at"):
            raise ValueError(f"missing required asset timestamps for {name}")
        stamps[name] = metadata
        images[name] = image
        classes[name] = image.gte(cuts[0]).add(image.gte(cuts[1])).add(image.gte(cuts[2])).rename("class").toInt()
        layers[name] = layer
    for name in ("tpi_slide_d0", "tpi_flood_d0", "tpi_slide_d1", "tpi_flood_d1"):
        if name not in images:
            raise ValueError(f"missing required manifest layer: {name}")
    keys = {path: key for path, key in entries}
    for name, image in images.items():
        layer = layers[name]
        for kind in ("png", "cog"):
            if not layer.get(kind):
                continue
            path = root / "now" / layer[kind]
            if path.is_file():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if kind == "png":
                url = classes[name].getThumbURL({
                    "region": region, "crs": "EPSG:3857", "dimensions": 2048,
                    "format": "png", "min": 0, "max": 3,
                    "palette": ["ffffb2", "fecc5c", "fd8d3c", "bd0026"]})
                path.write_bytes(fetch_png(url))
            else:
                key = keys[path]
                if not key.endswith(".tif"):
                    raise ValueError("GEE COG output must use .tif suffix")
                task = ee.batch.Export.image.toCloudStorage(
                    image=image.toFloat(), description=f"fallback_{name}", bucket=bucket,
                    fileNamePrefix=key[:-4], region=region, scale=1000, crs="EPSG:4326",
                    maxPixels=1e9, fileFormat="GeoTIFF", formatOptions={"cloudOptimized": True})
                task.start()
                for _ in range(max_polls):
                    state = task.status().get("state")
                    if state == "COMPLETED":
                        break
                    if state in ("FAILED", "CANCELLED"):
                        raise RuntimeError(f"GEE COG export failed for {name}")
                    sleep(10)
                else:
                    raise RuntimeError(f"GEE COG export timed out for {name}")
                with tempfile.TemporaryDirectory(dir=path.parent) as directory:
                    temporary = Path(directory) / path.name
                    client.bucket(bucket).blob(key).download_to_filename(str(temporary))
                    temporary.replace(path)
    labels = ["Baixo", "Moderado", "Alto", "Crítico"]
    area = ee.Image.pixelArea().divide(1e6)

    def km2(mask):
        values = area.updateMask(mask).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=region, scale=1000, maxPixels=1e9).getInfo()
        return float(next(iter(values.values())) or 0)

    summary_path = root / "now/summary.json"
    if not summary_path.is_file():
        slide = images["tpi_slide_d0"].gte(cuts[1])
        flood = images["tpi_flood_d0"].gte(cuts[1])
        worst = classes["tpi_slide_d0"].max(classes["tpi_flood_d0"])
        summary = {"generated_at": max(stamp["updated_at"] for stamp in stamps.values()),
                   "forecast_source": "geos-fp", "asset_dates": stamps,
                   "data_dates": node.get("data_dates", {}),
                   "critical_km2": {"slide": km2(slide), "flood": km2(flood), "any": km2(slide.Or(flood))},
                   "classes_km2": {label: km2(worst.eq(i)) for i, label in enumerate(labels)},
                   "people_critical": None, "people_exposed": None,
                   "_null_reasons": {"people_critical": "population not present in derived TPI assets",
                                     "people_exposed": "population not present in derived TPI assets"}}
        summary_path.write_text(json.dumps(summary))
    critical_path = root / "now/geojson/critical_areas.geojson"
    if not critical_path.is_file():
        features = []
        for name in ("tpi_slide_d0", "tpi_flood_d0", "tpi_slide_d1", "tpi_flood_d1"):
            cls = classes[name]
            collection = cls.updateMask(cls.gte(2)).reduceToVectors(
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


def upload(entries: list[tuple[Path, str]], *, bucket: str | None = None, client=None) -> None:
    from fetch import ingest

    bucket = (bucket or ingest.staging_bucket()).removeprefix("gs://").rstrip("/")
    ingest._validate_path_safe(bucket, "bucket")
    for path, _ in entries:
        _file(path)
    if client is None:
        ingest._require_creds()
        storage = ingest._import("google.cloud.storage")
        try:
            client = storage.Client()
        except Exception as exc:
            raise RuntimeError("GCS client initialization failed; check credentials and permissions") from exc
    try:
        destination = client.bucket(bucket)
        for path, key in entries:
            destination.blob(key).upload_from_filename(str(path))
    except Exception as exc:
        raise RuntimeError("GCS upload failed; check credentials, permissions and connectivity") from exc
