import { describe, expect, test } from "bun:test";
import { locatorGeometry } from "./LocatorInset";

const SOUTH_BRAZIL: [number, number, number, number] = [-58, -34, -48, -22.5];

describe("locatorGeometry", () => {
  test("the case area sits in the middle of the window", () => {
    const g = locatorGeometry(SOUTH_BRAZIL);
    expect(g.area.x + g.area.width / 2).toBeCloseTo(50, 0);
    expect(g.area.y + g.area.height / 2).toBeCloseTo(50, 0);
    expect(g.view).toEqual(g.area);
  });

  test("the view box follows the map bounds, not the case area", () => {
    const g = locatorGeometry(SOUTH_BRAZIL, [-50, -28, -48, -27]);
    expect(g.view.x).toBeGreaterThan(g.area.x);
    expect(g.view.width).toBeLessThan(g.area.width);
  });

  test("a tiny view keeps a visible box and a huge view is clipped to the window", () => {
    const tiny = locatorGeometry(SOUTH_BRAZIL, [-48.6, -27.6, -48.59, -27.59]).view;
    expect(tiny.width).toBe(3);
    expect(tiny.height).toBe(3);
    const huge = locatorGeometry(SOUTH_BRAZIL, [-170, -80, 170, 80]).view;
    expect(huge).toEqual({ x: 0, y: 0, width: 100, height: 100 });
  });

  test("land rings that touch the window become one path; far ones are left out", () => {
    const near = [-60, -40, -50, -40, -50, -20, -60, -20];
    const far = [100, 10, 110, 10, 110, 20, 100, 20];
    expect(locatorGeometry(SOUTH_BRAZIL).land).toBe("");
    const d = locatorGeometry(SOUTH_BRAZIL, null, [near, far]).land;
    expect(d.match(/M/g)?.length).toBe(1);
    expect(d.endsWith("Z")).toBe(true);
  });
});
