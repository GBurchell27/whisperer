"""Configuration for Whisperer, with optional overrides from whisperer.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

CONFIG_FILE = Path("whisperer.toml")
API_KEY_FILE = Path("openai_api_key.txt")
ENV_FILE = Path(".env")


@dataclass(frozen=True)
class WhispererConfig:
    """All tunable settings; every field can be overridden in whisperer.toml."""

    record_key: str = "ctrl_r"
    translate_key: str = "shift_r"
    email_key: str = "e"
    sample_rate: int = 16000
    min_duration_seconds: float = 1.0
    tap_duration_seconds: float = 0.5
    transcription_model: str = "gpt-4o-mini-transcribe"
    translation_model: str = "gpt-5.6-luna"
    rewrite_reasoning_effort: str = "low"
    local_model_size: str = "small.en"
    transcription_prompt: str = (
        "How are you doing today? I'm really looking forward to seeing you again!"
    )
    translation_system_prompt: str = (
        "You translate the input text to Quebec French using 'vous'. "
        "You only output the text and nothing else."
    )
    email_system_prompt: str = (
        "Turn the spoken transcript into a succinct email. "
        "Output only a one-line subject prefixed with 'Subject: ', a blank line, "
        "and a short body of at most a few sentences. "
        "Do not add a greeting, sign-off, or commentary unless the speaker included one. "
        "Preserve the speaker's intent, names, facts, and requests. Be concise."
    )
    use_local_backend: bool = False

    @classmethod
    def load(cls, config_path: Path = CONFIG_FILE) -> WhispererConfig:
        if not config_path.exists():
            return cls()
        with config_path.open("rb") as config_file:
            overrides = tomllib.load(config_file)
        valid_names = {field.name for field in fields(cls)}
        unknown_names = set(overrides) - valid_names
        if unknown_names:
            raise ValueError(
                f"Unknown settings in {config_path}: {', '.join(sorted(unknown_names))}"
            )
        return cls(**overrides)


def resolve_openai_api_key() -> str | None:
    """Look up the key: environment variable, then .env file, then the legacy key file."""
    environment_key = os.environ.get("OPENAI_API_KEY")
    if environment_key:
        return environment_key.strip()
    env_file_key = _read_openai_key_from_env_file(ENV_FILE)
    if env_file_key:
        return env_file_key
    if API_KEY_FILE.exists():
        return API_KEY_FILE.read_text(encoding="utf-8").strip()
    return None


def _read_openai_key_from_env_file(env_path: Path) -> str | None:
    """Minimal .env parser: KEY=VALUE lines, optional quotes, # comments."""
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "OPENAI_API_KEY":
            return value.strip().strip("'\"") or None
    return None
