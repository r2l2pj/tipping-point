#!/usr/bin/env bash
# Local keepalive of the production app: the same request as
# .github/workflows/app-keepalive.yml (GET /api/health, 30 s limit, two
# retries 10 s apart). A failed request exits 1, so the systemd unit fails and
# its OnFailure notice runs. See docs/DEPLOY-VPS.md, "Running without GitHub
# Actions".
#
# The request leaves this host and comes back through the public name, so it
# also proves DNS, TLS and the Caddy route. It cannot see a host that is down
# or cut off from the internet: use an outside monitor for that.
#
# SAFETY: this host also runs unrelated services of another company ("FE
# services"), currently stopped. This script sends one HTTP GET to the
# tipping-point health URL and nothing else: no docker command, no unit, no
# file, no other service.
set -Eeuo pipefail

APP_HEALTH_URL=${APP_HEALTH_URL:-https://tippingpoint.l17s.dev/api/health}
# Seconds between retries; the tests set it to 0.
KEEPALIVE_RETRY_DELAY=${KEEPALIVE_RETRY_DELAY:-10}

kp_main() {
  if [[ "$APP_HEALTH_URL" != https://* && "${KEEPALIVE_ALLOW_HTTP:-0}" != 1 ]]; then
    echo "keepalive: APP_HEALTH_URL must be an https:// URL" >&2
    exit 2
  fi
  if curl -fsS --max-time 30 --retry 2 --retry-delay "$KEEPALIVE_RETRY_DELAY" \
    -o /dev/null "$APP_HEALTH_URL"; then
    echo "keepalive: ok $APP_HEALTH_URL"
  else
    echo "keepalive: FAILED $APP_HEALTH_URL" >&2
    exit 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  kp_main "$@"
fi
