// Lucas (NISAR frames 'stretched to fit'): every compare image sits on ITS
// OWN frame bounds; pair.bounds (e.g. the Uruguaiana river window) is the
// camera only. Asset URLs carry the manifest version.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { boundsToImageCoordinates } from "@/lib/tpi";
import { frameCoordinates } from "../CompareOverlay";
import { otherGeometry } from "./radar";

const RIVER_WIN = [-57.2484, -29.8305, -56.9379, -29.5595] as const;

test("two frames with different bounds + a river-window pair → each image on its own bounds", () => {
  const pair = {
    bounds: RIVER_WIN,
    before: { url: "a.png", bounds: [-58, -30.60148, -56.676233, -28.2] }, // 010D full swath
    after: { url: "b.png", bounds: [-57.803, -30.601183, -55.297693, -28.2] }, // 082D
  };
  expect(frameCoordinates(pair.before)).toEqual(boundsToImageCoordinates([-58, -30.60148, -56.676233, -28.2]));
  expect(frameCoordinates(pair.after)).toEqual(boundsToImageCoordinates([-57.803, -30.601183, -55.297693, -28.2]));
  expect(frameCoordinates(pair.before)).not.toEqual(boundsToImageCoordinates([...RIVER_WIN]));
  // swapped in the same source: the coordinates change with the frame
  expect(frameCoordinates(pair.after)).not.toEqual(frameCoordinates(pair.before));
});

test("a frame without bounds is not drawn (never stretched into the camera box)", () => {
  expect(frameCoordinates({ bounds: undefined })).toBeUndefined();
  expect(frameCoordinates({ bounds: [1, 2, 3] })).toBeUndefined();
});

test("versioned asset URLs keep the track-suffix detection", () => {
  expect(otherGeometry({ png: "https://x/rs/png/nisar_hh_20261005_082D.png?v=2026-11-15T23%3A21%3A52Z" })).toBe(true);
  expect(otherGeometry({ png: "https://x/rs/png/nisar_hh_20261012.png?v=1" })).toBe(false);
});

test("compare camera = the frames' footprint: intersection, or union when they barely overlap", async () => {
  const { footprintBounds } = await import("../MapStage");
  // 010D and 082D Uruguaiana grids overlap widely → intersection
  expect(footprintBounds([-58, -30.6, -56.676, -28.2], [-57.803, -30.6, -55.298, -28.2])).toEqual([-57.803, -30.6, -56.676, -28.2]);
  // barely overlapping → union
  expect(footprintBounds([0, 0, 1, 1], [0.9, 0.9, 2, 2])).toEqual([0, 0, 2, 2]);
  expect(footprintBounds(undefined, [0, 0, 1, 1])).toBeUndefined();
});
