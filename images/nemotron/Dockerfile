FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 \
    NEMOTRON_ENHET=cuda NEMOTRON_MODELL=/models/nemotron-3.5-asr-streaming-0.6b
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements/benchmark-speech-runtime.txt /tmp/speech-runtime.txt
RUN pip install --no-cache-dir -r /tmp/speech-runtime.txt \
    'fastapi>=0.115' 'uvicorn>=0.30' 'python-multipart>=0.0.9' 'librosa>=0.11,<0.12' \
    && python -c "from transformers import AutoModelForRNNT, AutoProcessor"
COPY shared/ shared/
COPY worker/ worker/
COPY k8s/model_security.py k8s/model_security.py
RUN useradd --uid 10001 --create-home model
USER model
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "worker.nemotron_api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
