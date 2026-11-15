// lead-ux step 2: the one-row top bar. The case picker replaces the
// Now / Replay toggle, the past-event select, the freshness pill and the
// "Start here" card; Command Center / Public Alerts moves into the menu.
// @ts-expect-error -- bun:test resolves under `bun test`
import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import CasePicker, { NOW_TITLE, caseLabel } from "./CasePicker";
import TopBar from "./TopBar";
import { CommandCenterView } from "./CommandCenter";
import { bundle, liveBundle } from "./model/fixtures";

const noop = () => {};
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const cases = [liveBundle(), bundle({ id: "wa_2025", title: "Washington, Dec 2025" }), bundle({ id: "rs_2024", title: "Rio Grande do Sul, May 2024" })];

describe("top bar: one row", () => {
  const html = renderToStaticMarkup(
    <TopBar cases={cases} bundle={cases[1]} view="command" onView={noop} onSelectCase={noop} onTour={noop}
      freshness="Replay data · snapshot Nov 15" freshnessKind="archive" />);
  test("no Now / Replay toggle, no Command Center / Public Alerts toggle on the bar", () => {
    expect(html).not.toContain('aria-label="Time"');
    expect(html).not.toContain('aria-label="View"');
    expect(text(html)).not.toContain("Command Center");
    expect(text(html)).not.toContain("Public Alerts");
  });
  test("the case picker names the case and the data freshness", () => {
    expect(text(html)).toContain("Washington, Dec 2025");
    expect(text(html)).toContain("Replay data · snapshot Nov 15");
    expect(html).toContain('aria-label="Menu"');
  });
  test("Public Alerts view shows a way back on the bar", () => {
    const pub = renderToStaticMarkup(
      <TopBar cases={cases} bundle={cases[0]} view="public" onView={noop} onSelectCase={noop}
        freshness="Updated 17:29 BRT" freshnessKind="live" />);
    expect(pub).toContain('title="Back to the Command Center"');
  });
});

describe("case picker menu", () => {
  const menu = text(renderToStaticMarkup(
    <CasePicker cases={cases} bundle={cases[0]} freshness="Updated" freshnessKind="live"
      onSelectCase={noop} onTour={noop} defaultOpen />));
  test("lists the guided tour first, then every case with Now first", () => {
    const tour = menu.indexOf("Start here — guided tour");
    const all = menu.indexOf("All cases");
    expect(tour).toBeGreaterThanOrEqual(0);
    expect(all).toBeGreaterThan(tour);
    const after = menu.slice(all);
    expect(after.indexOf(NOW_TITLE)).toBeLessThan(after.indexOf("Washington, Dec 2025"));
    expect(after).toContain("Rio Grande do Sul, May 2024");
  });
  test("the live case reads as Now — Southern Brazil", () => {
    expect(caseLabel(liveBundle({ title: "x" }))).toBe(NOW_TITLE);
    expect(caseLabel(undefined)).toBe("No case loaded");
  });
});

test("the Overview panel no longer carries the Start here card", () => {
  const t = text(renderToStaticMarkup(
    <CommandCenterView cases={cases} alertList={{ state: "unavailable", alerts: [] }} view="command" onView={noop} />));
  const panel = t.slice(t.indexOf("Overview"));
  expect(panel).not.toContain("Start here");
});

describe("UX 4/8: fewer chips on the map", () => {
  const live = liveBundle();
  const wa = bundle({ id: "wa_2025", title: "Washington, Dec 2025", updated_at: "2026-11-15T12:00:00Z" });
  const render = (initialCase?: string) => renderToStaticMarkup(
    <CommandCenterView cases={[live, wa]} alertList={{ state: "unavailable", alerts: [] }} view="command" onView={noop} initialCase={initialCase} />);
  test("no Live/Replay word, no 'Open the … case' chip on the map", () => {
    const now = render();
    expect(now).not.toContain("data-featured-case");
    // Replay data is not a warning: no status word at all.
    expect(render("wa_2025")).not.toContain("data-status=");
  });
  test("a warning status (unknown age, stale, last saved) keeps its word, in the top bar", () => {
    const now = render(); // fixture: no run time -> unknown age
    const header = now.slice(now.indexOf("<header"), now.indexOf("</header>"));
    expect(header).toContain('data-status="unknown-age"');
  });
  test("Replay keeps a one-click way back to Now — in the top bar", () => {
    const replay = render("wa_2025");
    const header = replay.slice(replay.indexOf("<header"), replay.indexOf("</header>"));
    expect(header).toContain("data-back-to-live");
    expect(render()).not.toContain("data-back-to-live");
  });
  test("basemap: one button naming the current basemap; the choices are closed", () => {
    const now = render();
    expect(now).toContain("data-basemap-toggle");
    expect(now).not.toContain('aria-label="Basemap"');
  });
});

test("the tour never opens by itself ('/' lands on Uruguaiana): the menu is closed on render", () => {
  const html = renderToStaticMarkup(
    <CasePicker cases={cases} bundle={cases[1]} freshness="Replay data" freshnessKind="archive" onSelectCase={noop} onTour={noop} />);
  expect(html).not.toContain('role="menu"');
  expect(html).toContain('aria-expanded="false"');
});
