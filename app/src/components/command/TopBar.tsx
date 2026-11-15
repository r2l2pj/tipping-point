"use client";

import { useEffect, useRef, useState } from "react";
import CasePicker from "./CasePicker";
import type { Action } from "./state";
import type { CcBundle } from "./model/types";
import { STATUS_WORD, type DataStatusKind } from "./model/dataStatus";
import { Icon, ICON } from "./ui";

export type View = "command" | "public";

// lead-ux (Lucas: "too busy"): ONE row. Logo · case picker (case + Now /
// Replay + freshness + guided tour) · city search · menu (Command Center /
// Public Alerts). Phones: the search is an icon that opens a row under the
// bar, so the bar stays one 52 px row and the map starts right under it.
const SEARCH_ICON = "M10.5 4a6.5 6.5 0 104.2 11.5L20 20.5M15.5 10.5a5 5 0 11-10 0 5 5 0 0110 0";
const MENU_ICON = "M4 7h16M4 12h16M4 17h16";

export default function TopBar({
  cases,
  bundle,
  view,
  onView,
  onSelectCase,
  onTour,
  onNow,
  freshness,
  freshnessKind,
  search,
}: {
  cases: readonly CcBundle[];
  bundle: CcBundle | undefined;
  view: View;
  onView: (v: View) => void;
  onSelectCase: (id: string) => void;
  onTour?: (a: Extract<Action, { type: "goTo" }>) => void;
  onNow?: () => void; // Replay: one click back to the live map (Lucas)
  freshness: string;
  freshnessKind: string;
  search?: React.ReactNode;
}) {
  const [searchOpen, setSearchOpen] = useState(false); // phones only
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [menuOpen]);
  const iconBtn = "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] border border-cc-line-strong bg-cc-surface text-cc-ink hover:bg-cc-well";
  const viewItem = (on: boolean) =>
    `flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-cc-well ${on ? "bg-cc-accent-soft font-semibold" : ""}`;

  return (
    <header className="relative z-40 flex h-[52px] shrink-0 items-center gap-2 border-b border-cc-line bg-cc-header px-3 min-[900px]:gap-3 min-[900px]:px-4">
      <span className="flex shrink-0 items-center gap-2" aria-label="Tipping Point">
        <svg width="32" height="20" viewBox="0 0 34 22" fill="none" stroke="var(--cc-accent)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2 19l8-12 5 7 4-6 7 11" /><path d="M20 19l5-7 7 7" />
        </svg>
        <span className="font-cc-display text-[18px] font-bold tracking-[0.04em] max-[899px]:hidden">TIPPING POINT</span>
        <span className="hidden border-l border-cc-line-strong pl-2.5 text-[13px] text-cc-muted min-[1500px]:inline">Rain warns. Radar sees through.</span>
      </span>

      <CasePicker cases={cases} bundle={bundle} freshness={freshness} freshnessKind={freshnessKind}
        onSelectCase={onSelectCase} onTour={onTour} />
      {/* Honesty: a warning word when the data is not fresh live / replay data. */}
      {freshnessKind !== "live" && freshnessKind !== "archive" && freshnessKind in STATUS_WORD && (
        <span data-status={freshnessKind} className="shrink-0 rounded-full border border-[#e2c48a] bg-cc-warn-soft px-2.5 py-1 text-xs font-semibold text-cc-warn max-[899px]:hidden">
          {STATUS_WORD[freshnessKind as DataStatusKind]}
        </span>
      )}
      {onNow && (
        <button type="button" data-back-to-live onClick={onNow} title="Back to live (Now)"
          className="inline-flex h-10 shrink-0 items-center rounded-[10px] border border-cc-accent-line bg-cc-accent-soft px-2.5 text-xs font-semibold text-cc-accent">
          ← Now
        </button>
      )}

      {view === "public" && (
        <button type="button" onClick={() => onView("command")} title="Back to the Command Center"
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-cc-accent px-3 text-xs font-semibold text-white">
          <Icon d={ICON.megaphone} size={14} /> <span className="max-[899px]:hidden">Public alerts</span> <Icon d={ICON.close} size={12} />
        </button>
      )}

      <div className="grow max-[899px]:hidden" />
      {search && (
        <>
          {/* One CitySearch instance (it owns the "/" key and the ?city= link):
              inline on wide screens, a row under the bar on phones when opened. */}
          <div data-search-slot className={`min-[900px]:w-[260px] min-[900px]:shrink ${searchOpen
            ? "max-[899px]:absolute max-[899px]:inset-x-0 max-[899px]:top-full max-[899px]:border-b max-[899px]:border-cc-line max-[899px]:bg-cc-header max-[899px]:p-2"
            : "max-[899px]:hidden"}`}>{search}</div>
          <button type="button" aria-label="Open city search" aria-expanded={searchOpen} onClick={() => setSearchOpen((v) => !v)}
            className={`${iconBtn} min-[900px]:hidden`}>
            <Icon d={SEARCH_ICON} size={18} />
          </button>
        </>
      )}

      <div ref={menuRef} className="relative">
        <button type="button" aria-label="Menu" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)} className={iconBtn}>
          <Icon d={MENU_ICON} size={18} />
        </button>
        {menuOpen && (
          <div role="menu" aria-label="View"
            className="absolute right-0 top-full z-50 mt-1 w-[240px] rounded-xl border border-cc-line bg-cc-surface p-1.5 shadow-xl">
            <button type="button" role="menuitemradio" aria-checked={view === "command"} onClick={() => { onView("command"); setMenuOpen(false); }} className={viewItem(view === "command")}>
              <Icon d={ICON.command} size={16} /> Command Center
            </button>
            <button type="button" role="menuitemradio" aria-checked={view === "public"} onClick={() => { onView("public"); setMenuOpen(false); }} className={viewItem(view === "public")}>
              <Icon d={ICON.megaphone} size={16} /> Public Alerts
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
