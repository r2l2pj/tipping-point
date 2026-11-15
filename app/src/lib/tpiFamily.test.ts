// Conductor (L61): risk frames are picked by EXACT id family, never by
// substring — "tpi_flood" is a prefix of "tpi_flood_nisar".
// @ts-expect-error -- bun:test resolves under `bun test`; bun-types isn't a
// dependency so tsc can't see its types.
import { describe, expect, test } from "bun:test";

import { pickTpiFrame, tpiFamily } from "./layers";
import type { ManifestLayer } from "./types";

const L = (id: string, date: string, extra: Record<string, unknown> = {}) =>
  ({ id, date, png: `${id}_${date}.png`, ...extra }) as unknown as ManifestLayer;

describe("tpiFamily", () => {
  test("exact families of every shipped id shape", () => {
    expect(tpiFamily("tpi_flood")).toBe("flood");
    expect(tpiFamily("tpi_flood_d2")).toBe("flood");
    expect(tpiFamily("tpi_flood_gfs_d3")).toBe("flood");
    expect(tpiFamily("tpi_flood_6h")).toBe("flood");
    expect(tpiFamily("tpi_flood_gfs_6h")).toBe("flood");
    expect(tpiFamily("tpi_slide")).toBe("slide");
    expect(tpiFamily("tpi_slide_gfs_d1")).toBe("slide");
    expect(tpiFamily("tpi_flood_nisar")).toBe("flood_nisar");
    expect(tpiFamily("tpi_flood_nisar_d0")).toBe("flood_nisar");
    expect(tpiFamily("tpi_floodx")).toBeUndefined();
    expect(tpiFamily("nisar_water")).toBeUndefined();
    expect(tpiFamily("flood_confirmed")).toBeUndefined();
  });
});

describe("pickTpiFrame with flood AND flood + NISAR frames on the same day", () => {
  // The NISAR frames come FIRST, so order can never hide a substring match.
  const replay = [
    L("tpi_flood_nisar", "2025-12-08"),
    L("nisar_water_20251208", "2025-12-08"),
    L("tpi_flood", "2025-12-08"),
    L("tpi_slide", "2025-12-08"),
    L("tpi_flood_nisar", "2025-12-09"),
    L("tpi_slide", "2025-12-09"),
  ];
  const day = { date: "2025-12-08", kind: "observed" };

  test("flood / slide / null never return tpi_flood_nisar", () => {
    expect(pickTpiFrame(replay, day, "flood")?.id).toBe("tpi_flood");
    expect(pickTpiFrame(replay, day, "slide")?.id).toBe("tpi_slide");
    expect(pickTpiFrame(replay, day, null)?.id).not.toBe("tpi_flood_nisar");
    // A day with only the NISAR variant + slide: flood falls back to a plain
    // family (slide), never to flood + NISAR.
    expect(pickTpiFrame(replay, { date: "2025-12-09" }, "flood")?.id).toBe("tpi_slide");
  });

  test("flood_nisar returns that family only, never a fallback", () => {
    expect(pickTpiFrame(replay, day, "flood_nisar")?.id).toBe("tpi_flood_nisar");
    expect(pickTpiFrame([L("tpi_flood", "2025-12-10")], { date: "2025-12-10" }, "flood_nisar")).toBeUndefined();
  });

  test("Now ids (tpi_flood_nisar_d<k>) on a forecast day", () => {
    const now = [
      L("tpi_flood_nisar_d1", "2026-11-16", { forecast: true }),
      L("tpi_flood_d1", "2026-11-16", { forecast: true }),
      L("tpi_slide_d1", "2026-11-16", { forecast: true }),
    ];
    const fd = { date: "2026-11-16", kind: "forecast" };
    expect(pickTpiFrame(now, fd, "flood")?.id).toBe("tpi_flood_d1");
    expect(pickTpiFrame(now, fd, null)?.id).toBe("tpi_flood_d1");
    expect(pickTpiFrame(now, fd, "flood_nisar")?.id).toBe("tpi_flood_nisar_d1");
  });
});
