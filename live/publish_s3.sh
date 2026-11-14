#!/usr/bin/env bash
# Publish data/ to Neon Object Storage (L17) — aws CLI only, no Python dep.
# Env: NEON_S3_ENDPOINT NEON_S3_ACCESS_KEY_ID NEON_S3_SECRET_ACCESS_KEY
#      NEON_S3_REGION NEON_S3_BUCKET (bucket default tipping-point-data)
# Order: dated frames first (long cache), then metrics/summary/alerts
# (short cache), data/manifest.json always LAST.
# JSON/geojson go up pre-compressed: Content-Encoding gzip +
# Content-Type application/json, verified served correctly by Neon
# (wire = gz bytes, browsers decode transparently).
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
# JSON/geojson are uploaded individually below (gzipped)
EXCLUDES+=(--exclude "*.json" --exclude "*.geojson")

echo "[publish_s3] frames -> $DEST"
aws s3 sync "${EXCLUDES[@]}" --cache-control "public,max-age=86400" \
  "$DATA/" "$DEST" "${ENDPOINT[@]}"

# short-cache control files: manifest.json always last
is_short() {
  case "/$1" in
    */manifest.json|*/metrics.json|*/summary.json|*/alerts.json) return 0 ;;
  esac
  return 1
}

upload_json() {  # $1 = path relative to data/, $2 = cache-control
  local gz
  [ -f "$DATA/$1" ] || { echo "[publish_s3] vanished mid-run: $1"; return 0; }
  gz="$(mktemp --suffix=.gz)"
  gzip -9 -c "$DATA/$1" > "$gz"
  aws s3 cp "$gz" "$DEST$1" "${ENDPOINT[@]}" \
    --content-type application/json --content-encoding gzip \
    --cache-control "$2"
  rm -f "$gz"
}

echo "[publish_s3] json/geojson (gzipped) -> $DEST"
mapfile -t JSONS < <(find "$DATA" -type f \( -name '*.json' -o -name '*.geojson' \) -printf '%P\n')
for f in "${JSONS[@]}"; do
  case "$f" in raw/*|*/raw/*|manifest.json) continue ;; esac
  skip=0
  for b in "${BIG[@]}"; do [ "$f" = "$b" ] && { skip=1; break; }; done
  [ "$skip" = 1 ] && { echo "[publish_s3] skipping oversized $f"; continue; }
  if is_short "$f"; then CC="public,max-age=60"; else CC="public,max-age=86400"; fi
  upload_json "$f" "$CC"
done

if [ -f "$DATA/manifest.json" ]; then
  upload_json "manifest.json" "public,max-age=60"
  echo "[publish_s3] manifest.json uploaded last"
fi
echo "[publish_s3] done"
