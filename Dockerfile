ARG BASE=europe-north1-docker.pkg.dev/cgr-nav/pull-through/nav.no/python:3.12-dev

# ── Stage 1: Build ───────────────────────────────────────────────────────────
# Installs Python packages into an isolated venv using --copies so the
# venv is fully self-contained when copied to the runtime stage.
FROM ${BASE} AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build

USER root
RUN apk add --no-cache \
        ffmpeg \
        libsndfile \
        gcc \
        musl-dev \
        libffi-dev

# Select dependency profile at build time:
#   CPU (default): docker build .
#   GPU:           docker build --build-arg REQUIREMENTS=requirements/worker-gpu.txt --target model-worker .
ARG REQUIREMENTS=requirements/worker-cpu.txt
COPY requirements/ requirements/

RUN python -m venv --copies /app/.venv \
    && /app/.venv/bin/pip install --upgrade pip \
    && /app/.venv/bin/pip install -r ${REQUIREMENTS}

# ── Stage 2: Runtime (API) ───────────────────────────────────────────────────
FROM ${BASE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/app/.cache/huggingface \
    ARBEIDSMAPPE=/tmp/transkribering \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

USER root
RUN apk add --no-cache ffmpeg libsndfile \
    && mkdir -p /app/.cache/huggingface /tmp/transkribering \
    && chown -R nonroot:nonroot /app /tmp/transkribering

COPY --from=builder /app/.venv /app/.venv
COPY --chown=nonroot:nonroot . .

USER nonroot
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "apps.api.app:app", "--host", "0.0.0.0", "--port", "8765"]

# ── Stage 3: Model worker ────────────────────────────────────────────────────
# Separate image target for running the model worker as a standalone service.
# Build: docker build --target model-worker -t ao-ki-transkribering-worker .
FROM runtime AS model-worker
EXPOSE 9000
CMD ["python", "-m", "uvicorn", "apps.model_worker.app:app", "--host", "0.0.0.0", "--port", "9000"]
