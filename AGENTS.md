# AGENTS.md — working agreements for this repository

Read this before changing or deploying anything. It encodes hard-won operational
knowledge from running this project on Mistral Workflows. Keep it generic:
this repository is public — never add secrets, personal names, workspace IDs,
deployment names, channel IDs, or any concrete configuration values here.

## What this project is

Slack AI Voice Bridge — a [Mistral Workflows](https://docs.mistral.ai) project that
gives a Slack-based assistant a voice in both directions:

- `slack-voice-transcribe` — Slack voice message → Voxtral STT → transcript as threaded reply
- `slack-voice-speak` — text → Voxtral TTS → voice message uploaded to the channel/thread
- `list-voices` — diagnostics: resolve a voice name to its `voice_id`

See [README.md](README.md) for flows, config table, deployment and security notes.

## Layout

| Module | Responsibility |
| --- | --- |
| `src/workflows/` | Workflow definitions; the only touchpoint with the SDK decorators. httpx-based client imports must stay **inside activity bodies** (sandbox import rule — module-level network imports crash worker validation) |
| `src/clients/slack.py` | Thin async Slack Web API client (`SlackApiError`) |
| `src/clients/transcription.py`, `src/clients/tts.py` | Voxtral wrappers around `get_mistral_client()` from the Workflows SDK; injectable `client_factory` so unit tests need no network |
| `src/config.py` | Immutable `Settings.from_env()`; all runtime config from env vars |
| `src/entrypoints/worker.py` | `WORKFLOW_CLASSES` registry + `run_worker(WORKFLOW_CLASSES)` — pass classes, never module strings |

Python 3.12+. `make test` (network-free), `make lint` (ruff), `make start-worker`
(local worker, needs `SLACK_BOT_TOKEN`). Dependencies live in `pyproject.toml`:
`mistralai_workflows==3.16.0` pins `mistralai <3.0` — **never add a direct `mistralai` pin**,
it breaks the image build.

## Auth model (managed deployments)

- Managed deployments inject **no** `MISTRAL_API_KEY`; the platform mounts a rotating
  service-account token (`MISTRAL_SA_TOKEN_PATH`) that the Workflows SDK resolves.
  Use `get_mistral_client()` **inside activities only**.
- A service account cannot own user-owned resources (saved voices, audio). For those,
  bind the optional user key (`MISTRAL_USER_API_KEY`) and call
  `get_mistral_client(api_key=...)`; keep the inline `ref_audio` cloning fallback.
- Slack token comes exclusively from a workspace secret bound to `SLACK_BOT_TOKEN`.

## Model config and Voxtral generations

- STT model is env-driven: `VOXTRAL_MODEL` (code default `voxtral-mini-latest`,
  which Mistral marks **deprecated**). Voxtral 3 id: `voxtral-mini-transcribe-3`
  (alias of `voxtral-mini-transcribe-3-0`); larger: `voxtral-small-transcribe-3`.
- TTS model is env-driven: `VOXTRAL_TTS_MODEL` (code default
  `voxtral-mini-tts-2603`, Voxtral 2). Voxtral 3 id: `voxtral-mini-tts-3`
  (alias of `voxtral-mini-tts-3-0`).
- Migration notes (docs page "Migrate from Voxtral 2"): word timestamps are gone —
  `timestamp_granularities` accepts `segment` only; diarized segments may overlap;
  TTS `ref_audio` and Voxtral 2 preset voices still work; emotion via inline tags.
- Before any step touching an external service, check the **current** official docs
  (`search_mistral_docs` / `read_mistral_docs_page` MCP tools), including
  deprecation and migration pages. Training knowledge is not evidence.

## Deployment rules (Mistral managed workflows)

- Workspace secrets are created by a human in the console Secrets Manager
  (lowercase names, e.g. `slack_bot_token`), in the **same workspace as the deployment**.
  There is no MCP tool to create or list secrets — console only.
- Bind each secret explicitly: `secret_name` (exact Secrets Manager name) +
  `env_var_name` (container env var, upper snake case). A `secrets` list passed to the
  deploy API **replaces all bindings** — read the current list from
  `get_workflow_deployment` first and pass the full set, or omit it to keep.
- Known platform quirk (observed 2026-11): passing an explicit `secrets` list to the
  deploy API can fail with HTTP 502 "Could not verify secret references" even with
  valid names; older behavior was a silently hanging build. Workaround: deploy with
  `secrets` omitted (bindings are kept), add/change bindings via the deployment's
  console **Settings tab**, then `stop` + `start` the deployment — the worker reads
  env vars at boot. Verify afterwards that the binding is present.
- Re-tested 2026-11-03 on a fresh staging deployment: explicit lists with 5 and 6
  entries (all valid secret names, each with `env_var_name` mapping — the MCP deploy
  tool's schema supports the mapping) both fail with the same 502; a deploy with
  `secrets` omitted succeeds. Conclusion: the tool passes the mapping through, the
  server-side reference verification is what currently fails — use the console
  Settings tab for binding changes until the platform fixes it, and re-test the API
  path occasionally.
- Verify after **every** deploy: (a) all expected bindings present with correct
  `secret_name` + `env_var_name`, (b) `deployed_revision` equals the intended commit
  SHA, (c) a **fresh worker boot** in runtime logs ("Starting worker" + "Instance is
  healthy" with a recent timestamp). `ready=true` can precede the actual worker boot
  by ~2 minutes — a test started too early runs on the old code.
- Pushes do **not** auto-redeploy. Always deploy explicitly with the pushed commit
  SHA as `revision`, then `wait_workflow_deployment_ready`.
- Build logs arrive batched at the **end** of the build; ~20–25 min of silence is not
  proof of a hang. Runtime logs: check for a fresh boot before testing.
- `stop_workflow_deployment` keeps source and secrets; a stopped deployment takes no
  rollout — `start` it again or create a fresh deployment reusing the bindings.
- Model-switch runbook (STT or TTS): create the workspace secret (lowercase name,
  e.g. `voxtral_tts_model`) in the Secrets Manager -> add the binding on the
  deployment's console Settings tab (env var upper snake case, reference
  `secret:workspace:<name>`) -> `stop` + `start` the deployment (the worker reads
  env at boot) -> verify the binding and a fresh worker boot -> run the E2E canary
  and confirm the model in the runtime logs (`Transcribing ... with model ...` /
  `Synthesizing ... (model=...)`). Rollback = remove the binding + stop/start.

## Useful MCP tools (mistralai server)

`whoami` (confirm org/workspace context), `list_workflow_registrations`,
`list_workflows`, `get_workflow_deployment`, `deploy_workflow_deployment`,
`wait_workflow_deployment_ready`, `get_workflow_deployment_logs` (`build`|`runtime`),
`execute_workflow`, `get_workflow_execution`, `list_executions`,
`stop_workflow_deployment`, `start_workflow_deployment`,
`search_mistral_docs` / `read_mistral_docs_page`. The Secrets capability tools
(list/read/restore secrets) appear only for keys/workspaces where the feature is
enabled — if absent, use the console.

## Verify before calling anything "live"

1. `deployed_revision` == pushed commit SHA.
2. Fresh worker boot visible in runtime logs (recent timestamp).
3. All bindings correct (see above).
4. End-to-end execution with **real** data: `execute_workflow` → poll
   `get_workflow_execution` → inspect the actual side effect (e.g. the Slack thread
   reply) and the runtime logs (e.g. the `Transcribing … with model …` line).
5. On failure, keep the evidence chain (deployment name, service id, revision,
   timestamps, execution ids, log excerpts) — it is what a support ticket needs.
6. Only after the E2E passes, report the change as live. Clean up stale deployments
   afterwards (mind org limits).

## Repo hygiene (public repository)

- No secrets or secret values in git, code, tests, or commit metadata — values are
  write-only in the Secrets Manager.
- No personal names or personal data in code, comments, docs, or commits; keep names
  generic and vendor-neutral. English, generic documentation; Apache-2.0 license;
  dependency license table and third-party trademark disclaimer in README; the
  AI-assistance note stays; keep the security/privacy notes for voice processing
  (GDPR, voice-cloning consent).
- Never rewrite or scrub git history. Clean authorship from the start is cheaper.
- Git workflow: focused feature branches `vibe/<slug>`, one logical change per
  commit, draft PR against `main`, run `make test` and `make lint` first. Do not
  touch unrelated files.
