// lead-ux 1/8: ONE drawer. The Layers column is the drawer's "Map" tab; the
// map is full-bleed at every width; phones get a bottom sheet, closed.
// @ts-expect-error -- bun:test resolves under `bun test`
import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CommandCenterView } from "./CommandCenter";
import type { PanelTab } from "./PanelTabs";
import { bundle, liveBundle } from "./model/fixtures";

const noop = () => {};
const UNAVAILABLE = { state: "unavailable", alerts: [] } as const;
const render = (view: "command" | "public" = "command", panelTab?: PanelTab) => renderToStaticMarkup(
  <CommandCenterView cases={[liveBundle(), bundle()]} alertList={UNAVAILABLE} view={view} onView={noop} panelTab={panelTab} />);
const tabs = (html: string) => [...html.matchAll(/data-tab="([a-z]+)"/g)].map((m) => m[1]);

describe("one drawer", () => {
  test("no separate Layers card: the layers live in the Map tab", () => {
    const html = render();
    expect(html).not.toContain('aria-label="Map layers"');
    expect(tabs(html)).toEqual(["overview", "map", "forecast", "radar", "places", "sources"]);
    expect(render("command", "map")).toContain('aria-label="Map layers"');
  });
  test("Public Alerts: Alerts tab first, the Map tab kept", () => {
    const html = render("public");
    expect(tabs(html)).toEqual(["alerts", "map"]);
    expect(html).toContain('aria-label="Public alerts"');
  });
  test("drawer open on wide screens, sheet closed on phones, page never scrolls", () => {
    const html = render();
    expect(html).toContain('data-drawer="open"');
    expect(html).toContain('data-sheet="closed"');
    expect(html).toContain("cc-root flex h-dvh flex-col overflow-hidden");
  });
});
