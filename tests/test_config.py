"""Tests for config module."""

# pylint: disable=redefined-outer-name

import os
import stat

import pytest

import udemy_sage.config as cfg
from udemy_sage.config import (
    load_config,
    log_error,
    save_config,
    validate_config,
)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfg, "ERROR_LOG", tmp_path / "errors.log")
    return tmp_path


class TestConfig:
    @pytest.mark.usefixtures("tmp_config_dir")
    def test_load_empty(self):
        assert load_config() == {}

    @pytest.mark.usefixtures("tmp_config_dir")
    def test_save_and_load(self):
        data = {"provider": "openai", "vault_path": "/tmp/vault"}
        save_config(data)
        loaded = load_config()
        assert loaded["provider"] == "openai"
        assert loaded["vault_path"] == "/tmp/vault"

    def test_permissions(self, tmp_config_dir):
        save_config({"provider": "openai"})
        config_file = tmp_config_dir / "config.json"
        mode = os.stat(config_file).st_mode
        assert mode & stat.S_IRWXG == 0, "Group should have no access"
        assert mode & stat.S_IRWXO == 0, "Others should have no access"

    def test_config_dir_permissions(self, tmp_config_dir):
        save_config({"provider": "openai"})
        dmode = os.stat(tmp_config_dir).st_mode
        assert dmode & stat.S_IRWXG == 0, (
            "Config dir: group must have no access"
        )
        assert dmode & stat.S_IRWXO == 0, (
            "Config dir: others must have no access"
        )

    def test_log_error(self, tmp_config_dir):
        log_error("section/lesson.md", "API timeout")
        error_log = tmp_config_dir / "errors.log"
        assert error_log.exists()
        content = error_log.read_text()
        assert "section/lesson.md" in content
        assert "API timeout" in content

    def test_log_error_permissions(self, tmp_config_dir):
        log_error("a", "err")
        error_log = tmp_config_dir / "errors.log"
        mode = os.stat(error_log).st_mode
        assert mode & stat.S_IRWXG == 0
        assert mode & stat.S_IRWXO == 0

    def test_load_corrupt_json_renames_and_returns_empty(self, tmp_config_dir):
        corrupt = tmp_config_dir / "config.json"
        corrupt.write_text("{ not json", encoding="utf-8")
        assert load_config() == {}
        backups = list(tmp_config_dir.glob("config.json.corrupt.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{ not json"


class TestValidateConfig:
    def test_valid_openai(self):
        assert not validate_config({
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
            "vault_path": "/tmp/v",
        })

    def test_missing_api_key_for_openai(self):
        errs = validate_config({
            "provider": "openai",
            "model": "x",
            "vault_path": "/v",
            "api_key": "",
        })
        assert any("API key" in e for e in errs)

    def test_ollama_no_api_key_ok(self):
        assert not validate_config({
            "provider": "ollama",
            "model": "llama3",
            "vault_path": "/v",
            "api_key": "",
        })

    def test_missing_model(self):
        errs = validate_config({
            "provider": "openai",
            "model": "  ",
            "api_key": "k",
            "vault_path": "/v",
        })
        assert any("Model" in e for e in errs)

    def test_invalid_ollama_base_url(self):
        errs = validate_config({
            "provider": "ollama",
            "model": "llama3",
            "vault_path": "/v",
            "api_key": "",
            "ollama_base_url": "not-a-url",
        })
        assert any("Ollama base URL" in e for e in errs)

    def test_valid_ollama_base_url(self):
        assert not validate_config({
            "provider": "ollama",
            "model": "llama3",
            "vault_path": "/v",
            "api_key": "",
            "ollama_base_url": "http://192.168.1.5:11434",
        })
