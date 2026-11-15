"use client";

import { horizonLabel, probHorizonsAvailable } from "@/lib/layers";

import { useEffect, useMemo, useRef, useState } from "react";
import { Marker, type Map as MapInstance } from "maplibre-gl";
import Map from "../Map";
import { useMapLayers } from "../MapLayers";
import AreaCallout from "./AreaCallout";
import AreaPins from "./AreaPins";
import CompareOverlay, { type CompareControls } from "./CompareOverlay";
import { Legend } from "./LayersColumn";
import LocatorInset from "./LocatorInset";
import MapTools from "./MapTools";
import { pinAreas, type PriorityArea } from "./model/areas";
import { BASEMAP_LABEL, BASEMAP_ORDER } from "./model/basemap";
import { STATUS_WORD, type DataStatus } from "./model/dataStatus";
import { hasGfsFrames } from "./model/forecastCompare";
import { isRiskLayer } from "./model/sideCompare";
import { fmtDate } from "./model/format";
import { layerStateFor, overlaysFor } from "./model/layerRegistry";
import { resolveChangePair } from "./model/nisarChange";
import { useNisarChangeImage } from "./useNisarChange";
import { activeView } from "./model/views";
import { passLabel, passValue, radarFrame, radarPasses, type Pol } from "./model/radarFrame";
import { setRadarLook, useRadarLook, useTintedUrl, useWaterUrl } from "./useWaterRed";
import { WATER_RED_LEGEND } from "./model/waterRamp";
import { floodNisarFrame, floodNisarNote, nisarWaterLayer } from "./model/floodNisar";
import { useFloodNisar } from "./useFloodNisar";
import type { ComparePair } from "./model/radar";
import { assetUrl } from "@/lib/layers";
import { caseDirOf } from "./model/lazy";
import { preloadFrames } from "./model/preload";
import { effectiveLead, floodCaptionNote, forecastForDay, forecastLeadsForDay, frameFor, mapCaption, shiftDay, type IssueLead, type Step } from "./model/steps";
import type { CcBundle } from "./model/types";
import { hourForecastCaption, hourForecastFrame, hourLabel } from "./model/hours";
import { HAZARD_LABEL } from "./model/types";
import { shortCaption } from "./model/caption";
import type { ManifestLayer } from "@/lib/types";
import type { Action, ScreenState } from "./state";
import type { Insets } from "./useInsets";

// L61: the NISAR water overlay in the flooded-area red (FLOODED_FILL).
const WATER_RGB = [255, 31, 31] as const;

// Offsets from the stage edges: the floating cards plus a margin.
const L = (px: number) => `calc(var(--cc-il, 0px) + ${px}px)`;
const R = (px: number) => `calc(var(--cc-ir, 0px) + ${px}px)`;
const B = (px: number) => `calc(var(--cc-ib, 0px) + ${px}px)`;

// A place to frame on request ("View on the map"); `key` makes a repeat
// click on the same alert move the map again.
export interface MapFocus {
  bbox?: [number, number, number, number];
  center?: [number, number];
  key: number;
}

// The searched city (City search): a pin with its name.
export interface CityPin { center: [number, number]; name: string }

const CITY_OUTLINE_SRC = "tp-city-outline";
const CITY_OUTLINE_LYR = "tp-city-outline-line";

