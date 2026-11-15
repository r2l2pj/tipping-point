// L61: Flood risk + NISAR (contract t1b8 2026-11-15) — frame pick, water
// overlay, caption note, agreement sentence, and the plain pickers ignoring
// the tpi_flood_nisar family.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { pickTpiFrame } from "@/lib/layers";
import { agreementText, km2Text, nisarWaterHeadline, floodNisarFrame, floodNisarNote, hasFloodNisar, nisarAgreement, nisarWaterLayer } from "./floodNisar";
import type { CcBundle } from "./types";

const uy = {
  id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1],
  manifest: {
    event: "rs_uruguai_2026",
    layers: [
      { id: "tpi_flood", date: "2026-10-12", png: "rs_uruguai_2026/png/tpi_flood_20261012.png" },
      { id: "tpi_flood_nisar", kind: "observed", date: "2026-10-12", png: "rs_uruguai_2026/png/tpi_flood_nisar_20261012.png",
        bounds: [0, 0, 1, 1], nisar_passes: [{ date: "2026-09-30", track: "010D" }, { date: "2026-10-12", track: "010D" }] },
      { id: "tpi_flood", date: "2026-10-13", png: "rs_uruguai_2026/png/tpi_flood_20261013.png" },
      { id: "tpi_flood_nisar", kind: "observed", date: "2026-10-13", png: "rs_uruguai_2026/png/tpi_flood_nisar_20261013.png",
        nisar_passes: [{ date: "2026-10-12", track: "010D" }] },
      { id: "nisar_water_20261012", kind: "nisar_extra", date: "2026-10-12", pass_date: "2026-10-12", png: "rs_uruguai_2026/png/nisar_water_20261012.png", bounds: [0, 0, 1, 1] },
    ],
  },
  metricsView: {
    nisar_agreement: { water_km2: 28.04, observed: { alto_plus: { hit_pct: 40 } },
      forecast: { lead_1d: { alto_plus: { hit_pct: 12.5 } }, lead_3d: { alto_plus: { hit_pct: 0 } } } },
  },
} as unknown as CcBundle;

test("the plain day pick never lands on tpi_flood_nisar", () => {
  const l = pickTpiFrame(uy.manifest!.layers, { date: "2026-10-12" }, "flood");
  expect(l?.id).toBe("tpi_flood");
  expect(l?.png).toContain("tpi_flood_20261012");
});

test("floodNisarFrame: the day's flood + NISAR frame, else nothing (fallback)", () => {
  expect(hasFloodNisar(uy)).toBe(true);
  expect(floodNisarFrame(uy, "2026-10-12")?.png).toContain("tpi_flood_nisar_20261012");
  expect(floodNisarFrame(uy, "2026-10-20")).toBeUndefined();
});

test("nisarWaterLayer: same date, else the frame's pass date; Now: nisar_water", () => {
  expect(nisarWaterLayer(uy, "2026-10-12", floodNisarFrame(uy, "2026-10-12"))?.id).toBe("nisar_water_20261012");
  expect(nisarWaterLayer(uy, "2026-10-13", floodNisarFrame(uy, "2026-10-13"))?.id).toBe("nisar_water_20261012");
  const now = { id: "now", mode: "live", manifest: { event: "now", layers: [
    { id: "tpi_flood_nisar_d0", date: "2026-11-15", png: "png/tpi_flood_nisar_d0.png" },
    { id: "nisar_water", kind: "nisar_extra", png: "png/nisar_water.png", pass_date: "2026-11-06" },
  ] } } as unknown as CcBundle;
  expect(floodNisarFrame(now, "2026-11-15")?.id).toBe("tpi_flood_nisar_d0");
  expect(nisarWaterLayer(now, "2026-11-15")?.id).toBe("nisar_water");
});

test("caption note names the pass", () => {
  expect(floodNisarNote(floodNisarFrame(uy, "2026-10-12"))).toBe(" + NISAR water (pass Oct 12, 2026)");
  expect(floodNisarNote(undefined)).toBe("");
});

test("L62 agreement: window_12d (index without NISAR, best day ≤ 12 d before the pass), never forecast.lead_1d", () => {
  const rich = { ...uy, metricsView: { nisar_agreement: {
    pass: { date: "2026-10-12", track: "010D" }, water_km2: 28.04,
    observed: { alto_plus: { hit_pct: 40 } },
    forecast: { lead_1d: { alto_plus: { hit_pct: 0 } } },
    window_12d: { best_day: { critico: { day: "2026-09-30", days_before: 12, hit_pct: 61.2, lift: 2.1 }, alto_plus: { day: "2026-10-01", days_before: 11, hit_pct: 80 } } },
    gfs_window_12d: { best: { critico: { issued: "2026-09-27", valid: "2026-09-30", issued_days_before: 15, critico: { hit_pct: 33 } } } },
    headline_label: "index without NISAR, up to 12 days before the pass",
  } } } as unknown as CcBundle;
  const a = nisarAgreement(rich)!;
  expect(a.window).toEqual({ hit: 61.2, daysBefore: 12, day: "2026-09-30", cls: "Critical" });
  const t = agreementText(a);
  expect(t.main).toBe("Of the 28.0 km² the Oct 12 pass saw, the index without NISAR had flagged 61% as Critical 12 days before");
  expect(t.main).not.toContain("1 day before");
  expect(t.more).toBe("Pass Oct 12, 2026 · track 010D · Critical on Sep 30, 2026 · pass day: 40% (High or Critical) · NOAA GFS issued Sep 27, 2026 (15 days before): 33% Critical");
  expect(t.label).toBe("index without NISAR, up to 12 days before the pass");
  // flat keys only
  const flat = { ...uy, metricsView: { nisar_agreement_water_km2: 5, nisar_agreement_win_critical_hit_pct: 7.5, nisar_agreement_win_critical_days_before: 3 } } as unknown as CcBundle;
  expect(agreementText(nisarAgreement(flat)!).main).toBe("Of the 5.0 km² NISAR saw, the index without NISAR had flagged 8% as Critical 3 days before");
  // a list of pass records: the last one with a window is the headline
  const list = { ...uy, metricsView: { nisar_agreement: [(rich.metricsView as unknown as Record<string, unknown>).nisar_agreement, { water_km2: 1 }] } } as unknown as CcBundle;
  expect(nisarAgreement(list)!.waterKm2).toBe(28.04);
  expect(nisarAgreement({ ...uy, metricsView: {} } as unknown as CcBundle)).toBeUndefined();
});

