"use client";

import { PASSES_SHOWN, type RadarCard as RadarModel } from "./model/radar";
import { fmtBrt, fmtDate } from "./model/format";
import { CHANGE_OPACITY_MIN, NISAR_CHANGE_LABEL } from "./model/nisarChange";
import NisarSeriesChart, { NisarFrames } from "./NisarSeriesChart";
import RiverDanceChart from "./RiverDanceChart";
import type { RiverDance } from "./model/riverDance";
import { Icon, ICON } from "./ui";
import { isMissingText } from "./model/sparse";
import { layerKey } from "./model/uniqueLayers";

// Radar confirmation. "NISAR" is only said for a real pair; scars are always
// "candidates". "Confirm with NISAR radar" opens the confirmation on the map
// and does not move the timeline.
// Judge report #3: the physical drivers behind the radar layer, in plain words.
// L42 (conductor): two visible lines, the rest stays in "What NISAR sees and why".
export const RADAR_SEES = [
  "Dark = open water: smooth water reflects the radar away from the satellite.",
  "Flooded towns and forests stay or turn BRIGHT (double bounce), so this map may miss them.",
];
export const NISAR_WHY = [
  "NISAR's L-band radar sees through cloud and at night: the flood is mapped even when optical satellites see only cloud.",
  "Open water is smooth and reflects the radar away, so it turns dark. We map water that turned dark between two passes, on low ground near rivers.",
  "Flooded towns and forests can turn bright instead (the radar bounces between water and walls or trunks), so a dark-water map can miss them.",
  "NISAR passes over the same place every 12 days; the map updates with each new pass.",
];

