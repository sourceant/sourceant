#!/bin/bash
set -e

sourceant db upgrade head
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
