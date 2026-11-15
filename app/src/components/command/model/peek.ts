// lead-ux (Lucas: NISAR is the core): the one line the phone sheet shows
// while it is down, and the tab the drawer opens on.
import type { PanelTab } from "../PanelTabs";
import type { RadarCard } from "./radar";
import type { CcBundle } from "./types";

// "NISAR · 1.8 km² flooded area (candidates) · Sep 30, 2026 → Oct 12, 2026";
// without a usable pair, the card's own status line (never a made-up number).
export function nisarPeek(radar: RadarCard | undefined): string {
  if (!radar) return "Radar data unavailable";
  if (radar.pair && radar.flooded) {
    const who = radar.nisar ? "NISAR" : "Radar";
    return `${who} · ${radar.flooded.value} flooded area (candidates)${radar.dates ? ` · ${radar.dates}` : ""}`;
  }
  return radar.headline;
}

// Replay cases with a NISAR pair open on the NISAR tab; Now (and replays
// without NISAR) on Overview.
export function defaultTab(bundle: CcBundle | undefined, radar: RadarCard | undefined): PanelTab {
  return bundle?.mode === "replay" && radar?.pair && radar.nisar ? "radar" : "overview";
}
