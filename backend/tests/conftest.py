"""Shared test setup.

Every test runs against the code defaults, not the developer's own deployment configuration: a config.yaml at the repo root
and SETTINGS__* variables would otherwise change what the tests see. Tests that exercise configuration set their own (see
tests/test_config.py). Provider keys from .env stay visible; tests that must not call a model remove them themselves.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_deployment_config(tmp_path_factory, monkeypatch):
    empty = tmp_path_factory.getbasetemp() / "empty-config.yaml"
    if not empty.exists():
        empty.write_text("")
    monkeypatch.setenv("APP_CONFIG_FILE", str(empty))
    for name in [n for n in os.environ if n.startswith("SETTINGS__")]:
        monkeypatch.delenv(name)
