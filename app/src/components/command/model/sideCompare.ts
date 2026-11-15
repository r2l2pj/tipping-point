// Lucas (super urgent): ONE Compare ⇆ — a LEFT and a RIGHT side, each its own
// layer (TPI flood/slide risk, NASA GPM IMERG rain, the ML landslide
// likelihood, a NISAR pass, NASA GIBS satellite), its own day and — for the
// risk layers — Observed | Forecast issued k days before. Any day vs any
// other day; never both sides identical.
import {
  assetUrl,
  horizonLabel,
  isHorizonKey,
  probFrame,
  probHorizonsAvailable,
  type ProbHorizon,
} from "@/lib/layers";
import type { ManifestLayer } from "@/lib/types";
import { gibsTiles } from "./basemap";
import { fmtDate, fmtDayShort } from "./format";
import { hasGfsFrames } from "./forecastCompare";
import { caseDirOf, productUrl } from "./lazy";
import {
  floodLabels,
  nisarPairDays,
  nisarTracks,
  sensorName,
  sensorOf,
  type CompareOption,
  type CompareSide,
} from "./radar";
import {
  effectiveLead,
  forecastForDay,
  forecastLeadsForDay,
  frameFor,
  ISSUE_LEADS,
  shiftDay,
  type IssueLead,
  type Step,
} from "./steps";
import { HAZARD_LABEL, type CcBundle, type Hazard } from "./types";

type BBox = [number, number, number, number];

// Per-side layer choice. 'flood'/'slide' are the TPI risk family (Observed |
// Forecast issued k days before); 'rain' is the observed IMERG raster; 'ml'
// the Now P(trigger) horizon frames; 'nisar' a pass (+ pol, + track in Now);
// 'satellite' the NASA GIBS true-colour tiles of the day.
export type CompareLayer = "flood" | "slide" | "rain" | "ml" | "nisar" | "satellite";
export const isRiskLayer = (l: CompareLayer): l is "flood" | "slide" => l === "flood" || l === "slide";

export const COMPARE_LAYER_LABEL: Record<CompareLayer, string> = {
  flood: "Flood risk (TPI)",
  slide: "Landslide risk (TPI)",
  rain: "Rain (NASA GPM IMERG)",
  ml: "Landslide likelihood (ML)",
  nisar: "NISAR radar",
  satellite: "Satellite (NASA GIBS VIIRS)",
};

export interface Side {
  layer: CompareLayer;
  date: string;              // YYYY-MM-DD
  lead?: IssueLead;          // flood|slide — 0 = observed (the day's own frame)
  pol?: "HH" | "HV";         // nisar
  track?: string;            // nisar (Now ships one stack per track)
  horizon?: ProbHorizon;     // ml
}
export interface Sides { L: Side; R: Side }
export const bothNisar = (s: Sides | undefined): boolean =>
  !!s && s.L.layer === "nisar" && s.R.layer === "nisar";

export const sameSide = (a: Side, b: Side): boolean =>
  a.layer === b.layer &&
  a.date.slice(0, 10) === b.date.slice(0, 10) &&
  (a.lead ?? 0) === (b.lead ?? 0) &&
  (a.pol ?? "HH") === (b.pol ?? "HH") &&
  (a.track ?? "") === (b.track ?? "") &&
  (a.horizon ?? "") === (b.horizon ?? "");

const short = (d: string) => fmtDate(d).replace(/,\s*\d{4}$/, "");
const day10 = (d: string | undefined) => (d ?? "").slice(0, 10);
const ms = (d: string) => new Date(`${day10(d)}T12:00:00Z`).getTime();

// Nearest listed day to `day` (ties: the earlier one); undefined when the
// layer has no days at all.
const nearestDay = (days: readonly string[], day: string): string | undefined => {
  let best: string | undefined;
  let bd = Infinity;
  for (const d of days) {
    const dist = Math.abs(ms(d) - ms(day));
    if (dist < bd || (dist === bd && d < (best ?? d))) { best = d; bd = dist; }
  }
  return best;
};

