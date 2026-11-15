"""Workflow shape tests — every workflow is a thin L8 shell: secrets-only env,
an Earthdata token gate before any real work, one run step calling
`uv run python live/jobs.py <job>`, and fallback-only publishing for data jobs."""
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# file -> (cron, job name passed to live/jobs.py)
SPECS = {
    "now.yml": ("17 */3 * * *", "now"),
    "nisar.yml": ("30 6 * * *", "nisar"),
    "validation.yml": ("0 7 * * *", "validation"),
    "reference.yml": ("0 8 * * 1", "reference"),
}

PARAMS = [(name, cron, job) for name, (cron, job) in SPECS.items()]


def _load(name):
    text = (WORKFLOWS / name).read_text()
    return text, yaml.safe_load(text)


def _on(doc):
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return doc.get("on", doc.get(True))


def _steps(doc):
    return [s for job in doc["jobs"].values() for s in job["steps"]]


def _run_idx(steps, needle):
    return next(i for i, s in enumerate(steps) if needle in s.get("run", ""))


@pytest.mark.parametrize("name,cron,job", PARAMS)
def test_parses_and_triggers(name, cron, job):
    _, doc = _load(name)
    assert isinstance(doc, dict)
    on = _on(doc)
    assert {"cron": cron} in on["schedule"]
    assert "workflow_dispatch" in on


@pytest.mark.parametrize("name,cron,job", PARAMS)
def test_token_gate_before_job(name, cron, job):
    _, doc = _load(name)
    steps = _steps(doc)
    gate = _run_idx(steps, "check_earthdata_token")
    run = _run_idx(steps, f"live/jobs.py {job}")
    assert gate < run


@pytest.mark.parametrize("name,cron,job", PARAMS)
def test_run_step_env_and_no_edl(name, cron, job):
    text, doc = _load(name)
    steps = _steps(doc)
    run_step = steps[_run_idx(steps, f"live/jobs.py {job}")]
    env = run_step.get("env", {})
    assert "EARTHDATA_TOKEN" in env
    assert "EDL_USER" not in text
    assert "EDL_PASS" not in text


@pytest.mark.parametrize("name,cron,job", PARAMS)
def test_concurrency_group(name, cron, job):
    _, doc = _load(name)
    assert doc["concurrency"]["group"] == job


@pytest.mark.parametrize("name,cron,job", PARAMS)
def test_fallback_publish_and_permissions(name, cron, job):
    text, doc = _load(name)
    steps = _steps(doc)
    assert "NOW_GCS_BUCKET" not in text
    assert doc["permissions"]["contents"] == "write"
    assert "git add" not in text
    assert "git push" not in text
    publish = [s for s in steps if "publish_fallback.py" in s.get("run", "")]
    if job == "reference":
        assert not publish
    else:
        assert len(publish) == 1
        assert publish[0]["run"] == "uv run python live/publish_fallback.py"
        assert _run_idx(steps, f"live/jobs.py {job}") < _run_idx(steps, "publish_fallback.py")
        if job == "now":
            assert "if" not in publish[0]
        else:
            # Only publish when the job actually wrote data/ artifacts (S1).
            assert publish[0].get("if") == "steps.data_changed.outputs.changed == 'true'"
            detect = [s for s in steps if s.get("id") == "data_changed"]
            assert len(detect) == 1
            assert "git status --porcelain -- data/" in detect[0]["run"]
    # L26: for the now job the GitHub fallback branch is the primary
    # publish (before Neon); Neon is best-effort secondary elsewhere.
    neon = [s for s in steps if "publish_s3.sh" in s.get("run", "")]
    assert len(neon) == 1
    assert _run_idx(steps, f"live/jobs.py {job}") < _run_idx(steps, "publish_s3.sh")
    if publish:
        if job == "now":
            assert _run_idx(steps, "publish_fallback.py") < _run_idx(steps, "publish_s3.sh")
            assert neon[0].get("continue-on-error") is True
        else:
            assert _run_idx(steps, "publish_s3.sh") < _run_idx(steps, "publish_fallback.py")


def test_now_cron():
    _, doc = _load("now.yml")
    assert _on(doc)["schedule"] == [{"cron": "17 */3 * * *"}]


