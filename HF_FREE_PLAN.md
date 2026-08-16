# Hugging Face FREE PLAN — Gradio ZeroGPU (1× 9Hits + 3× FeelingSurf)

This repo ships a **dedicated Hugging Face Gradio file for ZeroGPU** (100% free tier, free Gradio now runs on ZeroGPU only — Docker Spaces are paid):

- **9Hits Viewer v6 → 1 system session** (clean cloud IP, no proxy pool, avoids `Duplicate USER on IP`)
- **FeelingSurf Viewer → 3 parallel instances** (same `access_token`, 3× earnings — official 3-container compose collapsed into one Space)

**Hardware:** `ZeroGPU` — free A100/H200 (time-sliced, 3.5 min/day free quota, Pro 8×) + 16 GB RAM · 2 vCPU. Both viewers run **concurrently** (`DUAL_VIEWER_MODE=concurrent`, `LOW_MEMORY=auto`). On 512 MB plans (Render/Koyeb) the same pair needs `extreme` + memguard; on ZeroGPU you get headroom and viewers stay **CPU-only** (quota not burned).

## File

- **`app_hf.py`** — primary HF Gradio ZeroGPU entrypoint (also `gradio_app.py` / `huggingface_app.py` / `app_huggingface.py`).  
  Original `app.py` remains as 9Hits-only legacy; use `app_hf.py` for FREE 1+3 ZeroGPU.

`app_hf.py` includes:
- `import spaces` + dummy `@spaces.GPU(duration=10)` (`_zerogpu_ping`) — **HF requires ≥1 GPU-decorated function** on ZeroGPU or the Space crashes. This ping is **not** auto-called; viewers run outside GPU to save quota (10s per manual ping only).
- ZeroGPU ping button in the dashboard to test GPU quota without starting viewers.

## What it does

- Auto-detects Docker vs HF ZeroGPU runtime:
  - **Docker** (`/opt/9hits/nhviewer` + `/usr/bin/FeelingSurfViewer` + Xvfb) → launches `start.sh` for 9Hits (Xvfb/memguard/health) + 3× `feelingsurf-run.sh` (`:98`/`:97`/`:96`, ports `3000`/`3001`/`3002`). `spaces` is a no-op here.
  - **HF Gradio ZeroGPU** (no baked binaries) → auto-downloads without root:
    - 9Hits v6: `https://dl.9hits.com/9hitsv6-linux64.tar.bz2` → `/tmp/9hits`
    - FeelingSurf 2.5.2: `https://github.com/feelingsurf/viewer/releases/download/2.5.2/FeelingSurfViewer-linux-<arch>-2.5.2.deb` via `dpkg -x` → `/tmp/feelingsurf`
    Then supervises Xvfb + viewers + memguard + health (1+3) — same restart logic as `start.sh`.

- **Gradio dashboard** (auto-refresh 2–3 s, ZeroGPU-aware):
  - Header badges: `ZeroGPU · 16GB` + `spaces ✅/❌`
  - Status cards: 9Hits (phase/pid/restarts/silent) + 3× FeelingSurf (pid/restarts/http) + memory bar (PSS)
  - **ZeroGPU row**: ping input + `⚡ Ping ZeroGPU (10s)` button + result box + quota note (3.5 min/day)
  - Tabs: Combined / 9Hits / FS#1-3 / Setup & Health
  - Secrets checklist, config table (`Hardware: ZeroGPU`), health links

- **Health**: `health_server.py` on `HEALTH_PORT=10000` (`/health`); Gradio on `PORT=7860` (HF exposes 7860). For uptime bots, ping Gradio URL or internal `/health`.

## Free plan env defaults (Space → Settings → Variables and secrets)

| Var | Default (free) | Note |
|-----|----------------|------|
| `ACCESS_KEY` | *(secret)* | 9Hits 32-hex |
| `ACCESS_TOKEN` / `access_token` | *(secret)* | FeelingSurf token |
| `NINEHITS_ENABLED` | `yes` | |
| `FEELINGSURF_ENABLED` | `yes` | |
| `FEELINGSURF_INSTANCES` | `3` | 1–5, default 3 |
| `SYSTEM_SESSION` | `yes` | 1 system session |
| `CLEAR_ALL_SESSIONS` | `yes` | |
| `EX_PROXY_SESSIONS` | `0` | no proxy (free IP) |
| `SESSION_NOTE` | `hf-free-system` | |
| `NOTE` | `hf-free` | |
| `HIDE_BROWSER` | `yes` | |
| `CACHE_LIMIT` | `0` | |
| `RESET_INTERVAL` | `2h` | |
| `DUAL_VIEWER_MODE` | `concurrent` | 16GB ZeroGPU |
| `LOW_MEMORY` | `auto` | off on 16GB |
| `NH_DISPLAY` | `:99` | |
| `FEELINGSURF_DISPLAY` | `:98`→`:96` | 1 per FS |
| `PORT` | `7860` | Gradio |
| `HEALTH_PORT` | `10000` | health |
| `DEFAULT_DL` | `https://dl.9hits.com/9hitsv6-linux64.tar.bz2` | |
| `FSVIEWER_VERSION` | `2.5.2` | |
| `SUPERVISOR_DELAY` | `10` | |
| `FS_SHARE_DISPLAY` | `no` | |

All can be overridden; e.g. `FEELINGSURF_INSTANCES=1` for 1+1, or `5` for 5× (still <16 GB).

## Deploy on Hugging Face ZeroGPU (step-by-step)

### Option A — Direct upload (recommended)