const unionBounds = (a?: BBox, b?: BBox): BBox | undefined =>
  a && b
    ? [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]
    : (a ?? b);

// --- per-layer day lists -----------------------------------------------------
//
// Each side's day list depends on its layer: only dates that exist.

function riskDays(bundle: CcBundle, steps: readonly Step[], hz: Hazard): { date: string; label: string }[] {
  return steps
    .filter((s) => frameFor(bundle, s.date, hz) || forecastLeadsForDay(bundle, s.date, hz).length > 0)
    .map((s) => ({ date: day10(s.date), label: `${fmtDate(s.date)}${s.forecast ? " (forecast)" : ""}` }));
}

const imergDate = (l: ManifestLayer): string | undefined => {
  if (typeof l.date === "string") return day10(l.date);
  const m = /^imerg_(\d{4})(\d{2})(\d{2})$/.exec(l.id) ?? /^imerg_(\d{4}-\d{2}-\d{2})$/.exec(l.id);
  return m ? (m.length === 4 ? `${m[1]}-${m[2]}-${m[3]}` : m[1]) : undefined;
};
const imergLayers = (bundle: CcBundle): ManifestLayer[] =>
  (bundle.manifest?.layers ?? []).filter((l) => l.id.startsWith("imerg_") && !!l.png);

function rainDays(bundle: CcBundle): { date: string; label: string }[] {
  const days = [...new Set(imergLayers(bundle).map(imergDate).filter((d): d is string => !!d))].sort();
  return days.map((d) => ({ date: d, label: fmtDate(d) }));
}

const probDates = (bundle: CcBundle): string[] =>
  [...new Set(
    (bundle.manifest?.layers ?? [])
      .filter((l) => l.id.startsWith("p_trigger") && !!l.png && typeof l.date === "string")
      .map((l) => day10(l.date))
  )].sort();

function mlDays(bundle: CcBundle): { date: string; label: string }[] {
  return probDates(bundle).map((d) => ({ date: d, label: fmtDate(d) }));
}

// NISAR pass days for a side: the dates of its track (Now offers every
// track, replays stay on the main track as before); without a nisar_frame
// stack the pair's own dates.
// Lucas: EVERY NISAR pass on each side, any order, any track — one list
// "Sep 23, 2026 · 082D · 4005" (value date|track). Only identical picks are
// blocked; a different track gets a non-blocking note (sidesCompare).
export function nisarSideDays(bundle: CcBundle, _track?: string): { date: string; label: string; value?: string; track?: string }[] {
  void _track;
  const tracks = nisarTracks(bundle);
  if (!tracks.length) return nisarPairDays(bundle).map((d) => ({ date: d, label: fmtDate(d) }));
  const out: { date: string; label: string; value: string; track?: string }[] = [];
  for (const t of tracks) for (const d of t.dates) {
    const f = t.frames[d];
    const mode = (f?.HH?.mode ?? f?.HV?.mode)?.split("_")[0];
    out.push({ date: d, track: t.track || undefined, value: `${d}|${t.track}`,
      label: `${fmtDate(d)}${t.track ? ` · ${t.track}` : ""}${mode ? ` · ${mode}` : ""}` });
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || (a.track ?? "").localeCompare(b.track ?? ""));
}

// Tracks the case offers: all of them (Lucas: any pass on either side).
const offeredTracks = (bundle: CcBundle) => nisarTracks(bundle);
// The default pair stays on the main track (most passes: 010D Uruguaiana).
export const mainTrackDays = (bundle: CcBundle): string[] =>
  nisarTracks(bundle)[0]?.dates ?? nisarPairDays(bundle);

const nisarDaysAll = (bundle: CcBundle): string[] => {
  const t = offeredTracks(bundle);
  if (t.length) return [...new Set(t.flatMap((x) => x.dates))].sort();
  return nisarPairDays(bundle);
};

