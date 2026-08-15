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
| `fetch_proxy_list.py` | Automatically fetches and converts proxy lists from a URL on boot |
| `webshare_to_9hits.py` | CLI tool to convert downloaded Webshare proxy lists to 9Hits format |

## Multi-Session & Proxy Setup (5–6 Sessions on 512 MB)

> ⚠️ **IMPORTANT: The 9Hits public proxy pool is CLOSED.**
> If you set `EX_PROXY_SESSIONS` without specifying your own pool URL or proxy list,
> every session will fail with `Pool error: The public pool is closed!`.

To earn points effectively, you should run **5–6 concurrent sessions**, each assigned
to a **different, unique IP address**.

### Proxy Options

* **Option A — Static Bulk Proxy List (`BULK_ADD_PROXY_LIST`)**
  Supply a list of proxies formatted as `server:port;user;pass` separated by pipe (`|`):
  ```env
  BULK_ADD_PROXY_TYPE=socks5
  BULK_ADD_PROXY_LIST=1.2.3.4:1080;user;pass|1.2.3.5:1080;user;pass|1.2.3.6:1080;user;pass|1.2.3.7:1080;user;pass|1.2.3.8:1080;user;pass
  ```
  Each entry creates an independent session using that proxy.

* **Option A+ — Dynamic Proxy Fetch (`BULK_ADD_PROXY_LIST_URL`) [Recommended]**
  Set `BULK_ADD_PROXY_LIST_URL` to your proxy provider's raw text download URL (e.g. Webshare).
  On startup, the container automatically downloads the proxy list, converts `ip:port:user:pass`
  to 9Hits format, defaults `BULK_ADD_PROXY_TYPE` to `socks5`, and boots your sessions.
  ```env
  BULK_ADD_PROXY_LIST_URL=https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN/-/any/username/direct/-/
  ```
  This keeps proxy credentials out of Git and your configuration simple.