export default function RadarCard({
  radar,
  floodOn,
  compareOpen,
  onConfirm,
  onShowFlood,
  onCompare,
  hideMissing = false,
  swipeDates,
  change,
  dance,
}: {
  radar: RadarModel;
  // Jhonatan's river's dance (Uruguaiana): bars jump the map to a pass.
  dance?: { d: RiverDance; selected?: string; onPass: (date: string, track?: string) => void };
  hideMissing?: boolean; // featured replay: no "pending" scar block (conductor, v6)
  // Lucas: the NISAR dates of the open swipe, as chips (no slider).
  swipeDates?: { dates: string[]; before: string; after: string; onPick: (before: string, after: string) => void };
  // Lucas (t5w): the Δ-dB change overlay on the normal map — a switch plus
  // the two pass pickers, pol, the cyan toggle and its own opacity.
  change?: {
    on: boolean;
    available: boolean; // the resolved pair composites (a reasoned pick disables the switch)
    reason?: string;
    label?: string; // "Dec 16, 2025 → Dec 28, 2025 · HH · track 172D"
    pol?: "HH" | "HV";
    pols: ("HH" | "HV")[];
    cyan: boolean;
    opacity: number;
    passes: { value: string; label: string }[]; // every pass: "<date>|<track>"
    before?: string;
    after?: string;
    onToggle: () => void;
    onPair: (patch: { before?: string; after?: string; pol?: "HH" | "HV" }) => void;
    onCyan: () => void;
    onOpacity: (value: number) => void;
  };
  floodOn: boolean;
  compareOpen: boolean;
  onConfirm: () => void;
  onShowFlood: () => void;
  onCompare: () => void;
}) {
  const noCandidates = hideMissing && isMissingText(radar.candidates.value) && !radar.candidates.lines?.length;
  const primary = "inline-flex items-center gap-1.5 rounded-lg bg-cc-accent px-2.5 py-1.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50";
  const secondary = "inline-flex items-center rounded-lg border border-cc-line-strong bg-cc-surface px-2.5 py-1.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-60";
  // The value is drawn above its label (order-first); the markup keeps the
  // term before its value.
  const cell = "flex flex-col rounded-lg border border-cc-line bg-cc-well px-2.5 py-1.5";
  const big = "text-[17px] font-semibold leading-tight tabular-nums";
  const lines = radar.candidates.lines ?? [];
  const caveats = lines.filter((l) => l.startsWith("Caveat:"));
  const notes = [
    ...lines.filter((l) => !l.startsWith("Caveat:")),
    radar.note,
    noCandidates ? undefined : radar.candidates.note,
    radar.limitation,
  ].filter((l): l is string => !!l);
  return (
    <section aria-label="Radar check" className="rounded-[10px] border border-cc-line px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
        <Icon d={ICON.radar} size={30} strokeWidth={1.8} className="shrink-0" />
        <div className="shrink-0">
          <div className="text-[15px] font-semibold leading-tight">{radar.pair && radar.nisar ? "NISAR radar" : "Radar"}</div>
          <div className="text-xs text-cc-muted">Post-event radar check</div>
        </div>
        <div className="min-w-0 flex-[1_1_150px] border-l border-cc-line pl-2.5">
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            <span aria-hidden="true" className={`h-[9px] w-[9px] shrink-0 rounded-full ${radar.pair ? "bg-cc-accent" : "bg-[#9aa7a0]"}`} />
            {radar.headline}
          </div>
          {radar.dates && <div className="text-xs text-cc-muted">Passes: {radar.dates}</div>}
        </div>
      </div>

      {/* The figures, one per cell: value first, then what it measures. */}
      <dl className="mt-2 grid grid-cols-2 gap-2" data-radar-stats>
        {radar.flooded && (
          <div className={cell}>
            <dt className="text-xs leading-tight text-cc-muted">{radar.flooded.label}</dt>
            <dd className={`${big} order-first`}>{radar.flooded.value}</dd>
            {radar.flooded.swath && <dd className="mt-0.5 text-[11px] leading-tight text-cc-faint">{radar.flooded.swath}</dd>}
          </div>
        )}
        {radar.people && (
          <div className={cell}>
            <dt className="text-xs leading-tight text-cc-muted">{radar.people.label}</dt>
            <dd className={`${big} order-first`}>{radar.people.value}</dd>
          </div>
        )}
        {!noCandidates && (
          <div className={cell}>
            <dt className="text-xs leading-tight text-cc-muted">{radar.candidates.label}</dt>
            <dd className={`${big} order-first`}>{radar.candidates.value}</dd>
          </div>
        )}
        {radar.others?.map((o) => (
          <div key={o.label} className={cell}>
            <dt className="text-xs leading-tight text-cc-muted">{o.label}</dt>
            <dd className="order-first text-[15px] font-semibold leading-tight">{o.value}</dd>
          </div>
        ))}
      </dl>

      {dance && <RiverDanceChart d={dance.d} selected={dance.selected} onPass={dance.onPass} />}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {radar.pair && (
          <button type="button" onClick={onConfirm} disabled={!radar.canShowOnMap} className={primary}>
            {radar.nisar ? "Check with NISAR radar" : "Check with radar"} <Icon d={ICON.arrowRight} size={14} strokeWidth={2.2} />
          </button>
        )}
        <button type="button" onClick={onShowFlood} aria-pressed={floodOn} disabled={!radar.canShowOnMap} className={secondary}>
          {floodOn ? "Hide on map" : "Show on map"}
        </button>
        <button type="button" onClick={onCompare} aria-pressed={compareOpen} disabled={!radar.compare.pair} className={secondary}>
          Before / after
        </button>
        {!radar.compare.pair && radar.compare.reason && !radar.headline.startsWith(radar.compare.reason) && (
          <span className="text-xs italic text-cc-faint">Before / after: {radar.compare.reason}</span>
        )}
        {swipeDates && <SwipeDatePicker {...swipeDates} />}
      </div>

      {/* NISAR change layer (t5w): Δ of two same-track, same-pol passes —
          red ≥3 dB darker = likely new water, cyan brighter. The pair
          defaults to the passes bracketing the selected day. */}
      {change && change.passes.length >= 2 && (
        <div className="mt-2 rounded-lg border border-cc-line px-2.5 py-2 text-xs" data-change-layer>
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              role="switch"
              aria-checked={change.on}
              aria-disabled={!change.available && !change.on}
              disabled={!change.available && !change.on}
              aria-label={change.available || change.on ? NISAR_CHANGE_LABEL : `${NISAR_CHANGE_LABEL}: ${change.reason ?? "no pair"}`}
              onClick={change.onToggle}
              className={`relative h-[22px] w-[38px] shrink-0 rounded-full border-[1.5px] ${
                change.on ? "border-cc-accent bg-cc-accent" : "border-[#9aa7a0] bg-[#c9d1cb]"
              } ${!change.available && !change.on ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
            >
              <span aria-hidden="true" className={`absolute top-[2px] h-[15px] w-[15px] rounded-full bg-white shadow-[0_0_0_1px_rgba(22,36,43,0.25)] transition-[left] ${change.on ? "left-[19px]" : "left-[2px]"}`} />
            </button>
            <span className="grow text-[13px] font-semibold leading-tight">{NISAR_CHANGE_LABEL}</span>
          </div>
          {change.on && (
            <div className="mt-1.5 grid w-full gap-1" data-change-picks>
              {([["before", "Before"], ["after", "After"]] as const).map(([k, lab]) => {
                const v = k === "before" ? change.before : change.after;
                const opts = !v || change.passes.some((p) => p.value === v)
                  ? change.passes
                  : [...change.passes, { value: v, label: `${fmtDate(v.slice(0, 10))} — no pass` }];
                return (
                  <div key={k} className="flex items-center gap-1" role="group" aria-label={`Change pass ${k}`}>
                    <span className="w-12 shrink-0 text-cc-muted">{lab}</span>
                    <select
                      aria-label={`Change pass ${k}`}
                      value={v ?? ""}
                      onChange={(e) => change.onPair({ [k]: e.target.value })}
                      className="h-7 min-w-0 grow rounded-md border border-cc-line-strong bg-cc-surface px-1 text-xs font-semibold"
                    >
                      {!v && <option value="">—</option>}
                      {opts.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                    </select>
                  </div>
                );
              })}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {change.pols.length > 1 && (
                  <span role="radiogroup" aria-label="Polarization" className="flex gap-1">
                    {change.pols.map((p) => (
                      <button key={p} type="button" role="radio" aria-checked={change.pol === p}
                        onClick={() => change.onPair({ pol: p })}
                        className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${change.pol === p ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`}>{p}</button>
                    ))}
                  </span>
                )}
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={change.cyan} onChange={change.onCyan} />
                  Also show brighter (cyan)
                </label>
                <label className="flex min-w-28 flex-1 items-center gap-1.5">
                  <span className="text-cc-muted">Opacity</span>
                  <input type="range" min={CHANGE_OPACITY_MIN * 100} max={100} step={5}
                    value={Math.round(change.opacity * 100)}
                    onChange={(e) => change.onOpacity(Number(e.target.value) / 100)}
                    aria-valuetext={`${Math.round(change.opacity * 100)} percent`}
                    className="min-w-16 flex-1 accent-cc-accent" />
                </label>
              </div>
            </div>
          )}
          <p className={`m-0 mt-1 text-[11px] leading-tight ${change.reason ? "italic text-cc-warn" : "text-cc-faint"}`} data-change-status>
            {change.reason ?? change.label ?? "Two passes, same track and polarization, with a fixed brightness stretch."}
          </p>
        </div>
      )}

      {/* A caveat stays in view; the method notes fold away. */}
      {caveats.map((l) => (
        <p key={l} className="mt-1.5 flex gap-1.5 rounded-md bg-cc-warn-soft px-2 py-1 text-xs text-cc-warn">
          <Icon d={ICON.warn} size={14} className="mt-px shrink-0" /> <span>{l}</span>
        </p>
      ))}
      {/* lead-ux diet: the honesty text as badges + ONE ⓘ line (the full
          notes and "what the radar sees" fold behind it). */}
      {radar.pair && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px] font-semibold" data-radar-badges>
          <span className="rounded-full border border-cc-line-strong px-2 py-0.5">Candidates, never confirmed</span>
          {radar.provisional && <span className="rounded-full border border-cc-line-strong px-2 py-0.5">Provisional NISAR product</span>}
        </div>
      )}
      <details className="mt-1 text-xs" data-radar-notes>
        <summary className="cursor-pointer text-cc-muted">ⓘ Dark radar = smooth surface, likely water; flooded towns and forests can look bright, so this map may miss them.</summary>
        {radar.pair && (
          <div className="mt-1 rounded-md bg-cc-well px-2 py-1" data-radar-sees>
            <p className="m-0 font-semibold">What the radar sees</p>
            {RADAR_SEES.map((l) => <p key={l} className="m-0 text-cc-muted">{l}</p>)}
            {radar.hypothesisLine && <p className="m-0 mt-0.5 font-semibold" data-radar-test>{radar.hypothesisLine}</p>}
          </div>
        )}
        {notes.length > 0 && (
          <ul className="m-0 mt-1 list-disc pl-4 text-cc-muted">
            {notes.map((l) => <li key={l}>{l}</li>)}
          </ul>
        )}
      </details>
      {radar.nextLook && (
        <div className="mt-1.5 text-xs" data-next-look>
          <p className="m-0 font-semibold">{radar.nextLook}</p>
          {radar.framing && <p className="m-0 text-cc-muted">{radar.framing}</p>}
        </div>
      )}
      {radar.series && radar.series.frames.length > 0 && (
        <details className="mt-1.5 text-xs" data-nisar-frames-wrap>
          <summary className="cursor-pointer font-semibold">Passes ({radar.series.frames.length})</summary>
          <NisarFrames s={radar.series} />
        </details>
      )}
      {(radar.series || radar.extras.length > 0) && (
        <details className="mt-1.5 text-xs" data-more-nisar>
          <summary className="cursor-pointer font-semibold">More NISAR</summary>
          {radar.series && <NisarSeriesChart s={radar.series} />}
      {radar.extras.length > 0 && (
        <div className="mt-2 border-t border-cc-rule pt-1.5 text-xs" data-nisar-extras>
          <p className="m-0 font-semibold">NISAR products</p>
          {radar.extras.map((x, i) => (
            <figure key={layerKey(x, i)} className="m-0 mt-1.5">
              <figcaption className="font-medium">{x.label}{x.date ? ` · ${x.date}` : ""}</figcaption>
              {/* eslint-disable-next-line @next/next/no-img-element -- data PNG from the manifest */}
              <img src={x.url} alt={x.label} loading="lazy" className="mt-0.5 h-auto w-full rounded border border-cc-line bg-black" />
              {x.legend.length > 0 && (
                <ul className="m-0 mt-0.5 flex list-none flex-wrap gap-x-3 p-0 text-cc-muted">
                  {x.legend.map((g) => (
                    <li key={g.label}><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: g.color }} />{g.label}</li>
                  ))}
                </ul>
              )}
              {x.note && <p className="m-0 mt-0.5 text-cc-muted">{x.note}</p>}
              {x.attribution && <p className="m-0 mt-0.5 text-cc-faint">{x.attribution}</p>}
            </figure>
          ))}
        </div>
      )}
        </details>
      )}
      {radar.pair && (
        <details className="mt-1.5 text-xs" data-nisar-why>
          <summary className="cursor-pointer font-semibold">What NISAR sees and why</summary>
          {/* eslint-disable-next-line @next/next/no-img-element -- static explainer in public/ */}
          <img src="/explainer/what-nisar-sees.png" width={1600} height={900} loading="lazy" data-explainer
            alt="What NISAR radar sees: calm open water reflects the pulse away and looks dark; a flooded town or forest bounces it back twice (water, then wall or trunk) and looks bright, so a dark-means-water rule can miss it; L-band sees through cloud and rain, day and night; HH sees surface and double bounce, HV sees volume scatter in vegetation."
            className="mt-1 h-auto w-full rounded border border-cc-line" />
          <ul className="m-0 mt-1 list-disc pl-4 text-cc-muted">
            {NISAR_WHY.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </details>
      )}

      {radar.passes.length > 0 && (
        <details className="mt-1.5 text-xs" data-radar-passes>
          <summary className="cursor-pointer font-semibold">Recent NISAR passes over the area ({radar.passCount})</summary>
          <ul className="m-0 mt-1 list-none p-0">
            {radar.passes.slice(0, PASSES_SHOWN).map((p, i) => (
              <li key={i} className="flex justify-between border-t border-cc-rule py-0.5 text-cc-muted first:border-t-0">
                <span className="tabular-nums">{fmtBrt(p.date)}{p.track ? ` · track ${p.track}` : ""}</span>
                {p.note && <span className="text-cc-faint">{p.note}</span>}
              </li>
            ))}
            {radar.passCount > PASSES_SHOWN && (
              <li className="text-cc-faint">
                and {radar.passCount - PASSES_SHOWN} older passes
              </li>
            )}
          </ul>
        </details>
      )}
    </section>
  );
}

