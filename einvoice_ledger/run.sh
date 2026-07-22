#!/usr/bin/env sh
set -eu

# Docker Desktop can preserve a container writable layer while restarting the
# process. Remove stale X11 markers before launching a fresh virtual display;
# otherwise Xvfb exits and Patchright's headed Chromium cannot start.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99

# Do not accept requests until the display socket exists.
attempt=0
while [ ! -S /tmp/.X11-unix/X99 ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 50 ]; then
        echo "Xvfb failed to create display :99" >&2
        exit 1
    fi
    sleep 0.1
done

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
