FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' 'httpx>=0.28,<1'
COPY worker/llama_api.py /app/worker/llama_api.py
COPY k8s/model_security.py /app/k8s/model_security.py
USER 10001:10001
CMD ["python", "-m", "uvicorn", "worker.llama_api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
