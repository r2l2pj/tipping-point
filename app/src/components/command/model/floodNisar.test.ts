// L61: Flood risk + NISAR (contract t1b8 2026-11-15) — frame pick, water
// overlay, caption note, agreement sentence, and the plain pickers ignoring
// the tpi_flood_nisar family.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { pickTpiFrame } from "@/lib/layers";
import { agreementText, floodNisarFrame, floodNisarNote, hasFloodNisar, nisarAgreement, nisarWaterLayer } from "./floodNisar";
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
  expect(t.main).toBe("NISAR saw water in 28.0 km²; the index without NISAR had flagged 61% of it 12 days before the pass");
  expect(t.main).not.toContain("1 day before");
  expect(t.more).toBe("Pass Oct 12, 2026 · track 010D · Critical on Sep 30, 2026 · pass day: 40% (High or Critical) · NOAA GFS issued Sep 27, 2026 (15 days before): 33% Critical");
  expect(t.label).toBe("index without NISAR, up to 12 days before the pass");
  // flat keys only
  const flat = { ...uy, metricsView: { nisar_agreement_water_km2: 5, nisar_agreement_win_critical_hit_pct: 7.5, nisar_agreement_win_critical_days_before: 3 } } as unknown as CcBundle;
  expect(agreementText(nisarAgreement(flat)!).main).toBe("NISAR saw water in 5.0 km²; the index without NISAR had flagged 7.5% of it 3 days before the pass");
  // a list of pass records: the last one with a window is the headline
  const list = { ...uy, metricsView: { nisar_agreement: [(rich.metricsView as unknown as Record<string, unknown>).nisar_agreement, { water_km2: 1 }] } } as unknown as CcBundle;
  expect(nisarAgreement(list)!.waterKm2).toBe(28.04);
  expect(nisarAgreement({ ...uy, metricsView: {} } as unknown as CcBundle)).toBeUndefined();
});
