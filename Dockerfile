# Managed-deployment image for the slack-ai-voice-bridge worker.
# Mistral Cloud builds this file into the worker container.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the project; its runtime dependencies come from pyproject.toml
# (single source of truth - no duplicated dependency list in this image).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run unprivileged for defense in depth.
RUN useradd --create-home --uid 1000 worker && chown -R worker /app
USER worker

CMD ["python", "-m", "src.entrypoints.worker"]
