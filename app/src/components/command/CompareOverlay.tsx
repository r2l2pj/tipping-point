"use client";

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import {
  Map as MapLibreMap,
  setWorkerUrl,
  type ImageSource,
  type Map as MapInstance,
} from "maplibre-gl";
import { setRadarLook, useCompareMode, useRadarLook, useWaterUrl } from "./useWaterRed";
import { WATER_RED_LEGEND } from "./model/waterRamp";
import { FOOTPRINT_LEGEND, footprintFromAlpha } from "./model/footprint";
import { drawFootprint, footprintOf } from "./useFootprint";
import "maplibre-gl/dist/maplibre-gl.css";
import { boundsToImageCoordinates } from "@/lib/tpi";
import { cameraPatch, clipPathFor, dragStep, pctFromClientX, SWIPE_MAX_PCT, SWIPE_MIN_PCT } from "@/lib/swipe";
import { horizonLabel, type ProbHorizon } from "@/lib/layers";
import { fmtDayShort } from "./model/format";
import { FLOODED_FILL, FLOODED_HALO, FLOODED_OPACITY, FLOODED_OUTLINE, type ComparePair } from "./model/radar";
import { ISSUE_LEADS, shiftDay, type IssueLead } from "./model/steps";
import { CHANGE_LEGEND, changeBlockedReason, changeComposite, type BBox as ChangeBBox } from "./model/changeComposite";
import {
  COMPARE_LAYER_LABEL,
  isRiskLayer,
  issueDayOf,
  sideSummary,
  type CompareLayer,
  type CompareLayerInfo,
  type ComparePresetId,
  type Sides,
} from "./model/sideCompare";

// Same worker fix and the same dark basemap as Map.tsx.
setWorkerUrl("/maplibre-gl-worker.js");
const BASEMAP =
  "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";

export interface CompareControls {
  zoomIn: () => void;
  zoomOut: () => void;
  recenter: () => void;
}

// Before / after comparison over the map container — ONE generic compare
// (Lucas, step 2): each side picks its own layer, day and Observed |
// Forecast k / polarization / track / horizon. Mounted only while open.
export default function CompareOverlay({
  pair,
  onClose,
  controlsRef,
  sidesCtl,
}: {
  pair: ComparePair;
  onClose: () => void;
  controlsRef: MutableRefObject<CompareControls | null>;
  // Lucas (step 2): LEFT / RIGHT layer + day + per-layer extras each;
  // preset chips on top; never both sides identical.
  sidesCtl?: {
    sides: Sides;
    layers: CompareLayerInfo[];
    days: Record<"L" | "R", { date: string; label: string; value?: string; track?: string; issue?: string }[]>;
    presets: { id: ComparePresetId; label: string }[];
    warning?: string;
    note?: string;
    // Radar tab / NISAR view: NISAR vs NISAR only — no layer chooser, no presets.
    nisarOnly?: boolean;
    missing?: { L?: string; R?: string };
    onSide: (side: "L" | "R", patch: {
      layer?: CompareLayer; date?: string; lead?: IssueLead;
      pol?: "HH" | "HV"; track?: string; horizon?: ProbHorizon;
    }) => void;
    onPreset: (id: ComparePresetId) => void;
  };
}) {
  // Lucas: NISAR before/after can show the classic change composite (one
  // map, red = new water) — only for a same-track, same-pol NISAR pair.
  // Lucas (demo bug): the overlay is re-mounted for every new pair (MapStage
  // keys it by the image URLs), which reset this to "side" — picking a pass
  // in the Change view threw you back to Side by side and the change picture
  // never updated. The mode lives outside the component now.
  const [mode, setMode] = useCompareMode();
  const bothNisarSides = !!sidesCtl && sidesCtl.sides.L.layer === "nisar" && sidesCtl.sides.R.layer === "nisar";
  const changeBlocked = sidesCtl ? changeBlockedReason(sidesCtl.sides.L, sidesCtl.sides.R) : "Change view needs two NISAR passes";
  const changeOn = mode === "change" && bothNisarSides && !changeBlocked && !!pair.before.url && !!pair.after.url;
  // Lucas: NISAR sides default to 'Water in red' (darker = redder); the
  // Change view keeps the plain frames (it decodes the gray itself).
  const look = useRadarLook();
  const isRadar = (x: ComparePair["before"]) => !x.tiles && !x.geojson && (!!x.sensor || !!x.pol);
  const anyRadar = isRadar(pair.before) || isRadar(pair.after);
  const bUrl = useWaterUrl(isRadar(pair.before) ? pair.before.url : undefined, pair.before.stretch, look === "water");
  const aUrl = useWaterUrl(isRadar(pair.after) ? pair.after.url : undefined, pair.after.stretch, look === "water");
  const shown: ComparePair = {
    ...pair,
    before: { ...pair.before, url: bUrl ?? pair.before.url },
    after: { ...pair.after, url: aUrl ?? pair.after.url },
  };
  return (
    <>
      {changeOn
        ? <ChangeMap pair={pair} onClose={sidesCtl ? undefined : onClose} />
        : <CompareMaps pair={shown} onClose={sidesCtl ? undefined : onClose} controlsRef={controlsRef} />}
      {sidesCtl && (
        <CompareBar sidesCtl={sidesCtl} onClose={onClose}
          radar={anyRadar ? {
            value: changeOn ? "change" : look,
            canChange: bothNisarSides,
            changeBlocked: changeBlocked || (!pair.before.url || !pair.after.url ? "Both NISAR passes must be published" : undefined),
            onChange: (v) => { if (v === "change") setMode("change"); else { setMode("side"); setRadarLook(v); } },
          } : undefined} />
      )}
      {sidesCtl && anyRadar && !changeOn && (
        <span className="pointer-events-none absolute z-30 flex items-center gap-1 rounded-lg bg-white/95 px-2.5 py-1 text-xs font-semibold text-cc-ink shadow"
          style={{ left: "calc(var(--cc-il, 0px) + 14px)", bottom: `calc(var(--cc-ib, 0px) + ${look === "water" ? 128 : 96}px)`, maxWidth: "calc(100% - 28px)" }} data-footprint-legend>
          <span aria-hidden="true" className="inline-block h-2.5 w-6 rounded-sm border-2" style={{ borderColor: "#ff1f1f", boxShadow: "0 0 0 1px #141414" }} />
          {FOOTPRINT_LEGEND}
        </span>
      )}
      {sidesCtl && anyRadar && !changeOn && look === "water" && (
        <span className="pointer-events-none absolute z-30 flex items-center gap-1 rounded-lg bg-white/95 px-2.5 py-1 text-xs font-semibold text-cc-ink shadow"
          style={{ left: "calc(var(--cc-il, 0px) + 14px)", bottom: "calc(var(--cc-ib, 0px) + 96px)", maxWidth: "calc(100% - 28px)" }} data-water-legend>
          <span aria-hidden="true" className="inline-block h-2.5 w-6 rounded-sm" style={{ background: "linear-gradient(90deg,#9a9a9a,#ff1f1f)" }} />
          {WATER_RED_LEGEND}{(isRadar(pair.before) && !pair.before.stretch) || (isRadar(pair.after) && !pair.after.stretch) ? " (approximate)" : ""}
        </span>
      )}
    </>
  );
}