* **Option B — Your Own 9Hits Proxy Pool (`EX_PROXY_URL`)**
  Create and manage a custom proxy pool at [dash.9hits.com/pool](https://dash.9hits.com/pool),
  then configure:
  ```env
  EX_PROXY_SESSIONS=5
  EX_PROXY_URL=https://dash.9hits.com/pool/YOUR_POOL_KEY
  ```

### Rules & Best Practices

1. **Unique IP per session:** 9Hits only rewards one active session per IP. Duplicate IPs produce no extra earnings.
2. **Disable system session on Render (`SYSTEM_SESSION=no`):** Render uses shared outbound egress IPs across instances. Running a system session on Render almost always fails with `Auth: Duplicate SESSION on IP`.
3. **Clear sessions on startup (`CLEAR_ALL_SESSIONS=yes`):** Wipes stale sessions left from previous container runs so new sessions connect immediately.
4. **Proxy quality drives earnings:** Low-latency, dedicated/semi-dedicated datacenter or residential proxies earn significantly more than overloaded public proxies.
5. **RAM reality check (512 MB Free Tier):** While 10 idle sessions can start on 512 MB RAM, active browsing loads heavy web pages and can cause Out-Of-Memory (OOM) kills. **5–6 sessions is the sweet spot** for Render's 512 MB free tier. Monitor `/health` restart counts if scaling up.

---

### Free IPs: Use Your Webshare Proxies

Webshare offers a free tier with 10 datacenter proxy IPs (format: `ip:port:user:pass`).

#### 1. Setup with `BULK_ADD_PROXY_LIST_URL`
In the Webshare dashboard (Proxy -> List -> Download), copy your direct download link and set it as `BULK_ADD_PROXY_LIST_URL` in your Render environment variables.

#### 2. Manual Conversion with `webshare_to_9hits.py`
If you downloaded your proxy list file:
```bash
# Convert a file
python3 webshare_to_9hits.py webshare_proxies.txt

# Or pipe directly from curl
curl -s "https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN/-/any/username/direct/-/" | python3 webshare_to_9hits.py
```
Output:
```env
# 10 proxies converted
BULK_ADD_PROXY_TYPE=socks5
BULK_ADD_PROXY_LIST=31.59.20.176:6754;plddclay;0k3zxdmptdz9|...
```

#### 3. Test Proxies with Curl
Verify that a proxy works before deploying:
```bash
curl -x socks5h://USER:PASS@IP:PORT https://api.ipify.org
```

#### 4. Limitations & Caveats
* **1 GB/month shared bandwidth limit:** Free Webshare accounts share 1 GB of bandwidth across all 10 IPs. This cap limits total monthly traffic, not the proxy count. To conserve bandwidth:
  * Set `ALLOW_POPUPS=no`
  * Set `ALLOW_ADULT=no`
  * Set `ALLOW_CRYPTO=no`
  * Set `CACHE_LIMIT=0`
* **Shared IPs & Duplicate Sessions:** Free tier IPs are shared among multiple Webshare users. If another 9Hits user is active on the same IP, you will see `Auth: Duplicate SESSION on IP`. Drop or replace that proxy entry.
* **Other free proxy sources:** Public free proxy lists (e.g. FreeProxyList, Spys.one, Geonode, ProxyScrape) are usually dead, overloaded, or shared by hundreds of users within minutes. Webshare or a small paid proxy pool is substantially more stable.

---

## Deploy on Render

### Option A — Blueprint (recommended)

1. Push this repo to GitHub.
2. Render Dashboard → **New → Blueprint** → connect the repo → **Apply**.
   `render.yaml` is already set up (Docker runtime, free plan, health check on `/health`).
3. When prompted, paste your 9Hits **access key** (from
   [panel.9hits.com/user/profile](https://panel.9hits.com/user/profile)).
4. Add your proxy configuration under the service's Environment tab (e.g. `BULK_ADD_PROXY_LIST_URL` or `BULK_ADD_PROXY_LIST`).
5. Wait for the deploy to go live (first boot downloads the viewer, so it can take
   a few minutes — the health endpoint is up immediately, so the deploy still succeeds).

### Option B — Manual

1. Render Dashboard → **New → Web Service** → connect this repo.
2. Runtime: **Docker** (it auto-detects the `Dockerfile`).
3. Instance type: Free (or bigger if you run many sessions).
4. Health check path: `/health`.
5. Leave the **Docker command** field empty (the image entrypoint does
   everything; anything you type there is appended to `/nh.sh` as extra flags).
6. Add environment variables (at minimum `ACCESS_KEY`; see table below).

---

## Environment variables

All viewer options from the upstream image are available as env vars (any
arguments you pass as the start command are appended to `/nh.sh` verbatim, too).

| Env var | `/nh.sh` flag | Notes |
| --- | --- | --- |
| `ACCESS_KEY` | `--access-key` | **Required.** From [panel.9hits.com/user/profile](https://panel.9hits.com/user/profile) |
| `CLEAR_ALL_SESSIONS` | `--clear-all-sessions` | Set `yes` (recommended on Render to wipe stale sessions on startup) |
| `SYSTEM_SESSION` | `--system-session` | `yes`/`no` — set to `no` on Render (shared egress IP causes auth duplicate errors) |
| `BULK_ADD_PROXY_LIST_URL` | — | Raw URL to download proxy list on boot (auto-converts `ip:port:user:pass`) |
| `BULK_ADD_PROXY_LIST` | `--bulk-add-proxy-list` | Static list: `server:port;user;pass\|server2:port;user2;pass2` |
| `BULK_ADD_PROXY_TYPE` | `--bulk-add-proxy-type` | `http`, `socks4`, `socks5` (default), `ssh` |
| `EX_PROXY_SESSIONS` | `--ex-proxy-sessions` | External proxy session count (requires `EX_PROXY_URL` or `BULK_ADD_PROXY_LIST`; public pool is closed) |
| `EX_PROXY_URL` | `--ex-proxy-url` | Custom proxy pool URL from [dash.9hits.com/pool](https://dash.9hits.com/pool) |
| `SESSION_NOTE` | `--session-note` | Note applied to created sessions (e.g. `my-proxies`) |
| `NOTE` | `--note` | Note for this machine (e.g. `render`) |
| `ALLOW_POPUPS` / `ALLOW_ADULT` / `ALLOW_CRYPTO` | `--allow-*` | `yes` or `no` (set `no` to conserve memory and bandwidth) |
| `HIDE_BROWSER` | `--hide-browser` | `yes` or `no` (keep `yes` for headless operation) |
| `HIDE_COLUMNS` | `--hide-columns` | e.g. `quality,points` |
| `CACHE_PATH` / `CACHE_LIMIT` | `--cache-path` / `--cache-limit` | e.g. `CACHE_LIMIT=0` to disable disk caching |
| `INSTALL_DIR` | `--install-dir` | Default `/etc` |
| `DEFAULT_DL` | `--default-dl` | Custom viewer download URL |
| `RE_INSTALL` | `--re-install` | `yes` to force re-download |
| `RESTART_DELAY` | `--restart-delay` | Viewer's own restart delay, default `5` |
| `RESET_INTERVAL` | `--reset-interval` | Periodic graceful reset, e.g. `2h`, `6h`, `30m` |
| `VNC` / `VNC_PW` / `NO_VNC_PW` / `VNC_PORT` | `--vnc*` | VNC is **not reachable on Render** (only `$PORT` is exposed) — useful for local Docker only |
| `EXTRA_ARGS` | — | Raw extra flags, e.g. `--hide-columns=quality,points` |
| `PORT` | — | Health endpoint port. Render sets it automatically (default `10000`) |
| `SUPERVISOR_DELAY` | — | Seconds between viewer relaunches, default `10` |

---

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

---

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

---

## Local testing

```bash
# one-off
docker build -t hits4me-9hits .
docker run -d --shm-size=2g -p 10000:10000 \
  -e ACCESS_KEY=<your-key> -e SYSTEM_SESSION=no \
  -e CLEAR_ALL_SESSIONS=yes \
  -e BULK_ADD_PROXY_LIST="1.2.3.4:1080;user;pass|1.2.3.5:1080;user;pass" \
  hits4me-9hits
curl http://localhost:10000/health   # -> {"status": "ok", ...}
docker logs -f <container-id>        # live dashboard (run_pty allocates the TTY, so plain -d works)

# or with compose
cp .env.example .env   # set ACCESS_KEY and proxy settings
docker compose up --build
```

---

## Troubleshooting

* **`Pool error: The public pool is closed!`** — The 9Hits public proxy pool is closed.
  If `EX_PROXY_SESSIONS` is set without `EX_PROXY_URL`, the viewer tries to use the closed
  public pool. Fix: set `EX_PROXY_SESSIONS=0` (or leave unset) and use `BULK_ADD_PROXY_LIST`
  or `BULK_ADD_PROXY_LIST_URL` (Option A / A+), or set `EX_PROXY_URL` to your own pool
  created at [dash.9hits.com/pool](https://dash.9hits.com/pool) (Option B).
* **`Auth: Duplicate SESSION on IP`** — Occurs when 9Hits detects multiple sessions on
  the same IP address. On Render, the host egress IP is shared and stale sessions linger.
  Fix: set `SYSTEM_SESSION=no` and `CLEAR_ALL_SESSIONS=yes`. If using external proxies,
  ensure each proxy IP in your list is unique and remove any shared proxy IP that collides
  with another user.
* **Dashboard says `User not found!`** — `ACCESS_KEY` is wrong or empty. Fix the
  env var on Render (Environment tab) and redeploy.
* **Viewer crash-looping** — check `restarts` in the `/health` JSON and the logs.
  Common causes: wrong access key, too many sessions for the instance size
  (Render free = 512 MB RAM / 0.1 CPU — run 5–6 sessions max; paid plans handle more),
  or `/dev/shm` limits (Render doesn't expose `--shm-size`; if Chrome dies with shm errors,
  reduce sessions or move to a dedicated VPS).
* **`/health` is 503** — the supervisor died; Render restarts the service
  automatically.
* **Service spins down overnight** — your uptime bot interval is ≥15 min, or it
  stopped pinging.
* **Render account/TOS** — 9Hits generates automated web traffic; make sure
  that's acceptable under your VPS/host provider's terms before scaling up.
