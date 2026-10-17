# Slack AI Voice Bridge

A [Mistral Workflows](https://docs.mistral.ai/studio/workflows/getting-started/overview) project that gives a chat-based AI assistant a voice in Slack — in both directions:

- **`slack-voice-transcribe`** — transcribes a Slack voice message with [Voxtral](https://docs.mistral.ai/) speech-to-text and posts the transcript as a threaded reply under the original message, so the assistant can process voice notes like text.
- **`slack-voice-speak`** — synthesizes text into a voice message with Voxtral TTS and posts it into a channel or thread (optionally in a cloned voice).
- **`list-voices`** — lists the voices visible to the caller (name, id, languages); a diagnostics helper for resolving a voice name to its voice id.

Community project; not affiliated with Mistral AI. Slack is a trademark of Slack Technologies, LLC.

## Flow (transcription)

    Slack voice message (file F...)
        |
        v   workflow call: slack-voice-transcribe
    files.info  -->  metadata (mimetype, download URL)
        |
        v
    download of the audio file (bearer token)
        |
        v
    Voxtral transcription (Workflows SDK client)
        |
        v
    chat.postMessage  -->  ":memo: Transcript ..." as a threaded reply

## Flow (voice reply)

    text (e.g. the assistant's answer)
        |
        v   workflow call: slack-voice-speak
    Voxtral TTS  (saved voice_id or inline ref_audio)
        |
        v
    audio bytes (mp3)
        |
        v
    3-step Slack file upload  -->  voice message in the channel/thread

## Workflow input (JSON)

    {
      "file_id": "F0ABC123",
      "channel_id": "C012345ABCDE",
      "thread_ts": "1234567890.123456"
    }

Return values: `slack-voice-transcribe` returns the transcript text (string); `slack-voice-speak` returns the uploaded Slack file ID.

## Architecture

| Module | Responsibility |
| --- | --- |
| src/workflows/slack_voice_transcribe.py | Transcription workflow: definition and orchestration; the only touchpoint with the workflow decorators |
| src/workflows/speak.py | Voice-reply workflow: text -> Voxtral TTS -> Slack audio upload |
| src/workflows/list_voices.py | Diagnostics: list the voices visible to the caller |
| src/clients/slack.py | Thin async Slack Web API client (files.info, download, chat.postMessage, 3-step audio upload) with its own error class SlackApiError |
| src/clients/transcription.py | Voxtral wrapper: temporary audio file, blocking SDK call in a worker thread, guaranteed cleanup |
| src/clients/tts.py | Voxtral TTS wrapper (saved voice or inline ref_audio) |
| src/config.py | Validated, immutable configuration from environment variables (Settings.from_env) |
| src/entrypoints/worker.py | Worker entrypoint: registers the workflows and starts the worker |

Design principles: small injectable units (HTTP transport and SDK clients are swappable so unit tests run without network), clear error classes, no secrets in code — credentials come exclusively from the environment (secret bindings).

## Configuration

| Env variable | Required | Description |
| --- | --- | --- |
| SLACK_BOT_TOKEN | yes | Token of the Slack app (bot scopes files:read, files:write) — bind as a Mistral workspace secret, never commit |
| MISTRAL_USER_API_KEY | no | User API key; needed to use user-owned saved voices for TTS (service accounts cannot own audio resources) |
| VOXTRAL_MODEL | no | Transcription model, default voxtral-mini-latest |
| VOXTRAL_VOICE_ID | no | Default saved voice for slack-voice-speak |
| TRANSCRIPT_HEADER | no | Header line for transcript replies, default ":memo: *Transcript* (auto-generated with Voxtral):" |
| SPEAK_INITIAL_COMMENT | no | Initial comment shown above uploaded voice messages, default ":loud_sound: Voice reply:" |

Managed deployments do not receive a MISTRAL_API_KEY: the platform mounts a rotating service-account token (MISTRAL_SA_TOKEN_PATH) that the Workflows SDK resolves automatically.

## Tests and local development

Requires Python 3.12+:

    pip install -e ".[dev]"
    make test           # unit tests (network-free: MockTransport + SDK fakes)
    make lint           # ruff
    make start-worker   # start the worker locally (requires SLACK_BOT_TOKEN)

## Deployment (Mistral managed deployment)

One-time setup:

1. Activate pay-as-you-go in your Mistral organization.
2. Install the Mistral GitHub App (https://github.com/apps/mistralai) on this repository and connect GitHub in the Mistral console.
3. Create the workspace secrets (e.g. slack_bot_token, mistral_user_api_key) in the Mistral console and bind them to the deployment's env vars (SLACK_BOT_TOKEN, MISTRAL_USER_API_KEY).

Create the deployment (console -> Deployments or API), e.g. name "slack-ai-voice-bridge", branch "main". New versions: push to main, then redeploy. Build and runtime logs are available in the deployment details.

## Security

- No secrets in the repository; all credentials come from secret bindings.
- The container runs as an unprivileged user.
- Processing voice messages involves personal data of the speakers (audio and transcript). The operator of a deployment is responsible for compliance with applicable data-protection law (e.g. a lawful basis under the GDPR) — keep the target channel private.
- Voice cloning requires the sample speaker's explicit consent (Mistral voice-cloning usage policy). The caller is responsible for ensuring consent before using a sample.

## Third-party dependencies

This repository only contains its own code; the following open-source packages are installed via pip at build time and carry their own licenses:

| Package | Role | License |
| --- | --- | --- |
| httpx | HTTP client for the Slack Web API | BSD-3-Clause |
| mistralai / mistralai_workflows | Mistral Workflows SDK and API client | Apache-2.0 |
| pydantic | Input validation | MIT |
| pytest, pytest-asyncio, ruff (dev only) | Testing and linting | MIT |

The Docker image builds on the official python:3.12-slim base image (Python Software Foundation license).

## Limitations

- Only files with an audio/* mimetype are processed (guard in the workflow).
- Detecting voice messages and calling the workflows is the caller's job (e.g. a scheduled assistant or a Slack event receiver), not this project's.

## Development note

This project was developed with AI assistance (Mistral models) and reviewed by its maintainer.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
