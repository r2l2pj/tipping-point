// Jhonata's "river's dance" (lead-ml series/river_dance.json): NISAR
// new-water area per pass vs the Uruguay River stage at the Uruguaiana gauge
// (9 passes, Pearson r ≈ 0.92). Slimmed on the server; the card draws it.
export interface DancePass { date: string; track?: string; stageM: number | null; newWaterKm2: number | null }
export interface RiverDance { passes: DancePass[]; floodStageM?: number; r?: number; boxUtm21s?: number[] }

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const rec = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;

export function slimRiverDance(raw: unknown): RiverDance | undefined {
  const r = rec(raw);
  if (!r || !Array.isArray(r.passes)) return undefined;
  const passes = r.passes.flatMap((p): DancePass[] => {
    const x = rec(p);
    if (!x || typeof x.date !== "string") return [];
    return [{
      date: x.date.slice(0, 10),
      ...(typeof x.track === "string" ? { track: x.track } : {}),
      stageM: num(x.stage_m),
      newWaterKm2: num(x.new_water_km2),
    }];
  }).sort((a, b) => a.date.localeCompare(b.date));
  if (passes.filter((p) => p.newWaterKm2 !== null).length < 2) return undefined;
  const corr = num(r.pearson_stage_newwater) ?? num(r.pearson_r) ?? num(r.r);
  const fs = num(r.flood_stage_m);
  const box = Array.isArray(r.box_utm21s) && r.box_utm21s.length === 4 && r.box_utm21s.every((v) => typeof v === "number") ? (r.box_utm21s as number[]) : undefined;
  return { passes, ...(fs !== null ? { floodStageM: fs } : {}), ...(corr !== null ? { r: corr } : {}), ...(box ? { boxUtm21s: box } : {}) };
}

// "r = 0.92 across 9 passes" (pairs with both values).
export function danceCaption(d: RiverDance): string | undefined {
  if (d.r === undefined) return undefined;
  const n = d.passes.filter((p) => p.stageM !== null && p.newWaterKm2 !== null).length;
  return `r = ${d.r.toFixed(2)} across ${n} passes`;
}
