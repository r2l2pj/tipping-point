// L61 (Lucas): the default Flood view is "Flood risk + NISAR" — lead-analysis
// tpi_flood_nisar frames (the flood index with NISAR-observed water), with
// the water itself drawn on top as its own overlay (nisar_water_*), and the
// agreement metric from lead-ml on the Overview. Every piece falls back to
// the plain flood index when the data is not published.
//
// Contract (t1b8, 2026-11-15):
// - cases: per-date id 'tpi_flood_nisar' (same family as tpi_flood), png,
//   bounds, label, note, nisar_passes [{date, track, id}], nisar_water_km2.
//   Now: ids tpi_flood_nisar_d0..d3.
// - marking: kind 'nisar_extra', id 'nisar_water_<YYYYMMDD>' (cases) /
//   'nisar_water' (Now), one colour #08519c, pass_date, pass_track, age_days.
// - agreement: metrics cases[<case>].nisar_agreement (Now:
//   now/nisar_agreement.json) = {water_km2, observed:{alto_plus:{hit_pct}},
//   forecast:{lead_1d|lead_2d|lead_3d:{alto_plus:{hit_pct}}}, ...} + flat keys.
import type { ManifestLayer } from "@/lib/types";
import { fmtDate, fmtKm2 } from "./format";
import type { CcBundle } from "./types";

export const FLOOD_NISAR_LABEL = "Flood risk with NISAR-observed water (candidate)";
export const NISAR_WATER_LABEL = "Observed by NISAR (candidate water)";
export const NISAR_WATER_COLOR = "#08519c";

type L = ManifestLayer & Record<string, unknown>;
const layersOf = (b: CcBundle) => (b.manifest?.layers ?? []) as L[];
const day10 = (d: unknown) => (typeof d === "string" ? d.slice(0, 10) : "");

export const isFloodNisarId = (id: string): boolean => /^tpi_flood_nisar(_d\d)?$/.test(id);

export const hasFloodNisar = (bundle: CcBundle): boolean =>
  layersOf(bundle).some((l) => isFloodNisarId(l.id) && !!l.png);

// The flood + NISAR frame of the day (only days with a usable pass have one).
export function floodNisarFrame(bundle: CcBundle, day: string | undefined): ManifestLayer | undefined {
  if (!day) return undefined;
  return layersOf(bundle).find((l) => isFloodNisarId(l.id) && !!l.png && day10(l.date) === day.slice(0, 10));
}

// The pass the frame used (the latest ≤ day, at most 12 days old).
export function framePass(frame: ManifestLayer | undefined): { date: string; track?: string } | undefined {
  const ps = (frame as L | undefined)?.nisar_passes;
  if (!Array.isArray(ps) || !ps.length) return undefined;
  const last = [...ps].filter((p) => p && typeof p.date === "string").sort((a, b) => String(a.date).localeCompare(String(b.date))).pop();
  return last ? { date: day10(last.date), ...(typeof last.track === "string" ? { track: last.track } : {}) } : undefined;
}

// The water overlay for that frame: same date, else the frame's pass date
// (cases); Now ships one 'nisar_water'.
export function nisarWaterLayer(bundle: CcBundle, day: string | undefined, frame?: ManifestLayer): ManifestLayer | undefined {
  const water = layersOf(bundle).filter((l) => /^nisar_water(_\d{8})?(_.*)?$/.test(l.id) && !!l.png);
  if (!water.length) return undefined;
  if (bundle.mode === "live") return water.find((l) => l.id.startsWith("nisar_water")) ?? undefined;
  const d = day?.slice(0, 10);
  const pass = framePass(frame)?.date;
  return water.find((l) => d && day10(l.date) === d)
    ?? (pass ? water.find((l) => day10(l.pass_date) === pass || l.id.startsWith(`nisar_water_${pass.replaceAll("-", "")}`)) : undefined);
}

// Caption note for the chip: " + NISAR water (pass Oct 12, 2026)".
export function floodNisarNote(frame: ManifestLayer | undefined): string {
  if (!frame) return "";
  const p = framePass(frame);
  return ` + NISAR water${p ? ` (pass ${fmtDate(p.date)})` : ""}`;
}

// --- agreement metric (lead-ml) --------------------------------------------
export interface NisarAgreement { waterKm2?: number; observedHit?: number; leads: { k: 1 | 2 | 3; hit: number }[] }

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const rec = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
const hitOf = (v: unknown) => num(rec(rec(v)?.alto_plus)?.hit_pct);

export function nisarAgreement(bundle: CcBundle): NisarAgreement | undefined {
  const mv = rec(bundle.metricsView as unknown);
  if (!mv) return undefined;
  const a = rec(mv.nisar_agreement);
  const fc = rec(a?.forecast);
  const leads = ([1, 2, 3] as const).flatMap((k) => {
    const hit = hitOf(fc?.[`lead_${k}d`]) ?? num(mv[`nisar_agreement_fc${k}d_hit_pct`]);
    return hit === undefined ? [] : [{ k, hit }];
  });
  const out: NisarAgreement = {
    waterKm2: num(a?.water_km2) ?? num(mv.nisar_agreement_water_km2),
    observedHit: hitOf(a?.observed) ?? num(mv.nisar_agreement_obs_hit_pct),
    leads,
  };
  return out.waterKm2 !== undefined && (leads.length || out.observedHit !== undefined) ? out : undefined;
}

const pctText = (v: number) => `${v < 10 && v % 1 ? v.toFixed(1) : Math.round(v)}%`;
const daysText = (k: number) => `${k} ${k === 1 ? "day" : "days"} before`;

// "NISAR saw water in 28 km²; the index had flagged 12% of it 1 day before"
// (+ the other leads on a second line).
export function agreementText(a: NisarAgreement): { main: string; more?: string } {
  const water = `NISAR saw water in ${fmtKm2(a.waterKm2!)}`;
  const [first, ...rest] = a.leads;
  if (!first) return { main: `${water}; the index flagged ${pctText(a.observedHit!)} of it on the pass day` };
  return {
    main: `${water}; the index had flagged ${pctText(first.hit)} of it ${daysText(first.k)}`,
    ...(rest.length ? { more: rest.map((x) => `${daysText(x.k)}: ${pctText(x.hit)}`).join(" · ") } : {}),
  };
}
