// Lucas (t5w) + lead-analysis contract: Uruguaiana NISAR MOSAICS — the
// tracks of a ~6-day window merged into one nearly full image of the case
// box (per pixel the pass closest to the window's reference pass). The
// Radar — NISAR view shows the mosaic of the selected day when published;
// single passes stay in the pass picker and in Compare.
// Manifest: kind 'nisar_mosaic', id nisar_mosaic_<pol>_<YYYYMMDD>, date (=
// the reference pass), window [start, end], polarization, passes [{date,
// track, share_pct}], png, bounds, stretch_db, coverage_pct, source_png,
// source_legend, footprints (GeoJSON, one feature per pass), note.
import { canonicalCaseId } from "@/lib/contract";
import { assetUrl } from "@/lib/layers";
import { stretchOf, type Stretch } from "./changeComposite";
import type { CcBundle } from "./types";

type BBox = [number, number, number, number];
export interface MosaicPass { date: string; track: string; share?: number }
export interface NisarMosaic {
  id: string;
  ref: string; // reference pass date (YYYY-MM-DD)
  start: string;
  end: string;
  pol: "HH" | "HV";
  passes: MosaicPass[];
  url: string;
  bounds?: BBox;
  stretch?: Stretch;
  coverage?: number;
  footprintsUrl?: string;
  note?: string;
}

const d10 = (v: unknown) => (typeof v === "string" ? v.slice(0, 10) : "");

export function nisarMosaics(bundle: CcBundle): NisarMosaic[] {
  const caseDir = canonicalCaseId(bundle.manifest?.event ?? bundle.id);
  const base = (bundle as unknown as { assetBase?: string }).assetBase ?? "/data";
  const out: NisarMosaic[] = [];
  for (const raw of bundle.manifest?.layers ?? []) {
    const l = raw as unknown as Record<string, unknown>;
    if (l.kind !== "nisar_mosaic" || typeof l.png !== "string") continue;
    const url = assetUrl(caseDir, l.png);
    if (!url) continue;
    const ref = d10(l.date);
    const win = Array.isArray(l.window) ? l.window.map(d10) : [];
    const passes = (Array.isArray(l.passes) ? l.passes : []).flatMap((p): MosaicPass[] => {
      const r = p as Record<string, unknown>;
      return typeof r?.date === "string" && typeof r.track === "string"
        ? [{ date: d10(r.date), track: r.track, ...(typeof r.share_pct === "number" ? { share: r.share_pct } : {}) }] : [];
    }).sort((a, b) => a.date.localeCompare(b.date));
    const start = win[0] || passes[0]?.date || ref, end = win[1] || passes[passes.length - 1]?.date || ref;
    if (!ref && !start) continue;
    const b = Array.isArray(l.bounds) && l.bounds.length === 4 ? (l.bounds as BBox) : undefined;
    out.push({
      id: String(l.id),
      ref: ref || start,
      start,
      end,
      pol: String(l.polarization ?? "HH").toUpperCase() === "HV" ? "HV" : "HH",
      passes,
      url,
      ...(b ? { bounds: b } : {}),
      ...(stretchOf(l) ? { stretch: stretchOf(l) } : {}),
      ...(typeof l.coverage_pct === "number" ? { coverage: l.coverage_pct } : {}),
      ...(typeof l.footprints === "string" ? { footprintsUrl: assetUrl(caseDir, l.footprints, base) } : {}),
      ...(typeof l.note === "string" ? { note: l.note } : {}),
    });
  }
  return out.sort((a, b) => a.start.localeCompare(b.start) || a.pol.localeCompare(b.pol));
}

// The mosaic for a day: the window containing it, else the latest window
// that started on/before it; the wanted polarization, else HH.
export function mosaicFor(mosaics: readonly NisarMosaic[], day: string | undefined, pol: "HH" | "HV" = "HH"): NisarMosaic | undefined {
  if (!day || !mosaics.length) return undefined;
  const d = day.slice(0, 10);
  const pick = (list: NisarMosaic[]) => list.find((m) => m.pol === pol) ?? list.find((m) => m.pol === "HH") ?? list[0];
  const inside = mosaics.filter((m) => m.start <= d && d <= m.end);
  if (inside.length) return pick(inside);
  const before = mosaics.filter((m) => m.start <= d);
  if (!before.length) return undefined;
  const last = before.reduce((a, m) => (m.start > a.start ? m : a)).start;
  return pick(before.filter((m) => m.start === last));
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const md = (iso: string) => ({ m: MON[Number(iso.slice(5, 7)) - 1], d: Number(iso.slice(8, 10)) });

// "NISAR mosaic Oct 9–17 · 010D + 060A + 082D + 132A" (pol when HV).
export function mosaicChip(m: NisarMosaic): string {
  const a = md(m.start), b = md(m.end);
  const span = m.start === m.end ? `${a.m} ${a.d}` : a.m === b.m ? `${a.m} ${a.d}–${b.d}` : `${a.m} ${a.d}–${b.m} ${b.d}`;
  const tracks = [...new Set(m.passes.map((p) => p.track))].sort();
  return `NISAR mosaic ${span}${m.pol === "HV" ? " (HV)" : ""}${tracks.length ? ` · ${tracks.join(" + ")}` : ""}`;
}

export const MOSAIC_NOTE = "Passes from different tracks see the ground at different angles — seams and brightness steps between them are expected; not a flood map.";
