// Lucas: hour chips over lead-analysis 6-hourly frames; swipe dates as chips.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { hasHourFrames, hourFrameFor, hourLabel, hoursOn } from "./hours";

const six = (datetime: string) => ({ id: "tpi_flood_6h", date: datetime.slice(0, 10), datetime, png: `png/tpi_flood_6h_${datetime}.png` });
const b = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], manifest: { layers: [
  six("2026-09-30T00:00Z"), six("2026-09-30T06:00Z"), six("2026-09-30T12:00Z"), six("2026-09-30T18:00Z"), six("2026-10-01T00:00Z"),
  { id: "tpi_flood", date: "2026-09-30", png: "png/tpi_flood_20260930.png" },
] } } as never;

test("hour chips only where 6-hourly frames exist; hours of the selected day", () => {
  expect(hasHourFrames(b, "flood")).toBe(true);
  expect(hasHourFrames(b, "slide")).toBe(false);
  expect(hasHourFrames({ id: "wa_2025", mode: "replay", bbox: [0, 0, 1, 1], manifest: { layers: [] } } as never, "flood")).toBe(false);
  expect(hoursOn(b, "flood", "2026-09-30")).toEqual(["00", "06", "12", "18"]);
  expect(hoursOn(b, "flood", "2026-10-01")).toEqual(["00"]);
  expect(hourFrameFor(b, "flood", "2026-09-30", "06")?.png).toBe("png/tpi_flood_6h_2026-09-30T06:00Z.png");
  expect(hourFrameFor(b, "flood", "2026-09-30", null)).toBeUndefined();
  expect(hourLabel(hourFrameFor(b, "flood", "2026-09-30", "06")!)).toBe("Sep 30, 03:00 BRT");
});

test("setCompareDates moves the swipe dates (Radar card chips)", async () => {
  const { reduce, initialState } = await import("../state");
  const s = reduce(initialState([b]), { type: "setCompareDates", before: "2026-09-18", after: "2026-11-05" }, [b]);
  expect(s.compareInit).toMatchObject({ before: "2026-09-18", after: "2026-11-05" });
});

test("6-hourly frames never stand in for a day's frame or create steps", async () => {
  const { pickTpiFrame } = await import("@/lib/layers");
  const { stepsFor } = await import("./steps");
  const layers = [
    { id: "tpi_flood_6h", date: "2026-09-30", datetime: "2026-09-30T06:00Z", png: "6h.png" },
    { id: "tpi_flood", date: "2026-09-30", png: "daily.png" },
  ];
  expect(pickTpiFrame(layers as never, { date: "2026-09-30" }, "flood")?.png).toBe("daily.png");
  const b = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], manifest: { layers } } as never;
  expect(stepsFor(b).map((s) => s.layer?.png)).toEqual(["daily.png"]);
});

test("Forecast view + hour chip: the 6-hourly GFS run valid at D HHZ issued k days before (nearest issue otherwise)", async () => {
  const { hourForecastFrame, hourForecastCaption } = await import("./hours");
  const g = (issued: string, datetime: string) => ({ id: "tpi_flood_gfs_6h", issued, datetime, date: datetime.slice(0, 10), png: `${issued}_${datetime}.png`, forecast_source: "noaa-gfs" });
  const bb = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], manifest: { layers: [
    g("2026-09-28", "2026-09-30T06:00Z"), g("2026-09-29", "2026-09-30T06:00Z"), g("2026-09-30", "2026-09-30T06:00Z"),
  ] } } as never;
  expect(hourForecastFrame(bb, "flood", "2026-09-30", "06", 2)).toMatchObject({ issued: "2026-09-28", k: 2 });
  expect(hourForecastFrame(bb, "flood", "2026-09-30", "06", 1)).toMatchObject({ issued: "2026-09-29", k: 1 });
  expect(hourForecastFrame(bb, "flood", "2026-09-30", "06", 3)).toMatchObject({ issued: "2026-09-28", k: 2 }); // nearest
  expect(hourForecastFrame(bb, "flood", "2026-09-30", "12", 1)).toBeUndefined();
  expect(hourForecastFrame(bb, "flood", "2026-09-30", null, 1)).toBeUndefined();
  expect(hourForecastCaption("2026-09-30", "06", "2026-09-28", "Flood")).toBe("Forecast for Sep 30 06Z, issued Sep 28 00Z (NOAA GFS) · Flood TPI");
});
