// The NISAR "change layer" overlay on the normal map (Lucas, t5w): Δ of two
// nisar_frame passes of the same track/polarization, classified client-side
// (deltaComposite) — red ≥3 dB darker = likely new water, cyan ≥3 dB
// brighter. The default pair brackets the selected day on the case's default
// track; the Radar card carries the two pass pickers; ?nisarChange=
// <before>:<after>:<pol> deep-links a pair.
import { changeBlockedReason, type BBox, type Stretch } from "./changeComposite";
import { fmtDate } from "./format";
import { nisarTracks } from "./radar";
import type { CcBundle } from "./types";

export const NISAR_CHANGE_LABEL = "NISAR change (what's different)";
export const NISAR_CHANGE_LEGEND =
  "Red: radar ≥3 dB darker (likely new water) · Cyan: ≥3 dB brighter";
export const NISAR_CHANGE_LEGEND_RED = "Red: radar ≥3 dB darker (likely new water)";
export const CHANGE_OPACITY_DEFAULT = 0.85;
export const CHANGE_OPACITY_MIN = 0.2;

// A pass usable for the change layer. `bounds` falls back to the case bbox
// (same convention as every manifest raster); `stretch` is the frame's fixed
// dB stretch — without it the gray levels cannot be decoded and the pair is
// blocked.
export interface NisarChangeFrame {
  date: string; // YYYY-MM-DD
  track: string; // "" when the manifest carries none
  pol: "HH" | "HV";
  url: string;
  bounds: BBox;
  stretch?: Stretch;
  label: string;
}

export function nisarChangeFrames(bundle: CcBundle): NisarChangeFrame[] {
  const out: NisarChangeFrame[] = [];
  for (const t of nisarTracks(bundle)) {
    for (const d of t.dates) {
      for (const pol of ["HH", "HV"] as const) {
        const f = t.frames[d]?.[pol];
        if (!f) continue;
        out.push({
          date: d,
          track: t.track,
          pol,
          url: f.url,
          bounds: f.bounds ?? bundle.bbox,
          stretch: f.stretch,
          label: f.label,
        });
      }
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date));
}

// The pass pickers carry "<date>|<track>" (the same values the compare's
// NISAR day list uses).
export const changeValue = (f: { date: string; track: string }): string =>
  `${f.date}|${f.track}`;

export interface NisarChangeSel {
  before?: string; // "<date>" or "<date>|<track>"
  after?: string;
  pol?: "HH" | "HV";
}

const day10 = (d: string | undefined) => (d ?? "").slice(0, 10);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

// The case default track: the one with the most passes (nisarTracks order).
const mainTrack = (bundle: CcBundle): string => nisarTracks(bundle)[0]?.track ?? "";

// Default pair: the last pass BEFORE the selected day vs the first pass
// ON/AFTER it, on the case default track (HH preferred — a date may ship
// only one pol). Day before the first pass → the two oldest; day after the
// last → the two newest.
export function defaultChangePair(
  bundle: CcBundle,
  day?: string
): { before?: NisarChangeFrame; after?: NisarChangeFrame; pol?: "HH" | "HV" } {
  const frames = nisarChangeFrames(bundle);
  const d = day10(day);
  // Lucas (follow the timeline): after = the newest pass on/before the day
  // (any track, HH first, the default track first on a shared date), before =
  // the previous pass on the SAME track and pol. Play steps through every pass.
  if (d) {
    const main = mainTrack(bundle);
    const cands = frames.filter((f) => f.date <= d)
      .sort((a, b) => b.date.localeCompare(a.date) || (a.pol === "HH" ? -1 : 1) - (b.pol === "HH" ? -1 : 1)
        || Number(b.track === main) - Number(a.track === main));
    for (const after of cands) {
      const before = frames.filter((f) => f.track === after.track && f.pol === after.pol && f.date < after.date).pop();
      if (before) return { before, after, pol: after.pol };
    }
  }
  for (const t of nisarTracks(bundle)) {
    for (const pol of ["HH", "HV"] as const) {
      const dates = t.dates.filter((x) => t.frames[x]?.[pol]);
      if (dates.length < 2) continue;
      let ai = dates.findIndex((x) => x >= d);
      if (ai === -1) ai = dates.length - 1;
      else if (ai === 0) ai = 1;
      const pick = (x: string) => frames.find((f) => f.date === x && f.track === t.track && f.pol === pol);
      return { before: pick(dates[ai - 1]), after: pick(dates[ai]), pol };
    }
  }
  return {};
}

