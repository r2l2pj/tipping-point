# NISAR change layer (t5w, URGENT) — Δ-dB overlay on the normal map

New layer id `nisarChange` ("NISAR change (what's different)"): a client-side
canvas composite of two same-track, same-pol `nisar_frame` PNGs — Δ = after_dB
− before_dB, transparent |Δ|<3, red #ff1f1f Δ≤−3 (darker = likely new water),
cyan #22d3ee Δ≥+3. Drawn as a MapLibre image source on the after-frame
bounds, added before `tp-flood-fill` (above the risk raster, below the NISAR
candidate polygons). Default pair = last pass before the selected day vs first
pass on/after it on the case default track (HH preferred); pair editable via
two pass pickers in the Radar card; own opacity slider (85% default, min 20%)
+ cyan checkbox. Toggle in the Radar card (switch + pickers) and in Layers →
More layers (Observations). Disabled with reason when tracks/pols differ or
the fixed stretch (stretch_db / legend db_min..db_max) is missing. Deep link
`?nisarChange=<YYYY-MM-DD>:<YYYY-MM-DD>[:HH|HV]`. Works on Dark/Satellite/
Streets (basemap-independent image layer) and in replay + Now (manifest-only
inputs). Legend: "Red: radar ≥3 dB darker (likely new water) · Cyan: ≥3 dB
brighter" (drops to the red half when cyan is off).

Changed:

- `app/src/components/command/model/changeComposite.ts` — `deltaComposite`
  (classified Δ map, before resampled to the after Web-Mercator grid, alpha-16
  swath masking) + `CHANGE_DB`/`CHANGE_RED`/`CHANGE_CYAN`.
- `app/src/components/command/model/nisarChange.ts` (new) — frame list,
  `defaultChangePair`, `changeSelection` (normalizes `<date>|<track>` picks),
  `resolveChangePair` → {pair+label}|{reason}, `nisarChangeAvailable`,
  `nisarChangeUnavailableReason`, `parseChangeLink`.
- `app/src/components/command/model/layerRegistry.ts` — `nisarChange`
  LayerId + Observations row; `layerRows` takes `{ sel }`; row disabled with
  the reason when the picked pair can't composite.
- `app/src/components/command/model/legend.ts` — red/cyan swatch row.
- `app/src/components/command/model/overlays.ts` — `MapLayerOptions.
  changeImage {url, bounds, key}` + `changeOpacity`.
- `app/src/components/command/state.ts` — `nisarChange` sel (case state,
  reset on case change), `changeCyan` + `changeOpacity` (view prefs),
  `setChangePair`/`toggleChangeCyan`/`setChangeOpacity` actions, `DeepLink.
  nisarChange`, `?layers=nisarchange` alias.
- `app/src/components/command/useNisarChange.ts` (new) — hook: loads both
  PNGs (crossOrigin anonymous), extracts ImageData via canvas, runs
  deltaComposite, returns a PNG data URL keyed by pair+cyan (stale image
  hidden until the new composite lands).
- `app/src/components/MapLayers.tsx` — `tp-change-img`/`tp-change-lyr` in
  DYNAMIC_IMG; `ensureImageLayer(…, LYR.floodFill)` + own raster-opacity.
- `app/src/components/command/MapStage.tsx` — resolves the spec, hooks the
  image, feeds `options.changeImage/changeOpacity`, `changeCyan` into both
  map legends.
- `app/src/components/command/RadarCard.tsx` — `data-change-layer` block:
  switch, Before/After pass selects, HH/HV chips, cyan checkbox, opacity
  slider, status line (pair label or block reason).
- `app/src/components/command/LayersColumn.tsx` — `changeCyan` prop →
  legend.
- `app/src/components/command/CommandCenter.tsx` — spec/frames/pickers
  wiring, `layerRows(…, { sel: state.nisarChange })`.
- `app/src/components/command/CommandCenterLoader.tsx` — `?nisarChange`
  search param → deepLink.

Tests: `app/src/components/command/model/nisarChange.test.ts` (new, 14
tests) — Δ classification at the ±3 dB edges, stretch decoding, swath
transparency, resampling; default-pair bracketing + edge clamping; resolve
reasons (same pass / unknown day / track / pol / missing stretch / <2
passes); availability; `<date>|<track>` normalization; deep link.

Commands + results (worktree
`/home/lucascoelho/code/r2l2pj/tipping-point-wt/nisar-change-layer`, branch
`feat/nisar-change-layer`):

- `cd app && bun install` → 762 packages, ok.
- `bun test` → 835 pass / 16 skip / 0 fail (851 tests, 59 files).
- `bun run lint` → 0 errors, 1108 warnings (all pre-existing; none in the
  new/changed lines).
- `bunx tsc --noEmit` → only the two pre-existing `RouteContext` /
  `LayoutProps` errors (verified identical on the clean tree).
- `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=1G bun run
  build` → compiled 12.3 s, TypeScript clean, 13/13 pages generated, exit 0.

Not done / notes: none — no new deps, branch-only commit, nothing pushed.
