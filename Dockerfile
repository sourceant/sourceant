FROM python:3.10-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev python3-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

FROM python:3.10-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then PKG_ARCH="x86_64-unknown-linux-gnu"; elif [ "$ARCH" = "aarch64" ]; then PKG_ARCH="aarch64-unknown-linux-gnu"; fi && \
    curl -fsSL -L "https://github.com/mohsen1/yek/releases/latest/download/yek-${PKG_ARCH}.tar.gz" \
    | tar xz --strip-components=1 -C /usr/local/bin

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
