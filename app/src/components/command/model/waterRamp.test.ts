// Lucas: 'Water in red' — darker radar = redder, on ONE image.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { redness, waterRamp } from "./waterRamp";

// gray for a dB value on the fixed −25..0 stretch: 1 + (dB+25)/25*254
const gray = (db: number) => Math.round(1 + ((db + 25) / 25) * 254);
const px = (...g: number[]) => new Uint8ClampedArray(g.flatMap((v) => [v, v, v, 255]));

test("redness: 0 at ≥ −17 dB, ramps to 1 at ≤ −22 dB (Lucas: start −17)", () => {
  expect(redness(-5)).toBe(0);
  expect(redness(-14)).toBe(0);
  expect(redness(-17)).toBe(0);
  expect(redness(-19.5)).toBeCloseTo(0.5);
  expect(redness(-22)).toBe(1);
  expect(redness(-30)).toBe(1);
});

test("waterRamp: bright stays grey, dark goes red, nodata stays transparent", () => {
  const src = px(gray(-8), gray(-19.5), gray(-24));
  const nod = new Uint8ClampedArray([0, 0, 0, 0]);
  const out = waterRamp(new Uint8ClampedArray([...src, ...nod]));
  expect([...out.slice(0, 4)]).toEqual([...src.slice(0, 4)]); // −8 dB: unchanged grey
  const mid = out.slice(4, 8);
  expect(mid[0]).toBeGreaterThan(mid[1]); // −19.5 dB: half way to red
  expect(mid[1]).toBeLessThan(gray(-19.5));
  expect([...out.slice(8, 12)]).toEqual([255, 31, 31, 255]); // −24 dB: full red
  expect(out[15]).toBe(0);
});

test("waterRamp: decodes with the frame's own stretch", () => {
  // −22..0 stretch: gray 1 = −22 dB → full red; a −25..0 decode would say −25 (also red),
  // gray 128 ≈ −11 dB on −22..0 (grey) vs −12.5 on −25..0 (grey) — pick one that differs:
  const g = 70; // −22..0 → −16.0 dB (grey, share 0); −25..0 → −18.2 dB (share .24)
  const a = waterRamp(px(g), { min: -22, max: 0 });
  const b = waterRamp(px(g));
  expect(a[1]).toBeGreaterThan(b[1]); // less red with the −22..0 decode
});
