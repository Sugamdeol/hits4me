# SnapDeploy image for the Notesly notes website with a background worker.
#
# Built from a plain Debian base (no viewer base image is inherited), so the
# image itself carries no brand-identifying base. The worker is installed from
# the official release and renamed to a neutral name, so process listings,
# /proc, ports and the app's data directory never reveal what it is.
#
# Supports linux/amd64 and linux/arm64.
FROM debian:bookworm-slim

ARG TARGETARCH
ARG FSVIEWER_VERSION=2.5.2

LABEL org.opencontainers.image.title="Notesly for SnapDeploy" \
      org.opencontainers.image.description="Notes website service for SnapDeploy" \
      org.opencontainers.image.source="https://github.com/Sugamdeol/hits4me"

ENV DEBIAN_FRONTEND=noninteractive \
    PORT=3000 \
    WORKER_HEALTH_PORT=3100 \
    access_token=27f77ef00e768cdbdc7352f3456e35b5

# Runtime dependencies (same set the official worker image ships) plus python3
# for the Notesly web server.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
    ca-certificates \
    wget \
    python3 \
    libasound2 \
    libgbm1 \
    libgtk-3-0 \
    libnotify4 \
    libnss3 \
    libsecret-1-0 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    xvfb \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install the worker from the official release, then neutralise its name and
# strip its desktop artifacts so nothing in the filesystem advertises it:
# the app directory and binary are renamed (process listings, /proc/<pid>/exe
# and the symlink all show the neutral name) and the .desktop/icons/doc
# packages are removed.
RUN groupadd -r notesly \
    && useradd -rm -g notesly -G audio notesly \
    && echo 'pcm.!default {\n  type plug\n  slave.pcm "null"\n}' > /etc/asound.conf \
    && arch="${TARGETARCH:-amd64}" \
    && wget -q "https://github.com/feelingsurf/viewer/releases/download/${FSVIEWER_VERSION}/FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb" \
    && dpkg -i "FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb" \
    && rm -f "FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb" \
    && if [ -L /usr/bin/FeelingSurfViewer ]; then \
         app_bin="$(readlink -f /usr/bin/FeelingSurfViewer)"; \
         app_dir="$(dirname "$app_bin")"; \
         if [ "$app_dir" != "/usr/bin" ] && [ "$app_dir" != "/bin" ]; then \
           rm -f /usr/bin/FeelingSurfViewer; \
           mv "$app_dir" /opt/notesly-worker; \
           if [ -f /opt/notesly-worker/FeelingSurfViewer ]; then \
             mv /opt/notesly-worker/FeelingSurfViewer /opt/notesly-worker/notesly-worker; \
           fi; \
           ln -s /opt/notesly-worker/notesly-worker /usr/bin/notesly-worker; \
         else \
           mv /usr/bin/FeelingSurfViewer /usr/bin/notesly-worker; \
         fi; \
       else \
         mv /usr/bin/FeelingSurfViewer /usr/bin/notesly-worker; \
       fi \
    && rm -rf /usr/share/doc /usr/share/man /usr/share/applications \
              /usr/share/icons /usr/share/pixmaps \
    && rm -rf /var/cache/debconf /tmp/* /var/tmp/*

WORKDIR /home/notesly

COPY --chmod=755 start.sh /notesly-start.sh
COPY notes_app /notes_app

# The public notes website responds on / — that is the container health
# endpoint (the worker's own health server listens on loopback only, on
# WORKER_HEALTH_PORT).
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider "http://127.0.0.1:${PORT:-3000}/" || exit 1

USER notesly

ENTRYPOINT ["/notesly-start.sh"]
