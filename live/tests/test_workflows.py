"""Workflow shape tests — every workflow is a thin L8 shell: secrets-only env,
an Earthdata token gate before any real work, one run step calling
`uv run python live/jobs.py <job>`, and a commit step pushing data back."""
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


def test_now_cron_and_commit_paths():
    text, doc = _load("now.yml")
    assert _on(doc)["schedule"] == [{"cron": "0 */3 * * *"}]
    commit = [s for s in _steps(doc) if "git add" in s.get("run", "")]
    assert commit, "now.yml has no git add commit step"
    assert "data/now" in commit[0]["run"]
    assert "EDL_USER" not in text and "EDL_PASS" not in text
