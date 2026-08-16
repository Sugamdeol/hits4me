---
title: hits4me FREE - 9Hits 1 Session + FeelingSurf 3x (ZeroGPU)
emoji: 🌐
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 4.44.0
app_file: app_hf.py
pinned: false
python_version: "3.10"
---

# hits4me — HF FREE PLAN ZeroGPU (use this README when deploying to Hugging Face)

Free Gradio now runs on **ZeroGPU hardware only** (A100/H200, free 3.5 min/day quota — viewers are CPU-only so quota not burned).

- **Gradio app file:** `app_hf.py` (also `gradio_app.py` / `huggingface_app.py` / `app_huggingface.py`)
- **FREE PLAN:** 9Hits ×1 system session + FeelingSurf ×3 parallel (`FEELINGSURF_INSTANCES=3`) — `DUAL_VIEWER_MODE=concurrent`, 16GB, ZeroGPU.
- **ZeroGPU:** Space → Settings → **Hardware → ZeroGPU** (free). Requires `sdk: gradio` + `spaces` in `requirements.txt` + at least one `@spaces.GPU` (dummy `_zerogpu_ping` in `app_hf.py`). `hardware:` in this YAML is ignored — set via UI or `hf spaces settings --hardware zero-a10g`.

Copy the frontmatter above into your Space's `README.md` (keep `python_version: "3.10"` or `"3.12"` for ZeroGPU) and deploy `app_hf.py` as described in `HF_FREE_PLAN.md`.

Viewers run **outside** `@spaces.GPU` (CPU/Xvfb/Chromium) — only the `⚡ Ping ZeroGPU` button in the dashboard uses GPU (10s per click, negligible vs 3.5 min/day free).

See `HF_FREE_PLAN.md` for full ZeroGPU step-by-step.
