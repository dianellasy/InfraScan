FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg/ffprobe decode dashcam MP4s. The model runs on ONNX Runtime
# (in requirements.txt), so no torch, ultralytics or OpenCV is needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY ./app /app/app
COPY ./inference /app/inference
