// t1b8 contract: Uruguaiana NISAR mosaics (kind nisar_mosaic).
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { mosaicChip, mosaicFor, nisarMosaics } from "./nisarMosaic";
import type { CcBundle } from "./types";

const BOX = [-58, -30.6, -55.3, -28.2];
const mos = (ref: string, win: [string, string], passes: [string, string][], pol = "HH") => ({
  id: `nisar_mosaic_${pol.toLowerCase()}_${ref.replaceAll("-", "")}`, kind: "nisar_mosaic", date: ref, window: win, polarization: pol,
  passes: passes.map(([date, track]) => ({ date, track, share_pct: 25 })), png: `rs_uruguai_2026/png/nisar_mosaic_${pol.toLowerCase()}_${ref.replaceAll("-", "")}_abcd1234.png`,
  bounds: BOX, stretch_db: { db_min: -25, db_max: 0 }, coverage_pct: 97, footprints: `rs_uruguai_2026/geojson/nisar_mosaic_footprints_${ref.replaceAll("-", "")}.geojson`,
});
const uy = { id: "rs_uruguai_2026", mode: "replay", manifest: { event: "rs_uruguai_2026", layers: [
  mos("2026-09-18", ["2026-09-18", "2026-09-23"], [["2026-09-18", "010D"], ["2026-09-22", "060A"], ["2026-09-23", "082D"]]),
  mos("2026-10-12", ["2026-10-09", "2026-10-17"], [["2026-10-09", "132A"], ["2026-10-12", "010D"], ["2026-10-16", "060A"], ["2026-10-17", "082D"]]),
  mos("2026-10-12", ["2026-10-09", "2026-10-17"], [["2026-10-09", "132A"], ["2026-10-12", "010D"]], "HV"),
  mos("2026-09-30", ["2026-09-27", "2026-10-05"], [["2026-09-30", "010D"], ["2026-10-05", "082D"]]),
] } } as unknown as CcBundle;

test("parse: windows, passes, stretch, coverage, footprints", () => {
  const m = nisarMosaics(uy);
  expect(m).toHaveLength(4);
  const oct = m.find((x) => x.ref === "2026-10-12" && x.pol === "HH")!;
  expect([oct.start, oct.end]).toEqual(["2026-10-09", "2026-10-17"]);
  expect(oct.stretch).toEqual({ min: -25, max: 0 });
  expect(oct.coverage).toBe(97);
  expect(oct.footprintsUrl).toContain("nisar_mosaic_footprints_20261012.geojson");
});

test("mosaicFor: the window containing the day, else the latest window started before; HV when asked", () => {
  const m = nisarMosaics(uy);
  expect(mosaicFor(m, "2026-10-14")!.ref).toBe("2026-10-12");
  expect(mosaicFor(m, "2026-10-07")!.ref).toBe("2026-09-30"); // between windows: the latest started
  expect(mosaicFor(m, "2026-09-10")).toBeUndefined();
  expect(mosaicFor(m, "2026-10-14", "HV")!.pol).toBe("HV");
  expect(mosaicFor(m, "2026-09-20", "HV")!.pol).toBe("HH"); // Sep 18–23 has no HV
});

test("a pass with a 0 % share is not named", () => {
  const z = { id: "x", manifest: { event: "rs_uruguai_2026", layers: [{ ...mos("2026-10-12", ["2026-10-09", "2026-10-17"], [["2026-10-12", "010D"]]),
    passes: [{ date: "2026-10-12", track: "010D", share_pct: 40.2 }, { date: "2026-10-17", track: "082D", share_pct: 0 }] }] } } as unknown as CcBundle;
  expect(mosaicChip(nisarMosaics(z)[0])).toBe("NISAR mosaic Oct 9–17 · 010D");
});

test("chip: 'NISAR mosaic Oct 9–17 · 010D + 060A + 082D + 132A'", () => {
  const m = nisarMosaics(uy);
  expect(mosaicChip(mosaicFor(m, "2026-10-12")!)).toBe("NISAR mosaic Oct 9–17 · 010D + 060A + 082D + 132A");
  expect(mosaicChip(mosaicFor(m, "2026-10-01")!)).toBe("NISAR mosaic Sep 27–Oct 5 · 010D + 082D");
});
