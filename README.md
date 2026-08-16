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

**Both viewers run simultaneously by default.** The image's `ENV` defaults
turn on 9Hits + FeelingSurf at the same time (`DUAL_VIEWER_MODE=concurrent`,
`LOW_MEMORY=extreme`), with per-viewer memory caps (`NH_MAX_MEMORY_MB=400`,
`FS_MAX_MEMORY_MB=400`) and a 24x7 process manager (`supervisor.py`) that
keeps each viewer alive independently. Just set `ACCESS_KEY` (9Hits) and
`ACCESS_TOKEN` (FeelingSurf) and both viewers will run, restart on crash,
and stay up indefinitely. To run only one viewer, set the corresponding
`NINEHITS_ENABLED=no` or `FEELINGSURF_ENABLED=no`.

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
   `DUAL_VIEWER_MODE=off`, and does not start `memguard.py`.

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
The existing **`9hits-viewer`** web service ([dashboard](https://dashboard.render.com/web/srv-da09a7dbedkc73a829cg)) runs **one** container. Applying this repo's Blueprint updates that same service only — it does **not** create any new Render service (there is exactly one service in `render.yaml`).

The Blueprint ships with **BOTH viewers enabled** (`NINEHITS_ENABLED=yes`, `FEELINGSURF_ENABLED=yes`) plus the 512 MB survival stack described below, so the old "OOM every ~1 minute" loop is gone. (Previously this service ran FeelingSurf-only because two un-tuned Chromium viewers exceed the free plan's 512 MB.)

1. In [Render Dashboard](https://dashboard.render.com/) apply the Blueprint for this repo so it updates the existing **`9hits-viewer`** service (or open that service and redeploy).
2. Or, only if `9hits-viewer` does not already exist, create **New → Web Service** → Docker runtime → Free tier and name it exactly `9hits-viewer`.
3. `ACCESS_KEY` and `ACCESS_TOKEN` are **hard-coded in `render.yaml`** — no prompting needed, just deploy.
4. Default environment comes from the Blueprint: both viewers + `DUAL_VIEWER_MODE=auto` + `LOW_MEMORY=auto` + `MEMGUARD_LIMIT_MB=512` (see [Running both viewers on 512 MB](#running-both-viewers-on-512-mb-render-free)). After deploy, check `GET /health` → `effective_mode` and `memory_used_mb` to see whether both viewers run concurrently or are being alternated.
5. *(Optional)* Deploy another free service in a different region (e.g. Frankfurt or Ohio) to get an extra unique IP address.

---

## Running both viewers on 512 MB (Render free)

Two Chromium-based viewers (9Hits v6 CEF + FeelingSurf Electron) each idle at
roughly 250–400 MB — together they blow past Render's 512 MB hard cgroup limit,
which is what caused the classic `Ran out of memory (used over 512MB)` →
"Service recovered" → repeat loop. This repo now keeps the pair inside the
budget with **three cooperating layers** (all stdlib/builtin, no extra deps):

**Layer 1 — shrink both viewers (`LOW_MEMORY`, default `auto`).**
On boxes with < 1 GB of detected memory the entrypoint adds Chromium/Electron
switches to both viewers: `--renderer-process-limit=1` (one renderer for all
sessions instead of one per session), `--enable-low-end-device-mode` +
`--memory-model=low` (Chromium's own low-RAM behaviour), V8 heap caps
(`--js-flags=--max-old-space-size=64`), disk/media caches off, background
sync/extensions/network off, and — for 9Hits only — `--disable-gpu` to drop the
separate GPU process (~40–80 MB). FeelingSurf keeps upstream's swiftshader GL
flags (upstream removed `--disable-gpu` because it crashes 2.5.2). Xvfb
resolutions also shrink to 1280x720, and `MALLOC_TRIM_THRESHOLD_`/`MMAP`
tunables make glibc give freed heap pages back to the kernel.
**`LOW_MEMORY=extreme`** takes it further: both viewers run with
`--single-process --in-process-gpu` — measured **~324 MB for the pair**, which
is what makes running BOTH viewers at the same time possible on 512 MB.

**Layer 2 — memory guardian (`memguard.py`, always on unless `off`).**
Every few seconds it sums container RSS from `/proc` against the real limit
(`MEMGUARD_LIMIT_MB`, auto-detected from the cgroup, set to `512` in
`render.yaml`). If total RSS crosses `MEMGUARD_HARD_PCT` (default 97% on small
boxes), it **gracefully restarts the heaviest viewer** (TERM → KILL after 15 s;
the viewer's own supervisor in `start.sh` brings it back) *before* the platform
can OOM-kill the whole container. A cooldown prevents kill-thrash. With
`DUAL_VIEWER_MODE=concurrent` + `extreme` the pair sits well under the
threshold, so the guardian stays silent and both viewers run together.

**Layer 3 — time-slice fallback (`DUAL_VIEWER_MODE`, default `concurrent`).**
The image's default is `concurrent` (both viewers at the same time).
If you set `DUAL_VIEWER_MODE=auto` and the box really cannot fit both at once
(3+ over-budget restarts inside 10 min), memguard auto-switches to
**time-slice**: the two viewers alternate, `TIME_SLICE` seconds each (default
1500 = 25 min), so only one Chromium is resident at a time — guaranteed to
fit 512 MB, at the cost of ~50% uptime per viewer. The supervisors gate their
launches on a turn file (`/tmp/active_viewer`) and **fail open**: if memguard
dies, both viewers run freely (no deadlock). With `LOW_MEMORY=extreme` the
fallback rarely triggers.

| `DUAL_VIEWER_MODE` | Behaviour |
| :--- | :--- |
| `concurrent` | **Always run both at the same time** (pair with `LOW_MEMORY=extreme`, ~324 MB). The guardian is the safety valve only. |
| `auto` (default) | Run both together; escalate to time-slice only if RAM proves too small. |
| `time-slice` | Alternate every `TIME_SLICE` seconds. Predictable ~50% uptime each, zero OOM risk. |
| `off` | Legacy: no guardian, no slicing (two viewers can still OOM 512 MB plans). |

## Dashboard GUI

Open **`/`** (or `/gui`) on your service in a browser — no login, no extra
server, ~0 MB of RAM (a static page that polls `/health` every 2 s):

* **Memory card** — live container RSS vs the budget with the hard-threshold
  marker, plus per-viewer bars (9Hits v6 / FeelingSurf) and the peak.
* **Dual-viewer card** — configured vs effective mode, the active viewer, and
  a **countdown to the next time-slice flip**.
* **Viewers card** — running state, phase/pid, silent seconds, restarts for
  each viewer and the memguard intervention counter.
* **Per-slot controls** — Start / Stop / Restart buttons for the 9Hits
  slot, the FeelingSurf slot, the memguard slot, and the health server
  itself (the buttons POST to `/control/<name>/<action>` and the
  supervisor picks up the request on its next tick).
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

* **One slot per service** — `ninehits`, `feelingsurf`, `memguard`, `health`.
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
  on the slot's process tree RSS. When a viewer exceeds its cap, the
  supervisor gracefully restarts **only that slot** (the other one
  keeps running). Default = 0 (off). Sensible values on a 512 MB free
  plan: `400` for both. This is independent of the total-RSS
  `memguard.py`; the per-slot cap catches a single runaway viewer
  before it can crash the cgroup.
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
curl -X POST http://localhost:10000/control/memguard/start

# Tail the last 4 KB of any slot's log
curl http://localhost:10000/logs/9hits
curl http://localhost:10000/logs/feelingsurf
curl http://localhost:10000/logs/memguard
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
actually charges; this is what memguard and the dashboard report):

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
With the *balanced* flags, two Chromium viewers can't both stay resident in
512 MB (~550–620 MB), which is why `auto` mode time-slices them. But
**`LOW_MEMORY=extreme` puts each viewer into single-process mode
(`--single-process --in-process-gpu`)** — measured at **~324 MB for the whole
pair** with two sessions each, stable over 60 s+ of real Chromium 149
(and ~458 MB if FeelingSurf keeps its normal swiftshader processes via
`FS_SP=no`). Both comfortably fit the 512 MB budget, so the two viewers run
**concurrently**:

```env
NINEHITS_ENABLED=yes
FEELINGSURF_ENABLED=yes
DUAL_VIEWER_MODE=concurrent    # both at the same time, no time-slicing
LOW_MEMORY=extreme
MEMGUARD_LIMIT_MB=512
MEMGUARD_HARD_PCT=97           # guardian acts at 497 MB - before the OOM
```

Safety nets keep `--single-process` from ever bricking a viewer:
* **Crash auto-fallback** — if a viewer crash-loops 3× at startup, its
  single-process flags are dropped automatically (it keeps running on the
  balanced set; `NH_SP=no` / `FS_SP=no` force this manually).
* **FeelingSurf GL** — upstream runs swiftshader; under single-process, if FS
  crash-loops, set `FS_GL_MODE=disable-gpu` (or `FS_SP=no`).
* **memguard** still guards at `MEMGUARD_HARD_PCT` of the budget, so a spike
  restarts the heaviest viewer instead of letting the platform OOM the box.

The dashboard (`/`) shows both viewers' RSS bars live, so you can confirm they
are up at the same time.

Watch it live via `GET /health` (JSON) or open **`/`** in a browser for the dashboard GUI:

```json
{
  "dual_viewer_mode": "auto",
  "effective_mode": "time-slice",      // auto escalated - box can't fit both
  "active_viewer": "feelingsurf",      // ninehits is parked until its turn
  "memory_used_mb": 412.5,             // total container RSS right now
  "memory_limit_mb": 512,              // memguard acts before this is hit
  "memory_peak_mb": 498.1,
  "ninehits_rss_mb": 0.0,              // parked viewer holds ~0 MB
  "feelingsurf_rss_mb": 331.2,
  "memguard_interventions": 3,         // times the heaviest viewer was restarted
  "memguard_last_target": "ninehits",
  "next_flip_in_seconds": 412          // countdown in time-slice mode
}
```

Notes:

* `effective_mode == "concurrent"` means **both viewers are earning 100%** of
  the time; `"time-slice"` means they alternate. Either way the container no
  longer OOM-loops.
* Uptime bots will see `"status": "restarting"` while the parked viewer is
  down in time-slice mode — that is expected and is not a crash.
* Want 9Hits alone or FeelingSurf alone on 512 MB? Set
  `NINEHITS_ENABLED=yes` + `FEELINGSURF_ENABLED=no` (or vice versa) — one
  viewer easily fits.
* On hosts with ≥ 2 GB (Fly 4 GB, Oracle 24 GB, HF 16 GB) `LOW_MEMORY=auto`
  disables the flags, memguard never intervenes, and everything runs
  concurrently as before.

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
| **Render** | Blueprint (`render.yaml`) updates the single existing **`9hits-viewer`** web service; `ACCESS_TOKEN` is preconfigured. **Both viewers enabled** (`NINEHITS_ENABLED=yes`) with `DUAL_VIEWER_MODE=auto` + `LOW_MEMORY=auto` + `MEMGUARD_LIMIT_MB=512`. | The 512 MB free plan is handled by the three-layer stack (see [Running both viewers on 512 MB](#running-both-viewers-on-512-mb-render-free)): memory flags shrink both Chromiums, memguard restarts the heaviest viewer before the platform OOMs the container, and `auto` alternates the two if they still don't fit. |
| **Koyeb** | ACCESS_KEY + ACCESS_TOKEN are hard-coded in `koyeb.yaml`. | Uses `koyeb.yaml`; one service runs both viewers. |
| **Fly.io** | Deploy this repository Dockerfile and set both secrets. | Uses `fly.toml`; one service runs both viewers. |
| **Railway** | Deploy this repository Dockerfile and set both secrets. | Uses `railway.json`; one service runs both viewers. |
| **Zeabur** | Deploy this repository Dockerfile and set both secrets. | Uses `zeabur.json`; one service runs both viewers. |
| **Hugging Face Spaces** | Standard Gradio Space; `app_hf.py` downloads and runs both viewers natively. | 16 GB runtime: 1× 9Hits system session + 3× FeelingSurf, with no memory guardian or low-memory tuning. |

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

Viewer config flags are applied by the **init pass** (`nhviewer <flags> --exit-on-init`); the **run pass** then starts with `--auto-start --in-loop --render-to-terminal [--reset-interval=...]` — the same flow as the [official 9Hits v6 installer](https://github.com/9hitste/install).

| Env var | `nhviewer` flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `NINEHITS_ENABLED` | — | `no` | `yes`/`no`/`1`/`0`/`true`/`false`/`on`/`off` — run the 9Hits viewer. Off by default for conservative bare deploys; the Render/Koyeb blueprints set `yes` because the 512 MB stack below keeps both viewers alive |
| `FEELINGSURF_ENABLED` | — | `yes` | `yes`/`no`/`1`/`0`/`true`/`false`/`on`/`off` — run the FeelingSurf viewer (auto-disables quietly when the binary is absent, e.g. the HF Gradio runtime) |
| `DUAL_VIEWER_MODE` | — | `auto` | `auto` / `concurrent` / `time-slice` / `off` — how the two viewers share one small box (see [Running both viewers on 512 MB](#running-both-viewers-on-512-mb-render-free)); `concurrent` + `LOW_MEMORY=extreme` = both at the same time in 512 MB |
| `TIME_SLICE` | — | `1500` | Seconds each viewer runs per turn in `time-slice` mode (25 min default) |
| `LOW_MEMORY` | — | `auto` | `auto` (flags on when box < 1 GB) / `off` / `balanced` / `extreme` (`--single-process` for both viewers, measured ~324 MB pair — lets both run concurrently in 512 MB; crash auto-fallback included) — Chromium memory-shrinking flags applied to both viewers |
| `NH_SP` | — | `yes` | Single-process for 9Hits in `extreme` mode (auto-disabled after 3 startup crashes) |
| `FS_SP` | — | `yes` | Single-process for FeelingSurf in `extreme` mode (auto-disabled after 3 startup crashes) |
| `FS_GL_MODE` | — | `swiftshader` | FeelingSurf GL: `swiftshader` (upstream) or `disable-gpu` (last resort if FS crash-loops under single-process) |
| `FS_SHARE_DISPLAY` | — | `yes` | Reuse the 9Hits Xvfb display for FeelingSurf (one less X server on tight instances) |
| `MEMGUARD_LIMIT_MB` | — | `0` (auto-detect) | Memory budget memguard enforces; set `512` on Render free / Koyeb nano. `0` = cgroup limit, then MemTotal |
| `MEMGUARD_HARD_PCT` | — | `97` | % of budget at which memguard restarts the heaviest viewer (97 = act at 497 MB on a 512 MB box, before the platform OOM) |
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
| `SUPERVISOR_DELAY` | — | `10` | Base restart cooldown (s) before relaunching an exited slot (alias: `RESTART_DELAY`) |
| `SUPERVISOR_MAX_DELAY` | — | `120` | Ceiling for the exponential backoff when a slot keeps crash-looping |
| `SUPERVISOR_PARK_AFTER` | — | `10` | After N rapid crashes the slot is parked (cooldown extended) |
| `SUPERVISOR_PARK_SECS` | — | `300` | Park duration (s) when a slot is parked. Slot still recovers automatically |
| `SUPERVISOR_TICK` | — | `0.5` | Main loop tick (s). Lower = faster signal/crash response, slightly higher steady-state CPU |
| `NINEHITS_CHECK_INTERVAL` | — | `30` | Seconds between expensive 9Hits checks (memory, child count) |
| `FEELINGSURF_CHECK_INTERVAL` | — | `30` | Seconds between expensive FeelingSurf checks |
| `LOG_DIR` | — | `/logs` | Per-slot log directory. `logs/9hits.log`, `logs/feelingsurf.log`, `logs/memguard.log`, `logs/health.log`, `logs/supervisor.log` |
| `SLOT_LOG_MAX_BYTES` | — | `10485760` | Rotate per-slot log when it exceeds this size |
| `SLOT_LOG_BACKUPS` | — | `2` | Number of rotated copies to keep per slot |
| `NH_MAX_MEMORY_MB` / `NINEHITS_MAX_MEMORY_MB` | — | `0` | Per-slot memory cap (RSS of the process tree). When exceeded, gracefully restart ONLY 9Hits. Sensible value on 512 MB free: `400` |
| `FS_MAX_MEMORY_MB` / `FEELINGSURF_MAX_MEMORY_MB` | — | `0` | Same for FeelingSurf. Sensible value on 512 MB free: `400` |
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
  "memguard_status": "running", "memguard_pid": 12,
  "health_status": "running", "health_pid": 1,
  "dual_viewer_mode": "auto",
  "effective_mode": "concurrent",
  "active_viewer": "both",
  "memory_used_mb": 312.4,             // total container RSS (memguard)
  "memory_limit_mb": 512,
  "memory_peak_mb": 351.1,
  "ninehits_rss_mb": 110.2,
  "feelingsurf_rss_mb": 198.6,
  "memguard_interventions": 0,
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
  30 s). They power the per-slot memory cap: when a slot's RSS
  exceeds its `max_memory_mb` cap, the supervisor restarts only
  that slot.
* Backward-compat flat fields (`viewer_pid`, `viewer_phase`,
  `viewer_silent_seconds`, `xvfb_running`, `restarts`, `viewer_running`,
  `feelingsurf_running`, `feelingsurf_pid`, `feelingsurf_restart_count`,
  `feelingsurf_supervisor_running`, ...) are still emitted so older
  uptime bots do not break.

Point any free uptime monitor (**UptimeRobot, Better Stack, Cron-job.org, Kuma**) to ping `https://<your-app>/health` every **5 to 10 minutes** to keep free-tier instances active and prevent sleep timeouts.

---

## Troubleshooting

* **Render: `Ran out of memory (used over 512MB)` → "Service recovered" → repeat (~1/min)** — the classic symptom of two un-tuned Chromium viewers on the free 512 MB plan. The current Blueprint runs **both viewers** with the 512 MB survival stack (`DUAL_VIEWER_MODE=auto` + `LOW_MEMORY=auto` + `MEMGUARD_LIMIT_MB=512`), which keeps RSS under the limit: memguard restarts the heaviest viewer before the platform can kill the container, and auto mode alternates the two if they still don't fit. Check `/health` → `effective_mode` / `memory_used_mb` / `memguard_interventions`. If you still see OOM (e.g. a proxy list with many sessions), lower the session count, set `FEELINGSURF_ENABLED=no`, or move to a ≥ 2 GB plan (9Hits v6's official recommendation).
* **`Auth: Duplicate USER on IP [x.x.x.x]`** — Another 9Hits user is already using that public/shared proxy IP. Switch to a system session on a dedicated cloud provider, refresh your Webshare list, or use private proxies.
* **`Auth: Duplicate SESSION on IP [x.x.x.x]`** — Multiple sessions from your account on the same IP. Ensure `SYSTEM_SESSION=no` when using proxies, or enable `CLEAR_ALL_SESSIONS=yes` to clear lingering connections.
* **`Pool error: The public pool is closed!`** — Set `EX_PROXY_SESSIONS=0` (or unset it) and use `BULK_ADD_PROXY_LIST` / `BULK_ADD_PROXY_LIST_URL`, or provide your own custom pool via `EX_PROXY_URL`.
* **`User not found!`** — `ACCESS_KEY` is incorrect or missing.
* **Logs stop right after deploy / viewer never appears (the Aug-2026 upstream change)** — the renewed `9hitste/appv6` image used to extract a ~145 MB bzip2 viewer tarball at every container start through its own `/nh.sh`, stalling for many minutes on free-tier CPUs and then hanging silently. This repo no longer does that: the viewer is extracted at **image build time** and started via the official two-pass flow with our own supervised **Xvfb :99**. If you still see stalls, check `/health` — `viewer_phase` (`init`/`run`/`down`) and `viewer_silent_seconds` tell you exactly where it is.
* **`WATCHDOG: no output ... no CPU progress` in the logs** — the viewer wedged (typically OOM-adjacent on 512 MB instances or a stuck Chromium) and was restarted automatically. If it repeats, lower the session count, set `FEELINGSURF_ENABLED=no`, or move to a bigger instance (v6 recommends ≥ 2 GB RAM).
* **Init pass keeps failing** (`init pass failed/timed out`) — the 9Hits API was unreachable or very slow; the supervisor retries 3× with backoff and then launches anyway (the next restart re-applies config). Increase `INIT_TIMEOUT` on very slow networks.
* **`/dev/shm` is only 64 MB** — free Docker tiers can't set `--shm-size`. The entrypoint tries a best-effort remount; where you control Docker yourself (oracle/compose), keep `shm_size: 2g` (already in `docker-compose.yml`).
* **VNC: watch the viewer live** — set `VNC=yes` + `VNC_PW=<pass>` and connect to port `5901` (only on hosts that expose it; Render web services only expose `$PORT`).
