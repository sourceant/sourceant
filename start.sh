#!/bin/bash
sourceant db upgrade head
pip install -r requirements-dev.txt
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000 --log-config log.ini