const barMemory: { open: "L" | "R" | null; alive: number } = { open: null, alive: 0 };
type SidesCtl = NonNullable<React.ComponentProps<typeof CompareOverlay>["sidesCtl"]>;
type RadarView = "water" | "plain" | "change";

// lead-ux (audit §4 #7): Compare is a map MODE with one slim bar —
// "Left [side ▾] ⇆ Right [side ▾] · Presets ▾ · ✕" (≤ 44 px at 1440, two
// rows at most on a phone). Each side is one button; its popover holds the
// layer, the day (the NOAA issue day on a forecast side) and Observed |
// Forecast +1 · +2 · +3 / pol / horizon.
function CompareBar({
  sidesCtl,
  onClose,
  radar,
}: {
  sidesCtl: SidesCtl;
  onClose: () => void;
  radar?: { value: RadarView; canChange: boolean; changeBlocked?: string; onChange: (v: RadarView) => void };
}) {
  // MapStage re-keys the overlay on every new image, so a pick inside the
  // popover remounts the bar: reopen the same side when that happens.
  // React renders the new bar while the old one is still mounted
  // (alive > 0): only then is it a remount, not a fresh Compare.
  const [open, setOpen] = useState<"L" | "R" | null>(() => (barMemory.alive > 0 ? barMemory.open : null));
  useEffect(() => { barMemory.alive += 1; return () => { barMemory.alive -= 1; }; }, []);
  useEffect(() => { barMemory.open = open; }, [open]);
  const barRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => { if (!barRef.current?.contains(e.target as Node)) setOpen(null); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(null); } };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("pointerdown", down); document.removeEventListener("keydown", key, true); };
  }, [open]);
  const seg = (on: boolean) =>
    `rounded-full px-2.5 py-0.5 text-xs font-semibold ${on ? "bg-cc-accent text-white" : "text-cc-ink"}`;
  const sel = "h-7 max-w-full rounded-md border border-cc-line-strong bg-cc-surface px-1 text-xs font-semibold";
  const mini = "h-8 shrink-0 rounded-lg border border-cc-line-strong bg-cc-surface px-1.5 text-xs font-semibold text-cc-ink";
  const sideBtn = (side: "L" | "R") => {
    const s = sidesCtl.sides[side];
    const miss = sidesCtl.missing?.[side];
    return (
      <button type="button" data-side-btn={side} aria-haspopup="dialog" aria-expanded={open === side}
        data-layer={s.layer} data-day={isRiskLayer(s.layer) && (s.lead ?? 0) > 0 ? issueDayOf(s) : s.date.slice(0, 10)}
        data-lead={isRiskLayer(s.layer) ? (s.lead ?? 0) : undefined}
        title={miss ? `${sideSummary(s)} — ${miss}` : sideSummary(s)}
        onClick={() => setOpen(open === side ? null : side)}
        // Phone: row 1 = Left + ✕, row 2 = Right + Presets / Radar view.
        className={`flex h-8 min-w-0 items-center gap-1 rounded-lg border px-2 text-xs sm:max-w-[15rem] ${open === side ? "border-cc-accent" : "border-cc-line-strong"} ${side === "L" ? "order-1 max-w-[calc(100vw-120px)]" : "order-3 max-w-[calc(100vw-208px)]"}`}>
        <span className="shrink-0 font-semibold text-cc-muted">{side === "L" ? "Left" : "Right"}</span>
        <span className="truncate font-semibold text-cc-ink" data-side-summary>{sideSummary(s)}</span>
        {miss && <span aria-label={miss} className="shrink-0 font-bold text-cc-warn">!</span>}
        <span aria-hidden="true" className="shrink-0 text-cc-muted">▾</span>
      </button>
    );
  };
  const panel = (side: "L" | "R") => {
    const s = sidesCtl.sides[side];
    const name = side === "L" ? "Left" : "Right";
    const info = sidesCtl.layers.find((l) => l.id === s.layer);
    const days = sidesCtl.days[side];
    // Forecast side: the picker is the ISSUE day X (● = a NOAA run was
    // issued that day); the frame shown is X + lead.
    const lead = isRiskLayer(s.layer) ? (s.lead ?? 0) : 0;
    const issue = lead > 0 ? issueDayOf(s) : undefined;
    const dayOpts = issue
      ? (days.some((d) => d.issue === issue) ? days : [...days, { date: s.date, issue, label: fmtDayShort(issue) }])
      : days.some((d) => d.date === s.date)
        ? days
        : [...days, { date: s.date, label: fmtDayShort(s.date) }];
    return (
      <div role="dialog" aria-label={`${name} side`} data-side={side}
        className={`absolute left-0 top-full z-40 mt-1 flex w-[min(22rem,calc(100vw-32px))] flex-col gap-1.5 rounded-xl border border-cc-line-strong bg-white p-2 text-xs shadow-lg ${side === "R" ? "sm:left-auto sm:right-0" : ""}`}>
        {!sidesCtl.nisarOnly && (
          <label className="flex flex-col gap-0.5"><span className="text-cc-muted">Layer</span>
            <select aria-label={`${name} layer`} value={s.layer}
              onChange={(e) => sidesCtl.onSide(side, { layer: e.target.value as CompareLayer })}
              className={sel} data-layer-select>
              {!info && <option value={s.layer}>{COMPARE_LAYER_LABEL[s.layer]}</option>}
              {sidesCtl.layers.map((l) => (
                <option key={l.id} value={l.id} disabled={!!l.disabled} title={l.disabled}>
                  {l.disabled ? `${l.label} — ${l.disabled}` : l.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {isRiskLayer(s.layer) && info?.canForecast && (
          <div className="flex flex-wrap items-center gap-1" data-risk-mode>
            <button type="button" aria-pressed={lead === 0} onClick={() => sidesCtl.onSide(side, { lead: 0 })} className={seg(lead === 0)}>Observed</button>
            <button type="button" aria-pressed={lead > 0} onClick={() => sidesCtl.onSide(side, { lead: s.lead || 3 })} className={seg(lead > 0)}>Forecast</button>
            {issue && ISSUE_LEADS.map((k) => (
              <button key={k} type="button" aria-pressed={s.lead === k} data-lead={k}
                onClick={() => sidesCtl.onSide(side, { lead: k, date: shiftDay(issue, k) })}
                className={seg(s.lead === k)}>+{k}{k === 3 ? " days" : ""}</button>
            ))}
          </div>
        )}
        {/* NISAR: one list of every pass (date · track · mode), any order. */}
        <label className="flex flex-col gap-0.5"><span className="text-cc-muted">{issue ? "NOAA forecast issued (● = run that day)" : "Day"}</span>
          <select aria-label={issue ? `${name} forecast issue day` : `${name} day`}
            value={issue ?? (s.layer === "nisar" && dayOpts.some((d) => d.value) ? `${s.date}|${s.track ?? ""}` : s.date)}
            onChange={(e) => {
              if (issue) { sidesCtl.onSide(side, { date: shiftDay(e.target.value, lead) }); return; }
              const [date, track] = e.target.value.split("|");
              sidesCtl.onSide(side, track !== undefined ? { date, track } : { date });
            }}
            className={sel} data-day-select data-issue-select={issue ? "" : undefined}>
            {dayOpts.map((d) => {
              const v = issue ? (d.issue ?? d.date) : (d.value ?? d.date);
              return <option key={v} value={v}>{d.label}</option>;
            })}
          </select>
        </label>
        {s.layer === "nisar" && (info?.pols?.length ?? 0) > 1 && (
          <div className="flex flex-wrap items-center gap-1">
            {(info!.pols!).map((p) => (
              <button key={p} type="button" aria-pressed={(s.pol ?? "HH") === p} onClick={() => sidesCtl.onSide(side, { pol: p })} className={seg((s.pol ?? "HH") === p)}>{p}</button>
            ))}
          </div>
        )}
        {s.layer === "ml" && (info?.horizons?.length ?? 0) > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {info!.horizons!.map((h) => (
              <button key={h} type="button" aria-pressed={(s.horizon ?? info!.horizons![0]) === h}
                onClick={() => sidesCtl.onSide(side, { horizon: h })} className={seg((s.horizon ?? info!.horizons![0]) === h)}>{horizonLabel(h)}</button>
            ))}
          </div>
        )}
        {sidesCtl.missing?.[side] && <p className="m-0 italic text-cc-muted">{sidesCtl.missing[side]}</p>}
      </div>
    );
  };
  return (
    // Centred on the visible map, clear of the floating cards and of the map
    // tool column (zoom / recenter) on the right; phone: left-aligned.
    <div ref={barRef}
      className="absolute left-2 top-2 z-30 w-max max-w-[calc(100%-72px)] sm:left-[var(--bar-x)] sm:max-w-[var(--bar-max)] sm:-translate-x-1/2"
      style={{
        "--bar-x": "calc(var(--cc-il, 0px) + (100% - var(--cc-il, 0px) - var(--cc-ir, 0px)) / 2)",
        "--bar-max": "calc(100% - var(--cc-il, 0px) - var(--cc-ir, 0px) - 128px)",
      } as React.CSSProperties}>
      <div role="toolbar" aria-label="Compare" data-compare-sides data-compare-bar
        className="flex flex-wrap items-center gap-1 rounded-xl border border-cc-line-strong bg-white/95 p-1 text-xs shadow">
        {sideBtn("L")}
        <span aria-hidden="true" className="order-2 hidden px-0.5 font-bold text-cc-muted sm:inline">⇆</span>
        {sideBtn("R")}
        {sidesCtl.sides.link && (
          <span className="order-4 shrink-0 rounded-full bg-cc-accent/10 px-2 py-1 font-semibold text-cc-accent" data-compare-link
            title="Both sides move together: forecast for a day vs what happened that day">Linked</span>
        )}
        {(sidesCtl.warning || sidesCtl.note) && (
          <span role="status" className={`order-4 shrink-0 rounded-full px-2 py-1 font-semibold ${sidesCtl.warning ? "bg-cc-warn-soft text-cc-warn" : "text-cc-muted"}`}
            title={sidesCtl.warning ?? sidesCtl.note} data-compare-warning={sidesCtl.warning ? "" : undefined} data-compare-note={sidesCtl.note ? "" : undefined}>
            {sidesCtl.warning ?? "Different tracks"}
          </span>
        )}
        {radar && (
          <select aria-label="Radar view" value={radar.value} onChange={(e) => radar.onChange(e.target.value as RadarView)}
            className={`order-4 w-[7rem] ${mini}`} data-radar-look data-change-mode={radar.canChange ? "" : undefined}>
            <option value="water">Water in red</option>
            <option value="plain">Plain radar</option>
            {radar.canChange && (
              <option value="change" disabled={!!radar.changeBlocked} title={radar.changeBlocked}>
                {radar.changeBlocked ? `Change — ${radar.changeBlocked}` : "Change (red = new water)"}
              </option>
            )}
          </select>
        )}
        {!sidesCtl.nisarOnly && sidesCtl.presets.length > 0 && (
          <select aria-label="Compare presets" value="" data-compare-presets className={`order-4 w-[5.75rem] ${mini}`}
            onChange={(e) => { if (e.target.value) { sidesCtl.onPreset(e.target.value as ComparePresetId); setOpen(null); } }}>
            <option value="" disabled>Presets ▾</option>
            {sidesCtl.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        )}
        <button type="button" onClick={onClose} aria-label="Close compare" title="Close compare" data-compare-close
          className="order-2 ml-auto flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-base font-bold text-cc-ink hover:bg-cc-line sm:order-5 sm:ml-0">✕</button>
      </div>
      {open && panel(open)}
    </div>
  );
}

function CompareMaps({
  pair,
  onClose,
  controlsRef,
}: {
  pair: ComparePair;
  onClose?: () => void; // undefined: the Compare bar's ✕ closes
  controlsRef: MutableRefObject<CompareControls | null>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  // The two maps and the latest pair: a recoloured NISAR frame ('Water in
  // red') swaps the image in place — the camera and the divider stay.
  const maps = useRef<{ b?: MapInstance; a?: MapInstance }>({});
  const latest = useRef(pair);
  useEffect(() => {
    latest.current = pair;
    for (const [m, side] of [[maps.current.b, pair.before], [maps.current.a, pair.after]] as const) {
      const src = m?.getSource("swipe-image") as ImageSource | undefined;
      if (src && src.type === "image" && side.url && !side.tiles) {
        const coords = frameCoordinates(side);
        if (!coords) { console.warn("compare: frame without bounds, not drawn", side.label); continue; }
        try { src.updateImage({ url: side.url, coordinates: coords }); } catch { /* keep the old image */ }
        const mm = m;
        if (mm && (side.pol || side.sensor) && side.bounds && side.bounds.length === 4) {
          void footprintOf(side.url, side.bounds as [number, number, number, number]).then((poly) => drawFootprint(mm, "swipe-footprint", poly));
        }
      }
    }
  }, [pair, pair.before.url, pair.after.url]);
  const beforeEl = useRef<HTMLDivElement>(null);
  const afterEl = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [pct, setPct] = useState(50);
  // QA (v6 demo): start the divider in the middle of the VISIBLE map — the
  // floating cards cover the stage edges (--cc-il / --cc-ir insets).
  useEffect(() => {
    // The card insets are measured after the first paint (deep links open the
    // swipe at once): read them once they have settled.
    const t = setTimeout(() => {
      const el = containerRef.current;
      if (!el || !el.clientWidth || dragging.current) return;
      const css = getComputedStyle(el);
      const il = parseFloat(css.getPropertyValue("--cc-il")) || 0;
      const ir = parseFloat(css.getPropertyValue("--cc-ir")) || 0;
      const w = el.clientWidth;
      const mid = ((il + (w - il - ir) / 2) / w) * 100;
      if (Number.isFinite(mid)) setPct(Math.min(SWIPE_MAX_PCT, Math.max(SWIPE_MIN_PCT, mid)));
    }, 700);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (!beforeEl.current || !afterEl.current) return;
    const make = (container: HTMLDivElement, attribution: boolean) =>
      new MapLibreMap({
        container,
        style: BASEMAP,
        minZoom: 5,
        maxZoom: 13,
        attributionControl: attribution ? { compact: true } : false,
      });
    const beforeMap = make(beforeEl.current, true);
    const afterMap = make(afterEl.current, false);

    // 'move' listeners are detached while applying a jumpTo so the synced
    // camera does not ping-pong between the pair.
    const sync = (src: MapInstance, dst: MapInstance) => () => {
      off();
      const c = src.getCenter();
      dst.jumpTo(cameraPatch({
        center: [c.lng, c.lat], zoom: src.getZoom(),
        bearing: src.getBearing(), pitch: src.getPitch(),
      }));
      on();
    };
    const beforeToAfter = sync(beforeMap, afterMap);
    const afterToBefore = sync(afterMap, beforeMap);
    function on() {
      beforeMap.on("move", beforeToAfter);
      afterMap.on("move", afterToBefore);
    }
    function off() {
      beforeMap.off("move", beforeToAfter);
      afterMap.off("move", afterToBefore);
    }
    on();

    const fit = (map: MapInstance, duration: number) =>
      map.fitBounds(
        [[pair.bounds[0], pair.bounds[1]], [pair.bounds[2], pair.bounds[3]]],
        { padding: 60, duration }
      );
    let fitted = false;
    const onLoad = (map: MapInstance, side: ComparePair["before"]) => {
      // A raster-tile side (NASA GIBS satellite) instead of a PNG image.
      if (side.tiles) {
        map.addSource("swipe-image", {
          type: "raster",
          tiles: side.tiles.tiles,
          tileSize: side.tiles.tileSize,
          maxzoom: side.tiles.maxzoom,
          attribution: side.tiles.attribution,
        });
      } else if (side.url && frameCoordinates(side)) {
        map.addSource("swipe-image", {
          type: "image",
          url: side.url,
          coordinates: frameCoordinates(side)!,
        });
      } else if (side.url) {
        console.warn("compare: frame without bounds, not drawn", side.label);
      }
      if (map.getSource("swipe-image")) map.addLayer({ id: "swipe-image", type: "raster", source: "swipe-image" });
      // Lucas: the red outline of the NISAR swath on a radar side.
      if ((side.pol || side.sensor) && side.url && side.bounds && side.bounds.length === 4) {
        const bb = side.bounds as [number, number, number, number];
        void footprintOf(side.url, bb).then((poly) => drawFootprint(map, "swipe-footprint", poly));
      }
      // NASA OPERA surface water (DSWx) polygons as a side of their own.
      if (side.geojson) {
        map.addSource("swipe-geo", { type: "geojson", data: side.geojson });
        map.addLayer({ id: "swipe-geo-fill", type: "fill", source: "swipe-geo", paint: { "fill-color": "#1f78b4", "fill-opacity": 0.55 } });
        map.addLayer({ id: "swipe-geo-line", type: "line", source: "swipe-geo", paint: { "line-color": "#0b3d66", "line-width": 1 } });
      }
      // Flooded area (radar) in its own colour above both radar images.
      if (pair.flood) {
        map.addSource("swipe-flood", { type: "geojson", data: pair.flood.url });
        map.addLayer({ id: "swipe-flood-fill", type: "fill", source: "swipe-flood",
          paint: { "fill-color": FLOODED_FILL, "fill-opacity": FLOODED_OPACITY } });
        map.addLayer({ id: "swipe-flood-halo", type: "line", source: "swipe-flood",
          paint: { "line-color": FLOODED_HALO, "line-width": 3.5, "line-opacity": 0.55 } });
        map.addLayer({ id: "swipe-flood-line", type: "line", source: "swipe-flood",
          paint: { "line-color": FLOODED_OUTLINE, "line-width": 1.5 } });
      }
      // Only the first map to load fits; its moves sync the other one.
      if (!fitted) {
        fitted = true;
        fit(map, 0);
      }
    };
    maps.current = { b: beforeMap, a: afterMap };
    beforeMap.on("load", () => onLoad(beforeMap, latest.current.before));
    afterMap.on("load", () => onLoad(afterMap, latest.current.after));

    controlsRef.current = {
      zoomIn: () => beforeMap.zoomIn(),
      zoomOut: () => beforeMap.zoomOut(),
      recenter: () => fit(beforeMap, 400),
    };
    return () => {
      controlsRef.current = null;
      maps.current = {};
      beforeMap.remove();
      afterMap.remove();
    };
    // The imagery is fixed for the lifetime of the overlay; a new pair
    // mounts a fresh component (keyed by the caller).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fromPointer = (clientX: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (rect) setPct(pctFromClientX(clientX, rect.left, rect.width));
  };
  const tag = "pointer-events-none absolute top-3 z-10 rounded-lg bg-white/95 px-2.5 py-1 text-xs font-semibold text-cc-ink shadow";

  return (
    <div ref={containerRef} data-compare={pair.kind} className="absolute inset-0 z-20 overflow-hidden bg-cc-map">
      {/* MapLibre styles its container `.maplibregl-map { position: relative }`,
          which overrides `absolute inset-0` on the container itself: the
          div collapsed to 0 px (canvas 300 px, clipped) and the comparison
          rendered blank. Each map fills its own positioned box instead. */}
      <div className="absolute inset-0">
        <div ref={beforeEl} className="h-full w-full" />
      </div>
      <div className="absolute inset-0" style={{ clipPath: clipPathFor(pct) }}>
        <div ref={afterEl} className="h-full w-full" />
      </div>
      {/* Below the slim Compare bar (top 8 px, ≤ 44 px tall). */}
      <div className={tag} style={{ left: "calc(var(--cc-il, 0px) + 14px)", top: 58 }} data-swipe-tag="L">{pair.before.label}</div>
      <div className={tag} style={{ right: "calc(var(--cc-ir, 0px) + 68px)", top: 58 }} data-swipe-tag="R">{pair.after.label}</div>
      {pair.flood && (
        <div data-swipe-legend className="pointer-events-none absolute z-10 flex items-center gap-1.5 rounded-lg bg-white/95 px-2.5 py-1 text-xs font-semibold text-cc-ink shadow"
          style={{ left: "calc(var(--cc-il, 0px) + 56px)", bottom: "calc(var(--cc-ib, 0px) + 12px)" }}>
          <span className="inline-block h-3 w-4 rounded-sm border border-white" style={{ background: FLOODED_FILL, outline: "1px solid #5b6870" }} />
          {pair.flood.label}
        </div>
      )}
      <div
        role="slider"
        tabIndex={0}
        aria-label="Divider between the image before the event and the image after it"
        aria-orientation="horizontal"
        aria-valuemin={SWIPE_MIN_PCT}
        aria-valuemax={SWIPE_MAX_PCT}
        aria-valuenow={Math.round(pct)}
        className="absolute inset-y-0 z-20 w-8 -translate-x-1/2 cursor-ew-resize touch-none"
        style={{ left: `${pct}%` }}
        onPointerDown={(e) => {
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          fromPointer(e.clientX);
        }}
        onPointerMove={(e) => {
          if (dragging.current) fromPointer(e.clientX);
        }}
        onPointerUp={() => {
          dragging.current = false;
        }}
        onPointerCancel={() => {
          dragging.current = false;
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            setPct((p) => dragStep(p, e.key === "ArrowLeft" ? -1 : 1));
            e.preventDefault();
          }
        }}
      >
        <div className="pointer-events-none absolute inset-y-0 left-1/2 w-[3px] -translate-x-1/2 bg-white shadow-[0_0_0_1px_rgba(0,0,0,0.45)]" />
        <div className="pointer-events-none absolute left-1/2 top-1/2 flex h-[30px] w-[30px] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-cc-ink bg-white">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#16242b" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9 7l-5 5 5 5M15 7l5 5-5 5" />
          </svg>
        </div>
      </div>
      {onClose && <button type="button" onClick={onClose}
        style={{ bottom: "calc(var(--cc-ib, 0px) + 12px)" }}
        className="absolute left-1/2 z-30 -translate-x-1/2 rounded-lg bg-white px-3.5 py-2 text-[13px] font-semibold text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.35)]">
        Close compare
      </button>}
    </div>
  );
}

// Lucas: ONE map with the before/after change composite (R = before HH,
// G = B = after HH), computed in the browser from the two frame PNGs; the
// NISAR flood candidates toggle on top.
function ChangeMap({ pair, onClose }: { pair: ComparePair; onClose?: () => void }) {
  const el = useRef<HTMLDivElement>(null);
  const [img, setImg] = useState<{ url: string; bounds: ChangeBBox; stats: string; footprint?: GeoJSON.Polygon } | { error: string }>();
  const [showFlood, setShowFlood] = useState(true);
  const mapRef = useRef<MapLibreMap | null>(null);

  useEffect(() => {
    let alive = true;
    const load = (src: string) => new Promise<HTMLImageElement>((ok, fail) => {
      const im = new Image();
      im.crossOrigin = "anonymous";
      im.onload = () => ok(im);
      im.onerror = () => fail(new Error(src));
      im.src = src;
    });
    const pixels = (im: HTMLImageElement) => {
      const c = document.createElement("canvas");
      c.width = im.naturalWidth; c.height = im.naturalHeight;
      const ctx = c.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(im, 0, 0);
      return ctx.getImageData(0, 0, c.width, c.height);
    };
    // Each frame on ITS OWN bounds (never pair.bounds: that is the camera).
    const bb = pair.before.bounds, ab = pair.after.bounds;
    Promise.all([load(pair.before.url), load(pair.after.url)]).then(([b, a]) => {
      if (!bb || !ab) { if (alive) setImg({ error: "A NISAR frame has no published bounds" }); return; }
      const before = pixels(b), after = pixels(a);
      const out = changeComposite(
        { data: before.data, width: before.width, height: before.height, bounds: bb as ChangeBBox, stretch: pair.before.stretch },
        { data: after.data, width: after.width, height: after.height, bounds: ab as ChangeBBox, stretch: pair.after.stretch },
      );
      const c = document.createElement("canvas");
      c.width = after.width; c.height = after.height;
      c.getContext("2d")!.putImageData(new ImageData(out, after.width, after.height), 0, 0);
      let opaque = 0;
      for (let k = 3; k < out.length; k += 4) if (out[k]) opaque++;
      // Lucas: the red outline of where BOTH passes imaged (the composite's alpha).
      const footprint = footprintFromAlpha(out, after.width, after.height, ab as ChangeBBox);
      if (alive) setImg({ url: c.toDataURL("image/png"), bounds: ab as ChangeBBox, stats: `${after.width}x${after.height} opaque ${opaque}`, footprint });
    }).catch(() => { if (alive) setImg({ error: "Could not read the two radar images for the change view" }); });
    return () => { alive = false; };
  }, [pair.before.url, pair.after.url, pair.before.bounds, pair.after.bounds]);

  useEffect(() => {
    if (!el.current || !img || "error" in img) return;
    const map = new MapLibreMap({ container: el.current, style: BASEMAP, minZoom: 5, maxZoom: 13, attributionControl: { compact: true } });
    mapRef.current = map;
    map.on("load", () => {
      map.addSource("change-image", { type: "image", url: img.url, coordinates: boundsToImageCoordinates(img.bounds) });
      map.addLayer({ id: "change-image", type: "raster", source: "change-image", paint: { "raster-opacity": 0.95 } });
      drawFootprint(map, "change-footprint", img.footprint);
      if (pair.flood) {
        map.addSource("change-flood", { type: "geojson", data: pair.flood.url });
        map.addLayer({ id: "change-flood-fill", type: "fill", source: "change-flood", paint: { "fill-color": FLOODED_FILL, "fill-opacity": FLOODED_OPACITY } });
        map.addLayer({ id: "change-flood-halo", type: "line", source: "change-flood", paint: { "line-color": FLOODED_HALO, "line-width": 3.5, "line-opacity": 0.55 } });
        map.addLayer({ id: "change-flood-line", type: "line", source: "change-flood", paint: { "line-color": FLOODED_OUTLINE, "line-width": 1.5 } });
      }
      // CAMERA only: pair.bounds (both frames, or the Uruguaiana river window
      // set by MapStage). The image above sits on its own img.bounds.
      const fb = pair.bounds ?? img.bounds;
      map.fitBounds([[fb[0], fb[1]], [fb[2], fb[3]]], { padding: 60, duration: 0 });
    });
    return () => { mapRef.current = null; map.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the fit is read once per composite; a new bounds object must not rebuild the map
  }, [img, pair.flood]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    for (const id of ["change-flood-fill", "change-flood-halo", "change-flood-line"]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", showFlood ? "visible" : "none");
    }
  }, [showFlood]);

  return (
    <div data-compare="change" data-change-map data-change-stats={img && !("error" in img) ? img.stats : undefined} className="absolute inset-0 z-20 overflow-hidden bg-cc-map">
      {/* MapLibre sets its container to position:relative — the map gets its
          own full-size box inside a positioned wrapper (as CompareMaps). */}
      <div className="absolute inset-0"><div ref={el} className="h-full w-full" /></div>
      {!img && <p className="absolute left-1/2 top-1/2 z-30 m-0 -translate-x-1/2 rounded-lg bg-white/95 px-3 py-1.5 text-sm shadow">Building the change view…</p>}
      {img && "error" in img && <p className="absolute left-1/2 top-1/2 z-30 m-0 -translate-x-1/2 rounded-lg bg-white/95 px-3 py-1.5 text-sm shadow" role="alert">{img.error}</p>}
      <div className="absolute z-30 flex max-w-[calc(100%-24px)] flex-col gap-1 rounded-lg bg-white/95 px-2.5 py-1.5 text-xs text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)]"
        style={{ left: "calc(var(--cc-il, 0px) + 14px)", bottom: "calc(var(--cc-ib, 0px) + 60px)" }} data-change-legend>
        <span className="font-semibold">{pair.before.label} → {pair.after.label}</span>
        {(!pair.before.stretch || !pair.after.stretch) && (
          <span className="rounded bg-cc-warn-soft px-1.5 py-0.5 font-semibold text-cc-warn" data-change-preview>
            Preview only — the radar brightness stretch is not fixed yet for these frames
          </span>
        )}
        <span className="flex flex-wrap items-center gap-x-2">
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: "#d0262a" }} />Red: radar got darker = likely new water
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: "#30d0d0" }} />Cyan: got brighter
          <span className="inline-block h-3 w-3 rounded-sm" style={{ background: "#8a8a8a" }} />Grey: no change
        </span>
        <span className="flex items-center gap-1" data-footprint-legend>
          <span aria-hidden="true" className="inline-block h-2.5 w-6 rounded-sm border-2" style={{ borderColor: "#ff1f1f", boxShadow: "0 0 0 1px #141414" }} />
          {FOOTPRINT_LEGEND}
        </span>
        <span className="sr-only">{CHANGE_LEGEND}</span>
        {pair.flood && (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={showFlood} onChange={(e) => setShowFlood(e.target.checked)} />
            {pair.flood.label} on top
          </label>
        )}
      </div>
      {onClose && <button type="button" onClick={onClose}
        style={{ bottom: "calc(var(--cc-ib, 0px) + 12px)" }}
        className="absolute left-1/2 z-30 -translate-x-1/2 rounded-lg bg-white px-3.5 py-2 text-[13px] font-semibold text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.35)]">
        Close compare
      </button>}
    </div>
  );
}

// Image coordinates of a compare side: its OWN frame bounds, or nothing (a
// frame without bounds is not drawn — never stretched into the pair's
// camera box, e.g. the river window).
export function frameCoordinates(side: { bounds?: readonly number[] }): ReturnType<typeof boundsToImageCoordinates> | undefined {
  const b = side.bounds;
  return b && b.length === 4 && b.every((v) => Number.isFinite(v)) ? boundsToImageCoordinates(b as [number, number, number, number]) : undefined;
}