1. **Create Space** → https://huggingface.co/spaces → **Create new Space**
   - Owner: you
   - Name: `hits4me-free` (or any)
   - **SDK: Gradio** (Docker is paid; Gradio+ZeroGPU is free)
   - **Hardware: ZeroGPU** — free A100/H200 (if you see CPU Basic, pick **ZeroGPU**; free Gradio now runs only on ZeroGPU)
   - Visibility: Private (secrets) or Public

2. **Upload files**:
   - Minimal: `app_hf.py` + `health_server.py` + `memguard.py` + `run_pty.py` + `feelingsurf-run.sh` + `fetch_proxy_list.py` + `requirements.txt` + `packages.txt`
   - Or whole repo via `git push` / web uploader — include `app_hf.py`

3. **README frontmatter** — ensure `app_file: app_hf.py` (or rename `app_hf.py` → `app.py`):
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
   python_version: "3.10"
   ---
   ```
   (Hardware is set via **Settings → Hardware → ZeroGPU**, not via YAML — `hardware:` in README is ignored; use `hf spaces settings --hardware zero-a10g` or the UI.)

4. **Secrets**:
   - Settings → Variables and secrets → Secrets: `ACCESS_KEY` (9Hits) + `ACCESS_TOKEN` (FeelingSurf, also accepts `access_token`)
   - Optional variable: `FEELINGSURF_INSTANCES=3`

5. **Build** — HF runs `pip install -r requirements.txt` (`gradio` + `spaces`) + `apt` from `packages.txt`, then `python app_hf.py`
   - Logs: `[setup] Detected memory limit ~16384 MB`, downloads if needed, `Gradio on 0.0.0.0:7860` + `spaces` ping ready
   - Dashboard: header `ZeroGPU · 16GB` + `✅ spaces`, status `OK` when both viewers alive, **ZeroGPU ping button** tests GPU (10s quota per click).

6. **Verify**:
   - Status card: `9Hits ✅` + `FeelingSurf 3/3 alive`
   - Logs tabs: `AUTH ok` / `connected`
   - ZeroGPU: click `⚡ Ping ZeroGPU` → `ZeroGPU ping: hello @ ...` (if `spaces not installed`, add `spaces` to `requirements.txt`)
   - Health: internal `GET http://localhost:10000/health` (memguard stats) — for uptime bots, monitor Gradio URL

### Option B — Duplicate

Push to GitHub → HF → **Duplicate Space** → set Hardware **ZeroGPU** + `app_file: app_hf.py` + same secrets.

## Why 1 + 3 on ZeroGPU?

- **9Hits**: Public pool closed. One system session on ZeroGPU's isolated IP is most reliable free.
- **FeelingSurf**: Official compose is 1 container/viewer. ZeroGPU's 16 GB holds 3× viewers in one Space (~6–8 GB + 9Hits ~400 MB) — same token ×3 = 3× credits. Viewers are CPU (Xvfb/Chromium) so they don't burn GPU quota; only the dummy ping uses GPU.

## Resource notes

- **ZeroGPU free quota**: 3.5 min/day (unauth 2 min), Pro 8×. Viewers **don't** use quota (CPU). Only `⚡ Ping` burns 10s per click — negligible. Don't wrap viewer loops in `@spaces.GPU` or you'll exhaust quota in minutes.
- **If on Render 512 MB**: set `FEELINGSURF_INSTANCES=1`, `LOW_MEMORY=extreme`, `DUAL_VIEWER_MODE=concurrent` (already in `koyeb.yaml`/`render.yaml`). HF ZeroGPU 16GB needs no extreme.
- **Python**: pin `3.10` or `3.12` for ZeroGPU (HF default). Our example uses `3.10`.

## Troubleshooting

- `ACCESS_KEY missing` → red banner; secret typo or not set.
- `FeelingSurf auth failed` → check `ACCESS_TOKEN` case; alias `access_token` works via app.
- `nhviewer/FeelingSurf not found` → no network at boot or missing `xvfb`/`wget`; check Build logs, **Restart Space**. Downloads cached to `/tmp`.
- `Xvfb not found` → ensure `xvfb` in `packages.txt` (already).
- `spaces not installed` → `pip install spaces` missing; we stub it as no-op, but add `spaces>=0.30.0` to `requirements.txt` (already) and rebuild.
- `ZeroGPU hardware / no GPU` → Space hardware is still **CPU Basic**; switch to **ZeroGPU**: Settings → Hardware → **ZeroGPU** (free). Requires `sdk: gradio` + at least one `@spaces.GPU` (we have `_zerogpu_ping`).
- `GPU quota exceeded` → you wrapped viewer in `@spaces.GPU` or spammed Ping; wait 24h reset or upgrade to Pro. Keep viewers CPU.
- `Duplicate USER on IP` (9Hits) → added proxy sessions without `EX_PROXY_URL`; keep `EX_PROXY_SESSIONS=0`.
- `OOM` on ZeroGPU 16GB with 1+3? Never — if you set `5×` and see restarts, lower to 3 or set `LOW_MEMORY=extreme`.

## Files

- `app_hf.py` — main HF FREE 1+3 ZeroGPU Gradio app (also `gradio_app.py` etc.)
- `app.py` — legacy 9Hits-only (kept)
- `health_server.py` / `memguard.py` / `run_pty.py` / `feelingsurf-run.sh`
- `packages.txt` — HF ZeroGPU apt (xvfb, libgtk-3-0, libnotify4, …)
- `requirements.txt` — `gradio>=4.44.0` + `spaces>=0.30.0` (ZeroGPU)

Enjoy free hits on ZeroGPU! 🌐⚡
