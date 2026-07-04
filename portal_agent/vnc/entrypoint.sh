#!/bin/bash
# Start the virtual display, a headed Chromium (CDP + persistent profile), x11vnc, and noVNC.
set -e
export DISPLAY=:99

echo "[vnc] starting Xvfb…"
Xvfb :99 -screen 0 1600x1200x24 -nolisten tcp &
sleep 1

CHROME="$(ls /ms-playwright/chromium-*/chrome-linux/chrome | head -1)"
echo "[vnc] launching Chromium: $CHROME"
# Headed on :99, CDP open to the host (0.0.0.0 + allow-origins), persistent profile in /profile.
# READ-ONLY intent: the backend only navigates; the Reader account can't modify anything either.
"$CHROME" \
  --no-sandbox --no-first-run --no-default-browser-check --disable-gpu \
  --user-data-dir=/profile \
  --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 --remote-allow-origins=* \
  --window-position=0,0 --window-size=1600,1200 \
  "https://portal.azure.com/" &
sleep 2

echo "[vnc] starting x11vnc on :5900…"
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet &
sleep 1

echo "[vnc] serving noVNC on :6080 (open http://localhost:6080/vnc.html)"
exec websockify --web=/usr/share/novnc 6080 localhost:5900
