// L67 (Lucas): the "complete passes only" rule, as a leaf module (type-only
// imports) so radar.ts, steps.ts and nisarSeries.ts all share it without an
// import cycle. Written by lead-app (6962c77); moved here by lead-ux.
import type { CcBundle } from "./types";

type Rec = Record<string, unknown>;

// L67 → override (Lucas: "better a cut square with MORE data"): the FULL
// 100 m frames (kind nisar_frame, the case box) are shown — lead-analysis's
// Uruguaiana-box frames (nisar_frame_uy) are NOT used. A pass covering less
// than 50 % of the case box is hidden everywhere (picker, Play, Compare,
// Change, next-pass caption, series thumbnails; a river's-dance click →
// the nearest shown pass): at Uruguaiana 010D (40 %) is hidden; 082D 83 %,
// 060A 68 %, 132A 60 % are shown.
export const MIN_NISAR_COVERAGE = 50;
export const isBoxFrame = (l: Rec): boolean => l.kind === "nisar_frame_uy" || (l.kind === "nisar_frame" && typeof l.box === "string" && l.box !== "");
export const completeEnough = (l: Rec): boolean => typeof l.coverage_pct !== "number" || l.coverage_pct >= MIN_NISAR_COVERAGE;
// Kept for reference (the cropped-box variant, not used any more).
export const URUGUAIANA_BOX: [number, number, number, number] = [-57.425, -29.97, -57.03, -29.2];
export const URUGUAIANA_BOX_COVERAGE: Record<string, number> = { "010D": 100, "082D": 100, "060A": 100, "132A": 0 };
export const isUruguaiana = (bundle: CcBundle): boolean =>
  /uruguai/i.test(String(bundle.manifest?.event ?? bundle.id));
export function usableNisarFrames(bundle: CcBundle): Rec[] {
  return (bundle.manifest?.layers ?? []).map((x) => x as unknown as Rec)
    .filter((l) => l.kind === "nisar_frame" && !isBoxFrame(l) && typeof l.date === "string" && typeof l.png === "string" && completeEnough(l));
}

// The frame layers of HIDDEN passes: a pass (date + track) with no usable
// frame. Readers that list passes from other products (the series
// thumbnails) drop these. Once box frames replace the full-swath ones, a
// full-swath frame of a complete pass is NOT hidden (its pass is usable).
const passKey = (l: Rec) => `${String(l.date ?? "").slice(0, 10)}|${typeof l.track === "string" ? l.track : ""}`;
export function hiddenNisarFrames(bundle: CcBundle): Rec[] {
  const ok = new Set(usableNisarFrames(bundle).map(passKey));
  return (bundle.manifest?.layers ?? []).map((x) => x as unknown as Rec)
    .filter((l) => l.kind === "nisar_frame" && !isBoxFrame(l) && typeof l.date === "string" && !ok.has(passKey(l)));
}
