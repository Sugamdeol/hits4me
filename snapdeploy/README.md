# FeelingSurf Viewer on SnapDeploy — fronted by a notes website

This folder is a standalone **FeelingSurf Viewer** deployment for
[SnapDeploy](https://snapdeploy.dev/). It does not run or include the 9Hits
viewer from the repository root.

It extends the official multi-architecture image,
[`feelingsurf/viewer:stable`](https://hub.docker.com/r/feelingsurf/viewer), and
puts a **notes website ("Notesly") in front** of the viewer:

* The public URL (container port `3000`) serves **Notesly**, a complete,
  working notes app — landing page, feature sections and a real notes editor
  (create / pin / search / tag / auto-save), with notes persisted server-side
  in a JSON file and mirrored in the browser.
* The **FeelingSurf viewer keeps running headlessly in the background on the
  container's IP, giving views**, exactly as before. Its own built-in health
  server is moved to an internal port (`VIEWER_HEALTH_PORT`, default `3100`)
  so it does not collide with the notes website.
* If the viewer crashes, a supervisor loop in `start.sh` restarts it, so the
  container keeps earning views while the notes site stays online.

The deployment therefore looks like an ordinary notes website to anyone
visiting the SnapDeploy URL, while the viewer continues to work in the
background.

## Deploy

1. In SnapDeploy, create a container from this GitHub repository.
2. Enable monorepo/root-directory selection and set the root directory to
   **`snapdeploy`**. SnapDeploy will detect `snapdeploy/Dockerfile` there.
3. Set the container/internal port to **`3000`** if SnapDeploy asks for it.
   The included Dockerfile already exposes that port.
4. Deploy. The public URL and `/` endpoint are used as the container health
   endpoint (the notes website answers `200` there); the viewer runs
   headlessly in the background.

The requested FeelingSurf access token is embedded in the image configuration,
so no token variable is required in SnapDeploy. You can override it at runtime
by setting `access_token` in SnapDeploy's environment settings.

> **Security warning:** The embedded token is visible to anyone who can read
> this repository or inspect the built image. Rotate the token in FeelingSurf
> and switch to a SnapDeploy secret if the repository or image is shared.

## What is served where

| Path | What it is |
| --- | --- |
| `/` | Notesly notes website (SnapDeploy health endpoint — always `200`) |
| `/health`, `/healthz`, `/ping` | JSON status incl. `viewer_running` (uptime monitors) |
| `/api/notes` | Notes REST API (GET / POST / PUT / DELETE) |
| `/api/stats` | Note counts + viewer status |
| `/static/*` | Notes website assets (HTML/CSS/JS) |

Example health response:

```json
{
  "service": "notesly",
  "app": "Notesly notes website",
  "version": "1.0.0",
  "status": "ok",
  "viewer_running": true,
  "note_count": 3,
  "uptime_seconds": 3600
}
```

## Resources

FeelingSurf recommends approximately **2 GB RAM and 2 CPU cores per viewer**.
A smaller plan may be terminated for exceeding its memory limit. Run only one
viewer per SnapDeploy container.

SnapDeploy may put free containers to sleep when they receive no traffic. If
continuous surfing is required, use an always-on plan; repeatedly pinging a
free service may violate the hosting plan's intended usage.

## Local test

```bash
cd snapdeploy
docker compose up --build
```

The embedded token is used by default. To test with a different token:

```bash
ACCESS_TOKEN=another_token docker compose up --build
```

In another terminal:

```bash
curl http://localhost:3000/          # notes website
curl http://localhost:3000/health    # status incl. viewer_running
```

Stop and remove the test container with `docker compose down`.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `access_token` | No | Embedded token | Official FeelingSurf authentication variable; overrides the image default |
| `ACCESS_TOKEN` | No | — | Convenience override used by the local Compose file |
| `PORT` | No | `3000` | Notes website / container port (must differ from `VIEWER_HEALTH_PORT`) |
| `VIEWER_HEALTH_PORT` | No | `3100` | Internal port for the viewer's own health server |
| `NOTES_DATA` | No | `~/.notesly/notes.json` | JSON file where notes are persisted |
| `SUPERVISOR_DELAY` | No | `10` | Seconds the supervisor waits before relaunching a crashed viewer |
