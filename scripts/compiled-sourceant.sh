#!/bin/sh
exec python -c 'from src.cli.main import cli; cli()' "$@"
