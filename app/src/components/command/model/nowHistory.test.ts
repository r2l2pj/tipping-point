// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { bundleForRun, parseNowHistory, runLabel } from "./nowHistory";
import { liveBundle } from "./fixtures";

const index = { schema: "tipping-point/now-history@1", runs: [
  { run: "2026-11-15T19:19:17Z", id: "20261115T191917Z", manifest: "now/history/20261115T191917Z/manifest.json" },
  { run: "2026-11-15T16:19:00Z", id: "20261115T161900Z", manifest: "now/history/20261115T161900Z/manifest.json" },
  { run: "2026-11-14T22:19:00Z", id: "20261114T221900Z", manifest: "now/history/20261114T221900Z/manifest.json" },
  { bad: true },
] };

test("parses the run list newest first; BRT labels, the day only when it differs", () => {
  const runs = parseNowHistory(index);
  expect(runs.map((r) => r.label)).toEqual(["16:19", "13:19", "Nov 14 19:19"]);
  expect(parseNowHistory({})).toEqual([]);
  expect(runLabel("bad")).toBe("bad");
});

test("a past run swaps only the map layers, PNGs under its run directory", () => {
  const [, run] = parseNowHistory(index);
  const b = liveBundle({ assetBase: "https://raw.example/data", manifest: { layers: [{ id: "tpi_flood_d0", date: "2026-11-15", png: "now/png/tpi_flood_d0.png" }] } as never });
  const out = bundleForRun(b, run, { layers: [{ id: "tpi_flood_d0", date: "2026-11-15", png: "png/tpi_flood_d0.png", cog: "cog/x.tif" }] });
  expect(out.manifest!.layers[0].png).toBe("https://raw.example/data/now/history/20261115T161900Z/png/tpi_flood_d0.png");
  expect(out.manifest!.layers[0].cog).toBeUndefined();
  expect(out.id).toBe(b.id);
  expect(bundleForRun(b, run, null)).toBe(b);
});
