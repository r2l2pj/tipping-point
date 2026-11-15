// Lucas: the red outline follows the opaque swath of the NISAR PNG.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { boxPolygon, footprintFromAlpha } from "./footprint";

const B = [-58, -30.6, -56.676, -28.2] as const;
// 20×20 image: an opaque band whose left/right edges slant (x from y/2 to y/2+8).
function band(): Uint8ClampedArray {
  const w = 20, h = 20, d = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = Math.floor(y / 2); x < Math.floor(y / 2) + 8; x++) d[(y * w + x) * 4 + 3] = 255;
  return d;
}

test("outline follows the slanted swath (not the bounds rectangle), inside the bounds", () => {
  const p = footprintFromAlpha(band(), 20, 20, B, 20);
  const ring = p.coordinates[0];
  expect(ring[0]).toEqual(ring[ring.length - 1]);
  const lons = ring.map((c) => c[0]), lats = ring.map((c) => c[1]);
  expect(Math.min(...lons)).toBeCloseTo(-58, 6); // the top row starts at x = 0
  expect(Math.max(...lons)).toBeLessThan(-56.676); // never reaches the east edge of the bounds
  expect(Math.max(...lats)).toBeCloseTo(-28.2, 6);
  expect(Math.min(...lats)).toBeCloseTo(-30.6, 6);
  expect(p).not.toEqual(boxPolygon(B));
  // the top-left corner is west of the bottom-left corner (the band slants east going south)
  const topLeft = ring[0], bottomLeft = ring[ring.length / 2 - 1 | 0];
  expect(topLeft[0]).toBeLessThan(bottomLeft[0]);
});

test("nothing opaque → the bounds rectangle", () => {
  expect(footprintFromAlpha(new Uint8ClampedArray(10 * 10 * 4), 10, 10, B)).toEqual(boxPolygon(B));
});
