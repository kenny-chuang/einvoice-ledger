#!/usr/bin/env sh
set -eu
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
