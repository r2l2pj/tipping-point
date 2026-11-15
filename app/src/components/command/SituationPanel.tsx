import type { ReactNode } from "react";

// Frame of the one drawer (Situation overview or Public Alerts).
// Wide: a 360 px card on the right, scrolling inside. Phones: a bottom sheet;
// closed it shows only its header (the tab row), open it takes up to 62 % of
// the screen height and scrolls inside.
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
      className={`flex w-full min-w-0 flex-col rounded-xl border border-cc-line bg-cc-surface shadow-[0_4px_18px_rgba(0,0,0,0.28)] min-[900px]:max-h-full min-[900px]:w-[372px] min-[1280px]:w-[400px] ${sheetOpen ? "max-[899px]:max-h-[62dvh]" : ""}`}
    >
      {header && <div className="shrink-0 border-b border-cc-rule px-2 py-1.5">{header}</div>}
      <div className={`flex min-h-0 flex-col gap-3 overflow-y-auto overscroll-contain px-4 pb-3 pt-2.5 ${header && !sheetOpen ? "max-[899px]:hidden" : ""}`}>
        {children}
      </div>
    </aside>
  );
}
