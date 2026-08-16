# =============================================================================
# 9Hits Viewer v6 + /health endpoint - ready for Render.com
#
# Based on the official 9Hits image: https://hub.docker.com/r/9hitste/appv6
#
# Why this image exists:
#   * The 9Hits viewer requires a TTY (Render doesn't allocate one), so the
#     entrypoint runs it under a pseudo-TTY (run_pty.py).
#   * Render web services must listen on 0.0.0.0:$PORT and pass an HTTP
#     health check, so the entrypoint also runs a tiny /health HTTP server
#     (health_server.py) - perfect target for an uptime ping bot.
# =============================================================================

FROM 9hitste/appv6

ARG FSVIEWER_VERSION=2.5.2

# Add the official FeelingSurf application to the 9Hits image. Both upstream
# viewers are Debian/Ubuntu applications, so they can safely share this image
# and its Chromium/X11 libraries. Pinning the release keeps builds repeatable.
RUN set -eux; \
    command -v apt-get >/dev/null 2>&1 || { \
        echo "ERROR: the 9Hits base image must provide apt-get" >&2; exit 1; \
    }; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        python3 ca-certificates wget util-linux \
        libasound2 libgbm1 libgtk-3-0 libnotify4 libnss3 libsecret-1-0 \
        libxss1 libxtst6 xdg-utils xvfb; \
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
    SUPERVISOR_DELAY=10

EXPOSE 10000 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD wget -q --spider "http://localhost:${PORT}/health" || exit 1

# Replace the upstream entrypoint: /start.sh launches BOTH viewers
# (supervised + auto-restarted) and the combined /health HTTP server.
# Extra docker/start-command arguments are forwarded to /nh.sh as flags.
ENTRYPOINT ["/start.sh"]
CMD []
