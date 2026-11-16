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
import LegendChip from "./LegendChip";
import LocatorInset from "./LocatorInset";
import MapTools from "./MapTools";
import { pinAreas, type PriorityArea } from "./model/areas";
import { BASEMAP_LABEL, BASEMAP_ORDER } from "./model/basemap";
import { hasGfsFrames } from "./model/forecastCompare";
import { isRiskLayer } from "./model/sideCompare";
import { fmtDate } from "./model/format";
import { layerStateFor, overlaysFor } from "./model/layerRegistry";
import { resolveChangePair } from "./model/nisarChange";
import { useNisarChangeImage } from "./useNisarChange";
import { activeView } from "./model/views";
import { passLabel, passValue, radarFrame, radarPasses, swathEdgeNote, type Pol } from "./model/radarFrame";
import { riverWindow, type LonLatBox } from "./model/riverWindow";
import { setRadarLook, useRadarLook, useTintedUrl, useWaterUrl } from "./useWaterRed";
import { WATER_RED_LEGEND } from "./model/waterRamp";
import { floodNisarFrame, floodNisarNote, nisarWaterLayer } from "./model/floodNisar";
import { useFloodNisar } from "./useFloodNisar";
import { useRadarPassRequest } from "./useRadarPick";
import { drawFootprint, useFootprint } from "./useFootprint";
import { FOOTPRINT_LEGEND } from "./model/footprint";
import { MOSAIC_NOTE, mosaicChip, mosaicFor, nisarMosaics } from "./model/nisarMosaic";
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
  comparePair,
  dispatch,
  onToggleLayers,
  insets,
  stageRef,
  focus,
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
  comparePair: ComparePair | undefined;
  dispatch: (a: Action) => void;
  onToggleLayers: () => void;
  insets: Insets; // space covered by the floating cards
  stageRef: React.RefObject<HTMLDivElement | null>;
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
  const [bmOpen, setBmOpen] = useState(false); // basemap choices
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
  const [rPick, setRPick] = useState<{ date?: string; track?: string; pol: Pol; forDay?: string }>({ pol: "HH" });
  const [rPickDay, setRPickDay] = useState(step?.date);
  if (rPickDay !== step?.date) {
    setRPickDay(step?.date);
    // A pick made for the new day (river's dance bar) survives the seek.
    setRPick((p) => ((p.date && p.date === step?.date) || (p.forDay && p.forDay === step?.date?.slice(0, 10)) ? p : { pol: p.pol }));
  }
  // River's dance: a bar asks for one pass (date + track) — once per request.
  const passReq = useRadarPassRequest();
  const [seenReq, setSeenReq] = useState(0);
  if (passReq && passReq.n !== seenReq) {
    setSeenReq(passReq.n);
    setRPick((p) => ({ date: passReq.date, track: passReq.track ?? "", pol: p.pol, ...(passReq.forDay ? { forDay: passReq.forDay } : {}) }));
  }
  const radarOn = !comparePair && (state.layers.includes("nisar") || activeView(state.layers) === "radar") && radarPassList.length > 0;
  const rFrame = useMemo(
    () => (radarOn ? radarFrame(radarPassList, step?.date, rPick) : undefined),
    [radarOn, radarPassList, step?.date, rPick]
  );
  // Lucas: Jhonata's 30 × 30 km river window (Uruguaiana): every NISAR pass
  // fills it. The Radar — NISAR view fits it when it opens and on every
  // river's-dance click; the user stays free to zoom out.
  const riverWin = useMemo(() => riverWindow(bundle), [bundle]);
  const [viewBox, setViewBox] = useState<LonLatBox>();
  useEffect(() => {
    if (!map) return;
    const read = () => {
      const b = map.getBounds();
      setViewBox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    };
    map.on("moveend", read);
    return () => { map.off("moveend", read); };
  }, [map]);
  useEffect(() => {
    if (!map || !radarOn || !riverWin) return;
    const fit = () => map.fitBounds([[riverWin[0], riverWin[1]], [riverWin[2], riverWin[3]]], {
      padding: { top: 70, bottom: insets.bottom + 30, left: insets.left + 30, right: insets.right + 30 }, duration: 600,
    });
    // L64 landing: on a fresh page the map's own initial fit (the case box)
    // runs after this effect — fit once the map has settled.
    let t: ReturnType<typeof setTimeout> | undefined;
    if (map.loaded() && !map.isMoving()) fit();
    else map.once("idle", () => { t = setTimeout(fit, 50); });
    return () => { if (t) clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fit on entering the view and on a dance click only
  }, [map, radarOn, riverWin, passReq?.n]);
  // Lucas + lead-analysis: the NISAR MOSAIC of the day (tracks merged per
  // ~6-day window) replaces the single pass while the view follows the
  // timeline; a pass picked in the picker pins that single pass.
  const mosaicList = useMemo(() => nisarMosaics(bundle), [bundle]);
  // L67 (Lucas): the mosaic is NOT the default any more (it caused problems);
  // the code stays for a later opt-in.
  const MOSAIC_DEFAULT = false;
  const mosaic = MOSAIC_DEFAULT && radarOn && !rPick.date ? mosaicFor(mosaicList, step?.date, rPick.pol) : undefined;
  const shownFrame = mosaic ? { url: mosaic.url, bounds: mosaic.bounds, stretch: mosaic.stretch } : rFrame;
  const [seams, setSeams] = useState(false);
  // The swath-edge note only when the visible map runs past the frame's
  // missing side (zoomed out, or a pass that does not fill the window).
  const swathNote = rFrame && !mosaic && viewBox
    ? swathEdgeNote(rFrame.pass, rFrame.bounds, bundle.bbox as LonLatBox | undefined, viewBox) : undefined;
  // Lucas: a red outline around the NISAR swath (where the radar looked).
  const radarFootprint = useFootprint(radarOn ? shownFrame?.url : undefined, shownFrame?.bounds);
  // 'Show pass seams': one outline per contributing pass (lead-analysis footprints).
  useEffect(() => {
    if (!map) return;
    let live = true;
    const url = seams && mosaic?.footprintsUrl;
    const draw = (fc?: GeoJSON.FeatureCollection) => {
      const run = () => drawFootprint(map, "tp-radar-seams", fc, "tp-flood-fill");
      if (map.isStyleLoaded()) run(); else map.once("load", run);
    };
    if (!url) { draw(undefined); return; }
    fetch(url).then((r) => (r.ok ? r.json() : undefined)).then((fc) => { if (live) draw(fc as GeoJSON.FeatureCollection | undefined); }).catch(() => {});
    return () => { live = false; };
  }, [map, seams, mosaic?.footprintsUrl]);
  useEffect(() => {
    if (!map) return;
    const run = () => drawFootprint(map, "tp-radar-footprint", radarOn ? radarFootprint : undefined, "tp-flood-fill");
    if (map.isStyleLoaded()) run(); else map.once("load", run);
  }, [map, radarOn, radarFootprint]);
  // Lucas: 'Water in red' (default) recolours the frame — darker = redder.
  const radarLook = useRadarLook();
  const radarUrl = useWaterUrl(shownFrame?.url, shownFrame?.stretch, radarLook === "water");
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
      radarFrame: shownFrame && radarUrl ? { url: radarUrl, bounds: shownFrame.bounds } : undefined,
      nisarWater: fnWaterShown,
      // Lucas: NISAR is the star — the risk index sits under it, more transparent.
      tpiOpacity: radarOn && state.layers.includes("tpi") ? 0.55 : undefined,
    }),
    [step?.date, state.layers, state.probHorizon, issued, hourFrame, state.basemap, state.overlayOpacity, bundle, changeImage, state.changeOpacity, radarUrl, fnFrame, fnWaterShown, radarOn, shownFrame]
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

  // Bounds of the map view for the locator; one update per frame while moving.
  const [locatorBox, setLocatorBox] = useState<[number, number, number, number] | null>(null);
  useEffect(() => {
    if (!map) return;
    let frame = 0;
    const read = () => {
      frame = 0;
      const b = map.getBounds();
      setLocatorBox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
    };
    const onMove = () => { if (!frame) frame = requestAnimationFrame(read); };
    map.on("move", onMove);
    onMove();
    return () => {
      map.off("move", onMove);
      if (frame) cancelAnimationFrame(frame);
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
      className="absolute inset-0 overflow-hidden bg-cc-map"
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

      {/* lead-ux: the Live / Replay word, "Open the Uruguaiana case" and
          "Back to live" chips moved to the top bar (case picker + "← Now"). */}

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
          <span data-radar-chip className="text-xs font-semibold">{mosaic ? mosaicChip(mosaic) : <>{rFrame.chip}{rFrame.note ? ` (${rFrame.note})` : ""}</>}</span>
          <span data-radar-follow={rFrame.pinned ? "pinned" : "timeline"} className="text-[11px] font-medium text-cc-muted">
            {rFrame.pinned ? `Pinned: ${fmtDate(rFrame.pass.date)} — the next timeline step follows the day again`
              : mosaic ? `Following the timeline · mosaic of ${mosaic.passes.length} passes${mosaic.coverage !== undefined ? ` · ${Math.round(mosaic.coverage)} % of the area` : ""}` : "Following the timeline"}
          </span>
          {mosaic && (
            <>
              <span className="max-w-[340px] text-[11px] font-medium text-cc-muted" data-mosaic-note>{MOSAIC_NOTE}</span>
              {mosaic.footprintsUrl && (
                <label className="flex items-center gap-1 text-[11px] font-medium">
                  <input type="checkbox" checked={seams} onChange={(e) => setSeams(e.target.checked)} data-mosaic-seams />
                  Show pass seams
                </label>
              )}
            </>
          )}
          {swathNote && <span className="text-[11px] font-medium text-cc-warn" data-swath-note>{swathNote}</span>}
          <div className="flex flex-wrap items-center gap-1">
            {(["HH", "HV"] as const).map((p) => {
              // A mosaic switches pol without pinning a pass; HV only where the window has it.
              const has = mosaic ? mosaicList.some((m) => m.start === mosaic.start && m.pol === p) : !!rFrame.pass.frames[p];
              const on = (mosaic ? mosaic.pol : rFrame.pol) === p;
              return (
                <button key={p} type="button" aria-pressed={on} disabled={!has} data-radar-pol={p}
                  title={has ? undefined : `No ${p} image for this ${mosaic ? "window" : "pass"}`}
                  onClick={() => setRPick((x) => (mosaic ? { ...x, pol: p } : { ...x, date: rFrame.pass.date, track: rFrame.pass.track, pol: p }))}
                  className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${on ? "bg-cc-accent text-white" : "border border-cc-line-strong"} disabled:opacity-40`}>
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
          <span className="flex items-center gap-1 text-[11px] font-medium text-cc-muted" data-footprint-legend>
            <span aria-hidden="true" className="inline-block h-2.5 w-6 rounded-sm border-2" style={{ borderColor: "#ff1f1f", boxShadow: "0 0 0 1px #141414" }} />
            {FOOTPRINT_LEGEND}
          </span>
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
          {/* lead-ux: one "Dark ▾" button; the three choices open above it. */}
          {bmOpen && (
            <div role="group" aria-label="Basemap" data-basemap
              className={`flex items-center gap-1 ${chip} !py-0.5`}>
              {BASEMAP_ORDER.map((b) => (
                <button key={b} type="button" aria-pressed={state.basemap === b}
                  onClick={() => { dispatch({ type: "setBasemap", basemap: b }); setBmOpen(false); }}
                  className={`rounded-md px-1.5 py-0.5 text-xs font-semibold ${state.basemap === b ? "bg-cc-accent text-white" : "border border-cc-line-strong"}`}>
                  {BASEMAP_LABEL[b]}
                </button>
              ))}
            </div>
          )}
          <button type="button" data-basemap-toggle aria-expanded={bmOpen} aria-label={`Basemap: ${BASEMAP_LABEL[state.basemap]}`}
            onClick={() => setBmOpen((o) => !o)} className={`${chip} !py-0.5 !text-xs font-semibold`}>
            {BASEMAP_LABEL[state.basemap]} ▾
          </button>
          <div className="flex items-end gap-2">
            {/* lead-ux: ONE legend chip at every width; a tap opens the full legend. */}
            <LegendChip bundle={bundle} layers={state.layers} hazard={state.hazard} stepDate={step?.date} probHorizon={state.probHorizon} changeCyan={state.changeCyan} />
            <LocatorInset bbox={bundle.bbox} view={locatorBox} onRecenter={() => fit(500)} className="max-[899px]:hidden" />
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
        <CompareOverlay key={`${bundle.id}-${comparePair.kind}-${comparePair.before.url}-${comparePair.after.url}`}
          // Lucas: the camera fits the real footprint of the two frames (their
          // intersection, or the union when they barely overlap) — never a
          // fixed box; each image keeps its own bounds.
          pair={footprintPair(comparePair)}
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

// Camera box of a compare pair: the intersection of the two frames' own
// bounds, or their union when the overlap is under a quarter of the smaller
// frame (or a side has no bounds — then the pair's own box stays).
type Box4 = [number, number, number, number];
export function footprintBounds(a?: readonly number[], b?: readonly number[]): Box4 | undefined {
  if (!a || !b || a.length !== 4 || b.length !== 4) return undefined;
  const area = (x: readonly number[]) => Math.max(0, x[2] - x[0]) * Math.max(0, x[3] - x[1]);
  const inter: Box4 = [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])];
  const union: Box4 = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
  return area(inter) >= 0.25 * Math.min(area(a), area(b)) ? inter : union;
}
function footprintPair(p: ComparePair): ComparePair {
  const fb = footprintBounds(p.before.bounds, p.after.bounds);
  return fb ? { ...p, bounds: fb } : p;
}
