// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { changeBlockedReason, changeComposite, type Raster } from "./changeComposite";

const raster = (w: number, h: number, gray: (i: number, j: number) => number, alpha = 255, bounds: [number, number, number, number] = [-58, -30, -57, -29]): Raster => {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const o = (j * w + i) * 4, g = gray(i, j);
    data[o] = data[o + 1] = data[o + 2] = g; data[o + 3] = alpha;
  }
  return { data, width: w, height: h, bounds };
};

test("new water → red, brighter → cyan, unchanged → grey; outside the swath stays transparent", () => {
  const before = raster(3, 1, (i) => [200, 80, 120][i]);
  const after = raster(3, 1, (i) => [20, 220, 120][i]);
  const out = changeComposite(before, after);
  expect([...out.slice(0, 4)]).toEqual([200, 20, 20, 255]);    // darker after: red
  expect([...out.slice(4, 8)]).toEqual([80, 220, 220, 255]);   // brighter after: cyan
  expect([...out.slice(8, 12)]).toEqual([120, 120, 120, 255]); // no change: grey
  const transparent = changeComposite(before, raster(3, 1, () => 50, 0));
  expect(transparent.every((v) => v === 0)).toBe(true);
});

test("different bounds: the before frame is resampled onto the after grid", () => {
  // Before covers the west half only (bright); after covers the whole box (dark).
  const before = raster(2, 2, () => 200, 255, [-58, -30, -57.5, -29]);
  const after = raster(4, 2, () => 30, 255, [-58, -30, -57, -29]);
  const out = changeComposite(before, after);
  expect(out[3]).toBe(255);  // west pixel: overlap → composite
  expect(out[0]).toBe(200);
  expect(out[3 * 4 + 3]).toBe(0); // east pixel: no before data → transparent
});

test("only same-track, same-pol NISAR pairs can show the change view", () => {
  expect(changeBlockedReason({ layer: "nisar", track: "010D", pol: "HH" }, { layer: "nisar", track: "010D", pol: "HH" })).toBeUndefined();
  expect(changeBlockedReason({ layer: "nisar", track: "010D" }, { layer: "nisar", track: "082D" })).toMatch(/same track/);
  expect(changeBlockedReason({ layer: "nisar", track: "010D", pol: "HH" }, { layer: "nisar", track: "010D", pol: "HV" })).toMatch(/polarization/);
  expect(changeBlockedReason({ layer: "flood" }, { layer: "nisar" })).toMatch(/two NISAR passes/);
});

test("fixed stretches: gray decoded to dB per frame, then one common range (Now HH -22..0 vs case -25..0)", async () => {
  const { changeComposite: cc, stretchOf } = await import("./changeComposite");
  // -10 dB in a -25..0 frame: gray = 1 + 15/25*254 = 153.4 → 153; in a -22..0 frame: 1 + 12/22*254 = 139.5 → 139.
  const before = raster(1, 1, () => 153); before.stretch = { min: -25, max: 0 };
  const after = raster(1, 1, () => 139); after.stretch = { min: -22, max: 0 };
  const out = cc(before, after);
  expect(Math.abs(out[0] - out[1])).toBeLessThanOrEqual(2); // same dB → same brightness → grey
  expect(stretchOf({ stretch_db: { db_min: -25, db_max: 0 } })).toEqual({ min: -25, max: 0 });
  expect(stretchOf({ legend: { unit: "dB", db_min: -30, db_max: -8 } })).toEqual({ min: -30, max: -8 });
  expect(stretchOf({ legend: { stops: [] } })).toBeUndefined();
});
