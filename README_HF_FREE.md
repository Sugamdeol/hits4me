---
title: hits4me - native Hugging Face Gradio viewer
emoji: 🌐
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.0
app_file: app_hf.py
pinned: false
python_version: "3.10"
---

# hits4me on Hugging Face Gradio

`app_hf.py` is the regular Hugging Face Gradio entrypoint. It runs:

- one 9Hits v6 system session; and
- three FeelingSurf instances using the same access token.

The HF runtime has enough memory for the viewers to run concurrently with their
normal upstream arguments. This entrypoint intentionally uses:

- `LOW_MEMORY=off` — no Chromium memory-reduction flags;
- `DUAL_VIEWER_MODE=off` — no time-slicing or memory guardian; and
- `FS_SP=no` — no single-process Chromium mode.

The dashboard reports read-only process RSS for visibility. It never restarts a
viewer because of memory usage.

## Credentials

The HF entrypoint contains the configured `ACCESS_KEY` and `ACCESS_TOKEN`, so a
new Space works without adding secrets first. Optional Space variables with
those names override the built-in values. The values are never printed in the
logs or dashboard.

## Deploy

1. Create a Hugging Face Space with the **Gradio** SDK. CPU Basic or a larger
   hardware option works; ZeroGPU is optional.
2. Upload the repository, or at least:
   `app_hf.py`, `health_server.py`, `run_pty.py`, `feelingsurf-run.sh`,
   `fetch_proxy_list.py`, `requirements.txt`, and `packages.txt`.
3. Ensure the Space metadata contains `app_file: app_hf.py`.
4. Build the Space. The app downloads the Linux viewer archives when the
   binaries are not already present, starts Xvfb displays, and launches the
   viewers directly.

`app.py` is retained as a compatibility wrapper and imports `app_hf.py`.
`app_huggingface.py`, `huggingface_app.py`, and `gradio_app.py` are synchronized
aliases of the same implementation.

## Runtime defaults

| Variable | HF value | Purpose |
|---|---:|---|
| `NINEHITS_ENABLED` | `yes` | Start one 9Hits system session |
| `FEELINGSURF_ENABLED` | `yes` | Start FeelingSurf |
| `FEELINGSURF_INSTANCES` | `3` | Parallel FeelingSurf processes |
| `SYSTEM_SESSION` | `yes` | Use the clean HF outbound IP |
| `CLEAR_ALL_SESSIONS` | `yes` | Clear stale 9Hits sessions at boot |
| `EX_PROXY_SESSIONS` | `0` | Do not use the closed public proxy pool |
| `DUAL_VIEWER_MODE` | `off` | Native concurrent mode; no scheduler |
| `LOW_MEMORY` | `off` | Normal viewer flags |
| `FS_SP` | `no` | Disable single-process mode |
| `FS_RESOLUTION` | `1920x1080x24` | Normal Xvfb size |
| `PORT` | `7860` | Gradio port |
| `HEALTH_PORT` | `10000` | Internal health server |

The `HEALTH_PORT` endpoint is available inside the Space at
`/health`. The Gradio dashboard is the externally exposed endpoint and shows
viewer logs, process state, credentials presence, and read-only RSS.

## Optional ZeroGPU check

The app keeps a small optional `@spaces.GPU` ping for Spaces configured with
ZeroGPU. It is not used by 9Hits or FeelingSurf. The viewer processes remain
CPU-only, so the ping can be ignored on normal CPU hardware.

## Troubleshooting

- **Viewer binary not found:** check build logs and restart the Space so the
  startup download can be retried.
- **`Xvfb` not found:** keep `xvfb` in `packages.txt` and rebuild.
- **9Hits authentication:** the built-in key is used unless an `ACCESS_KEY`
  variable overrides it.
- **FeelingSurf authentication:** the built-in token is exported both as
  `ACCESS_TOKEN` and lower-case `access_token`; an `ACCESS_TOKEN` variable can
  override it.
- **Need the small-host safeguards:** use `start.sh`/`Dockerfile` on the
  Render or Koyeb deployments instead. They retain the separate low-memory
  configuration for 512 MB services; the HF entrypoint does not use it.
