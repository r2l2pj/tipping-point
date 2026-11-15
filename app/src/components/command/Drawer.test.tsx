// lead-ux 1/8: ONE drawer. The Layers column is the drawer's "Map" tab; the
// map is full-bleed at every width; phones get a bottom sheet, closed.
// @ts-expect-error -- bun:test resolves under `bun test`
import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CommandCenterView } from "./CommandCenter";
import type { PanelTab } from "./PanelTabs";
import { bundle, liveBundle } from "./model/fixtures";
import { defaultTab, nisarPeek } from "./model/peek";

const noop = () => {};
const UNAVAILABLE = { state: "unavailable", alerts: [] } as const;
const render = (view: "command" | "public" = "command", panelTab?: PanelTab) => renderToStaticMarkup(
  <CommandCenterView cases={[liveBundle(), bundle()]} alertList={UNAVAILABLE} view={view} onView={noop} panelTab={panelTab} />);
const tabs = (html: string) => [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1]);

describe("one drawer", () => {
  test("no separate Layers card: the layers live in the Map tab", () => {
    const html = render();
    expect(html).not.toContain('aria-label="Map layers"');
    expect(tabs(html)).toEqual(["overview", "map", "forecast", "radar", "places"]);
    expect(render("command", "map")).toContain('aria-label="Map layers"');
  });
  test("the old footer credits are in the Sources tab", () => {
    expect(render()).not.toContain("<footer");
    const src = render(); // Overview ends with "Sources & credits"
    expect(src).toContain("data-credits");
    expect(src).toContain("Sources &amp; credits");
    expect(src).toContain("Forecast rain: NOAA GFS (the only non-NASA input)");
  });
  test("Public Alerts: Alerts tab first, the Map tab kept", () => {
    const html = render("public");
    expect(tabs(html)).toEqual(["alerts", "map"]);
    expect(html).toContain('aria-label="Public alerts"');
  });
  test("Lucas override: drawer OPEN on wide screens; the phone sheet peeks one NISAR line; page never scrolls", () => {
    const html = render();
    expect(html).toContain('data-drawer="open"');
    expect(html).toContain('data-sheet="closed"');
    expect(html).toContain("data-sheet-peek");
    expect(html).toContain("Details ›"); // the edge tab, shown once the drawer is closed
    expect(html).toContain("cc-root flex h-dvh flex-col overflow-hidden");
  });
});

describe("thin timeline", () => {
  test("one row: hour and run pickers are selects, not chip rows", async () => {
    const { default: TimelineBar } = await import("./TimelineBar");
    const steps = [0, 1, 2].map((i) => ({ index: i, date: `2026-09-2${8 + i}`, top: `Sep ${28 + i}`, sub: "", offset: i - 1, forecast: i === 2 })) as never;
    const html = renderToStaticMarkup(<TimelineBar steps={steps} marks={[{ index: 1, label: "NISAR pass", color: "blue" }] as never} dayIndex={1} playing={false}
      forecastTag="Forecast (NOAA GFS)" onSeek={noop} onPrev={noop} onNext={noop} onPlayToggle={noop}
      hours={{ available: ["00", "12"], hour: null, onHour: noop }}
      runs={{ list: [{ id: "a", label: "17:29" }, { id: "b", label: "17:02" }], picked: "a", onPick: noop }} />);
    expect(html).toContain('<select aria-label="Hour (UTC)"');
    expect(html).toContain('<select aria-label="Now run (BRT)"');
    expect(html).toContain("h-12"); // 48 px bar
    expect(html).toContain("data-mark-caption"); // the day's mark floats above the bar
    expect(html).toContain(">Forecast (NOAA GFS)<");
  });
});

describe("NISAR first (Lucas override)", () => {
  const card = (o: object) => ({ pair: true, nisar: true, headline: "NISAR radar images available", dates: "Sep 30, 2026 → Oct 12, 2026",
    flooded: { label: "Flooded area (NISAR)", value: "1.8 km²" }, candidates: { label: "", value: "", note: "" }, ...o }) as never;
  test("replay with a NISAR pair opens on the NISAR tab; Now and replays without NISAR on Overview", () => {
    expect(defaultTab(bundle({ id: "rs_uruguai_2026" }), card({}))).toBe("radar");
    expect(defaultTab(liveBundle(), card({}))).toBe("overview");
    expect(defaultTab(bundle({ id: "rs_2024" }), card({ pair: false }))).toBe("overview");
  });
  test("the peek line names NISAR, the area as candidates and the pass dates; no pair -> the card status", () => {
    expect(nisarPeek(card({}))).toBe("NISAR · 1.8 km² flooded area (candidates) · Sep 30, 2026 → Oct 12, 2026");
    expect(nisarPeek(card({ pair: false, flooded: undefined, headline: "Awaiting the next NISAR pass" }))).toBe("Awaiting the next NISAR pass");
  });
});
