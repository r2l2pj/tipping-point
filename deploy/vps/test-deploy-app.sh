#!/usr/bin/env bash
set -euo pipefail
script="$(dirname "$0")/deploy-app.sh"
for bad in invalid 000000000000000000000000000000000000000g; do
  if bash "$script" "$bad" >/dev/null 2>&1; then
    echo "Unexpectedly accepted invalid SHA: $bad" >&2
    exit 1
  fi
done
if bash "$script" >/dev/null 2>&1; then
  echo "Unexpectedly accepted missing SHA" >&2
  exit 1
fi
# A well-formed SHA must still be rejected when not invoked as root.
if [[ "$EUID" -ne 0 ]] && bash "$script" 0000000000000000000000000000000000000000 >/dev/null 2>&1; then
  echo "Unexpectedly allowed an unprivileged deploy" >&2
  exit 1
fi
bash -n "$script"
