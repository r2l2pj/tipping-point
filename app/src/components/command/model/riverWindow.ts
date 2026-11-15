// Lucas: Jhonata's strip shows FULL squares — his window is the 30 × 30 km
// box around Uruguaiana (river_dance.json box_utm21s), inside every NISAR
// track's swath; the case box is ~10× wider and 010D (east edge ≈ −56.7°)
// cannot fill it. The NISAR views of Uruguaiana fit the map to this window.
import type { CcBundle } from "./types";

export type LonLatBox = [number, number, number, number]; // [w, s, e, n]

// Inverse transverse Mercator (UTM, WGS84; Snyder 1987 series) — metres to
// degrees. Accurate to well under a metre inside a zone.
export function utmToLonLat(easting: number, northing: number, zone: number, south: boolean): [number, number] {
  const a = 6378137, f = 1 / 298.257223563, k0 = 0.9996;
  const e2 = f * (2 - f), ep2 = e2 / (1 - e2);
  const x = easting - 500000, y = south ? northing - 10000000 : northing;
  const m = y / k0;
  const mu = m / (a * (1 - e2 / 4 - (3 * e2 * e2) / 64 - (5 * e2 ** 3) / 256));
  const e1 = (1 - Math.sqrt(1 - e2)) / (1 + Math.sqrt(1 - e2));
  const phi1 = mu + ((3 * e1) / 2 - (27 * e1 ** 3) / 32) * Math.sin(2 * mu)
    + ((21 * e1 * e1) / 16 - (55 * e1 ** 4) / 32) * Math.sin(4 * mu)
    + ((151 * e1 ** 3) / 96) * Math.sin(6 * mu) + ((1097 * e1 ** 4) / 512) * Math.sin(8 * mu);
  const s = Math.sin(phi1), c = Math.cos(phi1), t = Math.tan(phi1);
  const n1 = a / Math.sqrt(1 - e2 * s * s);
  const t1 = t * t, c1 = ep2 * c * c;
  const r1 = (a * (1 - e2)) / (1 - e2 * s * s) ** 1.5;
  const d = x / (n1 * k0);
  const lat = phi1 - ((n1 * t) / r1) * (d * d / 2 - ((5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d ** 4) / 24
    + ((61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d ** 6) / 720);
  const lon = (d - ((1 + 2 * t1 + c1) * d ** 3) / 6 + ((5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d ** 5) / 120) / c;
  const lon0 = ((zone - 1) * 6 - 180 + 3) * (Math.PI / 180);
  return [(lon0 + lon) * (180 / Math.PI), lat * (180 / Math.PI)];
}

// UTM box [e0, n0, e1, n1] → lon/lat box (all four corners: the box is not
// axis-aligned in degrees).
export function utmBoxToLonLat(box: readonly number[], zone: number, south: boolean): LonLatBox {
  const [e0, n0, e1, n1] = box;
  const pts = [[e0, n0], [e0, n1], [e1, n0], [e1, n1]].map(([e, n]) => utmToLonLat(e, n, zone, south));
  return [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
}

// Jhonata's window (UTM 21S 476000,6700000 → 506000,6730000) when the data
// does not carry it.
export const URUGUAIANA_RIVER_BOX_UTM21S = [476000, 6700000, 506000, 6730000] as const;

export function riverWindow(bundle: CcBundle): LonLatBox | undefined {
  const box = bundle.riverDance?.boxUtm21s;
  if (box && box.length === 4) return utmBoxToLonLat(box, 21, true);
  const id = (bundle.manifest?.event ?? bundle.id) as string;
  return /uruguai/i.test(id) ? utmBoxToLonLat(URUGUAIANA_RIVER_BOX_UTM21S, 21, true) : undefined;
}

// The view is "zoomed out" past the window when it is more than 1.5× wider
// or taller — then a partial pass gets its swath-edge note.
export function zoomedOut(view: LonLatBox | undefined, win: LonLatBox): boolean {
  if (!view) return false;
  return (view[2] - view[0]) > 1.5 * (win[2] - win[0]) || (view[3] - view[1]) > 1.5 * (win[3] - win[1]);
}
