FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements/api.txt requirements/nordlyd-runtime.txt requirements/
RUN python -m pip install --no-cache-dir -r requirements/nordlyd-runtime.txt
COPY shared shared
COPY worker worker
COPY k8s/model_security.py k8s/model_security.py
ENV HF_HUB_OFFLINE=1 SPEECH_ASR_BACKEND=nordlyd \
    NORDLYD_MODELL=/models/nordlyd NORDLYD_ALIGNER_MODELL=/models/nordlyd-aligner
USER 10001
CMD ["python", "-m", "uvicorn", "worker.nemotron_api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
