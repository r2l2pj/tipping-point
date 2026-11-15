// Headline card (Lucas, L52): one line on load — how many people sit in
// High or Critical cells today or tomorrow (lead-analysis places.json
// people_high, an estimate) and the municipality with the most.
import type { Place, Places } from "./places";
import { fmtPeople } from "./format";

// `total`: the Now summary's people_alert (the tiles' number) when published,
// so the headline and the "Who is affected" tile never disagree.
export function headlineFor(p: Places, total?: number): { text: string; top: Place; people: number } | undefined {
  const withPeople = p.places.filter((x) => (x.peopleHigh ?? 0) > 0);
  if (!withPeople.length) return undefined;
  const people = typeof total === "number" && Number.isFinite(total) ? total : withPeople.reduce((s, x) => s + (x.peopleHigh ?? 0), 0);
  const top = withPeople.reduce((a, b) => ((b.peopleHigh ?? 0) > (a.peopleHigh ?? 0) ? b : a));
  // The tile's own formatter, so headline and tile read the same number.
  const n = total !== undefined && Number.isFinite(total) ? fmtPeople(people) : Math.round(people).toLocaleString("en-US");
  return { text: `Today & tomorrow: ${n} people in High or Critical risk areas · most in ${top.name} (${top.uf})`, top, people };
}
