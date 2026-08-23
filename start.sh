#!/bin/bash
pip install -r requirements-dev.txt
shopt -s nullglob
for plugin_dir in /plugins/*/; do
    pip install -e "$plugin_dir" || echo "Failed to install plugin: $plugin_dir"
done
shopt -u nullglob
sourceant db upgrade head
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000 --env-file .env
