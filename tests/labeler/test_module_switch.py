"""
tests/labeler/test_module_switch.py — PR-B MODULE env switch tests.

Covers:
  - MODULE unset → app.config["MODULE"] == "primera"
  - MODULE=segunda → app.config["MODULE"] == "segunda"
  - MODULE=invalid → raises RuntimeError at create_app time (per spec MS-3)
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

    def test_invalid_module_aborts_startup(self, tmp_path, base_env, monkeypatch):
        """An invalid MODULE value must raise RuntimeError at create_app time."""
        monkeypatch.setenv("MODULE", "invalid")
        with patch.dict(os.environ, base_env):
            with pytest.raises(RuntimeError, match="Invalid MODULE"):
                _create_app(tmp_path)

    def test_module_segunda_uses_segunda_labels_dir(
        self, tmp_path, base_env, monkeypatch
    ):
        """MODULE=segunda with no LABELS_DIR env must resolve to labels_segunda."""
        monkeypatch.setenv("MODULE", "segunda")
        monkeypatch.delenv("LABELS_DIR", raising=False)
        with patch.dict(os.environ, base_env):
            app = _create_app(tmp_path)
        assert app.config["MODULE"] == "segunda"
        assert app.config["LABELS_DIR"].endswith("labels_segunda")

    def test_labels_dir_env_overrides_module(self, tmp_path, base_env, monkeypatch):
        """An explicit LABELS_DIR env must take precedence over MODULE=segunda."""
        custom = str(tmp_path / "custom_labels")
        monkeypatch.setenv("MODULE", "segunda")
        monkeypatch.setenv("LABELS_DIR", custom)
        with patch.dict(os.environ, base_env):
            app = _create_app(tmp_path)
        assert app.config["LABELS_DIR"] == str(Path(custom).resolve())
