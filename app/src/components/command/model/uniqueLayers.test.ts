// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { layerKey, uniqueLayerIds } from "./uniqueLayers";

test("duplicate nisar_extra ids (WA nisar_sme2 ×3) become unique, with a warning; frame families stay", () => {
  const warns: string[] = [];
  const out = uniqueLayerIds([
    { id: "nisar_sme2", kind: "nisar_extra", date: "2025-11-22", png: "a.png", track: "172D" },
    { id: "nisar_sme2", kind: "nisar_extra", date: "2025-12-04", png: "b.png", track: "172D" },
    { id: "nisar_sme2", kind: "nisar_extra", date: "2025-12-16", png: "c.png", track: "172D" },
    { id: "tpi_flood", date: "2025-12-04", png: "t1.png" },
    { id: "tpi_flood", date: "2025-12-05", png: "t2.png" },
    { id: "tpi_flood", date: "2025-12-05", png: "t2.png" }, // exact duplicate
  ], (m) => warns.push(m));
  expect(out.map((l) => l.id)).toEqual(["nisar_sme2", "nisar_sme2_20251204_172D", "nisar_sme2_20251216_172D", "tpi_flood", "tpi_flood"]);
  expect(new Set(out.filter((l) => l.kind).map((l) => l.id)).size).toBe(3);
  expect(warns).toHaveLength(3);
});

test("layerKey: id + date + track + pol (+ index) — never two equal keys for distinct layers", () => {
  const keys = [
    { id: "nisar_sme2", date: "2025-11-22", track: "172D" }, { id: "nisar_sme2", date: "2025-12-04", track: "172D" },
    { id: "x", date: "2025-12-04", polarization: "HH" }, { id: "x", date: "2025-12-04", polarization: "HV" },
  ].map((l) => layerKey(l));
  expect(new Set(keys).size).toBe(4);
});
