// Date and time text that reads the same in every JavaScript engine.
//
// toLocaleString("en-US", { month, day, hour, minute }) is not stable. V8
// (Node, Chrome) prints "Nov 15, 05:29 PM". Apple's engine (Safari, and bun's
// JavaScriptCore) prints "Nov 15 at 05:29 PM". The server HTML and the browser
// text then differ and React reports a hydration mismatch. Some ICU versions
// also put a narrow no-break space before AM/PM.
//
// These helpers read the numbers with Intl.DateTimeFormat.formatToParts in a
// 24-hour cycle and build the text by hand: plain ASCII spaces, a comma after
// the day, a two-digit hour and minute. The 12-hour form is derived from the
// 24-hour hour, so it never depends on the engine's AM/PM text. This file has
// no imports, so src/lib and the command model can both use it.

export type ClockStyle = "12h" | "24h";

// One formatter per time zone: building an Intl.DateTimeFormat is costly.
const formatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(timeZone: string): Intl.DateTimeFormat {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone,
    });
    formatters.set(timeZone, f);
  }
  return f;
}

interface Fields {
  month: string;
  day: string;
  hour: number;
  minute: string;
}

function fieldsOf(d: Date, timeZone: string): Fields {
  const got: Record<string, string> = {};
  for (const p of formatterFor(timeZone).formatToParts(d)) got[p.type] = p.value;
  // Some engines print midnight as "24" even in a h23 cycle.
  return {
    month: got.month ?? "",
    day: got.day ?? "",
    hour: Number(got.hour) % 24,
    minute: (got.minute ?? "").padStart(2, "0"),
  };
}

const pad2 = (n: number): string => String(n).padStart(2, "0");

function clockText(f: Fields, clock: ClockStyle): string {
  if (clock === "24h") return `${pad2(f.hour)}:${f.minute}`;
  const h12 = f.hour % 12 === 0 ? 12 : f.hour % 12;
  return `${pad2(h12)}:${f.minute} ${f.hour < 12 ? "AM" : "PM"}`;
}

// Time of day in `timeZone`: "05:29 PM" (12h) or "17:29" (24h).
// The date must be valid: an invalid Date makes Intl throw a RangeError.
export function fmtClock(d: Date, timeZone: string, clock: ClockStyle): string {
  return clockText(fieldsOf(d, timeZone), clock);
}

// Month, day and time of day in `timeZone`: "Nov 15, 05:29 PM" (12h) or
// "Nov 15, 17:29" (24h). The year is left out on purpose.
export function fmtMonthDayClock(d: Date, timeZone: string, clock: ClockStyle): string {
  const f = fieldsOf(d, timeZone);
  return `${f.month} ${f.day}, ${clockText(f, clock)}`;
}
