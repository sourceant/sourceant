FROM python:3.9-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev python3-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

FROM python:3.9-slim

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd --create-home appuser
USER appuser

ENV PATH="/home/appuser/.local/bin:${PATH}"

COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache /wheels/*

COPY . .

USER root
RUN ln -s /app/sourceant /usr/local/bin/sourceant
USER appuser

ENTRYPOINT ["/app/start.prod.sh"]
