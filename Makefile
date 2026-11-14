# Tipping Point — pipeline wiring.
#
#   make all CASE=rs_2024|wa_2025   replay chain, in dependency order
#   make now                        live "modo agora" chain (live/jobs.py now)
#   make test                       pytest (pipeline/gee/live) + ruff
#
# Credential gates. Steps that need Google Earth Engine require GEE_PROJECT
# plus GOOGLE_APPLICATION_CREDENTIALS or GCP_SA_KEY; Earthdata steps require
# EARTHDATA_TOKEN. A step whose gate fails prints
#
#   SKIP <step>: missing <var>
#
# and the chain still exits 0 — `env -i make all` is a valid smoke test.
# Steps whose inputs are absent (because an earlier gated step skipped)
# print the same SKIP line naming the missing file instead of failing.

# Pinned uv (mise install); the /home/*/ wildcard keeps `env -i make` working
# when HOME is unset. Override with `make UV=/path/to/uv`.
UV ?= $(or $(wildcard $(HOME)/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv),\
      $(firstword $(wildcard /home/*/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv)),\
      uv)
NO_UV = ! $(UV) --version >/dev/null 2>&1
# --python 3.12 keeps uv off host Pythons without binary wheels (h5py et al).
PY := $(UV) run --python 3.12 python

CASE ?= rs_2024

EVENT_rs_2024 := rs-2024-05
EVENT_wa_2025 := wa-2025-12
EVENT := $(EVENT_$(CASE))

YMD_rs_2024 := 20240502
YMD_wa_2025 := 20251210
YMD := $(YMD_$(CASE))

REFDATE_rs_2024 := 2024-05-02
REFDATE_wa_2025 := 2025-12-10
REFDATE := $(REFDATE_$(CASE))

RAW_NISAR := data/raw/nisar
RAW_AUX := data/raw/aux
COG := data/$(CASE)/cog

DEM := $(RAW_AUX)/dem.tif
NISAR_PRE := $(RAW_NISAR)/gcov_hh_pre.tif
NISAR_POST := $(RAW_NISAR)/gcov_hh_post.tif
TPI_COG := $(COG)/$(CASE)_tpi_slide_$(YMD).tif
FLOOD_COG := $(COG)/$(CASE)_flood_nisar_$(YMD).tif
WATER_COG := $(COG)/$(CASE)_water_nisar_$(YMD).tif
CATALOG_CSV := data/raw/$(CASE)/landslide_catalog.csv

# Gate expressions, evaluated by the shell at recipe time.
MISSING_GEE = [ -z "$$GEE_PROJECT" ] || { [ -z "$$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -z "$$GCP_SA_KEY" ]; }
MISSING_EDL = [ -z "$$EARTHDATA_TOKEN" ]
GEE_VARS = GEE_PROJECT or GOOGLE_APPLICATION_CREDENTIALS/GCP_SA_KEY

.PHONY: all now test \
        gee-tpi gee-hand gee-impact nisar-fetch terrain flood-mask \
        polygonize validation forecast landslide-hit hls-scar \
        lead-time publish manifest

all: check-case gee-tpi gee-hand gee-impact nisar-fetch terrain flood-mask \
     polygonize validation forecast landslide-hit hls-scar lead-time \
     publish manifest

check-case:
	@if [ -z "$(EVENT)" ]; then \
		echo "unknown CASE '$(CASE)' — use rs_2024 or wa_2025" >&2; exit 2; \
	fi

# --- GEE derived assets (need GEE_PROJECT + SA credential) -----------------

gee-tpi:
	@echo "== $@ (case $(CASE))"
	@if $(MISSING_GEE); then \
		echo "SKIP $@: missing $(GEE_VARS)"; \
	else \
		$(PY) -m gee.tpi --case $(CASE); \
	fi

gee-hand:
	@echo "== $@ (case $(CASE))"
	@if $(MISSING_GEE); then \
		echo "SKIP $@: missing $(GEE_VARS)"; \
	else \
		$(PY) -m gee.hand --case $(CASE); \
	fi

gee-impact:
	@echo "== $@ (case $(CASE))"
	@if $(MISSING_GEE); then \
		echo "SKIP $@: missing $(GEE_VARS)"; \
	else \
		$(PY) -m gee.impact --case $(CASE); \
	fi

# --- NISAR / local raster chain --------------------------------------------

nisar-fetch:
	@echo "== $@ (event $(EVENT))"
	@if $(MISSING_EDL); then \
		echo "SKIP $@: missing EARTHDATA_TOKEN"; \
	else \
		$(PY) pipeline/scripts/01_get_nisar.py --event $(EVENT) --execute; \
	fi

terrain:
	@echo "== $@ (event $(EVENT))"
	@if [ ! -f "$(DEM)" ]; then \
		echo "SKIP $@: missing $(DEM)"; \
	else \
		$(PY) pipeline/scripts/00_terrain.py --dem $(DEM) \
			--out $(RAW_AUX)/slope.tif --event $(EVENT); \
	fi

flood-mask:
	@echo "== $@ (event $(EVENT))"
	@if [ ! -f "$(NISAR_PRE)" ] || [ ! -f "$(NISAR_POST)" ]; then \
		echo "SKIP $@: missing $(NISAR_PRE)/$(NISAR_POST)"; \
	else \
		$(PY) pipeline/scripts/02_flood_mask.py --event $(EVENT) \
			--pre $(NISAR_PRE) --post $(NISAR_POST); \
	fi

polygonize:
	@echo "== $@ (event $(EVENT))"
	@if [ ! -f "$(FLOOD_COG)" ]; then \
		echo "SKIP $@: missing $(FLOOD_COG)"; \
	else \
		$(PY) pipeline/scripts/06_polygonize.py --event $(EVENT) \
			--raster $(FLOOD_COG); \
	fi

validation:
	@echo "== $@ (event $(EVENT))"
	@if $(MISSING_EDL); then \
		echo "SKIP $@-opera-local: missing EARTHDATA_TOKEN"; \
	elif [ ! -f "$(WATER_COG)" ]; then \
		echo "SKIP $@-opera-local: missing $(WATER_COG)"; \
	else \
		$(PY) pipeline/scripts/04_validation.py --event $(EVENT) --auto; \
	fi
	@if $(MISSING_GEE); then \
		echo "SKIP $@-opera-gee: missing $(GEE_VARS)"; \
	else \
		$(PY) -m gee.opera --case $(CASE); \
	fi

forecast:
	@echo "== $@ (event $(EVENT))"
	@if [ ! -f "$(TPI_COG)" ]; then \
		echo "SKIP $@: missing $(TPI_COG)"; \
	else \
		$(PY) pipeline/scripts/07_forecast.py --event $(EVENT) \
			--series $(TPI_COG); \
	fi

landslide-hit:
	@echo "== $@ (event $(EVENT))"
	@if [ ! -f "$(CATALOG_CSV)" ]; then \
		echo "SKIP $@: missing $(CATALOG_CSV)"; \
	elif [ ! -f "$(TPI_COG)" ]; then \
		echo "SKIP $@: missing $(TPI_COG)"; \
	else \
		$(PY) pipeline/scripts/08_landslide_hit.py --event $(EVENT) \
			--points $(CATALOG_CSV) --tpi-classes $(TPI_COG); \
	fi

hls-scar:
	@echo "== $@ (case $(CASE))"
	@if $(MISSING_GEE); then \
		echo "SKIP $@: missing $(GEE_VARS)"; \
	else \
		$(PY) -m gee.hls_scar --case $(CASE); \
	fi

lead-time:
	@echo "== $@ (event $(EVENT))"
	@if $(MISSING_EDL); then \
		echo "SKIP $@: missing EARTHDATA_TOKEN"; \
	elif $(MISSING_GEE); then \
		echo "SKIP $@: missing $(GEE_VARS)"; \
	elif [ ! -f "$(CATALOG_CSV)" ]; then \
		echo "SKIP $@: missing $(CATALOG_CSV)"; \
	else \
		$(PY) -m gee.lead_time --case $(CASE) && \
		$(PY) -m ml.lead_time --replay $(COG)/$(CASE)_lead_time_slide_3h.tif \
			--labels $(CATALOG_CSV) --event $(EVENT); \
	fi

# --- publish (always last) --------------------------------------------------

publish:
	@echo "== $@ (event $(EVENT))"
	@if $(NO_UV); then \
		echo "SKIP $@: missing uv"; \
	else \
		$(PY) tools/publish_case.py --event $(EVENT); \
	fi

manifest:
	@echo "== $@"
	@if $(NO_UV); then \
		echo "SKIP $@: missing uv"; \
	else \
		$(PY) tools/build_manifest.py; \
	fi

# --- live chain --------------------------------------------------------------

now:
	@echo "== now (live/jobs.py now)"
	@if $(MISSING_EDL); then \
		echo "SKIP now: missing EARTHDATA_TOKEN"; \
	elif $(MISSING_GEE); then \
		echo "SKIP now: missing $(GEE_VARS)"; \
	else \
		$(PY) live/jobs.py now; \
	fi

# --- tests -------------------------------------------------------------------

test:
	@if $(NO_UV); then \
		echo "SKIP $@: missing uv"; \
	else \
		$(PY) -m pytest pipeline/tests gee/tests live/tests; rc=$$?; \
		$(UV) run --python 3.12 ruff check .; \
		exit $$rc; \
	fi
