# Imagen CPU. Para GPU, parta de nvidia/cuda:12.x-cudnn-runtime e instale onnxruntime-gpu.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core espeak-ng libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
# Módulos de IA opcionales: descomente tras revisar sus licencias.
# COPY backend/requirements-ai.txt backend/requirements-ai.txt
# RUN pip install --no-cache-dir -r backend/requirements-ai.txt

COPY backend backend
COPY frontend frontend

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app /data
USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/system/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
