# hits4me

Run the **9Hits Viewer v6** ([9hitste/appv6](https://hub.docker.com/r/9hitste/appv6)) on
[Render](https://render.com) as a single web service, with a **`/health` endpoint**
built in so you can monitor it with an uptime ping bot (UptimeRobot, Better Stack,
Kuma, Upptime, cron + curl, …).

## Why the wrapper?

The stock image has two problems on Render:

1. **Render allocates no TTY** — the viewer renders a text dashboard and exits
   immediately without a TTY (plain `docker run -d` crash-loops for the same reason).
2. **Render web services must listen on `0.0.0.0:$PORT`** and pass an HTTP health
   check — the viewer listens on nothing.

So this repo builds a small image on top of `9hitste/appv6` whose entrypoint:

| File | Job |
| --- | --- |
| `start.sh` | Builds the `/nh.sh` flags from env vars, supervises the viewer (auto-restarts it on exit) |
| `run_pty.py` | Allocates a pseudo-TTY for the viewer, mirrors the dashboard to the logs |
| `health_server.py` | Tiny stdlib-only HTTP server answering `GET /health` on `0.0.0.0:$PORT` |

## Deploy on Render

### Option A — Blueprint (recommended)

1. Push this repo to GitHub.
2. Render Dashboard → **New → Blueprint** → connect the repo → **Apply**.
   `render.yaml` is already set up (Docker runtime, free plan, health check on `/health`).
3. When prompted, paste your 9Hits **access key** (from
   [panel.9hits.com/user/profile](https://panel.9hits.com/user/profile)).
4. Wait for the deploy to go live (first boot downloads the viewer, so it can take
   a few minutes — the health endpoint is up immediately, so the deploy still succeeds).

### Option B — Manual

1. Render Dashboard → **New → Web Service** → connect this repo.
2. Runtime: **Docker** (it auto-detects the `Dockerfile`).
3. Instance type: Free (or bigger if you run many sessions).
4. Health check path: `/health`.
5. Leave the **Docker command** field empty (the image entrypoint does
   everything; anything you type there is appended to `/nh.sh` as extra flags).
6. Add environment variables (at minimum `ACCESS_KEY`; see table below).

## Environment variables

All viewer options from the upstream image are available as env vars (any
arguments you pass as the start command are appended to `/nh.sh` verbatim, too).

| Env var | `/nh.sh` flag | Notes |
| --- | --- | --- |
| `ACCESS_KEY` | `--access-key` | **Required.** From [panel.9hits.com/user/profile](https://panel.9hits.com/user/profile) |
| `SYSTEM_SESSION` | `--system-session` | `yes`/`no` — one session on the machine's IP |
| `EX_PROXY_SESSIONS` | `--ex-proxy-sessions` | Number of external-proxy sessions |
| `EX_PROXY_URL` | `--ex-proxy-url` | External proxy pool URL (empty = 9Hits pool) |
| `BULK_ADD_PROXY_LIST` | `--bulk-add-proxy-list` | `server:port;user;pass\|server2:port;user2;pass2` |
| `BULK_ADD_PROXY_TYPE` | `--bulk-add-proxy-type` | `http`, `socks4`, `socks5`, `ssh` |
| `SESSION_NOTE` | `--session-note` | Note applied to created sessions |
| `NOTE` | `--note` | Note for this machine |
| `ALLOW_POPUPS` / `ALLOW_ADULT` / `ALLOW_CRYPTO` | `--allow-*` | `yes` or `no` |
| `HIDE_BROWSER` | `--hide-browser` | `yes` or `no` |
| `CLEAR_ALL_SESSIONS` | `--clear-all-sessions` | `yes` to wipe sessions on start |
| `HIDE_COLUMNS` | `--hide-columns` | e.g. `quality,points` |
| `CACHE_PATH` / `CACHE_LIMIT` | `--cache-path` / `--cache-limit` | e.g. `CACHE_LIMIT=104857600` (100 MB) |
| `INSTALL_DIR` | `--install-dir` | Default `/etc` |
| `DEFAULT_DL` | `--default-dl` | Custom viewer download URL |
| `RE_INSTALL` | `--re-install` | `yes` to force re-download |
| `RESTART_DELAY` | `--restart-delay` | Viewer's own restart delay, default `5` |
| `RESET_INTERVAL` | `--reset-interval` | Periodic graceful reset, e.g. `6h`, `30m` |
| `VNC` / `VNC_PW` / `NO_VNC_PW` / `VNC_PORT` | `--vnc*` | VNC is **not reachable on Render** (only `$PORT` is exposed) — useful for local Docker only |
| `EXTRA_ARGS` | — | Raw extra flags, e.g. `--hide-columns=quality,points` |
| `PORT` | — | Health endpoint port. Render sets it automatically (default `10000`) |
| `SUPERVISOR_DELAY` | — | Seconds between viewer relaunches, default `10` |

## The /health endpoint

```
GET https://<your-service>.onrender.com/health
```

```json
{
  "service": "9hits-viewer",
  "version": "1.0.0",
  "status": "ok",
  "viewer_running": true,
  "supervisor_running": true,
  "viewer_pid": 42,
  "restarts": 0,
  "uptime_seconds": 5123
}
```

* `/health`, `/healthz`, `/ping` and `/` all answer (HEAD too).
* `status` is `ok` while the viewer runs, `restarting` while it's between
  relaunches, `error` if the supervisor is gone (HTTP 503 — Render restarts the
  container, which is the right recovery).
* It answers 200 as soon as the container boots, so deploys succeed even while
  the viewer is still downloading.

## Uptime ping bot setup

1. **Monitor URL:** `https://<your-service>.onrender.com/health`
2. **Interval: 14 minutes or less.** Render's free tier spins down a web
   service after ~15 minutes without inbound traffic — your pings keep it
   awake (Render's own health checks don't count as traffic). 5–10 minute
   intervals are typical. Free tier also has a 750 hour/month pool, so a
   24/7 pinged service may hit that limit — a paid plan lifts both limits.
3. **Bonus — alert when the viewer itself crashes:** use a *keyword* monitor
   (UptimeRobot: "Keyword" monitor type; Better Stack: keyword check; Kuma:
   HTTP monitor with a "keyword" condition) for the string `"status": "ok"`.
   If the viewer crash-loops while the container stays up, the keyword
   disappears and you get paged even though the HTTP status is still 200.

## Local testing

```bash
# one-off
docker build -t hits4me-9hits .
docker run -d --shm-size=2g -p 10000:10000 \
  -e ACCESS_KEY=<your-key> -e SYSTEM_SESSION=yes \
  hits4me-9hits
curl http://localhost:10000/health   # -> {"status": "ok", ...}
docker logs -f <container-id>        # live dashboard (run_pty allocates the TTY, so plain -d works)

# or with compose
cp .env.example .env   # set ACCESS_KEY
docker compose up --build
```

## Troubleshooting

* **Dashboard says `User not found!`** — `ACCESS_KEY` is wrong or empty. Fix the
  env var on Render (Environment tab) and redeploy.
* **Viewer crash-looping** — check `restarts` in the `/health` JSON and the logs.
  Common causes: wrong access key, too many sessions for the instance size
  (Render free = 512 MB RAM / 0.1 CPU — start with 1 system session; paid plans
  handle more), or `/dev/shm` limits (Render doesn't expose `--shm-size`; if
  Chrome dies with shm errors, reduce sessions or move to a dedicated VPS).
* **`/health` is 503** — the supervisor died; Render restarts the service
  automatically.
* **Service spins down overnight** — your uptime bot interval is ≥15 min, or it
  stopped pinging.
* **Render account/TOS** — 9Hits generates automated web traffic; make sure
  that's acceptable under your VPS/host provider's terms before scaling up.
