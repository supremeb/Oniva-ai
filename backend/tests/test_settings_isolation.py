"""The unit suite must not read backend/.env (a developer's local file would leak into Settings)."""
import os

from app.config import Settings, _ENV_FILE, _resolve_env_file


def test_settings_ignore_the_env_file_under_pytest():
    assert os.environ["ONIVA_ENV_FILE"] == ""
    assert Settings.model_config.get("env_file") is None
    assert Settings().model_config.get("env_file") is None


def test_env_file_resolution_keeps_production_default_and_override(monkeypatch):
    monkeypatch.delenv("ONIVA_ENV_FILE", raising=False)
    assert _resolve_env_file() == str(_ENV_FILE)
    monkeypatch.setenv("ONIVA_ENV_FILE", "/tmp/x.env")
    assert _resolve_env_file() == "/tmp/x.env"
    monkeypatch.setenv("ONIVA_ENV_FILE", "")
    assert _resolve_env_file() is None


def test_litellm_does_not_load_dotenv_into_the_environment():
    assert os.environ["LITELLM_MODE"] == "PRODUCTION"
