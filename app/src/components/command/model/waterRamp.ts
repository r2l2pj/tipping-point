// Lucas (URGENT, keep it simple): "Water in red" — a colour ramp on ONE
// NISAR radar image. Each pixel's gray is decoded to dB (the frame's fixed
// stretch); the darker, the redder: dB ≥ −17 keeps the grey radar, −17..−22
// ramps grey → #ff1f1f, ≤ −22 is full red. No second date, no mask.
import type { Stretch } from "./changeComposite";

export const WATER_RED_LEGEND = "Red = very dark radar = smooth surface, likely water";
export const RAMP_START_DB = -17;
export const RAMP_FULL_DB = -22;
// Lead-analysis contract: every NISAR frame is rendered on ONE fixed
// −25..0 dB stretch. A frame that does not declare it is decoded with it too
// (the legend then says "approximate").
export const DEFAULT_STRETCH: Stretch = { min: -25, max: 0 };
const RED = [255, 31, 31] as const;

const toDb = (g: number, s: Stretch) => s.min + ((g - 1) / 254) * (s.max - s.min);

// Share of red (0..1) for a dB value.
export const redness = (db: number): number =>
  Math.min(1, Math.max(0, (RAMP_START_DB - db) / (RAMP_START_DB - RAMP_FULL_DB)));

// RGBA in → RGBA out (gray PNGs decode to r = g = b). Transparent pixels
// (nodata) stay transparent.
export function waterRamp(src: Uint8ClampedArray, stretch: Stretch = DEFAULT_STRETCH): Uint8ClampedArray<ArrayBuffer> {
  const out = new Uint8ClampedArray(src.length);
  for (let i = 0; i < src.length; i += 4) {
    const a = src[i + 3];
    const g = src[i];
    out[i + 3] = a;
    if (a === 0 || g === 0) { out[i] = src[i]; out[i + 1] = src[i + 1]; out[i + 2] = src[i + 2]; continue; }
    const t = redness(toDb(g, stretch));
    out[i] = Math.round(g + (RED[0] - g) * t);
    out[i + 1] = Math.round(src[i + 1] + (RED[1] - src[i + 1]) * t);
    out[i + 2] = Math.round(src[i + 2] + (RED[2] - src[i + 2]) * t);
  }
  return out;
}
