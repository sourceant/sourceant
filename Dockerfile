# syntax=docker/dockerfile:1.7
FROM python:3.10-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev python3-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt
RUN pip install --no-cache-dir Cython==3.3.0 setuptools==80.9.0 wheel==0.45.1
COPY src /app/src
COPY scripts/cache_tree_sitter_languages.py /app/cache_tree_sitter_languages.py
RUN python -m src.build.compile --source /app/src --output /compiled --package src

FROM python:3.10-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl git ripgrep && \
    rm -rf /var/lib/apt/lists/*

# Semgrep is shelled out to, never imported, so it is kept in an environment of
# its own. Its own pins on click and mcp disagree with this application's, and
# resolving both against one another has no answer.
RUN python -m venv /opt/semgrep \
    && /opt/semgrep/bin/pip install --no-cache-dir semgrep==1.177.0 \
    && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep

WORKDIR /app

RUN useradd --create-home appuser
USER appuser

ENV PATH="/home/appuser/.local/bin:${PATH}"
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV SOURCEANT_TREE_SITTER_CACHE=/home/appuser/.cache/sourceant/tree-sitter

RUN --mount=from=builder,source=/app/wheels,target=/wheels \
    pip install --no-cache-dir /wheels/*

RUN --mount=from=builder,source=/app/cache_tree_sitter_languages.py,target=/tmp/cache_tree_sitter_languages.py \
    python /tmp/cache_tree_sitter_languages.py

COPY --from=builder --chown=appuser:appuser /compiled/src /app/src
COPY VERSION start.prod.sh /app/
COPY LICENSE.md /usr/share/doc/sourceant/LICENSE.md

COPY --chmod=755 scripts/compiled-sourceant.sh /home/appuser/.local/bin/sourceant
COPY --chmod=755 scripts/compiled-sourceant.sh /app/sourceant

ENTRYPOINT ["/app/start.prod.sh"]

FROM runtime AS test
COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir --user pytest==8.4.2 -r /tmp/requirements-dev.txt

FROM runtime AS release
