FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8050 \
    HOSPITAL_PLACEMENT_VALIDATE_BLOCKSNET=0 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8050

CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-8050} --workers 1 --threads 4 --timeout 300 --access-logfile - --error-logfile -"]