export default function MapStage({
  bundle,
  steps,
  state,
  areas,
  status,
  comparePair,
  dispatch,
  onToggleLayers,
  insets,
  stageRef,
  focus,
  featuredCase,
  cityPin,
  cityOutline,
  hourFrame,
  hour,
  sidesCtl,
}: {
  bundle: CcBundle;
  steps: Step[];
  state: ScreenState;
  areas: PriorityArea[];
  status: DataStatus;
  comparePair: ComparePair | undefined;
  dispatch: (a: Action) => void;
  onToggleLayers: () => void;
  insets: Insets; // space covered by the floating cards
  stageRef: React.RefObject<HTMLDivElement | null>;
  featuredCase?: { id: string; short: string };
  focus?: MapFocus;
  cityPin?: CityPin;
  // L52 replay city pick: the municipality polygon, drawn as an accent
  // outline over the frame; removed when the card closes.
  cityOutline?: GeoJSON.Feature | null;
  // Lucas: a 6-hourly TPI frame picked with the timeline hour chips.
  hourFrame?: ManifestLayer;
  hour?: string | null;
  sidesCtl?: React.ComponentProps<typeof CompareOverlay>["sidesCtl"];
}) {
  const [map, setMap] = useState<MapInstance | null>(null);
  const [bearing, setBearing] = useState(0);
  const [capOpen, setCapOpen] = useState(false);
  const compare = useRef<CompareControls | null>(null);
  const step = steps[state.dayIndex];

  const layerState = useMemo(
    () => layerStateFor(state.layers, state.hazard),
    [state.layers, state.hazard]
  );
  // Replay "forecast issued this day": the GFS frame issued on the step
  // day for D+k replaces the day's own frame.
  const issued = useMemo(
    () => hourForecastFrame(bundle, state.hazard, step?.date, hour ?? null, state.issueLead)?.layer ??
      forecastForDay(bundle, step?.date, state.hazard, effectiveLead(bundle, step?.date, state.hazard, state.issueLead))?.layer,
    [bundle, step?.date, state.hazard, state.issueLead, hour]
  );
  // Lucas (BUG): the Radar — NISAR view draws the radar frame itself, not
  // only the flood polygons — the pass on/before the day (main track, HH)
  // unless the user picked another pass or HV. A new day drops the pass
  // pick (the frame follows the timeline) but keeps the polarization.
  const radarPassList = useMemo(() => radarPasses(bundle), [bundle]);
  const [rPick, setRPick] = useState<{ date?: string; track?: string; pol: Pol }>({ pol: "HH" });
  const [rPickDay, setRPickDay] = useState(step?.date);
  if (rPickDay !== step?.date) {
    setRPickDay(step?.date);
    setRPick((p) => ({ pol: p.pol }));
  }
  const radarOn = !comparePair && activeView(state.layers) === "radar" && radarPassList.length > 0;
  const rFrame = useMemo(
    () => (radarOn ? radarFrame(radarPassList, step?.date, rPick) : undefined),
    [radarOn, radarPassList, step?.date, rPick]
  );
  // Lucas: 'Water in red' (default) recolours the frame — darker = redder.
  const radarLook = useRadarLook();
  const radarUrl = useWaterUrl(rFrame?.url, rFrame?.stretch, radarLook === "water");
  // L61: Flood + TPI on an observed day → the flood + NISAR frame when
  // lead-analysis published one for the day, with the NISAR water on top.
  const floodNisarOn = useFloodNisar();
  const fnFrame = useMemo(
    () => (floodNisarOn && state.hazard === "flood" && state.layers.includes("tpi") && !issued && !hourFrame
      ? floodNisarFrame(bundle, step?.date) : undefined),
    [floodNisarOn, state.hazard, state.layers, issued, hourFrame, bundle, step?.date]
  );
  const fnWater = useMemo(() => {
    const l = fnFrame ? nisarWaterLayer(bundle, step?.date, fnFrame) : undefined;
    const url = l ? assetUrl(caseDirOf(bundle), l.png) : undefined;
    return l && url ? { url, bounds: l.bounds } : undefined;
  }, [fnFrame, bundle, step?.date]);
  // Lucas: NISAR water reads RED like the candidate polygons (#ff1f1f).
  const fnWaterRed = useTintedUrl(fnWater?.url, WATER_RGB);
  const fnWaterShown = useMemo(
    () => (fnWater && fnWaterRed ? { url: fnWaterRed, bounds: fnWater.bounds } : undefined),
    [fnWater, fnWaterRed]
  );
  // L52 forecast-vs-observed swipe: leads with a full pair for the day.
  const gfsAny = useMemo(() => hasGfsFrames(bundle), [bundle]);
  // NISAR change layer (Lucas, t5w): the Δ-dB composite of the picked pair —
  // a reasoned spec still resolves, the overlay only paints on a pair.
  const changeSpec = useMemo(
    () => resolveChangePair(bundle, state.nisarChange ?? {}, step?.date),
    [bundle, state.nisarChange, step?.date]
  );
  const changeImage = useNisarChangeImage(changeSpec, state.layers.includes("nisarChange"), state.changeCyan);
  const options = useMemo(
    () => ({
      zoneMarkers: false,
      frameDate: step?.date,
      overlays: overlaysFor(state.layers),
      probHorizon: state.probHorizon,
      frameLayer: issued ?? hourFrame ?? fnFrame,
      // L52: the satellite underlay is dated by the case (its reference day);
      // live gets the latest imagery GIBS published.
      basemap: state.basemap,
      imageryDate:
        bundle.mode === "live"
          ? undefined
          : (bundle.series?.reference_date ?? bundle.reference_date)?.slice(0, 10),
      opacity: state.overlayOpacity,
      changeImage,
      changeOpacity: state.changeOpacity,
      radarFrame: rFrame && radarUrl ? { url: radarUrl, bounds: rFrame.bounds } : undefined,
      nisarWater: fnWaterShown,
    }),
    [step?.date, state.layers, state.probHorizon, issued, hourFrame, state.basemap, state.overlayOpacity, bundle, changeImage, state.changeOpacity, rFrame, radarUrl, fnFrame, fnWaterShown]
  );
  useMapLayers(map, bundle, state.dayIndex, layerState, false, undefined, options);

  // Warm the browser cache with the TPI frames of the chosen hazard, so a
  // timeline step swaps an image that is already downloaded.
  useEffect(() => {
    const urls = new Set<string>();
    for (const s of steps) {
      const png = frameFor(bundle, s.date, state.hazard)?.layer.png;
      const url = assetUrl(caseDirOf(bundle), png);
      if (url) urls.add(url);
    }
    return preloadFrames(urls);
  }, [bundle, steps, state.hazard]);

  const pins = useMemo(() => pinAreas(areas), [areas]);
  const selected = areas.find((a) => a.rank === state.selectedArea);

  // Center on the selected area.
  useEffect(() => {
    if (map && selected?.at) {
      map.flyTo({ center: selected.at, zoom: Math.max(map.getZoom(), 11.5), duration: 1200 });
    }
    // Only a new selection moves the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, state.selectedArea, bundle.id]);

  // Frame a requested place. Opening the map view changes the card insets,
  // and the padding ease that follows would cancel a camera move started now:
  // wait for it (300 ms) first.
  // When the case was last fitted (see the padding effect below).
  const fitted = useRef<{ key: string; at: number }>({ key: "", at: 0 });
  // A case switch (city search from Replay) refits the case bounds for
  // 1.5 s: start after that.
  useEffect(() => {
    if (!map || !focus) return;
    // This effect runs before the padding effect: a case not fitted yet is
    // about to be.
    const sinceFit = fitted.current.key !== bundle.id ? 0 : performance.now() - fitted.current.at;
    const t = setTimeout(() => {
      const b = focus.bbox;
      if (b) {
        map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, maxZoom: 12, duration: 1000 });
      } else if (focus.center) {
        map.flyTo({ center: focus.center, zoom: Math.max(map.getZoom(), 11), duration: 1000 });
      }
    }, Math.max(350, 1600 - sinceFit));
    return () => clearTimeout(t);
    // Only a new focus moves the map; the case id is read for timing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, focus]);

  useEffect(() => {
    if (!map || !cityPin) return;
    const el = document.createElement("div");
    el.className = "cc-city-pin";
    el.dataset.cityPin = cityPin.name;
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", `Searched city: ${cityPin.name}`);
    const dot = document.createElement("span");
    dot.className = "block h-4 w-4 rounded-full border-[3px] border-white bg-cc-accent shadow-[0_1px_4px_rgba(0,0,0,0.5)]";
    const name = document.createElement("span");
    name.className = "mt-0.5 block whitespace-nowrap rounded bg-white/95 px-1.5 text-xs font-semibold text-cc-ink shadow";
    name.textContent = cityPin.name;
    el.append(dot, name);
    el.style.display = "flex";
    el.style.flexDirection = "column";
    el.style.alignItems = "center";
    const m = new Marker({ element: el, anchor: "top", offset: [0, -8] }).setLngLat(cityPin.center).addTo(map);
    return () => { m.remove(); };
  }, [map, cityPin]);

  // The replay municipality outline: a single line layer in the accent
  // colour, added lazily and removed on close/unmount.
  useEffect(() => {
    if (!map || !cityOutline) return;
    try {
      map.addSource(CITY_OUTLINE_SRC, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [cityOutline] },
      });
      map.addLayer({
        id: CITY_OUTLINE_LYR,
        type: "line",
        source: CITY_OUTLINE_SRC,
        paint: { "line-color": "#17473f", "line-width": 2.5 },
      });
    } catch {
      // map mid-teardown or a style reload: the next pass retries.
    }
    return () => {
      try { if (map.getLayer(CITY_OUTLINE_LYR)) map.removeLayer(CITY_OUTLINE_LYR); } catch {}
      try { if (map.getSource(CITY_OUTLINE_SRC)) map.removeSource(CITY_OUTLINE_SRC); } catch {}
    };
  }, [map, cityOutline]);

  useEffect(() => {
    if (!map) return;
    const onRotate = () => setBearing(map.getBearing());
    map.on("rotate", onRotate);
    return () => {
      map.off("rotate", onRotate);
    };
  }, [map]);

  const fit = (duration = 500) =>
    map?.fitBounds(
      [[bundle.bbox[0], bundle.bbox[1]], [bundle.bbox[2], bundle.bbox[3]]],
      { padding: 60, duration }
    );

  // The camera treats the area under the cards as padding, so "fit" and
  // "center on the area" use the visible part of the map.
  //
  // Any camera call stops a running animation. Map.tsx fits the case bounds
  // with an animation when the map loads and when the case changes; a
  // padding change right after it would cancel that fit and leave the map
  // on its start position. So: on a new map or a new case, and while that
  // fit may still run, the padding is set first and the fit is done again
  // here. Later padding changes only ease the padding.
  useEffect(() => {
    if (!map) return;
    const padding = { left: insets.left, right: insets.right, bottom: insets.bottom, top: 0 };
    const key = bundle.id;
    const fresh = fitted.current.key !== key || performance.now() - fitted.current.at < 1500;
    if (fresh) {
      if (fitted.current.key !== key) fitted.current = { key, at: performance.now() };
      map.setPadding(padding);
      fit(0);
    } else {
      map.easeTo({ padding, duration: 300 });
    }
    // `fit` reads the bundle of this render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, bundle.id, insets.left, insets.right, insets.bottom]);
  const comparing = !!comparePair;
  // An hour chip in Forecast view: the 6-hourly NOAA run (lead in hours).
  const hourFc = hourForecastFrame(bundle, state.hazard, step?.date, hour ?? null, state.issueLead);
  const effLead = (hourFc?.k as IssueLead | undefined) ?? effectiveLead(bundle, step?.date, state.hazard, state.issueLead);
  const fLeads = forecastLeadsForDay(bundle, step?.date, state.hazard);
  const caption = hourFc && step && hour && state.layers.includes("tpi")
    ? { text: hourForecastCaption(step.date, hour, hourFc.issued, HAZARD_LABEL[state.hazard]), forecast: true }
    : mapCaption(bundle, steps, state.dayIndex, state.hazard, state.layers, effLead);
  // L52 merged view: name the NISAR pass while the outlines are on screen.
  const passNote = floodCaptionNote(bundle, step?.date, state.layers);
  const fnNote = floodNisarNote(fnFrame);
  const captionNote = passNote + fnNote;
  // Prod regression: the ML forecast horizon must be on the map, not only in
  // the Layers panel (closed on phones).
  const probHorizons = useMemo(() => probHorizonsAvailable(bundle.manifest?.layers), [bundle]);
  // Now + landslide: the chips stay on the map (picking one returns to the ML
  // view even when a forecast day put the TPI on).
  const showHorizons = probHorizons.length > 0 && (state.layers.includes("ptrigger") || (bundle.mode === "live" && state.hazard === "slide"));
  const mlOn = state.layers.includes("ptrigger");
  const chip =
    "rounded-lg bg-white/95 px-2.5 py-1 text-[13px] font-semibold text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)]";

  return (
    <div
      ref={stageRef}
      className="relative overflow-hidden bg-cc-map max-[899px]:h-[60vh] max-[899px]:min-h-[400px] min-[900px]:absolute min-[900px]:inset-0"
      style={{
        "--cc-il": `${insets.left}px`,
        "--cc-ir": `${insets.right}px`,
        "--cc-ib": `${insets.bottom}px`,
      } as React.CSSProperties}
    >
      <Map bounds={state.fitTo && state.activeId === bundle.id ? state.fitTo : bundle.bbox} onReady={setMap} />
      <AreaPins map={map} areas={pins} selected={state.selectedArea}
        onSelect={(rank) => dispatch({ type: "selectArea", rank })} />
      <AreaCallout
        map={map}
        bundle={bundle}
        area={selected}
        stepDate={step?.date}
        hazard={state.hazard}
        expanded={state.calloutExpanded}
        onToggle={() => dispatch({ type: "toggleCallout" })}
        onClose={() => dispatch({ type: "clearArea" })}
      />

      {/* North arrow: follows the map bearing. */}
      <div aria-hidden="true" className="pointer-events-none absolute top-3 z-10 flex flex-col items-center text-[13px] font-bold text-white [text-shadow:0_1px_3px_#000]" style={{ left: L(14) }}>
        N
        <svg width="20" height="24" viewBox="0 0 20 24" fill="#ffffff" stroke="#16242b" strokeWidth="1" strokeLinejoin="round" style={{ transform: `rotate(${-bearing}deg)` }}>
          <path d="M10 1l7 21-7-5-7 5z" />
        </svg>
      </div>

      <span data-status={status.kind} className={`absolute top-3 z-10 ${chip} !text-xs tracking-[0.02em]`} style={{ left: L(56) }}>
        {STATUS_WORD[status.kind]}
      </span>

      {!comparing && featuredCase && bundle.mode === "live" && (
        <button type="button" data-featured-case onClick={() => dispatch({ type: "goTo", id: featuredCase.id, hazard: "flood" })}
          className={`absolute top-3 z-10 ${chip} !text-xs font-semibold text-cc-accent`} style={{ left: L(140) }}>
          <span className="max-[639px]:hidden">Open the </span>{featuredCase.short}<span className="max-[639px]:hidden"> case</span> →
        </button>
      )}

      {/* Nav (Lucas): one click from any past event back to the live map. */}
      {!comparing && bundle.mode === "replay" && (
        <button type="button" data-back-to-live onClick={() => dispatch({ type: "setMode", mode: "now" })}
          className={`absolute top-3 z-10 ${chip} !text-xs font-semibold text-cc-accent`} style={{ left: L(152) }}>
          ← Back to live<span className="max-[639px]:hidden"> (Now)</span>
        </button>
      )}

      {!comparing && showHorizons && (
        <div role="radiogroup" aria-label="Forecast horizon" data-horizons
          className={`absolute z-10 flex flex-wrap items-center gap-1 ${chip} !py-0.5 max-[639px]:!bottom-auto max-[639px]:top-12 max-[639px]:!left-12`} style={{ left: L(14), bottom: B(70) }}>
          <span className="pr-1 text-xs font-semibold"><span className="max-[639px]:hidden">Landslide likelihood within </span>next:</span>
          {probHorizons.map((h) => (
            <button key={h} type="button" role="radio" aria-checked={mlOn && state.probHorizon === h}
              onClick={() => dispatch({ type: "setProbHorizon", horizon: h })}
              className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${mlOn && state.probHorizon === h ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`}>
              {horizonLabel(h)}
            </button>
          ))}
        </div>
      )}

      {rFrame && (
        <div role="group" aria-label="NISAR radar frame" data-radar-frame
          className={`absolute z-10 flex max-w-[calc(100%-28px)] flex-col items-start gap-1 ${chip} !py-1`} style={{ left: L(14), top: 52 }}>
          <span data-radar-chip className="text-xs font-semibold">{rFrame.chip}{rFrame.note ? ` (${rFrame.note})` : ""}</span>
          <div className="flex flex-wrap items-center gap-1">
            {(["HH", "HV"] as const).map((p) => {
              const has = !!rFrame.pass.frames[p];
              return (
                <button key={p} type="button" aria-pressed={rFrame.pol === p} disabled={!has} data-radar-pol={p}
                  title={has ? undefined : `No ${p} image for this pass`}
                  onClick={() => setRPick((x) => ({ ...x, date: rFrame.pass.date, track: rFrame.pass.track, pol: p }))}
                  className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${rFrame.pol === p ? "bg-cc-accent text-white" : "border border-cc-line-strong"} disabled:opacity-40`}>
                  {p}
                </button>
              );
            })}
            <label className="flex items-center gap-1 text-xs font-medium">
              <span className="sr-only">NISAR pass</span>
              <select data-radar-pass value={passValue(rFrame.pass)}
                onChange={(e) => {
                  const [date, track = ""] = e.target.value.split("|");
                  setRPick((x) => ({ ...x, date, track }));
                }}
                className="max-w-[190px] rounded-md border border-cc-line-strong bg-white px-1 py-0.5 text-xs">
                {radarPassList.map((p) => (
                  <option key={passValue(p)} value={passValue(p)}>{passLabel(p)}</option>
                ))}
              </select>
            </label>
          </div>
          <div role="group" aria-label="Radar colours" data-radar-look className="flex flex-wrap items-center gap-1">
            {([["water", "Water in red"], ["plain", "Plain radar"]] as const).map(([k, label]) => (
              <button key={k} type="button" aria-pressed={radarLook === k} data-radar-look-btn={k}
                onClick={() => setRadarLook(k)}
                className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${radarLook === k ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`}>{label}</button>
            ))}
          </div>
          {radarLook === "water" ? (
            <span className="flex items-center gap-1 text-[11px] font-medium text-cc-muted" data-water-legend>
              <span aria-hidden="true" className="inline-block h-2.5 w-6 rounded-sm" style={{ background: "linear-gradient(90deg,#9a9a9a,#ff1f1f)" }} />
              {WATER_RED_LEGEND}{rFrame.stretch ? "" : " (approximate: brightness scale not declared for this frame)"}
            </span>
          ) : (
            <span className="text-[11px] font-medium text-cc-muted">Dark = smooth surface (open water) · bright = buildings, trees</span>
          )}
        </div>
      )}

      {/* Lucas: Compare ⇆ in Now too — any two days of the run. */}
      {!comparing && bundle.mode === "live" && steps.length > 1 && (
        <button type="button" data-compare-now onClick={() => dispatch({ type: "openCompare", kind: "forecast" })}
          className={`absolute z-10 ${chip} !py-1 !text-xs`} style={{ left: L(14), bottom: B(showHorizons ? 104 : 70) }}>
          Compare ⇆
        </button>
      )}

      {/* Lucas: one map view at a time — Observed | Forecast | Compare ⇆.
          The user picks the target day on the timeline; Forecast shows the
          NOAA GFS run issued 1–3 days before it. Compare opens only from its
          own button. A lead without an archived run falls back to the
          nearest one, with the reason inline (never a dead chip). */}
      {!comparing && bundle.mode === "replay" && gfsAny && (
        <div className={`absolute z-10 flex max-w-[calc(100%-28px)] flex-col items-start gap-1 ${chip} !py-1`} style={{ left: L(14), bottom: B(70) }} data-fview>
          <div role="group" aria-label="Map view" className="flex flex-wrap items-center gap-1">
            {([["observed", "Observed"], ["forecast", "Forecast"], ["compare", "Compare ⇆"]] as const).map(([v, label]) => {
              const on = v === "observed" ? state.issueLead === 0 : v === "forecast" ? state.issueLead > 0 : false;
              return (
                <button key={v} type="button" aria-pressed={on} data-fview-btn={v}
                  onClick={() => {
                    if (v === "observed") dispatch({ type: "setIssueLead", lead: 0 });
                    else {
                      if (state.issueLead === 0) dispatch({ type: "setIssueLead", lead: 3 });
                      if (v === "compare") dispatch({ type: "openCompare", kind: "forecast" });
                    }
                  }}
                  className={`rounded-md px-2 py-0.5 text-xs font-semibold ${on ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`}>{label}</button>
              );
            })}
          </div>
          {state.issueLead > 0 && (
            <div role="group" aria-label="Forecast lead (days after the NOAA issue day)" className="flex flex-wrap items-center gap-1" data-issue-leads>
              {([1, 2, 3] as IssueLead[]).map((k) => (
                <button key={k} type="button" aria-pressed={effLead === k}
                  onClick={() => dispatch({ type: "setIssueLead", lead: k })}
                  className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${effLead === k ? "bg-cc-accent text-white" : fLeads.includes(k) ? "border border-cc-line-strong" : "border border-dashed border-cc-line-strong text-cc-muted"}`}>
                  +{k}{k === 3 ? " days" : ""}
                </button>
              ))}
            </div>
          )}
          {state.issueLead > 0 && step && effLead !== state.issueLead && (
            <p className="m-0 text-xs font-medium text-cc-muted" data-lead-note>
              {fLeads.length
                ? `No NOAA run issued ${fmtDate(shiftDay(step.date, -state.issueLead))} — showing the run issued ${fmtDate(shiftDay(step.date, -effLead))} (+${effLead} d)`
                : `No NOAA forecast archived for ${fmtDate(step.date)}`}
            </p>
          )}
        </div>
      )}

      {!comparing && (() => {
        // Lucas: the full sentence covered the map — a one-line chip, the
        // full sentence behind ⓘ (tap) and in the tooltip / screen readers.
        // lead-analysis: every 6-hourly frame carries a 'smooth by construction'
        // note — shown with the full caption.
        const sixH = hourFc?.layer ?? (!issued ? hourFrame : undefined);
        const hourNote = sixH && typeof (sixH as unknown as { note?: unknown }).note === "string"
          ? ` — ${(sixH as unknown as { note: string }).note}` : "";
        const full = `${caption.text}${hourFrame && !issued ? ` · 72 h rain until ${hourLabel(hourFrame)}` : ""}${captionNote}${hourNote}`;
        return (
          <div className="absolute z-10 flex max-w-[60%] flex-col items-start gap-1 max-[639px]:max-w-[calc(100%-120px)]" style={{ left: L(14), bottom: B(36) }}>
            {capOpen && (
              <p className={`m-0 ${chip} !font-medium`} data-caption-full>{full}</p>
            )}
            <button type="button" data-caption title={full} aria-expanded={capOpen} onClick={() => setCapOpen((o) => !o)}
              className={`${chip} max-w-full truncate !py-0.5 text-left ${caption.forecast ? "border-2 border-dashed border-cc-forecast-ink" : ""}`}>
              <span className="sr-only">{full} </span>
              <span aria-hidden="true">{shortCaption(caption.text, caption.forecast)}{hourFrame && !issued ? ` · ${hourLabel(hourFrame).split(", ").pop()}` : ""}{passNote.startsWith(" · next NISAR pass") ? passNote : ""}{fnNote} ⓘ</span>
            </button>
          </div>
        );
      })()}

      {!comparing && (
        <div className="absolute z-10 flex flex-col items-end gap-1.5" style={{ right: R(14), bottom: B(12) }}>
          {/* Basemap switcher (L52): a row above the locator inset, clear of
              the horizon chips (left) and the City card (top-centre). */}
          <div role="group" aria-label="Basemap" data-basemap
            className={`flex items-center gap-1 ${chip} !py-0.5`}>
            {BASEMAP_ORDER.map((b) => (
              <button key={b} type="button" aria-pressed={state.basemap === b}
                onClick={() => dispatch({ type: "setBasemap", basemap: b })}
                className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${state.basemap === b ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`}>
                {BASEMAP_LABEL[b]}
              </button>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <Legend bundle={bundle} layers={state.layers} hazard={state.hazard} stepDate={step?.date} probHorizon={state.probHorizon} compact changeCyan={state.changeCyan}
              className={`rounded-[10px] bg-white/95 px-2.5 py-2 text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)] max-[639px]:hidden ${state.layersOpen ? "min-[900px]:hidden" : ""}`} />
            {/* Phones: the legend is collapsed by default so it does not cover
                the map and the timeline chips; one tap opens it. */}
            <details data-legend-phone className="min-[640px]:hidden rounded-[10px] bg-white/95 text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)]">
              <summary className="cursor-pointer list-none px-2.5 py-1.5 text-xs font-semibold">Legend</summary>
              <Legend bundle={bundle} layers={state.layers} hazard={state.hazard} stepDate={step?.date} probHorizon={state.probHorizon} compact changeCyan={state.changeCyan} className="max-h-[50vh] overflow-y-auto px-2.5 pb-2" />
            </details>
            <LocatorInset bbox={bundle.bbox} className="max-[899px]:hidden" />
          </div>
        </div>
      )}

      {/* L52: the TPI class colours stay readable while the swipe paints a
          TPI frame on either side. */}
      {comparePair?.kind === "forecast" && (!sidesCtl || [sidesCtl.sides.L, sidesCtl.sides.R].some((s) => isRiskLayer(s.layer))) && (
        // Wide screens: the Layers column already shows this legend.
        <div className="absolute z-30 flex items-end gap-2 min-[900px]:hidden" style={{ right: R(14), bottom: B(12) }}>
          <Legend bundle={bundle} layers={["tpi"]} hazard={state.hazard} stepDate={step?.date} probHorizon={state.probHorizon} compact
            className="rounded-[10px] bg-white/95 px-2.5 py-2 text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)] max-[639px]:hidden" />
          <details data-legend-phone className="min-[640px]:hidden rounded-[10px] bg-white/95 text-cc-ink shadow-[0_2px_8px_rgba(0,0,0,0.3)]">
            <summary className="cursor-pointer list-none px-2.5 py-1.5 text-xs font-semibold">Legend</summary>
            <Legend bundle={bundle} layers={["tpi"]} hazard={state.hazard} stepDate={step?.date} probHorizon={state.probHorizon} compact className="max-h-[50vh] overflow-y-auto px-2.5 pb-2" />
          </details>
        </div>
      )}

      {comparePair && (
        <CompareOverlay key={`${bundle.id}-${comparePair.kind}-${comparePair.before.url}-${comparePair.after.url}`} pair={comparePair}
          sidesCtl={comparePair.kind === "forecast" ? sidesCtl : undefined}
          controlsRef={compare} onClose={() => dispatch({ type: "closeCompare" })} />
      )}

      <MapTools
        className="absolute top-3 z-30"
        style={{ right: R(14) }}
        layersShown={state.layersOpen}
        onToggleLayers={onToggleLayers}
        onRecenter={() => (comparing ? compare.current?.recenter() : fit(500))}
        onZoomIn={() => (comparing ? compare.current?.zoomIn() : map?.zoomIn())}
        onZoomOut={() => (comparing ? compare.current?.zoomOut() : map?.zoomOut())}
      />
    </div>
  );
}
