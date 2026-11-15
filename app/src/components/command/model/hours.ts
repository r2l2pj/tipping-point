// Lucas: an HOUR selector next to the date picker. lead-analysis 6-hourly
// TPI frames (contract: id 'tpi_<hazard>_6h', 'datetime' = end of the 72 h
// rain window in UTC, e.g. "2026-09-30T06:00Z"). Only cases that publish
// them show the hour chips.
import type { ManifestLayer } from "@/lib/types";
import { isGfsFrame } from "@/lib/layers";
import type { CcBundle, Hazard } from "./types";

export const HOURS = ["00", "06", "12", "18"] as const;
const dt = (l: ManifestLayer): string | undefined => {
  const v = (l as unknown as { datetime?: unknown }).datetime;
  return typeof v === "string" ? v : undefined;
};
const sixHourly = (bundle: CcBundle | undefined, hazard: Hazard): ManifestLayer[] =>
  (bundle?.manifest?.layers ?? []).filter((l) => l.id === `tpi_${hazard}_6h` && !isGfsFrame(l) && !!l.png && !!dt(l));

export const hasHourFrames = (bundle: CcBundle | undefined, hazard: Hazard): boolean => sixHourly(bundle, hazard).length > 0;
export function hoursOn(bundle: CcBundle | undefined, hazard: Hazard, date: string | undefined): string[] {
  if (!date) return [];
  return sixHourly(bundle, hazard).map(dt).filter((d): d is string => !!d && d.startsWith(date.slice(0, 10))).map((d) => d.slice(11, 13));
}
export function hourFrameFor(bundle: CcBundle | undefined, hazard: Hazard, date: string | undefined, hour: string | null): ManifestLayer | undefined {
  if (!date || !hour) return undefined;
  return sixHourly(bundle, hazard).find((l) => dt(l)?.startsWith(`${date.slice(0, 10)}T${hour}`));
}
// "Sep 30, 03:00 BRT" (UTC−3) for the caption.
export function hourLabel(l: ManifestLayer): string {
  const d = dt(l);
  if (!d) return "";
  const t = new Date(d.replace(/Z?$/, "Z").replace(/T(\d\d):(\d\d)Z$/, "T$1:$2:00Z"));
  if (Number.isNaN(t.getTime())) return d;
  return t.toLocaleString("en-US", { timeZone: "America/Sao_Paulo", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) + " BRT";
}

// Forecast view + hour chip (conductor): the 6-hourly NOAA GFS frame valid at
// D HH:00Z issued k days before (00Z run, lead_h = 24k + HH ≤ 72), else the
// nearest issue that has one. lead-analysis contract: id tpi_<hazard>_gfs_6h,
// issued YYYY-MM-DD, datetime = issue 00Z + lead_h.
const dayShift = (iso: string, days: number): string =>
  new Date(new Date(`${iso.slice(0, 10)}T12:00:00Z`).getTime() + days * 86_400_000).toISOString().slice(0, 10);
export function hourForecastFrame(bundle: CcBundle | undefined, hazard: Hazard, date: string | undefined, hour: string | null, k: number):
  { layer: ManifestLayer; issued: string; k: number } | undefined {
  if (!date || !hour || k <= 0) return undefined;
  const target = `${date.slice(0, 10)}T${hour}`;
  const cands = (bundle?.manifest?.layers ?? []).filter((l) => l.id === `tpi_${hazard}_gfs_6h` && !!l.png && dt(l)?.startsWith(target) && typeof l.issued === "string");
  if (!cands.length) return undefined;
  const kOf = (l: ManifestLayer) => Math.round((Date.parse(`${date.slice(0, 10)}T12:00:00Z`) - Date.parse(`${(l.issued as string).slice(0, 10)}T12:00:00Z`)) / 86_400_000);
  const want = dayShift(date, -k);
  const pick = cands.find((l) => (l.issued as string).slice(0, 10) === want) ??
    [...cands].sort((a, b) => Math.abs(kOf(a) - k) - Math.abs(kOf(b) - k) || kOf(b) - kOf(a))[0];
  return { layer: pick, issued: (pick.issued as string).slice(0, 10), k: kOf(pick) };
}
const short = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
export const hourForecastCaption = (date: string, hour: string, issued: string, hazardLabel: string): string =>
  `Forecast for ${short(date)} ${hour}Z, issued ${short(issued)} 00Z (NOAA GFS) · ${hazardLabel} TPI`;
