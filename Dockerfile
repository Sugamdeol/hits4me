# =============================================================================
# 9Hits Viewer v6 + /health endpoint.
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

# Extract the 9Hits v6 viewer (baked into the base image at
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
    echo 'pcm.!default {\n  type plug\n  slave.pcm "null"\n}' > /etc/asound.conf; \
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
COPY supervisor.py   /supervisor.py

# Persistent per-service log directory (mounted by docker-compose, /logs on the
# host when bind-mounting, or a tmpfs otherwise - the supervisor recreates
# the directory on every start).
RUN mkdir -p /logs && chmod 1777 /logs

RUN chmod +x /start.sh /run_pty.py /health_server.py /fetch_proxy_list.py /supervisor.py

# LOW_MEMORY shrinks the Chromium so the viewer stays comfortably within small
# free plans. The 24x7 process manager (supervisor.py) is the entrypoint and
# keeps the 9Hits viewer + the /health server alive.
#
# ACCESS_KEY is baked in below so every host that builds this Dockerfile
# (docker-compose, Render, ...) gets the token automatically - no manual env
# vars needed.
ENV PORT=10000 \
    ACCESS_KEY=23f9097a8d823267188c49b3cc0598b1 \
    NINEHITS_ENABLED=yes \
    SUPERVISOR_DELAY=10 \
    SUPERVISOR_MAX_DELAY=120 \
    SUPERVISOR_PARK_AFTER=10 \
    SUPERVISOR_PARK_SECS=300 \
    SUPERVISOR_TICK=0.5 \
    NINEHITS_CHECK_INTERVAL=30 \
    NH_DIR=/opt/9hits \
    NH_DISPLAY=:99 \
    INIT_TIMEOUT=600 \
    NH_WATCHDOG=yes \
    NH_WATCHDOG_STUCK=600 \
    LOW_MEMORY=balanced \
    NH_MAX_MEMORY_MB=400 \
    SLOT_LOG_MAX_BYTES=10485760 \
    SLOT_LOG_BACKUPS=2 \
    LOG_DIR=/logs

EXPOSE 10000 5901

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD wget -q --spider "http://localhost:${PORT}/health" || exit 1

# /supervisor.py is the process manager: it owns one long-lived child per slot
# (9Hits launcher, health server) and restarts each independently on crash.
# /start.sh is still in the image for backwards compatibility (it can be run
# directly with `docker run ...` and no extra arguments, and the ninehits-only
# mode is what the Python supervisor invokes for the 9Hits slot).
# Extra positional arguments (when /start.sh is the entrypoint) are forwarded
# to the nhviewer init pass - existing deployments that pass flags this way
# keep working.
ENTRYPOINT ["/supervisor.py"]
CMD []
