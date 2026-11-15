// Lucas (step 2): ONE Compare ⇆ — per-side layer + day, presets, never the
// same thing on both sides.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import {
  compareLayers,
  defaultSides,
  isRiskLayer,
  nisarDefaults,
  nisarSideDays,
  normSide,
  parseSideSpec,
  presetSides,
  sameSide,
  sideDays,
  sidesCompare,
  viewCompareLayer,
} from "./sideCompare";
import { nisarTracks } from "./radar";
import type { Step } from "./steps";

const mkSteps = (dates: string[]): Step[] =>
  dates.map((date, index) => ({ index, date, forecast: false, top: date, sub: "" })) as Step[];
const DAYS = ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"];
const steps = mkSteps(DAYS);
const frame = (date: string) => ({ id: "tpi_flood", date, png: `tpi_flood_${date}.png`, bounds: [0, 0, 1, 1] });
const gfs = (k: number, issued: string, date: string) => ({ id: `tpi_flood_gfs_d${k}`, issued, date, png: `gfs_d${k}_${date}.png`, forecast_source: "noaa-gfs" });
const nisar = (date: string, pol = "hh", track = "010D") =>
  ({ id: `nisar_${pol}_${date.replaceAll("-", "")}_${track}`, kind: "nisar_frame", date, png: `nisar_stack/png/nisar_${pol}_${date.replaceAll("-", "")}_${track}.png`, track, polarization: pol.toUpperCase(), mode: "4005_DHDH_A", bounds: [0, 0, 1, 1] });
const uy = {
  id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1],
  manifest: {
    event: "rs_uruguai_2026",
    layers: [
      ...DAYS.map(frame),
      gfs(3, "2026-09-27", "2026-09-30"),
      { id: "imerg_20260928", kind: "raster", date: "2026-09-28", png: "imerg_20260928.png", window: ["2026-09-28", "2026-09-30"], product: "IMERG Early" },
      ...["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24", "2026-11-05"].map((d) => nisar(d)),
    ],
  },
} as never;

// --- defaults + days -----------------------------------------------------------

test("defaults: right = selected day; left = 3 days earlier; same layer as the map's view", () => {
  const s = defaultSides(uy, steps, 4, "flood", 0)!;
  expect(s.R).toEqual({ layer: "flood", date: "2026-09-30", lead: 0 });
  expect(s.L).toEqual({ layer: "flood", date: "2026-09-27", lead: 0 });
  expect(sameSide(s.L, s.R)).toBe(false);
  expect(defaultSides(uy, steps, 4, "flood", 3)).toEqual({
    L: { layer: "flood", date: "2026-09-30", lead: 3 },
    R: { layer: "flood", date: "2026-09-30", lead: 0 },
  });
  // The map's own view seeds the layer: a radar-only view → the pass pair
  // (index + outlines compares the index — see the test below).
  const n = defaultSides(uy, steps, 4, "flood", 0, { layer: viewCompareLayer(uy, steps, ["flood"], "flood") })!;
  expect(n.L.layer).toBe("nisar");
  expect(nisarDefaults(["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24", "2026-11-05"], "2026-09-30")).toEqual({ before: "2026-09-30", after: "2026-10-12" });
});

test("each side's day list depends on its layer: only dates that exist", () => {
  expect(sideDays(uy, steps, "flood", { layer: "flood", date: "" }).map((d) => d.date)).toEqual(DAYS);
  expect(sideDays(uy, steps, "flood", { layer: "rain", date: "" }).map((d) => d.date)).toEqual(["2026-09-28"]);
  expect(sideDays(uy, steps, "flood", { layer: "nisar", date: "" }).map((d) => d.date)).toEqual(["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24", "2026-11-05"]);
  // Satellite lists the case days (steps ∪ layer dates).
  expect(sideDays(uy, steps, "flood", { layer: "satellite", date: "" }).map((d) => d.date)).toContain("2026-09-30");
  // A NISAR pass shows date · track · mode: 'Sep 30, 2026 · 010D · 4005'.
  expect(nisarSideDays(uy)[1].label).toMatch(/^Sep 30, 2026( · \w+)? · 4005$/);
});

