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

# The health server and the PTY wrapper are pure stdlib Python.
# Debian/Ubuntu based images usually ship python3 already; install it if not.
RUN set -eux; \
    if command -v python3 >/dev/null 2>&1; then \
        echo "python3 already present: $(python3 --version)"; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update \
        && apt-get install -y --no-install-recommends python3 \
        && rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache python3; \
    else \
        echo "ERROR: cannot locate apt-get or apk to install python3" >&2; \
        exit 1; \
    fi

WORKDIR /

COPY start.sh        /start.sh
COPY run_pty.py      /run_pty.py
COPY health_server.py /health_server.py

RUN chmod +x /start.sh /run_pty.py /health_server.py

ENV PORT=10000 \
    SUPERVISOR_DELAY=10

# Replace the upstream entrypoint: /start.sh launches BOTH the 9Hits viewer
# (under a PTY, supervised + auto-restarted) and the /health HTTP server.
# Extra docker/start-command arguments are forwarded to /nh.sh as flags.
ENTRYPOINT ["/start.sh"]
CMD []
