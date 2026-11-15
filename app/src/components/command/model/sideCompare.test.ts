// Lucas (bug): the compare must never show the same thing on both sides.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { defaultSides, nisarDefaults, sameSide, sidesCompare } from "./sideCompare";

const steps = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"].map((date, index) =>
  ({ index, date, forecast: false, top: date, sub: "" })) as never[];
const frame = (date: string) => ({ id: "tpi_flood", date, png: `tpi_flood_${date}.png` });
const gfs = (k: number, issued: string, date: string) => ({ id: `tpi_flood_gfs_d${k}`, issued, date, png: `gfs_d${k}_${date}.png`, forecast_source: "noaa-gfs" });
const uy = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], manifest: { event: "rs_uruguai_2026", layers: [
  ...["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"].map(frame), gfs(3, "2026-09-27", "2026-09-30"),
] } } as never;

test("defaults: right = selected day observed; left = 3 days earlier (Observed view) or the forecast for that day (Forecast view)", () => {
  const s = defaultSides(steps, 4, 0)!;
  expect(s.R).toEqual({ date: "2026-09-30", lead: 0 });
  expect(s.L).toEqual({ date: "2026-09-27", lead: 0 });
  expect(sameSide(s.L, s.R)).toBe(false);
  expect(defaultSides(steps, 4, 3)).toEqual({ L: { date: "2026-09-30", lead: 3 }, R: { date: "2026-09-30", lead: 0 } });
  expect(defaultSides(steps, 0, 0)!.L.date).not.toBe("2026-09-26"); // no earlier day: another step, never the same
});

test("any day vs any day; identical sides say 'Pick a different date'", () => {
  const ok = sidesCompare(uy, { L: { date: "2026-09-27", lead: 0 }, R: { date: "2026-09-30", lead: 0 } }, "flood");
  expect(ok.pair?.before.url).toContain("tpi_flood_2026-09-27.png");
  expect(ok.pair?.after.url).toContain("tpi_flood_2026-09-30.png");
  expect(ok.pair?.before.label).toBe("Observed Sep 27 (NASA IMERG + SMAP)");
  expect(ok.warning).toBeUndefined();
  const fc = sidesCompare(uy, { L: { date: "2026-09-30", lead: 3 }, R: { date: "2026-09-30", lead: 0 } }, "flood");
  expect(fc.pair?.before.label).toBe("Forecast for Sep 30, issued Sep 27 (NOAA GFS)");
  expect(fc.pair?.before.url).toContain("gfs_d3_2026-09-30.png");
  const same = sidesCompare(uy, { L: { date: "2026-09-30", lead: 0 }, R: { date: "2026-09-30", lead: 0 } }, "flood");
  expect(same.warning).toBe("Pick a different date");
});

test("NISAR defaults: last pass on/before the day vs the first pass after it — never the same pass", () => {
  const uyPasses = ["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24", "2026-11-05"];
  expect(nisarDefaults(uyPasses, "2026-09-30")).toEqual({ before: "2026-09-30", after: "2026-10-12" });
  expect(nisarDefaults(uyPasses, "2026-10-05")).toEqual({ before: "2026-09-30", after: "2026-10-12" });
  expect(nisarDefaults(["2025-11-22", "2025-12-04", "2025-12-16"], "2025-12-08")).toEqual({ before: "2025-12-04", after: "2025-12-16" });
  expect(nisarDefaults(uyPasses, "2026-12-01")).toEqual({ before: "2026-10-24", after: "2026-11-05" });
  expect(nisarDefaults(uyPasses, "2026-09-01")).toEqual({ before: "2026-09-18", after: "2026-09-30" });
});

test("Radar card date chips: After lists only later passes; picking the last Before keeps a later After (bug repro)", async () => {
  const RadarCard = (await import("../RadarCard")).default;
  const picks: [string, string][] = [];
  const dates = ["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24", "2026-11-05"];
  const html = renderToStaticMarkup(createElement(RadarCard, {
    radar: { nisar: true, pair: true, headline: "x", candidates: { label: "c", value: "1", note: "" }, compare: { kind: "radar" }, extras: [], passes: [], passCount: 0, canShowOnMap: false } as never,
    floodOn: false, compareOpen: true, onConfirm: () => {}, onShowFlood: () => {}, onCompare: () => {},
    swipeDates: { dates, before: "2026-10-24", after: "2026-11-05", onPick: (b, a) => picks.push([b, a]) },
  }));
  const beforeRow = html.slice(html.indexOf('aria-label="Radar date before"'), html.indexOf('aria-label="Radar date after"'));
  const afterRow = html.slice(html.indexOf('aria-label="Radar date after"'));
  expect(beforeRow).not.toContain(">Nov 5<"); // the last pass can't be a Before
  expect(afterRow).not.toContain(">Oct 24<"); // only passes after Before
  expect(afterRow).toContain(">Nov 5<");
});
