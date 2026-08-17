# Notesly (SnapDeploy): notes website + hidden background worker.
# Multi-arch (linux/amd64 + linux/arm64). Built from plain Debian; the worker
# is installed from the official release and renamed to a neutral name so
# process listings, /proc and ports never reveal what it is.
FROM debian:bookworm-slim

ARG TARGETARCH
ARG FSVIEWER_VERSION=2.5.2

ENV DEBIAN_FRONTEND=noninteractive \
    PORT=3000 \
    WORKER_HEALTH_PORT=3100 \
    access_token=27f77ef00e768cdbdc7352f3456e35b5

RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
       ca-certificates wget python3 libasound2 libgbm1 libgtk-3-0 \
       libnotify4 libnss3 libsecret-1-0 libxss1 libxtst6 xdg-utils xvfb \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install the worker from the official release, then neutralise its name and
# strip desktop artifacts so nothing in the filesystem advertises it.
RUN groupadd -r notesly \
    && useradd -rm -g notesly -G audio notesly \
    && printf 'pcm.!default {\n  type plug\n  slave.pcm "null"\n}\n' > /etc/asound.conf \
    && arch="${TARGETARCH:-amd64}" \
    && wget -q "https://github.com/feelingsurf/viewer/releases/download/${FSVIEWER_VERSION}/FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb" \
    && dpkg -i "FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb" \
    && rm -f "FeelingSurfViewer-linux-${arch}-${FSVIEWER_VERSION}.deb" \
    && app_dir="$(dirname "$(readlink -f /usr/bin/FeelingSurfViewer 2>/dev/null)")" \
    && if [ -n "$app_dir" ] && [ "$app_dir" != "/usr/bin" ] && [ "$app_dir" != "/bin" ]; then \
         rm -f /usr/bin/FeelingSurfViewer; \
         mv "$app_dir" /opt/notesly-worker; \
         [ -f /opt/notesly-worker/FeelingSurfViewer ] && mv /opt/notesly-worker/FeelingSurfViewer /opt/notesly-worker/notesly-worker; \
         ln -s /opt/notesly-worker/notesly-worker /usr/bin/notesly-worker; \
       else \
         mv /usr/bin/FeelingSurfViewer /usr/bin/notesly-worker; \
       fi \
    && rm -rf /usr/share/doc /usr/share/man /usr/share/applications \
              /usr/share/icons /usr/share/pixmaps /var/cache/debconf /tmp/* /var/tmp/*

WORKDIR /home/notesly

COPY --chmod=755 start.sh /notesly-start.sh
COPY notes_app /notes_app

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider "http://127.0.0.1:${PORT:-3000}/" || exit 1

USER notesly

ENTRYPOINT ["/notesly-start.sh"]
