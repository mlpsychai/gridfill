# gridfill — Tkinter desktop app served in the browser via noVNC.
#
# Base is debian:bookworm-slim (not python:slim) because python:slim's
# Python lives outside apt and `python3-tk` from apt won't bind to it.
# Debian's python3 (3.11) + python3-tk are installed from the same source
# and import cleanly. The app has zero pip dependencies.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-tk \
        xvfb \
        x11vnc \
        fluxbox \
        novnc \
        websockify \
        fonts-dejavu \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the modules the app needs at runtime. admin/, assets/, docs/,
# saved_svgs/, jpeg/, .venv/, etc. are intentionally excluded.
COPY grid_fill.py app.py ui.py tools.py renderer.py state.py telemetry.py \
     copic_palette.py curated_palettes.py ./

COPY start.sh /start.sh
RUN chmod +x /start.sh

ENV DISPLAY=:1 \
    SCREEN_WIDTH=1920 \
    SCREEN_HEIGHT=1200 \
    SCREEN_DEPTH=24 \
    VNC_PORT=5900 \
    NOVNC_PORT=6080

EXPOSE 6080

# tini reaps zombies from the multi-process startup script.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/start.sh"]
