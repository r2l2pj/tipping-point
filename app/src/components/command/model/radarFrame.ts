// Lucas (BUG): the "Radar — NISAR" map view drew only the flood polygons —
// the radar image itself showed up only inside Compare. The single map now
// draws one NISAR frame: by default the pass on/before the selected day on
// the case's main track (the one with the most passes), HH; the user can
// switch HH/HV and pick any other pass.
import { canonicalCaseId } from "@/lib/contract";
import { assetUrl } from "@/lib/layers";
import { fmtDate } from "./format";
import { completeEnough, isUruguaiana, nisarTracks, sensorName, sensorOf } from "./radar";
import { stretchOf, type Stretch } from "./changeComposite";
import type { CcBundle } from "./types";

type BBox = [number, number, number, number];
export type Pol = "HH" | "HV";

export interface RadarPass {
  date: string; // YYYY-MM-DD
  track: string; // "" when the manifest carries none
  frames: Partial<Record<Pol, { url: string; bounds?: BBox; stretch?: Stretch }>>;
  coverage?: number; // % of the case box this pass imaged (lead-analysis)
  sensor: string; // "NISAR radar" | "Radar"
}

export interface RadarPick { date: string; track: string; pol: Pol }

export interface RadarFrame {
  pass: RadarPass;
  pol: Pol;
  url: string;
  bounds?: BBox;
  stretch?: Stretch; // fixed dB stretch (undefined: not declared)
  chip: string; // "NISAR HH · Oct 12, 2026 · track 010D"
  pinned: boolean; // a manual pick (until the next timeline move), else following the timeline
  note?: string; // why the frame is not on/before the day
}

// Legacy pair ids (WA before the stack contract, Now): nisar_pre/post/ref
// and their HV twins, read as passes when no nisar_frame stack exists.
const LEGACY = /^(?:nisar|radar)_(hv_)?(pre|post|ref)$/;

export function radarPasses(bundle: CcBundle): RadarPass[] {
  const stacks = nisarTracks(bundle);
  const out: RadarPass[] = [];
  if (stacks.length) {
    for (const s of stacks) {
      for (const d of s.dates) {
        const f = s.frames[d];
        const frames: RadarPass["frames"] = {};
        for (const p of ["HH", "HV"] as const) {
          const side = f[p];
          if (side?.url) frames[p] = { url: side.url, bounds: side.bounds, stretch: side.stretch };
        }
        const any = f.HH ?? f.HV;
        const cov = f.HH?.coverage ?? f.HV?.coverage;
        out.push({ date: d, track: s.track, frames, sensor: any?.sensor ?? "NISAR radar", ...(cov !== undefined ? { coverage: cov } : {}) });
      }
    }
  } else {
    const caseDir = canonicalCaseId(bundle.manifest?.event ?? bundle.id);
    const byDay = new Map<string, RadarPass>();
    for (const raw of bundle.manifest?.layers ?? []) {
      const l = raw as unknown as Record<string, unknown>;
      const m = LEGACY.exec(String(l.id));
      if (!m || typeof l.date !== "string" || typeof l.png !== "string" || (!isUruguaiana(bundle) && !completeEnough(l))) continue;
      const url = assetUrl(caseDir, l.png);
      if (!url) continue;
      const pol: Pol = m[1] || String(l.polarization ?? "").toUpperCase() === "HV" ? "HV" : "HH";
      const date = l.date.slice(0, 10);
      const track = typeof l.track === "string" ? l.track : "";
      const key = `${date}|${track}`;
      const p = byDay.get(key) ?? { date, track, frames: {}, sensor: sensorName(sensorOf(l)) };
      const b = Array.isArray(l.bounds) && l.bounds.length === 4 ? (l.bounds as BBox) : undefined;
      p.frames[pol] ??= { url, bounds: b, stretch: stretchOf(l) };
      byDay.set(key, p);
    }
    out.push(...byDay.values());
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || a.track.localeCompare(b.track));
}

// Main track: the fullest coverage of the case box when lead-analysis
// publishes coverage_pct (conductor: 082D 83 % over 010D 40 % at
// Uruguaiana), else the one with the most passes (010D Uruguaiana, 172D WA).
export function mainTrack(passes: readonly RadarPass[]): string {
  const n = new Map<string, { k: number; cov: number[] }>();
  for (const p of passes) {
    const e = n.get(p.track) ?? { k: 0, cov: [] };
    e.k += 1;
    if (p.coverage !== undefined) e.cov.push(p.coverage);
    n.set(p.track, e);
  }
  const med = (v: number[]) => (v.length ? [...v].sort((a, b) => a - b)[Math.floor(v.length / 2)] : -1);
  return [...n.entries()].sort((a, b) => med(b[1].cov) - med(a[1].cov) || b[1].k - a[1].k || a[0].localeCompare(b[0]))[0]?.[0] ?? "";
}

