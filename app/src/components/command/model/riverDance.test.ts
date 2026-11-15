// Jhonata's river's dance (schema ~/code/r2l2pj/dance.json, lead-ml).
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import RiverDanceChart from "../RiverDanceChart";
import { danceCaption, slimRiverDance } from "./riverDance";

const RAW = {"flood_stage_m": 8.5, "pearson_stage_newwater": 0.918, "passes": [{"date": "2026-09-18", "track": "010D", "stage_m": 7.5, "new_water_km2": 12.1}, {"date": "2026-09-23", "track": "082D", "stage_m": 5.45, "new_water_km2": 2.31}, {"date": "2026-09-30", "track": "010D", "stage_m": 6.29, "new_water_km2": 2.3}, {"date": "2026-10-05", "track": "082D", "stage_m": 8.53, "new_water_km2": 14.34}, {"date": "2026-10-12", "track": "010D", "stage_m": 9.8, "new_water_km2": 35.55}, {"date": "2026-10-17", "track": "082D", "stage_m": 9.99, "new_water_km2": 31.29}, {"date": "2026-10-24", "track": "010D", "stage_m": 7.065, "new_water_km2": 12.39}, {"date": "2026-10-29", "track": "082D", "stage_m": 8.555, "new_water_km2": 21.23}, {"date": "2026-11-05", "track": "010D", "stage_m": 5.728, "new_water_km2": 10.71}]};

test("slim: 9 passes sorted, flood stage, r; caption 'r = 0.92 across 9 passes'", () => {
  const d = slimRiverDance(RAW)!;
  expect(d.passes).toHaveLength(9);
  expect(d.passes[4]).toEqual({ date: "2026-10-12", track: "010D", stageM: 9.8, newWaterKm2: 35.55 });
  expect(d.floodStageM).toBe(8.5);
  expect(danceCaption(d)).toBe("r = 0.92 across 9 passes");
  expect(slimRiverDance({ passes: [] })).toBeUndefined();
  expect(slimRiverDance(null)).toBeUndefined();
});

test("chart: one clickable bar per pass, flood-stage dashed line, amber border above flood stage", () => {
  const d = slimRiverDance(RAW)!;
  const html = renderToStaticMarkup(createElement(RiverDanceChart, { d, onPass: () => {}, selected: "2026-10-12" }));
  expect(html.match(/data-dance-bar=/g)).toHaveLength(9);
  expect(html).toContain("r = 0.92 across 9 passes");
  expect(html).toContain("data-dance-flood-stage");
  expect(html).toContain('aria-pressed="true"');
  // above 8.5 m: Oct 5 (8.53), Oct 12 (9.8), Oct 17 (9.99), Oct 29 (8.555)
  expect(html.match(/data-dance-above="true"/g)).toHaveLength(4);
  expect(html).toContain("Show the NISAR pass of Oct 12, 2026 · track 010D: 35.55 km² new water, river 9.8 m");
});
