"use client";

// Locator: where the map view sits on the continent. A small Web Mercator
// window around the case, with land drawn from a bundled coarse outline
// (Natural Earth 1:110m land, public domain; no second map and no tile
// service), the case area as a thin outline and the current map view as a
// filled box that follows pan and zoom.
import { useEffect, useState } from "react";

type BBox = [number, number, number, number];

const WORLD = 2048; // Web Mercator world size in window pixels
const SPAN = 228; // window size in those pixels: about 40 degrees wide
const MAX_LAT = 85;
// Outer rings of the land polygons, each a flat [lon, lat, lon, lat, ...] list.
export type LandRings = number[][];
const LAND_URL = "/geo/land-110m.json";
let landOnce: Promise<LandRings> | null = null;
const loadLand = (): Promise<LandRings> =>
  (landOnce ??= fetch(LAND_URL).then((r) => (r.ok ? (r.json() as Promise<LandRings>) : [])).catch(() => []));

const worldX = (lon: number) => ((lon + 180) / 360) * WORLD;
const worldY = (lat: number) => {
  const r = (Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI) / 180;
  return (0.5 - Math.log(Math.tan(Math.PI / 4 + r / 2)) / (2 * Math.PI)) * WORLD;
};

export type LocatorBox = { x: number; y: number; width: number; height: number };

// Everything the drawing needs, in a 0-100 square. The window is centered on
// the case and does not move; boxes are clipped to it and never thinner than
// 3 units, so a street-level view still shows.
export function locatorGeometry(bbox: BBox, view?: BBox | null, land?: LandRings | null): { land: string; area: LocatorBox; view: LocatorBox } {
  const [w, s, e, n] = bbox;
  const left = worldX((w + e) / 2) - SPAN / 2;
  const top = Math.max(0, Math.min(WORLD - SPAN, (worldY(n) + worldY(s)) / 2 - SPAN / 2));
  const k = 100 / SPAN;
  const box = (b: BBox): LocatorBox => {
    const x0 = Math.max(0, Math.min(100, (worldX(b[0]) - left) * k));
    const x1 = Math.max(0, Math.min(100, (worldX(b[2]) - left) * k));
    const y0 = Math.max(0, Math.min(100, (worldY(b[3]) - top) * k));
    const y1 = Math.max(0, Math.min(100, (worldY(b[1]) - top) * k));
    const width = Math.max(3, x1 - x0);
    const height = Math.max(3, y1 - y0);
    return { x: Math.min(x0, 100 - width), y: Math.min(y0, 100 - height), width, height };
  };
  // Land as one SVG path. Only rings that touch the window are drawn; the
  // svg viewport clips the rest.
  let path = "";
  for (const ring of land ?? []) {
    let d = "";
    let inside = false;
    for (let i = 0; i + 1 < ring.length; i += 2) {
      const px = (worldX(ring[i]) - left) * k;
      const py = (worldY(ring[i + 1]) - top) * k;
      if (px > -100 && px < 200 && py > -100 && py < 200) inside = true;
      d += `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`;
    }
    if (inside) path += `${d}Z`;
  }
  return { land: path, area: box(bbox), view: box(view ?? bbox) };
}

const fmtLat = (v: number) => `${Math.abs(Math.round(v * 10) / 10)}°${v < 0 ? "S" : v > 0 ? "N" : ""}`;
const fmtLon = (v: number) => `${Math.abs(Math.round(v * 10) / 10)}°${v < 0 ? "W" : v > 0 ? "E" : ""}`;

export default function LocatorInset({ bbox, view, onRecenter, className = "" }: {
  bbox: BBox;
  // Current bounds of the main map [west, south, east, north]; the case area until the map reports them.
  view?: BBox | null;
  // Click: bring the main map back to the case area.
  onRecenter?: () => void;
  className?: string;
}) {
  const [land, setLand] = useState<LandRings | null>(null);
  useEffect(() => {
    let live = true;
    void loadLand().then((rings) => { if (live) setLand(rings); });
    return () => { live = false; };
  }, []);
  const g = locatorGeometry(bbox, view, land);
  const [w, s, e, n] = view ?? bbox;
  const label = `Locator: the map view spans ${fmtLat(s)} to ${fmtLat(n)}, ${fmtLon(w)} to ${fmtLon(e)}`;
  const frame = `block h-[104px] w-[104px] overflow-hidden rounded-[10px] border-[1.5px] border-white/75 bg-[#0f181a] p-0 ${className}`;
  const drawing = (
    <svg width="100%" height="100%" viewBox="0 0 100 100" aria-hidden="true">
      <rect x={0} y={0} width={100} height={100} fill="#12262b" />
      {g.land && <path d={g.land} fill="#3d4a4d" stroke="rgba(255,255,255,0.45)" strokeWidth={0.6} data-land />}
      <rect {...g.area} fill="none" stroke="rgba(255,255,255,0.6)" strokeWidth={1} strokeDasharray="3 2" />
      <rect {...g.view} fill="rgba(255,255,255,0.3)" stroke="#ffffff" strokeWidth={2} />
    </svg>
  );
  if (!onRecenter) {
    return <div role="img" aria-label={label} data-locator className={frame}>{drawing}</div>;
  }
  return (
    <button type="button" onClick={onRecenter} aria-label={`${label}. Click to return to the case area`}
      title="Where this view is. Click to return to the case area." data-locator className={`${frame} cursor-pointer`}>
      {drawing}
    </button>
  );
}