// Conductor (L61): the NISAR card headline + an agreement sentence that names
// its own base — real metrics.json values (main, Nov 15).
const UY_METRICS = {
  nisar_water_km2_case_box: 1105.61, nisar_raised_km2_floodplain: 822.03,
  nisar_agreement_water_km2_single_pass: 612.27, nisar_agreement_win_critical_hit_pct: 8.95,
  nisar_agreement_win_critical_days_before: 10, nisar_agreement_pass_date: "2026-10-12",
  nisar_agreement: {
    pass: { date: "2026-10-12", track: "010D" }, water_km2: 612.27, water_km2_single_pass: 612.27,
    window_12d: { best_day: { critico: { day: "2026-10-02", days_before: 10, hit_pct: 8.95 } } },
    map_12d: { passes: ["2026-09-30/010D", "2026-10-05/082D", "2026-10-12/010D"], water_km2_case_box: 1105.61, raised_km2_floodplain: 822.03 },
  },
};
const WA_METRICS = {
  nisar_agreement_win_critical_hit_pct: 50.53, nisar_agreement_win_critical_days_before: 4,
  nisar_agreement_pass_date: "2025-12-16", nisar_agreement_water_km2: 2.0,
  nisar_agreement: { pass: { date: "2025-12-16", track: "172D" }, water_km2: 2.0,
    window_12d: { best_day: { critico: { day: "2025-12-12", days_before: 4, hit_pct: 50.53 } } } },
};

test("NISAR card headline: water on the served map (passes ≤ 12 d) + the floodplain part that raised it", () => {
  const h = nisarWaterHeadline({ ...uy, metricsView: UY_METRICS } as unknown as CcBundle)!;
  expect(h.text).toBe("NISAR saw water in 1,106 km² (passes Sep 30–Oct 12); 822 km² on the floodplain raised the flood map");
  expect(h.passes.map((p) => p.track)).toEqual(["010D", "082D", "010D"]);
  // Flat keys only (no map_12d record): same numbers, passes from nowhere → no range.
  const flat = nisarWaterHeadline({ ...uy, metricsView: { nisar_water_km2_case_box: 1105.61, nisar_raised_km2_floodplain: 822.03 } } as unknown as CcBundle)!;
  expect(flat.text).toBe("NISAR saw water in 1,106 km²; 822 km² on the floodplain raised the flood map");
  // WA: no map record yet → its single scored pass, no 'raised' clause.
  expect(nisarWaterHeadline({ ...uy, metricsView: WA_METRICS } as unknown as CcBundle)!.text).toBe("NISAR saw water in 2.0 km² (pass Dec 16)");
  expect(nisarWaterHeadline({ ...uy, metricsView: {} } as unknown as CcBundle)).toBeUndefined();
});

test("agreement sentence names its own base (the single pass it scores)", () => {
  expect(agreementText(nisarAgreement({ ...uy, metricsView: UY_METRICS } as unknown as CcBundle)!).main)
    .toBe("Of the 612 km² the Oct 12 pass saw, the index without NISAR had flagged 9% as Critical 10 days before");
  expect(agreementText(nisarAgreement({ ...uy, metricsView: WA_METRICS } as unknown as CcBundle)!).main)
    .toBe("Of the 2.0 km² the Dec 16 pass saw, the index without NISAR had flagged 51% as Critical 4 days before");
  expect(km2Text(1105.61)).toBe("1,106 km²");
  expect(km2Text(28.04)).toBe("28.0 km²");
});

test("Radar card: the headline replaces the teammate-polygon flooded area (replay only)", async () => {
  const { radarCard } = await import("./radar");
  const card = radarCard({ ...uy, metricsView: { ...UY_METRICS, flooded_km2: 1.8 } } as unknown as CcBundle);
  expect(card.water).toBe("NISAR saw water in 1,106 km² (passes Sep 30–Oct 12); 822 km² on the floodplain raised the flood map");
  expect(card.flooded).toBeUndefined();
  expect(card.people).toBeUndefined();
  const none = radarCard({ ...uy, metricsView: { flooded_km2: 1.8 } } as unknown as CcBundle);
  expect(none.water).toBeUndefined();
});
