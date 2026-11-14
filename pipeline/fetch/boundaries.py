"""Municipal boundary fetch — REFERENCE-ONLY layer (never a TPI input).

rs-2024-05 -> IBGE servicodados malhas/localidades (RS municipalities)
wa-2025-12 -> US Census TIGERweb Places/CouSub (places + county subdivisions)

Every output Feature carries properties {name, admin, source, license, url}.
If a source is unreachable the output is an empty FeatureCollection with a
top-level 'fetch_error' member — this module never crashes silently.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests
from shapely.errors import GEOSException
from shapely.geometry import box, shape
from tp.events import event_data_dir

HTTP_TIMEOUT = 120

IBGE_MALHAS_URL = "https://servicodados.ibge.gov.br/api/v3/malhas/estados/{uf}"
IBGE_MUNICIPIOS_URL = (
    "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
)
# BAS 2026 vintage sublayers of the Places_CouSub_ConCity_SubMCD MapServer:
# 8 County Subdivisions, 11 Incorporated Places, 12 Census Designated Places.
TIGERWEB_QUERY_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "Places_CouSub_ConCity_SubMCD/MapServer/{layer}/query"
)
TIGERWEB_LAYERS = {8: "county_subdivision", 11: "place", 12: "place"}

SOURCES = {
    "IBGE": {"license": "IBGE open data (attribution required)"},
    "US Census TIGER": {"license": "public domain (US Government work)"},
}


def _get_json(url: str, **params):
    r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def intersects_aoi(feature: dict, aoi) -> bool:
    """True when a GeoJSON feature's geometry intersects the shapely AOI."""
    geom = feature.get("geometry")
    if not geom:
        return False
    try:
        return bool(shape(geom).intersects(aoi))
    except (GEOSException, ValueError, TypeError, KeyError, AttributeError):
        return False


def _feature(geometry, name, admin, source, url) -> dict:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "name": name,
            "admin": admin,
            "source": source,
            "license": SOURCES[source]["license"],
            "url": url,
        },
    }


def _fetch_ibge(aoi, uf: str) -> list[dict]:
    malhas_url = IBGE_MALHAS_URL.format(uf=uf)
    malhas = _get_json(
        malhas_url,
        formato="application/vnd.geo+json",
        qualidade="minima",
        intrarregiao="municipio",
    )
    municipios = _get_json(IBGE_MUNICIPIOS_URL.format(uf=uf))
    names = {str(m["id"]): m["nome"] for m in municipios}
    features = []
    for f in malhas.get("features", []):
        if not intersects_aoi(f, aoi):
            continue
        code = str((f.get("properties") or {}).get("codarea", ""))
        features.append(
            _feature(f["geometry"], names.get(code, code), "municipio",
                     "IBGE", malhas_url)
        )
    return features


def _fetch_tigerweb(aoi, state_fips: str) -> list[dict]:
    west, south, east, north = aoi.bounds
    features = []
    for layer, admin in TIGERWEB_LAYERS.items():
        url = TIGERWEB_QUERY_URL.format(layer=layer)
        data = _get_json(
            url,
            where=f"STATE='{state_fips}'",
            geometry=f"{west},{south},{east},{north}",
            geometryType="esriGeometryEnvelope",
            inSR=4326,
            outSR=4326,
            spatialRel="esriSpatialRelIntersects",
            outFields="BASENAME,NAME,GEOID",
            f="geojson",
        )
        for f in data.get("features", []):
            if not intersects_aoi(f, aoi):
                continue
            props = f.get("properties") or {}
            features.append(
                _feature(f["geometry"], props.get("BASENAME") or props.get("NAME"),
                         admin, "US Census TIGER", url)
            )
    return features


def _write_feature_collection(features: list[dict], out: Path,
                              fetch_error: str | None = None) -> None:
    fc = {"type": "FeatureCollection", "features": features}
    if fetch_error:
        fc["fetch_error"] = fetch_error
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc))


def municipal_boundaries(event: dict) -> Path:
    """Write data/<case_id>/geojson/municipal_boundaries.geojson; return Path.

    <case_id> is the config.yaml case key ('rs-2024-05' -> 'rs_2024') — data
    folders are per case, not per event (ruling L4).
    """
    case = "_".join(event["id"].split("-")[:2])
    out = event_data_dir(case) / "geojson" / "municipal_boundaries.geojson"
    aoi = box(*event["aoi"]["bbox"])
    try:
        if event.get("country") == "BR":
            features = _fetch_ibge(aoi, uf="RS")
        elif event.get("country") == "US":
            features = _fetch_tigerweb(aoi, state_fips="53")
        else:
            raise ValueError(
                f"no municipal boundary source for {event.get('country')!r}"
            )
    except (OSError, ValueError, KeyError, RuntimeError, GEOSException) as exc:
        _write_feature_collection([], out, fetch_error=f"{type(exc).__name__}: {exc}")
        return out
    _write_feature_collection(features, out)
    return out
