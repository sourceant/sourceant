#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
count=$(git ls-files -z -- '*.py' | xargs -0 -r grep -owi kind | wc -l)
baseline=$(cat .kind-baseline)
if [ "$count" -gt "$baseline" ]; then
  echo "The word kind appears $count times, up from $baseline. Name categories type, not kind."
  exit 1
fi
if [ "$count" -lt "$baseline" ]; then
  echo "The word kind appears $count times, down from $baseline. Lower .kind-baseline to $count."
fi
