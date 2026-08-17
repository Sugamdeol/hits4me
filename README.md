---
title: 9Hits Viewer v6 + FeelingSurf Viewer
emoji: 🌐
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.0
app_file: app_hf.py
pinned: false
---

# hits4me

Run the **9Hits Viewer v6** ([9hitste/appv6](https://hub.docker.com/r/9hitste/appv6)) and the
**FeelingSurf Viewer** ([feelingsurf/viewer](https://hub.docker.com/r/feelingsurf/viewer)) on
**100% Free Cloud Hosting Platforms** (**Hugging Face Spaces, Koyeb, Render, Oracle Cloud Always Free, Fly.io, Railway, Zeabur**) as lightweight services with integrated **`/health` endpoints** for uptime monitoring.

* **9Hits Viewer** — sophisticated traffic-exchange viewer with proxy/proxy-pool/system-session options.
* **FeelingSurf Viewer** — drop-in autosurf viewer; one container, one env var (`access_token`), no extra configuration.

**Two independent deployments.** The **9Hits deployment** (this image)
runs **only the 9Hits viewer** (`FEELINGSURF_ENABLED=no` by default), with a
24x7 process manager (`supervisor.py`) that keeps it alive and a `supervisor`
keep-alive pinger (`keepalive.py`) that pings the **separate FeelingSurf
deployment** every 5 minutes to keep it awake. The **FeelingSurf deployment**
is a separate service built from `Dockerfile.feelingsurf` that runs only the
FeelingSurf viewer. Set `ACCESS_KEY` (9Hits) on the 9Hits deployment and
`ACCESS_TOKEN` (FeelingSurf) on the FeelingSurf deployment. See
[Standalone FeelingSurf deployment](#2-standalone-feelingsurf-deployment-separate-service--keep-alive).

---

## Free Multi-Platform Strategy: Free System Sessions on Clean Cloud IPs

Rather than relying on shared free proxy lists (which frequently encounter `Auth: Duplicate USER on IP` errors from other 9Hits members), the most reliable method is to run **one system session on each free cloud hosting platform**.

Each provider assigns an isolated, clean datacenter outbound IP address:

| Platform | Free Tier Type | Resources | Outbound Region / IP | Deployment Method |
| :--- | :--- | :--- | :--- | :--- |
| **Hugging Face Spaces** | **100% Free (Gradio SDK)** | **16 GB RAM**, 2 vCPU | US / EU (AWS) | Free Gradio Space (`app_hf.py`) |
| **Koyeb** | ACCESS_KEY + ACCESS_TOKEN are hard-coded in `koyeb.yaml`. | Uses `koyeb.yaml`; one service runs both viewers. |
| **Render** | **100% Free (Web Service)** | 512 MB RAM, 750 hrs/mo | Oregon, Ohio, Frankfurt, Singapore | Docker (`render.yaml`, both tokens hard-coded) |
| **Oracle Cloud Always Free** | **100% Forever Free** | **24 GB RAM**, 4 ARM cores | Your chosen home region | `docker compose up -d` |
| **Fly.io** | ACCESS_KEY + ACCESS_TOKEN are hard-coded in `koyeb.yaml`. | Uses `fly.toml`; one service runs both viewers. |
| **Zeabur / Railway** | Free Trial / Starter credits | 512 MB RAM | Global Edge | `zeabur.json` / `railway.json` |

---

## Deployment Guides for 100% Free Platforms

### 1. Hugging Face Spaces (100% FREE — Gradio SDK, 16 GB RAM)
> 💡 *Note: Docker Spaces require a paid subscription on HF, but **Gradio Spaces are 100% Free** with 16 GB RAM! This repo includes `app_hf.py` to run seamlessly on the standard Gradio SDK.*

1. Go to [huggingface.co/spaces](https://huggingface.co/spaces) → **Create new Space**.
2. Space Name: `hits4me-viewer` (or any name).
3. Select **Gradio** SDK (leave hardware as **Free 2 vCPU · 16 GB RAM**).
4. Connect or duplicate this repository.
5. The HF entrypoint includes the configured `ACCESS_KEY` and `ACCESS_TOKEN`, so
   no secret variables are required. Optional variables with those names override
   the built-in values. Keep `SYSTEM_SESSION=yes`, `CLEAR_ALL_SESSIONS=yes`,
   `SESSION_NOTE=hf-system`, `NOTE=huggingface`, and `RESET_INTERVAL=2h` if you
   want to customize the defaults.
6. The Space will build and launch a live status dashboard while running 9Hits
   and three native FeelingSurf processes. HF mode uses `LOW_MEMORY=off`,
   `DUAL_VIEWER_MODE=off`.

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
The existing **`9hits-viewer`** web service ([dashboard](https://dashboard.render.com/web/srv-da09a7dbedkc73a829cg)) is the **9Hits deployment**: it runs **only the 9Hits viewer**. `render.yaml` also defines a separate **`feelingsurf`** web service that runs only FeelingSurf (see [Standalone FeelingSurf deployment](#2-standalone-feelingsurf-deployment-separate-service--keep-alive)). Render's free plan allows only ONE web service, so running both services side by side needs a paid plan or another host.

The **`9hits-viewer`** Blueprint ships with **9Hits only** (`NINEHITS_ENABLED=yes`, `FEELINGSURF_ENABLED=no`, `DUAL_VIEWER_MODE=off`) plus the 512 MB memory stack described below, so the old "OOM every ~1 minute" loop (two Chromium viewers on the free plan) is gone.

1. In [Render Dashboard](https://dashboard.render.com/) apply the Blueprint for this repo so it updates the existing **`9hits-viewer`** service (or open that service and redeploy).
2. Or, only if `9hits-viewer` does not already exist, create **New → Web Service** → Docker runtime → Free tier and name it exactly `9hits-viewer`.
3. `ACCESS_KEY` is **hard-coded in `render.yaml`** — no prompting needed, just deploy. (The FeelingSurf token is hard-coded on the separate `feelingsurf` service.)
4. Default environment comes from the Blueprint: 9Hits-only + `DUAL_VIEWER_MODE=off` + `LOW_MEMORY=balanced` + `INIT_TIMEOUT=600` + `NH_MAX_MEMORY_MB=400`. It also pings the `feelingsurf` service via `FEELINGSURF_URL` every `FEELINGSURF_PING_INTERVAL` s to keep it awake.
5. *(Optional)* Deploy another free service in a different region (e.g. Frankfurt or Ohio) to get an extra unique IP address.

---

## Memory on 512 MB plans (Render free)

The default deployment runs **9Hits only** — a single Chromium-based viewer
(9Hits v6 CEF) idles at roughly 250–400 MB, which fits comfortably inside
Render's 512 MB free cgroup limit. `LOW_MEMORY` shrinks the 9Hits Chromium to
keep it well under the budget: `--renderer-process-limit=1`, `--disable-gpu`,
V8 heap caps, disk/media caches off, and `Xvfb` resolution trimmed. The
`supervisor.py` per-slot memory cap (`NH_MAX_MEMORY_MB`, default 400) restarts
only 9Hits if it runs away.

FeelingSurf is deployed as a **separate service** (`Dockerfile.feelingsurf`),
so its memory never competes with 9Hits on the same box. Because the two
viewers no longer share one container, the old dual-viewer memory guardian
(`memguard.py`), time-slicing, and OOM-thrash handling were **removed**.

## Dashboard GUI

Open **`/`** (or `/gui`) on your service in a browser — no login, no extra
server, ~0 MB of RAM (a static page that polls `/health` every 2 s):

* **Memory card** — live container RSS vs the budget with the hard-threshold
  marker, plus per-viewer bars (9Hits v6 / FeelingSurf) and the peak.
* **Dual-viewer card** — configured vs effective mode, the active viewer, and
  a **countdown to the next time-slice flip**.
* **Viewers card** — running state, phase/pid, silent seconds and restarts
  for each viewer.
* **Per-slot controls** — Start / Stop / Restart buttons for the 9Hits
  slot, the FeelingSurf slot, and the health server itself (the buttons
  POST to `/control/<name>/<action>` and the supervisor picks up the
  request on its next tick).
* **Log tails** — last 4 KB of `logs/9hits.log` and `logs/feelingsurf.log`
  inlined, so you can see which service produced an error without
  `docker exec`-ing into the container.

It is served by the same health server, so it works on Render free (which only
exposes `$PORT`), Koyeb, Fly, Railway, Zeabur and local compose alike.

## Process manager & per-viewer controls (24x7 operation)

The container is owned by a tiny stdlib Python supervisor (`supervisor.py`).
It is the **PID 1** process inside the container - it does not depend on
any web request, Gradio activity, or external keep-alive. It runs an
internal 500 ms loop, reaps every long-lived child directly, and
restarts crashed slots independently. The result is that both viewers
stay up for as long as the container is running, with full 24x7
operation.

### Properties

* **One slot per service** — `ninehits`, `feelingsurf`, `health`.
  Each is an independent `ManagedSlot` instance in `supervisor.py` and
  each has its own PID, log file, restart counter, and exponential
  backoff state.
* **Independent restarts** — when 9Hits crashes, only the 9Hits slot is
  relaunched after `SUPERVISOR_DELAY` seconds; FeelingSurf and the health
  server keep running untouched, with their PIDs unchanged.
* **Exponential backoff** — a slot that keeps crashing within seconds sees
  its delay grow (`10 → 20 → 40 → 80 → 120 s`) up to
  `SUPERVISOR_MAX_DELAY` (default 120 s), so a broken deploy cannot
  burn the host's CPU. After `SUPERVISOR_PARK_AFTER` (default 10) rapid
  crashes the slot is parked for `SUPERVISOR_PARK_SECS` (default 300 s)
  before the next try; it still recovers automatically once the
  underlying issue clears.
* **No duplicate processes** — the `ManagedSlot.start()` method first
  checks that no proc object is already alive for the slot, and on
  container start the supervisor also scans `/proc` for matching
  viewer exes (`nhviewer`, `may`, `chrome`, `electron`,
  `FeelingSurfViewer`) and refuses to launch a second instance.
  Rapid-fire `start` requests via the dashboard or `curl` collapse to
  one actual start.
* **Per-slot memory cap** — `NH_MAX_MEMORY_MB` / `NINEHITS_MAX_MEMORY_MB`
  and `FS_MAX_MEMORY_MB` / `FEELINGSURF_MAX_MEMORY_MB` set a soft cap
  on the slot's process tree PSS (proportional set size). When a viewer exceeds its cap for **two consecutive samples**, the supervisor gracefully restarts **only that slot** (the other one keeps running). Default = 0 (off). Sensible values on a 512 MB free plan: `400` for both. PSS is read from `/proc/<pid>/smaps_rollup` (Linux >= 4.14) with a VmRSS fallback on older kernels; summing PSS — not summed RSS — avoids counting Chromium's large shared mappings once per process. This is independent of the total-RSS `memguard.py`; the per-slot cap catches a single runaway viewer before it can crash the cgroup.
* **Per-slot child-count cap** — `NH_MAX_CHILDREN` /
  `NINEHITS_MAX_CHILDREN` and the FS equivalents guard against fork
  bombs (a Chromium that started spawning helper processes
  indefinitely). Default = 0 (off).
* **Lightweight monitoring loop** — the main loop ticks at
  `SUPERVISOR_TICK` (default 0.5 s) for fast signal/crash response.
  The expensive per-slot checks (memory + child count) run at
  `NINEHITS_CHECK_INTERVAL` / `FEELINGSURF_CHECK_INTERVAL`
  (default 30 s) so the steady-state CPU cost stays near zero.
* **Log rotation** — every slot rotates its log when it exceeds
  `SLOT_LOG_MAX_BYTES` (default 10 MiB), keeping `SLOT_LOG_BACKUPS`
  copies (default 2). Worst case for five slots: 150 MiB of logs.
* **Clean shutdown** — `docker stop` (or `kill -TERM`, or `kill -INT`,
  or `kill -QUIT`, or `kill -HUP` on the supervisor) makes the
  supervisor forward the signal to every child, wait their
  `stop_grace` (15 s for FS, 20 s for 9Hits), then SIGKILL anything
  still alive. The health endpoint reports `status: "error"` and 503
  the moment the supervisor dies. No orphan children are left.
* **Per-slot log files** — every child writes to
  `logs/<name>.log` AND to the docker stdout stream. The
  `LOG_DIR=/logs` directory is bind-mounted by `docker-compose.yml`,
  and on Render/Koyeb it lives inside the container (still readable
  with `docker exec <id> cat /logs/9hits.log`).
* **Per-viewer resource caps (optional)** —
  `NH_CPU_SHARES` / `FS_CPU_SHARES` (Linux cgroup v1 weight). Set
  them in the Blueprint / env when you want to give 9Hits a strict
  CPU budget so it cannot starve FeelingSurf.

Runtime control (also exposed by the dashboard buttons):

```bash
# Start, stop, or restart a managed slot
curl -X POST http://localhost:10000/control/ninehits/restart
curl -X POST http://localhost:10000/control/feelingsurf/stop

# Tail the last 4 KB of any slot's log
curl http://localhost:10000/logs/9hits
curl http://localhost:10000/logs/feelingsurf
curl http://localhost:10000/logs/supervisor

# Full supervisor snapshot (debug)
curl http://localhost:10000/slots | python3 -m json.tool
```

The supervisor is the only long-lived process in the container. There is
no shell at the top of the process tree to leak environment variables or
turn into an orphan — every child is reaped by the supervisor and the
final exit code of each managed process is recorded in
`/tmp/supervisor_state.json`.

## Measured memory (real Chromium 149, this repo's flags)

The proprietary viewer binaries can't be downloaded on every box, so these
numbers were measured with a **real, modern Chromium engine** (Chromium 149)
running the exact `NH_MEM_FLAGS` / `FS_MEM_FLAGS` sets that `start.sh` applies,
against realistic autosurf-style pages (headless — real deployments add
~20–40 MB per Xvfb display, so treat these as *lower bounds*).

Two numbers are given for the pairs: **RSS** (what the raw process list sums
to — double-counts the file-backed pages the two instances share) and
**PSS** (proportional set size — the *unique* memory, which is what the cgroup
actually charges; this is what the supervisor and the dashboard report):

| Scenario | RSS (median, MB) |
| :--- | ---: |
| one viewer, tuned (`LOW_MEMORY=balanced`), active on a light page | **~284–318** |
| one viewer, tuned, idle (between campaigns) | **~277** |
| one viewer, stock (no flags) | ~316 |
| 3 sessions in one viewer, stock | ~610 |
| 3 sessions in one viewer, tuned (`--renderer-process-limit=1`) | ~403 (**−34%**) |
| both viewers, `balanced`, active together | ~570–620 (RSS) ❌ |
| both viewers, balanced, even idle | ~550 (RSS) ❌ |
| **both viewers, `LOW_MEMORY=extreme` (`--single-process`), 2 tabs each** | **~324 RSS / ~308 PSS** ✅ |
| **9Hits `extreme` + FeelingSurf swiftshader (`FS_SP=no`)** | **~458 RSS** ✅ |

**What this means — and how to run BOTH at the same time:**
`LOW_MEMORY=extreme` puts each viewer into single-process mode
(`--single-process --in-process-gpu`) and measures **~324 MB for the whole
pair** in a lab — but on Render's free 512 MB plan that mode is *not stable*:
the 9Hits run pass crashes almost immediately with exit code 133 (SIGTRAP).
The default deployment runs 9Hits only (9Hits easily fits 512 MB). If you
*opt in* to running both viewers in one container on Render, use **balanced**
with the per-viewer caps doing the trimming:

```env
NINEHITS_ENABLED=yes
FEELINGSURF_ENABLED=yes        # opt-in: run both in one container
DUAL_VIEWER_MODE=concurrent    # both at the same time, no time-slicing
LOW_MEMORY=balanced
INIT_TIMEOUT=600
NH_MAX_MEMORY_MB=400
FS_MAX_MEMORY_MB=400
```

Safety nets keep `--single-process` from ever bricking a viewer if you do
enable `extreme` on a bigger host:
* **Fast-crash detector** — a run pass that exits in under 10 s with a
  non-clean code sets `/tmp/viewer.fastcrash`, and the next launch drops
  `--single-process` immediately.
* **Crash auto-fallback** — if a viewer crash-loops 3× at startup, its
  single-process flags are dropped automatically (it keeps running on the
  balanced set; `NH_SP=no` / `FS_SP=no` force this manually).
* **FeelingSurf GL** — upstream runs swiftshader; under single-process, if FS
  crash-loops, set `FS_GL_MODE=disable-gpu` (or `FS_SP=no`).
* **Per-slot memory cap** — the supervisor restarts 9Hits if its process
  tree exceeds `NH_MAX_MEMORY_MB`, so a spike is contained instead of letting
  the platform OOM the box. (The old `memguard.py` guardian was removed.)

The dashboard (`/`) shows both viewers' RSS bars live, so you can confirm they
are up at the same time.

Watch it live via `GET /health` (JSON) or open **`/`** in a browser for the dashboard GUI:

```json
{
  "dual_viewer_mode": "auto",
  "effective_mode": "time-slice",      // auto escalated - box can't fit both
  "active_viewer": "feelingsurf",      // ninehits is parked until its turn
  "memory_used_mb": 412.5,             // total container RSS right now
  "memory_limit_mb": 512,
  "memory_peak_mb": 498.1,
  "ninehits_rss_mb": 0.0,              // parked viewer holds ~0 MB
  "feelingsurf_rss_mb": 331.2,
  "next_flip_in_seconds": 412          // countdown in time-slice mode
}
```

Notes:

* `effective_mode == "concurrent"` means **both viewers are earning 100%** of
  the time; `"time-slice"` means they alternate. Either way the container no
  longer OOM-loops.
* Uptime bots will see `"status": "restarting"` while the parked viewer is
  down in time-slice mode — that is expected and is not a crash.
* 9Hits alone is the default (`NINEHITS_ENABLED=yes` +
  `FEELINGSURF_ENABLED=no`); FeelingSurf runs in its own separate deployment
  (`Dockerfile.feelingsurf`). Each single viewer easily fits 512 MB.
* On hosts with ≥ 2 GB (Fly 4 GB, Oracle 24 GB, HF 16 GB) `LOW_MEMORY=auto`
  disables the flags and everything runs concurrently as before.
  `LOW_MEMORY=extreme` (`--single-process`) also requires ≥ 2 GB — it is
  not safe on Render's 512 MB free plan.

---

### 4. Oracle Cloud Always Free (24 GB RAM / 4 CPUs Forever Free)
Oracle Cloud provides the most generous free tier in the cloud industry (4 ARM vCPUs + 24 GB RAM, plus 2 x86 AMD instances):
```bash
git clone https://github.com/Sugamdeol/hits4me.git && cd hits4me
cp .env.example .env
# ACCESS_KEY and ACCESS_TOKEN are already baked into .env.example and the
# Dockerfiles - no manual env editing needed.

# Start the 9Hits deployment (9Hits only):
docker compose up -d viewers

# Start the separate FeelingSurf deployment (its own container):
docker compose up -d feelingsurf
```

---

### 5. Fly.io
1. Install `flyctl`: `curl -L https://fly.io/install.sh | sh`
2. Run `fly launch` in this directory (uses `fly.toml`) for the **9Hits deployment**.
3. `ACCESS_KEY` is already baked into `fly.toml [env]` — no `fly secrets set` needed.
4. Deploy: `fly deploy`.

---

## FeelingSurf Viewer (additional autosurf viewer)

Hit "too many free platforms, no proxies" with 9Hits? This repo deploys the
**FeelingSurf Viewer** as its **own independent service** (`Dockerfile.feelingsurf`),
separate from the 9Hits deployment. Drop in an `access_token` and the
supervisor keeps FeelingSurf alive 24×7.

⚠️ **Disclaimer:** *Never share your FeelingSurf `access_token` — it grants full access to your account.*

### Get an access token
1. Register at [feelingsurf.fr](https://www.feelingsurf.fr/) and finish email confirmation.
2. Go to **Member area → Profile / Settings → API / Access Token** and generate one. (The token is a long opaque string — treat it like a password.)

### 1. `docker compose` — the 9Hits deployment
The repository Dockerfile (`Dockerfile`) builds the **9Hits deployment**. It
runs **only the 9Hits viewer** (`FEELINGSURF_ENABLED=no` by default), with a
supervised `/health` server and the keep-alive pinger.

```bash
cp .env.example .env
# ACCESS_KEY is already baked into .env.example / the Dockerfile - no manual
# env editing needed.
docker compose up -d viewers
curl http://localhost:10000/health                  # 9Hits status
```

Service map:
| Container | Processes | Health |
| :--- | :--- | :--- |
| `viewers` | supervised 9Hits | `GET /health` on port `10000` |

### 2. Standalone FeelingSurf deployment (separate service) + keep-alive

The **9Hits deployment** runs 9Hits only. To run FeelingSurf on its **own
independent service/container**, this repo ships a dedicated
`Dockerfile.feelingsurf` that builds a container running **only the
FeelingSurf viewer** plus its `/health` endpoint.

```bash
# Standalone FeelingSurf service via docker compose:
docker compose up -d feelingsurf
curl http://localhost:10001/health          # FeelingSurf /health endpoint
```

Service map:
| Container | Processes | Health |
| :--- | :--- | :--- |
| `viewers` (9Hits deployment) | supervised 9Hits only | `GET /health` on port `10000` |
| `feelingsurf` (separate) | supervised FeelingSurf only | `GET /health` on port `10001` (host) / `10000` (container) |

> The `feelingsurf` service is independent: its own name, container,
> environment and ports (offset to `10001`/`3001` so it can run on the same
> host as the `viewers` container). Render users: the free plan allows only
> ONE web service, so running two services side by side needs a paid plan or
> another host.

#### Keep the FeelingSurf deployment awake

The **9Hits deployment** doubles as a lightweight keep-alive client for the
separate FeelingSurf deployment. Every `FEELINGSURF_PING_INTERVAL` seconds
(default `300` = 5 min) `supervisor.py` fires a tiny HTTP GET at the FeelingSurf
deployment from a **background daemon thread** (`keepalive.py`). It pings
`<URL>/health` first and falls back to `<URL>/` if that returns 404.

The target URL is **resolved automatically** — no manual URL to set:

| Resolution order | Source | When it applies |
| :--- | :--- | :--- |
| 1 | `FEELINGSURF_URL` | explicit override (hosted deployments, e.g. Render) |
| 2 | `FEELINGSURF_INTERNAL_URL` | in-cluster/internal DNS when the two share a network |
| 3 | Platform auto-detection | Render / Fly when this service *is* the FeelingSurf deployment |

On **docker-compose** the two services share a network, so the pinger
defaults to the internal service DNS and finds the new `feelingsurf` container
automatically on any host — you never set an IP or URL:

```env
# docker-compose auto-targets the feelingsurf service - no URL needed.
FEELINGSURF_URL=
FEELINGSURF_PING_INTERVAL=300
# FEELINGSURF_PING_TIMEOUT=10
```

```env
# Hosted deployments (e.g. Render): point it at the FeelingSurf public URL.
FEELINGSURF_URL=https://your-feelingsurf-service.example.com
FEELINGSURF_PING_INTERVAL=300
# FEELINGSURF_PING_TIMEOUT=10
```

The pinger is only disabled when none of the above yields a URL. It can never
block, crash, or restart the 9Hits app: connection/timeout/HTTP errors are
caught and logged (`[KeepAlive] ...`) and simply retried next interval, so a
temporarily unreachable FeelingSurf service has zero effect on the 9Hits
viewers. Only one request is sent per interval — no tight loop.

Log output looks like:
```
[KeepAlive] pinger started: target=https://your-feelingsurf-service.example.com/health interval=300s timeout=10s
[KeepAlive] FeelingSurf responded with 200
[KeepAlive] FeelingSurf ping failed: <urlopen error timed out> on https://your-feelingsurf-service.example.com/health
```

Architecture:

```
9Hits deployment (9Hits only)         FeelingSurf deployment (separate)
  supervisor.py  ──every 5 min──▶        FeelingSurf viewer + /health
  + [KeepAlive] daemon thread            (Dockerfile.feelingsurf)
  (continues running normally)           (responds to health/ping requests)
```

### 3. Free cloud platforms

Two independent services. The **9Hits deployment** uses the repository
`Dockerfile` (9Hits only) and, where it defines a second service, the
**FeelingSurf deployment** uses `Dockerfile.feelingsurf`. Configure
`ACCESS_KEY` (9Hits) on the 9Hits service and `ACCESS_TOKEN` (FeelingSurf) on
the FeelingSurf service.

| Platform | Deployment | Notes |
| :--- | :--- | :--- |
| **Oracle Cloud Always Free** | `docker compose up -d viewers` (9Hits) and `docker compose up -d feelingsurf` (FeelingSurf). | Two containers; the 24 GB tier has ample memory. |
| **Render** | Blueprint (`render.yaml`) updates the existing **`9hits-viewer`** web service (9Hits only) and defines a separate **`feelingsurf`** web service; `ACCESS_KEY` / `ACCESS_TOKEN` are hard-coded per service. 9Hits-only: `DUAL_VIEWER_MODE=off` + `LOW_MEMORY=balanced` + `INIT_TIMEOUT=600` + `NH_MAX_MEMORY_MB=400` (`extreme`/single-process crashes with code 133 here). The 9Hits service pings the FeelingSurf service via `FEELINGSURF_URL` every 5 min. | Render's free plan allows only ONE web service — running the two services side by side needs a paid plan or another host. |
| **Koyeb** | `ACCESS_KEY` + `ACCESS_TOKEN` are hard-coded in `koyeb.yaml` (this deploys the 9Hits deployment). | Uses `koyeb.yaml`; one service runs 9Hits. |
| **Fly.io** | Deploy this repository Dockerfile and set `ACCESS_KEY`. | Uses `fly.toml`; one service runs 9Hits. |
| **Railway** | Deploy this repository Dockerfile and set `ACCESS_KEY`. | Uses `railway.json`; one service runs 9Hits. |
| **Zeabur** | Deploy this repository Dockerfile and set `ACCESS_KEY`. | Uses `zeabur.json`; one service runs 9Hits. |
| **Hugging Face Spaces** | Standard Gradio Space; `app_hf.py` downloads and runs both viewers natively. | 16 GB runtime: 1× 9Hits system session + 3× FeelingSurf, with no memory guardian or low-memory tuning. |

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

Viewer config flags are applied by the **init pass** (`nhviewer <flags> --exit-on-init`); the **run pass** then starts with `--auto-start --in-loop --render-to-terminal [--reset-interval=...]` — the same flow as the [official 9Hits v6 installer](https://github.com/9hitste/install).

| Env var | `nhviewer` flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `NINEHITS_ENABLED` | — | `yes` | `yes`/`no`/`1`/`0`/`true`/`false`/`on`/`off` — run the 9Hits viewer. **ON by default** in the 9Hits deployment. |
| `FEELINGSURF_ENABLED` | — | `no` | `yes`/`no`/`1`/`0`/`true`/`false`/`on`/`off` — run the FeelingSurf viewer. **OFF by default**: FeelingSurf runs in its own separate deployment (`Dockerfile.feelingsurf`, where this is `yes`). Set to `yes` here only to run it inside the same container as 9Hits (auto-disables quietly when the binary is absent, e.g. the HF Gradio runtime). |
| `DUAL_VIEWER_MODE` | — | `off` | `auto` / `concurrent` / `time-slice` / `off` — how the two viewers share one small box (see [Memory on 512 MB plans](#memory-on-512-mb-plans-render-free)). **`off` by default** (single-viewer, 9Hits-only deployment); set `concurrent` only when running both viewers together. |
| `TIME_SLICE` | — | `1500` | Seconds each viewer runs per turn in `time-slice` mode (25 min default) |
| `LOW_MEMORY` | — | `balanced` | `auto` (flags on when box < 1 GB) / `off` / `balanced` (**default, safe on Render 512 MB**) / `extreme` (`--single-process --in-process-gpu`, ~324 MB pair in a lab but **crashes with exit 133 on Render 512 MB** — needs ≥ 2 GB) — Chromium memory-shrinking flags applied to both viewers |
| `NH_SP` | — | `no` | Single-process for 9Hits in `extreme` mode. Auto-fallback: dropped on the next launch if the run pass dies in under 10 s (fast-crash detector), and after 3 startup crashes |
| `FS_SP` | — | `no` | Single-process for FeelingSurf in `extreme` mode. Same auto-fallback (dropped after repeated startup crashes) |
| `FS_GL_MODE` | — | `swiftshader` | FeelingSurf GL: `swiftshader` (upstream) or `disable-gpu` (last resort if FS crash-loops under single-process) |
| `FS_SHARE_DISPLAY` | — | `yes` | Reuse the 9Hits Xvfb display for FeelingSurf (one less X server on tight instances) |
| `CREATE_SWAP` | — | *none* | Best-effort swap size, e.g. `256M` (needs `swapon` permission; auto-tried on < 1 GB boxes) |
| `NH_RUN_EXTRA_ARGS` | — | *none* | Raw flags appended to the 9Hits **run pass** (the init pass uses `EXTRA_ARGS`) |
| `FS_EXTRA_FLAGS` | — | *none* | Raw flags appended to the FeelingSurf launch (last switch wins) |
| `FS_RESOLUTION` | — | `auto` | FeelingSurf Xvfb resolution (`1280x720x24` on small boxes, else `1920x1080x24`) |
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
| `CACHE_LIMIT` | `--cache-limit` | `0` | Disk cache limit in bytes (`0` = no cache; unset = official 200 MB cap) |
| `HIDE_COLUMNS` | `--hide-columns` | *none* | Dashboard columns to hide, e.g. `quality,points` |
| `RESET_INTERVAL` | `--reset-interval` (run pass) | `2h` | Graceful self-restart interval (`2h`, `6h`, `30m`) |
| `PORT` | — | `10000` | Port for `/health` endpoint |
| `FEELINGSURF_URL` | — | *none* | Public URL of the **separate** FeelingSurf deployment, e.g. `https://your-feelingsurf-service.example.com`. Highest priority source for the keep-alive pinger (`keepalive.py`), which GETs `<URL>/health` (falling back to `<URL>/`) every `FEELINGSURF_PING_INTERVAL` s. On docker-compose this is auto-set to the internal service DNS (`http://feelingsurf:10000`) so no URL is needed. Falls back to `FEELINGSURF_INTERNAL_URL`, then platform auto-detection. |
| `FEELINGSURF_INTERNAL_URL` | — | *none* | In-cluster/internal DNS of the FeelingSurf deployment when it shares the 9Hits network, e.g. `http://feelingsurf:10000`. Used when `FEELINGSURF_URL` is unset. |
| `FEELINGSURF_PING_INTERVAL` | — | `300` | Seconds between FeelingSurf keep-alive pings (default 300 = 5 min) |
| `FEELINGSURF_PING_TIMEOUT` | — | `10` | Per-request timeout (s) for the keep-alive ping |
| `SUPERVISOR_DELAY` | — | `10` | Base restart cooldown (s) before relaunching an exited slot (alias: `RESTART_DELAY`) |
| `SUPERVISOR_MAX_DELAY` | — | `120` | Ceiling for the exponential backoff when a slot keeps crash-looping |
| `SUPERVISOR_PARK_AFTER` | — | `10` | After N rapid crashes the slot is parked (cooldown extended) |
| `SUPERVISOR_PARK_SECS` | — | `300` | Park duration (s) when a slot is parked. Slot still recovers automatically |
| `SUPERVISOR_TICK` | — | `0.5` | Main loop tick (s). Lower = faster signal/crash response, slightly higher steady-state CPU |
| `NINEHITS_CHECK_INTERVAL` | — | `30` | Seconds between expensive 9Hits checks (memory, child count) |
| `FEELINGSURF_CHECK_INTERVAL` | — | `30` | Seconds between expensive FeelingSurf checks |
| `LOG_DIR` | — | `/logs` | Per-slot log directory. `logs/9hits.log`, `logs/feelingsurf.log`, `logs/health.log`, `logs/supervisor.log` |
| `SLOT_LOG_MAX_BYTES` | — | `10485760` | Rotate per-slot log when it exceeds this size |
| `SLOT_LOG_BACKUPS` | — | `2` | Number of rotated copies to keep per slot |
| `NH_MAX_MEMORY_MB` / `NINEHITS_MAX_MEMORY_MB` | — | `0` | Per-slot memory cap (PSS of the process tree, two consecutive samples). When exceeded for two consecutive checks, gracefully restart ONLY 9Hits. Uses `/proc/<pid>/smaps_rollup` PSS with VmRSS fallback. Sensible value on 512 MB free: `400` |
| `FS_MAX_MEMORY_MB` / `FEELINGSURF_MAX_MEMORY_MB` | — | `0` | Same for FeelingSurf (PSS of the process tree, two consecutive samples). Sensible value on 512 MB free: `400` |
| `NH_MAX_CHILDREN` / `NINEHITS_MAX_CHILDREN` | — | `0` | Refuse a 9Hits slot whose process tree has more than N children (fork-bomb guard) |
| `FS_MAX_CHILDREN` / `FEELINGSURF_MAX_CHILDREN` | — | `0` | Same for FeelingSurf |
| `NH_CPU_SHARES` | — | *none* | Linux cgroup v1 CPU weight for the 9Hits slot (1-262144) |
| `FS_CPU_SHARES` | — | *none* | Linux cgroup v1 CPU weight for the FeelingSurf slot |
| `NH_MEM_LIMIT_MB` | — | *none* | Legacy alias for `NH_MAX_MEMORY_MB` (kept for back-compat) |
| `FS_MEM_LIMIT_MB` | — | *none* | Legacy alias for `FS_MAX_MEMORY_MB` (kept for back-compat) |
| `EXTRA_ARGS` | — | *none* | Extra raw flags appended to the init pass |
| `DEFAULT_DL` | — | *none* | Download a different viewer build from this URL at container start |
| `NH_DISPLAY` | — | `:99` | X display number used for the 9Hits Xvfb |
| `NH_RESOLUTION` | — | `auto` | Xvfb resolution, e.g. `1920x1080x24` (`auto` = scale with CPU/RAM) |
| `INIT_TIMEOUT` | — | `300` | Max seconds for one init pass before it is killed and retried |
| `NH_WATCHDOG` | — | `yes` | Restart the viewer when it is wedged (no output **and** no CPU progress) |
| `NH_WATCHDOG_STUCK` | — | `600` | Seconds of silence before the wedge watchdog engages |
| `NH_RENDER_TO_TERMINAL` | — | `yes` | `no` disables the live dashboard (silent logs; watchdog still works) |
| `PTY_COLS` / `PTY_ROWS` | — | `120` / `30` | Terminal size handed to the viewer dashboard |
| `VNC` / `VNC_PW` / `VNC_PORT` / `NO_VNC_PW` | — | off / — / `5901` / off | Optional x11vnc mirror of the 9Hits display for live viewing |

---

## Health Monitoring & Uptime Bots

Every instance exposes a lightweight status endpoint:

```
GET https://<your-service>/health
```

```json
{
  "service": "hits4me-combined-viewer",
  "version": "3.3.0",
  "status": "ok",                       // "ok" | "degraded" | "restarting" | "error"
  "ninehits": {
    "status": "running",                // "running" | "stopped" | "crashed" |
                                        // "parked" | "starting" | "stopping" | "disabled"
    "running": true,                    // convenience boolean for uptime bots
    "enabled": true,
    "pid": 42,
    "uptime": "20m34s",                 // human-readable
    "uptime_seconds": 1234,
    "restarts": 0,
    "last_exit_code": null,
    "last_error": null,
    "memory_mb": 110.2,                // slot's process tree RSS
    "child_count": 7,                  // slot's process tree size
    "max_memory_mb": 400,              // 0 = no cap
    "max_children": 0,
    "check_interval_seconds": 30,
    "log_file": "/logs/9hits.log"
  },
  "feelingsurf": {
    "status": "running", "running": true, "enabled": true,
    "pid": 17, "uptime": "9m0s", "uptime_seconds": 540,
    "restarts": 1, "last_exit_code": null, "last_error": null,
    "memory_mb": 198.6, "child_count": 9,
    "max_memory_mb": 400, "max_children": 0,
    "check_interval_seconds": 30,
    "log_file": "/logs/feelingsurf.log"
  },
  "supervisor_running": true,
  "supervisor_version": "2.0.0",
  "ninehits_running": true,             // back-compat top-level booleans
  "feelingsurf_running": true,          // (true | false | "disabled")
  "health_status": "running", "health_pid": 1,
  "dual_viewer_mode": "off",
  "effective_mode": "off",
  "active_viewer": "ninehits",
  "memory_used_mb": 312.4,             // total container RSS
  "memory_limit_mb": 512,
  "memory_peak_mb": 351.1,
  "ninehits_rss_mb": 110.2,
  "feelingsurf_rss_mb": 198.6,
  "uptime_seconds": 1234,
  "low_memory": "extreme",
  "slots": { /* full supervisor state per slot, used by the dashboard */ }
}
```

* `status`:
  * `"ok"` — every enabled viewer is running.
  * `"degraded"` — at least one viewer is up, another is down. The
    supervisor is in control and will recover it automatically.
  * `"restarting"` — both viewers enabled, neither up yet (very early
    in the boot, or both crashed at the same time).
  * `"error"` — the supervisor itself is gone (HTTP 503). Container
    needs a full restart to recover.
* Per-viewer `status` (inside the nested `ninehits` / `feelingsurf` object):
  * `"running"` — the managed process is alive.
  * `"stopped"` — the slot is enabled but the process is not running
    (will be restarted by the supervisor on cooldown, or was stopped
    by the operator).
  * `"crashed"` — the process just died abnormally (last exit ≠ 0/TERM).
  * `"parked"` — the slot is cooling off after too many rapid
    crashes; the supervisor will retry once `SUPERVISOR_PARK_SECS`
    elapse.
  * `"starting"` / `"stopping"` — the supervisor is launching /
    killing it right now.
  * `"disabled"` — the slot is off by configuration.
* `memory_mb` and `child_count` (per slot) are sampled at
  `NINEHITS_CHECK_INTERVAL` / `FEELINGSURF_CHECK_INTERVAL` (default
  30 s). They power the per-slot memory cap: when a slot's PSS
  exceeds its `max_memory_mb` cap for two consecutive checks, the supervisor restarts only
  that slot (VmRSS fallback on kernels without `smaps_rollup`).
* Backward-compat flat fields (`viewer_pid`, `viewer_phase`,
  `viewer_silent_seconds`, `xvfb_running`, `restarts`, `viewer_running`,
  `feelingsurf_running`, `feelingsurf_pid`, `feelingsurf_restart_count`,
  `feelingsurf_supervisor_running`, ...) are still emitted so older
  uptime bots do not break.

Point any free uptime monitor (**UptimeRobot, Better Stack, Cron-job.org, Kuma**) to ping `https://<your-app>/health` every **5 to 10 minutes** to keep free-tier instances active and prevent sleep timeouts.

---

## Troubleshooting

* **Render: `Ran out of memory (used over 512MB)` → "Service recovered" → repeat (~1/min)** — historically caused by two un-tuned Chromium viewers on the free 512 MB plan. The deployment is now **9Hits-only** (`FEELINGSURF_ENABLED=no`, `DUAL_VIEWER_MODE=off`; FeelingSurf runs in its own separate service), so a single viewer easily fits 512 MB and the old dual-viewer memory guardian (`memguard.py`) was removed. The supervisor's per-slot cap (`NH_MAX_MEMORY_MB=400`) still restarts 9Hits if it runs away. If you still see OOM (e.g. a proxy list with many sessions), lower the session count, or move to a ≥ 2 GB plan (9Hits v6's official recommendation).
* **`Auth: Duplicate USER on IP [x.x.x.x]`** — Another 9Hits user is already using that public/shared proxy IP. Switch to a system session on a dedicated cloud provider, refresh your Webshare list, or use private proxies.
* **`Auth: Duplicate SESSION on IP [x.x.x.x]`** — Multiple sessions from your account on the same IP. Ensure `SYSTEM_SESSION=no` when using proxies, or enable `CLEAR_ALL_SESSIONS=yes` to clear lingering connections.
* **`Pool error: The public pool is closed!`** — Set `EX_PROXY_SESSIONS=0` (or unset it) and use `BULK_ADD_PROXY_LIST` / `BULK_ADD_PROXY_LIST_URL`, or provide your own custom pool via `EX_PROXY_URL`.
* **`User not found!`** — `ACCESS_KEY` is incorrect or missing.
* **Logs stop right after deploy / viewer never appears (the Aug-2026 upstream change)** — the renewed `9hitste/appv6` image used to extract a ~145 MB bzip2 viewer tarball at every container start through its own `/nh.sh`, stalling for many minutes on free-tier CPUs and then hanging silently. This repo no longer does that: the viewer is extracted at **image build time** and started via the official two-pass flow with our own supervised **Xvfb :99**. If you still see stalls, check `/health` — `viewer_phase` (`init`/`run`/`down`) and `viewer_silent_seconds` tell you exactly where it is.
* **Run pass exits with code 133 in `extreme` mode** — `LOW_MEMORY=extreme` adds `--single-process --in-process-gpu`, a mode Chromium officially labels unsupported; on Render's free 512 MB plan it aborts within seconds (`133` = 128 + 5 = SIGTRAP), so the 9Hits slot crash-loops (often preceded by init passes timing out with code `124`). The fix ships `LOW_MEMORY=balanced` as the default in the Dockerfile, `render.yaml`, and `.env.example`, and raises `INIT_TIMEOUT` to `600`. If you deliberately re-enable `extreme`, `start.sh`'s fast-crash detector writes `/tmp/viewer.fastcrash` when the run pass dies in under 10 s and relaunches **without** `--single-process` on the very next attempt. Existing Render services need a manual redeploy (or a Blueprint re-apply) to pick up the new env.
* **9Hits restarts every ~30 s with `over memory cap: memory 1471 MB > cap 400 MB` while `/health` shows `memory_used_mb: 158`** — the per-slot cap was summing **VmRSS** over the whole Chromium process tree. Each of Chromium's ~20 processes maps the same binary/fonts/shm, so the shared pages were counted ~20×: a healthy ~158 MB tree looked like 1471 MB and the slot was SIGTERMed every check (exit 143, always during the init pass, so no session ever ran — and the teardown took Xvfb with it). Fixed by summing **PSS** (proportional set size) from `/proc/<pid>/smaps_rollup` (Linux >= 4.14, VmRSS fallback on older kernels) and requiring **two consecutive over-cap samples** (`memory %.0f MB (PSS) > cap %d MB for 2 consecutive checks`) before restarting.
* **Two FeelingSurf instances / FeelingSurf aborts with `Failed to shutdown.`, exit 133 or 139 (and `memory_peak_mb` ~581 MB)** — under `supervisor.py`, `start.sh` runs as `start.sh ninehits-only`, but the `ninehits-only` branch whose own comment says it must `NOT start the FeelingSurf supervisor, the memguard, or the health server` sat **after** those startup blocks. Every 9Hits restart therefore spawned a second Electron that fought the real one over the Xvfb display and `127.0.0.1:3000`, trapping with `FATAL:electron/shell/browser/electron_browser_main_parts.cc:523] Failed to shutdown.` (code 133 = SIGTRAP, 139 = SIGSEGV) and spiking the peak to 581 MB. Fixed by gating the FeelingSurf and memguard blocks behind `RUN_MODE=ninehits-only` checks and suppressing the `combined health endpoint` banner in that mode. If a single FeelingSurf still traps under `--single-process`, set `FS_GL_MODE=disable-gpu`.
* **`WATCHDOG: no output ... no CPU progress` in the logs** — the viewer wedged (typically OOM-adjacent on 512 MB instances or a stuck Chromium) and was restarted automatically. If it repeats, lower the session count, set `FEELINGSURF_ENABLED=no`, or move to a bigger instance (v6 recommends ≥ 2 GB RAM).
* **Init pass keeps failing** (`init pass failed/timed out`) — the 9Hits API was unreachable or very slow; the supervisor retries 3× with backoff and then launches anyway (the next restart re-applies config). Increase `INIT_TIMEOUT` on very slow networks.
* **`/dev/shm` is only 64 MB** — free Docker tiers can't set `--shm-size`. The entrypoint tries a best-effort remount; where you control Docker yourself (oracle/compose), keep `shm_size: 2g` (already in `docker-compose.yml`).
* **VNC: watch the viewer live** — set `VNC=yes` + `VNC_PW=<pass>` and connect to port `5901` (only on hosts that expose it; Render web services only expose `$PORT`).
