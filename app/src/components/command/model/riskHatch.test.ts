// Conductor (Lucas): risk as a yellow hatch on the Flood risk + NISAR views.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { HATCH_YELLOW, hatchClass, hatchRgba, hatchScale, onHatch } from "./riskHatch";

test("TPI class colours → only High and Critical are hatched", () => {
  expect(hatchClass(255, 255, 178, 255)).toBe(0); // Low
  expect(hatchClass(254, 204, 92, 255)).toBe(3); // Moderate: a sparse hatch (display only)
  expect(hatchClass(253, 141, 60, 255)).toBe(1); // High
  expect(hatchClass(189, 0, 38, 255)).toBe(2); // Critical
  expect(hatchClass(189, 0, 38, 0)).toBe(0); // transparent
});

test("High = thin 45° lines (1 px every 6 px); Critical = thin cross-hatch every 5 px", () => {
  const high = Array.from({ length: 24 }, (_, x) => onHatch(1, x, 0));
  expect(high.filter(Boolean).length).toBe(4); // 1 of every 6
  expect(onHatch(1, 0, 0)).toBe(true);
  expect(onHatch(1, 1, 0)).toBe(false);
  expect(onHatch(1, 5, 1)).toBe(true); // diagonal: x + y = 6
  const crit = Array.from({ length: 20 }, (_, x) => onHatch(2, x, 3)).filter(Boolean).length;
  expect(crit).toBe(8); // both diagonals, every 5 px: 4 + 4 in 20 px
  expect(onHatch(0, 0, 0)).toBe(false);
});

test("Moderate = sparse single diagonal (1 px every 10 px), fainter alpha", () => {
  expect(Array.from({ length: 20 }, (_, x) => onHatch(3, x, 0)).filter(Boolean).length).toBe(2);
  expect(onHatch(3, 9, 1)).toBe(true);
  const out = hatchRgba(new Uint8ClampedArray([254, 204, 92, 255]), 1, 1, 10);
  expect(out.data[3]).toBe(170); // pixel (0, 0) on a line, ~40 % with the 60 % layer
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
