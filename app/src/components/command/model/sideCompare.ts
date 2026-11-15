// Lucas (super urgent): ONE Compare ⇆ — a LEFT and a RIGHT side, each its own
// day and Observed | Forecast issued k days before, on the current layer.
// Any day vs any other day; never both sides identical.
import { assetUrl } from "@/lib/layers";
import type { ManifestLayer } from "@/lib/types";
import { fmtDate } from "./format";
import { caseDirOf } from "./lazy";
import type { CompareOption } from "./radar";
import { forecastForDay, frameFor, shiftDay, type IssueLead, type Step } from "./steps";
import { HAZARD_LABEL, type CcBundle, type Hazard } from "./types";

export interface Side { date: string; lead: IssueLead } // lead 0 = observed (the day's own frame)
export interface Sides { L: Side; R: Side }

export const sameSide = (a: Side, b: Side): boolean => a.date.slice(0, 10) === b.date.slice(0, 10) && a.lead === b.lead;

// RIGHT = the selected day; LEFT = the forecast for that day when the
// Forecast view is on, else the day 3 days earlier (nearest step on or
// before it, else the first other step).
export function defaultSides(steps: readonly Step[], dayIndex: number, issueLead: IssueLead): Sides | undefined {
  const r = steps[dayIndex] ?? steps[steps.length - 1];
  if (!r) return undefined;
  const R: Side = { date: r.date.slice(0, 10), lead: 0 };
  if (issueLead > 0) return { L: { date: R.date, lead: issueLead }, R };
  const target = shiftDay(R.date, -3);
  const earlier = [...steps].reverse().find((s) => s.date.slice(0, 10) <= target)
    ?? steps.find((s) => s.date.slice(0, 10) !== R.date);
  if (!earlier) return { L: R, R };
  return { L: { date: earlier.date.slice(0, 10), lead: 0 }, R };
}

const short = (d: string) => fmtDate(d).replace(/,\s*\d{4}$/, "");
function sideLayer(bundle: CcBundle, s: Side, hazard: Hazard): { layer?: ManifestLayer; label: string } {
  if (s.lead > 0) {
    const f = forecastForDay(bundle, s.date, hazard, s.lead);
    return { layer: f?.layer, label: `Forecast for ${short(s.date)}, issued ${short(shiftDay(s.date, -s.lead))} (NOAA GFS)` };
  }
  const f = frameFor(bundle, s.date, hazard);
  const live = bundle.mode === "live";
  return {
    layer: f?.layer,
    label: live
      ? `${HAZARD_LABEL[hazard]} TPI — ${short(s.date)}${f?.forecast ? " (NOAA GFS forecast)" : ""}`
      : `Observed ${short(s.date)} (NASA IMERG + SMAP)`,
  };
}

export function sidesCompare(bundle: CcBundle, sides: Sides | undefined, hazard: Hazard): CompareOption & { warning?: string; missing?: { L?: string; R?: string } } {
  const kind = "forecast" as const;
  if (!sides) return { kind, reason: "Pick a day on the timeline" };
  const l = sideLayer(bundle, sides.L, hazard);
  const r = sideLayer(bundle, sides.R, hazard);
  const dir = caseDirOf(bundle);
  const lu = assetUrl(dir, l.layer?.png);
  const ru = assetUrl(dir, r.layer?.png);
  const none = (s: Side) => (s.lead > 0 ? `No NOAA run archived for ${fmtDate(shiftDay(s.date, -s.lead))}` : `No frame for ${fmtDate(s.date)}`);
  const missing = { L: lu ? undefined : none(sides.L), R: ru ? undefined : none(sides.R) };
  // Keep the swipe open with whatever side exists, so the pickers stay usable.
  const before = lu ?? ru, after = ru ?? lu;
  if (!before || !after) return { kind, reason: missing.L ?? missing.R, missing };
  return {
    kind,
    warning: sameSide(sides.L, sides.R) ? "Pick a different date" : undefined,
    missing,
    pair: {
      kind,
      lead: sides.L.lead,
      bounds: l.layer?.bounds ?? r.layer?.bounds ?? bundle.bbox,
      before: { url: before, label: lu ? l.label : `${l.label} — not published`, bounds: l.layer?.bounds },
      after: { url: after, label: ru ? r.label : `${r.label} — not published`, bounds: r.layer?.bounds },
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
