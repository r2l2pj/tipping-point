import type { ReactNode } from "react";

// Frame of the one drawer (Situation overview or Public Alerts).
// Wide: a 352 px card floating on the right of the map (open on first load,
// Lucas), at most ~72 % of the height so the map stays >= 70 % visible;
// scrolls inside. Phones: a bottom sheet — down, it is ONE line (`peek`: the
// NISAR summary) that opens it; up, it takes up to 62 % of the height.
export default function SituationPanel({
  label,
  header,
  peek,
  onPeek,
  sheetOpen = false,
  children,
}: {
  label: string;
  header?: ReactNode;
  peek?: string;
  onPeek?: () => void;
  sheetOpen?: boolean;
  children: ReactNode;
}) {
  const down = !!peek && !sheetOpen; // phones only
  return (
    <aside
      aria-label={label}
      className={`flex w-full min-w-0 flex-col rounded-xl border border-cc-line bg-cc-surface shadow-[0_4px_18px_rgba(0,0,0,0.28)] min-[900px]:max-h-[min(calc(100dvh-140px),640px)] min-[900px]:w-[352px] ${sheetOpen ? "max-[899px]:max-h-[62dvh]" : ""}`}
    >
      {peek && (
        <button type="button" onClick={onPeek} data-sheet-peek aria-label={`${peek}. Show details`}
          className={`flex min-h-11 w-full items-center gap-2 px-3 text-left text-[13px] font-semibold min-[900px]:hidden ${down ? "" : "hidden"}`}>
          <span className="min-w-0 grow truncate">{peek}</span>
          <span aria-hidden="true" className="shrink-0 text-cc-muted">▴</span>
        </button>
      )}
      {header && <div className={`shrink-0 border-b border-cc-rule px-2 py-1.5 ${down ? "max-[899px]:hidden" : ""}`}>{header}</div>}
      <div className={`flex min-h-0 flex-col gap-3 overflow-y-auto overscroll-contain px-4 pb-3 pt-2.5 ${down ? "max-[899px]:hidden" : ""}`}>
        {children}
      </div>
    </aside>
  );
}
