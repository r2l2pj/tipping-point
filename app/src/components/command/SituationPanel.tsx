import type { ReactNode } from "react";

// Frame of the one drawer (Situation overview or Public Alerts).
// Wide: a 352 px card floating on the right of the map, scrolling inside.
// Phones: a bottom sheet, up to 62 % of the screen height. Closed by default;
// the "Details ›" edge tab opens it.
export default function SituationPanel({
  label,
  header,
  sheetOpen = false,
  children,
}: {
  label: string;
  header?: ReactNode;
  sheetOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <aside
      aria-label={label}
      className={`flex w-full min-w-0 flex-col rounded-xl border border-cc-line bg-cc-surface shadow-[0_4px_18px_rgba(0,0,0,0.28)] min-[900px]:max-h-full min-[900px]:w-[352px] ${sheetOpen ? "max-[899px]:max-h-[62dvh]" : ""}`}
    >
      {header && <div className="shrink-0 border-b border-cc-rule px-2 py-1.5">{header}</div>}
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto overscroll-contain px-4 pb-3 pt-2.5">
        {children}
      </div>
    </aside>
  );
}
