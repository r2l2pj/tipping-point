// @ts-expect-error -- bun:test resolves under `bun test`
import { describe, expect, test } from "bun:test";
import { fmtClock, fmtMonthDayClock } from "./dateParts";

const BRT = "America/Sao_Paulo";

describe("fmtMonthDayClock", () => {
  test("12-hour text: comma after the day, plain space before AM/PM", () => {
    expect(fmtMonthDayClock(new Date("2026-11-15T20:29:00Z"), BRT, "12h")).toBe("Nov 15, 05:29 PM");
    expect(fmtMonthDayClock(new Date("2026-11-14T09:00:00Z"), BRT, "12h")).toBe("Nov 14, 06:00 AM");
  });

  test("no 'at', no non-ASCII space, whatever the engine prints", () => {
    const text = fmtMonthDayClock(new Date("2026-11-15T20:29:00Z"), BRT, "12h");
    expect(text).not.toContain(" at ");
    expect(text).toMatch(/^[\x20-\x7e]+$/);
  });

  test("24-hour text and the day change at the zone's midnight", () => {
    expect(fmtMonthDayClock(new Date("2026-11-15T05:00:00Z"), BRT, "24h")).toBe("Nov 15, 02:00");
    expect(fmtMonthDayClock(new Date("2026-11-15T02:59:00Z"), BRT, "24h")).toBe("Nov 14, 23:59");
  });

  test("midnight is 00:xx in 24h and 12:xx AM in 12h; noon is 12:xx PM", () => {
    const midnight = new Date("2026-11-15T03:05:00Z");
    expect(fmtMonthDayClock(midnight, BRT, "24h")).toBe("Nov 15, 00:05");
    expect(fmtMonthDayClock(midnight, BRT, "12h")).toBe("Nov 15, 12:05 AM");
    expect(fmtMonthDayClock(new Date("2026-11-15T15:05:00Z"), BRT, "12h")).toBe("Nov 15, 12:05 PM");
  });

  test("time zone is honoured", () => {
    const d = new Date("2026-11-15T20:29:00Z");
    expect(fmtMonthDayClock(d, "UTC", "12h")).toBe("Nov 15, 08:29 PM");
    expect(fmtMonthDayClock(d, "Asia/Tokyo", "24h")).toBe("Nov 16, 05:29");
  });
});

describe("fmtClock", () => {
  test("time of day only", () => {
    expect(fmtClock(new Date("2026-11-15T05:56:26Z"), BRT, "24h")).toBe("02:56");
    expect(fmtClock(new Date("2026-11-15T20:29:00Z"), BRT, "12h")).toBe("05:29 PM");
    expect(fmtClock(new Date("2026-11-15T03:05:00Z"), BRT, "24h")).toBe("00:05");
  });
});
