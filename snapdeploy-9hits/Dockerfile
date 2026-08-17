# SnapDeploy image for the Notesly notes website with a hidden background
# worker.
#
# The public container port serves the Notesly notes website; the worker runs
# headlessly in the background via /nh.sh under a PTY wrapper whose output is
# redacted, so container logs stay generic.
#
# The worker access key is hardcoded below (ENV ACCESS_KEY) so no variable is
# required in SnapDeploy; it can still be overridden at runtime.
#
# The 9Hits Viewer is x86_64-only, so the base image platform is pinned to
# linux/amd64. Without this pin BuildKit requests the builder's own
# architecture and fails with "no match for platform in manifest: not found"
# when 9hitste/appv6 has no manifest for that platform.
FROM --platform=linux/amd64 9hitste/appv6

LABEL org.opencontainers.image.title="Notesly for SnapDeploy" \
      org.opencontainers.image.description="Notes website service for SnapDeploy" \
      org.opencontainers.image.source="https://github.com/Sugamdeol/hits4me"

# The notes server and the PTY wrapper are pure stdlib Python.
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

COPY --chmod=755 start.sh /notesly-start.sh
COPY run_hidden.py /run_hidden.py
COPY notes_app /notes_app

ENV PORT=8000 \
    ACCESS_KEY=23f9097a8d823267188c49b3cc0598b1 \
    SUPERVISOR_DELAY=10
EXPOSE 8000

# SnapDeploy probes /health on the public container port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/health', timeout=5)" || exit 1

ENTRYPOINT ["/notesly-start.sh"]
CMD []
