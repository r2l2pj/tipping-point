"use client";
// The NISAR swath outline of one frame (model/footprint), computed once per
// PNG in the browser and cached; the bounds rectangle until it is ready or
// when the pixels cannot be read.
import { useEffect, useState } from "react";
import { boxPolygon, footprintFromAlpha, type Box } from "./model/footprint";

const cache = new Map<string, Promise<GeoJSON.Polygon>>();
export function footprintOf(url: string, b: Box): Promise<GeoJSON.Polygon> {
  const k = `${url}|${b.join(",")}`;
  let p = cache.get(k);
  if (!p) {
    p = new Promise<GeoJSON.Polygon>((resolve) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        try {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth; c.height = img.naturalHeight;
          const ctx = c.getContext("2d", { willReadFrequently: true });
          if (!ctx) return resolve(boxPolygon(b));
          ctx.drawImage(img, 0, 0);
          resolve(footprintFromAlpha(ctx.getImageData(0, 0, c.width, c.height).data, c.width, c.height, b));
        } catch { resolve(boxPolygon(b)); }
      };
      img.onerror = () => resolve(boxPolygon(b));
      img.src = url;
    });
    cache.set(k, p);
  }
  return p;
}

export function useFootprint(url: string | undefined, b: Box | undefined): GeoJSON.Polygon | undefined {
  const [done, setDone] = useState<{ k: string; poly: GeoJSON.Polygon } | null>(null);
  const k = url && b ? `${url}|${b.join(",")}` : "";
  const [w, s, e, n] = b ?? [];
  useEffect(() => {
    if (!url || w === undefined || s === undefined || e === undefined || n === undefined) return;
    let live = true;
    footprintOf(url, [w, s, e, n]).then((poly) => { if (live) setDone({ k: `${url}|${[w, s, e, n].join(",")}`, poly }); });
    return () => { live = false; };
  }, [url, w, s, e, n]);
  if (!url || !b) return undefined;
  return done?.k === k ? done.poly : boxPolygon(b);
}

// Add/update the outline (dark halo + red line) on a map.
export function drawFootprint(map: import("maplibre-gl").Map, id: string, poly: GeoJSON.Polygon | undefined, before?: string) {
  try {
    const data: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: poly ? [{ type: "Feature", properties: {}, geometry: poly }] : [] };
    const src = map.getSource(id) as import("maplibre-gl").GeoJSONSource | undefined;
    if (src) { src.setData(data); return; }
    map.addSource(id, { type: "geojson", data });
    const b = before && map.getLayer(before) ? before : undefined;
    map.addLayer({ id: `${id}-halo`, type: "line", source: id, paint: { "line-color": "#141414", "line-width": 4, "line-opacity": 0.55 } }, b);
    map.addLayer({ id: `${id}-line`, type: "line", source: id, paint: { "line-color": "#ff1f1f", "line-width": 2 } }, b);
  } catch { /* the outline is a cue, never a crash */ }
}
