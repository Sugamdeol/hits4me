---
title: 9Hits Viewer v6
emoji: 🌐
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
---

# hits4me

Run the **9Hits Viewer v6** ([9hitste/appv6](https://hub.docker.com/r/9hitste/appv6)) on
**100% Free Cloud Hosting Platforms** (**Hugging Face Spaces, Koyeb, Render, Oracle Cloud Always Free, Fly.io, Railway, Zeabur**) as a lightweight service with an integrated **`/health` endpoint** for uptime monitoring.

Two ways to run it — pick whichever fits your host:

| | **Docker** | **No Docker** (`run_native.py`) |
| :--- | :--- | :--- |
| Entrypoint | `Dockerfile` → `start.sh` | `python3 run_native.py` |
| Needs | Docker daemon | Python 3.6+ only (stdlib) |
| Needs root / systemd? | root for the daemon | **No** |
| Use it on | Render, Koyeb, Fly, Railway, Zeabur, Oracle | HF Gradio Spaces, plain VPS, WSL, Colab, LXC, shared boxes |

Both paths share the same environment variables, the same `/health` JSON and the
same `viewer_config.py` flag mapping, so you can move between them freely.

---

## 🐍 No Docker? Use the native Python runner

`run_native.py` is a **pure-stdlib, Docker-free** replacement for the container.
It needs **neither root nor systemd** (unlike the official `install.sh`), which
makes it the right choice on Hugging Face free Gradio Spaces, unprivileged VPS
accounts, WSL, Colab, LXC containers and any host where Docker is unavailable.

It does everything the container did, in one process tree:

1. verifies the platform (Linux x86_64, glibc ≥ 2.31)
2. downloads + extracts the official viewer tarball (cached after the first run)
3. checks the required shared libraries, and can install them (`--install-deps`)
4. starts its own **Xvfb** virtual display — no desktop needed
5. runs the one-time init pass that applies your key, sessions and proxies
6. supervises the viewer under a **pseudo-TTY**, relaunching it whenever it exits
7. serves the same **`/health`** endpoint on `0.0.0.0:$PORT` for uptime bots

### Quick start

```bash
git clone https://github.com/Sugamdeol/hits4me.git && cd hits4me

# 1. Install system dependencies (once).
#    Skip this if you pass --install-deps below and can run as root.
sudo apt-get install -y --no-install-recommends \
  xvfb libnss3 libgtk-3-0 libgbm1 libasound2 libatk1.0-0 libatk-bridge2.0-0 \
  libatspi2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
  libxfixes3 libxrandr2 libxss1 libxtst6 libatomic1 bzip2

# 2. Run it (no Docker, no root, no systemd)
export ACCESS_KEY=<your-9hits-access-key>
python3 run_native.py --system-session
```

Or use the wrapper, which loads `.env` for you — the *same* `.env` the Docker
Compose setup uses:

```bash
cp .env.example .env && nano .env     # set ACCESS_KEY
./run_native.sh --system-session
```

If you *are* root and want the dependencies handled automatically:

```bash
sudo python3 run_native.py --install-deps --access-key=<key> --system-session
```

The runner prints the exact `apt-get`/`yum` command for your distro when
something is missing, so a non-root user can hand it to an admin.

### Command-line options

Every environment variable in the [reference table](#environment-variables-reference)
also works as a flag, and **the flag wins**:

```bash
python3 run_native.py \
  --access-key=<key> \
  --system-session \
  --session-note=vps-system \
  --note=my-vps \
  --reset-interval=2h \
  --port=10000
```

Runner-specific options:

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--install-dir` | `/opt/9hits` as root, else `~/.local/share/9hits` | Where the viewer is downloaded/extracted (also holds config + cache) |
| `--install-deps` | off | Install missing system packages (needs root) |
| `--skip-checks` | off | Warn instead of aborting on failed platform/dependency checks |
| `--port` | `10000` | Port for the `/health` endpoint |
| `--no-health-server` | off | Don't serve `/health` (handy for local runs) |
| `--resolution` | auto | Xvfb resolution, e.g. `1920x1080x24` |
| `--display` | auto (`:99`+) | X display to use; an already-working `$DISPLAY` is reused |
| `--supervisor-delay` | `10` | Seconds before relaunching an exited viewer |
| `--force-download` | off | Re-download the viewer even if already installed |
| `--skip-init` | off | Reuse the persisted config instead of re-applying it |
| `--download-url` | official URL | Use a mirror for the tarball |
| `--extra-arg` | — | Extra flag passed straight to the viewer (repeatable) |

Run `python3 run_native.py --help` for the full list.

### Keeping it running in the background

There is no systemd requirement, so any of these work:

```bash
# nohup - simplest, survives logout
nohup python3 run_native.py --system-session > 9hits.log 2>&1 &

# tmux / screen - attach any time to watch the dashboard
tmux new -s 9hits 'python3 run_native.py --system-session'

# user systemd unit (no root), if your box has lingering enabled
systemctl --user start 9hits
```

> **Requirements:** Linux **x86_64** with **glibc ≥ 2.31** (Ubuntu 20.04+,
> Debian 11+, RHEL/Rocky/Alma 9+, Fedora 36+). The viewer is a closed-source
> x86_64 binary, so **ARM machines (Oracle Ampere, Raspberry Pi, Apple Silicon)
> cannot run it** — on those, use an x86_64 host. On macOS/Windows, run it
> inside WSL2 or a Linux VM.

---

## Free Multi-Platform Strategy: Free System Sessions on Clean Cloud IPs

Rather than relying on shared free proxy lists (which frequently encounter `Auth: Duplicate USER on IP` errors from other 9Hits members), the most reliable method is to run **one system session on each free cloud hosting platform**.

Each provider assigns an isolated, clean datacenter outbound IP address:

| Platform | Free Tier Type | Resources | Outbound Region / IP | Deployment Method |
| :--- | :--- | :--- | :--- | :--- |
| **Hugging Face Spaces** | **100% Free (Gradio SDK)** | **16 GB RAM**, 2 vCPU | US / EU (AWS) | **No Docker** — Gradio Space (`app.py`) |
| **Koyeb** | **100% Free (Nano Service)** | 512 MB RAM, 0.1 vCPU | Frankfurt, Washington D.C., Singapore | Docker (`koyeb.yaml`) |
| **Render** | **100% Free (Web Service)** | 512 MB RAM, 750 hrs/mo | Oregon, Ohio, Frankfurt, Singapore | Docker (`render.yaml`) |
| **Oracle Cloud Always Free** | **100% Forever Free** | 24 GB RAM (ARM) / 1 GB x86 | Your chosen home region | `docker compose up -d` or `run_native.py` |
| **Fly.io** | Free Allowance | Up to 3 shared VMs (256 MB) | 30+ Global Regions | `fly.toml` |
| **Zeabur / Railway** | Free Trial / Starter credits | 512 MB RAM | Global Edge | `zeabur.json` / `railway.json` |
| **Any VPS / WSL / Colab / LXC** | — | anything x86_64 | Anywhere | **No Docker** — `run_native.py` |

> ⚠️ **Oracle ARM note:** the free 24 GB / 4-core Ampere shape is **ARM64**, and
> the 9Hits viewer is an **x86_64-only** binary. Use Oracle's *x86 AMD* free
> instances (or another x86_64 host) — neither Docker nor `run_native.py` can
> work around this.

---

## Deployment Guides for 100% Free Platforms

### 1. Hugging Face Spaces (100% FREE — Gradio SDK, 16 GB RAM)
> 💡 *Note: Docker Spaces require a paid subscription on HF, but **Gradio Spaces are 100% Free** with 16 GB RAM! There is no Docker daemon inside a Gradio Space, so `app.py` drives the Docker-free `run_native.py` runner and shows a live status dashboard. System packages come from `packages.txt`, which Spaces installs for you.*

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
1. In [Render Dashboard](https://dashboard.render.com/) → **New → Blueprint** (connect this repo).
2. Or create **New → Web Service** → Docker runtime → Free tier.
3. Paste `ACCESS_KEY` when prompted.
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

### 4. Oracle Cloud Always Free (x86 AMD instances)
Oracle Cloud provides a generous free tier. Use the **x86 AMD** shapes — the
4-core/24 GB Ampere shape is ARM64 and cannot run the x86_64 viewer.

```bash
git clone https://github.com/Sugamdeol/hits4me.git && cd hits4me
cp .env.example .env

# Edit .env and set ACCESS_KEY and SYSTEM_SESSION=yes
nano .env

docker compose up -d
```

**Docker not installed (or not allowed)?** Same `.env`, no container:

```bash
./run_native.sh --system-session
```

---

### 5. Fly.io
1. Install `flyctl`: `curl -L https://fly.io/install.sh | sh`
2. Run `fly launch` in this directory (uses `fly.toml`).
3. Set your secret: `fly secrets set ACCESS_KEY="<your-access-key>"`.
4. Deploy: `fly deploy`.

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
| `INSTALL_DIR` | `--install-dir` | `/opt/9hits` (root) / `~/.local/share/9hits` | *(native runner)* where the viewer is installed |

All of the above work identically with Docker and with `run_native.py`; the
native runner additionally accepts each one as a command-line flag, which takes
precedence over the environment.

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

### Native runner (`run_native.py`)

* **`missing dependencies - N shared libraries`** — the runner prints the exact
  `apt-get`/`yum` command for your distro; run it (or re-run as root with
  `--install-deps`). Use `--skip-checks` to attempt startup anyway.
* **`unsupported CPU architecture 'aarch64'`** — the viewer is x86_64-only.
  ARM hosts (Oracle Ampere, Raspberry Pi, Apple Silicon) cannot run it.
* **`glibc X.Y is too old`** — you need Ubuntu 20.04+, Debian 11+, RHEL/Rocky/
  Alma 9+ or Fedora 36+.
* **`Xvfb is not installed and no usable DISPLAY was found`** — install it
  (`sudo apt-get install -y xvfb`), or point `--display` at an X server you
  already have; a working `$DISPLAY` is detected and reused automatically.
* **`cannot create install dir /opt/9hits: Permission denied`** — you're not
  root; pass `--install-dir=$HOME/9hits` (or just omit it, which already
  defaults to a per-user path).
* **`downloaded file is not a valid .tar.bz2 archive`** — a proxy or captive
  portal returned an error page. Check network/DNS or pass `--download-url`.
* **Port already in use** — pass `--port=<other>` or `--no-health-server`.

Run the test suite (no network, no real viewer binary needed):

```bash
python3 tests/test_run_native.py
```
