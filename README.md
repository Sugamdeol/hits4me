---
title: 9Hits Viewer v6
emoji: 🌐
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 10000
pinned: false
---

# hits4me

Run the **9Hits Viewer v6** ([9hitste/appv6](https://hub.docker.com/r/9hitste/appv6)) on
any cloud container platform (**Render, Koyeb, Hugging Face Spaces, Fly.io, Railway, Zeabur, Oracle Cloud, VPS**) as a lightweight service with an integrated **`/health` endpoint** for uptime monitoring.

---

## Why the wrapper?

1. **Headless TTY Emulation** — 9Hits Viewer requires a pseudo-TTY dashboard and exits immediately in background container environments without one. `run_pty.py` wraps the viewer in a PTY so it stays running anywhere.
2. **HTTP Health Server** — Most free hosting platforms require an HTTP service on `0.0.0.0:$PORT` to pass deployment health checks. `health_server.py` answers `GET /health` immediately on boot.
3. **Automated Supervision** — Automatically relaunches the viewer if it crashes or reaches its `--reset-interval`.
4. **Proxy & System Session Flexibility** — Supports static proxy lists, dynamic Webshare download URLs, custom 9Hits pools, and single-click zero-proxy **system sessions** on distinct cloud IPs.

| File | Job |
| --- | --- |
| `start.sh` | Builds `/nh.sh` flags from env vars, supervises viewer lifecycle, redacts secrets |
| `run_pty.py` | Allocates pseudo-TTY for the viewer, mirrors dashboard to stdout |
| `health_server.py` | Pure stdlib HTTP server on `0.0.0.0:$PORT` (`/health`, `/healthz`, `/ping`) |
| `fetch_proxy_list.py` | Auto-downloads and parses proxy lists on boot from `BULK_ADD_PROXY_LIST_URL` |
| `webshare_to_9hits.py` | CLI converter for Webshare list files to 9Hits format |

---

## Multi-Platform Strategy: Free System Sessions with Different IPs

Instead of dealing with shared/overloaded free proxy lists (which often hit `Auth: Duplicate USER on IP`), you can run **independent system sessions across multiple free cloud hosting platforms**. 

Each platform assigns a **completely different, dedicated outbound IP address** from its datacenter network:

| Platform | Free Tier Specifications | Default Region / Egress IP | Configuration |
| :--- | :--- | :--- | :--- |
| **Hugging Face Spaces** | **16 GB RAM**, 2 vCPU (24/7 Always On) | US / EU (AWS egress) | Native Docker Space |
| **Koyeb** | **512 MB RAM**, 0.1 vCPU (Free Nano) | Frankfurt, Washington D.C., Singapore | `koyeb.yaml` |
| **Render** | **512 MB RAM**, 750 free hrs/mo | Oregon, Ohio, Frankfurt, Singapore | `render.yaml` |
| **Fly.io** | Up to 3 shared VMs (256 MB RAM) | 30+ Global Edge Regions | `fly.toml` |
| **Zeabur** | Free trial / Starter credits | Global Edge | `zeabur.json` |
| **Railway** | Starter / Trial credits | US West / EU | `railway.json` |
| **Oracle Cloud Free** | **24 GB RAM**, 4 ARM cores (Always Free) | Your Home Region | `docker-compose.yml` |

---

## Deployment Guides

### 1. Hugging Face Spaces (Generous 16 GB RAM — Best Free Tier)
1. Go to [Hugging Face Spaces](https://huggingface.co/spaces) → **Create new Space**.
2. Space Name: `hits4me` (or any name).
3. Select **Docker** → **Blank**.
4. Visibility: **Public** or **Private**.
5. Once created, push this repository or connect it to your GitHub repo.
6. In **Settings** → **Variables and secrets**, add:
   - Secret `ACCESS_KEY`: `<your-9hits-access-key>`
   - Variable `SYSTEM_SESSION`: `yes`
   - Variable `CLEAR_ALL_SESSIONS`: `yes`
   - Variable `SESSION_NOTE`: `hf-system`
   - Variable `NOTE`: `huggingface`
   - Variable `RESET_INTERVAL`: `2h`

---

### 2. Koyeb (1 Free Always-On Nano Service)
1. Go to [Koyeb Dashboard](https://app.koyeb.com/) → **Create App**.
2. Select **GitHub** → select `hits4me`.
3. Deployment method: **Dockerfile**.
4. Service Type: **Web Service**, Instance Type: **Free (Nano)**.
5. In **Environment Variables**:
   - `ACCESS_KEY`: `<your-9hits-access-key>`
   - `SYSTEM_SESSION`: `yes`
   - `CLEAR_ALL_SESSIONS`: `yes`
   - `SESSION_NOTE`: `koyeb-system`
   - `NOTE`: `koyeb`
   - `RESET_INTERVAL`: `2h`
   - `PORT`: `10000`
6. Health Check: Path `/health`, Port `10000`. Click **Deploy**.

---

### 3. Render (Blueprint or Web Service)
1. Go to [Render Dashboard](https://dashboard.render.com/) → **New → Blueprint** (connect this repo).
2. Or create **New → Web Service** → Docker runtime → Free instance.
3. Paste `ACCESS_KEY` when prompted.
4. Environment settings for a System Session:
   ```env
   ACCESS_KEY=<your-key>
   SYSTEM_SESSION=yes
   CLEAR_ALL_SESSIONS=yes
   SESSION_NOTE=render-system
   NOTE=render-oregon
   RESET_INTERVAL=2h
   ```
5. *(Optional)* Create another service in a different region (e.g. Frankfurt / Ohio) for an additional distinct IP.

---

### 4. Fly.io
1. Install the `flyctl` CLI: `curl -L https://fly.io/install.sh | sh`
2. Run `fly launch` in the repository directory (it will detect `fly.toml` and `Dockerfile`).
3. Set your secret: `fly secrets set ACCESS_KEY="<your-9hits-access-key>"`
4. Deploy: `fly deploy`

---

### 5. Oracle Cloud / VPS / Local Docker
For persistent 24/7 self-hosted instances:
```bash
# Clone and configure
git clone https://github.com/Sugamdeol/hits4me.git
cd hits4me
cp .env.example .env

# Edit .env and fill in ACCESS_KEY + SYSTEM_SESSION=yes
nano .env

# Launch
docker compose up -d
```

---

## Proxy Configuration (Alternative Multi-Session on 1 Container)

> ⚠️ **IMPORTANT: The 9Hits public proxy pool is CLOSED.**
> Do not use `EX_PROXY_SESSIONS` without your own custom pool or proxy list.

If you prefer running multiple sessions inside a single container using proxies:

* **Option A — Static Bulk Proxy List (`BULK_ADD_PROXY_LIST`)**
  ```env
  BULK_ADD_PROXY_TYPE=socks5
  BULK_ADD_PROXY_LIST=1.2.3.4:1080;user;pass|1.2.3.5:1080;user;pass|1.2.3.6:1080;user;pass
  ```

* **Option A+ — Webshare Dynamic Download Link (`BULK_ADD_PROXY_LIST_URL`)**
  ```env
  BULK_ADD_PROXY_LIST_URL=https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN/-/any/username/direct/-/
  ```

* **Option B — Custom Pool (`EX_PROXY_URL`)**
  Create your pool at [dash.9hits.com/pool](https://dash.9hits.com/pool) and set:
  ```env
  EX_PROXY_SESSIONS=5
  EX_PROXY_URL=https://dash.9hits.com/pool/YOUR_POOL_KEY
  ```

---

## Environment Variables Reference

| Env var | `/nh.sh` flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `ACCESS_KEY` | `--access-key` | *required* | From [panel.9hits.com/user/profile](https://panel.9hits.com/user/profile) |
| `SYSTEM_SESSION` | `--system-session` | `no` | `yes`/`no` — runs direct session on instance IP |
| `CLEAR_ALL_SESSIONS` | `--clear-all-sessions` | `yes` | Wipes stale sessions on boot |
| `BULK_ADD_PROXY_LIST_URL` | — | *none* | URL to download proxy list on boot |
| `BULK_ADD_PROXY_LIST` | `--bulk-add-proxy-list` | *none* | Pipe-delimited proxy list (`ip:port;user;pass\|...`) |
| `BULK_ADD_PROXY_TYPE` | `--bulk-add-proxy-type` | `socks5` | `socks5`, `http`, `socks4`, `ssh` |
| `EX_PROXY_SESSIONS` | `--ex-proxy-sessions` | *none* | Number of pool sessions (requires `EX_PROXY_URL`) |
| `EX_PROXY_URL` | `--ex-proxy-url` | *none* | Pool URL from `dash.9hits.com/pool` |
| `SESSION_NOTE` | `--session-note` | `my-proxies`| Session label in 9Hits panel |
| `NOTE` | `--note` | `render` | Machine label in 9Hits panel |
| `HIDE_BROWSER` | `--hide-browser` | `yes` | Run headless |
| `ALLOW_POPUPS` | `--allow-popups` | `no` | Popups toggle (keep `no` to save RAM/BW) |
| `ALLOW_ADULT` | `--allow-adult` | `no` | Adult campaigns toggle |
| `ALLOW_CRYPTO` | `--allow-crypto` | `no` | Crypto mining campaigns toggle |
| `CACHE_LIMIT` | `--cache-limit` | `0` | Disk cache limit (`0` disables disk cache) |
| `RESET_INTERVAL` | `--reset-interval` | `2h` | Periodic viewer reset (`2h`, `6h`, `30m`) |
| `PORT` | — | `10000` | Port for `/health` endpoint |
| `SUPERVISOR_DELAY` | — | `10` | Seconds before relaunching exited viewer |

---

## Health Monitoring & Uptime Bots

Every instance exposes a lightweight status endpoint:

```
GET https://<your-service>/health
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
  "uptime_seconds": 3600
}
```

Point any free uptime monitor (**UptimeRobot, Better Stack, Cron-job.org, Kuma**) to ping `https://<your-app>/health` every **5 to 10 minutes** to keep free-tier instances active and prevent sleep timeouts.

---

## Troubleshooting

* **`Auth: Duplicate USER on IP [x.x.x.x]`** — Another 9Hits user is already using that public/shared proxy IP. Switch to a system session on a dedicated cloud provider, refresh your Webshare list, or use private proxies.
* **`Auth: Duplicate SESSION on IP [x.x.x.x]`** — Multiple sessions from your account on the same IP. Ensure `SYSTEM_SESSION=no` when using proxies, or enable `CLEAR_ALL_SESSIONS=yes` to clear lingering connections.
* **`Pool error: The public pool is closed!`** — Set `EX_PROXY_SESSIONS=0` (or unset it) and use `BULK_ADD_PROXY_LIST` / `BULK_ADD_PROXY_LIST_URL`, or provide your own custom pool via `EX_PROXY_URL`.
* **`User not found!`** — `ACCESS_KEY` is incorrect or missing.
