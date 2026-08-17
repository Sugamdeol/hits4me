# Notesly on SnapDeploy — notes website with a hidden background worker

This folder is a standalone deployment for
[SnapDeploy](https://snapdeploy.dev/). It does not touch any of the files in
the repository root.

* The public URL (container port `3000`) serves **Notesly**, a complete,
  working notes app — landing page, feature sections and a real notes editor
  (create / pin / search / tag / auto-save), with notes persisted server-side
  in a JSON file and mirrored in the browser.
* A **background worker** runs headlessly on the container's IP via the
  image's launcher (`/nh.sh`), supervised and auto-restarted by `start.sh`.
  The worker access key is **hardcoded in the image** (`ENV ACCESS_KEY`), so
  no token variable is required in SnapDeploy.

The deployment therefore looks like an ordinary notes website to anyone
visiting the SnapDeploy URL, while the worker continues to run in the
background.

> **Discreet by design:**
> * Nothing in the public surface references the worker — the website, its
>   HTML/JS/CSS, `/health`, `/api/*` and the HTTP headers only ever mention
>   Notesly.
> * The worker runs under a PTY wrapper (`run_hidden.py`) that **redacts
>   brand tokens from its output**, so container logs show generic lines
>   (`[notesly] worker session started (pid …)`) and never brand names.
> * Startup logs, pid files and data directories use neutral names
>   (`notesly-worker`).
>
> **Honest limits:** the base image itself (`9hitste/appv6`) and the worker's
> own child processes retain their original internal names — those cannot be
> changed without breaking the worker. Everything the container exposes to
> the internet (HTTP surface) and to casual log/process review at the
> supervisor level is neutral.

## Deploy

1. In SnapDeploy, create a container from this GitHub repository.
2. Enable monorepo/root-directory selection and set the root directory to
   **`snapdeploy-9hits`**. SnapDeploy will detect `snapdeploy-9hits/Dockerfile`
   there.
3. Set the container/internal port to **`3000`** if SnapDeploy asks for it.
   The included Dockerfile already exposes that port.
4. Deploy. The public URL and `/` endpoint are used as the container health
   endpoint (the notes website answers `200` there); the worker runs
   headlessly in the background.

The access key is embedded in the image configuration, so no token variable is
required in SnapDeploy. You can override it at runtime by setting
`ACCESS_KEY` in SnapDeploy's environment settings.

> **Security warning:** The embedded key is visible to anyone who can read
> this repository or inspect the built image. Rotate the key in the panel and
> switch to a SnapDeploy secret if the repository or image is shared.

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

## Worker settings

Sensible defaults are baked in: one system session on the container's IP
(`--system-session`), headless (`--hide-browser`), popups/adult/crypto off,
2-hour reset interval, and neutral session labels (`note=notesly`). All of
them can be overridden with environment variables (see below).

## Local test

```bash
cd snapdeploy-9hits
docker compose up --build
```

The embedded key is used by default. To test with a different key:

```bash
ACCESS_KEY=another_key docker compose up --build
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
| `ACCESS_KEY` | No | Embedded key | Worker access key; overrides the image default |
| `PORT` | No | `3000` | Notes website / container port |
| `SUPERVISOR_DELAY` | No | `10` | Seconds the supervisor waits before relaunching a crashed worker |
| `SYSTEM_SESSION` | No | `yes` | Run a direct session on the container IP |
| `CLEAR_ALL_SESSIONS` | No | `yes` | Clear stale sessions on boot |
| `HIDE_BROWSER` | No | `yes` | Run headless |
| `ALLOW_POPUPS` | No | `no` | Popups toggle |
| `ALLOW_ADULT` | No | `no` | Adult campaigns toggle |
| `ALLOW_CRYPTO` | No | `no` | Crypto mining campaigns toggle |
| `RESET_INTERVAL` | No | `2h` | Periodic worker reset (`2h`, `6h`, `30m`) |
| `SESSION_NOTE` | No | `notesly` | Session label in the panel |
| `NOTE` | No | `notesly` | Machine label in the panel |
| `EX_PROXY_SESSIONS` / `EX_PROXY_URL` | No | — | External proxy pool sessions |
| `BULK_ADD_PROXY_LIST` / `BULK_ADD_PROXY_TYPE` | No | — | Static proxy list (`ip:port;user;pass\|…`) |
| `EXTRA_ARGS` | No | — | Raw extra flags appended to the launcher |
