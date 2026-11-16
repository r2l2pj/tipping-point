// Lucas: Jhonata's 30 × 30 km river window (UTM 21S) → lon/lat.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { riverWindow, utmBoxToLonLat, utmToLonLat, zoomedOut } from "./riverWindow";
import { swathEdgeNote } from "./radarFrame";
import type { CcBundle } from "./types";

test("UTM 21S → lon/lat: the central meridian and the river box corners", () => {
  const [lon0] = utmToLonLat(500000, 7000000, 21, true);
  expect(lon0).toBeCloseTo(-57, 9);
  const w = utmBoxToLonLat([476000, 6700000, 506000, 6730000], 21, true);
  expect(w[0]).toBeCloseTo(-57.2484, 3);
  expect(w[1]).toBeCloseTo(-29.8305, 3);
  expect(w[2]).toBeCloseTo(-56.9379, 3);
  expect(w[3]).toBeCloseTo(-29.5595, 3);
});

test("riverWindow: the conductor's FINAL L67 Uruguaiana box (no box frames yet), else none", () => {
  const uy = { id: "rs_uruguai_2026", manifest: { event: "rs_uruguai_2026", layers: [] } } as unknown as CcBundle;
  expect(riverWindow(uy)).toBeUndefined(); // L67 override: the camera stays on the case box
  const wa = { id: "wa_2025", manifest: { event: "wa_2025", layers: [] } } as unknown as CcBundle;
  expect(riverWindow(wa)).toBeUndefined();
});

test("zoomedOut + swath-edge note: only a partial pass, side from its bounds", () => {
  const win: [number, number, number, number] = [-57.25, -29.83, -56.94, -29.56];
  expect(zoomedOut(win, win)).toBe(false);
  expect(zoomedOut([-58, -30.2, -56, -29], win)).toBe(true);
  const pass = { date: "2026-09-18", track: "010D", frames: {}, sensor: "NISAR radar" };
  expect(swathEdgeNote(pass, [-57.25, -29.85, -56.7, -29.05], [-58, -30, -56, -29])).toBe("swath edge — NISAR did not image the east part on this pass");
  expect(swathEdgeNote({ ...pass, coverage: 95 }, [-57.25, -29.85, -56.7, -29.05], [-58, -30, -56, -29])).toBeUndefined();
  expect(swathEdgeNote(pass, [-58.1, -30.1, -55.9, -28.9], [-58, -30, -56, -29])).toBeUndefined();
  // with the view: only when the map shows past the frame's missing (east) edge
  expect(swathEdgeNote(pass, [-58, -30.6, -56.68, -28.2], [-58, -30.6, -55.3, -28.2], win)).toBeUndefined(); // river window inside the frame
  expect(swathEdgeNote(pass, [-58, -30.6, -56.68, -28.2], [-58, -30.6, -55.3, -28.2], [-58, -30.6, -55.3, -28.2])).toContain("east part");
});
