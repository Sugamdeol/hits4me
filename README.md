# hits4me — 9Hits Viewer v6

Run the **9Hits Viewer v6** ([9hitste/appv6](https://hub.docker.com/r/9hitste/appv6)) as a
lightweight container with an integrated **`/health` endpoint** and a small
**web dashboard**, suitable for free Docker hosts (Render free, Oracle Cloud
Always Free, any VPS).

The 9Hits **access key is hard-coded in the image env** (`ACCESS_KEY` in the
`Dockerfile`, mirrored in `docker-compose.yml` and `render.yaml`), so a bare
`docker compose up -d` or a Render blueprint deploy needs **no manual
environment variables at all**.

---

## Quick start

```bash
docker compose up -d viewer
curl http://localhost:10000/health
open http://localhost:10000/          # dashboard
```

Override anything by copying `.env.example` to `.env` first (optional).

### Render (free Docker web service)

Deploy this repo as a Blueprint — `render.yaml` defines the `9hits-viewer`
web service with the access key, `LOW_MEMORY=balanced`, `NH_MAX_MEMORY_MB=400`
and `healthCheckPath: /health` already set.

---

## How it works

| File | Role |
| :--- | :--- |
| `Dockerfile` | Builds on `9hitste/appv6`, extracts the v6 viewer to `/opt/9hits` **at build time** (no 145 MB bzip2 stall at boot), bakes in `ACCESS_KEY`. |
| `supervisor.py` | Container PID 1. Owns one long-lived child per slot (`ninehits`, `health`), restarts each independently with exponential backoff, per-slot logs + rotation, optional RSS caps. |
| `start.sh` | 9Hits slot launcher: Xvfb `:99`, `nhviewer <flags> --exit-on-init` (init pass), then `nhviewer --auto-start --in-loop --render-to-terminal` (run pass) under a pseudo-TTY with a wedge watchdog. |
| `run_pty.py` | Pseudo-TTY wrapper (the v6 viewer refuses to run without a TTY) + wedge/heartbeat detection. |
| `health_server.py` | `/health` JSON, dashboard GUI, `/logs/<slot>`, `/slots`, `/control/<slot>/<action>`. |
| `fetch_proxy_list.py` | Downloads `BULK_ADD_PROXY_LIST_URL` at boot and converts it to the viewer's list format. |
| `webshare_to_9hits.py` | Helper to convert a Webshare export into `BULK_ADD_PROXY_LIST`. |

Process tree:

```
supervisor.py (PID 1)
├── start.sh ninehits-only ── Xvfb :99 ── run_pty.py ── nhviewer (Chromium)
└── health_server.py  (0.0.0.0:$PORT)
```

---

## Dashboard & health endpoint

* `GET /` — dashboard (memory bars, viewer status, per-slot Start/Stop/Restart, log tail; polls `/health` every 2 s)
* `GET /health` (`/healthz`, `/ping`) — JSON status
* `GET /logs/ninehits` | `/logs/health` | `/logs/supervisor` — last 4 KB of a log
* `GET /slots` — raw supervisor snapshot
* `POST /control/ninehits/restart` — queue a slot action (`start` / `stop` / `restart`)

```json
{
  "service": "hits4me-9hits-viewer",
  "version": "4.0.0",
  "status": "ok",
  "ninehits": {
    "status": "running", "running": true, "enabled": true,
    "pid": 42, "uptime": "20m34s", "uptime_seconds": 1234,
    "restarts": 0, "memory_mb": 310.2, "child_count": 7,
    "max_memory_mb": 400, "log_file": "/logs/9hits.log"
  },
  "supervisor_running": true,
  "memory_used_mb": 312.4,
  "memory_limit_mb": 512,
  "uptime_seconds": 1234
}
```

`status` is `ok` (viewer running or disabled), `restarting` (viewer down,
supervisor recovering it) or `error` (supervisor gone → HTTP 503). Everything
else answers HTTP 200 so uptime bots don't see spurious 5xx during a restart.

---

## Memory on 512 MB plans (Render free)

* `LOW_MEMORY=balanced` (default) applies Chromium shrink flags:
  `--renderer-process-limit=1`, `--enable-low-end-device-mode`,
  `--memory-model=low`, `--js-flags=--max-old-space-size=64`, caches off,
  `--disable-gpu`.
* `LOW_MEMORY=extreme` adds `--single-process --in-process-gpu` (`NH_SP=yes`).
  This is ~160 MB in a lab but **crashes with exit code 133 on Render free** —
  the fast-crash detector drops the SP flags automatically on the next launch.
* `NH_MAX_MEMORY_MB=400` makes the supervisor gracefully restart the viewer
  before the platform OOM-kills the container.
* `INIT_TIMEOUT=600` — the config init pass is slow on 0.1 vCPU (300 s timed
  out with code 124).
* Run 5–6 sessions max on 512 MB; 9Hits officially recommends ≥ 2 GB.

---

## Proxies

```bash
# Option A: static list
BULK_ADD_PROXY_TYPE=socks5
BULK_ADD_PROXY_LIST='1.2.3.4:1080;user;pass|1.2.3.5:1080;user;pass'

# Option A+: download the list at boot (e.g. Webshare)
BULK_ADD_PROXY_LIST_URL=https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN/-/any/username/direct/-/

# Option B: your own 9Hits pool (https://dash.9hits.com/pool)
EX_PROXY_SESSIONS=5
EX_PROXY_URL=https://dash.9hits.com/pool/YOUR_POOL_KEY
```

The public 9Hits pool is closed — `EX_PROXY_SESSIONS` without `EX_PROXY_URL`
or a bulk list logs a warning and every session fails with
`Pool error: The public pool is closed!`.

Alternatively run a single direct session on the host's clean cloud IP with
`SYSTEM_SESSION=yes`.

---

## Environment variables

Viewer config flags are applied by the **init pass**
(`nhviewer <flags> --exit-on-init`); the **run pass** then starts with
`--auto-start --in-loop --render-to-terminal [--reset-interval=...]` — the
same flow as the [official 9Hits v6 installer](https://github.com/9hitste/install).

### Viewer

| Env var | `nhviewer` flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `ACCESS_KEY` | `--access-key` | *baked in* | From [panel.9hits.com/user/profile](https://panel.9hits.com/user/profile) |
| `NINEHITS_ENABLED` | — | `yes` | Run the viewer at all (`no` = `/health` only) |
| `SYSTEM_SESSION` | `--system-session` | `no` | Direct session on the instance IP |
| `CLEAR_ALL_SESSIONS` | `--clear-all-sessions` | `yes` | Wipe stale sessions on boot |
| `BULK_ADD_PROXY_LIST` | `--bulk-add-proxy-list` | *none* | Pipe-delimited proxy list |
| `BULK_ADD_PROXY_TYPE` | `--bulk-add-proxy-type` | `socks5` | `socks5`, `http`, `socks4`, `ssh` |
| `BULK_ADD_PROXY_LIST_URL` | — | *none* | Download the proxy list at boot |
| `EX_PROXY_SESSIONS` | `--ex-proxy-sessions` | *none* | Pool session count (needs `EX_PROXY_URL`) |
| `EX_PROXY_URL` | `--ex-proxy-url` | *none* | Pool URL from `dash.9hits.com/pool` |
| `SESSION_NOTE` | `--session-note` | `my-proxies` | Session label in the panel |
| `NOTE` | `--note` | *none* | Machine label in the panel |
| `HIDE_BROWSER` | `--hide-browser` | `yes` | Headless |
| `ALLOW_POPUPS` / `ALLOW_ADULT` / `ALLOW_CRYPTO` | `--allow-*` | `no` | Campaign toggles |
| `CACHE_LIMIT` | `--cache-limit` | `0` | Disk cache bytes (`0` = none; unset = 200 MB) |
| `HIDE_COLUMNS` | `--hide-columns` | *none* | e.g. `quality,points` |
| `RESET_INTERVAL` | `--reset-interval` (run pass) | `2h` | Graceful self-restart interval |
| `EXTRA_ARGS` | — | *none* | Raw flags appended to the init pass |
| `NH_RUN_EXTRA_ARGS` | — | *none* | Raw flags appended to the run pass |

### Runtime / memory

| Env var | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `10000` | Port for `/health` + dashboard |
| `LOW_MEMORY` | `balanced` | `auto` / `off` / `balanced` / `extreme` |
| `NH_SP` | `yes` | Single-process Chromium in `extreme` mode (auto-dropped after fast crashes) |
| `CREATE_SWAP` | *none* | Best-effort swap size, e.g. `256M` |
| `NH_DIR` | `/opt/9hits` | Where the viewer was extracted at build time |
| `DEFAULT_DL` | *none* | Download a different viewer build at container start |
| `NH_DISPLAY` | `:99` | Xvfb display |
| `NH_RESOLUTION` | `auto` | Xvfb resolution, e.g. `1920x1080x24` |
| `INIT_TIMEOUT` | `600` | Max seconds for one init pass |
| `NH_WATCHDOG` / `NH_WATCHDOG_STUCK` | `yes` / `600` | Restart a wedged viewer (no output **and** no CPU) |
| `NH_RENDER_TO_TERMINAL` | `yes` | `no` disables the live viewer dashboard in the logs |
| `PTY_COLS` / `PTY_ROWS` | `120` / `30` | Terminal size handed to the viewer |
| `VNC` / `VNC_PW` / `VNC_PORT` / `NO_VNC_PW` | off / — / `5901` / off | Optional x11vnc mirror of the display |

### Supervisor

| Env var | Default | Description |
| :--- | :--- | :--- |
| `SUPERVISOR_DELAY` | `10` | Base restart cooldown (s) |
| `SUPERVISOR_MAX_DELAY` | `120` | Exponential backoff ceiling (s) |
| `SUPERVISOR_PARK_AFTER` / `SUPERVISOR_PARK_SECS` | `10` / `300` | Park a crash-looping slot, then retry |
| `SUPERVISOR_TICK` | `0.5` | Main loop tick (s) |
| `NINEHITS_CHECK_INTERVAL` | `30` | Seconds between expensive per-slot checks |
| `NH_MAX_MEMORY_MB` / `NINEHITS_MAX_MEMORY_MB` | `0` | Restart the viewer when its process tree exceeds this (MB) |
| `NH_MAX_CHILDREN` / `NINEHITS_MAX_CHILDREN` | `0` | Fork-bomb guard |
| `NH_CPU_SHARES` / `NH_MEM_LIMIT_MB` | *none* | cgroup v1 CPU weight / legacy memory-cap alias |
| `LOG_DIR` | `/logs` | `9hits.log`, `health.log`, `supervisor.log` |
| `SLOT_LOG_MAX_BYTES` / `SLOT_LOG_BACKUPS` | `10485760` / `2` | Per-slot log rotation |
| `SUPERVISOR_DISABLED` | off | Re-exec the legacy `/start.sh` layout instead of the Python supervisor |

---

## Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `User not found!` in the log | `ACCESS_KEY` is wrong/empty |
| `Pool error: The public pool is closed!` | Configure your own pool (`EX_PROXY_URL`) or use a proxy list |
| Init pass exits with code `124` | Raise `INIT_TIMEOUT` (throttled CPU) |
| Run pass dies within seconds with code `133` | Single-process Chromium is unsupported on that host — keep `LOW_MEMORY=balanced` / `NH_SP=no` |
| Container OOM-killed | Lower the session count, set `NH_MAX_MEMORY_MB=400`, use a bigger plan |
| Viewer silent and idle | The wedge watchdog restarts it after `NH_WATCHDOG_STUCK` seconds |
| Nothing on `/` | Check `logs/supervisor.log` and `logs/health.log` |
