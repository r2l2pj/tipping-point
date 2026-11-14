# U26 implementation — S2 + S9

Commit: b79b485 (unit/u26)

## Changes

- live/alerts.py: interpret quiet hours in America/Sao_Paulo using stdlib ZoneInfo; vectorize both D0 and D1 slide/flood assets at existing 0.6/0.4 thresholds and select the worst class at each consenting subscriber location.
- Send changed classes >= ALERT_MIN_CLASS through requests.post to Telegram sendMessage only when TELEGRAM_TOKEN exists and outside Brasília quiet hours. Require HTTP success and Telegram ok=true before stamping last_alert_ts. Include the Defesa Civil footer at the end of every message. Log missing-token skips and delivery failures without exposing the token or raising network failures into the job.
- Refresh subscriber state each pass. Preserve existing last_alert_ts with SQL COALESCE when nothing was sent (SQLite and Neon).
- Lead-approved deferral rule: during quiet hours, preserve last_class for alert-eligible changed classes, while refreshing current_label/data_date. The next daytime pass therefore detects the change and sends if the risk remains. If risk disappears before daytime, no stale alert is sent. Added the requested code comment; no pending/schema column added.
- live/jobs.py: one-line Brasília quiet-hours conversion.
- live/tests/test_alerts.py: regression coverage for UTC/BRT boundaries, D0+D1 worst class, confirmed-send-only timestamps, previous timestamp preservation, missing-token skip, HTTP/API/network failures, consent filtering, footer, quiet-to-daytime delivery and disappearance before daytime. Telegram requests.post is mocked; no real Telegram traffic.
- live/tests/test_jobs.py: lead explicitly approved expanding scope to update D0-only expectations to four assets/eight regions/vectorizations.

## Commands and results

UV executable: $HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv

- Initial inspection: pwd; git status --short; todo log; ls -a; git log -3 --oneline. Clean starting worktree, request logging enabled. Logged request with todo add.
- First `$UV run pytest live/tests -q && $UV run ruff check live/`: 112 passed, 2 failed in test_jobs.py::test_critical_areas_from_assets[False/True], which expected only D0. Stopped and reported scope blocker to t6g; lead authorized test_jobs.py changes and clarified deferral.
- Standalone `$UV run ruff check live/`: initially found BLE001 and PIE807; replaced broad send exception handler with requests.RequestException/ValueError and replaced empty-list lambda with list.
- Final verification on the exact code subsequently committed as b79b485:
  `$UV run pytest live/tests -q && $UV run ruff check live/ && git diff --check`
  Output: `116 passed, 26 warnings in 22.87s`; `All checks passed!`; diff whitespace check exit 0. Warnings are Rasterio georeferencing and affine-multiplication deprecation warnings.
- Reviewed git diff, status and recent log; staged only the four approved files; committed with requested subject and Devin attribution. Commit succeeded: b79b485.

No credentialed Telegram or GEE network run was performed. No schema/dependency changes; no model switches; no helpers.
