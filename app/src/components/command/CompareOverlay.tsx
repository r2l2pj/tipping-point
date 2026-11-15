"use client";

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import {
  Map as MapLibreMap,
  setWorkerUrl,
  type Map as MapInstance,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { boundsToImageCoordinates } from "@/lib/tpi";
import { cameraPatch, clipPathFor, dragStep, pctFromClientX, SWIPE_MAX_PCT, SWIPE_MIN_PCT } from "@/lib/swipe";
import { horizonLabel, type ProbHorizon } from "@/lib/layers";
import { fmtDayShort } from "./model/format";
import { FLOODED_FILL, FLOODED_OUTLINE, type ComparePair } from "./model/radar";
import { ISSUE_LEADS, type IssueLead } from "./model/steps";
import {
  COMPARE_LAYER_LABEL,
  isRiskLayer,
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
    days: Record<"L" | "R", { date: string; label: string }[]>;
    presets: { id: ComparePresetId; label: string }[];
    warning?: string;
    missing?: { L?: string; R?: string };
    onSide: (side: "L" | "R", patch: {
      layer?: CompareLayer; date?: string; lead?: IssueLead;
      pol?: "HH" | "HV"; track?: string; horizon?: ProbHorizon;
    }) => void;
    onPreset: (id: ComparePresetId) => void;
  };
}) {
  const seg = (on: boolean) =>
    `rounded-full px-2.5 py-0.5 text-xs font-semibold ${on ? "bg-cc-accent text-white" : "text-cc-ink"}`;
  const sel = "h-7 rounded-md border border-cc-line-strong bg-cc-surface px-1 text-xs font-semibold";
  return (
    <>
      <CompareMaps pair={pair} onClose={onClose} controlsRef={controlsRef} />
      {sidesCtl && (
        <div className="absolute z-30 top-20 flex -translate-x-1/2 flex-col items-center gap-1"
          // Centred on the visible map, never under the floating cards.
          style={{ left: "calc(var(--cc-il, 0px) + (100% - var(--cc-il, 0px) - var(--cc-ir, 0px)) / 2)",
            maxWidth: "calc(100% - var(--cc-il, 0px) - var(--cc-ir, 0px) - 24px)" }}>
          <div className="flex flex-col gap-1 rounded-xl border border-cc-line-strong bg-white/95 px-2 py-1.5 text-xs shadow" data-compare-sides>
            {sidesCtl.presets.length > 0 && (
              <div role="group" aria-label="Compare presets" data-compare-presets className="flex flex-wrap items-center gap-1">
                {sidesCtl.presets.map((p) => (
                  <button key={p.id} type="button" onClick={() => sidesCtl.onPreset(p.id)}
                    className="rounded-full border border-cc-line-strong px-2.5 py-0.5 text-xs font-semibold text-cc-ink">{p.label}</button>
                ))}
              </div>
            )}
            {(["L", "R"] as const).map((side) => {
              const s = sidesCtl.sides[side];
              const name = side === "L" ? "Left" : "Right";
              const info = sidesCtl.layers.find((l) => l.id === s.layer);
              const days = sidesCtl.days[side];
              const dayOpts = days.some((d) => d.date === s.date)
                ? days
                : [...days, { date: s.date, label: fmtDayShort(s.date) }];
              return (
                <div key={side} role="group" aria-label={`${name} side`} className="flex flex-wrap items-center gap-1" data-side={side}>
                  <span className="w-9 font-semibold text-cc-muted">{name}</span>
                  <select aria-label={`${name} layer`} value={s.layer}
                    onChange={(e) => sidesCtl.onSide(side, { layer: e.target.value as CompareLayer })}
                    className={sel} data-layer-select>
                    {!info && <option value={s.layer}>{COMPARE_LAYER_LABEL[s.layer]}</option>}
                    {sidesCtl.layers.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                  </select>
                  {s.layer === "nisar" && info?.tracks?.length ? (
                    <select aria-label={`${name} track`} value={s.track ?? info.tracks[0].id}
                      onChange={(e) => sidesCtl.onSide(side, { track: e.target.value })}
                      className={sel} data-track-select>
                      {info.tracks.map((t) => <option key={t.id} value={t.id}>Track {t.id || "—"}</option>)}
                    </select>
                  ) : null}
                  <select aria-label={`${name} day`} value={s.date}
                    onChange={(e) => sidesCtl.onSide(side, { date: e.target.value })}
                    className={sel} data-day-select>
                    {dayOpts.map((d) => <option key={d.date} value={d.date}>{d.label}</option>)}
                  </select>
                  {isRiskLayer(s.layer) && info?.canForecast && (
                    <>
                      <button type="button" aria-pressed={(s.lead ?? 0) === 0} onClick={() => sidesCtl.onSide(side, { lead: 0 })} className={seg((s.lead ?? 0) === 0)}>Observed</button>
                      <button type="button" aria-pressed={(s.lead ?? 0) > 0} onClick={() => sidesCtl.onSide(side, { lead: s.lead || 3 })} className={seg((s.lead ?? 0) > 0)}>Forecast</button>
                      {(s.lead ?? 0) > 0 && ISSUE_LEADS.map((k) => (
                        <button key={k} type="button" aria-pressed={s.lead === k} onClick={() => sidesCtl.onSide(side, { lead: k })} className={seg(s.lead === k)}>{k} d before</button>
                      ))}
                    </>
                  )}
                  {s.layer === "nisar" && (info?.pols?.length ?? 0) > 1 && (
                    <>
                      {(info!.pols!).map((p) => (
                        <button key={p} type="button" aria-pressed={(s.pol ?? "HH") === p} onClick={() => sidesCtl.onSide(side, { pol: p })} className={seg((s.pol ?? "HH") === p)}>{p}</button>
                      ))}
                    </>
                  )}
                  {s.layer === "ml" && (info?.horizons?.length ?? 0) > 0 && (
                    <>
                      {info!.horizons!.map((h) => (
                        <button key={h} type="button" aria-pressed={(s.horizon ?? info!.horizons![0]) === h}
                          onClick={() => sidesCtl.onSide(side, { horizon: h })} className={seg((s.horizon ?? info!.horizons![0]) === h)}>{horizonLabel(h)}</button>
                      ))}
                    </>
                  )}
                  {sidesCtl.missing?.[side] && <span className="italic text-cc-muted">{sidesCtl.missing[side]}</span>}
                </div>
              );
            })}
            {sidesCtl.warning && <p className="m-0 font-semibold text-cc-warn" role="status" data-compare-warning>{sidesCtl.warning}</p>}
          </div>
        </div>
      )}
    </>
  );
}

function CompareMaps({
  pair,
  onClose,
  controlsRef,
}: {
  pair: ComparePair;
  onClose: () => void;
  controlsRef: MutableRefObject<CompareControls | null>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
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
      } else {
        map.addSource("swipe-image", {
          type: "image",
          url: side.url,
          coordinates: boundsToImageCoordinates(side.bounds ?? pair.bounds),
        });
      }
      map.addLayer({ id: "swipe-image", type: "raster", source: "swipe-image" });
      // Flooded area (radar) in its own colour above both radar images.
      if (pair.flood) {
        map.addSource("swipe-flood", { type: "geojson", data: pair.flood.url });
        map.addLayer({ id: "swipe-flood-fill", type: "fill", source: "swipe-flood",
          paint: { "fill-color": FLOODED_FILL, "fill-opacity": 0.35 } });
        map.addLayer({ id: "swipe-flood-line", type: "line", source: "swipe-flood",
          paint: { "line-color": FLOODED_OUTLINE, "line-width": 1.2 } });
      }
      // Only the first map to load fits; its moves sync the other one.
      if (!fitted) {
        fitted = true;
        fit(map, 0);
      }
    };
    beforeMap.on("load", () => onLoad(beforeMap, pair.before));
    afterMap.on("load", () => onLoad(afterMap, pair.after));

    controlsRef.current = {
      zoomIn: () => beforeMap.zoomIn(),
      zoomOut: () => beforeMap.zoomOut(),
      recenter: () => fit(beforeMap, 400),
    };
    return () => {
      controlsRef.current = null;
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
      <div className={tag} style={{ left: "calc(var(--cc-il, 0px) + 56px)", top: 44 }}>{pair.before.label}</div>
      <div className={tag} style={{ right: "calc(var(--cc-ir, 0px) + 68px)" }}>{pair.after.label}</div>
      {pair.flood && (
        <div data-swipe-legend className="pointer-events-none absolute bottom-12 z-10 flex items-center gap-1.5 rounded-lg bg-white/95 px-2.5 py-1 text-xs font-semibold text-cc-ink shadow"
          style={{ left: "calc(var(--cc-il, 0px) + 56px)" }}>
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
      <button type="button" onClick={onClose}
        style={{ bottom: "calc(var(--cc-ib, 0px) + 12px)" }}
        className="absolute left-1/2 z-30 -translate-x-1/2 rounded-lg bg-white px-3.5 py-2 text-[13px] font-semibold text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.35)]">
        Close compare
      </button>
    </div>
  );
}
