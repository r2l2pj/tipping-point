"use client";

import { canonicalCaseId } from "@/lib/contract";
import type { Action } from "./state";
import type { CcBundle } from "./model/types";
import { nisarNoCandidates, radarSensor } from "./model/radar";
import { isFeatured } from "./state";

// "Start here" (judge report #6, conductor): the stops that show the
// project in under a minute. Each one sets case, day, hazard and layers in
// one step. lead-ux: listed at the top of the case picker menu (CasePicker).
export interface TourStop {
  key: string;
  title: string;
  what: string;
  action: Extract<Action, { type: "goTo" }>;
  primary?: boolean; // L42: NISAR first and largest
}

// L42 (conductor, challenge core = NISAR): NISAR first (WA swipe, HH | HV
// and the time series when published), then the live Southern Brazil NISAR
// card, then the rain index (RS), then the forecast replay (WA).
export function tourStops(cases: readonly CcBundle[]): TourStop[] {
  // Bundle ids may be the event ids ("wa-2025-12"); match the canonical key.
  const caseOf = (key: string) => cases.find((c) => c.mode !== "live" && canonicalCaseId(c.id) === key);
  const waCase = caseOf("wa_2025");
  const wa = waCase?.id;
  const rs = caseOf("rs_2024")?.id;
  const live = cases.find((c) => c.mode === "live");
  const hasHv = !!waCase?.manifest?.layers?.some((l) => l.id === "nisar_hv_pre");
  const extras = [hasHv ? "switch HH | HV" : undefined, waCase?.nisarSeries ? "see the radar time series" : undefined]
    .filter(Boolean).join(", then ");
  const stops: Omit<TourStop, "title">[] = [];
  // Lucas: the Uruguay River flood (rs_uruguai_2026) is the main case —
  // when loaded, its NISAR stop is #1 and the largest.
  const uy = cases.find((c) => c.mode !== "live" && isFeatured(c));
  const uyNisar = uy ? radarSensor(uy).nisar : true;
  if (uy) stops.push({
    key: "uy-radar", primary: true,
    // Lucas: the case lands on the map (flood index + outlines), not the swipe.
    what: `Flood index day by day with the ${uyNisar ? "NISAR radar" : "radar"} flood outlines; radar before / after in the Radar tab`,
    action: { type: "goTo", id: uy.id, hazard: "flood" },
  });
  if (wa) stops.push({
    key: "wa-radar", primary: !uy,
    // Lucas: same replay as Uruguaiana — lands on the map (flood index day by
    // day + NISAR outlines from the pass date); the swipe is in the Radar tab.
    what: `Flood index day by day with the NISAR radar flood outlines (seen through cloud); in the Radar tab: before / after${extras ? `, ${extras}` : ""}`,
    action: { type: "goTo", id: wa, hazard: "flood" },
  });
  if (live) stops.push({
    key: "now",
    what: nisarNoCandidates(live)
      ? `Latest NISAR pass: ${nisarNoCandidates(live)!.computed ? "no new flooding detected" : "no flood candidates (no usable pair)"} — plus today's risk and public alerts (every 3 h)`
      : "Today's NISAR radar flood candidates, risk and public alerts (every 3 h)",
    action: { type: "goTo", id: live.id },
  });
  if (rs) stops.push({
    key: "rs",
    what: "Rain warns: landslide risk on the event day, with landslide scar candidates seen from space",
    action: { type: "goTo", id: rs, hazard: "slide", layer: "candidates" },
  });
  if (wa) stops.push({
    key: "wa",
    what: "Replay: the landslide-risk forecast issued Dec 3 for Dec 6, as it was available then",
    // The day is the TARGET day of the forecast (Dec 6 = D-4), and lead 3 means
    // the run issued 3 days earlier (Dec 3). Picking the issue day (Dec 3 = D-7)
    // asks for the run of Nov 30, which is not archived.
    action: { type: "goTo", id: wa, day: "2025-12-06", hazard: "slide", lead: 3 },
  });
  const titles: Record<string, string> = {
    "uy-radar": `${uy?.title ?? "Uruguay River flood"} — ${uyNisar ? "NISAR radar" : "radar"}`,
    "wa-radar": "Washington, Dec 2025 — NISAR radar",
    // R2: no live NISAR candidates -> the stop is not billed as NISAR.
    now: live && nisarNoCandidates(live) ? "Now — Southern Brazil" : "Now — Southern Brazil, NISAR radar",
    rs: "Rio Grande do Sul, May 2024 — rain index",
    wa: "Washington, Dec 2025 — forecast replay",
  };
  return stops.map((s, i) => ({ ...s, title: `${i + 1} · ${titles[s.key]}` }));
}
