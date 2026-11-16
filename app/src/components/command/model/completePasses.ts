// L67 (Lucas): the "complete passes only" rule, as a leaf module (type-only
// imports) so radar.ts, steps.ts and nisarSeries.ts all share it without an
// import cycle. Written by lead-app (6962c77); moved here by lead-ux.
import type { CcBundle } from "./types";

type Rec = Record<string, unknown>;

// L67 (Lucas): only COMPLETE NISAR passes are shown anywhere (picker, Play,
// Compare, Change, river's-dance clicks): coverage_pct ≥ 90 % of the box.
// lead-analysis's Uruguaiana-box frames (kind 'nisar_frame_uy', or a
// nisar_frame with a 'box') replace the full-swath frames when published.
export const MIN_NISAR_COVERAGE = 90;
export const isBoxFrame = (l: Rec): boolean => l.kind === "nisar_frame_uy" || (l.kind === "nisar_frame" && typeof l.box === "string" && l.box !== "");
export const completeEnough = (l: Rec): boolean => typeof l.coverage_pct !== "number" || l.coverage_pct >= MIN_NISAR_COVERAGE;
// Conductor FINAL L67 box (lead-nisar-uru, verified on the published 100 m
// pixels): [-57.425, -29.97, -57.03, -29.20] around Uruguaiana. In it every
// 010D / 082D / 060A pass is 100 %, every 132A pass 0 %. The full-swath
// frames' coverage_pct is the share of the CASE box (010D 40 %), so until
// lead-analysis publishes box frames with their own coverage, Uruguaiana
// uses this per-track table.
export const URUGUAIANA_BOX: [number, number, number, number] = [-57.425, -29.97, -57.03, -29.2];
export const URUGUAIANA_BOX_COVERAGE: Record<string, number> = { "010D": 100, "082D": 100, "060A": 100, "132A": 0 };
export const isUruguaiana = (bundle: CcBundle): boolean =>
  /uruguai/i.test(String(bundle.manifest?.event ?? bundle.id));
export function usableNisarFrames(bundle: CcBundle): Rec[] {
  const all = (bundle.manifest?.layers ?? []).map((x) => x as unknown as Rec)
    .filter((l) => (l.kind === "nisar_frame" || l.kind === "nisar_frame_uy") && typeof l.date === "string" && typeof l.png === "string");
  const box = all.filter(isBoxFrame);
  if (box.length) return box.filter(completeEnough);
  if (isUruguaiana(bundle)) {
    // a frame without a track: unknown coverage, kept
    return all.filter((l) => typeof l.track !== "string" || (URUGUAIANA_BOX_COVERAGE[l.track] ?? 0) >= MIN_NISAR_COVERAGE);
  }
  return all.filter(completeEnough);
}

// The frame layers of HIDDEN passes: a pass (date + track) with no usable
// frame. Readers that list passes from other products (the series
// thumbnails) drop these. Once box frames replace the full-swath ones, a
// full-swath frame of a complete pass is NOT hidden (its pass is usable).
const passKey = (l: Rec) => `${String(l.date ?? "").slice(0, 10)}|${typeof l.track === "string" ? l.track : ""}`;
export function hiddenNisarFrames(bundle: CcBundle): Rec[] {
  const ok = new Set(usableNisarFrames(bundle).map(passKey));
  return (bundle.manifest?.layers ?? []).map((x) => x as unknown as Rec)
    .filter((l) => (l.kind === "nisar_frame" || l.kind === "nisar_frame_uy") && typeof l.date === "string" && !ok.has(passKey(l)));
}
