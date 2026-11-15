# L42 — WA 2025 NISAR physical-hypothesis test (double bounce)

Branch: `l42-hypothesis` (worktree `tipping-point-data-wt/l42-hypothesis`).
Pushed to `main` via `git push origin l42-hypothesis:main` (fast-forward;
origin/main was the branch base a363629).

## What changed

- `tools/wa_nisar_hypothesis.py` (new) — offline analysis tool. Reprojects
  the committed NISAR GCOV pre/post HH+HV dB COGs (median-filtered 3x3 per
  `tp.flood_mask`, Δ on native 60 m UTM, bilinear → the OPERA validation
  30 m lattice), the flood/bright/water masks (nearest), and MCD12Q1
  LC_Type1 (`~/.cache/l42-hypothesis/lc_type1_2025.tif`, copied from the
  `g8-wa-dist` worktree's `static/cache/wa/`, h09v04). Groups: forest
  IGBP 1–5, urban 13, open 6–10+12+14+16, excluded 11/15/17/255.
- `data/wa_2025/nisar_hypothesis.json` (new) — verdict + all numbers + a
  plain-language `result_paragraph`.

## Commands run

- `systemd-run --user --scope -p MemoryMax=3G env PYTHONPATH=.:pipeline
  <uv> run python tools/wa_nisar_hypothesis.py` — two full runs (~2 min
  each), exit 0.
- `<uv> run ruff check tools/wa_nisar_hypothesis.py` — clean.

## Results (verdict: supported)

- Missed set = OPERA DSWx-S1 new flood ∩ NOT NISAR candidates = validation
  class 3, n = 60,936 px. Composition: forest 37.1 %, urban 7.7 %,
  open 41.2 %, other 14.1 %. Detected set (n=8,718): open 94.4 %,
  forest 3.3 %, urban 1.9 %. Urban+forest share among misses 44.8 % vs
  5.2 % among detections — **8.56× enriched**.
- Mean ΔHH (Dec 4 → Dec 16): flooded open −2.72 dB (n=31,662), flooded
  urban −0.66 (n=2,348), flooded forest −0.19 (n=16,753); non-flooded
  controls +0.18 urban / +0.40 forest / +0.18 open (n≈1.4–11.1 M).
  Control-adjusted: open −2.90, urban −0.84, forest −0.59 dB.
- Recall: open 24.7 %, urban 3.4 %, forest 1.3 %.
- HV same pattern weaker: flooded open −1.83, urban −0.34, forest −0.16 dB.
- Sanity: rasterized flood mask vs validation pred mask IoU 0.9901.
- **Nuance**: flooded urban/forest do NOT rise +3 dB (≤0.5 % px); they stay
  near their already-bright baseline — double bounce cancels the specular
  darkening. The committed +3 dB `flood_bright` layer recovers only
  7/60,936 misses (0.01 %) — closing the gap needs HV/texture or
  land-cover-aware detection, not a brighter-than-baseline gate.

## Reported to

lead-analysis (t195), lead-submission (t196), conductor (t5w) via hc send.
