FROM python:3.9-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev python3-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

FROM python:3.9-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd --create-home appuser
USER appuser

ENV PATH="/home/appuser/.local/bin:${PATH}"
ENV PYTHONPATH=/app

COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache /wheels/*

COPY . .

COPY --chown=appuser:appuser sourceant /home/appuser/.local/bin/sourceant
RUN chmod +x /home/appuser/.local/bin/sourceant

ENTRYPOINT ["/app/start.prod.sh"]