// NISAR pols the case ships (stack frames or the pair layers).
function nisarPols(bundle: CcBundle): ("HH" | "HV")[] {
  const tracks = offeredTracks(bundle);
  const pols = new Set<"HH" | "HV">();
  for (const t of tracks) for (const d of t.dates) {
    if (t.frames[d].HH) pols.add("HH");
    if (t.frames[d].HV) pols.add("HV");
  }
  if (!tracks.length) {
    const ids = new Set((bundle.manifest?.layers ?? []).filter((l) => !!l.png).map((l) => l.id));
    if (ids.has("nisar_pre") || ids.has("nisar_post") || ids.has("radar_pre") || ids.has("radar_post")) pols.add("HH");
    if (ids.has("nisar_hv_pre") || ids.has("nisar_hv_post")) pols.add("HV");
  }
  return [...pols].sort();
}

// Satellite (NASA GIBS): every calendar day exists; the picker lists the
// days the case knows (timeline steps + any manifest layer date).
function satelliteDays(bundle: CcBundle, steps: readonly Step[]): { date: string; label: string }[] {
  const ds = new Set(steps.map((s) => day10(s.date)));
  for (const l of bundle.manifest?.layers ?? []) {
    if (typeof l.date === "string") ds.add(day10(l.date));
  }
  return [...ds].filter(Boolean).sort().map((d) => ({ date: d, label: fmtDate(d) }));
}

export function sideDays(
  bundle: CcBundle,
  steps: readonly Step[],
  hazard: Hazard,
  s: Side
): { date: string; label: string }[] {
  switch (s.layer) {
    case "flood":
    case "slide":
      return riskDays(bundle, steps, s.layer);
    case "rain":
      return rainDays(bundle);
    case "ml":
      return mlDays(bundle);
    case "nisar":
      return nisarSideDays(bundle, s.track);
    case "satellite":
      return satelliteDays(bundle, steps);
  }
}

// --- the layers a case offers ------------------------------------------------

export interface CompareLayerInfo {
  id: CompareLayer;
  label: string;
  days: { date: string; label: string }[];
  canForecast?: boolean;                    // risk layers, replay with GFS runs
  pols?: ("HH" | "HV")[];                   // nisar
  tracks?: { id: string; days: string[] }[]; // nisar — only when >1 (Now)
  horizons?: ProbHorizon[];                 // ml
}

export function compareLayers(
  bundle: CcBundle,
  steps: readonly Step[],
  hazard: Hazard
): CompareLayerInfo[] {
  const out: CompareLayerInfo[] = [];
  const live = bundle.mode === "live";
  const canForecast = !live && hasGfsFrames(bundle);
  for (const hz of [hazard, hazard === "flood" ? "slide" : "flood"] as Hazard[]) {
    const days = riskDays(bundle, steps, hz);
    if (days.length) {
      out.push({ id: hz, label: COMPARE_LAYER_LABEL[hz], days, ...(canForecast ? { canForecast: true } : {}) });
    }
  }
  const rain = rainDays(bundle);
  if (rain.length) out.push({ id: "rain", label: COMPARE_LAYER_LABEL.rain, days: rain });
  if (live) {
    const horizons = probHorizonsAvailable(bundle.manifest?.layers);
    const days = mlDays(bundle);
    // Horizon-keyed frames, or the legacy single p_trigger family.
    if (days.length && (horizons.length || probFrame(bundle.manifest?.layers, "24h"))) {
      out.push({ id: "ml", label: COMPARE_LAYER_LABEL.ml, days, horizons: horizons.length ? horizons : ["24h"] });
    }
  }
  const ndays = nisarDaysAll(bundle);
  if (ndays.length) {
    const tracks = offeredTracks(bundle);
    const pols = nisarPols(bundle);
    out.push({
      id: "nisar",
      label: COMPARE_LAYER_LABEL.nisar,
      days: nisarSideDays(bundle),
      ...(pols.length ? { pols } : {}),
      ...(tracks.length > 1 ? { tracks: tracks.map((t) => ({ id: t.track, days: t.dates })) } : {}),
    });
  }
  const sat = satelliteDays(bundle, steps);
  if (sat.length) out.push({ id: "satellite", label: COMPARE_LAYER_LABEL.satellite, days: sat });
  return out;
}

