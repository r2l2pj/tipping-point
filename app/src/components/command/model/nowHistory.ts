// Lucas: in Now, step through the last Now runs (every 3 h) instead of days.
// lead-analysis contract tipping-point/now-history@1: data/now/history/
// index.json {runs: [{run, id, manifest}], newest first, keep 8}; each run
// manifest's PNG paths resolve under its own run directory. Map only: the
// panel keeps the latest run's figures.
import { resolveAsset } from "@/lib/contract";
import type { ManifestLayer } from "@/lib/types";
import type { CcBundle } from "./types";

type M = Record<string, unknown>;
const isRecord = (v: unknown): v is M => typeof v === "object" && v !== null && !Array.isArray(v);

export interface NowRun { id: string; run: string; manifest: string; label: string }

// "16:19" in BRT (UTC−3), with the day when it is not the newest run's day.
export function runLabel(iso: string, newestIso?: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  const opt = { timeZone: "America/Sao_Paulo" } as const;
  const hm = t.toLocaleTimeString("en-GB", { ...opt, hour: "2-digit", minute: "2-digit", hour12: false });
  const day = (d: Date) => d.toLocaleDateString("en-US", { ...opt, month: "short", day: "numeric" });
  return newestIso && day(new Date(newestIso)) !== day(t) ? `${day(t)} ${hm}` : hm;
}

export function parseNowHistory(raw: unknown): NowRun[] {
  if (!isRecord(raw) || !Array.isArray(raw.runs)) return [];
  const runs = raw.runs.filter((r): r is M => isRecord(r) && typeof r.id === "string" && typeof r.run === "string" && typeof r.manifest === "string");
  const newest = runs[0]?.run as string | undefined;
  return runs.map((r) => ({ id: r.id as string, run: r.run as string, manifest: r.manifest as string, label: runLabel(r.run as string, newest) }));
}

// The live bundle with a past run's map layers (absolute PNG URLs under the
// run directory) — everything else stays the latest run's.
export function bundleForRun(bundle: CcBundle, run: NowRun, runManifest: unknown): CcBundle {
  if (!isRecord(runManifest) || !Array.isArray(runManifest.layers) || !bundle.manifest) return bundle;
  const dir = run.manifest.replace(/\/manifest\.json$/, "");
  const layers = runManifest.layers.filter(isRecord).map((l) => {
    const png = typeof l.png === "string" ? resolveAsset(bundle.assetBase ?? "/data", dir, l.png) : undefined;
    return { ...l, png, cog: undefined } as unknown as ManifestLayer;
  });
  return { ...bundle, manifest: { ...bundle.manifest, layers } };
}
