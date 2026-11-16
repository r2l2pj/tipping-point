// L67 → override (Lucas: "better a cut square with MORE data"): the full
// 100 m frames, hidden below 50 % of the case box; box frames ignored; the
// camera stays on the case box.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { nisarTracks, usableNisarFrames } from "./radar";
import { passLabel, radarFrame, radarPasses } from "./radarFrame";
import { riverWindow } from "./riverWindow";
import { hiddenNisarFrames } from "./completePasses";
import { nextNisarPass } from "./steps";
import { nisarSeriesView } from "./nisarSeries";
import type { CcBundle } from "./types";

const f = (date: string, track: string, cov: number, extra: Record<string, unknown> = {}) => ({
  id: `nisar_hh_${date.replaceAll("-", "")}_${track}`, kind: "nisar_frame", date, track, polarization: "HH",
  png: `rs_uruguai_2026/png/nisar_hh_${date.replaceAll("-", "")}_${track}.png`, bounds: [-58, -30.6, -55.3, -28.2], coverage_pct: cov, ...extra,
});
// Uruguaiana case-box coverage: 010D 40 %, 082D 83 %, 060A 68 %, 132A 60 %.
const uy = { id: "rs_uruguai_2026", mode: "replay", manifest: { event: "rs_uruguai_2026", layers: [
  f("2026-09-30", "010D", 40), f("2026-10-04", "060A", 68), f("2026-10-05", "082D", 83), f("2026-10-09", "132A", 60), f("2026-10-12", "010D", 40),
  f("2026-10-12", "010D", 100, { id: "nisar_uy_hh_20261012_010D", kind: "nisar_frame_uy", box: "uruguaiana", bounds: [-57.425, -29.97, -57.03, -29.2] }),
] } } as unknown as CcBundle;

test("passes < 50 % of the case box are hidden (010D); box frames are not used", () => {
  expect(usableNisarFrames(uy).map((l) => `${l.date} ${l.track}`)).toEqual(["2026-10-04 060A", "2026-10-05 082D", "2026-10-09 132A"]);
  expect(radarPasses(uy).map((p) => p.track)).toEqual(["060A", "082D", "132A"]);
  expect(nisarTracks(uy).flatMap((t) => t.dates).sort()).toEqual(["2026-10-04", "2026-10-05", "2026-10-09"]);
});

test("labels show each pass's own case-box coverage", () => {
  expect(radarPasses(uy).map(passLabel)).toEqual(["Oct 4, 2026 · 060A · 68 % of area", "Oct 5, 2026 · 082D · 83 % of area", "Oct 9, 2026 · 132A · 60 % of area"]);
});

test("landing Oct 12 → the newest shown pass ≤ Oct 12 (Oct 9 132A); a hidden picked pass → the nearest shown one", () => {
  const p = radarPasses(uy);
  expect(radarFrame(p, "2026-10-12")!.pass.date).toBe("2026-10-09");
  expect(radarFrame(p, "2026-10-12", { date: "2026-10-12", track: "010D", pol: "HH" })!.pass.date).toBe("2026-10-09");
});

test("the camera stays on the case box (no small-box fit)", () => {
  expect(riverWindow(uy)).toBeUndefined();
});

test("next-pass caption and hidden frames follow the same rule", () => {
  expect(nextNisarPass(uy, "2026-09-30")).toBe("2026-10-04");
  expect(hiddenNisarFrames(uy).map((l) => `${l.date} ${l.track}`)).toEqual(["2026-09-30 010D", "2026-10-12 010D"]);
});

test("series thumbnails: a hidden pass (010D) has no thumbnail; shown passes keep theirs", () => {
  const dates = ["2026-09-30", "2026-10-05", "2026-10-09"];
  const b = { ...uy, nisarSeries: { dates, areas: { flood_candidates: { hh_db: [-10, -12, -18] } },
    frames: [
      { date: "2026-09-30", layer_id: "nisar_hh_20260930_010D", pol: "HH" },
      { date: "2026-10-05", layer_id: "nisar_hh_20261005_082D", pol: "HH" },
      { date: "2026-10-09", layer_id: "nisar_hh_20261009_132A", pol: "HH" },
    ] } } as unknown as CcBundle;
  expect(nisarSeriesView(b)!.frames.map((x) => x.label)).toEqual(["Oct 5, 2026", "Oct 9, 2026"]);
});
