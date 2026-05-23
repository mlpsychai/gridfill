#!/bin/bash
# Boot order: Xvfb → fluxbox → x11vnc → websockify(noVNC) → the Tk app.
# Container lifetime is bound to the Tk app — when it exits, the container exits.
set -e

# Wipe any stale X locks from a prior run in the same container layer.
rm -f /tmp/.X${DISPLAY#:}-lock /tmp/.X11-unix/X${DISPLAY#:} 2>/dev/null || true

# 1. Virtual framebuffer
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -ac +extension RANDR -nolisten tcp &
XVFB_PID=$!

# Wait for the X socket to appear (cheap and reliable; avoids racy sleeps).
for _ in $(seq 1 50); do
    [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && break
    sleep 0.1
done

# 2. Minimal window manager so Tk windows get frames + focus.
fluxbox >/dev/null 2>&1 &

# 3. VNC server bound to the Xvfb display.
#    -nopw: no VNC password (this is a public demo, no data behind it).
#    -shared: allow multiple concurrent viewers.
x11vnc -display "${DISPLAY}" -forever -shared -nopw -quiet -rfbport "${VNC_PORT}" -bg

# 4. Bridge: WebSocket (browser) ↔ raw VNC (x11vnc), and serve the noVNC HTML.
websockify --web /usr/share/novnc "${NOVNC_PORT}" "localhost:${VNC_PORT}" >/dev/null 2>&1 &

# 5. The Tk app — exec so it owns PID 1's child slot and signals propagate.
cd /app
exec python3 grid_fill.py
