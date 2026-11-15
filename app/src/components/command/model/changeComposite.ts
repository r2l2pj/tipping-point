// Lucas: the classic SAR flood change composite, built in the browser for
// ANY NISAR pair the user picks. R = before HH, G = after HH, B = after HH
// (both frames share lead-analysis's linear dB→gray stretch), so:
//   dark after a bright before (new open water) → RED
//   brighter after (structures, double bounce)   → CYAN
//   unchanged                                    → GREY
// The before frame is resampled onto the after frame's pixel grid (both
// PNGs are Web-Mercator images with lon/lat bounds), so bounds may differ.
export type BBox = [number, number, number, number];

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));

// stretch: the frame's fixed linear dB stretch (lead-analysis contract:
// gray = 1 + (dB − db_min)/(db_max − db_min)·254, 0 = no data). Without it
// the gray levels are a per-image stretch — usable only as a preview.
export interface Stretch { min: number; max: number }
export interface Raster { data: Uint8ClampedArray; width: number; height: number; bounds: BBox; stretch?: Stretch }

const toDb = (g: number, s: Stretch) => s.min + ((g - 1) / 254) * (s.max - s.min);

// Pixels with alpha below this are outside the swath (transparent).
const MIN_ALPHA = 16;

export function changeComposite(before: Raster, after: Raster): Uint8ClampedArray<ArrayBuffer> {
  const { width: aW, height: aH, bounds: aB } = after;
  // Both frames carry a fixed stretch: decode gray → dB and put both on one
  // common display range, so the same dB gives the same brightness.
  const both = before.stretch && after.stretch;
  const lo = both ? Math.min(before.stretch!.min, after.stretch!.min) : 0;
  const hi = both ? Math.max(before.stretch!.max, after.stretch!.max) : 0;
  const disp = (g: number, s?: Stretch) => (both && s ? Math.round(((toDb(g, s) - lo) / (hi - lo)) * 255) : g);
  const out = new Uint8ClampedArray(aW * aH * 4);
  const aN = mercY(aB[3]), aS = mercY(aB[1]);
  const bN = mercY(before.bounds[3]), bS = mercY(before.bounds[1]);
  const [bw, , be] = before.bounds;
  for (let j = 0; j < aH; j++) {
    const y = aN - ((j + 0.5) / aH) * (aN - aS);
    const bj = Math.floor(((bN - y) / (bN - bS)) * before.height);
    for (let i = 0; i < aW; i++) {
      const o = (j * aW + i) * 4;
      const aAlpha = after.data[o + 3];
      if (aAlpha < MIN_ALPHA) continue;
      const lon = aB[0] + ((i + 0.5) / aW) * (aB[2] - aB[0]);
      const bi = Math.floor(((lon - bw) / (be - bw)) * before.width);
      if (bi < 0 || bj < 0 || bi >= before.width || bj >= before.height) continue;
      const p = (bj * before.width + bi) * 4;
      if (before.data[p + 3] < MIN_ALPHA) continue;
      const bv = disp(before.data[p], before.stretch); // gray: R = G = B
      const av = disp(after.data[o], after.stretch);
      out[o] = bv;
      out[o + 1] = av;
      out[o + 2] = av;
      out[o + 3] = 255;
    }
  }
  return out;
}

export const CHANGE_LEGEND = "Red: radar got darker = likely new water · Cyan: got brighter · Grey: no change";

// Only a same-track, same-polarization pair is a valid change composite.
export function changeBlockedReason(
  l: { layer: string; track?: string; pol?: string },
  r: { layer: string; track?: string; pol?: string }
): string | undefined {
  if (l.layer !== "nisar" || r.layer !== "nisar") return "Change view needs two NISAR passes";
  if ((l.track ?? "") !== (r.track ?? "")) return "Change view needs two passes of the same track (viewing angle differs)";
  if ((l.pol ?? "HH") !== (r.pol ?? "HH")) return "Change view needs the same polarization on both sides";
  return undefined;
}

// A frame's fixed stretch from its manifest entry (stretch_db, else the
// legend's db_min/db_max); undefined = per-image stretch (preview only).
export function stretchOf(layer: Record<string, unknown> | undefined): Stretch | undefined {
  const pick = (o: unknown) => {
    const r = o as { db_min?: unknown; db_max?: unknown } | undefined;
    return r && typeof r.db_min === "number" && typeof r.db_max === "number" && r.db_max > r.db_min ? { min: r.db_min, max: r.db_max } : undefined;
  };
  return pick(layer?.stretch_db) ?? pick(layer?.legend);
}
