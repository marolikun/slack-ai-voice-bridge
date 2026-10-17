.PHONY: start-worker test lint format

# Start the worker locally. A local run needs MISTRAL_API_KEY (user API key,
# since there is no platform-injected service-account token) and SLACK_BOT_TOKEN
# in the environment; a managed deployment receives a rotating service-account
# token automatically and only requires the SLACK_BOT_TOKEN secret binding.
start-worker:
	python -m src.entrypoints.worker

# Run the unit tests.
test:
	python -m pytest

# Static checks (ruff).
lint:
	python -m ruff check src tests

# Auto-format.
format:
	python -m ruff format src tests
