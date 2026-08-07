FROM europe-north1-docker.pkg.dev/cgr-nav/pull-through/nav.no/python:3.12-dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/app/.cache/huggingface \
    ARBEIDSMAPPE=/tmp/transkribering

WORKDIR /app

USER root

RUN apk add --no-cache \
        ffmpeg \
        libsndfile

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

RUN mkdir -p /app/.cache/huggingface /tmp/transkribering \
    && chown -R nonroot:nonroot /app /tmp/transkribering

COPY --chown=nonroot:nonroot . .

USER nonroot

EXPOSE 8765

ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "apps.api.app:app", "--host", "0.0.0.0", "--port", "8765"]
