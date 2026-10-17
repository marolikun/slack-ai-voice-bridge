"""Tests for settings validation."""

import pytest

from src.config import (
    DEFAULT_SPEAK_INITIAL_COMMENT,
    DEFAULT_TRANSCRIPT_HEADER,
    ConfigError,
    Settings,
)

_BASE_ENV = {"SLACK_BOT_TOKEN": "t"}


def test_missing_required_env_raises_with_hint() -> None:
    with pytest.raises(ConfigError, match="SLACK_BOT_TOKEN"):
        Settings.from_env(env={})


def test_mistral_api_key_not_required() -> None:
    """Managed deployments authenticate via service-account token, not API key."""
    settings = Settings.from_env(env={"SLACK_BOT_TOKEN": "t"})
    assert settings.slack_bot_token == "t"


def test_from_env_reads_all_values() -> None:
    settings = Settings.from_env(
        env={**_BASE_ENV, "VOXTRAL_MODEL": "custom-model"}
    )
    assert settings.slack_bot_token == "t"
    assert settings.voxtral_model == "custom-model"


def test_voxtral_model_defaults_and_blank_falls_back() -> None:
    assert Settings.from_env(env=dict(_BASE_ENV)).voxtral_model == "voxtral-mini-latest"
    blank = Settings.from_env(env={**_BASE_ENV, "VOXTRAL_MODEL": "  "})
    assert blank.voxtral_model == "voxtral-mini-latest"


def test_voice_id_is_optional_and_trimmed() -> None:
    assert Settings.from_env(env=dict(_BASE_ENV)).voxtral_voice_id == ""
    settings = Settings.from_env(env={**_BASE_ENV, "VOXTRAL_VOICE_ID": "  v-1  "})
    assert settings.voxtral_voice_id == "v-1"


def test_message_texts_default_to_english() -> None:
    settings = Settings.from_env(env=dict(_BASE_ENV))
    assert settings.transcript_header == DEFAULT_TRANSCRIPT_HEADER
    assert settings.speak_initial_comment == DEFAULT_SPEAK_INITIAL_COMMENT


def test_message_texts_can_be_overridden() -> None:
    settings = Settings.from_env(
        env={
            **_BASE_ENV,
            "TRANSCRIPT_HEADER": ":memo: Custom header:",
            "SPEAK_INITIAL_COMMENT": ":loud_sound: Custom reply:",
        }
    )
    assert settings.transcript_header == ":memo: Custom header:"
    assert settings.speak_initial_comment == ":loud_sound: Custom reply:"


def test_blank_message_texts_fall_back_to_defaults() -> None:
    settings = Settings.from_env(
        env={**_BASE_ENV, "TRANSCRIPT_HEADER": "   ", "SPEAK_INITIAL_COMMENT": "  "}
    )
    assert settings.transcript_header == DEFAULT_TRANSCRIPT_HEADER
    assert settings.speak_initial_comment == DEFAULT_SPEAK_INITIAL_COMMENT
