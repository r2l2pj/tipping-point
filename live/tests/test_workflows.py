"""Workflow shape tests — every workflow is a thin L8 shell: secrets-only env,
an Earthdata token gate before any real work, one run step calling
`uv run python live/jobs.py <job>`, and fallback-only publishing for data jobs."""
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# file -> (cron, job name passed to live/jobs.py)
SPECS = {
    "now.yml": ("0 */3 * * *", "now"),
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


def test_now_cron():
    _, doc = _load("now.yml")
    assert _on(doc)["schedule"] == [{"cron": "0 */3 * * *"}]


def test_now_header_comment_matches_cron():
    text, _ = _load("now.yml")
    header = text.split("on:", 1)[0]
    assert "every 3 h" in header and "every 6 h" not in header
