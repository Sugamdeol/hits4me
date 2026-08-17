# SnapDeploy image for FeelingSurf Viewer (not 9Hits), fronted by a notes
# website. The official image supports both linux/amd64 and linux/arm64.
FROM feelingsurf/viewer:stable

LABEL org.opencontainers.image.title="FeelingSurf Viewer + Notesly front for SnapDeploy" \
      org.opencontainers.image.description="Notes website in front of the FeelingSurf Viewer for SnapDeploy" \
      org.opencontainers.image.source="https://github.com/Sugamdeol/hits4me"

# SnapDeploy routes web traffic to the exposed container port. The notes
# website (Notesly) listens here; the FeelingSurf viewer keeps running
# headlessly in the background with its own internal health server on
# VIEWER_HEALTH_PORT. Both values can be overridden at runtime from the
# SnapDeploy environment settings.
ENV PORT=3000 \
    VIEWER_HEALTH_PORT=3100 \
    access_token=27f77ef00e768cdbdc7352f3456e35b5
EXPOSE 3000

# The notes web front is a single Python 3 stdlib server (no pip deps).
# Base image is debian:bookworm-slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=755 start.sh /snapdeploy-start.sh
COPY notes_app /notes_app

# The public notes website responds on / — that is the container health
# endpoint too (the viewer's own health server moved to VIEWER_HEALTH_PORT).
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider "http://127.0.0.1:${PORT:-3000}/" || exit 1

ENTRYPOINT ["/snapdeploy-start.sh"]
