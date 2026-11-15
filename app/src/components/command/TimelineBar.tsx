"use client";

import { useEffect, useRef } from "react";
import { forecastChip, isGfsFrame } from "@/lib/layers";
import type { ManifestLayer } from "@/lib/types";
import { NO_STEPS_TEXT, type Step, type StepMark } from "./model/steps";
import { Icon, ICON } from "./ui";
import { fmtDate } from "./model/format";
import { HOURS } from "./model/hours";

// Width of one step on the thin track; above DENSE_FROM steps the narrow one.
const STEP_PX = 40;
const STEP_DENSE_PX = 30;
const DENSE_FROM = 16;
// Room kept beside the selected step when the track scrolls it into view.
const SCROLL_MARGIN_PX = 12;

const MARK_BG = (c: StepMark["color"]) => (c === "red" ? "bg-[#bd0026]" : c === "blue" ? "bg-[#2166ac]" : "bg-[#b06f00]");

// lead-ux (Lucas: "the focus is still not the map"): ONE thin bar over the
// bottom of the map — play · prev / next · day picker · hour / run pickers ·
// forecast badge · the step track (scrolls inside, keeps the selected day in
// view). The day's timeline marks show as one line above the bar.
export default function TimelineBar({
  steps,
  marks,
  dayIndex,
  playing,
  forecastTag,
  mapFrame,
  onSeek,
  onPrev,
  onNext,
  onPlayToggle,
  children,
  emptyText = NO_STEPS_TEXT,
  hours,
  forecastDates,
  runs,
}: {
  // Lucas (Now): the last Now runs (every 3 h), newest first.
  runs?: { list: { id: string; label: string }[]; picked: string; onPick: (id: string | null) => void };
  // Replay: days that have an archived NOAA forecast (marked in the picker).
  forecastDates?: Set<string>;
  // Lucas: HOUR selector (6-hourly frames); only for cases that publish them.
  hours?: { available: string[]; hour: string | null; onHour: (h: string | null) => void };
  emptyText?: string;
  steps: Step[];
  marks: StepMark[];
  dayIndex: number;
  playing: boolean;
  forecastTag: string;
  // The TPI frame on the map. The forecast badge names its issued run when it
  // is a GFS frame, so the badge and the map caption never disagree.
  mapFrame?: ManifestLayer;
  onSeek: (i: number) => void;
  onPrev: () => void;
  onNext: () => void;
  onPlayToggle: () => void;
  children?: React.ReactNode;
}) {
  const n = steps.length;
  // Keep the selected step in view when the steps scroll sideways (the track
  // scrolls, never the page).
  const track = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // PR #5 (teammate): a step's offsetLeft is relative to its offsetParent,
    // not the track, so the position comes from bounding boxes plus the
    // track's own scrollLeft; the date label can be wider than a dense step.
    const box = track.current;
    const el = box?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!box || !el) return;
    const own = el.getBoundingClientRect();
    let from = own.left;
    let to = own.right;
    for (const part of el.querySelectorAll("span")) {
      const r = part.getBoundingClientRect();
      from = Math.min(from, r.left);
      to = Math.max(to, r.right);
    }
    const boxRect = box.getBoundingClientRect();
    const origin = boxRect.left + box.clientLeft - box.scrollLeft; // content x = 0
    const start = from - origin - SCROLL_MARGIN_PX;
    const end = to - origin + SCROLL_MARGIN_PX;
    // The browser clamps scrollLeft to the scrollable range.
    if (start < box.scrollLeft) box.scrollLeft = start;
    else if (end > box.scrollLeft + box.clientWidth) box.scrollLeft = end - box.clientWidth;
  }, [dayIndex, n]);
  const chipText = mapFrame && isGfsFrame(mapFrame) ? forecastChip(mapFrame, undefined) : forecastTag;
  const stepTag = (s: Step) =>
    s.forecast && s.layer && isGfsFrame(s.layer) ? forecastChip(s.layer, undefined) : forecastTag;
  const dense = n > DENSE_FROM;
  const eventIndex = Math.max(0, steps.findIndex((s) => s.offset === 0));
  // Every date in the title / accessible name; on the track the selected
  // step, the event day and every second (dense: third) step print it.
  const showDate = (i: number): boolean => {
    if (i === dayIndex || i === eventIndex) return true;
    if (Math.abs(i - dayIndex) === 1) return false;
    return (i - eventIndex) % (dense ? 3 : 2) === 0;
  };
  const dayMarks = marks.filter((m) => m.index === dayIndex);
  const btn = "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-cc-line-strong bg-cc-surface disabled:cursor-not-allowed disabled:opacity-40";
  const sel = "h-8 shrink-0 rounded-lg border border-cc-line-strong bg-cc-surface px-1.5 text-[13px] font-semibold text-cc-ink";
  return (
    <div className="relative" data-timeline>
      {/* Landing on a marked day names it (lead-analysis timeline marks). */}
      {dayMarks.length > 0 && (
        <p className="m-0 mb-1 flex gap-1 overflow-hidden text-xs font-semibold" data-mark-caption>
          {dayMarks.map((m) => (
            <span key={m.label} title={m.label} className={`truncate rounded px-1.5 py-0.5 text-white shadow ${MARK_BG(m.color)}`}>{m.label}</span>
          ))}
        </p>
      )}
      <div className="flex h-12 items-center gap-1.5 rounded-xl border border-cc-line bg-cc-surface/95 px-2 shadow-[0_4px_18px_rgba(0,0,0,0.28)]">
        <button type="button" onClick={onPlayToggle} disabled={n < 2} aria-label={playing ? "Pause" : "Play"}
          className="inline-flex h-8 w-9 shrink-0 items-center justify-center rounded-lg bg-cc-accent text-white disabled:cursor-not-allowed disabled:opacity-40">
          <Icon d={playing ? ICON.pause : ICON.play} size={16} fill={!playing} strokeWidth={playing ? 3 : 2.2} />
        </button>
        <button type="button" onClick={onPrev} disabled={n === 0 || dayIndex <= 0} aria-label="Previous step" className={btn}>
          <Icon d="M15 6l-6 6 6 6" size={16} strokeWidth={2.4} />
        </button>
        <button type="button" onClick={onNext} disabled={n === 0 || dayIndex >= n - 1} aria-label="Next step" className={btn}>
          <Icon d="M9 6l6 6-6 6" size={16} strokeWidth={2.4} />
        </button>
        {/* Lucas: a date picker, not only the step dots. */}
        {n > 1 && (
          <select aria-label="Pick a day" value={dayIndex} onChange={(e) => onSeek(Number(e.target.value))} data-day-picker
            className={`${sel} min-w-0 max-w-[230px] max-[639px]:max-w-[150px]`}>
            {steps.map((s) => (
              <option key={s.date} value={s.index}>{fmtDate(s.date)}{s.forecast ? " (forecast)" : ""}{forecastDates?.has(s.date) ? " · ● NOAA forecast" : ""}</option>
            ))}
          </select>
        )}
        {hours && hours.available.length > 0 && (
          <select aria-label="Hour (UTC)" data-hours value={hours.hour ?? ""} onChange={(e) => hours.onHour(e.target.value || null)} className={sel}>
            <option value="">Day</option>
            {HOURS.filter((h) => hours.available.includes(h)).map((h) => (
              <option key={h} value={h}>{h}Z</option>
            ))}
          </select>
        )}
        {runs && (
          <select aria-label="Now run (BRT)" data-runs value={runs.picked}
            onChange={(e) => { const i = runs.list.findIndex((r) => r.id === e.target.value); runs.onPick(i <= 0 ? null : e.target.value); }}
            className={`${sel} max-[639px]:hidden`}>
            {runs.list.map((r, i) => (
              <option key={r.id} value={r.id}>{i === 0 ? `Run: latest · ${r.label}` : `Run: ${r.label}`}</option>
            ))}
          </select>
        )}
        {/* Honesty: the forecast source is named whenever the case has forecast days. */}
        {steps.some((s) => s.forecast) && (
          <span title={chipText} data-forecast-badge
            className="max-w-[170px] shrink-0 truncate rounded-md bg-cc-forecast px-2 py-0.5 text-xs font-semibold text-cc-forecast-ink max-[899px]:hidden">
            {chipText}
          </span>
        )}

        <div ref={track} className="relative min-w-0 flex-1 overflow-x-auto [scrollbar-width:none]">
          {n === 0 ? (
            <p className="m-0 truncate px-2 text-[13px] italic text-cc-faint" data-no-steps title={emptyText}>{emptyText}</p>
          ) : (
            <div className="relative flex px-1" style={{ minWidth: n * (dense ? STEP_DENSE_PX : STEP_PX) }} role="group" aria-label="Timeline steps">
              {steps.map((s) => {
                const on = s.index === dayIndex;
                const event = s.offset === 0;
                return (
                  <button
                    key={s.date}
                    type="button"
                    onClick={() => onSeek(s.index)}
                    aria-pressed={on}
                    aria-label={`${s.top}${s.sub ? `, ${s.sub}` : ""}${s.forecast ? `, ${stepTag(s)}` : ""}`}
                    title={`${s.date}${s.sub ? ` (${s.sub})` : ""}${s.day && typeof s.day.tpi === "number" ? ` — TPI ${s.day.tpi.toFixed(2)}` : ""}${marks.filter((m) => m.index === s.index).map((m) => ` · ${m.label}`).join("")}`}
                    className="relative flex h-9 min-w-0 flex-1 flex-col items-center"
                  >
                    {/* Track: solid for observed steps, dashed for forecast. */}
                    <span aria-hidden="true" className={`absolute left-0 right-0 top-[9px] ${s.forecast ? "border-t-2 border-dashed border-[#9aa7a0]" : "h-[2px] bg-cc-accent"}`} />
                    <span aria-hidden="true" className="relative flex h-5 items-center">
                      <span className={`box-border rounded-full ${on ? "h-4 w-4 border-[3px] border-cc-accent bg-white shadow-[0_0_0_3px_rgba(23,71,63,0.25)]"
                        : s.forecast ? "h-2.5 w-2.5 border-2 border-dashed border-[#5f6d73] bg-white"
                        : "h-2.5 w-2.5 border-2 border-cc-accent bg-cc-accent"}`} />
                    </span>
                    {marks.some((m) => m.index === s.index) && (
                      <span aria-hidden="true" className="absolute right-0.5 top-0 flex gap-px">
                        {marks.filter((m) => m.index === s.index).map((m) => (
                          <span key={m.label} className={`h-1.5 w-1.5 rounded-full ${MARK_BG(m.color)}`} />
                        ))}
                      </span>
                    )}
                    <span className={`whitespace-nowrap text-[10px] leading-none ${on ? "font-bold text-cc-ink" : "text-cc-muted"} ${event && !on ? "font-semibold text-cc-ink underline" : ""} ${showDate(s.index) ? "" : "invisible"}`}>{s.top}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        {children}
      </div>
    </div>
  );
}
