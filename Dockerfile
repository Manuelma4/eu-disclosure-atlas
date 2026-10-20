# syntax=docker/dockerfile:1.10
# One image for the Python services (query-api, ingestion, extraction): same code, different commands.
#   query-api:  disclosure-atlas --lake /data/lake serve --host 0.0.0.0 --port 8000
#   ingestion:  disclosure-atlas --lake /data/lake watch se_fi_pdmr
# Dependencies are pinned by uv.lock; the final stage has no build tools and runs as a non-root user.

ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:0.9-python${PYTHON_VERSION}-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so code changes reuse the cached layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --extra api --extra orchestration --extra otel --extra pdf --extra shorts --extra llm --extra ai --no-install-project
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra api --extra orchestration --extra otel --extra pdf --extra shorts --extra llm --extra ai --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
LABEL org.opencontainers.image.source="https://github.com/Manuelma4/eu-disclosure-atlas" \
      org.opencontainers.image.description="EU Disclosure Atlas Python services (query-api, ingestion, extraction)" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN groupadd --system --gid 10001 atlas \
 && useradd --system --uid 10001 --gid atlas --home-dir /home/atlas --create-home atlas \
 && mkdir -p /data/lake && chown -R 10001:10001 /data
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/home/atlas
WORKDIR /data
USER 10001:10001
# DuckDB extensions used by the lake (src/disclosure_atlas/lake.py), installed at build time: pods run with a
# read-only root filesystem and no internet egress, so INSTALL at runtime must find them already in place.
RUN python -c "import duckdb; con = duckdb.connect(); [con.install_extension(e) for e in ('ducklake', 'postgres', 'sqlite', 'httpfs')]"
EXPOSE 8000
ENTRYPOINT ["disclosure-atlas", "--lake", "/data/lake"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
