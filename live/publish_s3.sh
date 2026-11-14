#!/usr/bin/env bash
# Publish data/ to Neon Object Storage (L17) — aws CLI only, no Python dep.
# Env: NEON_S3_ENDPOINT NEON_S3_ACCESS_KEY_ID NEON_S3_SECRET_ACCESS_KEY
#      NEON_S3_REGION NEON_S3_BUCKET (bucket default tipping-point-data)
# Order: dated frames first (long cache), then metrics/summary/alerts
# (short cache), data/manifest.json always LAST.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data"
BUCKET="${NEON_S3_BUCKET:-tipping-point-data}"
MAX_MB="${PUBLISH_MAX_MB:-64}"

for var in NEON_S3_ENDPOINT NEON_S3_ACCESS_KEY_ID NEON_S3_SECRET_ACCESS_KEY NEON_S3_REGION; do
  if [ -z "${!var:-}" ]; then
    echo "[publish_s3] $var unset — skipping storage publish" >&2
    exit 0
  fi
done
[ -d "$DATA" ] || { echo "[publish_s3] data/ missing — nothing to publish" >&2; exit 0; }
command -v aws >/dev/null || { echo "[publish_s3] aws CLI not found — skipping" >&2; exit 0; }

export AWS_ACCESS_KEY_ID="$NEON_S3_ACCESS_KEY_ID"
export AWS_SECRET_ACCESS_KEY="$NEON_S3_SECRET_ACCESS_KEY"
export AWS_DEFAULT_REGION="$NEON_S3_REGION"
export AWS_EC2_METADATA_DISABLED=true
ENDPOINT=(--endpoint-url "$NEON_S3_ENDPOINT")
DEST="s3://$BUCKET/data/"

# oversized files stay out of the free-tier bucket (same cap as the
# GitHub fallback publisher)
mapfile -t BIG < <(find "$DATA" -type f -size +"${MAX_MB}"M -printf '%P\n')
# raw/ holds fetch intermediates, not app-facing products
EXCLUDES=(--exclude "raw/*")
for f in "${BIG[@]}"; do
  echo "[publish_s3] skipping oversized $f"
  EXCLUDES+=(--exclude "$f")
done

# short-cache control files: upload after frames, manifest.json last
SHORT=(metrics.json now/manifest.json now/summary.json now/alerts.json)
for f in "${SHORT[@]}"; do
  EXCLUDES+=(--exclude "$f")
done
EXCLUDES+=(--exclude "manifest.json")

echo "[publish_s3] frames -> $DEST"
aws s3 sync "${EXCLUDES[@]}" --cache-control "public,max-age=86400" \
  "$DATA/" "$DEST" "${ENDPOINT[@]}"

for f in "${SHORT[@]}"; do
  if [ -f "$DATA/$f" ]; then
    aws s3 cp --cache-control "public,max-age=60" \
      "$DATA/$f" "$DEST$f" "${ENDPOINT[@]}"
  fi
done

if [ -f "$DATA/manifest.json" ]; then
  aws s3 cp --cache-control "public,max-age=60" \
    "$DATA/manifest.json" "${DEST}manifest.json" "${ENDPOINT[@]}"
  echo "[publish_s3] manifest.json uploaded last"
fi
echo "[publish_s3] done"