export const passLabel = (p: RadarPass): string =>
  `${fmtDate(p.date)}${p.track ? ` · ${p.track}` : ""}${p.coverage !== undefined ? ` · ${Math.round(p.coverage)} % of area` : ""}`;

// A pass that did not image the whole case box: which side it misses
// (coverage_pct < 90, else its frame bounds vs the case box). With `view`,
// only when the visible map runs past the frame's edge on that side (the
// user can see the missing part — never inside the river window).
export function swathEdgeNote(pass: RadarPass, bounds: BBox | undefined, caseBox: BBox | undefined, view?: BBox): string | undefined {
  const partial = pass.coverage !== undefined ? pass.coverage < 90
    : !!(bounds && caseBox && (bounds[0] > caseBox[0] + 0.05 || bounds[2] < caseBox[2] - 0.05 || bounds[1] > caseBox[1] + 0.05 || bounds[3] < caseBox[3] - 0.05));
  if (!partial) return undefined;
  const side = bounds && caseBox
    ? bounds[2] < caseBox[2] - 0.05 ? "east" : bounds[0] > caseBox[0] + 0.05 ? "west" : bounds[3] < caseBox[3] - 0.05 ? "north" : bounds[1] > caseBox[1] + 0.05 ? "south" : undefined
    : undefined;
  if (view && bounds) {
    const past = side === "east" ? view[2] > bounds[2] : side === "west" ? view[0] < bounds[0]
      : side === "north" ? view[3] > bounds[3] : side === "south" ? view[1] < bounds[1]
      : view[0] < bounds[0] || view[2] > bounds[2] || view[1] < bounds[1] || view[3] > bounds[3];
    if (!past) return undefined;
  }
  return `swath edge — NISAR did not image the ${side ? `${side} part` : "whole area"} on this pass`;
}
export const passValue = (p: RadarPass): string => `${p.date}|${p.track}`;

// The frame to draw: the user's pick when it exists, else the main-track
// pass on/before the day (the first pass after it when none came before).
export function radarFrame(passes: readonly RadarPass[], day: string | undefined, pick?: Partial<RadarPick>): RadarFrame | undefined {
  if (!passes.length) return undefined;
  const wantPol: Pol = pick?.pol ?? "HH";
  let pass = pick?.date ? passes.find((p) => p.date === pick.date && (pick.track === undefined || p.track === pick.track)) : undefined;
  // L67: a picked pass that is not shown (incomplete coverage, e.g. a river's-
  // dance bar) → the nearest complete pass (any track; the earlier on a tie).
  if (pick?.date && !pass) {
    const t = Date.parse(pick.date);
    pass = [...passes].sort((a, b) => Math.abs(Date.parse(a.date) - t) - Math.abs(Date.parse(b.date) - t) || a.date.localeCompare(b.date))[0];
  }
  let note: string | undefined;
  const pinned = !!pass;
  if (!pass) {
    // Lucas (follow the timeline): the newest pass on/before the day on ANY
    // track (the main track first on a shared date), so Play steps through
    // every pass (Sep 30, Oct 5, Oct 12, Oct 17 … at Uruguaiana).
    const track = mainTrack(passes);
    const d = day ?? passes[passes.length - 1].date;
    pass = [...passes].filter((p) => p.date <= d)
      .sort((a, b) => b.date.localeCompare(a.date) || Number(b.track === track) - Number(a.track === track))[0];
    if (!pass) {
      pass = passes.filter((p) => p.track === track)[0] ?? passes[0];
      note = `first pass after ${fmtDate(d)}`;
    }
  }
  const pol: Pol = pass.frames[wantPol] ? wantPol : pass.frames.HH ? "HH" : "HV";
  const f = pass.frames[pol];
  if (!f) return undefined;
  const name = pass.sensor === "NISAR radar" ? "NISAR" : "Radar";
  return {
    pass,
    pol,
    url: f.url,
    bounds: f.bounds,
    stretch: f.stretch,
    pinned,
    chip: `${name} ${pol} · ${fmtDate(pass.date)}${pass.track ? ` · track ${pass.track}` : ""}`,
    ...(note ? { note } : {}),
  };
}
