// L67 (Lucas): only complete NISAR passes (coverage_pct ≥ 90 %); the
// Uruguaiana-box frames replace the full-swath ones; the camera fits the box.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { nisarTracks, usableNisarFrames } from "./radar";
import { radarFrame, radarPasses } from "./radarFrame";
import { riverWindow } from "./riverWindow";
import type { CcBundle } from "./types";

const f = (date: string, track: string, cov: number, extra: Record<string, unknown> = {}) => ({
  id: `nisar_hh_${date.replaceAll("-", "")}_${track}`, kind: "nisar_frame", date, track, polarization: "HH",
  png: `rs_uruguai_2026/png/nisar_hh_${date.replaceAll("-", "")}_${track}.png`, bounds: [-58, -30.6, -55.3, -28.2], coverage_pct: cov, ...extra,
});
const swath = { id: "wa_2025", mode: "replay", manifest: { event: "wa_2025", layers: [
  f("2026-09-30", "010D", 40), f("2026-10-05", "082D", 83), f("2026-10-12", "010D", 40), f("2026-10-17", "082D", 95),
] } } as unknown as CcBundle;

test("coverage < 90 % passes are hidden from the stack, the picker and Compare", () => {
  expect(usableNisarFrames(swath).map((l) => l.date)).toEqual(["2026-10-17"]);
  expect(radarPasses(swath).map((p) => p.date)).toEqual(["2026-10-17"]);
  expect(nisarTracks(swath).flatMap((t) => t.dates)).toEqual(["2026-10-17"]);
});

test("a picked hidden pass (river's-dance bar) → the nearest complete pass", () => {
  const p = radarPasses(swath);
  expect(radarFrame(p, "2026-10-12", { date: "2026-10-12", track: "010D", pol: "HH" })!.pass.date).toBe("2026-10-17");
});

test("Uruguaiana without box frames: the verified per-track table (010D/082D/060A 100 %, 132A 0 %) + the L67 camera box", () => {
  const uy0 = { id: "rs_uruguai_2026", mode: "replay", manifest: { event: "rs_uruguai_2026", layers: [
    f("2026-09-27", "132A", 60), f("2026-09-30", "010D", 40), f("2026-10-04", "060A", 68), f("2026-10-05", "082D", 83),
  ] } } as unknown as CcBundle;
  expect(radarPasses(uy0).map((p) => p.track)).toEqual(["010D", "060A", "082D"]);
  expect(radarPasses(uy0).map((p) => p.coverage)).toEqual([100, 100, 100]); // the box, not the case-box share
  expect(riverWindow(uy0)).toEqual([-57.425, -29.97, -57.03, -29.2]);
});

test("Uruguaiana-box frames replace the full-swath ones; the camera fits their box", () => {
  const box = [-57.3, -29.85, -56.9, -29.5];
  const uy = { ...swath, id: "rs_uruguai_2026", manifest: { event: "rs_uruguai_2026", layers: [
    ...(swath.manifest!.layers as unknown[]),
    f("2026-09-30", "010D", 100, { id: "nisar_uy_hh_20260930_010D", kind: "nisar_frame_uy", box: "uruguaiana", bounds: box }),
    f("2026-10-12", "010D", 100, { id: "nisar_uy_hh_20261012_010D", kind: "nisar_frame_uy", box: "uruguaiana", bounds: box }),
    f("2026-10-05", "082D", 62, { id: "nisar_uy_hh_20261005_082D", kind: "nisar_frame_uy", box: "uruguaiana", bounds: box }),
  ] } } as unknown as CcBundle;
  expect(radarPasses(uy).map((p) => `${p.date} ${p.track}`)).toEqual(["2026-09-30 010D", "2026-10-12 010D"]);
  expect(riverWindow(uy)).toEqual(box);
});

// lead-ux (L67 extend): one rule for the picker, the next-pass caption and
// the series thumbnails.
import { hiddenNisarFrames } from "./completePasses";
import { nextNisarPass } from "./steps";
const uy = { id: "rs_uruguai_2026", mode: "replay", manifest: { event: "rs_uruguai_2026", layers: [
  f("2026-09-30", "010D", 40), f("2026-10-03", "132A", 60), f("2026-10-12", "010D", 40),
] } } as unknown as CcBundle;

test("Uruguaiana: the next-pass caption uses the box table (010D kept at 40 % of the case box, 132A hidden)", () => {
  expect(nextNisarPass(uy, "2026-09-30")).toBe("2026-10-12");
});
test("hidden frames = passes with no usable frame (132A at Uruguaiana)", () => {
  expect(hiddenNisarFrames(uy).map((l) => l.track)).toEqual(["132A"]);
  expect(hiddenNisarFrames(swath).map((l) => l.date)).toEqual(["2026-09-30", "2026-10-05", "2026-10-12"]);
});

import { nisarSeriesView } from "./nisarSeries";
test("series thumbnails: a hidden pass (132A) has no thumbnail; complete passes keep theirs", () => {
  const dates = ["2026-09-30", "2026-10-03", "2026-10-12"];
  const b = { ...uy, nisarSeries: { dates, areas: { flood_candidates: { hh_db: [-10, -12, -18] } },
    frames: [
      { date: "2026-09-30", layer_id: "nisar_hh_20260930_010D", pol: "HH" },
      { date: "2026-10-03", layer_id: "nisar_hh_20261003_132A", pol: "HH" },
      { date: "2026-10-12", layer_id: "nisar_hh_20261012_010D", pol: "HH" },
    ] } } as unknown as CcBundle;
  expect(nisarSeriesView(b)!.frames.map((f) => f.label)).toEqual(["Sep 30, 2026", "Oct 12, 2026"]);
});
