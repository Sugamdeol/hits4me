# Hugging Face Gradio — native 1×9Hits + 3×FeelingSurf

This repository's Hugging Face entrypoint is `app_hf.py`. It is a normal
Gradio application, not the 512 MB Docker deployment:

- **9Hits v6:** one system session on the Space's outbound IP;
- **FeelingSurf:** three parallel instances using the same token; and
- **memory:** native viewer behavior on the HF runtime, with no memory
  guardian, no time-slice scheduler, and no low-memory flags.

The Docker deployment still contains the separate `start.sh`/`memguard.py`
small-host safeguards for Render and Koyeb. They are not started by the HF
entrypoint.

## HF entrypoint behavior

`app_hf.py` downloads missing viewer binaries without root, starts Xvfb, and
supervises the viewers directly. Its forced native settings are:

```text
NINEHITS_ENABLED=yes
FEELINGSURF_ENABLED=yes
FEELINGSURF_INSTANCES=3
SYSTEM_SESSION=yes
CLEAR_ALL_SESSIONS=yes
EX_PROXY_SESSIONS=0
DUAL_VIEWER_MODE=off
LOW_MEMORY=off
FS_SP=no
FS_RESOLUTION=1920x1080x24
```

The dashboard reads process RSS only for display. It does not kill, restart, or
reconfigure viewers based on memory usage.

## Credentials

`app_hf.py` includes the configured 9Hits `ACCESS_KEY` and FeelingSurf
`ACCESS_TOKEN`. A Space variable with either name overrides the built-in value,
but variables are optional. The values are not written to logs or displayed in
the UI.

## Deploy step by step

1. Create a new Space at <https://huggingface.co/spaces>.
2. Select the **Gradio** SDK. CPU Basic or larger hardware is suitable;
   ZeroGPU is optional.
3. Set `app_file: app_hf.py` in the Space README metadata, or use the
   repository's root `README.md` unchanged.
4. Upload the repository. For a minimal upload, use:
   - `app_hf.py`
   - `health_server.py`
   - `run_pty.py`
   - `feelingsurf-run.sh`
   - `fetch_proxy_list.py`
   - `requirements.txt`
   - `packages.txt`
5. Build or restart the Space. Startup logs should show native mode and the
   two viewer families starting concurrently.

`app.py` is a compatibility wrapper for older Spaces that still point at that
filename. `app_huggingface.py`, `huggingface_app.py`, and `gradio_app.py` are
synchronized aliases.

## Health and dashboard

- Gradio is exposed on `PORT` (normally `7860`).
- `health_server.py` listens on `HEALTH_PORT` (normally `10000`) inside the
  Space and serves `/health`.
- The Gradio UI shows combined logs, per-viewer status, restart counts, and
  read-only process RSS.

The optional ZeroGPU ping button is only a hardware check. 9Hits and
FeelingSurf run outside `@spaces.GPU`, so normal viewer activity does not use
GPU quota.

## Troubleshooting

- **`ACCESS_KEY` missing:** check that `app_hf.py` is the app file; the built-in
  value is applied during import unless a blank override is supplied.
- **FeelingSurf auth failure:** check that `ACCESS_TOKEN` is not overriding the
  built-in value with a stale token. The app exports both uppercase and
  lower-case token names.
- **Binary download failure:** restart the Space after confirming outbound
  network access. Downloads are retried by the supervisor.
- **`Xvfb` missing:** rebuild with `xvfb` present in `packages.txt`.
- **Need memory protection on a small service:** deploy the Dockerfile path for
  Render/Koyeb instead. Do not copy its `LOW_MEMORY=extreme` settings into the
  HF app; HF is intentionally native.
