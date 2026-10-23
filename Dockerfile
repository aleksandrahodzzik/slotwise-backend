FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 slotwise
WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
COPY web ./web

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    WEB_DIR="/app/web" \
    WEB_CONCURRENCY=4

USER slotwise
EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "slotwise.entrypoint"]
