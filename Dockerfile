FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

WORKDIR /app

# Dependencias do SO
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codigo
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/

# Pastas de runtime
RUN mkdir -p /app/data /app/data/outputs /app/logs

# Entrypoint roda o orquestrador em modo daemon
CMD ["python", "-u", "-m", "src.main", "--daemon"]
