// Lucas: a RED outline around the footprint of every NISAR radar image, so
// the user sees exactly where the radar looked. The swath is a slanted
// band inside the frame's bounds (transparent outside it): the outline
// follows the opaque pixels — leftmost/rightmost opaque column on sampled
// rows — mapped back through Web Mercator (the PNGs are EPSG:3857).
export type Box = readonly [number, number, number, number]; // [w, s, e, n]
export const FOOTPRINT_COLOR = "#ff1f1f";
export const FOOTPRINT_LEGEND = "Red outline = NISAR radar footprint";

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const latOf = (y: number) => (360 / Math.PI) * Math.atan(Math.exp(y)) - 90;

export function boxPolygon(b: Box): GeoJSON.Polygon {
  return { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] };
}

// RGBA pixels (alpha at i*4+3) → the swath outline; the bounds rectangle
// when nothing is opaque or the image is fully opaque edge to edge.
export function footprintFromAlpha(rgba: ArrayLike<number>, width: number, height: number, b: Box, samples = 64): GeoJSON.Polygon {
  const span = (y: number): [number, number] | undefined => {
    let lo = -1, hi = -1;
    for (let x = 0; x < width; x++) if (rgba[(y * width + x) * 4 + 3] > 0) { lo = x; break; }
    if (lo < 0) return undefined;
    for (let x = width - 1; x >= lo; x--) if (rgba[(y * width + x) * 4 + 3] > 0) { hi = x; break; }
    return [lo, hi + 1];
  };
  const rows: number[] = [];
  const step = Math.max(1, Math.floor(height / samples));
  for (let y = 0; y < height; y += step) rows.push(y);
  if (rows[rows.length - 1] !== height - 1) rows.push(height - 1);
  const spans = rows.flatMap((y) => { const s = span(y); return s ? [{ y, s }] : []; });
  if (spans.length < 2) return boxPolygon(b);
  const y0 = mercY(b[3]), y1 = mercY(b[1]);
  const lon = (x: number) => b[0] + (x / width) * (b[2] - b[0]);
  const lat = (y: number) => latOf(y0 + (y / height) * (y1 - y0));
  // the first/last sampled rows mark the top/bottom edges of the band
  const top = spans[0], bot = spans[spans.length - 1];
  const left = spans.map(({ y, s }) => [lon(s[0]), lat(y === top.y ? y : y === bot.y ? y + 1 : y + 0.5)]);
  const right = [...spans].reverse().map(({ y, s }) => [lon(s[1]), lat(y === top.y ? y : y === bot.y ? y + 1 : y + 0.5)]);
  const ring = [...left, ...right];
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
}
