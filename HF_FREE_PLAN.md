# Hugging Face FREE PLAN — Gradio Deployment (1× 9Hits + 3× FeelingSurf)

This repo now ships a **dedicated Hugging Face Gradio file** for the 100% free tier (2 vCPU · 16 GB RAM) :

- **9Hits Viewer v6 → 1 system session** (clean cloud IP, no proxy pool, avoids `Duplicate USER on IP`)
- **FeelingSurf Viewer → 3 parallel instances** (same `access_token`, 3× earnings — official multi-container pattern collapsed into one Space)

Free hardware is 16 GB, so both viewers run **concurrently** (`DUAL_VIEWER_MODE=concurrent`, `LOW_MEMORY=auto`). On 512 MB plans (Render/Koyeb) the same pair needs `extreme` + memguard; on HF you get headroom.

## File

- **`app_hf.py`** — primary HF Gradio entrypoint (also copied as `gradio_app.py` / `huggingface_app.py` / `app_huggingface.py` for convenience).  
  Original `app.py` remains as 9Hits-only legacy; use `app_hf.py` for FREE 1+3.

## What it does

- Auto-detects Docker vs HF Gradio runtime:
  - **Docker** (`/opt/9hits/nhviewer` + `/usr/bin/FeelingSurfViewer` + Xvfb present) → launches `start.sh` for 9Hits (via its Xvfb/memguard/health) + 3× `feelingsurf-run.sh` (each on `:98`/`:97`/`:96`, ports `3000`/`3001`/`3002`).
  - **HF Gradio** (no baked binaries) → auto-downloads without root:
    - 9Hits v6: `https://dl.9hits.com/9hitsv6-linux64.tar.bz2` → `/tmp/9hits`
    - FeelingSurf 2.5.2: `https://github.com/feelingsurf/viewer/releases/download/2.5.2/FeelingSurfViewer-linux-<arch>-2.5.2.deb` via `dpkg -x` → `/tmp/feelingsurf`
    Then supervises Xvfb + viewers + memguard + health with same restart logic as `start.sh` but tuned for 1+3.

- **Gradio dashboard** (auto-refresh 2–3 s):
  - Status cards: 9Hits (phase/pid/restarts/silent) + 3× FeelingSurf (pid/restarts/http) + memory bar (PSS from `memguard.json`)
  - Tabs: Combined logs, 9Hits log, 3× FeelingSurf logs, Setup/Health
  - Secrets checklist, config table, health links

- **Health**: `health_server.py` still runs on `HEALTH_PORT=10000` (`/health`, `/healthz`, `/ping`) for internal memguard stats; Gradio itself is on `PORT=7860` (HF externally exposes this). For uptime bots, ping the Gradio URL or the internal `/health` on 10000 if you front it.

## Free plan env defaults (set in Space → Settings → Variables and secrets)

| Var | Default (free) | Note |
|-----|----------------|------|
| `ACCESS_KEY` | *(required, secret)* | 9Hits 32-hex key |
| `ACCESS_TOKEN` / `access_token` | *(required, secret)* | FeelingSurf token |
| `NINEHITS_ENABLED` | `yes` | |
| `FEELINGSURF_ENABLED` | `yes` | |
| `FEELINGSURF_INSTANCES` | `3` | NEW — this file's knob (1–5, default 3) |
| `SYSTEM_SESSION` | `yes` | 1 system session |
| `CLEAR_ALL_SESSIONS` | `yes` | clean slate |
| `EX_PROXY_SESSIONS` | `0` | no proxy sessions (free IP) |
| `SESSION_NOTE` | `hf-free-system` | |
| `NOTE` | `hf-free` | |
| `HIDE_BROWSER` | `yes` | headless |
| `CACHE_LIMIT` | `0` | no cache |
| `RESET_INTERVAL` | `2h` | viewer auto-restart |
| `DUAL_VIEWER_MODE` | `concurrent` | 16 GB fits both |
| `LOW_MEMORY` | `auto` | off on 16 GB |
| `NH_DISPLAY` | `:99` | 9Hits Xvfb |
| `FEELINGSURF_DISPLAY` | `:98` (then `:97`,`:96`) | 1 per FS |
| `PORT` | `7860` | Gradio (HF) |
| `HEALTH_PORT` | `10000` | health_server |
| `DEFAULT_DL` | `https://dl.9hits.com/9hitsv6-linux64.tar.bz2` | 9Hits download |
| `FSVIEWER_VERSION` | `2.5.2` | FeelingSurf version |
| `SUPERVISOR_DELAY` | `10` | restart delay |
| `FS_SHARE_DISPLAY` | `no` | each FS gets its own Xvfb |

All can be overridden via env; e.g. set `FEELINGSURF_INSTANCES=1` to run only 1× FeelingSurf + 1× 9Hits, or `FEELINGSURF_INSTANCES=5` for 5× (still <16 GB).

## Deploy on Hugging Face (step-by-step)

### Option A — Direct upload (recommended)

1. Go to <https://huggingface.co/spaces> → **Create new Space**
   - Owner: you
   - Space name: `hits4me-free` (or any)
   - **SDK: Gradio** (not Docker — Docker Spaces are paid; Gradio is free)
   - Hardware: **Free · 2 vCPU · 16 GB RAM** (leave default)
   - Visibility: Private (recommended — secrets stay private) or Public

