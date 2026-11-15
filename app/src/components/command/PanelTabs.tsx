"use client";

// L52 U1: the right panel as tabs (content moved, nothing deleted).
// One row with an underline on the selected tab: five pill buttons wrapped
// to two rows in the Now panel and the solid pill outweighed the content.
export type PanelTab = "overview" | "forecast" | "radar" | "places" | "sources";
const LABEL: Record<PanelTab, string> = {
  overview: "Overview", forecast: "Forecast", radar: "Radar (NISAR)", places: "Places", sources: "Sources",
};

export default function PanelTabs({ tabs, tab, onTab }: { tabs: PanelTab[]; tab: PanelTab; onTab: (t: PanelTab) => void }) {
  return (
    <div role="tablist" aria-label="Panel" className="sticky -top-2.5 z-10 -mx-1 -mt-2.5 mb-1.5 flex shrink-0 border-b border-cc-line bg-cc-surface px-1 pt-2.5" data-panel-tabs>
      {tabs.map((t) => (
        <button key={t} type="button" role="tab" aria-selected={tab === t} data-tab={t} onClick={() => onTab(t)}
          className={`-mb-px flex-1 whitespace-nowrap border-b-2 px-1.5 pb-2 pt-1.5 text-center text-[13px] ${tab === t ? "border-cc-accent font-semibold text-cc-accent" : "border-transparent font-medium text-cc-muted hover:border-cc-line-strong hover:text-cc-ink"}`}>
          {LABEL[t]}
        </button>
      ))}
    </div>
  );
}