test("compareLayers: only layers the case has are offered; live offers ml", () => {
  const ids = compareLayers(uy, steps, "flood").map((l) => l.id);
  expect(ids).toContain("flood");
  expect(ids).toContain("rain");
  expect(ids).toContain("nisar");
  expect(ids).toContain("satellite");
  expect(ids).not.toContain("ml"); // replay
  const now = { ...uy, mode: "live", manifest: { event: "now", layers: [
    ...uy.manifest.layers,
    { id: "p_trigger_24h", date: "2026-10-01", png: "p24.png" },
    { id: "p_trigger_72h", date: "2026-10-01", png: "p72.png" },
  ] } } as never;
  const live = compareLayers(now, steps, "flood");
  expect(live.map((l) => l.id)).toContain("ml");
  expect(live.find((l) => l.id === "ml")?.horizons).toEqual(["24h", "72h"]);
  expect(live.find((l) => l.id === "flood")?.canForecast).toBeUndefined(); // live: no leads
  expect(compareLayers(uy, steps, "flood").find((l) => l.id === "flood")?.canForecast).toBe(true);
});

// --- per-side resolution + labels --------------------------------------------

test("risk labels: observed vs forecast", () => {
  const ok = sidesCompare(uy, { L: { layer: "flood", date: "2026-09-27", lead: 0 }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(ok.pair?.before.url).toContain("tpi_flood_2026-09-27.png");
  expect(ok.pair?.before.label).toBe("Observed Sep 27 (NASA IMERG + SMAP) · Flood TPI");
  const fc = sidesCompare(uy, { L: { layer: "flood", date: "2026-09-30", lead: 3 }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(fc.pair?.before.label).toBe("NOAA forecast issued Sep 27 for Sep 30 (+3 d) · Flood TPI");
  expect(fc.pair?.before.url).toContain("gfs_d3_2026-09-30.png");
});

test("rain / satellite / ml / nisar labels", () => {
  const rain = sidesCompare(uy, { L: { layer: "rain", date: "2026-09-28" }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(rain.pair?.before.label).toBe("Rain (NASA GPM IMERG Early) — Sep 28–30");
  expect(rain.pair?.before.url).toContain("imerg_20260928.png");

  const sat = sidesCompare(uy, { L: { layer: "satellite", date: "2026-09-27" }, R: { layer: "satellite", date: "2026-10-12" } });
  expect(sat.pair?.before.label).toBe("Satellite (NASA GIBS VIIRS) — Sep 27");
  expect(sat.pair?.after.label).toBe("Satellite (NASA GIBS VIIRS) — Oct 12");
  expect(sat.pair?.before.tiles?.tiles[0]).toContain("gibs.earthdata.nasa.gov");
  expect(sat.pair?.before.tiles?.tiles[0]).toContain("/default/2026-09-27/");
  expect(sat.pair?.after.tiles?.tiles[0]).toContain("/default/2026-10-12/");

  const ni = sidesCompare(uy, { L: { layer: "nisar", date: "2026-09-30", pol: "HH" }, R: { layer: "nisar", date: "2026-10-12", pol: "HH" } });
  expect(ni.pair?.before.label).toBe("NISAR HH — Sep 30, 2026 (track 010D)");
  expect(ni.pair?.after.label).toBe("NISAR HH — Oct 12, 2026 (track 010D)");
  expect(ni.pair?.before.url).toContain("nisar_hh_20260930_010D.png");
  // Jhonatan: the radar flood candidates ride over a NISAR swipe.
  expect(ni.pair?.flood).toBeUndefined(); // this fixture has no flood_confirmed product — ok

  const ml = sidesCompare({ ...uy, mode: "live", manifest: { event: "now", layers: [
    ...uy.manifest.layers, { id: "p_trigger_24h", date: "2026-10-01", png: "p24.png" }] } } as never,
    { L: { layer: "ml", date: "2026-10-01", horizon: "24h" }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(ml.pair?.before.label).toBe("Landslide likelihood (ML) — next 24 h");
  expect(ml.pair?.before.url).toContain("p24.png");
});

test("any day vs any day; identical sides say 'Pick a different date'", () => {
  const same = sidesCompare(uy, { L: { layer: "flood", date: "2026-09-30", lead: 0 }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(same.warning).toBe("Pick a different date");
  // Different layers or different pol/track/horizon are never "identical".
  expect(sidesCompare(uy, { L: { layer: "nisar", date: "2026-09-30", pol: "HH" }, R: { layer: "nisar", date: "2026-09-30", pol: "HV" } }).warning).toBeUndefined();
  expect(sameSide({ layer: "nisar", date: "2026-09-30", pol: "HH", track: "010D" }, { layer: "nisar", date: "2026-09-30", pol: "HH", track: "082D" })).toBe(false);
  // Missing sides keep the swipe open and name the gap.
  const missing = sidesCompare(uy, { L: { layer: "nisar", date: "2026-10-30" }, R: { layer: "nisar", date: "2026-10-12" } });
  expect(missing.missing?.L).toContain("No NISAR pass for Oct 30");
  // …basemap only on the missing side (never the other side's image).
  expect(missing.pair?.before.url).toBe("");
  expect(missing.pair?.after.url).toContain("nisar_hh_20261012_010D.png");
  expect(missing.pair?.before.label).toContain("No NISAR pass for Oct 30");
});

// --- presets -------------------------------------------------------------------

test("presets: forecast vs what happened / NISAR before-after / day before vs day of", () => {
  const f = presetSides(uy, steps, 4, "flood", "forecast-observed")!;
  expect(f.L).toMatchObject({ layer: "flood", date: "2026-09-30", lead: 3 });
  expect(f.R).toMatchObject({ layer: "flood", date: "2026-09-30", lead: 0 });
  const n = presetSides(uy, steps, 4, "flood", "nisar-pair")!;
  expect(n.L).toMatchObject({ layer: "nisar", date: "2026-09-30", pol: "HH" });
  expect(n.R).toMatchObject({ layer: "nisar", date: "2026-10-12", pol: "HH" });
  expect(n.L.track).toBe(n.R.track); // same track
  const d = presetSides(uy, steps, 4, "flood", "day-before")!;
  expect(d).toEqual({ L: { layer: "flood", date: "2026-09-29", lead: 0 }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  // Hidden when the case cannot do it.
  const noGfs = { ...uy, manifest: { event: "x", layers: DAYS.map(frame) } } as never;
  expect(presetSides(noGfs, steps, 4, "flood", "forecast-observed")).toBeUndefined();
  expect(presetSides(uy, steps, 0, "flood", "day-before")).toBeUndefined(); // no earlier day
});

// --- deep links -----------------------------------------------------------------

test("parseSideSpec: <layer>:<YYYY-MM-DD>[:<lead|pol|horizon>[:<track>]]", () => {
  expect(parseSideSpec("flood:2026-09-30")).toEqual({ layer: "flood", date: "2026-09-30", lead: 0 });
  expect(parseSideSpec("flood:2026-09-30:3")).toEqual({ layer: "flood", date: "2026-09-30", lead: 3 });
  expect(parseSideSpec("slide:2026-09-30:d2")).toEqual({ layer: "slide", date: "2026-09-30", lead: 2 });
  expect(parseSideSpec("nisar:2026-10-12:HV")).toEqual({ layer: "nisar", date: "2026-10-12", pol: "HV" });
  expect(parseSideSpec("nisar:2026-10-12:hh:154d")).toEqual({ layer: "nisar", date: "2026-10-12", pol: "HH", track: "154D" });
  expect(parseSideSpec("ml:2026-10-01:72h")).toEqual({ layer: "ml", date: "2026-10-01", horizon: "72h" });
  expect(parseSideSpec("satellite:2026-09-27")).toEqual({ layer: "satellite", date: "2026-09-27" });
  expect(parseSideSpec("imerg:2026-09-28")).toEqual({ layer: "rain", date: "2026-09-28" });
  expect(parseSideSpec("bogus:2026-09-30")).toBeUndefined();
  expect(parseSideSpec("flood:not-a-day")).toBeUndefined();
  expect(parseSideSpec("flood:2026-09-30:x")).toBeUndefined();
});

test("normSide: clamps the day to the layer's own days; fills lead/pol/horizon", () => {
  expect(normSide(uy, steps, "flood", { layer: "rain", date: "2026-09-30" })).toEqual({ layer: "rain", date: "2026-09-28" });
  expect(normSide(uy, steps, "flood", { layer: "nisar", date: "2026-10-05" })).toMatchObject({ layer: "nisar", date: "2026-09-30", pol: "HH" });
  expect(normSide(uy, steps, "flood", { layer: "flood", date: "2026-09-30" })).toEqual({ layer: "flood", date: "2026-09-30", lead: 0 });
});

// --- NISAR tracks (Now contract) ------------------------------------------------

test("nisarTracks: one stack per track; pairs only within the same track + pol", () => {
  const now = { id: "now", mode: "live", bbox: [0, 0, 1, 1], manifest: { event: "now", layers: [
    nisar("2026-11-03", "hh", "154D"), nisar("2026-10-20", "hh", "154D"), nisar("2026-10-08", "hv", "154D"),
    nisar("2026-11-02", "hh", "010D"), nisar("2026-10-19", "hh", "010D"),
  ] } } as never;
  const tracks = nisarTracks(now);
  expect(tracks.map((t) => t.track).sort()).toEqual(["010D", "154D"]);
  expect(tracks.find((t) => t.track === "154D")?.dates).toEqual(["2026-10-08", "2026-10-20", "2026-11-03"]);
  // A side on a track + day resolves only that track's frame.
  const a = sidesCompare(now, {
    L: { layer: "nisar", date: "2026-10-20", pol: "HH", track: "154D" },
    R: { layer: "nisar", date: "2026-11-03", pol: "HH", track: "154D" },
  });
  expect(a.pair?.before.label).toBe("NISAR HH — Oct 20, 2026 (track 154D)");
  expect(a.pair?.after.url).toContain("nisar_hh_20261103_154D.png");
  // Lucas: each side lists EVERY pass of every track (date · track), any order.
  expect(nisarSideDays(now).map((d) => `${d.date}|${d.track}`)).toEqual([
    "2026-10-08|154D", "2026-10-19|010D", "2026-10-20|154D", "2026-11-02|010D", "2026-11-03|154D",
  ]);
  // Different tracks: allowed, with a non-blocking note; only identical picks are flagged.
  const mixed = sidesCompare(now, {
    L: { layer: "nisar", date: "2026-10-19", pol: "HH", track: "010D" },
    R: { layer: "nisar", date: "2026-11-03", pol: "HH", track: "154D" },
  });
  expect(mixed.pair).toBeDefined();
  expect(mixed.note).toBe("Different tracks — viewing angle differs, brightness not directly comparable");
  expect(mixed.warning).toBeUndefined();
  // Any order: the later pass on the left is fine.
  const reversed = sidesCompare(now, {
    L: { layer: "nisar", date: "2026-11-03", pol: "HH", track: "154D" },
    R: { layer: "nisar", date: "2026-10-20", pol: "HH", track: "154D" },
  });
  expect(reversed.pair && !reversed.warning && !reversed.note).toBe(true);
  const same = sidesCompare(now, {
    L: { layer: "nisar", date: "2026-11-03", pol: "HH", track: "154D" },
    R: { layer: "nisar", date: "2026-11-03", pol: "HH", track: "154D" },
  });
  expect(same.warning).toBe("Pick a different date");
  // Live offers every track; the compare lists them.
  expect(compareLayers(now, steps, "flood").find((l) => l.id === "nisar")?.tracks?.map((t) => t.id).sort()).toEqual(["010D", "154D"]);
  // A date the other track lacks resolves that side's own track frame.
  const b = sidesCompare(now, {
    L: { layer: "nisar", date: "2026-10-20", pol: "HH", track: "154D" },
    R: { layer: "nisar", date: "2026-10-20", pol: "HH", track: "010D" },
  });
  expect(b.missing?.R).toContain("track 010D");
});

test("nisarDefaults: last pass on/before the day vs the first pass after it — never the same pass", () => {
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

test("isRiskLayer guards the lead chips", () => {
  expect(isRiskLayer("flood")).toBe(true);
  expect(isRiskLayer("slide")).toBe(true);
  expect(isRiskLayer("nisar")).toBe(false);
});

test("Compare opens on the map's own layer: risk index over NISAR outlines → the index; radar-only → NISAR", async () => {
  const { viewCompareLayer } = await import("./sideCompare");
  const uyB = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], manifest: { event: "rs_uruguai_2026", layers: [
    { id: "tpi_flood", date: "2026-09-30", png: "a.png" }, { id: "tpi_flood", date: "2026-10-12", png: "b.png" },
    { id: "nisar_hh_20260930", kind: "nisar_frame", date: "2026-09-30", polarization: "HH", png: "n1.png" },
    { id: "nisar_hh_20261012", kind: "nisar_frame", date: "2026-10-12", polarization: "HH", png: "n2.png" },
  ] } } as never;
  const st = ["2026-09-30", "2026-10-12"].map((date, index) => ({ index, date, forecast: false, top: date, sub: "" })) as never[];
  expect(viewCompareLayer(uyB, st, ["tpi", "flood"], "flood")).toBe("flood");
  expect(viewCompareLayer(uyB, st, ["flood"], "flood")).toBe("nisar");
});

test("Radar tab 'Before / after' opens NISAR vs NISAR only (no layer chooser / presets); the map's Compare stays generic", async () => {
  const { reduce, initialState } = await import("../state");
  const s0 = initialState([uy], "rs_uruguai_2026");
  const radar = reduce(s0, { type: "openCompare", kind: "radar" }, [uy]);
  expect(radar.compareNisarOnly).toBe(true);
  expect(radar.compareSides?.L.layer).toBe("nisar");
  expect(radar.compareSides?.R.layer).toBe("nisar");
  const generic = reduce({ ...s0, layers: ["tpi", "flood"] }, { type: "openCompare", kind: "forecast" }, [uy]);
  expect(generic.compareNisarOnly).toBe(false);
  const link = initialState([uy], "rs_uruguai_2026", undefined, { compare: "radar", before: "2026-09-30", after: "2026-10-12", pol: "HH" });
  expect(link.compareNisarOnly).toBe(true);
});

test("Now: a rejected flood pair still offers NISAR Before / after once the Now stack has ≥ 2 passes", async () => {
  const { radarCard } = await import("./radar");
  const { liveBundle } = await import("./fixtures");
  const stack = [
    { id: "nisar_hh_20261103_154D", kind: "nisar_frame", date: "2026-11-03", track: "154D", polarization: "HH", png: "nisar_stack/png/a.png", bounds: [-55, -30, -53, -28] },
    { id: "nisar_hh_20261022_154D", kind: "nisar_frame", date: "2026-10-22", track: "154D", polarization: "HH", png: "nisar_stack/png/b.png", bounds: [-55, -30, -53, -28] },
  ];
  const response = { pairs: 1, flooded_km2: null, polygons: null, null_reasons: { pairs: "per-scene threshold at the clamp" } };
  const without = radarCard(liveBundle({ response, manifest: { layers: [] } } as never));
  const withStack = radarCard(liveBundle({ response, manifest: { event: "now", layers: stack } } as never));
  expect(withStack.compare.reason).toBeUndefined();
  expect(without.compare.reason === undefined || /no comparable/.test(without.compare.reason ?? "")).toBe(true);
});

test("WA pair-only: nisar_ref (Nov 10) is a pass too; RS 2024 says NISAR came later", async () => {
  const { nisarPairDays, noRadarText } = await import("./radar");
  const wa = { id: "wa_2025", mode: "replay", bbox: [0, 0, 1, 1], manifest: { event: "wa_2025", layers: [
    { id: "nisar_ref", date: "2025-11-10", png: "ref.png" }, { id: "nisar_pre", date: "2025-12-04", png: "pre.png" }, { id: "nisar_post", date: "2025-12-16", png: "post.png" },
  ] } } as never;
  expect(nisarPairDays(wa)).toEqual(["2025-11-10", "2025-12-04", "2025-12-16"]);
  const ref = sidesCompare(wa, { L: { layer: "nisar", date: "2025-11-10", pol: "HH" }, R: { layer: "nisar", date: "2025-12-16", pol: "HH" } });
  expect(ref.pair?.before.url).toContain("ref.png");
  const rs = { id: "rs_2024", mode: "replay", bbox: [0, 0, 1, 1], reference_date: "2024-05-01", manifest: { layers: [] } } as never;
  expect(noRadarText(rs)).toBe("NISAR launched Jul 2025 — no passes for this 2024 event; use Compare ⇆ on the map with Satellite for before/after");
});

test("NASA OPERA surface water in Compare where the case has it: WA validation image, Now DSWx polygons", async () => {
  const { compareLayers, sidesCompare: sc } = await import("./sideCompare");
  const wa = { id: "wa_2025", mode: "replay", bbox: [0, 0, 1, 1], manifest: { event: "wa_2025", layers: [
    { id: "opera_validation", date: "2025-12-16", png: "wa_2025/png/opera_validation_20251216.png", bounds: [0, 0, 1, 1] },
    { id: "nisar_pre", date: "2025-12-04", png: "pre.png" }, { id: "nisar_post", date: "2025-12-16", png: "post.png" },
  ] } } as never;
  expect(compareLayers(wa, [], "flood").map((l) => l.id)).toContain("opera");
  const r = sc(wa, { L: { layer: "nisar", date: "2025-12-16", pol: "HH" }, R: { layer: "opera", date: "2025-12-16" } });
  expect(r.pair?.after.label).toBe("NISAR vs NASA OPERA surface water — Dec 16");
  expect(r.pair?.after.url).toContain("opera_validation_20251216.png");
  const now = { id: "now", mode: "live", bbox: [0, 0, 1, 1], products: { flood_dswx: "response/flood_dswx.geojson" }, manifest: { event: "now", layers: [] } } as never;
  const steps = [{ index: 0, date: "2026-11-15", forecast: false, top: "", sub: "" }] as never[];
  expect(compareLayers(now, steps, "flood").map((l) => l.id)).toContain("opera");
  const n = sc(now, { L: { layer: "opera", date: "2026-11-15" }, R: { layer: "satellite", date: "2026-11-14" } });
  expect(n.pair?.before.geojson).toContain("flood_dswx.geojson");
  expect(n.pair?.before.label).not.toMatch(/S1|Sentinel/);
  const uyNone = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], manifest: { layers: [] } } as never;
  expect(compareLayers(uyNone, [], "flood").map((l) => l.id)).not.toContain("opera");
});

// --- per-hazard side → frame (Lucas BUG: Landslide Compare showed flood) ----

// Two-hazard replay (RS / WA shape): observed tpi_flood + tpi_slide every
// day, GFS runs for both hazards; flood-only replay (Uruguaiana shape); live
// (Now shape): tpi_<hz>_d0..d3.
const both = {
  id: "wa_2025", mode: "replay", bbox: [0, 0, 1, 1],
  manifest: {
    event: "wa_2025",
    layers: [
      ...DAYS.map(frame),
      ...DAYS.map((date) => ({ id: "tpi_slide", date, png: `tpi_slide_${date}.png`, bounds: [0, 0, 1, 1] })),
      gfs(3, "2026-09-27", "2026-09-30"),
      gfs(1, "2026-09-27", "2026-09-28"),
      { id: "tpi_slide_gfs_d3", issued: "2026-09-27", date: "2026-09-30", png: "slide_gfs_d3_2026-09-30.png", forecast_source: "noaa-gfs" },
      { id: "tpi_slide_gfs_d1", issued: "2026-09-27", date: "2026-09-28", png: "slide_gfs_d1_2026-09-28.png", forecast_source: "noaa-gfs" },
    ],
  },
} as never;
const NOW_DAYS = ["2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"];
const live = {
  id: "now", mode: "live", bbox: [0, 0, 1, 1],
  manifest: {
    event: "now",
    layers: (["flood", "slide"] as const).flatMap((hz) => NOW_DAYS.map((date, k) => ({
      id: `tpi_${hz}_d${k}`, date, png: `png/tpi_${hz}_d${k}.png`, bounds: [0, 0, 1, 1], ...(k ? { forecast: true } : {}),
    }))),
  },
} as never;

for (const [name, b, st, day] of [["replay (RS/WA)", both, steps, "2026-09-30"], ["live (Now)", live, mkSteps(NOW_DAYS), "2026-11-16"]] as const) {
  test(`${name}: each risk side shows ITS hazard's frame, observed and forecast`, () => {
    for (const hz of ["flood", "slide"] as const) {
      const r = sidesCompare(b, { L: { layer: hz, date: DAYS[1], lead: 0 }, R: { layer: hz, date: day, lead: 0 } });
      expect(r.pair?.after.url).toContain(`tpi_${hz}_`);
      expect(r.pair?.after.label).toContain(hz === "slide" ? "Landslide" : "Flood");
      expect(r.missing?.R).toBeUndefined();
      expect(sideDays(b, st, hz, { layer: hz, date: "" }).length).toBe(st.length);
    }
  });
}

test("replay forecast side per hazard: the GFS frame of that hazard, issued X for X+k", () => {
  const s = sidesCompare(both, { L: { layer: "slide", date: "2026-09-30", lead: 3 }, R: { layer: "slide", date: "2026-09-30", lead: 0 } });
  expect(s.pair?.before.url).toContain("slide_gfs_d3_2026-09-30.png");
  expect(s.pair?.before.label).toBe("NOAA forecast issued Sep 27 for Sep 30 (+3 d) · Landslide TPI");
  expect(s.pair?.after.url).toContain("tpi_slide_2026-09-30.png");
  // A day with no run issued / a run without that lead says which.
  expect(sidesCompare(both, { L: { layer: "slide", date: "2026-09-29", lead: 3 }, R: { layer: "slide", date: "2026-09-30", lead: 0 } }).missing?.L)
    .toBe("No NOAA run issued Sep 26, 2026");
  expect(sidesCompare(both, { L: { layer: "slide", date: "2026-09-29", lead: 2 }, R: { layer: "slide", date: "2026-09-30", lead: 0 } }).missing?.L)
    .toBe("The NOAA run issued Sep 27, 2026 has no +2 d frame");
});

test("flood-only case: Landslide is listed disabled with the reason, never a blank or flood side", () => {
  const lay = compareLayers(uy, steps, "flood");
  const slide = lay.find((l) => l.id === "slide");
  expect(slide?.disabled).toBe("Landslide index not computed for this flood case");
  expect(sideDays(uy, steps, "slide", { layer: "slide", date: "" })).toEqual([]);
  const r = sidesCompare(uy, { L: { layer: "slide", date: "2026-09-30", lead: 0 }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(r.missing?.L).toBe("Landslide index not computed for this flood case");
  expect(r.pair?.before.url).toBe(""); // never the flood frame on a landslide side
  expect(r.pair?.before.label).toContain("Landslide index not computed for this flood case");
  // Defaults never pick the disabled layer.
  expect(defaultSides(uy, steps, 4, "slide", 0)?.R.layer).not.toBe("slide");
});

test("forecast side: the picker lists ISSUE days (● = NOAA run), date = X + lead", () => {
  const d = sideDays(both, steps, "slide", { layer: "slide", date: "2026-09-30", lead: 3 });
  const x = d.find((e) => e.issue === "2026-09-27")!;
  expect(x).toMatchObject({ date: "2026-09-30", run: true, label: "● Sep 27, 2026" });
  expect(d.find((e) => e.issue === "2026-09-28")).toMatchObject({ date: "2026-10-01", run: false, label: "Sep 28, 2026" });
  // normSide keeps a forecast side on its issue day + lead.
  expect(normSide(both, steps, "slide", { layer: "slide", date: "2026-09-28", lead: 1 })).toEqual({ layer: "slide", date: "2026-09-28", lead: 1 });
});

// --- conductor (L61): 'Flood risk' vs 'Flood risk + NISAR' -------------------

const withNisar = {
  ...(both as unknown as Record<string, unknown>),
  manifest: {
    event: "wa_2025",
    layers: [
      // flood + NISAR listed first: exact-family matching, not order.
      { id: "tpi_flood_nisar", date: "2026-09-30", png: "tpi_flood_nisar_2026-09-30.png", bounds: [0, 0, 1, 1],
        nisar_passes: [{ date: "2026-09-27", track: "172D", id: "x" }] },
      ...(both as unknown as { manifest: { layers: unknown[] } }).manifest.layers,
    ],
  },
} as never;

test("Compare: plain 'Flood risk' never shows the NISAR frame; 'Flood risk + NISAR' is its own option", () => {
  const plain = sidesCompare(withNisar, { L: { layer: "flood", date: "2026-09-30", lead: 3 }, R: { layer: "flood", date: "2026-09-30", lead: 0 } });
  expect(plain.pair?.after.url).toContain("tpi_flood_2026-09-30.png");
  const fn = sidesCompare(withNisar, { L: { layer: "flood", date: "2026-09-30", lead: 0 }, R: { layer: "flood_nisar", date: "2026-09-30" } });
  expect(fn.pair?.after.url).toContain("tpi_flood_nisar_2026-09-30.png");
  expect(fn.pair?.after.label).toBe("Observed Sep 30 · Flood TPI + NISAR water (pass Sep 27)");
  expect(fn.pair?.before.url).toContain("tpi_flood_2026-09-30.png");
  const lay = compareLayers(withNisar, steps, "flood");
  expect(lay.find((l) => l.id === "flood_nisar")?.days.map((d) => d.date)).toEqual(["2026-09-30"]);
  // The default stays the plain index (like-with-like forecast vs observed).
  expect(defaultSides(withNisar, steps, 4, "flood", 3)).toEqual({
    L: { layer: "flood", date: "2026-09-30", lead: 3 },
    R: { layer: "flood", date: "2026-09-30", lead: 0 },
  });
  expect(parseSideSpec("flood_nisar:2026-09-30")).toEqual({ layer: "flood_nisar", date: "2026-09-30" });
  // A case without NISAR frames does not offer it.
  expect(compareLayers(both, steps, "flood").some((l) => l.id === "flood_nisar")).toBe(false);
});
