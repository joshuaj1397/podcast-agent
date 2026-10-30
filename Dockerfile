# Podcast agent CLI. The model is served by Ollama (see compose.yaml), not baked into this image.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    OLLAMA_API_BASE=http://ollama:11434

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra aws --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra aws

COPY harness ./harness
COPY kb ./kb
COPY data ./data
COPY eval ./eval

RUN useradd --create-home --uid 1000 app && mkdir -p out .cache && chown -R app /app/out /app/.cache /app/eval
USER app

ENTRYPOINT ["podcast-agent"]
CMD ["run", "data/heldout"]
