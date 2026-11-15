// L64 (Lucas): '/' opens Uruguaiana on Oct 12 — the NISAR image ('Water in
// red') on top of the flood index; Now one click away.
// @ts-expect-error -- bun:test resolves under `bun test`
import { expect, test } from "bun:test";
import { initialState, FEATURED_LANDING_DAY } from "../state";
import { stepsFor } from "./steps";
import { activeView } from "./views";
import { bundle, liveBundle } from "./fixtures";

const days = ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"];
const uy = bundle({
  id: "rs_uruguai_2026",
  series: { reference_date: "2026-10-12", days: days.map((date) => ({ date, kind: "observed" })) },
  manifest: { event: "rs_uruguai_2026", layers: [
    ...days.map((d) => ({ id: "tpi_flood", date: d, png: `rs_uruguai_2026/png/tpi_flood_${d}.png`, bounds: [-58, -30.6, -55.3, -28.2] })),
    { id: "nisar_hh_20261012", kind: "nisar_frame", date: "2026-10-12", track: "010D", polarization: "HH", png: "rs_uruguai_2026/png/nisar_hh_20261012.png", bounds: [-58, -30.6, -56.676, -28.2] },
  ] },
} as never);

test("'/' → Uruguaiana, Oct 12, flood index + NISAR image (radar view); ?case=now still opens Now", () => {
  const cases = [uy, liveBundle()];
  const s = initialState(cases);
  expect(s.activeId).toBe("rs_uruguai_2026");
  expect(s.hazard).toBe("flood");
  expect(stepsFor(uy)[s.dayIndex].date).toBe(FEATURED_LANDING_DAY);
  expect(s.layers).toEqual(["tpi", "nisar"]);
  expect(activeView(s.layers)).toBe("radar");
  expect(initialState(cases, "now").activeId).toBe("now");
  // a deep link keeps its own layers
  expect(initialState(cases, undefined, undefined, { layers: "tpi" }).layers).toEqual(["tpi"]);
});