// The layer the map is on → the compare's default layer: rain view → rain,
// the ML likelihood view → ml, the radar flood view → nisar, else the
// current hazard's TPI.
export function viewCompareLayer(
  bundle: CcBundle,
  steps: readonly Step[],
  on: readonly string[],
  hazard: Hazard
): CompareLayer | undefined {
  const avail = new Set(compareLayers(bundle, steps, hazard).map((l) => l.id));
  if (on.includes("imerg") && avail.has("rain")) return "rain";
  if (on.includes("ptrigger") && avail.has("ml")) return "ml";
  // The risk index wins when it is on (the NISAR outlines are drawn over it);
  // a radar-only view compares NISAR passes.
  if (on.includes("tpi") && avail.has(hazard)) return hazard;
  if (on.includes("flood") && avail.has("nisar")) return "nisar";
  if (avail.has(hazard)) return hazard;
  return [...avail][0];
}

// Fill defaults and clamp the day to the side's layer day list (the day a
// deep link or a layer switch names may not exist on it). Layers the case
// does not offer keep their date: the resolver's missing line names it.
export function normSide(bundle: CcBundle, steps: readonly Step[], hazard: Hazard, s: Side): Side {
  const days = sideDays(bundle, steps, hazard, s).map((d) => d.date);
  const day = day10(s.date);
  const date = days.includes(day) ? day : (nearestDay(days, day) ?? day);
  const out: Side = { ...s, date };
  if (isRiskLayer(s.layer)) out.lead = (s.lead ?? 0) as IssueLead;
  if (s.layer === "nisar") {
    out.pol = s.pol === "HV" ? "HV" : "HH";
    // A pass is date + track: keep the side's track when it flew that day,
    // else the track that did.
    const passes = nisarSideDays(bundle).filter((d) => d.date === date);
    if (passes.length && !passes.some((d) => (d.track ?? "") === (s.track ?? ""))) out.track = passes[0].track;
  }
  if (s.layer === "ml") out.horizon = s.horizon ?? probHorizonsAvailable(bundle.manifest?.layers)[0] ?? "24h";
  return out;
}

// --- defaults ----------------------------------------------------------------

