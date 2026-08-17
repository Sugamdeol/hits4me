# SnapDeploy image for FeelingSurf Viewer (not 9Hits).
# The official image supports both linux/amd64 and linux/arm64.
FROM feelingsurf/viewer:stable

LABEL org.opencontainers.image.title="FeelingSurf Viewer for SnapDeploy" \
      org.opencontainers.image.description="SnapDeploy-ready FeelingSurf Viewer container" \
      org.opencontainers.image.source="https://github.com/Sugamdeol/hits4me"

# SnapDeploy routes web traffic to the exposed container port. The viewer's
# built-in health server listens here; both values can still be overridden at
# runtime from the SnapDeploy environment settings.
ENV PORT=3000 \
    access_token=27f77ef00e768cdbdc7352f3456e35b5
EXPOSE 3000

COPY --chmod=755 start.sh /snapdeploy-start.sh

# Check the FeelingSurf Viewer's own health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider "http://127.0.0.1:${PORT:-3000}/" || exit 1

ENTRYPOINT ["/snapdeploy-start.sh"]
