"use client";

import { useEffect, useRef, useState } from "react";
import type { Action } from "./state";
import type { CcBundle } from "./model/types";
import { tourStops } from "./StartHere";
import { Icon, ICON } from "./ui";

// lead-ux (Lucas: "too busy"): ONE case picker in the top bar replaces the
// Now / Replay toggle, the past-event select, the freshness pill and the
// "Start here" card. The button names the case in view (line 1) and how
// fresh its data is (line 2); the menu lists the guided tour first, then
// every case. Nothing is removed — each old control is one tap away here.

export const NOW_TITLE = "Now — Southern Brazil";
const TOUR_KEY = "tp-start-here-dismissed"; // same key as the old card

export function caseLabel(bundle: CcBundle | undefined): string {
  if (!bundle) return "No case loaded";
  return bundle.mode === "live" ? NOW_TITLE : bundle.title;
}

export default function CasePicker({
  cases,
  bundle,
  freshness,
  freshnessKind,
  onSelectCase,
  onTour,
  defaultOpen = false,
}: {
  cases: readonly CcBundle[];
  bundle: CcBundle | undefined;
  freshness: string;
  freshnessKind: string;
  onSelectCase: (id: string) => void;
  onTour?: (a: Extract<Action, { type: "goTo" }>) => void;
  defaultOpen?: boolean; // tests: render the menu
}) {
  const [open, setOpen] = useState(defaultOpen);
  const box = useRef<HTMLDivElement>(null);
  const stops = onTour ? tourStops(cases) : [];

  // The tour never opens by itself ('/' lands on the Uruguaiana NISAR view,
  // lead-app; a menu over it would hide it). Until it is used, a small
  // "Tour" hint on the button says it is here.
  const [tourHint, setTourHint] = useState(false);
  useEffect(() => {
    if (!onTour || !stops.length) return;
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- read once after mount (no storage on the server)
      if (window.localStorage.getItem(TOUR_KEY) !== "1") setTourHint(true);
    } catch {
      // storage blocked: no hint; the tour stays one tap away
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const close = () => {
    setOpen(false);
    setTourHint(false);
    try { window.localStorage.setItem(TOUR_KEY, "1"); } catch { /* convenience only */ }
  };
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [open]);

  const live = cases.find((c) => c.mode === "live");
  const ordered = [...(live ? [live] : []), ...cases.filter((c) => c.mode !== "live")];
  const dot =
    freshnessKind === "live" ? "bg-cc-accent"
    : freshnessKind === "archive" ? "bg-cc-faint"
    : "bg-cc-warn";
  const item = "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left hover:bg-cc-well";

  return (
    <div ref={box} className="relative min-w-0 max-[899px]:grow" data-case-picker>
      <button type="button" aria-label={`Case: ${caseLabel(bundle)}. ${freshness}`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}
        className="flex h-10 w-full min-w-0 items-center gap-2 rounded-[10px] border border-cc-line-strong bg-cc-surface pl-2.5 pr-2 text-left min-[900px]:max-w-[420px]">
        <Icon d={bundle?.mode === "live" ? ICON.pin : ICON.clock} size={16} />
        <span className="min-w-0 grow leading-tight">
          <span className="block truncate text-[13px] font-semibold" data-case-title>{caseLabel(bundle)}</span>
          <span className="flex items-center gap-1 truncate text-[11px] text-cc-muted" role="status">
            <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
            <span className="truncate">{freshness}</span>
          </span>
        </span>
        {tourHint && !open && (
          <span data-tour-hint className="shrink-0 rounded-full bg-cc-accent px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-white">Tour</span>
        )}
        <span aria-hidden="true" className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <div role="menu" aria-label="Cases"
          className="z-50 max-h-[75vh] overflow-y-auto max-[899px]:fixed max-[899px]:inset-x-2 max-[899px]:top-[54px] min-[900px]:absolute min-[900px]:left-0 min-[900px]:top-full min-[900px]:mt-1 min-[900px]:w-[420px] rounded-xl border border-cc-line bg-cc-surface p-1.5 text-[13px] shadow-xl">
          {stops.length > 0 && (
            <>
              <p className="m-0 px-2 pb-0.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-cc-faint">Start here — guided tour</p>
              {stops.map((s) => (
                <button key={s.key} type="button" role="menuitem" data-tour={s.key} title={s.what}
                  onClick={() => { onTour?.(s.action); close(); }}
                  className={`${item} ${s.primary ? "font-semibold" : ""}`}>
                  <span className="min-w-0">
                    <span className="block">{s.title}</span>
                    {s.primary && <span className="block text-xs font-normal text-cc-muted">{s.what}</span>}
                  </span>
                </button>
              ))}
              <hr className="my-1 border-cc-rule" />
            </>
          )}
          <p className="m-0 px-2 pb-0.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-cc-faint">All cases</p>
          {ordered.map((c) => (
            <button key={c.id} type="button" role="menuitemradio" aria-checked={c.id === bundle?.id} data-case={c.id}
              onClick={() => { onSelectCase(c.id); close(); }}
              className={`${item} ${c.id === bundle?.id ? "bg-cc-accent-soft font-semibold" : ""}`}>
              <span className="w-12 shrink-0 text-[11px] font-semibold uppercase text-cc-faint">{c.mode === "live" ? "Live" : "Replay"}</span>
              <span className="min-w-0">{caseLabel(c)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