// RIGHT = the selected day; LEFT follows the layer (3 days earlier on the
// risk/satellite days, the nisar pair around the day, the next ML horizon).
export function defaultSides(
  bundle: CcBundle,
  steps: readonly Step[],
  dayIndex: number,
  hazard: Hazard,
  issueLead: IssueLead,
  opts: { layer?: CompareLayer; horizon?: ProbHorizon; pol?: "HH" | "HV" } = {}
): Sides | undefined {
  const sel = steps[dayIndex] ?? steps[steps.length - 1];
  if (!sel) return undefined;
  const day = day10(sel.date);
  const offered = compareLayers(bundle, steps, hazard);
  const want = opts.layer && offered.some((l) => l.id === opts.layer)
    ? opts.layer
    : offered.some((l) => l.id === hazard)
      ? hazard
      : offered[0]?.id;
  if (!want) return undefined;
  const riskFallback = (): Side => {
    const days = riskDays(bundle, steps, hazard).map((d) => d.date);
    return { layer: hazard, date: nearestDay(days, shiftDay(day, -3)) ?? days[0] ?? day, lead: 0 };
  };
  let out: Sides | undefined;
  if (isRiskLayer(want)) {
    const days = riskDays(bundle, steps, want).map((d) => d.date);
    const R: Side = { layer: want, date: nearestDay(days, day) ?? day, lead: 0 };
    const L: Side = issueLead > 0
      ? { layer: want, date: R.date, lead: issueLead }
      : { layer: want, date: nearestDay(days, shiftDay(R.date, -3)) ?? days.find((d) => d !== R.date) ?? R.date, lead: 0 };
    out = { L, R };
  } else if (want === "nisar") {
    const days = mainTrackDays(bundle);
    const def = nisarDefaults(days, day);
    const pol = opts.pol ?? "HH";
    const track = offeredTracks(bundle)[0]?.track || undefined;
    out = {
      L: { layer: "nisar", date: def.before ?? days[0] ?? day, pol, track },
      R: { layer: "nisar", date: def.after ?? days[days.length - 1] ?? day, pol, track },
    };
  } else if (want === "ml") {
    const days = probDates(bundle);
    const h = opts.horizon ?? probHorizonsAvailable(bundle.manifest?.layers)[0] ?? "24h";
    const R: Side = { layer: "ml", date: nearestDay(days, day) ?? days[days.length - 1] ?? day, horizon: h };
    const other = (probHorizonsAvailable(bundle.manifest?.layers).length
      ? probHorizonsAvailable(bundle.manifest?.layers)
      : ["24h"]).find((x) => x !== h);
    out = { L: other ? { layer: "ml", date: R.date, horizon: other } : riskFallback(), R };
  } else {
    // rain / satellite: right = the listed day nearest the selected day,
    // left = the listed day before it (3 days back for satellite).
    const days = sideDays(bundle, steps, hazard, { layer: want, date: day }).map((d) => d.date);
    const r = nearestDay(days, day) ?? days[days.length - 1];
    if (!r) return undefined;
    const before = [...days].reverse().find((d) => d < r)
      ?? (want === "satellite" ? nearestDay(days, shiftDay(r, -3)) : undefined);
    out = before && before !== r
      ? { L: { layer: want, date: before }, R: { layer: want, date: r } }
      : { L: riskFallback(), R: { layer: want, date: r } };
  }
  if (!out) return undefined;
  // Never identical: nudge LEFT to another listed day when it collapsed.
  if (sameSide(out.L, out.R)) {
    const cur = out;
    const days = sideDays(bundle, steps, hazard, cur.L).map((d) => d.date);
    const alt = days.find((d) => d !== cur.L.date);
    if (alt) out = { ...cur, L: { ...cur.L, date: alt } };
  }
  return out;
}

// --- presets (one-tap chips) -------------------------------------------------

export type ComparePresetId = "forecast-observed" | "nisar-pair" | "day-before";
export const COMPARE_PRESETS: { id: ComparePresetId; label: string }[] = [
  { id: "forecast-observed", label: "Forecast vs what happened" },
  { id: "nisar-pair", label: "NISAR before/after" },
  { id: "day-before", label: "Day before vs day of" },
];

// The sides a preset produces; undefined = the case/day cannot do it and
// the chip hides.
export function presetSides(
  bundle: CcBundle,
  steps: readonly Step[],
  dayIndex: number,
  hazard: Hazard,
  id: ComparePresetId
): Sides | undefined {
  const sel = steps[dayIndex] ?? steps[steps.length - 1];
  const day = day10(sel?.date);
  if (!day) return undefined;
  switch (id) {
    case "forecast-observed": {
      if (!riskDays(bundle, steps, hazard).length) return undefined;
      const lead = effectiveLead(bundle, day, hazard, 3);
      if (lead === 0) return undefined;
      return { L: { layer: hazard, date: day, lead }, R: { layer: hazard, date: day, lead: 0 } };
    }
    case "nisar-pair": {
      const track = offeredTracks(bundle)[0]?.track || undefined;
      const days = mainTrackDays(bundle);
      if (days.length < 2) return undefined;
      const def = nisarDefaults(days, day);
      if (!def.before || !def.after || def.before === def.after) return undefined;
      return {
        L: { layer: "nisar", date: def.before, pol: "HH", track },
        R: { layer: "nisar", date: def.after, pol: "HH", track },
      };
    }
    case "day-before": {
      const days = riskDays(bundle, steps, hazard).map((d) => d.date);
      const r = nearestDay(days, day);
      if (!r) return undefined;
      const l = [...days].reverse().find((d) => d < r);
      return l ? { L: { layer: hazard, date: l, lead: 0 }, R: { layer: hazard, date: r, lead: 0 } } : undefined;
    }
  }
}

