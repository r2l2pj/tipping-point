"use client";

import MethodsDrawer from "./MethodsDrawer";
import { liveClasses } from "@/lib/contract";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import type { AlertCardData } from "@/lib/alerts";
import EvidenceDetails from "./EvidenceDetails";
import LayersColumn from "./LayersColumn";
import MapStage, { type CityPin, type MapFocus } from "./MapStage";
import CityCard from "./CityCard";
import CitySearch from "./CitySearch";
import ReplayCityCard from "./ReplayCityCard";
import { parsePlaces, type Place, type Places } from "./model/places";
import { placesDailyUrl, replayDayClass, replaySource, type ReplayPlace } from "./model/replayPlaces";
import PanelTabs, { type PanelTab } from "./PanelTabs";
import { headlineFor } from "./model/headline";
import { geoUrl } from "@/lib/layers";
import PanelSection from "./PanelSection";
import { observedRows, type AlertList } from "./model/alerts";
import { expandLayerUrls } from "./model/layerUrls";
import { priorityAreas } from "./model/areas";
import { availability, missingText } from "./model/availability";
import { dataStatus, freshnessText } from "./model/dataStatus";
import { classLabel, fmtDate, fmtKm2 } from "./model/format";
import { CLASS_COLOR } from "./model/legend";
import { layerRows, visibleLayerRows } from "./model/layerRegistry";
import { caseDirOf, declared, effectiveBundle, productUrl, withLazy, zonesAreTrimmed } from "./model/lazy";
import { bothNisar, COMPARE_PRESETS, compareLayers, defaultSides, nisarDefaults, presetSides, sideDays, sidesCompare, viewCompareLayer, nisarSideDays } from "./model/sideCompare";
import { changeSelection, changeValue, nisarChangeFrames, resolveChangePair } from "./model/nisarChange";
import { nightLightsCompare, radarCard } from "./model/radar";
import { evidence, replayCheckRows } from "./model/replayCheck";
import { sourceRows } from "./model/sources";
import { statusChip } from "./model/status";
import { chartForecastLabel, effectiveLead, forecastForDay, forecastLeadsForDay, forecastTag, frameFor, stepLabel, stepMarks, stepsFor, timelineForecastTag, noStepsText } from "./model/steps";
import { criticalAreaTile, peopleTile, zoneSnapshot } from "./model/tiles";
import type { CcBundle } from "./model/types";
import PriorityAreas from "./PriorityAreas";
import PublicAlertsPanel from "./PublicAlertsPanel";
import RadarCard from "./RadarCard";
import RainSoilChart from "./RainSoilChart";
import ReplayCheck from "./ReplayCheck";
import SituationPanel from "./SituationPanel";
import SourceAges from "./SourceAges";
import { initialState, linkParams, PLAYBACK_MS, reduce, type Action, type ScreenState, type DeepLink } from "./state";
import StatusBlock from "./StatusBlock";
import { hideMissingFor } from "./model/sparse";
import { hasHourFrames, hourFrameFor, hoursOn } from "./model/hours";
import { bundleForRun, parseNowHistory, type NowRun } from "./model/nowHistory";
import { resolveAsset } from "@/lib/contract";
import { plainText } from "./model/plain";
import Tiles from "./Tiles";
import TimelineBar from "./TimelineBar";
import TopBar, { type View } from "./TopBar";
import { useGeo } from "./useGeo";
import { useInsets } from "./useInsets";
import { agreementText, nisarAgreement } from "./model/floodNisar";
import { requestRadarPass } from "./useRadarPick";

export interface CommandCenterProps {
  cases: CcBundle[];
  alertList: AlertList;
  fontClass?: string;
  // `?case=<id|alias>` deep link: the case the screen opens on.
  initialCase?: string;
  // `?day=<offset>` deep link: day offset from the event day.
  initialDay?: number | string;
  deepLink?: DeepLink;
  // `?city=<IBGE id>` deep link: open the City card of that municipality.
  initialCity?: string;
}

// Browser clock, minute steps. Undefined on the server and during hydration,
// so ages and "stale" are computed on the client only (Cache Components).
const subscribeClock = (onTick: () => void) => {
  const t = setInterval(onTick, 60_000);
  return () => clearInterval(t);
};
const clockNow = (): number | undefined => Math.floor(Date.now() / 60_000) * 60_000;
const noClock = (): number | undefined => undefined;

// The address decides the view: /alerts is the Public Alerts view. The switch
// uses the History API, so the client root is not remounted and the map, the
// step and the layers stay as they were.
export default function CommandCenter(props: CommandCenterProps) {
  const pathname = usePathname();
  const view: View = pathname === "/alerts" ? "public" : "command";
  const nowMs = useSyncExternalStore(subscribeClock, clockNow, noClock);
  const onView = useCallback((next: View) => {
    const path = next === "public" ? "/alerts" : "/";
    // The case and step of the address stay with the view.
    if (window.location.pathname !== path) {
      window.history.pushState(null, "", path + window.location.search);
    }
  }, []);
  return <CommandCenterView {...props} view={view} onView={onView} nowMs={nowMs} />;
}

