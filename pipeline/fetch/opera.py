"""DEPRECATED STUB — retired (L10b).

OPERA DSWx is already in the Earth Engine catalog
(OPERA/DSWX/L3_V1/HLS|S1); no fetch or ingest step is needed. The real
fetcher was removed by ruling L10b; this stub remains only so
`python -m pipeline.fetch.opera` exits 0 until live/jobs.py drops the
step (lead-live owns that swap).
"""
import sys

RETIRED_MESSAGE = (
    "retired (L10b): OPERA DSWx is in the EE catalog "
    "(OPERA/DSWX/L3_V1/HLS|S1); no fetch needed"
)


def main(argv=None) -> None:
    print(RETIRED_MESSAGE)
    sys.exit(0)


if __name__ == "__main__":
    main()
