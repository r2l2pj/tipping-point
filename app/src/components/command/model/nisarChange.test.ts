// @ts-expect-error -- bun:test resolves under `bun test`
import { describe, expect, test } from "bun:test";
import { CHANGE_CYAN, CHANGE_RED, deltaComposite, type Raster } from "./changeComposite";
import {
  changeSelection,
  defaultChangePair,
  nisarChangeAvailable,
  nisarChangeFrames,
  nisarChangeUnavailableReason,
  parseChangeLink,
  resolveChangePair,
} from "./nisarChange";
import { bundle, manifest } from "./fixtures";

// --- Δ classification (deltaComposite) --------------------------------------

const raster = (
  w: number,
  h: number,
  gray: (i: number, j: number) => number,
  alpha = 255,
  bounds: [number, number, number, number] = [-58, -30, -57, -29],
  stretch: { min: number; max: number } = { min: 0, max: 254 } // identity: gray − 1 = "dB" → exact Δ
): Raster => {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const o = (j * w + i) * 4, g = gray(i, j);
    data[o] = data[o + 1] = data[o + 2] = g; data[o + 3] = alpha;
  }
  return { data, width: w, height: h, bounds, stretch };
};

const px = (out: Uint8ClampedArray, i: number) => [...out.slice(i * 4, i * 4 + 4)];
const rgb = (hex: string) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];

describe("Δ classification", () => {
  test("Δ ≤ −3 dB → red, Δ ≥ +3 dB → cyan, |Δ| < 3 → transparent (exact edges)", () => {
    // Identity stretch: gray − 1 stands for dB. before = 109.
    const before = raster(5, 1, () => 110);
    const after = raster(5, 1, (i) => [104, 107, 110, 113, 116][i]); // Δ −6, −3, −1, +3, +6
    const out = deltaComposite(before, after);
    expect(px(out, 0)).toEqual([...rgb(CHANGE_RED), 255]);
    expect(px(out, 1)).toEqual([...rgb(CHANGE_RED), 255]); // Δ = −3 exactly is red
    expect(px(out, 2)).toEqual([0, 0, 0, 0]);             // |Δ| < 3 → transparent
    expect(px(out, 3)).toEqual([...rgb(CHANGE_CYAN), 255]); // Δ = +3 exactly is cyan
    expect(px(out, 4)).toEqual([...rgb(CHANGE_CYAN), 255]);
  });

  test("cyan toggle off leaves Δ ≥ +3 transparent; red stays", () => {
    const before = raster(2, 1, () => 110);
    const after = raster(2, 1, (i) => [104, 116][i]);
    const out = deltaComposite(before, after, false);
    expect(px(out, 0)).toEqual([...rgb(CHANGE_RED), 255]);
    expect(px(out, 1)).toEqual([0, 0, 0, 0]);
  });

  test("gray is decoded through each frame's own stretch (−25..0 vs −22..0)", () => {
    // −10 dB: gray = 1 + 15/25·254 ≈ 153 in a −25..0 frame; ≈139 in −22..0.
    // After −20 dB in −22..0: 1 + 2/22·254 ≈ 24. Δ ≈ −10 → red.
    const before = raster(1, 1, () => 153, 255, [-58, -30, -57, -29], { min: -25, max: 0 });
    const after = raster(1, 1, () => 24, 255, [-58, -30, -57, -29], { min: -22, max: 0 });
    expect(px(deltaComposite(before, after), 0)).toEqual([...rgb(CHANGE_RED), 255]);
    // Same dB on both → Δ ≈ 0 → transparent.
    const same = raster(1, 1, () => 139, 255, [-58, -30, -57, -29], { min: -22, max: 0 });
    expect(px(deltaComposite(before, same), 0)).toEqual([0, 0, 0, 0]);
  });

  test("outside the swath / no before data → transparent", () => {
    const before = raster(3, 1, () => 110);
    expect(deltaComposite(before, raster(3, 1, () => 50, 0)).every((v) => v === 0)).toBe(true);
    // Before covers the west half only: east after pixels have no before data.
    const half = raster(2, 2, () => 110, 255, [-58, -30, -57.5, -29]);
    const out = deltaComposite(half, raster(4, 2, () => 90, 255, [-58, -30, -57, -29]));
    expect(out[3]).toBe(255);        // west overlap → classified
    expect(out[3 * 4 + 3]).toBe(0);  // east: no before → transparent
  });
});

