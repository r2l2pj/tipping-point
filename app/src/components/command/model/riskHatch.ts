// Conductor (Lucas, L61): on the Flood risk + NISAR views the RISK is drawn
// as a yellow HATCH so the NISAR image stays visible under it. Pure: the TPI
// class PNG (Low #ffffb2, Moderate #fecc5c, High #fd8d3c, Critical #bd0026)
// is re-rendered at `scale`× — transparent everywhere except High (diagonal
// 45° lines) and Critical (denser cross-hatch), both #facc15.
export const HATCH_YELLOW: readonly [number, number, number] = [250, 204, 21];
// Conductor (Lucas, 21:3x): THINNER lines so the NISAR red water shows
// through and matches — 1 px lines; High every 6 px, Critical a cross-hatch
// (both diagonals) every 5 px.
export const HATCH_HIGH = { spacing: 6, width: 1 } as const; // Lucas pre-close: every 6 px
export const HATCH_CRITICAL = { spacing: 5, width: 1 } as const; // both diagonals, every 5 px

// TPI class colours (places.ts CLASS_COLORS) → 0 none / 1 High / 2 Critical.
const PALETTE: readonly [number, number, number, 0 | 1 | 2][] = [
  [255, 255, 178, 0], // Low
  [254, 204, 92, 0], // Moderate
  [253, 141, 60, 1], // High
  [189, 0, 38, 2], // Critical
];
export function hatchClass(r: number, g: number, b: number, a: number): 0 | 1 | 2 {
  if (a < 128) return 0;
  let best: 0 | 1 | 2 = 0;
  let bd = Infinity;
  for (const [pr, pg, pb, c] of PALETTE) {
    const d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2;
    if (d < bd) { bd = d; best = c; }
  }
  return best;
}

// Is output pixel (x, y) on a hatch line for this class?
export function onHatch(cls: 0 | 1 | 2, x: number, y: number): boolean {
  if (cls === 1) return (x + y) % HATCH_HIGH.spacing < HATCH_HIGH.width;
  if (cls === 2) {
    const s = HATCH_CRITICAL.spacing, w = HATCH_CRITICAL.width;
    return (x + y) % s < w || (((x - y) % s) + s) % s < w;
  }
  return false;
}

// Output scale: fine lines on the small class PNGs (Uruguaiana 253 px), at
// most ~2048 px on the long side.
export const hatchScale = (w: number, h: number): number =>
  Math.max(1, Math.min(8, Math.floor(2048 / Math.max(1, w, h))));

export function hatchRgba(src: Uint8ClampedArray, w: number, h: number, scale = hatchScale(w, h)):
  { data: Uint8ClampedArray; width: number; height: number } {
  const cls = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) cls[i] = hatchClass(src[4 * i], src[4 * i + 1], src[4 * i + 2], src[4 * i + 3]);
  const W = w * scale, H = h * scale;
  const out = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) {
    const row = Math.floor(y / scale) * w;
    for (let x = 0; x < W; x++) {
      const c = cls[row + Math.floor(x / scale)] as 0 | 1 | 2;
      if (c && onHatch(c, x, y)) {
        const o = 4 * (y * W + x);
        out[o] = HATCH_YELLOW[0]; out[o + 1] = HATCH_YELLOW[1]; out[o + 2] = HATCH_YELLOW[2]; out[o + 3] = 255;
      }
    }
  }
  return { data: out, width: W, height: H };
}
