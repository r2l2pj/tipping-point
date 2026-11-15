# REPLAY v2 (L44) — see .herdr/implement-replay-v2.md

Changed: tools/replay_v2.py (new, v2 COG builder), tools/replay_v2_score.py
(new orchestrator -> cases.<c>.replay_v2 only), tools/replay_v2_png.py (new,
doc crops), pipeline/tests/test_replay_v2.py (new, 7 hermetic tests),
docs/replay-v2.md + docs/img/replay_v2_rs_2024_flood_20240502_{v1,v2}.png,
--cog-suffix option added to warned_before / smap_latency_sensitivity /
prediction_lift / rs_ems_validation / rs_ems_landslide / alarm_precision.

Commands: tools/replay_v2.py --case rs_2024 --case wa_2025 (17+22 _v2 COGs,
no skips; domain-mean R v1->v2 +4.0 %; water mask 6.9 %/12.9 % of finite
cells); tools/replay_v2_score.py (all headlines re-scored, only replay_v2
keys added — diff-verified); pytest -q pipeline/tests live/tests gee/tests
-> 912 passed.
