"""
tests/labeler/test_module_switch.py — PR-B MODULE env switch tests.

Covers:
  - MODULE unset → app.config["MODULE"] == "primera"
  - MODULE=segunda → app.config["MODULE"] == "segunda"
  - MODULE=invalid → falls back to "primera"
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def base_env(tmp_path):
    """Minimal env required to boot create_app in production mode."""
    return {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-key-for-module-switch",
    }


def _create_app(tmp_path: Path):
    from src.modules.labeler.server import create_app
    index_path = tmp_path / "crops" / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(index_path=index_path, labels_dir=tmp_path)
    app.config["TESTING"] = True
    return app


class TestModuleSwitch:
    def test_module_defaults_to_primera_when_unset(self, tmp_path, base_env, monkeypatch):
        """With MODULE unset, create_app must default to primera."""
        monkeypatch.delenv("MODULE", raising=False)
        with patch.dict(os.environ, base_env):
            app = _create_app(tmp_path)
        assert app.config["MODULE"] == "primera"

    def test_module_segunda_when_env_set(self, tmp_path, base_env, monkeypatch):
        """MODULE=segunda must be reflected in app.config."""
        monkeypatch.setenv("MODULE", "segunda")
        with patch.dict(os.environ, base_env):
            app = _create_app(tmp_path)
        assert app.config["MODULE"] == "segunda"

    def test_invalid_module_falls_back_to_primera(self, tmp_path, base_env, monkeypatch):
        """An invalid MODULE value must fall back to primera."""
        monkeypatch.setenv("MODULE", "invalid")
        with patch.dict(os.environ, base_env):
            app = _create_app(tmp_path)
        assert app.config["MODULE"] == "primera"