// --- the pair ---------------------------------------------------------------

const nframe = (date: string, over: Record<string, unknown> = {}) => ({
  id: `nisar_gcov_${date.replace(/-/g, "")}`,
  kind: "nisar_frame",
  date,
  png: `wa_2025/png/nisar_${date}.png`,
  bounds: [-122.6, 47, -121.5, 48.5],
  track: "172D",
  polarization: "HH",
  sensor: "NISAR (GCOV provisional)",
  stretch_db: { db_min: -25, db_max: 0 },
  ...over,
});

const stack = (...layers: Record<string, unknown>[]) => bundle({ manifest: manifest(layers) });
const DAYS = ["2025-12-04", "2025-12-16", "2025-12-28"];
const waStack = () => stack(...DAYS.map((d) => nframe(d)));
const reason = (s: ReturnType<typeof resolveChangePair>): string | undefined =>
  "reason" in s ? s.reason : undefined;

describe("default pair (Lucas: follows the timeline — previous pass → the pass on/before the day)", () => {
  test("after = newest pass on/before the day, before = the previous one on its track", () => {
    const d = defaultChangePair(waStack(), "2025-12-20");
    expect(d.before?.date).toBe("2025-12-04");
    expect(d.after?.date).toBe("2025-12-16");
    expect(d.pol).toBe("HH");
  });
  test("a pass ON the day is the 'after'; edges clamp to a real pair", () => {
    expect(defaultChangePair(waStack(), "2025-12-16").before?.date).toBe("2025-12-04");
    expect(defaultChangePair(waStack(), "2025-12-16").after?.date).toBe("2025-12-16");
    expect(defaultChangePair(waStack(), "2025-12-01").before?.date).toBe("2025-12-04"); // before all: first two
    expect(defaultChangePair(waStack(), "2025-12-01").after?.date).toBe("2025-12-16");
    expect(defaultChangePair(waStack(), "2026-01-15").after?.date).toBe("2025-12-28"); // after all: last two
    expect(defaultChangePair(waStack(), "2026-01-15").before?.date).toBe("2025-12-16");
    expect(defaultChangePair(waStack(), undefined).before?.date).toBe("2025-12-04"); // no day: oldest pair
  });
  test("HV is the default when HH does not pair up", () => {
    const hv = stack(nframe("2025-12-04", { polarization: "HV" }), nframe("2025-12-16", { polarization: "HV" }), nframe("2025-12-28"));
    const d = defaultChangePair(hv, "2025-12-20");
    expect(d.pol).toBe("HV");
    expect(d.after?.date).toBe("2025-12-16"); // Dec 16 is the first ≥ day with HV
  });
});