def test_now_header_comment_matches_cron():
    text, _ = _load("now.yml")
    header = text.split("on:", 1)[0]
    assert "every 3 h" in header and "every 6 h" not in header


def test_now_caches_climatology_before_run():
    """L30-clim: actions/cache keeps clim_imerg_0p1.tif across runs and
    is restored before the run step that reads it."""
    _, doc = _load("now.yml")
    steps = _steps(doc)
    cache = next(i for i, s in enumerate(steps)
                 if s.get("uses", "").startswith("actions/cache@v4")
                 and "clim_imerg_0p1" in s["with"]["path"])
    assert steps[cache]["with"]["path"] == \
        "data/static/south_brazil/clim/clim_imerg_0p1.tif"
    assert steps[cache]["with"]["key"].startswith("clim-imerg-0p1-")
    assert cache < _run_idx(steps, "live/jobs.py now")


def test_now_notify_step_after_fallback_publish():
    """G1+G2: subscriber alerts run on the just-published bundle, after the
    fallback branch publish; always exit-0, so continue-on-error anyway."""
    _, doc = _load("now.yml")
    steps = _steps(doc)
    notify = [s for s in steps if "live.notify" in s.get("run", "")]
    assert len(notify) == 1
    step = notify[0]
    assert step["run"] == "uv run python -m live.notify"
    assert step.get("continue-on-error") is True
    env = step.get("env", {})
    assert env["TELEGRAM_TOKEN"] == "${{ secrets.TELEGRAM_TOKEN }}"
    assert env["DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"
    i_notify = steps.index(step)
    assert _run_idx(steps, "publish_fallback.py") < i_notify
    assert i_notify < _run_idx(steps, "publish_s3.sh")


def test_now_watchdog_dispatches_stale_now_runs():
    _text, doc = _load("now-watchdog.yml")
    on = _on(doc)
    crons = [c["cron"] for c in on["schedule"]]
    assert len(crons) >= 2 and all(not c.startswith("0 ") and not c.startswith("17 ")
                                   for c in crons)          # other minutes than now.yml
    assert "workflow_dispatch" in on
    assert doc["permissions"] == {"actions": "write", "contents": "read"}
    steps = doc["jobs"]["watchdog"]["steps"]
    dispatch = next(s for s in steps if "gh workflow run now.yml" in s.get("run", ""))
    run = dispatch["run"]
    assert "--status success" in run and "12600" in str(dispatch["env"])
    assert "queued" in run and "in_progress" in run           # never stacks runs


def test_now_watchdog_stale_alarm_after_dispatch():
    """L13b: a final step reads the PUBLISHED Now manifest off the fallback
    branch and fails the run (owner email) when updated_at is > 6 h old —
    and it must run after the dispatch step so a dispatch still happens."""
    _text, doc = _load("now-watchdog.yml")
    steps = doc["jobs"]["watchdog"]["steps"]
    dispatch = next(i for i, s in enumerate(steps)
                    if "gh workflow run now.yml" in s.get("run", ""))
    stale = next(i for i, s in enumerate(steps)
                 if "live.staleness" in s.get("run", ""))
    assert stale > dispatch and stale == len(steps) - 1
    run = steps[stale]["run"]
    assert "git fetch" in run and "FETCH_HEAD:data/now/manifest.json" in run
    assert "21600" in str(steps[stale].get("env", {}))
    assert _on(doc)["workflow_dispatch"]["inputs"]["stale_after_s"]["default"] == "21600"


def test_now_publish_has_drop_guard_allowlist_only_for_r2():
    import yaml
    from pathlib import Path
    wf = yaml.safe_load((Path(__file__).resolve().parents[2]
                         / ".github/workflows/now.yml").read_text())
    step = next(s for s in wf["jobs"][next(iter(wf["jobs"]))]["steps"]
                if s.get("name") == "Publish data/ to fallback branch")
    assert step["env"]["FALLBACK_ALLOW_DROP"].split() == [
        "data/now/response/flood_nisar.geojson"]
    assert "continue-on-error" not in step       # a refused publish fails the run
