"use client";

import { Icon, ICON } from "./ui";

// L52 U1: the right panel as tabs (content moved, nothing deleted).
// lead-ux 1/8: the Layers column is the "Map" tab; the Public Alerts view
// has its own "Alerts" tab. One row that scrolls sideways, never wraps.
export type PanelTab = "overview" | "map" | "forecast" | "radar" | "places" | "sources" | "alerts";
const LABEL: Record<PanelTab, string> = {
  overview: "Overview", map: "Map", forecast: "Forecast", radar: "NISAR", places: "Places", sources: "Sources", alerts: "Alerts",
};

export default function PanelTabs({
  tabs,
  tab,
  onTab,
  onClose,
  sheetOpen = false,
}: {
  tabs: PanelTab[];
  tab: PanelTab;
  onTab: (t: PanelTab) => void;
  onClose?: () => void; // wide: hide the drawer; phones: lower the sheet
  sheetOpen?: boolean; // phones: the sheet is up (the active tab only reads as picked then)
}) {
  return (
    <div className="flex items-center gap-1" data-panel-tabs>
      <div role="tablist" aria-label="Panel" className="flex min-w-0 grow gap-0.5 overflow-x-auto [scrollbar-width:none]">
        {tabs.map((t) => {
          const on = tab === t;
          return (
            <button key={t} type="button" role="tab" aria-selected={on} data-tab={t} onClick={() => onTab(t)}
              className={`shrink-0 whitespace-nowrap rounded-full px-2 py-1.5 text-[13px] font-semibold ${on ? `${sheetOpen ? "" : "max-[899px]:bg-transparent max-[899px]:text-cc-ink max-[899px]:underline max-[899px]:decoration-2 max-[899px]:underline-offset-4"} bg-cc-accent text-white` : "text-cc-ink hover:bg-cc-well"}`}>
              {LABEL[t]}
            </button>
          );
        })}
      </div>
      {onClose && (
        <button type="button" onClick={onClose} data-drawer-close
          aria-label="Hide the panel" title="Hide the panel"
          className={`shrink-0 rounded-lg p-1.5 text-cc-muted hover:bg-cc-well ${sheetOpen ? "" : "max-[899px]:hidden"}`}>
          <Icon d={ICON.close} size={18} className="max-[899px]:hidden" />
          <Icon d={ICON.chevronUp} size={18} strokeWidth={2.2} className="rotate-180 min-[900px]:hidden" />
        </button>
      )}
    </div>
  );
}
