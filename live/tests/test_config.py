"""live.config derives names from config.yaml and carries no bucket leftovers."""
import yaml

import live.config as cfg


def test_static_domain_follows_config_yaml():
    raw = yaml.safe_load(cfg.CONFIG_PATH.read_text())
    assert cfg.STATIC_DOMAIN == raw["domains"]["now"]["name"]


def test_no_gcs_bucket_constant():
    assert not hasattr(cfg, "GCS_BUCKET")


def test_env_example_lists_current_vars():
    text = (cfg.REPO_ROOT / ".env.example").read_text()
    for var in ("EARTHDATA_TOKEN", "GEE_PROJECT",
                "GOOGLE_APPLICATION_CREDENTIALS", "GCP_SA_KEY"):
        assert var in text, var
    assert "EDL_USER" not in text and "EDL_PASS" not in text
    # L55: no messaging channel, no subscriber DB
    assert "TELEGRAM" not in text and "DATABASE_URL" not in text
    assigned = [line.split("=", 1)[1] for line in text.splitlines()
                if "=" in line and not line.startswith("#")]
    assert all(v == "" for v in assigned)