export function CommandCenterView({
  cases: rawCases,
  alertList,
  fontClass = "",
  initialCase,
  initialDay,
  deepLink,
  initialCity,
  view,
  onView,
  nowMs,
  initial,
  panelTab,
}: CommandCenterProps & {
  view: View;
  onView: (v: View) => void;
  nowMs?: number;
  initial?: Partial<ScreenState>; // tests
  panelTab?: PanelTab | "all"; // tests: "all" renders every tab's content in order
}) {
  // Layer URLs travel compacted (page budget): restore them once, here.
  const cases = useMemo(() => rawCases.map(expandLayerUrls), [rawCases]);
  const reducer = useCallback(
    (s: ScreenState, a: Action) => reduce(s, a, cases),
    [cases]
  );
  const [state, dispatch] = useReducer(reducer, undefined, () => ({
    ...initialState(cases, initialCase, initialDay, deepLink),
    ...initial,
  }));
  const [drawer, setDrawer] = useState(false); // Layers drawer, narrow screens
  const [focus, setFocus] = useState<MapFocus>(); // "View on the map" target

  // City search (Lucas): places.json of the live run (lead-analysis). A
  // pick in Now opens the live card; a pick in a replay case stays there —
  // the source is that case's municipalities (replaySource).
  // geoUrl, not declared(): an old manifest that never lists `places` (the
  // frozen bundled copy, an older SQLite table) still serves the canonical
  // places.json — only an explicit `places: null` (declared absent) hides it.
  const liveCase = cases.find((c) => c.mode === "live");
  const placesUrl = liveCase
    ? geoUrl(liveCase, caseDirOf(liveCase), "places", "places.json")
    : undefined;
  const [city, setCity] = useState<{ place: Place; dates: string[] }>();
  // Lucas: hour chips (6-hourly frames) — kept per case, cleared on a case change.
  const [hourPick, setHourPick] = useState<{ caseId: string; hour: string | null }>({ caseId: "", hour: null });
  const [replayCity, setReplayCity] = useState<ReplayPlace>();
  // L52 U1: right-panel tabs (content moved, nothing deleted).
  // Lucas: every case lands on the map + Overview; only a radar swipe link
  // opens the Radar tab.
  const [tab, setTab] = useState<PanelTab | "all">(panelTab ?? (deepLink?.compare === "radar" ? "radar" : "overview"));
  // Lucas: Now steps through the last Now runs (every 3 h) — lead-analysis
  // now/history. Map only; the panel keeps the latest figures.
  const historyUrl = liveCase && declared(liveCase, "history")
    ? geoUrl(liveCase, caseDirOf(liveCase), "history", "history/index.json")
    : undefined;
  const [runs, setRuns] = useState<NowRun[]>([]);
  useEffect(() => {
    if (!historyUrl) return;
    let alive = true;
    fetch(historyUrl).then((r) => (r.ok ? r.json() : null)).then((raw) => { if (alive) setRuns(parseNowHistory(raw)); }).catch(() => {});
    return () => { alive = false; };
  }, [historyUrl]);
  const [runPick, setRunPick] = useState<{ run: NowRun; manifest: unknown }>();
  const pickRun = (id: string | null) => {
    const run = id ? runs.find((r) => r.id === id) : undefined;
    // The newest run is the live view itself.
    if (!run || run.id === runs[0]?.id || !liveCase) { setRunPick(undefined); return; }
    const url = resolveAsset(liveCase.assetBase ?? "/data", "", run.manifest);
    if (!url) return;
    fetch(url).then((r) => (r.ok ? r.json() : null)).then((m) => { if (m) setRunPick({ run, manifest: m }); }).catch(() => {});
  };
  // Headline card (Lucas, "simple and WOW"): the live places file, once.
  const [places, setPlaces] = useState<Places>();
  useEffect(() => {
    if (!placesUrl) return;
    let alive = true;
    fetch(placesUrl).then((r) => (r.ok ? r.json() : null)).then((raw) => {
      const p = raw ? parsePlaces(raw) : undefined;
      if (alive && p) setPlaces(p);
    }).catch(() => {});
    return () => { alive = false; };
  }, [placesUrl]);
  const activeRef = useRef(state.activeId);
  useEffect(() => { activeRef.current = state.activeId; }, [state.activeId]);
  const setCityParam = (id: string | undefined) => {
    const params = new URLSearchParams(window.location.search);
    if (id) params.set("city", id); else params.delete("city");
    const q = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (q ? `?${q}` : ""));
  };
  const onCity = useCallback((place: Place, all: Places) => {
    onView("command");
    // L52 (Lucas): city zoom-ins on a detailed basemap with street labels
    // (Streets: the daily VIIRS satellite is too coarse/cloudy at city zoom).
    dispatch({ type: "setBasemap", basemap: "streets" });
    if (liveCase && activeRef.current === liveCase.id) {
      // Now: the live city card (forecast days) — unchanged behaviour.
      setCity({ place, dates: all.dates });
      setReplayCity(undefined);
      setCityParam(place.id);
    } else {
      // Replay pick: stay in the case — the replay card + outline, never
      // a switch to Now and no live numbers.
      setReplayCity(place as ReplayPlace);
      setCity(undefined);
    }
    setFocus({ bbox: place.bbox, center: place.centroid, key: Date.now() });
  }, [liveCase, onView]);
  const cityPin = useMemo<CityPin | undefined>(
    () => {
      const p = city?.place ?? replayCity;
      return p ? { center: p.centroid, name: p.name } : undefined;
    },
    [city, replayCity]
  );
  // A case change closes the replay card (its municipality belongs to the
  // case it was picked in); the live card keeps resolving to Now.
  const [prevActive, setPrevActive] = useState(state.activeId);
  if (prevActive !== state.activeId) {
    setPrevActive(state.activeId);
    if (replayCity) setReplayCity(undefined);
  }

  useEffect(() => {
    if (!state.playing) return;
    const t = setInterval(() => dispatch({ type: "tick" }), PLAYBACK_MS);
    return () => clearInterval(t);
  }, [state.playing]);

  // The address names the case and step in view, so a reload or a copied
  // link opens the same thing. Not written while playing.
  useEffect(() => {
    if (state.playing) return;
    const want = linkParams(cases.find((c) => c.id === state.activeId), state.dayIndex);
    const params = new URLSearchParams(window.location.search);
    for (const key of ["case", "day"] as const) {
      const v = want[key];
      if (v === undefined) params.delete(key);
      else params.set(key, v);
    }
    const query = params.toString();
    const search = query ? `?${query}` : "";
    if (search !== window.location.search) {
      window.history.replaceState(null, "", window.location.pathname + search);
    }
  }, [cases, state.activeId, state.dayIndex, state.playing]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        dispatch({ type: "escape" });
        setDrawer(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The loader sends a trimmed bundle: the full priority zones and the scar
  // candidates are fetched here by URL (shared cache with the map hook). A
  // pending token of the loader wins over any value.
  const loaded = cases.find((c) => c.id === state.activeId);
  const zonesUrl = loaded && zonesAreTrimmed(loaded) ? productUrl(loaded, "priority_zones") : undefined;
  const scarsUrl = loaded && !loaded.scars ? productUrl(loaded, "landslide_candidates") : undefined;
  const fullZones = useGeo(zonesUrl);
  const scars = useGeo(scarsUrl);
  // Memoized: the bundle, the steps and the areas keep their identity
  // between timeline steps, so a step does not rebuild the map sources, the
  // pins or the zone list (that made playback stutter).
  const bundle = useMemo(
    () => loaded && withLazy(effectiveBundle(loaded), {
      zones: fullZones, zonesRequested: !!zonesUrl,
      scars, scarsRequested: !!scarsUrl,
    }),
    [loaded, fullZones, zonesUrl, scars, scarsUrl]
  );
  const steps = useMemo(() => stepsFor(bundle), [bundle]);
  // places_daily (lead-analysis, tipping-point/places-daily@1): the shown
  // step day's observed flood class per municipality — replay only.
  const dailyUrl = bundle?.mode === "replay" ? placesDailyUrl(bundle) : undefined;
  const [daily, setDaily] = useState<{ url: string; raw: unknown }>();
  useEffect(() => {
    if (!dailyUrl) return;
    let alive = true;
    fetch(dailyUrl)
      .then((r) => (r.ok ? r.json() : null))
      .then((raw) => { if (alive) setDaily({ url: dailyUrl, raw }); })
      .catch(() => {});
    return () => { alive = false; };
  }, [dailyUrl]);
  const areas = useMemo(() => priorityAreas(bundle), [bundle]);
  const marks = useMemo(() => (bundle ? stepMarks(bundle, steps) : []), [bundle, steps]);
  const radar = useMemo(() => (bundle ? radarCard(bundle) : undefined), [bundle]);
  const observed = useMemo(() => observedRows(bundle), [bundle]);

  const seek = useCallback((index: number) => dispatch({ type: "seek", index }), []);
  const toggleTable = useCallback(() => dispatch({ type: "toggleTable" }), []);

  // Floating cards over the full-bleed map (wide screens).
  const stageRef = useRef<HTMLDivElement>(null);
  const layersRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const insets = useInsets(stageRef, layersRef, panelRef, timelineRef);
  // lead-ux 1/8: map-first at every width — the page never scrolls; the
  // drawer / sheet scrolls inside.
  const root = `cc-root flex h-dvh flex-col overflow-hidden ${fontClass}`;

  if (!bundle || !radar) {
    return (
      <div className={root}>
        <TopBar cases={cases} bundle={undefined} view={view} onView={onView}
          onSelectCase={() => {}}
          freshness="Unknown age · no data loaded" freshnessKind="unknown-age" />
        <main className="m-auto max-w-md">
          <p role="status" className="p-6 text-center text-[15px]">
            Data unavailable: no case could be loaded. This does not mean there is no risk.
          </p>
        </main>
      </div>
    );
  }

  const live = bundle.mode === "live";
  const hour = hourPick.caseId === bundle.id ? hourPick.hour : null;
  const step = steps[state.dayIndex];
  // Keyed read: a stale places_daily while a new case's is in flight.
  const dailyRaw = daily && daily.url === dailyUrl ? daily.raw : undefined;
  // Every case gets the box when it has a source: Now reads the live
  // places.json; a replay reads its own municipalities (or the live list
  // cut to its bounds; no source → the box hides).
  const searchSource = live
    ? (placesUrl ? { kind: "live" as const, url: placesUrl } : undefined)
    : replaySource(bundle, placesUrl);
  const status = dataStatus(bundle, nowMs);
  const chip = statusChip(bundle, steps, state.dayIndex, state.hazard);
  // Featured replay (v6): blocks with no data for this case are not shown.
  const sparse = hideMissingFor(bundle);
  const rows = visibleLayerRows(layerRows(bundle, state.layers, state.hazard, step?.date, state.probHorizon, { sel: state.nisarChange }))
    .filter((r) => !sparse || r.available || r.on);
  const nightLights = nightLightsCompare(bundle);
  // Lucas (step 2): ONE generic Compare ⇆ — LEFT / RIGHT layer + day +
  // Observed | Forecast k / pol / track / horizon each.
  const sidesNow = state.compareOpen === "forecast"
    ? (state.compareSides ?? defaultSides(bundle, steps, state.dayIndex, state.hazard, state.issueLead,
        { layer: viewCompareLayer(bundle, steps, state.layers, state.hazard), horizon: state.probHorizon }))
    : undefined;
  const sidesInfo = sidesNow
    ? {
        sides: sidesNow,
        result: sidesCompare(bundle, sidesNow),
        layers: compareLayers(bundle, steps, state.hazard),
        days: {
          L: sideDays(bundle, steps, state.hazard, sidesNow.L),
          R: sideDays(bundle, steps, state.hazard, sidesNow.R),
        },
        presets: COMPARE_PRESETS.filter((p) => presetSides(bundle, steps, state.dayIndex, state.hazard, p.id)),
      }
    : undefined;
  const nisarOpen = bothNisar(sidesNow);
  const comparePair =
    state.compareOpen === "night-lights" ? nightLights.pair
    : state.compareOpen === "forecast"
      ? sidesInfo?.result.pair
      : undefined;
  const snap = zoneSnapshot(bundle, step?.date);
  // The ML card's global tables travel on one case only (page budget).
  const sharedMl = cases.find((c) => c.ml?.when)?.ml;
  const mergedMl = bundle.ml && sharedMl ? { ...sharedMl, ...bundle.ml } : bundle.ml;
  const tag = forecastTag(bundle);
  // The TPI frame on the map, one source of truth for the tiles and the
  // timeline chip: the "forecast issued this day" overlay when on, else the
  // step's own frame (TPI index on).
  const tpiOn = state.layers.includes("tpi");
  const overlayFrame = tpiOn ? forecastForDay(bundle, step?.date, state.hazard, effectiveLead(bundle, step?.date, state.hazard, state.issueLead))?.layer : undefined;
  const mapFrame = overlayFrame ?? (tpiOn ? frameFor(bundle, step?.date, state.hazard)?.layer : undefined);
  // Area by class of the selected hazard (summary v2), or the v1 union.
  const classesKm2 = bundle.riskPaused ? [] : Object.entries(liveClasses(bundle.summary, state.hazard) ?? {});
  const headlineData = live && places
    ? headlineFor(places, (bundle.summary as Record<string, unknown> | undefined)?.people_alert as number | undefined)
    : undefined;
  // lead-analysis (WA): one honest line on forecast vs the NISAR flood area.
  const fnn = (bundle.metricsView as Record<string, unknown> | undefined)?.forecast_nisar_overlap_note;
  const forecastNisarNote = typeof fnn === "string" && fnn.trim() ? plainText(fnn.trim()) : undefined;
  const tabsShown: PanelTab[] = view === "public" ? ["alerts", "map"]
    : ["overview", "map", "forecast", "radar", ...(!sparse || areas.length > 0 ? ["places" as const] : [])];
  // The Public Alerts view has its own first tab; a tab that does not exist
  // in the view in sight falls back to that view's first tab.
  const tabNow: PanelTab = tab === "all" ? tabsShown[0] : tabsShown.includes(tab) ? tab : tabsShown[0];
  const show = (t: PanelTab) => tab === "all" || t === tabNow;
  const areaTile = criticalAreaTile(bundle, state.hazard, step?.date, step?.day, overlayFrame);
  const peopleT = peopleTile(bundle, step?.date, step?.day, overlayFrame);
  const showAffected = !sparse || !areaTile.missing || !peopleT.missing || classesKm2.length > 0;

  // Gap 2: the card's own geometry frames the map; a card without one falls
  // back to the priority area of the same name.
  const viewOnMap = (alert: AlertCardData) => {
    onView("command");
    if (alert.bbox || alert.centroid) {
      setFocus({ bbox: alert.bbox, center: alert.centroid, key: Date.now() });
      return;
    }
    const name = alert.region ?? alert.area;
    const match = areas.find((a) => a.name === name);
    if (match && state.selectedArea !== match.rank) dispatch({ type: "selectArea", rank: match.rank });
  };

  return (
    <div className={root} data-view={view} data-case={bundle.id} data-layers={state.layers.join(",")}>
      <TopBar
        cases={cases}
        bundle={bundle}
        view={view}
        onView={onView}
        onSelectCase={(id) => dispatch({ type: "selectCase", id })}
        onTour={(a) => dispatch(a)}
        onNow={bundle.mode === "replay" && cases.some((c) => c.mode === "live") ? () => dispatch({ type: "setMode", mode: "now" }) : undefined}
        freshness={freshnessText(status, nowMs)}
        freshnessKind={status.kind}
        search={searchSource && (
          <CitySearch
            key={bundle.id}
            source={searchSource}
            initialCityId={live ? initialCity : undefined}
            onSelect={onCity}
          />
        )}
      />

      {/* The map fills the whole area at every width; the timeline and the
          one drawer (a bottom sheet on phones) float over it. */}
      <main className="relative min-h-0 flex-1">
        <MapStage
          bundle={live && runPick ? bundleForRun(bundle, runPick.run, runPick.manifest) : bundle}
          steps={steps}
          state={state}
          areas={areas}
          comparePair={comparePair}
          dispatch={dispatch}
          insets={insets}
          stageRef={stageRef}
          focus={focus}
          cityPin={cityPin}
          hourFrame={hourFrameFor(bundle, state.hazard, step?.date, hour)}
          hour={hour}
          sidesCtl={sidesInfo ? {
            sides: sidesInfo.sides, layers: sidesInfo.layers, days: sidesInfo.days,
            presets: sidesInfo.presets,
            warning: sidesInfo.result.warning, note: sidesInfo.result.note, missing: sidesInfo.result.missing,
            nisarOnly: !!state.compareNisarOnly,
            onSide: (side, patch) => dispatch({ type: "setCompareSide", side, ...patch }),
            onPreset: (id) => dispatch({ type: "applyComparePreset", id }),
          } : undefined}
          cityOutline={!live ? replayCity?.outline : undefined}
          onToggleLayers={() => {
            // The map's layers button: open the drawer (or sheet) on the Map
            // tab; pressed again on the Map tab, it hides it.
            const wide = window.matchMedia("(min-width: 900px)").matches;
            const open = wide ? state.layersOpen : drawer;
            if (open && tabNow === "map") {
              if (wide) dispatch({ type: "toggleLayersOpen" }); else setDrawer(false);
              return;
            }
            setTab("map");
            if (wide && !state.layersOpen) dispatch({ type: "toggleLayersOpen" });
            if (!wide) setDrawer(true);
          }}
        />
        {city && live && view === "command" && (
          <div className="pointer-events-auto relative z-30 mx-3 mt-3 min-[900px]:absolute min-[900px]:left-1/2 min-[900px]:top-14 min-[900px]:mx-0 min-[900px]:mt-0 min-[900px]:w-[360px] min-[900px]:-translate-x-1/2">
            <CityCard place={city.place} dates={city.dates}
              onShow={() => setFocus({ bbox: city.place.bbox, center: city.place.centroid, key: Date.now() })}
              onClose={() => { setCity(undefined); setCityParam(undefined); }} />
          </div>
        )}
        {replayCity && !live && view === "command" && (
          <div className="pointer-events-auto relative z-30 mx-3 mt-3 min-[900px]:absolute min-[900px]:left-1/2 min-[900px]:top-14 min-[900px]:mx-0 min-[900px]:mt-0 min-[900px]:w-[360px] min-[900px]:-translate-x-1/2">
            <ReplayCityCard place={replayCity}
              stepDate={step?.date}
              dayClass={replayDayClass(dailyRaw, replayCity.code, step?.date)}
              onShow={() => setFocus({ bbox: replayCity.bbox, center: replayCity.centroid, key: Date.now() })}
              onClose={() => setReplayCity(undefined)} />
          </div>
        )}
        {/* lead-ux 1/8: ONE drawer. Wide: a 360 px panel on the right
            (closable; the map's layers button reopens it on the Map tab).
            Phones: a bottom sheet over the full-screen map, closed by default
            (its tab row is the peek). The Layers column is the "Map" tab. */}
        {/* Lucas: the drawer opens closed; a slim edge tab opens it over the map. */}
        <button type="button" data-drawer-open aria-label="Show details" title="Show details"
          onClick={() => {
            if (window.matchMedia("(min-width: 900px)").matches) dispatch({ type: "toggleLayersOpen" });
            else setDrawer(true);
          }}
          className={`absolute right-0 top-1/2 z-30 flex -translate-y-1/2 items-center gap-1 rounded-l-xl border border-r-0 border-cc-line bg-cc-surface px-1.5 py-3 text-[13px] font-semibold text-cc-ink shadow-[0_2px_10px_rgba(0,0,0,0.3)] [writing-mode:vertical-rl] ${state.layersOpen ? "min-[900px]:hidden" : ""} ${drawer ? "max-[899px]:hidden" : ""}`}>
          Details ›
        </button>
        <div className="pointer-events-none absolute inset-0 z-20 flex flex-col justify-end gap-2 p-2 min-[900px]:grid min-[900px]:grid-cols-[minmax(0,1fr)_auto] min-[900px]:grid-rows-[minmax(0,1fr)_auto] min-[900px]:gap-3 min-[900px]:p-3">
        {/* Phones: the open sheet is a reading view — the timeline steps aside. */}
        <div ref={timelineRef} className={`pointer-events-auto min-w-0 min-[900px]:col-start-1 min-[900px]:row-start-2 ${drawer ? "max-[899px]:hidden" : ""}`}>
      <TimelineBar
        emptyText={noStepsText(bundle)}
        steps={steps}
        marks={marks}
        dayIndex={state.dayIndex}
        playing={state.playing}
        forecastTag={timelineForecastTag(bundle)}
        mapFrame={mapFrame}
        onSeek={(index) => dispatch({ type: "seek", index })}
        onPrev={() => dispatch({ type: "prev" })}
        onNext={() => dispatch({ type: "next" })}
        onPlayToggle={() => dispatch({ type: "togglePlay" })}
        forecastDates={live ? undefined : new Set(steps.filter((s) => forecastLeadsForDay(bundle, s.date, state.hazard).length > 0).map((s) => s.date))}
        runs={live && runs.length > 1 ? { list: runs, picked: runPick?.run.id ?? runs[0].id, onPick: pickRun } : undefined}
        hours={hasHourFrames(bundle, state.hazard) ? { available: hoursOn(bundle, state.hazard, step?.date), hour, onHour: (h) => setHourPick({ caseId: bundle.id, hour: h }) } : undefined}
      />
        </div>

        <div ref={panelRef} data-drawer={state.layersOpen ? "open" : "closed"} data-sheet={drawer ? "open" : "closed"}
          className={`pointer-events-auto flex min-h-0 min-[900px]:col-start-2 min-[900px]:row-span-2 min-[900px]:row-start-1 min-[900px]:self-start min-[900px]:max-h-full ${state.layersOpen ? "" : "min-[900px]:hidden"} ${drawer ? "" : "max-[899px]:hidden"}`}>
        <SituationPanel label={view === "public" ? "Public alerts" : "Situation overview"} sheetOpen={drawer}
          header={tab !== "all" ? (
            <PanelTabs tabs={tabsShown} tab={tabNow} onTab={(t) => { setTab(t); setDrawer(true); }}
              onClose={() => {
                if (window.matchMedia("(min-width: 900px)").matches) dispatch({ type: "toggleLayersOpen" });
                else setDrawer(false);
              }}
              sheetOpen={drawer} />
          ) : undefined}>
          {show("map") && (
              <LayersColumn
                bundle={bundle}
                rows={rows}
                hazard={state.hazard}
                layers={state.layers}
                notice={state.layerNotice}
                probHorizon={state.probHorizon}
                stepDate={step?.date}
                onHazard={(hazard) => dispatch({ type: "setHazard", hazard })}
                onToggle={(id) => dispatch({ type: "toggleLayer", id })}
                onProbHorizon={(horizon) => dispatch({ type: "setProbHorizon", horizon })}
                onView={(v) => dispatch({ type: "setView", view: v })}
                satellite={{
                  on: state.basemap === "satellite",
                  toggle: () => dispatch({ type: "setBasemap", basemap: state.basemap === "satellite" ? "dark" : "satellite" }),
                }}
                opacity={state.overlayOpacity}
                onOpacity={(value) => dispatch({ type: "setOpacity", value })}
                changeCyan={state.changeCyan}
                embedded
              />
          )}
          {view === "public" ? (show("alerts") && <>
            <PublicAlertsPanel
              alertList={alertList}
              live={live}
              onGoToNow={() => dispatch({ type: "setMode", mode: "now" })}
              onViewOnMap={viewOnMap}
              observed={observed}
              observedOn={state.layers.includes("flood")}
              onShowObserved={() => dispatch({ type: "toggleLayer", id: "flood" })}
              bundle={bundle}
              stepDate={step?.date}
            >
            </PublicAlertsPanel>
            <SourceAges rows={sourceRows(bundle, nowMs)} />
          </>) : (
            <>
              {show("overview") && (
                <>
                  {headlineData && places && (
                    <section aria-label="Headline" data-headline className="rounded-xl border-2 border-cc-accent-line bg-cc-accent-soft px-3 py-2.5">
                      <p className="m-0 text-[17px] font-semibold leading-snug">{headlineData.text}</p>
                      <button type="button" onClick={() => onCity(headlineData.top, places)} className="mt-1.5 rounded-lg bg-cc-accent px-3 py-1.5 text-sm font-semibold text-white">Show me</button>
                    </section>
                  )}
                  {(() => {
                    // L61: lead-ml's NISAR agreement — how much of the water
                    // NISAR saw the index had flagged days before.
                    const a = nisarAgreement(bundle);
                    if (!a) return null;
                    const t = agreementText(a);
                    return (
                      <section aria-label="NISAR agreement" data-nisar-agreement className="rounded-xl border border-cc-line px-3 py-2 text-[13px]">
                        <p className="m-0 text-xs font-semibold uppercase tracking-wide text-cc-muted">{t.label}</p>
                        <p className="m-0 font-semibold">{t.main}</p>
                        {t.more && <p className="m-0 text-cc-muted" data-agreement-details>{t.more}</p>}
                        <p className="m-0 text-xs text-cc-muted">NISAR water = candidate (very dark radar outside permanent water)</p>
                      </section>
                    );
                  })()}
                {showAffected && <div>
                  <h3 className="m-0 mb-1 text-[13px] font-semibold text-cc-muted">Who is affected</h3>
                  <Tiles area={areaTile} people={peopleT} />
                  {classesKm2.length > 0 && (
                    <dl className="mt-2 grid grid-cols-2 gap-x-4 text-[13px]" aria-label="Area by class">
                      {classesKm2.map(([cls, km2]) => (
                        <div key={cls} className="flex items-center gap-2 border-t border-cc-rule py-1">
                          <span aria-hidden="true" className="h-3 w-3 shrink-0 rounded-[3px] border border-black/15"
                            style={{ background: CLASS_COLOR[classLabel(cls)] ?? "var(--cc-line-strong)" }} />
                          <dt className="grow text-cc-muted">class {classLabel(cls)}</dt>
                          <dd className="font-semibold tabular-nums">{fmtKm2(km2)}</dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>}
                </>
              )}
              {show("radar") && (
                <PanelSection label="Now on the ground">
                <RadarCard
                  hideMissing={sparse}
                  change={(() => {
                    // NISAR change layer (Lucas, t5w): the Δ-dB overlay on the
                    // normal map — toggle + two pass pickers + cyan + opacity.
                    const frames = nisarChangeFrames(bundle);
                    if (frames.length < 2) return undefined;
                    const spec = resolveChangePair(bundle, state.nisarChange ?? {}, step?.date);
                    const sel = changeSelection(bundle, state.nisarChange ?? {}, step?.date);
                    return {
                      on: state.layers.includes("nisarChange"),
                      available: "pair" in spec,
                      reason: "reason" in spec ? spec.reason : undefined,
                      label: "pair" in spec ? spec.label : undefined,
                      pol: sel.pol,
                      pols: [...new Set(frames.map((f) => f.pol))],
                      cyan: state.changeCyan,
                      opacity: state.changeOpacity,
                      passes: frames.map((f) => ({
                        value: changeValue(f),
                        label: `${fmtDate(f.date)}${f.track ? ` · track ${f.track}` : ""} · ${f.pol}`,
                      })),
                      before: sel.before,
                      after: sel.after,
                      onToggle: () => dispatch({ type: "toggleLayer", id: "nisarChange" }),
                      onPair: (patch: { before?: string; after?: string; pol?: "HH" | "HV" }) =>
                        dispatch({ type: "setChangePair", ...patch }),
                      onCyan: () => dispatch({ type: "toggleChangeCyan" }),
                      onOpacity: (value: number) => dispatch({ type: "setChangeOpacity", value }),
                    };
                  })()}
                  swipeDates={nisarOpen && sidesNow ? (() => {
                    const track = sidesNow.L.layer === "nisar" ? sidesNow.L.track : sidesNow.R.layer === "nisar" ? sidesNow.R.track : undefined;
                    const dates = nisarSideDays(bundle, track).map((d) => d.date);
                    if (dates.length < 2) return undefined;
                    const def = nisarDefaults(dates, step?.date);
                    return {
                      dates,
                      before: sidesNow.L.layer === "nisar" ? sidesNow.L.date : def.before ?? dates[0],
                      after: sidesNow.R.layer === "nisar" ? sidesNow.R.date : def.after ?? dates[dates.length - 1],
                      onPick: (before, after) => dispatch({ type: "setCompareDates", before, after }),
                    };
                  })() : undefined}
                  radar={radar}
                  dance={bundle.riverDance ? {
                    d: bundle.riverDance,
                    selected: step?.date,
                    // A bar: that day on the timeline, the Radar — NISAR view,
                    // and that exact pass (date + track) on the map.
                    onPass: (date: string, track?: string) => {
                      const idx = steps.findIndex((x) => x.date.slice(0, 10) === date);
                      const near = idx >= 0 ? idx : steps.reduce((bi, x, i) =>
                        Math.abs(Date.parse(x.date) - Date.parse(date)) < Math.abs(Date.parse(steps[bi].date) - Date.parse(date)) ? i : bi, 0);
                      requestRadarPass(date, track, steps[near]?.date.slice(0, 10));
                      if (state.compareOpen) dispatch({ type: "closeCompare" });
                      dispatch({ type: "seek", index: near });
                      dispatch({ type: "setView", view: "radar" });
                    },
                  } : undefined}
                  floodOn={state.layers.includes("flood")}
                  compareOpen={nisarOpen}
                  onConfirm={() => dispatch({ type: "confirmRadar" })}
                  onShowFlood={() => dispatch({ type: "toggleLayer", id: "flood" })}
                  onCompare={() =>
                    dispatch(nisarOpen ? { type: "closeCompare" } : { type: "openCompare", kind: "radar" })}
                />
                {forecastNisarNote && (
                  <p className="m-0 rounded-lg border border-cc-line bg-cc-well px-2.5 py-1.5 text-xs" data-forecast-nisar-note>
                    <span className="font-semibold">Forecast vs NISAR: </span>{forecastNisarNote}
                  </p>
                )}
                </PanelSection>
              )}
              {show("places") && (
                <PriorityAreas
                  areas={areas}
                  missingText={missingText(availability(bundle, "priority_zones"))}
                  expanded={state.listExpanded}
                  selected={state.selectedArea}
                  asOf={snap.asOf}
                  dimmed={snap.dimmed}
                  onToggleAll={() => dispatch({ type: "toggleList" })}
                  onSelect={(rank) => dispatch({ type: "selectArea", rank })}
                  lowerBound={bundle.mode === "replay"}
                  partsNote={bundle.mode === "live"}
                  onJumpToSnapshot={bundle.mode === "replay" && snap.iso
                    ? (() => {
                        const i = steps.findIndex((st) => st.date.slice(0, 10) === snap.iso!.slice(0, 10));
                        return i >= 0 ? () => seek(i) : undefined;
                      })()
                    : undefined}
                />
              )}
              {show("forecast") && (
                <>
                  <PanelSection label="Prediction">
                <StatusBlock
                  chip={chip}
                  status={status}
                  stepLabel={step ? stepLabel(bundle, step) : undefined}
                >
                  {live && chip.kind === "no-data" && cases.some((c) => c.mode === "replay") && (
                    <p className="mt-1.5 text-[13px]">
                      Past events with published data are in Replay.{" "}
                      <button type="button" onClick={() => dispatch({ type: "setMode", mode: "replay" })}
                        className="font-semibold text-cc-accent underline decoration-dotted underline-offset-2">
                        Open Replay
                      </button>
                    </p>
                  )}
                </StatusBlock>

                {(!sparse || (!!bundle.series && steps.some((s) => s.day))) && <RainSoilChart
                  bundle={bundle}
                  steps={steps}
                  dayIndex={state.dayIndex}
                  asTable={state.chartAsTable}
                  forecastLabel={chartForecastLabel(bundle) ?? "source not published"}
                  onSeek={seek}
                  onToggleTable={toggleTable}
                />}
                {rows.some((r) => r.id === "ptrigger" && r.available) && (
                  <p className="m-0 text-xs text-cc-faint">
                    Relative likelihood of a rain-triggered landslide (24 h–14 d) is in the Map tab.
                  </p>
                )}
                  </PanelSection>
              {!live && (
                <ReplayCheck
                  rows={replayCheckRows(bundle)}
                  detailsOpen={state.detailsOpen}
                  onToggleDetails={() => dispatch({ type: "toggleDetails" })}
                >
                  <EvidenceDetails
                    hideMissing={sparse}
                    evidence={evidence(bundle, step?.day)}
                    swot={bundle.swot}
                    ml={mergedMl}
                    mlImportance={bundle.mlImportance}
                    evidenceRows={rows.filter((r) => r.group === "Replay evidence")}
                    nightLights={nightLights}
                    onToggleLayer={(id) => dispatch({ type: "toggleLayer", id })}
                    onCompareNightLights={() => dispatch({ type: "openCompare", kind: "night-lights" })}
                  />
                </ReplayCheck>
              )}

                </>
              )}
              {/* lead-ux: Sources folded into the end of Overview (one tab less);
                  the old footer (data credits + Methods) lives here too. */}
              {(show("sources") || tabNow === "overview") && (
                <details data-sources className="border-t border-cc-rule pt-2" open={tab === "sources" || tab === "all"}>
                  <summary className="cursor-pointer text-sm font-semibold">Sources &amp; credits</summary>
                  <div className="mt-2 flex flex-col gap-2">
                  <SourceAges rows={sourceRows(bundle, nowMs).filter((r) => !sparse || !r.missing)} />
                  <div className="flex flex-col gap-1.5 border-t border-cc-rule pt-2 text-xs text-cc-faint" data-credits>
        <span className="min-w-0 grow">
          Data: NASA GPM · SMAP · NASADEM · NISAR · OPERA · Forecast rain: NOAA GFS (the only non-NASA input) · Basemap © CARTO © OpenStreetMap · Boundaries ©{" "}
          {caseDirOf(bundle) === "wa_2025" ? "US Census · Landslide points: Washington Geological Survey" : "IBGE"} · Population: GPW (NASA SEDAC) / US Census
          {typeof (bundle.metricsView as Record<string, unknown> | undefined)?.flood_ems_iou === "number" &&
            " · Flood reference: © European Union, Copernicus Emergency Management Service"}
        </span>
        {/* Judges (conductor): methods links, closed by default. */}
        <MethodsDrawer />
                </div>
                  </div>
                </details>
              )}
            </>
          )}
        </SituationPanel>
        </div>

        </div>
      </main>

    </div>
  );
}