// --- deep links --------------------------------------------------------------

// `<layer>:<YYYY-MM-DD>[:<lead|pol|horizon>[:<track>]]` — the ?compare=1
// spec (L=…&R=…). Unknown extras fail the whole spec → defaults.
const LAYER_ALIASES: Record<string, CompareLayer> = {
  flood: "flood",
  slide: "slide",
  rain: "rain",
  imerg: "rain",
  ml: "ml",
  ptrigger: "ml",
  prob: "ml",
  nisar: "nisar",
  radar: "nisar",
  satellite: "satellite",
  sat: "satellite",
  gibs: "satellite",
  viirs: "satellite",
};
export function parseSideSpec(raw: string | undefined): Side | undefined {
  const parts = (raw ?? "").trim().split(":");
  const layer = LAYER_ALIASES[(parts[0] ?? "").toLowerCase()];
  const date = parts[1] ?? "";
  if (!layer || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return undefined;
  const extra = (parts[2] ?? "").trim();
  const s: Side = { layer, date };
  if (isRiskLayer(layer)) {
    if (extra === "" || /^obs/i.test(extra)) s.lead = 0;
    else if (/^d?([1-3])$/.test(extra)) s.lead = Number(extra.replace("d", "")) as IssueLead;
    else return undefined;
  } else if (layer === "nisar") {
    const up = extra.toUpperCase();
    if (up === "HH" || up === "HV" || up === "") { if (up) s.pol = up as "HH" | "HV"; }
    else return undefined;
    const track = (parts[3] ?? "").trim();
    if (track) s.track = track.toUpperCase();
  } else if (layer === "ml") {
    if (extra === "") { /* horizon filled by normSide */ }
    else if (isHorizonKey(extra)) s.horizon = extra;
    else return undefined;
  } else if (extra) {
    return undefined;
  }
  return s;
}

// --- per-side resolution -----------------------------------------------------

const imergLayerFor = (bundle: CcBundle, day: string): ManifestLayer | undefined =>
  imergLayers(bundle).find((l) => imergDate(l) === day);

// "Sep 28–30" from the layer's window (same month); else the layer's date.
const imergWhen = (l: ManifestLayer | undefined, day: string): string => {
  const w = (l as ManifestLayer & { window?: unknown } | undefined)?.window;
  if (Array.isArray(w) && w.length === 2 && w.every((x) => typeof x === "string")) {
    const [a, z] = w as [string, string];
    return a.slice(0, 7) === z.slice(0, 7)
      ? `${short(a)}–${Number(z.slice(8, 10))}`
      : `${short(a)}–${short(z)}`;
  }
  return short(l?.date ?? day);
};

// The NISAR frame of a side: its track's frame on the day (preferred pol,
// the other pol as fallback — a date may ship only one), else the fixed
// pair layer of the day for cases without a stack.
function nisarFrameFor(
  bundle: CcBundle,
  s: Side
): { frame?: CompareSide; pol: "HH" | "HV"; track?: string; name: string } {
  const want = s.pol === "HV" ? "HV" : "HH";
  const day = day10(s.date);
  const tracks = nisarTracks(bundle);
  if (tracks.length) {
    const t = (s.track ? tracks.find((x) => x.track === s.track) : undefined) ?? tracks[0];
    const f = t.frames[day];
    const pick = want === "HV" ? f?.HV ?? f?.HH : f?.HH ?? f?.HV;
    return {
      frame: pick,
      pol: pick === f?.HV ? "HV" : "HH",
      track: t.track || undefined,
      name: pick?.sensor === "Radar" ? "Radar" : "NISAR",
    };
  }
  // Pair-only manifest (WA, Now before the stack): nisar_(hv_)?pre/post —
  // radar_pre/post as the non-NISAR fallback — matched by date.
  const cand = (bundle.manifest?.layers ?? []).filter(
    (l) => l.date?.slice(0, 10) === day && !!l.png
  );
  const ids = want === "HV"
    ? ["nisar_hv_pre", "nisar_hv_post", "nisar_hv_ref"]
    : ["nisar_pre", "nisar_post", "nisar_ref", "radar_pre", "radar_post"];
  const alt = want === "HV"
    ? ["nisar_pre", "nisar_post", "nisar_ref", "radar_pre", "radar_post"]
    : ["nisar_hv_pre", "nisar_hv_post", "nisar_hv_ref"];
  const layer = ids.map((id) => cand.find((l) => l.id === id)).find(Boolean)
    ?? alt.map((id) => cand.find((l) => l.id === id)).find(Boolean);
  if (!layer) return { pol: want, name: "NISAR" };
  const pol = ids.includes(layer.id) ? want : want === "HV" ? "HH" : "HV";
  const url = assetUrl(caseDirOf(bundle), layer.png)!;
  const b = Array.isArray(layer.bounds) && layer.bounds.length === 4 ? layer.bounds : undefined;
  const track = (layer as unknown as Record<string, unknown>).track;
  return {
    frame: {
      url,
      label: "",
      bounds: b,
      pol,
      track: typeof track === "string" ? track : undefined,
      sensor: sensorName(sensorOf(layer)),
    },
    pol,
    track: typeof track === "string" ? track : undefined,
    name: sensorName(sensorOf(layer)) === "Radar" ? "Radar" : "NISAR",
  };
}

function sideResolved(
  bundle: CcBundle,
  s: Side
): { side?: CompareSide; label: string; missing: string } {
  const dir = caseDirOf(bundle);
  const day = day10(s.date);
  const img = (l: ManifestLayer | undefined, label: string): CompareSide | undefined => {
    const url = l ? assetUrl(dir, l.png) : undefined;
    return l && url ? { url, label, bounds: l.bounds } : undefined;
  };
  switch (s.layer) {
    case "flood":
    case "slide": {
      const hz = s.layer;
      const lead = s.lead ?? 0;
      if (lead > 0) {
        const f = forecastForDay(bundle, day, hz, lead);
        return {
          side: img(f?.layer, `Forecast for ${short(day)}, issued ${short(shiftDay(day, -lead))} (NOAA GFS)`),
          label: `Forecast for ${short(day)}, issued ${short(shiftDay(day, -lead))} (NOAA GFS)`,
          missing: `No NOAA run archived for ${fmtDate(shiftDay(day, -lead))}`,
        };
      }
      const f = frameFor(bundle, day, hz);
      const live = bundle.mode === "live";
      const label = live
        ? `${HAZARD_LABEL[f?.hazard ?? hz]} TPI — ${short(day)}${f?.forecast ? " (NOAA GFS forecast)" : ""}`
        : `Observed ${short(day)} (NASA IMERG + SMAP)`;
      return { side: img(f?.layer, label), label, missing: `No frame for ${fmtDate(day)}` };
    }
    case "rain": {
      const l = imergLayerFor(bundle, day);
      const product = typeof (l as ManifestLayer & { product?: unknown } | undefined)?.product === "string"
        ? ((l as ManifestLayer & { product: string }).product.trim() || "IMERG")
        : "IMERG";
      const label = `Rain (NASA GPM ${product}) — ${imergWhen(l, day)}`;
      return { side: img(l, label), label, missing: `No IMERG rain map for ${fmtDate(day)}` };
    }
    case "ml": {
      const h = s.horizon ?? probHorizonsAvailable(bundle.manifest?.layers)[0] ?? "24h";
      const label = `Landslide likelihood (ML) — next ${horizonLabel(h)}`;
      return { side: img(probFrame(bundle.manifest?.layers, h, day), label), label, missing: `No landslide likelihood map for ${fmtDate(day)}` };
    }
    case "nisar": {
      const f = nisarFrameFor(bundle, s);
      const label = `${f.name} ${f.pol} — ${fmtDate(day)}${f.track ? ` (track ${f.track})` : ""}`;
      return {
        side: f.frame ? { ...f.frame, label } : undefined,
        label,
        missing: `No NISAR pass for ${fmtDate(day)}${s.track ? ` on track ${s.track}` : ""}`,
      };
    }
    case "satellite": {
      const tiles = gibsTiles(day);
      const label = `Satellite (NASA GIBS VIIRS) — ${short(day)}`;
      return {
        side: { url: tiles.tiles[0], label, bounds: bundle.bbox, tiles },
        label,
        missing: `No satellite imagery for ${fmtDate(day)}`,
      };
    }
  }
}

export function sidesCompare(
  bundle: CcBundle,
  sides: Sides | undefined
): CompareOption & { warning?: string; note?: string; missing?: { L?: string; R?: string } } {
  const kind = "forecast" as const;
  if (!sides) return { kind, reason: "Pick a day on the timeline" };
  const l = sideResolved(bundle, sides.L);
  const r = sideResolved(bundle, sides.R);
  const missing = { L: l.side ? undefined : l.missing, R: r.side ? undefined : r.missing };
  // Keep the swipe open with whatever side exists, so the pickers stay usable.
  const left = l.side ?? r.side, right = r.side ?? l.side;
  if (!left || !right) return { kind, reason: missing.L ?? missing.R, missing };
  // Jhonatan: the radar flood candidates over the swipe when a NISAR side
  // is on (their own colour).
  const floodUrl = [sides.L, sides.R].some((s) => s.layer === "nisar")
    ? productUrl(bundle, "flood_confirmed")
    : undefined;
  return {
    kind,
    warning: sameSide(sides.L, sides.R) ? "Pick a different date" : undefined,
    // Lucas: different tracks are allowed — say what it means, never block.
    note: sides.L.layer === "nisar" && sides.R.layer === "nisar" && (sides.L.track ?? "") !== (sides.R.track ?? "")
      ? "Different tracks — viewing angle differs, brightness not directly comparable" : undefined,
    missing,
    pair: {
      kind,
      lead: isRiskLayer(sides.L.layer) ? (sides.L.lead ?? 0) : 0,
      bounds: unionBounds(l.side?.bounds, r.side?.bounds) ?? bundle.bbox,
      before: { ...left, label: l.side ? l.label : `${l.label} — not published` },
      after: { ...right, label: r.side ? r.label : `${r.label} — not published` },
      ...(floodUrl ? { flood: { url: floodUrl, label: floodLabels(bundle).layer } } : {}),
    },
  };
}

// NISAR swipe defaults around the selected day: last pass on/before it vs the
// first pass after it (Uruguaiana Sep 30 → Oct 12, WA Dec 4 → Dec 16).
export function nisarDefaults(dates: readonly string[], day: string | undefined): { before?: string; after?: string } {
  if (dates.length < 2) return {};
  const d = (day ?? dates[0]).slice(0, 10);
  const i = dates.findIndex((x) => x > d);
  const ai = i === -1 ? dates.length - 1 : Math.max(1, i);
  return { before: dates[ai - 1], after: dates[ai] };
}

export { ISSUE_LEADS };
