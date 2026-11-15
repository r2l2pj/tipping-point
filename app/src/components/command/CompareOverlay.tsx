"use client";

import { useEffect, useRef, useState, type MutableRefObject } from "react";
import {
  Map as MapLibreMap,
  setWorkerUrl,
  type ImageSource,
  type Map as MapInstance,
} from "maplibre-gl";
import { setRadarLook, useRadarLook, useWaterUrl } from "./useWaterRed";
import { WATER_RED_LEGEND } from "./model/waterRamp";
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
  const seg = (on: boolean) =>
    `rounded-full px-2.5 py-0.5 text-xs font-semibold ${on ? "bg-cc-accent text-white" : "text-cc-ink"}`;
  const sel = "h-7 max-w-[12rem] rounded-md border border-cc-line-strong bg-cc-surface px-1 text-xs font-semibold";
  // Lucas: NISAR before/after can show the classic change composite (one
  // map, red = new water) — only for a same-track, same-pol NISAR pair.
  const [mode, setMode] = useState<"side" | "change">("side");
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
        ? <ChangeMap pair={pair} onClose={onClose} />
        : <CompareMaps pair={shown} onClose={onClose} controlsRef={controlsRef} />}
      {sidesCtl && (
        <div className="absolute z-30 top-20 flex -translate-x-1/2 flex-col items-center gap-1"
          // Centred on the visible map, never under the floating cards.
          style={{ left: "calc(var(--cc-il, 0px) + (100% - var(--cc-il, 0px) - var(--cc-ir, 0px)) / 2)",
            // Clear of the map tool column (zoom / recenter) on the right.
            maxWidth: "calc(100% - var(--cc-il, 0px) - var(--cc-ir, 0px) - 128px)" }}>
          <div className="flex flex-col gap-1 rounded-xl border border-cc-line-strong bg-white/95 px-2 py-1.5 text-xs shadow" data-compare-sides>
            {!sidesCtl.nisarOnly && sidesCtl.presets.length > 0 && (
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
              // Forecast side: the picker is the ISSUE day X (● = a NOAA
              // run was issued that day); the frame shown is X + lead.
              const lead = isRiskLayer(s.layer) ? (s.lead ?? 0) : 0;
              const issue = lead > 0 ? issueDayOf(s) : undefined;
              const dayOpts = issue
                ? (days.some((d) => d.issue === issue) ? days : [...days, { date: s.date, issue, label: fmtDayShort(issue) }])
                : days.some((d) => d.date === s.date)
                  ? days
                  : [...days, { date: s.date, label: fmtDayShort(s.date) }];
              return (
                <div key={side} role="group" aria-label={`${name} side`} className="flex flex-wrap items-center gap-1" data-side={side}>
                  <span className="w-9 font-semibold text-cc-muted">{name}</span>
                  {!sidesCtl.nisarOnly && <select aria-label={`${name} layer`} value={s.layer}
                    onChange={(e) => sidesCtl.onSide(side, { layer: e.target.value as CompareLayer })}
                    className={sel} data-layer-select>
                    {!info && <option value={s.layer}>{COMPARE_LAYER_LABEL[s.layer]}</option>}
                    {sidesCtl.layers.map((l) => (
                      <option key={l.id} value={l.id} disabled={!!l.disabled} title={l.disabled}>
                        {l.disabled ? `${l.label} — ${l.disabled}` : l.label}
                      </option>
                    ))}
                  </select>}
                  {/* NISAR: one list of every pass (date · track · mode), any order. */}
                  {issue && <span className="text-cc-muted">NOAA issued</span>}
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
                  {isRiskLayer(s.layer) && info?.canForecast && (
                    <span className="inline-flex flex-wrap items-center gap-1" data-risk-mode>
                      <button type="button" aria-pressed={(s.lead ?? 0) === 0} onClick={() => sidesCtl.onSide(side, { lead: 0 })} className={seg((s.lead ?? 0) === 0)}>Observed</button>
                      <button type="button" aria-pressed={(s.lead ?? 0) > 0} onClick={() => sidesCtl.onSide(side, { lead: s.lead || 3 })} className={seg((s.lead ?? 0) > 0)}>Forecast</button>
                      {issue && ISSUE_LEADS.map((k) => (
                        <button key={k} type="button" aria-pressed={s.lead === k} data-lead={k}
                          onClick={() => sidesCtl.onSide(side, { lead: k, date: shiftDay(issue, k) })}
                          className={seg(s.lead === k)}>+{k}{k === 3 ? " days" : ""}</button>
                      ))}
                    </span>
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
            {sidesCtl.note && <p className="m-0 text-cc-muted" data-compare-note>{sidesCtl.note}</p>}
            {sidesCtl.sides.link && <p className="m-0 text-cc-muted" data-compare-link>Both sides move together: forecast for a day vs what happened that day</p>}
            {anyRadar && !changeOn && (
              <div role="group" aria-label="Radar colours" className="flex flex-wrap items-center gap-1" data-radar-look>
                {([["water", "Water in red"], ["plain", "Plain radar"]] as const).map(([k, label]) => (
                  <button key={k} type="button" aria-pressed={look === k} data-radar-look-btn={k}
                    onClick={() => setRadarLook(k)} className={seg(look === k)}>{label}</button>
                ))}
                {look === "water" && (
                  <span className="flex items-center gap-1 text-cc-muted" data-water-legend>
                    <span aria-hidden="true" className="inline-block h-2.5 w-6 rounded-sm" style={{ background: "linear-gradient(90deg,#9a9a9a,#ff1f1f)" }} />
                    {WATER_RED_LEGEND}{(isRadar(pair.before) && !pair.before.stretch) || (isRadar(pair.after) && !pair.after.stretch) ? " (approximate)" : ""}
                  </span>
                )}
              </div>
            )}
            {bothNisarSides && (
              <div role="group" aria-label="NISAR view" className="flex flex-wrap items-center gap-1" data-change-mode>
                <button type="button" aria-pressed={!changeOn} onClick={() => setMode("side")} className={seg(!changeOn)}>Side by side</button>
                <button type="button" aria-pressed={changeOn} disabled={!!changeBlocked} title={changeBlocked}
                  onClick={() => setMode("change")} className={`${seg(changeOn)} disabled:opacity-45`}>Change (red = new water)</button>
                {changeBlocked && <span className="italic text-cc-muted" data-change-blocked>{changeBlocked}</span>}
              </div>
            )}
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
  // The two maps and the latest pair: a recoloured NISAR frame ('Water in
  // red') swaps the image in place — the camera and the divider stay.
  const maps = useRef<{ b?: MapInstance; a?: MapInstance }>({});
  const latest = useRef(pair);
  useEffect(() => {
    latest.current = pair;
    for (const [m, side] of [[maps.current.b, pair.before], [maps.current.a, pair.after]] as const) {
      const src = m?.getSource("swipe-image") as ImageSource | undefined;
      if (src && src.type === "image" && side.url && !side.tiles) {
        try { src.updateImage({ url: side.url, coordinates: boundsToImageCoordinates(side.bounds ?? pair.bounds) }); } catch { /* keep the old image */ }
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
      } else if (side.url) {
        map.addSource("swipe-image", {
          type: "image",
          url: side.url,
          coordinates: boundsToImageCoordinates(side.bounds ?? pair.bounds),
        });
      }
      if (side.tiles || side.url) map.addLayer({ id: "swipe-image", type: "raster", source: "swipe-image" });
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

// Lucas: ONE map with the before/after change composite (R = before HH,
// G = B = after HH), computed in the browser from the two frame PNGs; the
// NISAR flood candidates toggle on top.
function ChangeMap({ pair, onClose }: { pair: ComparePair; onClose: () => void }) {
  const el = useRef<HTMLDivElement>(null);
  const [img, setImg] = useState<{ url: string; bounds: ChangeBBox; stats: string } | { error: string }>();
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
    const bb = pair.before.bounds ?? pair.bounds, ab = pair.after.bounds ?? pair.bounds;
    Promise.all([load(pair.before.url), load(pair.after.url)]).then(([b, a]) => {
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
      if (alive) setImg({ url: c.toDataURL("image/png"), bounds: ab as ChangeBBox, stats: `${after.width}x${after.height} opaque ${opaque}` });
    }).catch(() => { if (alive) setImg({ error: "Could not read the two radar images for the change view" }); });
    return () => { alive = false; };
  }, [pair.before.url, pair.after.url, pair.before.bounds, pair.after.bounds, pair.bounds]);

  useEffect(() => {
    if (!el.current || !img || "error" in img) return;
    const map = new MapLibreMap({ container: el.current, style: BASEMAP, minZoom: 5, maxZoom: 13, attributionControl: { compact: true } });
    mapRef.current = map;
    map.on("load", () => {
      map.addSource("change-image", { type: "image", url: img.url, coordinates: boundsToImageCoordinates(img.bounds) });
      map.addLayer({ id: "change-image", type: "raster", source: "change-image", paint: { "raster-opacity": 0.95 } });
      if (pair.flood) {
        map.addSource("change-flood", { type: "geojson", data: pair.flood.url });
        map.addLayer({ id: "change-flood-fill", type: "fill", source: "change-flood", paint: { "fill-color": FLOODED_FILL, "fill-opacity": FLOODED_OPACITY } });
        map.addLayer({ id: "change-flood-halo", type: "line", source: "change-flood", paint: { "line-color": FLOODED_HALO, "line-width": 3.5, "line-opacity": 0.55 } });
        map.addLayer({ id: "change-flood-line", type: "line", source: "change-flood", paint: { "line-color": FLOODED_OUTLINE, "line-width": 1.5 } });
      }
      map.fitBounds([[img.bounds[0], img.bounds[1]], [img.bounds[2], img.bounds[3]]], { padding: 60, duration: 0 });
    });
    return () => { mapRef.current = null; map.remove(); };
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
        <span className="sr-only">{CHANGE_LEGEND}</span>
        {pair.flood && (
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={showFlood} onChange={(e) => setShowFlood(e.target.checked)} />
            {pair.flood.label} on top
          </label>
        )}
      </div>
      <button type="button" onClick={onClose}
        style={{ bottom: "calc(var(--cc-ib, 0px) + 12px)" }}
        className="absolute left-1/2 z-30 -translate-x-1/2 rounded-lg bg-white px-3.5 py-2 text-[13px] font-semibold text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.35)]">
        Close compare
      </button>
    </div>
  );
}