2. Upload files:
   - **Minimal set**: `app_hf.py` + `health_server.py` + `memguard.py` + `run_pty.py` + `feelingsurf-run.sh` + `fetch_proxy_list.py` + `requirements.txt` + `packages.txt`
   - **Or whole repo**: `git push` or HF web uploader — include `app_hf.py` + keep original `app.py` (you will select `app_hf.py` below)

3. If you uploaded whole repo with both `app.py` and `app_hf.py`, tell HF which file to run:
   - Space → **Settings** → **Variables and secrets** → **New variable**:
     - `GRADIO_APP_FILE` or via HF UI: edit Space's `README.md` frontmatter to set `app_file: app_hf.py`
   - Example `README.md` header for HF (copy to your Space's README):
     ```yaml
     ---
     title: hits4me FREE - 9Hits 1 Session + FeelingSurf 3x
     emoji: 🌐
     colorFrom: blue
     colorTo: indigo
     sdk: gradio
     sdk_version: 4.44.0
     app_file: app_hf.py
     pinned: false
     ---
     ```
     (HF reads `app_file` from README; alternatively rename `app_hf.py` → `app.py` on HF)

4. Add secrets:
   - Space → **Settings** → **Variables and secrets** → **New secret**:
     - `ACCESS_KEY` = your 9Hits key (from https://dash.9hits.com/)
     - `ACCESS_TOKEN` = your FeelingSurf token (from https://www.feelingsurf.fr/member → Profile → API token) — also accepts `access_token` lower-case
   - (Optional) add **Variable** `FEELINGSURF_INSTANCES=3` (or 1/2/5)

5. Wait for build (HF runs `pip install -r requirements.txt` + `apt install` from `packages.txt`, then `python app_hf.py`)
   - Logs show `[setup] Detected memory limit ~16384 MB`, then downloads if needed, then `Gradio on 0.0.0.0:7860`
   - Dashboard appears: green `OK` when both viewers alive

6. Verify:
   - Gradio UI status card shows `9Hits ✅` + `FeelingSurf 3/3 alive`
   - Logs tabs show viewer output (e.g. `9Hits: AUTH ok`, `FeelingSurf: connected`)
   - Internal health: `GET https://<your-space>.hf.space/health` via HF proxy or `https://<space>-10000.hf.space` — or check `GET /health` on `PORT_HEALTH` if you expose it

### Option B — Duplicate this repo

1. Push this repo to your GitHub, then HF Space → **Duplicate Space** or **Import from GitHub**
2. Same secrets + `app_file: app_hf.py` as above

## Why 1 + 3?

- **9Hits**: Public proxy pool is closed. Shared free proxies give `Pool error` / `Duplicate USER on IP`. One system session on HF's isolated AWS IP is most reliable for free tier. If you need more sessions, add your own private pool via `BULK_ADD_PROXY_LIST` / `EX_PROXY_URL`, but keep 1 system + few proxies on HF.
- **FeelingSurf**: Official compose runs one container per viewer, each on a dedicated IP. On HF's 16 GB you can afford 3 containers worth of viewers in one Space (each ~2 GB, total ~6–8 GB + 9Hits ~400 MB). Same token ×3 = 3× credit rate, same IP (HF's IP) but FeelingSurf allows concurrent viewers per token (unlike 9Hits pool).

## Resource notes

- HF free: 16 GB, 2 vCPU → concurrent fine. `LOW_MEMORY=auto` will stay off (chose native flags). Memory bar will show ~4–8 GB used, peak ~9 GB. No time-slice needed.
- If you lower HF to 8 GB or run on Render 512 MB, set `FEELINGSURF_INSTANCES=1` and `LOW_MEMORY=extreme` + `DUAL_VIEWER_MODE=concurrent` (the `start.sh` path already does that for 512 MB). But on HF free, keep defaults.

## Troubleshooting

- `ACCESS_KEY missing` → Gradio banner shows red warning; `ACCESS_KEY` secret not set or typo.
- `FeelingSurf auth failed` → check `ACCESS_TOKEN`; it is lower-case `access_token` inside container (alias `ACCESS_TOKEN` works via this file).
- `nhviewer not found` / `FeelingSurf not found` → Space had no network at boot or `packages.txt` missing `xvfb`/`wget`; check Build logs for download errors, then **Restart Space** (HF → Settings → Restart). Downloads are cached to `/tmp`, so second boot is faster.
- `Xvfb not found` → add `xvfb` to `packages.txt` (already included in this repo's updated `packages.txt`).
- `OOM` or `Ran out of memory` → you won't see this on HF 16 GB with 1+3; if you raised instances to 5 and see restarts, lower to 3 or set `LOW_MEMORY=extreme`.
- `Duplicate USER on IP` (9Hits) → you added proxy sessions but skipped `EX_PROXY_URL`; public pool closed. Use private proxies or keep `EX_PROXY_SESSIONS=0`.

## Files included

- `app_hf.py` — main HF FREE 1+3 Gradio app (also available as `gradio_app.py`, `huggingface_app.py`, `app_huggingface.py`)
- `app.py` — legacy 9Hits-only HF app (kept)
- `health_server.py` / `memguard.py` / `run_pty.py` / `feelingsurf-run.sh` — shared supervisors
- `packages.txt` — expanded for HF (xvfb, libgtk-3-0, libnotify4, etc.)
- `requirements.txt` — `gradio>=4.0.0`

Enjoy free hits! 🌐
