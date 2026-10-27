# Phase 1 image: hub or node on x86 Linux. No GPU and no AMADEUS.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/prawn-monitor
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir -e ".[usb]"

ENV PYTHONUNBUFFERED=1
EXPOSE 8080 8090
