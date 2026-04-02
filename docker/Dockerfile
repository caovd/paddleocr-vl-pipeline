FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG USE_GPU=false
COPY requirements.txt requirements-gpu.txt ./
RUN if [ "$USE_GPU" = "true" ]; then \
        pip install --no-cache-dir -r requirements-gpu.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

RUN mkdir -p /app/models

COPY app.py .

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
