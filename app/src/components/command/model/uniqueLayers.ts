// Loader guard (conductor bug: WA ships three nisar_extra layers all id
// 'nisar_sme2', Nov 22 / Dec 4 / Dec 16 → React "two children with the same
// key"). Kinds whose ids must be unique (nisar_extra, nisar_change) get a
// date/track/pol suffix on repeats; exact duplicates (same id, date, png)
// are dropped. Per-date frame families (tpi_*, nisar_frame) share an id by
// contract and are left alone.
type L = { id: string; date?: string; png?: string | null; kind?: unknown; track?: unknown; polarization?: unknown };
const UNIQUE_KINDS = new Set(["nisar_extra", "nisar_change"]);

export function uniqueLayerIds<T extends L>(layers: readonly T[], warn: (msg: string) => void = (m) => console.warn(m)): T[] {
  const exact = new Set<string>();
  const seen = new Map<string, number>();
  const out: T[] = [];
  for (const l of layers) {
    const key = `${l.id}|${l.date ?? ""}|${l.png ?? ""}`;
    if (exact.has(key)) { warn(`manifest: dropped duplicate layer ${l.id} (${l.date ?? "no date"})`); continue; }
    exact.add(key);
    if (typeof l.kind === "string" && UNIQUE_KINDS.has(l.kind)) {
      const n = seen.get(l.id) ?? 0;
      seen.set(l.id, n + 1);
      if (n > 0) {
        const parts = [l.date?.slice(0, 10).replace(/-/g, ""), typeof l.track === "string" ? l.track : undefined,
          typeof l.polarization === "string" ? l.polarization.toLowerCase() : undefined].filter(Boolean);
        const id = `${l.id}_${parts.join("_") || n}`;
        warn(`manifest: duplicate layer id ${l.id} → ${id}`);
        out.push({ ...l, id });
        continue;
      }
    }
    out.push(l);
  }
  return out;
}

// React key for any manifest-layer list: id + date + track + polarization.
export const layerKey = (l: { id: string; date?: string; track?: unknown; polarization?: unknown; pol?: unknown }, i?: number): string =>
  [l.id, l.date ?? "", typeof l.track === "string" ? l.track : "", typeof (l.polarization ?? l.pol) === "string" ? (l.polarization ?? l.pol) : "", i ?? ""].join("|");
