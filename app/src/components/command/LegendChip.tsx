"use client";

import { Legend, legendTitle } from "./LayersColumn";
import type { LayerId } from "./model/layerRegistry";
import { classRows, layerLegendRows } from "./model/legend";
import type { CcBundle, Hazard } from "./model/types";

// lead-ux (Lucas: "legend a small floating chip"): one line on the map —
// the colour ramp of what is painted and its name; a tap opens the full
// legend (classes, ranges, forecast hatch, NISAR water, source + date).
export default function LegendChip({
  bundle,
  layers,
  hazard,
  stepDate,
  probHorizon,
  changeCyan,
  className = "",
}: {
  bundle: CcBundle;
  layers: readonly LayerId[];
  hazard: Hazard;
  stepDate?: string;
  probHorizon?: string;
  changeCyan?: boolean;
  className?: string;
}) {
  const tpi = layers.includes("tpi");
  const rows = layerLegendRows(bundle, layers, probHorizon, stepDate, changeCyan).filter((r) => !r.note);
  const swatches = tpi ? classRows(bundle).map((c) => c.color) : rows.slice(0, 4).map((r) => r.swatch);
  const name = tpi ? legendTitle(hazard, true) : rows[0]?.label;
  if (!name) return null;
  return (
    <details data-legend-chip className={`group relative max-w-[min(320px,calc(100vw-120px))] rounded-[10px] bg-white/95 text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)] ${className}`}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-2.5 py-1.5 text-xs font-semibold [&::-webkit-details-marker]:hidden" title="Legend">
        <span aria-hidden="true" className="flex shrink-0 overflow-hidden rounded-[3px] border border-black/15">
          {swatches.map((bg, i) => <span key={i} className="inline-block h-3 w-3.5" style={{ background: bg }} />)}
        </span>
        <span className="min-w-0 truncate">{name}{tpi ? " · Low → Critical" : ""}</span>
        <span aria-hidden="true" className="shrink-0 text-cc-muted group-open:rotate-180">▴</span>
      </summary>
      <Legend bundle={bundle} layers={layers} hazard={hazard} stepDate={stepDate} probHorizon={probHorizon} compact changeCyan={changeCyan}
        // Opens as a panel above the chip: the chip and its neighbours (the
        // basemap button, the locator) keep their place.
        className="absolute bottom-full right-0 z-20 mb-1.5 max-h-[50vh] w-[min(320px,calc(100vw-40px))] overflow-y-auto rounded-[10px] bg-white/95 px-2.5 py-2 shadow-[0_2px_8px_rgba(0,0,0,0.3)]" />
    </details>
  );
}
