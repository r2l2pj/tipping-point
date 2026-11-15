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
import { pickTpiFrame, tpiFamily } from "@/lib/layers";
import type { ManifestLayer } from "@/lib/types";
import { fmtDate, fmtKm2 } from "./format";
import type { CcBundle } from "./types";

export const FLOOD_NISAR_LABEL = "Flood risk with NISAR-observed water (candidate)";
// Lucas: NISAR water is RED everywhere (the published mask is blue #08519c;
// the map repaints it #ff1f1f at ~55 %, like the candidate polygons).
export const NISAR_WATER_LABEL = "NISAR-observed water (candidate)";

type L = ManifestLayer & Record<string, unknown>;
const layersOf = (b: CcBundle) => (b.manifest?.layers ?? []) as L[];
const day10 = (d: unknown) => (typeof d === "string" ? d.slice(0, 10) : "");

// lead-app2 d45c783: the risk frame families are matched by EXACT id family.
export const isFloodNisarId = (id: string): boolean => tpiFamily(id) === "flood_nisar";

export const hasFloodNisar = (bundle: CcBundle): boolean =>
  layersOf(bundle).some((l) => isFloodNisarId(l.id) && !!l.png);

// The flood + NISAR frame of the day (only days with a usable pass have one).
export function floodNisarFrame(bundle: CcBundle, day: string | undefined): ManifestLayer | undefined {
  if (!day) return undefined;
  // The shared picker, family 'flood_nisar': exact family, never a fallback.
  const l = pickTpiFrame(bundle.manifest?.layers, { date: day.slice(0, 10) }, "flood_nisar",
    bundle.series?.reference_date ?? bundle.reference_date);
  return l && isFloodNisarId(l.id) ? l : undefined;
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

// --- agreement metric (L62) ------------------------------------------------
// lead-nisar-uru / lead-ml (tools/nisar_pass_agreement.py): the headline pass
// record cases.<case>.nisar_agreement = {pass{date,track}, water_km2,
// observed{alto_plus|critico{hit_pct}}, window_12d{best_day{critico|alto_plus
// {day, days_before, hit_pct, area_pct, lift}}}, gfs_window_12d{best{critico
// {issued, issued_days_before, critico{hit_pct}}}}, headline_label, passes[]}
// + flat nisar_agreement_win_critical_hit_pct / _days_before / _best_day.
// The sentence reads window_12d (the index WITHOUT NISAR, best day up to 12
// days before the pass) — never forecast.lead_1d (rain leads the river).
export interface NisarAgreement {
  waterKm2: number;
  pass?: { date: string; track?: string };
  window?: { hit: number; daysBefore: number; day?: string; cls: "Critical" | "High or Critical" };
  passDayHit?: number; // the index on the pass day (High or Critical)
  gfs?: { hit: number; issued?: string; daysBefore?: number };
  label: string;
}

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const rec = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
const WINDOW_LABEL = "Index without NISAR, up to 12 days before the pass";

export function nisarAgreement(bundle: CcBundle): NisarAgreement | undefined {
  const mv = rec(bundle.metricsView as unknown);
  if (!mv) return undefined;
  const rawA = mv.nisar_agreement;
  // A list of pass records: the last one with a window is the headline.
  const a = Array.isArray(rawA)
    ? rec([...rawA].reverse().find((x) => rec(rec(x)?.window_12d)))
    : rec(rawA);
  // Conductor: the sentence names its own base — the single pass it scores.
  const water = num(a?.water_km2_single_pass) ?? num(mv.nisar_agreement_water_km2_single_pass)
    ?? num(a?.water_km2) ?? num(mv.nisar_agreement_water_km2);
  if (water === undefined) return undefined;
  const best = rec(rec(a?.window_12d)?.best_day);
  const crit = rec(best?.critico), high = rec(best?.alto_plus);
  const pickWin = (b: Record<string, unknown> | undefined, cls: "Critical" | "High or Critical") => {
    const hit = num(b?.hit_pct), k = num(b?.days_before);
    return hit !== undefined && k !== undefined
      ? { hit, daysBefore: k, cls, ...(typeof b?.day === "string" ? { day: b.day.slice(0, 10) } : {}) } : undefined;
  };
  const flatHit = num(mv.nisar_agreement_win_critical_hit_pct), flatK = num(mv.nisar_agreement_win_critical_days_before);
  const window = pickWin(crit, "Critical") ?? pickWin(high, "High or Critical")
    ?? (flatHit !== undefined && flatK !== undefined
      ? { hit: flatHit, daysBefore: flatK, cls: "Critical" as const,
          ...(typeof mv.nisar_agreement_win_critical_best_day === "string" ? { day: mv.nisar_agreement_win_critical_best_day.slice(0, 10) } : {}) }
      : undefined);
  const pass = rec(a?.pass) ?? (typeof mv.nisar_agreement_pass_date === "string" ? { date: mv.nisar_agreement_pass_date } : undefined);
  const g = rec(rec(rec(a?.gfs_window_12d)?.best)?.critico);
  const gHit = num(rec(g?.critico)?.hit_pct);
  const passDayHit = num(rec(rec(a?.observed)?.alto_plus)?.hit_pct);
  if (!window && passDayHit === undefined) return undefined;
  return {
    waterKm2: water,
    ...(pass && typeof pass.date === "string" ? { pass: { date: pass.date.slice(0, 10), ...(typeof pass.track === "string" ? { track: pass.track } : {}) } } : {}),
    ...(window ? { window } : {}),
    ...(passDayHit !== undefined ? { passDayHit } : {}),
    ...(gHit !== undefined ? { gfs: { hit: gHit, ...(typeof g?.issued === "string" ? { issued: g.issued.slice(0, 10) } : {}),
      ...(num(g?.issued_days_before) !== undefined ? { daysBefore: num(g?.issued_days_before) } : {}) } } : {}),
    label: typeof a?.headline_label === "string" && a.headline_label.trim() ? a.headline_label.trim() : WINDOW_LABEL,
  };
}

// Whole percent (one decimal under 1 %): "9%", "51%", "0.4%".
const pctText = (v: number) => `${v > 0 && v < 1 ? v.toFixed(1) : Math.round(v)}%`;
const daysText = (k: number) => `${k} ${k === 1 ? "day" : "days"}`;
// Headline areas: "1,106 km²" from 100 km² up, else one decimal ("2.0 km²").
export const km2Text = (v: number): string =>
  v >= 100 ? `${Math.round(v).toLocaleString("en-US")} km²` : fmtKm2(v);
const shortDay = (d: string) => fmtDate(d).replace(/,\s*\d{4}$/, "");

// Conductor: the sentence names its own base — "Of the 612 km² the Oct 12
// pass saw, the index without NISAR had flagged 9% as Critical 10 days
// before" + a details line (pass, track, pass day, NOAA GFS).
export function agreementText(a: NisarAgreement): { main: string; more?: string; label: string } {
  const base = `Of the ${km2Text(a.waterKm2)} ${a.pass ? `the ${shortDay(a.pass.date)} pass` : "NISAR"} saw`;
  const main = a.window
    ? `${base}, the index without NISAR had flagged ${pctText(a.window.hit)} as ${a.window.cls} ${daysText(a.window.daysBefore)} before`
    : `${base}, the index flagged ${pctText(a.passDayHit!)} as High or Critical on the pass day`;
  const more = [
    a.pass ? `Pass ${fmtDate(a.pass.date)}${a.pass.track ? ` · track ${a.pass.track}` : ""}` : undefined,
    a.window?.day ? `${a.window.cls} on ${fmtDate(a.window.day)}` : undefined,
    a.window && a.passDayHit !== undefined ? `pass day: ${pctText(a.passDayHit)} (High or Critical)` : undefined,
    a.gfs ? `NOAA GFS${a.gfs.issued ? ` issued ${fmtDate(a.gfs.issued)}` : ""}${a.gfs.daysBefore !== undefined ? ` (${daysText(a.gfs.daysBefore)} before)` : ""}: ${pctText(a.gfs.hit)} Critical` : undefined,
  ].filter(Boolean).join(" · ");
  return { main, label: a.label, ...(more ? { more } : {}) };
}

// --- NISAR card headline (conductor, L61) -------------------------------------
// "NISAR saw water in 1,106 km² (passes Sep 30–Oct 12); 822 km² on the
// floodplain raised the flood map" — the water on the served flood + NISAR
// map (every pass ≤ 12 days, full case box; metrics nisar_water_km2_case_box,
// raised = the HAND ≤ 5 m floodplain part, nisar_raised_km2_floodplain).
// A case without that map record (WA today) says what its single scored pass
// saw. Replaces the old teammate-polygon flooded area on the Radar card.
export interface NisarWaterHeadline {
  waterKm2: number;
  raisedKm2?: number;
  passes: { date: string; track?: string }[];
  text: string;
}
const passRange = (ds: string[]): string => {
  if (ds.length <= 1) return ds[0] ? shortDay(ds[0]) : "";
  const [a, z] = [ds[0], ds[ds.length - 1]];
  return a.slice(0, 7) === z.slice(0, 7) ? `${shortDay(a)}–${Number(z.slice(8, 10))}` : `${shortDay(a)}–${shortDay(z)}`;
};
export function nisarWaterHeadline(bundle: CcBundle): NisarWaterHeadline | undefined {
  const mv = rec(bundle.metricsView as unknown);
  if (!mv) return undefined;
  const rawA = mv.nisar_agreement;
  const a = Array.isArray(rawA) ? rec([...rawA].reverse().find((x) => rec(rec(x)?.window_12d))) : rec(rawA);
  const map = rec(a?.map_12d);
  const parsePass = (v: unknown): { date: string; track?: string } | undefined => {
    if (typeof v === "string") { const [date, track] = v.split("/"); return /^\d{4}-\d{2}-\d{2}/.test(date) ? { date: date.slice(0, 10), ...(track ? { track } : {}) } : undefined; }
    const r = rec(v);
    return r && typeof r.date === "string" ? { date: r.date.slice(0, 10), ...(typeof r.track === "string" ? { track: r.track } : {}) } : undefined;
  };
  let water = num(mv.nisar_water_km2_case_box) ?? num(map?.water_km2_case_box);
  const raised = water !== undefined ? num(mv.nisar_raised_km2_floodplain) ?? num(map?.raised_km2_floodplain) : undefined;
  let passes = (Array.isArray(map?.passes) ? map!.passes as unknown[] : []).map(parsePass).filter((p): p is { date: string; track?: string } => !!p);
  if (water === undefined) {
    water = num(a?.water_km2_single_pass) ?? num(mv.nisar_agreement_water_km2_single_pass) ?? num(a?.water_km2) ?? num(mv.nisar_agreement_water_km2);
    const p = parsePass(a?.pass) ?? parsePass(mv.nisar_agreement_pass_date);
    passes = p ? [p] : [];
  }
  if (water === undefined) return undefined;
  passes.sort((x, y) => x.date.localeCompare(y.date));
  const range = passRange(passes.map((p) => p.date));
  const text = `NISAR saw water in ${km2Text(water)}${range ? ` (${passes.length > 1 ? "passes" : "pass"} ${range})` : ""}${
    raised !== undefined ? `; ${km2Text(raised)} on the floodplain raised the flood map` : ""}`;
  return { waterKm2: water, ...(raised !== undefined ? { raisedKm2: raised } : {}), passes, text };
}
