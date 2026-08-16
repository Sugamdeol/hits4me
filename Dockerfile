# =============================================================================
# 9Hits Viewer v6 + FeelingSurf Viewer + /health endpoint - ready for Render.com
#
# Based on the official 9Hits image: https://hub.docker.com/r/9hitste/appv6
# (it carries the correct system libraries plus the viewer tarball baked in).
#
# Why this image exists:
#   * The upstream image extracts its 145 MB bzip2 viewer tarball on EVERY
#     container start and runs the viewer via an opaque /nh.sh. On free-tier
#     CPUs (Render 0.1 vCPU) that runtime extraction stalls for minutes and a
#     wedged viewer produces no output and never exits ("Extracting
#     9hitsv6-linux64 ..." then silence forever). We instead extract the
#     viewer at BUILD time into /opt/9hits and drive it directly, mirroring
#     the official install.sh flow:
#       Xvfb :99  ->  nhviewer <flags> --exit-on-init  (apply config)
#                 ->  nhviewer --auto-start --in-loop --render-to-terminal
#   * The v6 viewer requires a TTY (Render doesn't allocate one), so the
#     entrypoint runs it under a pseudo-TTY (run_pty.py, with a wedge
#     watchdog and output heartbeat).
#   * Render web services must listen on 0.0.0.0:$PORT and pass an HTTP
#     health check, so the entrypoint also runs a tiny /health HTTP server
#     (health_server.py) - perfect target for an uptime ping bot.
# =============================================================================

FROM 9hitste/appv6

ARG FSVIEWER_VERSION=2.5.2

# Add the official FeelingSurf application to the 9Hits image. Both upstream
# viewers are Debian/Ubuntu applications, so they can safely share this image
# and its Chromium/X11 libraries. Pinning the release keeps builds repeatable.
#
# Also extract the 9Hits v6 viewer (baked into the base image at
# /etc/9hitsv6-linux64.tar.bz2) into /opt/9hits NOW, at build time, so the
# container starts instantly instead of decompressing 145 MB of bzip2 on a
# throttled CPU at every boot. The tarball is then removed (whiteout) to save
# runtime disk.
RUN set -eux; \
    command -v apt-get >/dev/null 2>&1 || { \
        echo "ERROR: the 9Hits base image must provide apt-get" >&2; exit 1; \
    }; \
    test -f /etc/9hitsv6-linux64.tar.bz2 || { \
        echo "ERROR: /etc/9hitsv6-linux64.tar.bz2 missing - base image layout changed?" >&2; exit 1; \
    }; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 ca-certificates wget bzip2 util-linux psmisc bc \
        libasound2 libgbm1 libgtk-3-0 libnotify4 libnss3 libsecret-1-0 \
        libxss1 libxtst6 libatspi2.0-0 libatomic1 libcanberra-gtk-module \
        xdg-utils xvfb x11-utils x11vnc; \
    getent group audio >/dev/null || groupadd -r audio; \
    getent group fsviewer >/dev/null || groupadd -r fsviewer; \
    id fsviewer >/dev/null 2>&1 || useradd -r -m -g fsviewer -G audio fsviewer; \
    echo 'pcm.!default {\n  type plug\n  slave.pcm "null"\n}' > /etc/asound.conf; \
    arch="$(dpkg --print-architecture)"; \
    wget -q -O /tmp/feelingsurf.deb \
      "https://github.com/feelingsurf/viewer/releases/download/${FSVIEWER_VERSION}/FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb"; \
    dpkg -i /tmp/feelingsurf.deb || apt-get install -f -y --no-install-recommends; \
    test -x /usr/bin/FeelingSurfViewer; \
    rm -f /tmp/feelingsurf.deb; \
    # --- 9Hits viewer: extract at build time, mirroring official install.sh ---
    mkdir -p /opt/9hits /tmp/nhextract; \
    tar -xjf /etc/9hitsv6-linux64.tar.bz2 -C /tmp/nhextract; \
    if [ -f /tmp/nhextract/nhviewer ]; then \
        cp -a /tmp/nhextract/. /opt/9hits/; \
    else \
        sub="$(find /tmp/nhextract -mindepth 1 -maxdepth 1 -type d | head -n 1)"; \
        test -n "$sub" && test -f "$sub/nhviewer" || { \
            echo "ERROR: nhviewer not found in the baked tarball" >&2; exit 1; \
        }; \
        cp -a "$sub"/. /opt/9hits/; \
    fi; \
    rm -rf /tmp/nhextract; \
    chmod -R a+rwX /opt/9hits; \
    chmod +x /opt/9hits/nhviewer; \
    test -x /opt/9hits/nhviewer; \
    rm -f /etc/9hitsv6-linux64.tar.bz2; \
    rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* /tmp/* /var/tmp/*

WORKDIR /

COPY start.sh        /start.sh
COPY run_pty.py      /run_pty.py
COPY health_server.py /health_server.py
COPY fetch_proxy_list.py /fetch_proxy_list.py
COPY feelingsurf-run.sh /feelingsurf-run.sh

RUN chmod +x /start.sh /run_pty.py /health_server.py /fetch_proxy_list.py /feelingsurf-run.sh

ENV PORT=10000 \
    FEELINGSURF_PORT=3000 \
    FEELINGSURF_ENABLED=yes \
    SUPERVISOR_DELAY=10 \
    NH_DIR=/opt/9hits \
    NH_DISPLAY=:99 \
    INIT_TIMEOUT=300 \
    NH_WATCHDOG=yes \
    NH_WATCHDOG_STUCK=600

EXPOSE 10000 3000 5901

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD wget -q --spider "http://localhost:${PORT}/health" || exit 1

# /start.sh launches BOTH viewers (supervised + auto-restarted) plus the
# Xvfb display for 9Hits and the combined /health HTTP server.
# Extra docker/start-command arguments are forwarded to the nhviewer init pass.
ENTRYPOINT ["/start.sh"]
CMD []
