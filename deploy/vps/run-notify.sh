#!/usr/bin/env bash
set -euo pipefail

inbox=/var/lib/tipping-point-notify/inbox
input="$inbox/data/now"
if [[ -L "$inbox" || -L "$inbox/data" || -L "$input" ]]; then
  echo "Notifier input path must not contain symlinked directories." >&2
  exit 1
fi
test -s "$input/manifest.json" || { echo "Notifier manifest is missing." >&2; exit 1; }
if find "$input" -type l -print -quit | grep -q .; then
  echo "Notifier input must not contain symlinks." >&2
  exit 1
fi

exec docker run --rm --read-only --tmpfs /tmp --user 1000:1000 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --env-file /etc/tipping-point/notifier.env \
  --env DATABASE_URL=sqlite:/app/.data/tipping-point.sqlite \
  --mount source=tipping-point-data,target=/app/.data \
  --mount type=bind,src=/var/lib/tipping-point-notify,dst=/input,readonly \
  tipping-point-notifier:stable -m live.notify --out-dir /input/inbox/data/now
