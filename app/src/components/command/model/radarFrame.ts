// Lucas (BUG): the "Radar — NISAR" map view drew only the flood polygons —
// the radar image itself showed up only inside Compare. The single map now
// draws one NISAR frame: by default the pass on/before the selected day on
// the case's main track (the one with the most passes), HH; the user can
// switch HH/HV and pick any other pass.
import { canonicalCaseId } from "@/lib/contract";
import { assetUrl } from "@/lib/layers";
import { fmtDate } from "./format";
import { nisarTracks, sensorName, sensorOf } from "./radar";
import { stretchOf, type Stretch } from "./changeComposite";
import type { CcBundle } from "./types";

type BBox = [number, number, number, number];
export type Pol = "HH" | "HV";

export interface RadarPass {
  date: string; // YYYY-MM-DD
  track: string; // "" when the manifest carries none
  frames: Partial<Record<Pol, { url: string; bounds?: BBox; stretch?: Stretch }>>;
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
        out.push({ date: d, track: s.track, frames, sensor: any?.sensor ?? "NISAR radar" });
      }
    }
  } else {
    const caseDir = canonicalCaseId(bundle.manifest?.event ?? bundle.id);
    const byDay = new Map<string, RadarPass>();
    for (const raw of bundle.manifest?.layers ?? []) {
      const l = raw as unknown as Record<string, unknown>;
      const m = LEGACY.exec(String(l.id));
      if (!m || typeof l.date !== "string" || typeof l.png !== "string") continue;
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

// Main track = the one with the most passes (010D Uruguaiana, 172D WA).
export function mainTrack(passes: readonly RadarPass[]): string {
  const n = new Map<string, number>();
  for (const p of passes) n.set(p.track, (n.get(p.track) ?? 0) + 1);
  return [...n.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? "";
}

export const passLabel = (p: RadarPass): string => `${fmtDate(p.date)}${p.track ? ` · ${p.track}` : ""}`;
export const passValue = (p: RadarPass): string => `${p.date}|${p.track}`;

// The frame to draw: the user's pick when it exists, else the main-track
// pass on/before the day (the first pass after it when none came before).
export function radarFrame(passes: readonly RadarPass[], day: string | undefined, pick?: Partial<RadarPick>): RadarFrame | undefined {
  if (!passes.length) return undefined;
  const wantPol: Pol = pick?.pol ?? "HH";
  let pass = pick?.date ? passes.find((p) => p.date === pick.date && (pick.track === undefined || p.track === pick.track)) : undefined;
  let note: string | undefined;
  if (!pass) {
    const track = mainTrack(passes);
    const onTrack = passes.filter((p) => p.track === track);
    const d = day ?? onTrack[onTrack.length - 1].date;
    pass = [...onTrack].reverse().find((p) => p.date <= d);
    if (!pass) {
      pass = onTrack[0];
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
    chip: `${name} ${pol} · ${fmtDate(pass.date)}${pass.track ? ` · track ${pass.track}` : ""}`,
    ...(note ? { note } : {}),
  };
}
