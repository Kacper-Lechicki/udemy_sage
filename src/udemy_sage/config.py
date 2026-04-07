"""Configuration management with secure file permissions."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CONFIG_DIR = Path.home() / ".udemy-sage"
CONFIG_FILE = CONFIG_DIR / "config.json"
ERROR_LOG = CONFIG_DIR / "errors.log"

PROVIDERS = ("openai", "anthropic", "gemini", "openrouter", "ollama")

CONFIGURABLE_FIELDS = (
    "provider",
    "model",
    "api_key",
    "vault_path",
    "cookies_browser",
    "ollama_base_url",
)

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "gemini": "gemini-2.0-flash",
    "openrouter": "openai/gpt-4o-mini",
    "ollama": "llama3.2",
}

# Approximate cost per 1M input tokens (USD)
COST_PER_M_TOKENS: dict[str, float] = {
    "openai": 0.15,
    "anthropic": 0.25,
    "gemini": 0.075,
    "openrouter": 0.15,
    "ollama": 0.0,
}


def _try_chmod(path: Path, mode: int) -> None:
    """chmod path; ignore OSError (e.g. Windows or restricted filesystems)."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_config_dir() -> None:
    """Create config directory if it doesn't exist (owner-only: chmod 700)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _try_chmod(CONFIG_DIR, stat.S_IRWXU)


def _chmod_private(path: Path) -> None:
    """Restrict file to owner read/write (600)."""
    _try_chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_config() -> dict[str, Any]:
    """Load configuration from disk. Returns empty dict if no config exists."""
    if not CONFIG_FILE.exists():
        return {}
    raw = CONFIG_FILE.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        backup = CONFIG_DIR / f"config.json.corrupt.{int(time.time())}"
        try:
            CONFIG_FILE.rename(backup)
        except OSError:
            pass
        return {}


def save_config(config: dict[str, Any]) -> None:
    """Save configuration to disk with chmod 600."""
    ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _chmod_private(CONFIG_FILE)


def is_configured() -> bool:
    """Check if a valid configuration exists."""
    config = load_config()
    return bool(config.get("provider") and config.get("vault_path"))


def validate_config(config: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors, or an empty list if OK."""
    errors: list[str] = []
    provider = str(config.get("provider") or "").strip()
    if not provider:
        errors.append("AI provider is required.")
    elif provider not in PROVIDERS:
        errors.append(f"Unknown provider: {provider!r}.")

    if not str(config.get("model") or "").strip():
        errors.append("Model name is required.")

    if not str(config.get("vault_path") or "").strip():
        errors.append("Obsidian vault path is required.")

    if provider and provider != "ollama":
        if not str(config.get("api_key") or "").strip():
            errors.append("API key is required for this provider.")

    ollama_url = str(config.get("ollama_base_url") or "").strip()
    if ollama_url:
        parsed = urlparse(ollama_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            errors.append(
                "Ollama base URL must be http(s) with a host "
                "(e.g. http://localhost:11434)."
            )

    return errors


def log_error(lesson_id: str, error: str) -> None:
    """Append to the error log. Does not include transcripts or API keys."""
    ensure_config_dir()
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{lesson_id}] {error}\n")
    _chmod_private(ERROR_LOG)
