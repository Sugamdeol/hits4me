---
title: 9Hits Viewer v6 + FeelingSurf Viewer
emoji: 🌐
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
---

# hits4me

Run the **9Hits Viewer v6** ([9hitste/appv6](https://hub.docker.com/r/9hitste/appv6)) and the
**FeelingSurf Viewer** ([feelingsurf/viewer](https://hub.docker.com/r/feelingsurf/viewer)) on
**100% Free Cloud Hosting Platforms** (**Hugging Face Spaces, Koyeb, Render, Oracle Cloud Always Free, Fly.io, Railway, Zeabur**) as lightweight services with integrated **`/health` endpoints** for uptime monitoring.

* **9Hits Viewer** — sophisticated traffic-exchange viewer with proxy/proxy-pool/system-session options.
* **FeelingSurf Viewer** — drop-in autosurf viewer; one container, one env var (`access_token`), no extra configuration.

---

## Free Multi-Platform Strategy: Free System Sessions on Clean Cloud IPs

Rather than relying on shared free proxy lists (which frequently encounter `Auth: Duplicate USER on IP` errors from other 9Hits members), the most reliable method is to run **one system session on each free cloud hosting platform**.

Each provider assigns an isolated, clean datacenter outbound IP address:

| Platform | Free Tier Type | Resources | Outbound Region / IP | Deployment Method |
| :--- | :--- | :--- | :--- | :--- |
| **Hugging Face Spaces** | **100% Free (Gradio SDK)** | **16 GB RAM**, 2 vCPU | US / EU (AWS) | Free Gradio Space (`app.py`) |
| **Koyeb** | Deploy this repository Dockerfile and set both secrets. | Uses `koyeb.yaml`; one service runs both viewers. |
| **Render** | **100% Free (Web Service)** | 512 MB RAM, 750 hrs/mo | Oregon, Ohio, Frankfurt, Singapore | Docker (`render.yaml`) |
| **Oracle Cloud Always Free** | **100% Forever Free** | **24 GB RAM**, 4 ARM cores | Your chosen home region | `docker compose up -d` |
| **Fly.io** | Deploy this repository Dockerfile and set both secrets. | Uses `fly.toml`; one service runs both viewers. |
| **Zeabur / Railway** | Free Trial / Starter credits | 512 MB RAM | Global Edge | `zeabur.json` / `railway.json` |

---

## Deployment Guides for 100% Free Platforms

### 1. Hugging Face Spaces (100% FREE — Gradio SDK, 16 GB RAM)
> 💡 *Note: Docker Spaces require a paid subscription on HF, but **Gradio Spaces are 100% Free** with 16 GB RAM! This repo includes `app.py` to run seamlessly on the free Gradio SDK.*

1. Go to [huggingface.co/spaces](https://huggingface.co/spaces) → **Create new Space**.
2. Space Name: `hits4me-viewer` (or any name).
3. Select **Gradio** SDK (leave hardware as **Free 2 vCPU · 16 GB RAM**).
4. Connect or duplicate this repository.
5. In **Settings** → **Variables and secrets**, add:
   - Secret `ACCESS_KEY`: `<your-9hits-access-key>`
   - Variable `SYSTEM_SESSION`: `yes`
   - Variable `CLEAR_ALL_SESSIONS`: `yes`
   - Variable `SESSION_NOTE`: `hf-system`
   - Variable `NOTE`: `huggingface`
   - Variable `RESET_INTERVAL`: `2h`
6. The Space will build and launch a live status dashboard while running the 9Hits Viewer.

---

### 2. Koyeb (100% Free Docker Nano Instance)
1. Go to [app.koyeb.com](https://app.koyeb.com/) → **Create App**.
2. Select **GitHub** → select `hits4me`.
3. Choose **Dockerfile** deployment and the **Free (Nano)** instance type.
4. Set Environment Variables:
   - `ACCESS_KEY`: `<your-9hits-access-key>`
   - `SYSTEM_SESSION`: `yes`
   - `CLEAR_ALL_SESSIONS`: `yes`
   - `SESSION_NOTE`: `koyeb-system`
   - `NOTE`: `koyeb`
   - `RESET_INTERVAL`: `2h`
   - `PORT`: `10000`
5. Health Check: Path `/health`, Port `10000`. Click **Deploy**.

---

### 3. Render (100% Free Docker Web Service)
The existing **`9hits-viewer`** web service ([dashboard](https://dashboard.render.com/web/srv-da09a7dbedkc73a829cg)) runs **both** the 9Hits and FeelingSurf viewers in one Docker container. Applying this repo's Blueprint updates that same service. It does **not** create a new Render service named `hits4me-viewers`.

1. In [Render Dashboard](https://dashboard.render.com/) apply the Blueprint for this repo so it updates the existing **`9hits-viewer`** service (or open that service and redeploy).
2. Or, only if `9hits-viewer` does not already exist, create **New → Web Service** → Docker runtime → Free tier and name it exactly `9hits-viewer`.
3. Paste `ACCESS_KEY` when prompted. `ACCESS_TOKEN` is preconfigured in `render.yaml`.
4. Set Environment Variables:
   ```env
   ACCESS_KEY=<your-key>
   SYSTEM_SESSION=yes
   CLEAR_ALL_SESSIONS=yes
   SESSION_NOTE=render-system
   NOTE=render-oregon
   RESET_INTERVAL=2h
   ```
5. *(Optional)* Deploy another free service in a different region (e.g. Frankfurt or Ohio) to get an extra unique IP address.

---

### 4. Oracle Cloud Always Free (24 GB RAM / 4 CPUs Forever Free)
Oracle Cloud provides the most generous free tier in the cloud industry (4 ARM vCPUs + 24 GB RAM, plus 2 x86 AMD instances):
```bash
git clone https://github.com/Sugamdeol/hits4me.git && cd hits4me
cp .env.example .env

# Edit .env and set ACCESS_KEY (9Hits) AND/OR ACCESS_TOKEN (FeelingSurf)
nano .env

# Start BOTH viewers in one container/deployment:
docker compose up -d
```

---

### 5. Fly.io
1. Install `flyctl`: `curl -L https://fly.io/install.sh | sh`
2. Run `fly launch` in this directory (uses `fly.toml`).
3. Set both secrets: `fly secrets set ACCESS_KEY="<your-9hits-key>" ACCESS_TOKEN="<your-feelingsurf-token>"`.
4. Deploy: `fly deploy`.

---

## FeelingSurf Viewer (additional autosurf viewer)

Hit "too many free platforms, no proxies" with 9Hits? Same repo can run the **FeelingSurf Viewer** ([feelingsurf/viewer:stable](https://hub.docker.com/r/feelingsurf/viewer)) alongside (or instead of) the 9Hits viewer. It's a self-contained Electron-style app — only `access_token` env var, no proxy-aware flags, no dashboards to configure.

⚠️ **Disclaimer:** *Never share your FeelingSurf `access_token` — it grants full access to your account.*

### Get an access token
1. Register at [feelingsurf.fr](https://www.feelingsurf.fr/) and finish email confirmation.
2. Go to **Member area → Profile / Settings → API / Access Token** and generate one. (The token is a long opaque string — treat it like a password.)

### 1. `docker run` (the official quick start)
```bash
docker run -d \
  -e access_token=YOUR_ACCESS_TOKEN_HERE \
  --tmpfs /tmp \
  --tmpfs /dev/shm \
  feelingsurf/viewer:stable
```
That's it — no apt deps, no flags, no proxy list to maintain. The container starts Xvfb internally, launches the viewer, and exposes its UI on **`http://<host>:3000/`**. The image's built-in Docker `HEALTHCHECK` pings that endpoint every minute.

### 2. `docker compose` (same container as 9Hits)
The repository Dockerfile installs FeelingSurf alongside 9Hits. One Compose service and one deploy now run both viewers:

```bash
cp .env.example .env
# Fill in ACCESS_KEY (9Hits) AND/OR ACCESS_TOKEN (FeelingSurf), e.g.:
#   ACCESS_KEY=...
#   ACCESS_TOKEN=...

docker compose up -d                                # both viewers, one container
curl http://localhost:10000/health                  # combined status
# Set FEELINGSURF_ENABLED=no only when you intentionally want 9Hits alone.
```

Service map:
| Container | Processes | Health |
| :--- | :--- | :--- |
| `viewers` | supervised 9Hits + supervised FeelingSurf | combined `GET /health` on port `10000`; FeelingSurf also listens internally on `3000` |

The platform deploys one image once. Both viewers share the container network and `/dev/shm`, while independent supervisors restart either process if it exits. Budget at least **4 GB RAM and 2 CPUs** for reliable operation; small free instances may run out of memory.

### 3. Free cloud platforms

The combined repository `Dockerfile` is used on cloud platforms. Configure both `ACCESS_KEY` and `ACCESS_TOKEN` on the same service.

| Platform | Deployment | Notes |
| :--- | :--- | :--- |
| **Oracle Cloud Always Free** | use `docker compose up -d` | One combined service; the 24 GB tier has ample memory. |
| **Render** | Blueprint (`render.yaml`) updates the existing **`9hits-viewer`** web service so it runs both viewers. `ACCESS_KEY` is prompted and `ACCESS_TOKEN` is preconfigured. | The free 512 MB plan is unlikely to have enough RAM; use a plan with at least 4 GB. |
| **Koyeb** | Deploy this repository Dockerfile and set both secrets. | Uses `koyeb.yaml`; one service runs both viewers. |
| **Fly.io** | Deploy this repository Dockerfile and set both secrets. | Uses `fly.toml`; one service runs both viewers. |
| **Railway** | Deploy this repository Dockerfile and set both secrets. | Uses `railway.json`; one service runs both viewers. |
| **Zeabur** | Deploy this repository Dockerfile and set both secrets. | Uses `zeabur.json`; one service runs both viewers. |
| **Hugging Face Spaces** | Docker Space; image `feelingsurf/viewer:stable` (paid Docker Space required). | For the 100% free Gradio Space path, run the **9Hits** viewer (`app.py`) and use a second space / external host for FeelingSurf. |

Recommended platform sizing per FeelingSurf container (per the [official repo](https://github.com/feelingsurf/docker-viewer)):

* **RAM:** ~2 GB
* **CPU:** ~2 cores  
* **tmpfs:** `/tmp` and `/dev/shm`
* **Port:** `3000` (in-viewer HTTP endpoint + Docker healthcheck)

### 4. Why use FeelingSurf alongside 9Hits?

* **Independent earnings / IP reputation:** even if a 9Hits proxy tripwire fires (`Auth: Duplicate USER on IP`), FeelingSurf keeps earning on the same cloud box.
* **Different credit economy:** FeelingSurf awards credits independent of 9Hits, so you can pause one to focus budget on the other without affecting your account elsewhere.
* **Trivial setup:** one env var, no proxy gymnastics.

---

## Proxy Setup (Running Multiple Sessions on 1 Machine)

> ⚠️ **IMPORTANT: The 9Hits public proxy pool is CLOSED.**
> Do not use `EX_PROXY_SESSIONS` without your own custom pool or proxy list.

If you have dedicated or private proxies:

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
  Configure at [dash.9hits.com/pool](https://dash.9hits.com/pool):
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