// Before / after dates as chips: one tap each; picking a 'before' on or after
// the current 'after' moves 'after' to the next date (never a dead chip).
function SwipeDatePicker({ dates, before, after, onPick }: { dates: string[]; before: string; after: string; onPick: (b: string, a: string) => void }) {
  const chip = (on: boolean) => `rounded-md px-2 py-0.5 text-xs font-semibold ${on ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`;
  const short = (d: string) => fmtDate(d).replace(/,\s*\d{4}$/, "");
  const next = (d: string) => dates.find((x) => x > d) ?? d;
  return (
    <div className="mt-1.5 grid w-full gap-1 text-xs" data-swipe-dates>
      {/* Lucas (bug): two independent pickers; After is always later than
          Before, so the two halves never show the same pass. */}
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Radar date before">
        <span className="w-12 text-cc-muted">Before</span>
        {dates.slice(0, -1).map((d) => (
          <button key={d} type="button" aria-pressed={d === before} className={chip(d === before)}
            onClick={() => onPick(d, after > d ? after : next(d))}>{short(d)}</button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Radar date after">
        <span className="w-12 text-cc-muted">After</span>
        {dates.filter((d) => d > before).map((d) => (
          <button key={d} type="button" aria-pressed={d === after} className={chip(d === after)}
            onClick={() => onPick(before, d)}>{short(d)}</button>
        ))}
      </div>
    </div>
  );
}
