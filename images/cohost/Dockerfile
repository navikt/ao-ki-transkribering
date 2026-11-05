FROM europe-west4-docker.pkg.dev/ao-ki-taskforce-prod-2472/vllm/whisper-diarization@sha256:6379c245040db5f183c7007888863d65e38f814df20dd66298dcf9ae6af7972a AS speech

# Use the same native llama runtime as the existing Borealis deployment. Its
# Ubuntu 24 userspace also runs the older conda Python environment, not vice versa.
FROM ghcr.io/ggml-org/llama.cpp@sha256:b0baccbe2e00b5d74e63f85f9f023c93d61d0f329475269e1e7002e87eb875ea
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home model
COPY --from=speech /opt/conda /opt/conda
ENV PATH="/opt/conda/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 NEMOTRON_ENHET=cuda NEMOTRON_DIAR_MODELL=/models/nemotron-diarization \
    LD_LIBRARY_PATH="/usr/local/nvidia/lib64:/usr/local/cuda/lib64"
WORKDIR /app
COPY shared/ shared/
COPY worker/ worker/
COPY k8s/model_security.py k8s/model_security.py
RUN python -c "import torch, ctranslate2, faster_whisper; from worker.nemotron_api import app" \
    && /app/llama-server --version
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/opt/conda/bin/python"]
CMD ["-m", "uvicorn", "worker.nemotron_api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
