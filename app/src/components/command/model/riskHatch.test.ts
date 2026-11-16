// Conductor (Lucas): risk as a yellow hatch on the Flood risk + NISAR views.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { HATCH_YELLOW, hatchClass, hatchRgba, hatchScale, onHatch } from "./riskHatch";

test("TPI class colours → only High and Critical are hatched", () => {
  expect(hatchClass(255, 255, 178, 255)).toBe(0); // Low
  expect(hatchClass(254, 204, 92, 255)).toBe(0); // Moderate
  expect(hatchClass(253, 141, 60, 255)).toBe(1); // High
  expect(hatchClass(189, 0, 38, 255)).toBe(2); // Critical
  expect(hatchClass(189, 0, 38, 0)).toBe(0); // transparent
});

test("High = thin 45° lines (1 px every 10 px); Critical = the same thin lines on both diagonals", () => {
  const high = Array.from({ length: 20 }, (_, x) => onHatch(1, x, 0));
  expect(high.filter(Boolean).length).toBe(2); // 1 of every 10
  expect(onHatch(1, 0, 0)).toBe(true);
  expect(onHatch(1, 1, 0)).toBe(false);
  expect(onHatch(1, 9, 1)).toBe(true); // diagonal: x + y = 10
  const crit = Array.from({ length: 20 }, (_, x) => onHatch(2, x, 3)).filter(Boolean).length;
  const highRow = Array.from({ length: 20 }, (_, x) => onHatch(1, x, 3)).filter(Boolean).length;
  expect(crit).toBe(2 * highRow); // a cross-hatch: both diagonals
  expect(onHatch(0, 0, 0)).toBe(false);
});

test("hatchRgba: transparent except yellow lines on High/Critical pixels, upscaled", () => {
  // 2×1 source: High | Low
  const src = new Uint8ClampedArray([253, 141, 60, 255, 255, 255, 178, 255]);
  const out = hatchRgba(src, 2, 1, 4);
  expect(out.width).toBe(8);
  expect(out.height).toBe(4);
  const px = (x: number, y: number) => Array.from(out.data.slice(4 * (y * 8 + x), 4 * (y * 8 + x) + 4));
  expect(px(0, 0)).toEqual([...HATCH_YELLOW, 255]); // High, on a line
  expect(px(3, 0)[3]).toBe(0); // High, between lines
  for (let x = 4; x < 8; x++) for (let y = 0; y < 4; y++) expect(px(x, y)[3]).toBe(0); // Low → nothing
  expect(hatchScale(253, 258)).toBe(7);
  expect(hatchScale(4000, 100)).toBe(1);
});