describe("resolveChangePair", () => {
  test("default selection resolves the day's pair", () => {
    const spec = resolveChangePair(waStack(), {}, "2025-12-20");
    expect(spec).toMatchObject({ label: "Dec 4, 2025 → Dec 16, 2025 · HH · track 172D" }); // follows the day: previous → current
  });
  test("explicit picks ('<date>|<track>'); a reversed pick is sorted", () => {
    const spec = resolveChangePair(waStack(), { before: "2025-12-28|172D", after: "2025-12-04|172D" });
    expect(spec).toMatchObject({ pair: { before: { date: "2025-12-04" }, after: { date: "2025-12-28" } } });
  });
  test("same pass twice, unknown day, track and pol mismatch, missing stretch each get a reason", () => {
    expect(reason(resolveChangePair(waStack(), { before: "2025-12-16|172D", after: "2025-12-16|172D" })))
      .toBe("Pick two different passes");
    expect(reason(resolveChangePair(waStack(), { before: "2025-12-10|172D" }))).toMatch(/No NISAR pass for Dec 10/);
    const twoTracks = stack(nframe("2025-12-04"), nframe("2025-12-16", { track: "010D" }));
    expect(reason(resolveChangePair(twoTracks, { before: "2025-12-04|172D", after: "2025-12-16|010D" }))).toMatch(/same track/);
    const pols = stack(nframe("2025-12-04"), nframe("2025-12-16", { polarization: "HV" }));
    expect(reason(resolveChangePair(pols, { before: "2025-12-04|172D", after: "2025-12-16|172D" }))).toMatch(/polarization/);
    const noStretch = stack(
      nframe("2025-12-04", { stretch_db: undefined, legend: undefined }),
      nframe("2025-12-16", { stretch_db: undefined, legend: undefined }));
    expect(reason(resolveChangePair(noStretch, {}))).toMatch(/stretch/);
    expect(reason(resolveChangePair(stack(nframe("2025-12-04")), {}))).toMatch(/two NISAR passes/);
  });
  test("availability: a pairable stack vs untestable manifests", () => {
    expect(nisarChangeAvailable(waStack())).toBe(true);
    const noStretch = stack(
      nframe("2025-12-04", { stretch_db: undefined, legend: undefined }),
      nframe("2025-12-16", { stretch_db: undefined, legend: undefined }));
    expect(nisarChangeAvailable(noStretch)).toBe(false);
    expect(nisarChangeUnavailableReason(noStretch)).toMatch(/stretch/);
    const twoTracks = stack(nframe("2025-12-04"), nframe("2025-12-16", { track: "010D" }));
    expect(nisarChangeAvailable(twoTracks)).toBe(false);
    expect(nisarChangeUnavailableReason(twoTracks)).toMatch(/track|polarization/);
    expect(nisarChangeUnavailableReason(stack(nframe("2025-12-04")))).toBeUndefined();
    // A legend-only stretch (db_min/db_max) counts too.
    const legend = stack(
      nframe("2025-12-04", { stretch_db: undefined, legend: { unit: "dB", db_min: -25, db_max: 0 } }),
      nframe("2025-12-16", { stretch_db: undefined, legend: { unit: "dB", db_min: -25, db_max: 0 } }));
    expect(nisarChangeAvailable(legend)).toBe(true);
  });
  test("frames flatten every track's passes with bounds + stretch", () => {
    const fs = nisarChangeFrames(waStack());
    expect(fs.map((f) => f.date)).toEqual(DAYS);
    expect(fs[0]).toMatchObject({ track: "172D", pol: "HH", bounds: [-122.6, 47, -121.5, 48.5], stretch: { min: -25, max: 0 } });
  });
});

describe("selection + deep link", () => {
  test("a bare date picks the default track's pass; '<date>|<track>' sticks", () => {
    const s = changeSelection(waStack(), { before: "2025-12-04" }, "2025-12-20");
    expect(s).toEqual({ before: "2025-12-04|172D", after: "2025-12-16|172D", pol: "HH" });
    const twoTracks = stack(nframe("2025-12-04"), nframe("2025-12-16", { track: "010D" }), nframe("2025-12-28"));
    const t = changeSelection(twoTracks, { before: "2025-12-16|010D" }, "2025-12-20");
    expect(t.before).toBe("2025-12-16|010D");
  });
  test("?nisarChange=before:after[:pol]", () => {
    expect(parseChangeLink("2025-12-04:2025-12-28:HH")).toEqual({ before: "2025-12-04", after: "2025-12-28", pol: "HH" });
    expect(parseChangeLink("2025-12-04:2025-12-28:hv")).toEqual({ before: "2025-12-04", after: "2025-12-28", pol: "HV" });
    expect(parseChangeLink("2025-12-04:2025-12-28")).toEqual({ before: "2025-12-04", after: "2025-12-28" });
    expect(parseChangeLink("2025-12-04:bad")).toBeUndefined();
    expect(parseChangeLink(undefined)).toBeUndefined();
    // A link pair resolves straight into frames.
    const spec = resolveChangePair(waStack(), parseChangeLink("2025-12-04:2025-12-28:HH")!);
    expect(spec).toMatchObject({ pair: { before: { date: "2025-12-04" }, after: { date: "2025-12-28" } } });
  });
});
