# Notesly on SnapDeploy — notes website with a hidden background worker

This folder is a standalone deployment for
[SnapDeploy](https://snapdeploy.dev/). It does not run or include the 9Hits
viewer from the repository root.

It builds from a plain **Debian base** (no viewer base image is inherited),
installs the worker from the official release, and puts a **notes website
("Notesly") in front**:

* The public URL (container port `3000`) serves **Notesly**, a complete,
  working notes app — landing page, feature sections and a real notes editor
  (create / pin / search / tag / auto-save), with notes persisted server-side
  in a JSON file and mirrored in the browser.
* The **worker keeps running headlessly in the background on the container's
  IP, giving views**, exactly as before. Its own built-in health server is
  moved to a loopback-only internal port (`WORKER_HEALTH_PORT`, default
  `3100`) so it does not collide with the notes website.
* If the worker crashes, a supervisor loop in `start.sh` restarts it, so the
  container keeps earning views while the notes site stays online.

The deployment therefore looks like an ordinary notes website to anyone
visiting the SnapDeploy URL, while the worker continues to run in the
background.

> **Discreet by design:** nothing in the public surface references the worker.
> The website, its HTML/JS/CSS, `/health`, `/api/*` and the HTTP headers only
> ever mention Notesly.
>
> **Discreet inside the container too:** the image is built from a plain
> Debian base (no brand-identifying base image), the worker binary, its
> directory and `/usr/bin` entry point are renamed to `notesly-worker`, its
> data directory is forced to `/tmp/notesly-worker-data`, and its desktop
> entries, icons, docs and man pages are stripped. Process listings,
> `/proc/<pid>/exe`, port listings and file listings therefore show nothing
> but neutral names. The worker's health server listens only on `127.0.0.1`
> and startup logs call it a generic "worker session".

## Deploy

> **Render alternative:** this folder can also be deployed on Render as the
> `notesly` web service via the repo-root [`render.yaml`](../render.yaml)
> blueprint (with the access token hard coded). See the Render section of the
> [root README](../README.md) — one blueprint apply creates it together with
> the original 9Hits viewer service.

1. In SnapDeploy, create a container from this GitHub repository.
2. Enable monorepo/root-directory selection and set the root directory to
   **`snapdeploy`**. SnapDeploy will detect `snapdeploy/Dockerfile` there.
3. Set the container/internal port to **`3000`** if SnapDeploy asks for it.
   The included Dockerfile already exposes that port.
4. Deploy. The public URL and `/` endpoint are used as the container health
   endpoint (the notes website answers `200` there); the worker runs
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
| `/health`, `/healthz`, `/ping` | JSON status (uptime monitors) |
| `/api/notes` | Notes REST API (GET / POST / PUT / DELETE) |
| `/api/stats` | Note counts |
| `/static/*` | Notes website assets (HTML/CSS/JS) |

Example health response:

```json
{
  "service": "notesly",
  "app": "Notesly notes website",
  "version": "1.0.0",
  "status": "ok",
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
curl http://localhost:3000/health    # status
```

Stop and remove the test container with `docker compose down`.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `access_token` | No | Embedded token | Official FeelingSurf authentication variable; overrides the image default |
| `ACCESS_TOKEN` | No | — | Convenience override used by the local Compose file |
| `PORT` | No | `3000` | Notes website / container port (must differ from `WORKER_HEALTH_PORT`) |
| `WORKER_HEALTH_PORT` | No | `3100` | Internal (loopback-only) port for the worker's own health server |
| `NOTES_DATA` | No | `~/.notesly/notes.json` | JSON file where notes are persisted |
| `SUPERVISOR_DELAY` | No | `10` | Seconds the supervisor waits before relaunching a crashed worker |
