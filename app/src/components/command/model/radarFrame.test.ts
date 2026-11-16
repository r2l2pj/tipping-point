// Lucas (BUG): the Radar — NISAR view draws the frame for the selected day.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { mainTrack, passLabel, radarFrame, radarPasses } from "./radarFrame";
import type { CcBundle } from "./types";

const ymd = (d: string) => d.replaceAll("-", "");
const nisar = (date: string, pol = "hh", track = "010D") => ({
  id: `nisar_${pol}_${ymd(date)}_${track}`, kind: "nisar_frame", date, track, polarization: pol.toUpperCase(),
  png: `rs_uruguai_2026/png/nisar_${pol}_${ymd(date)}_${track}.png`, bounds: [0, 0, 1, 1],
});
const uy = {
  id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1],
  manifest: {
    event: "rs_uruguai_2026",
    layers: [
      ...["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24"].flatMap((d) => [nisar(d), nisar(d, "hv")]),
      nisar("2026-10-05", "hh", "082D"),
      nisar("2026-10-17", "hh", "082D"),
    ],
  },
} as unknown as CcBundle;

test("radarPasses: every pass of every track, sorted; main track = most passes", () => {
  const p = radarPasses(uy);
  expect(p.map(passLabel)).toEqual([
    "Sep 18, 2026 · 010D", "Sep 30, 2026 · 010D", "Oct 5, 2026 · 082D",
    "Oct 12, 2026 · 010D", "Oct 17, 2026 · 082D", "Oct 24, 2026 · 010D",
  ]);
  expect(mainTrack(p)).toBe("010D");
});

test("radarFrame: the main-track pass on/before the day, HH, with the chip", () => {
  const p = radarPasses(uy);
  const f = radarFrame(p, "2026-10-16")!;
  expect(f.pass.date).toBe("2026-10-12"); // not Oct 5 / Oct 17 (082D)
  expect(f.pol).toBe("HH");
  expect(f.chip).toBe("NISAR HH · Oct 12, 2026 · track 010D");
  expect(f.url).toContain("nisar_hh_20261012_010D.png");
  expect(radarFrame(p, "2026-09-30")!.pass.date).toBe("2026-09-30");
});

test("radarFrame: before the first pass shows the first one with a note", () => {
  const f = radarFrame(radarPasses(uy), "2026-09-01")!;
  expect(f.pass.date).toBe("2026-09-18");
  expect(f.note).toBe("first pass after Sep 1, 2026");
});

test("radarFrame: HV and a picked pass (any track); HV falls back to HH when missing", () => {
  const p = radarPasses(uy);
  const hv = radarFrame(p, "2026-10-16", { pol: "HV" })!;
  expect(hv.chip).toBe("NISAR HV · Oct 12, 2026 · track 010D");
  const other = radarFrame(p, "2026-10-16", { date: "2026-10-17", track: "082D", pol: "HV" })!;
  expect(other.pass.track).toBe("082D");
  expect(other.pol).toBe("HH"); // 082D ships no HV here
});

test("radarPasses: legacy nisar_pre/post/ref pair without a stack (Now)", () => {
  const now = {
    id: "now", mode: "live", bbox: [0, 0, 1, 1],
    manifest: {
      event: "now",
      layers: [
        { id: "nisar_pre", date: "2026-10-25", png: "now/png/nisar_pre.png", polarization: "HH" },
        { id: "nisar_post", date: "2026-11-06", png: "now/png/nisar_post.png", polarization: "HH" },
        { id: "nisar_hv_post", date: "2026-11-06", png: "now/png/nisar_hv_post.png", polarization: "HV" },
        { id: "tpi_flood", date: "2026-11-06", png: "now/png/tpi.png" },
      ],
    },
  } as unknown as CcBundle;
  const p = radarPasses(now);
  expect(p.map((x) => x.date)).toEqual(["2026-10-25", "2026-11-06"]);
  expect(Object.keys(p[1].frames).sort()).toEqual(["HH", "HV"]);
  const f = radarFrame(p, "2026-11-15")!;
  expect(f.chip).toBe("NISAR HH · Nov 6, 2026");
});

test("radarPasses: no NISAR → no frame", () => {
  const none = { id: "rs_2024", mode: "replay", manifest: { event: "rs_2024", layers: [] } } as unknown as CcBundle;
  expect(radarPasses(none)).toEqual([]);
  expect(radarFrame([], "2024-05-01")).toBeUndefined();
});