// A selection (deep link / picker value) normalized to "<date>|<track>"
// values the pass pickers carry; unset sides fall back to the day's default.
export function changeSelection(
  bundle: CcBundle,
  sel: NisarChangeSel,
  day?: string
): { before?: string; after?: string; pol?: "HH" | "HV" } {
  const frames = nisarChangeFrames(bundle);
  const def = defaultChangePair(bundle, day);
  const norm = (v: string | undefined, fallback?: NisarChangeFrame): string | undefined => {
    const [rawD, rawT] = (v ?? "").split("|");
    const d = day10(rawD);
    if (!DATE_RE.test(d)) return fallback ? changeValue(fallback) : undefined;
    const onDay = frames.filter((f) => f.date === d);
    if (!onDay.length) return `${d}|${rawT ?? ""}`; // a picked day with no pass: resolve names it
    const f =
      (rawT !== undefined ? onDay.find((x) => x.track === rawT) : undefined) ??
      onDay.find((x) => x.track === mainTrack(bundle)) ??
      onDay[0];
    return changeValue(f);
  };
  const pol = sel.pol === "HH" || sel.pol === "HV" ? sel.pol : def.pol;
  return { before: norm(sel.before, def.before), after: norm(sel.after, def.after), pol };
}

export type ChangeSpec =
  | { pair: { before: NisarChangeFrame; after: NisarChangeFrame }; label: string }
  | { reason: string };

// The pair a selection produces, or the reason it cannot composite. A
// reversed pick is sorted; the same pass twice, a track or pol mismatch, and
// frames without a fixed stretch each get their own reason.
export function resolveChangePair(
  bundle: CcBundle,
  sel: NisarChangeSel,
  day?: string
): ChangeSpec {
  const frames = nisarChangeFrames(bundle);
  if (frames.length < 2) return { reason: "needs two NISAR passes over the area" };
  const s = changeSelection(bundle, sel, day);
  if (!s.before || !s.after) {
    return { reason: nisarChangeUnavailableReason(bundle) ?? "Pick a before and an after pass" };
  }
  const pick = (v?: string): { f: NisarChangeFrame } | { reason: string } => {
    const [d, t] = (v ?? "").split("|");
    const onDay = frames.filter((f) => f.date === day10(d) && (t === undefined || f.track === t));
    const f = onDay.find((x) => x.pol === s.pol) ?? onDay[0];
    return f
      ? { f }
      : { reason: `No NISAR pass for ${fmtDate(day10(d))}${t ? ` on track ${t}` : ""}` };
  };
  const b = pick(s.before), a = pick(s.after);
  if ("reason" in b) return b;
  if ("reason" in a) return a;
  let [bf, af] = [b.f, a.f];
  if (bf.date > af.date) [bf, af] = [af, bf]; // picked in reverse → sort
  if (bf.date === af.date && bf.track === af.track && bf.pol === af.pol) {
    return { reason: "Pick two different passes" };
  }
  const blocked = changeBlockedReason(
    { layer: "nisar", track: bf.track || undefined, pol: bf.pol },
    { layer: "nisar", track: af.track || undefined, pol: af.pol }
  );
  if (blocked) return { reason: blocked };
  if (!bf.stretch || !af.stretch) {
    return { reason: "the radar brightness stretch is not fixed yet for these passes" };
  }
  return {
    pair: { before: bf, after: af },
    label: `${fmtDate(bf.date)} → ${fmtDate(af.date)} · ${bf.pol}${bf.track ? ` · track ${bf.track}` : ""}`,
  };
}

// Can this case composite anything at all: two passes, same track and pol,
// both with a fixed stretch.
export function nisarChangeAvailable(bundle: CcBundle): boolean {
  const dates = new Map<string, Set<string>>();
  for (const f of nisarChangeFrames(bundle)) {
    if (!f.stretch) continue;
    const k = `${f.track}|${f.pol}`;
    const s = dates.get(k) ?? new Set<string>();
    s.add(f.date);
    dates.set(k, s);
    if (s.size >= 2) return true;
  }
  return false;
}

// Why the toggle stays off when ≥2 passes exist but none can composite;
// undefined when <2 passes (the row hides entirely) or a pair works.
export function nisarChangeUnavailableReason(bundle: CcBundle): string | undefined {
  const frames = nisarChangeFrames(bundle);
  if (frames.length < 2 || nisarChangeAvailable(bundle)) return undefined;
  if (frames.filter((f) => f.stretch).length < 2) {
    return "the radar brightness stretch is not fixed yet for these passes";
  }
  return "the passes differ in track or polarization — not comparable";
}

// ?nisarChange=<YYYY-MM-DD>:<YYYY-MM-DD>[:HH|HV] — the pair's pass dates
// (default track) and polarization.
export function parseChangeLink(raw: string | undefined): NisarChangeSel | undefined {
  const parts = (raw ?? "").trim().split(":");
  const before = parts[0] ?? "";
  const after = parts[1] ?? "";
  if (!DATE_RE.test(before) || !DATE_RE.test(after)) return undefined;
  const pol = (parts[2] ?? "").trim().toUpperCase();
  return { before, after, ...(pol === "HH" || pol === "HV" ? { pol: pol as "HH" | "HV" } : {}) };
}
