FROM vllm/vllm-openai:latest

USER root

# vLLM's OpenAI-compatible audio endpoint requires optional audio dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && python3 -m pip install --no-cache-dir "vllm[audio]" soundfile \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
