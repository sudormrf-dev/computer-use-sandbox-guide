#!/bin/bash
set -e

# Start virtual display
Xvfb :99 -screen 0 "${SCREEN_WIDTH:-1280}x${SCREEN_HEIGHT:-800}x24" -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to be ready
sleep 1
if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "ERROR: Xvfb failed to start" >&2
    exit 1
fi

export DISPLAY=:99

# Optionally start VNC for debugging
if [ "${VNC_ENABLED:-0}" = "1" ]; then
    x11vnc -display :99 -nopw -forever -shared -rfbport 5999 &
    echo "VNC started on port 5999"
fi

echo "Virtual display :99 running (${SCREEN_WIDTH}x${SCREEN_HEIGHT})"

# Run the requested command
exec "$@"
