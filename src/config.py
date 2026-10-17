"""Runtime configuration, sourced from environment variables.

In a managed deployment there is no MISTRAL_API_KEY by design: the platform
mounts a rotating service-account token (MISTRAL_SA_TOKEN_PATH), and the
Workflows SDK client resolves it automatically (see src/clients/tts.py and
src/clients/transcription.py). The Slack token must be provided as a Mistral
workspace secret bound to the env var SLACK_BOT_TOKEN. No secret is ever read
from anywhere else.

Optional: MISTRAL_USER_API_KEY (workspace secret "mistral_user_api_key").
Service accounts cannot own or use saved voices (audio resources are
user-owned), so TTS with a saved voice_id requires calling the API as a user.
When MISTRAL_USER_API_KEY is bound, the TTS and voices clients authenticate
with that user key (get_mistral_client(api_key=...)); without it, the
service-account client is used and only inline ref_audio cloning works.

User-facing message texts (TRANSCRIPT_HEADER, SPEAK_INITIAL_COMMENT) are also
configurable so deployments can localize them; the defaults are English.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_VOXTRAL_MODEL = "voxtral-mini-latest"
DEFAULT_HTTP_TIMEOUT_SECONDS = 120.0
DEFAULT_TRANSCRIPT_HEADER = ":memo: *Transcript* (auto-generated with Voxtral):"
DEFAULT_SPEAK_INITIAL_COMMENT = ":loud_sound: Voice reply:"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    slack_bot_token: str
    voxtral_model: str = DEFAULT_VOXTRAL_MODEL
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    voxtral_voice_id: str = ""
    mistral_user_api_key: str = ""
    transcript_header: str = DEFAULT_TRANSCRIPT_HEADER
    speak_initial_comment: str = DEFAULT_SPEAK_INITIAL_COMMENT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the environment (injectable for tests)."""
        source = os.environ if env is None else env
        slack_token = source.get("SLACK_BOT_TOKEN", "").strip()
        if not slack_token:
            raise ConfigError(
                "Missing required environment variables: SLACK_BOT_TOKEN."
                " Bind the Slack token as a workspace secret in the Mistral console."
            )
        model = source.get("VOXTRAL_MODEL", "").strip() or DEFAULT_VOXTRAL_MODEL
        voice_id = source.get("VOXTRAL_VOICE_ID", "").strip()
        user_key = source.get("MISTRAL_USER_API_KEY", "").strip()
        transcript_header = (
            source.get("TRANSCRIPT_HEADER", "").strip() or DEFAULT_TRANSCRIPT_HEADER
        )
        speak_initial_comment = (
            source.get("SPEAK_INITIAL_COMMENT", "").strip()
            or DEFAULT_SPEAK_INITIAL_COMMENT
        )
        return cls(
            slack_bot_token=slack_token,
            voxtral_model=model,
            voxtral_voice_id=voice_id,
            mistral_user_api_key=user_key,
            transcript_header=transcript_header,
            speak_initial_comment=speak_initial_comment,
        )
