#!/bin/bash
pip install -r requirements-dev.txt
for plugin_dir in /plugins/*/; do
    if [ -d "$plugin_dir" ]; then
        pip install -e "$plugin_dir" || echo "Failed to install plugin: $plugin_dir"
    fi
done
sourceant db upgrade head
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000 --env-file .env
