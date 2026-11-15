// L52 U1: big map views and the headline card.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { activeView, VIEWS } from "./views";
import { headlineFor } from "./headline";
import { parsePlaces } from "./places";

test("the view follows the layers on (deep links keep ?layers=)", () => {
  expect(activeView(["imerg", "hand"])).toBe("rain");
  expect(activeView(["flood", "tpi"])).toBe("risk");
  expect(activeView(["flood"])).toBe("radar");
  expect(activeView(["tpi"])).toBe("risk");
  expect(activeView(["ptrigger"])).toBe("risk");
  expect(activeView(["hand"])).toBeUndefined();
  expect(VIEWS.map((v) => v.label)).toEqual(["Risk now & next days", "Rain", "Radar — NISAR", "Satellite"]);
});

test("headline: total people in High/Critical cells and the top municipality", () => {
  const P = parsePlaces(JSON.parse(readFileSync(join(process.cwd(), "test/fixtures/now/places.json"), "utf8")))!;
  const h = headlineFor(P)!;
  const top = [...P.places].sort((a, b) => (b.peopleHigh ?? 0) - (a.peopleHigh ?? 0))[0];
  expect(h.top.id).toBe(top.id);
  expect(h.text).toMatch(/^Today & tomorrow: [\d,]+ people in High or Critical risk areas · most in .+ \([A-Z]{2}\)$/);
  expect(headlineFor({ dates: [], places: [] })).toBeUndefined();
});

test("timeline_marks from the metrics become timeline marks (event red, nisar blue, signal amber)", async () => {
  const { stepMarks } = await import("./steps");
  const steps = ["2026-09-30", "2026-10-05", "2026-10-12"].map((date, index) => ({ index, date, forecast: false, top: date, sub: "" }));
  const b = { id: "rs_uruguai_2026", mode: "replay", bbox: [0, 0, 1, 1], metricsView: { timeline_marks: [
    { date: "2026-09-30", label: "local flood index Critical over the river corridor", kind: "signal" },
    { date: "2026-10-05", label: "Uruguaiana above flood stage 8.50 m", kind: "event" },
    { date: "2026-10-12", label: "NISAR pass", kind: "nisar" },
    { date: "2026-11-05", label: "outside the steps", kind: "nisar" },
  ] } } as never;
  const m = stepMarks(b, steps as never);
  expect(m.map((x) => [x.index, x.color])).toEqual([[0, "amber"], [1, "red"], [2, "blue"]]);
});

test("a replay without an event day lands on its first 'signal' timeline mark (Uruguaiana Sep 30)", async () => {
  const { defaultStepIndex } = await import("./steps");
  const steps = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-20"].map((date, index) => ({ index, date, forecast: false, top: date, sub: "" }));
  const b = { metricsView: { timeline_marks: [{ date: "2026-09-18", label: "NISAR pass", kind: "nisar" }, { date: "2026-09-30", label: "index Critical", kind: "signal" }] } } as never;
  expect(defaultStepIndex(steps as never, b)).toBe(2);
  expect(defaultStepIndex(steps as never)).toBe(3);
});

test("one replay experience: the landing day comes from the data (landing mark > onset anchor > event day > signal)", async () => {
  const { defaultStepIndex } = await import("./steps");
  const steps = ["2025-12-06", "2025-12-07", "2025-12-08", "2025-12-09", "2025-12-10"].map((date, index) =>
    ({ index, date, forecast: false, top: date, sub: "", offset: index - 4 }));
  const onset = { flood_warned: { anchor_utc: "2025-12-08T08:00:00+00:00", anchor_basis: "onset" } };
  expect(defaultStepIndex(steps as never, { metricsView: onset } as never)).toBe(2); // WA: Dec 8 onset
  expect(defaultStepIndex(steps as never, { metricsView: { slide_warned: { anchor_utc: "2025-12-07T03:00:00Z", anchor_basis: "onset" } } } as never)).toBe(1);
  expect(defaultStepIndex(steps as never, { metricsView: { ...onset, timeline_marks: [{ date: "2025-12-09", label: "x", kind: "event", landing: true }] } } as never)).toBe(3);
  expect(defaultStepIndex(steps as never, { metricsView: { flood_warned: { anchor_utc: "2025-12-08", anchor_basis: "damage" } } } as never)).toBe(4); // event day
  expect(defaultStepIndex(steps as never)).toBe(4);
});
