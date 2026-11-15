"use client";
// Jhonatan's "river's dance": NISAR new-water area per pass (red bars) with
// the Uruguay River stage at Uruguaiana (line) and the flood stage (dashed).
// A bar is a button: it jumps the map to that pass (Radar — NISAR view).
import { fmtDate } from "./model/format";
import { danceCaption, type RiverDance } from "./model/riverDance";
import { FLOODED_FILL } from "./model/radar";

const W = 300, H = 130, PL = 28, PR = 30, PT = 10, PB = 22;
const STAGE = "#2166ac";

export default function RiverDanceChart({ d, onPass, selected }: {
  d: RiverDance;
  onPass?: (date: string, track?: string) => void;
  selected?: string; // date of the pass on the map
}) {
  const t = (s: string) => Date.parse(`${s}T12:00:00Z`);
  const t0 = t(d.passes[0].date), t1 = t(d.passes[d.passes.length - 1].date);
  const x = (s: string) => PL + ((t(s) - t0) / (t1 - t0 || 1)) * (W - PL - PR);
  const wMax = Math.max(...d.passes.map((p) => p.newWaterKm2 ?? 0), 1);
  const stages = d.passes.map((p) => p.stageM).filter((v): v is number => v !== null);
  const sLo = Math.floor(Math.min(...stages, d.floodStageM ?? Infinity) - 0.5);
  const sHi = Math.ceil(Math.max(...stages, d.floodStageM ?? -Infinity) + 0.5);
  const yW = (v: number) => H - PB - (v / wMax) * (H - PT - PB);
  const yS = (v: number) => H - PB - ((v - sLo) / (sHi - sLo || 1)) * (H - PT - PB);
  const gap = Math.min(...d.passes.slice(1).map((p, i) => x(p.date) - x(d.passes[i].date)), 40);
  const bw = Math.max(4, gap * 0.55);
  const line = d.passes.filter((p) => p.stageM !== null)
    .map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${yS(p.stageM!).toFixed(1)}`).join("");
  const cap = danceCaption(d);
  return (
    <div className="mt-2 rounded-lg border border-cc-line px-2.5 py-2 text-xs" data-river-dance>
      <p className="m-0 font-semibold">The river&apos;s dance: NISAR new water vs the Uruguay River at Uruguaiana</p>
      {cap && <p className="m-0 text-[13px] font-semibold" data-dance-r>{cap}</p>}
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-1 w-full max-w-[360px]" role="group"
        aria-label={`NISAR new-water area and river stage on ${d.passes.length} passes`}>
        {[0, wMax / 2, wMax].map((v) => (
          <g key={`w${v}`}>
            <line x1={PL} x2={W - PR} y1={yW(v)} y2={yW(v)} stroke="#d9dee0" strokeWidth={0.5} />
            <text x={PL - 3} y={yW(v) + 3} textAnchor="end" fontSize={8} fill={FLOODED_FILL}>{Math.round(v)}</text>
          </g>
        ))}
        {[sLo, sHi].map((v) => (
          <text key={`s${v}`} x={W - PR + 3} y={yS(v) + 3} fontSize={8} fill={STAGE}>{v} m</text>
        ))}
        {d.floodStageM !== undefined && (
          <g data-dance-flood-stage>
            <line x1={PL} x2={W - PR} y1={yS(d.floodStageM)} y2={yS(d.floodStageM)} stroke={STAGE} strokeWidth={1} strokeDasharray="4 3" />
            <text x={W - PR + 3} y={yS(d.floodStageM) + 3} fontSize={8} fill={STAGE}>{d.floodStageM} m</text>
          </g>
        )}
        {d.passes.map((p) => {
          const v = p.newWaterKm2 ?? 0;
          const label = `${fmtDate(p.date)}${p.track ? ` · track ${p.track}` : ""}: ${p.newWaterKm2 ?? "no"} km² new water${p.stageM !== null ? `, river ${p.stageM} m` : ""}`;
          const on = selected === p.date;
          const above = d.floodStageM !== undefined && p.stageM !== null && p.stageM > d.floodStageM;
          return (
            <g key={`${p.date}${p.track ?? ""}`} role={onPass ? "button" : undefined} tabIndex={onPass ? 0 : undefined}
              aria-label={onPass ? `Show the NISAR pass of ${label}` : label} aria-pressed={onPass ? on : undefined}
              data-dance-bar={p.date} style={{ cursor: onPass ? "pointer" : undefined }}
              onClick={() => onPass?.(p.date, p.track)}
              onKeyDown={(e) => { if (onPass && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onPass(p.date, p.track); } }}>
              <title>{label}</title>
              {/* a tall transparent hit area, so a thin bar is easy to tap */}
              <rect x={x(p.date) - Math.max(bw, 12) / 2} y={PT} width={Math.max(bw, 12)} height={H - PT - PB} fill="transparent" />
              <rect x={x(p.date) - bw / 2} y={yW(v)} width={bw} height={H - PB - yW(v)} fill={FLOODED_FILL}
                fillOpacity={on ? 1 : 0.8}
                // lead-ml: amber border = the gauge above flood stage that day.
                stroke={on ? "#141414" : above ? "#f59e0b" : "none"} strokeWidth={on ? 1.2 : 1.5} data-dance-above={above || undefined} />
            </g>
          );
        })}
        <path d={line} fill="none" stroke={STAGE} strokeWidth={1.8} pointerEvents="none" />
        {d.passes.filter((p) => p.stageM !== null).map((p) => (
          <circle key={`c${p.date}`} cx={x(p.date)} cy={yS(p.stageM!)} r={2} fill={STAGE} pointerEvents="none" />
        ))}
        {[d.passes[0], d.passes[Math.floor(d.passes.length / 2)], d.passes[d.passes.length - 1]].map((p) => (
          <text key={`d${p.date}`} x={x(p.date)} y={H - 8} textAnchor="middle" fontSize={8} fill="#5b6870">
            {fmtDate(p.date).replace(/,\s*\d{4}$/, "")}
          </text>
        ))}
      </svg>
      <ul className="m-0 mt-0.5 flex list-none flex-wrap gap-x-3 gap-y-0.5 p-0 text-cc-muted">
        <li><span className="mr-1 inline-block h-2.5 w-2.5 align-middle" style={{ background: FLOODED_FILL }} />NISAR new water (km², candidate)</li>
        <li><span className="mr-1 inline-block h-0.5 w-3 align-middle" style={{ background: STAGE }} />river stage at Uruguaiana (m)</li>
        {d.floodStageM !== undefined && <li><span className="mr-1 inline-block w-3 border-t border-dashed align-middle" style={{ borderColor: STAGE }} />flood stage</li>}
        {d.floodStageM !== undefined && <li><span className="mr-1 inline-block h-2.5 w-2.5 border-[1.5px] align-middle" style={{ background: FLOODED_FILL, borderColor: "#f59e0b" }} />river above flood stage that day</li>}
      </ul>
      {onPass && <p className="m-0 mt-0.5 text-cc-faint">Tap a bar to see that pass on the map.</p>}
    </div>
  );
}
