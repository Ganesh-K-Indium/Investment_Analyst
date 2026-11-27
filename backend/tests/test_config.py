"""Deployment configuration (app/core/config.py): sources and precedence, validation, and the defaults it gives the Settings screen."""
import pytest

from app.core import config
from app.services import app_settings
from app.services.app_settings import schema


@pytest.fixture()
def yaml_file(tmp_path, monkeypatch):
    """Point the app at a temporary config.yaml; returns a writer."""
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("APP_CONFIG_FILE", str(path))

    def write(text: str):
        path.write_text(text)
        return path
    write("")
    return write


def test_environment_beats_config_file_beats_default(yaml_file, monkeypatch):
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    yaml_file("db_pool_size: 15\nDB_MAX_OVERFLOW: 7\n")
    cfg = config.get_settings()
    assert cfg.db_pool_size == 15 and cfg.db_max_overflow == 7          # file wins over the default; keys are case-insensitive
    monkeypatch.setenv("DB_POOL_SIZE", "25")
    assert config.get_settings().db_pool_size == 25                    # the environment wins over the file
    assert config.get_settings().db_pool_timeout == 30                 # untouched settings keep their default


def test_an_empty_environment_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "")
    monkeypatch.setenv("APP_ENV", "")
    cfg = config.get_settings()
    assert cfg.db_pool_size == 10 and cfg.app_env == "development"


def test_common_spellings_of_the_environment_name_are_accepted(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    assert config.get_settings().is_production


def test_a_typo_in_the_config_file_is_an_error_not_silently_ignored(yaml_file):
    yaml_file("db_pool_sise: 15\n")
    with pytest.raises(config.ConfigError, match="db_pool_sise"):
        config.get_settings()


def test_an_invalid_value_is_rejected(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "0")
    with pytest.raises(Exception, match="db_pool_size"):
        config.get_settings()


def test_settings_are_reread_when_the_environment_changes(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://a:6379/0")
    assert config.get_settings().redis_url == "redis://a:6379/0"
    monkeypatch.setenv("REDIS_URL", "redis://b:6379/0")
    assert config.get_settings().redis_url == "redis://b:6379/0"


def test_provider_keys_are_found_by_their_variable_name(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-ant ")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    cfg = config.get_settings()
    assert cfg.secret("ANTHROPIC_API_KEY") == "sk-ant"
    assert cfg.secret("GEMINI_API_KEY") == "g-key"                     # Gemini falls back to the Google key


def test_production_refuses_to_start_without_its_secrets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    for name in ("JWT_SECRET_KEY", "APP_SECRET_KEY", "INTEGRATION_SECRET_KEY", "MCP_SHARED_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(config.ConfigError, match="JWT_SECRET_KEY"):
        config.validate_on_startup()


def test_development_only_warns(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert config.validate_on_startup().app_env == "development"


# ── Defaults for the Settings screen ─────────────────────────────────────────

def test_deployment_defaults_sit_between_code_defaults_and_saved_settings(yaml_file, monkeypatch):
    yaml_file("settings_defaults:\n  models:\n    generator_model: gpt-4.1\n  answers:\n    style: concise\n")
    monkeypatch.setenv("SETTINGS__RETRIEVAL__TOP_K", "20")
    monkeypatch.setenv("MAX_PAGES", "300")
    resolved = app_settings.resolve()
    assert resolved["models"]["generator_model"] == "gpt-4.1"          # from the file
    assert resolved["answers"]["style"] == "concise"
    assert resolved["retrieval"]["top_k"] == 20                        # from SETTINGS__ (JSON-parsed)
    assert resolved["documents"]["max_pages"] == 300                   # MAX_PAGES feeds the documents default
    assert resolved["retrieval"]["rerank_top_n"] == schema.SYSTEM_DEFAULTS["retrieval"]["rerank_top_n"]
    user_layer = {"answers": {"style": "deep"}}
    assert app_settings.resolve(user_layer)["answers"]["style"] == "deep"   # what a user saves still wins


def test_the_settings_screen_shows_the_deployment_defaults(monkeypatch):
    monkeypatch.setenv("SETTINGS__ANSWERS__STYLE", "deep")
    ui = schema.ui_schema()
    answers = next(s for s in ui["sections"] if s["id"] == "answers")
    assert next(f for f in answers["fields"] if f["key"] == "style")["default"] == "deep"
    assert ui["defaults"]["answers"]["style"] == "deep"


def test_an_invalid_deployment_default_fails_at_startup(monkeypatch):
    monkeypatch.setenv("SETTINGS__ANSWERS__STYLE", "verbose")
    with pytest.raises(config.ConfigError, match="answers.style"):
        config.validate_on_startup()


def test_a_malformed_settings_variable_name_is_explained(monkeypatch):
    monkeypatch.setenv("SETTINGS__TOP_K", "5")
    with pytest.raises(config.ConfigError, match="SETTINGS__<SECTION>__<KEY>"):
        config.settings_defaults_layer()


def test_the_example_config_file_lists_every_setting():
    """config.example.yaml is generated from app/core/config.py. After adding a setting, regenerate it from backend/:
    python -m app.core.config --example > ../config.example.yaml"""
    on_disk = (config.REPO_ROOT / "config.example.yaml").read_text()
    assert on_disk == config.example_yaml(), "config.example.yaml is out of date; regenerate it (see this test's docstring)"
    example = __import__("yaml").safe_load("\n".join(l[2:] for l in on_disk.splitlines() if l.startswith("# ") and ":" in l and not l.startswith("# ──")))
    assert example is None or isinstance(example, dict)
