from __future__ import annotations

import requests


def bbox_params(event: dict) -> dict:
    w, s, e, n = event["aoi"]["bbox"]
    return {
        "where": "1=1", "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
        "outSR": "4326", "f": "geojson",
    }


def query_features(url: str, params: dict, *, get=None) -> list[dict]:
    get = get or requests.get
    features = []
    while True:
        resp = get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"FeatureServer query failed: {payload['error']}")
        page = payload.get("features") or []
        features.extend(page)
        if not (payload.get("exceededTransferLimit") or
                (payload.get("properties") or {}).get("exceededTransferLimit")):
            return features
        if not page:
            raise RuntimeError("FeatureServer pagination returned an empty truncated page")
        params = {**params, "resultOffset": len(features)}
