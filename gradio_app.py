#!/usr/bin/env python3
"""
Hugging Face Spaces (regular Gradio runtime) — native viewer entrypoint
==============================================================

Runs BOTH viewers as regular child processes in a Hugging Face Gradio Space (2 vCPU · 16 GB RAM):

  • 9Hits Viewer v6      → 1 system session only (clean cloud IP, no proxy pool)
  • FeelingSurf Viewer    → 3 parallel instances (same access_token, 3× earnings)

Why this FREE PLAN config?
--------------------------
* Hugging Face provides enough memory for the viewers to run natively. This entrypoint
  deliberately disables memory trimming, time-slicing, and the memory guardian; the
  viewers receive their normal upstream arguments. An optional ZeroGPU ping remains
  available for Spaces that expose ZeroGPU, but the viewers never run inside it.
* 9Hits public pool is CLOSED — a single system session on a clean datacenter IP
  (HF's isolated cloud IP) avoids `Auth: Duplicate USER on IP` and is most reliable.
* FeelingSurf official recommendation is 2 GB per container → 3 containers ≈ 6 GB.
  HF's 16 GB runtime easily fits 3× FeelingSurf + 1× 9Hits concurrently (CPU-only).

ZeroGPU note
------------
* Add `spaces` to `requirements.txt` and keep at least one `@spaces.GPU` function (dummy `_zerogpu_ping`) — HF requires it or the Space crashes on ZeroGPU.
  Viewers themselves run **outside** `@spaces.GPU` (CPU) to avoid burning GPU quota; only the ping button uses GPU (10s per call, negligible vs 3.5 min/day free).

How it works on HF vs Docker
-----------------------------
* **Docker (local / Koyeb / Render / Fly) :** the image already contains
  `/opt/9hits/nhviewer` and `/usr/bin/FeelingSurfViewer`. This file just
  launches `start.sh` for the 9Hits viewer and additionally spawns two extra
  FeelingSurf instances. The HF entrypoint forces native mode, so `start.sh`
  does not start its small-host memory guardian.
  `spaces` is a no-op here.

* **HF Gradio runtime (no Docker image):** the binaries are absent. This file
  auto-downloads at startup (no root needed):
    - 9Hits v6  → https://dl.9hits.com/9hitsv6-linux64.tar.bz2  → /tmp/9hits
    - FeelingSurf → https://github.com/feelingsurf/viewer/releases/download/2.5.2/…
      extracted via `dpkg -x` → /tmp/feelingsurf/FeelingSurfViewer
  Then it launches Xvfb (:99 for 9Hits, :98/:97/:96 for FeelingSurf) and supervises
  viewers with the same restart logic as `start.sh` but tuned for 1+3 sessions.

Environment (set via HF Space → Settings → Variables and secrets)
-----------------------------------------------------------------
Credentials are preconfigured below for this Space. Environment variables
with the same names still override the built-in values when supplied.

All others have FREE PLAN defaults below; override only if you know why:

  NINEHITS_ENABLED=yes   FEELINGSURF_ENABLED=yes
  FEELINGSURF_INSTANCES=3   (this file's extra knob, default 3)
  SYSTEM_SESSION=yes   CLEAR_ALL_SESSIONS=yes   EX_PROXY_SESSIONS=0
  SESSION_NOTE=hf-free-system   NOTE=hf-free
  DUAL_VIEWER_MODE=off   LOW_MEMORY=off   (native HF mode; no memory guardian)
  RESET_INTERVAL=2h   CACHE_LIMIT=0   HIDE_BROWSER=yes
  NH_DISPLAY=:99   FEELINGSURF_DISPLAY=:98 etc
  PORT=7860 (Gradio)  HEALTH_PORT=10000 (combined /health)
  DEFAULT_DL=https://dl.9hits.com/9hitsv6-linux64.tar.bz2
  FSVIEWER_VERSION=2.5.2

Gradio dashboard
----------------
  * Status cards for 9Hits + 3× FeelingSurf + memory
  * Live logs (combined + per-viewer, auto-refresh every 3s)
  * Health JSON is still available at :10000/health (and /health proxy on 7860)

Deploy (standard HF Gradio)
---------------------
1. Create a new HF Space with the **Gradio** SDK (CPU Basic or any larger
   hardware; ZeroGPU is optional)
   · Title: hits4me — 9Hits 1 Session + FeelingSurf 3x
   · `sdk: gradio`, `sdk_version: 4.44.0` (or 5.x), `app_file: app_hf.py`
   · Python 3.10 or 3.12
2. Upload this repo or at least `app_hf.py` + `health_server.py` +
   `run_pty.py` + `feelingsurf-run.sh` + `fetch_proxy_list.py` + `requirements.txt` + `packages.txt`.
3. The built-in credentials are used automatically. Optional Space variables
   `ACCESS_KEY` and `ACCESS_TOKEN` override them without a code change.
4. If repo has both `app.py` and `app_hf.py`, ensure README frontmatter `app_file: app_hf.py`.
5. Build → Gradio UI appears and both viewers start. The optional ZeroGPU ping can be ignored on CPU hardware.

See also `app.py`, which imports this file as a compatibility entrypoint.
"""

import os
import sys
import json
import time
import signal
import shutil
import subprocess
import threading
import pathlib
import urllib.request
import tarfile
import http.client
from concurrent.futures import ThreadPoolExecutor

# --------------------------------------------------------------------------- #
# Gradio + ZeroGPU imports (optional — fallbacks for local/Docker)
# --------------------------------------------------------------------------- #
try:
    import gradio as gr
except ImportError:
    gr = None
    print("[app_hf] WARNING: gradio not installed — dashboard disabled", flush=True)

# ZeroGPU: Hugging Face free tier Gradio now runs on ZeroGPU hardware.
# ZeroGPU requires at least one @spaces.GPU-decorated function (otherwise the
# Space will crash on ZeroGPU). The decorator is a no-op on CPU/local, so it
# is safe to keep unconditionally. Viewer processes themselves are CPU-only
# (Xvfb/Chromium) and deliberately run OUTSIDE @spaces.GPU to avoid burning
# the free daily GPU quota (3.5 min/day). Only a tiny dummy function uses GPU.
try:
    import spaces  # pip install spaces (HF ZeroGPU helper)
    SPACES_AVAILABLE = True
except ImportError:
    SPACES_AVAILABLE = False
    # lightweight stub for CPU/Docker/local — keeps same @spaces.GPU API
    class _SpacesStub:
        def GPU(self, *args, **kwargs):
            def decorator(fn):
                return fn
            # also supports @spaces.GPU without parens
            if args and callable(args[0]) and not kwargs:
                return args[0]
            return decorator
        def __getattr__(self, _name):
            return lambda *a, **k: (lambda f: f) if a and callable(a[0]) else lambda f: f
    spaces = _SpacesStub()
    print("[app_hf] spaces not installed — ZeroGPU decorator is no-op (pip install spaces for HF ZeroGPU)", flush=True)

# Dummy GPU function to satisfy ZeroGPU scheduler — must exist at import time
# Duration 10s keeps quota negligible; never auto-called (viewers use CPU).
@spaces.GPU(duration=10)  # type: ignore[misc]
def _zerogpu_ping(message: str = "ok") -> str:
    """ZeroGPU health ping — proves Space is ZeroGPU-compatible.

    Keep at least one @spaces.GPU function in any ZeroGPU Space; HF will
    error if none exists. This is NOT used by 9Hits/FeelingSurf viewers
    (they are CPU-only Chromium) — calling it consumes GPU quota, so it is
    only invoked via the dashboard button, not on auto-refresh.
    """
    import time as _t
    return f"ZeroGPU ping: {message} @ {_t.strftime('%H:%M:%S')} (quota 10s, free 3.5 min/day)"

# Also expose a tiny helper for the UI button (CPU wrapper that calls GPU)
def zerogpu_ping_wrapper(msg: str = "hello") -> str:
    try:
        return _zerogpu_ping(msg)
    except Exception as e:
        return f"ZeroGPU ping failed: {e} (are you on ZeroGPU hardware? Set Space → Settings → Hardware → ZeroGPU)"

# --------------------------------------------------------------------------- #
# FREE PLAN defaults (user can override via env)
# --------------------------------------------------------------------------- #
PORT_GRADIO = int(os.environ.get("PORT", "7860") or 7860)
PORT_HEALTH = int(os.environ.get("HEALTH_PORT", "10000") or 10000)
FSVIEWER_VERSION = os.environ.get("FSVIEWER_VERSION", "2.5.2")

# Free plan viewer counts
NINEHITS_SESSIONS = 1  # system session only
FEELINGSURF_INSTANCES = int(os.environ.get("FEELINGSURF_INSTANCES", "3") or 3)
if FEELINGSURF_INSTANCES < 1:
    FEELINGSURF_INSTANCES = 1
if FEELINGSURF_INSTANCES > 5:
    FEELINGSURF_INSTANCES = 5  # sanity cap, 16GB can still do 5

# Env defaults for free plan (do not overwrite if user already set)
FREE_DEFAULTS = {
    "NINEHITS_ENABLED": "yes",
    "FEELINGSURF_ENABLED": "yes",
    "SYSTEM_SESSION": "yes",
    "CLEAR_ALL_SESSIONS": "yes",
    "EX_PROXY_SESSIONS": "0",
    "SESSION_NOTE": "hf-free-system",
    "NOTE": "hf-free",
    "HIDE_BROWSER": "yes",
    "ALLOW_POPUPS": "no",
    "ALLOW_ADULT": "no",
    "ALLOW_CRYPTO": "no",
    "CACHE_LIMIT": "0",
    "RESET_INTERVAL": "2h",
    "SUPERVISOR_DELAY": "10",
    # HF has enough memory: keep the viewers in normal native mode. `off` is
    # also important for the Docker-like fallback because start.sh uses it to
    # skip its small-host memory guardian and time-slice scheduler.
    "DUAL_VIEWER_MODE": "off",
    "LOW_MEMORY": "off",
    "TIME_SLICE": "1500",
    "NH_DISPLAY": ":99",
    "FEELINGSURF_DISPLAY": ":98",
    "FEELINGSURF_PORT": "3000",
    "INIT_TIMEOUT": "300",
    "NH_WATCHDOG": "yes",
    "NH_WATCHDOG_STUCK": "600",
    "FS_SP": "no",
    "FS_GL_MODE": "swiftshader",
    "FS_SHARE_DISPLAY": "no",  # each FeelingSurf gets its own Xvfb (3×)
    "DEFAULT_DL": "https://dl.9hits.com/9hitsv6-linux64.tar.bz2",
}
for k, v in FREE_DEFAULTS.items():
    if not os.environ.get(k):
        os.environ[k] = v

# --------------------------------------------------------------------------- #
# HF credentials
# --------------------------------------------------------------------------- #
# The requested HF deployment is self-contained. These are the credentials
# already used by the repository's deployment manifests. A Space variable can
# still override either value without requiring another code change. Never log
# the values themselves.
HF_ACCESS_KEY = '23f9097a8d823267188c49b3cc0598b1'
HF_ACCESS_TOKEN = '27f77ef00e768cdbdc7352f3456e35b5'

if not os.environ.get("ACCESS_KEY"):
    os.environ["ACCESS_KEY"] = HF_ACCESS_KEY
_access_token = os.environ.get("ACCESS_TOKEN") or os.environ.get("access_token") or HF_ACCESS_TOKEN
os.environ["ACCESS_TOKEN"] = _access_token
os.environ["access_token"] = _access_token

# This entrypoint is intentionally native. Do not inherit small-host settings
# from an old Space configuration and accidentally start a guardian/scheduler.
os.environ["DUAL_VIEWER_MODE"] = "off"
os.environ["LOW_MEMORY"] = "off"
os.environ["FS_SP"] = "no"
os.environ["FS_MEM_FLAGS"] = ""

# NH paths — on HF we cannot write to /opt, so use /tmp/9hits
if not os.environ.get("NH_DIR"):
    if os.path.exists("/opt/9hits/nhviewer"):
        os.environ["NH_DIR"] = "/opt/9hits"
    else:
        os.environ["NH_DIR"] = "/tmp/9hits"
NH_DIR = os.environ["NH_DIR"]
NH_BIN = os.environ.get("NH_BIN", os.path.join(NH_DIR, "nhviewer"))

# FeelingSurf binary location — try Docker path first, then HF /tmp path
FS_BIN_CANDIDATES = [
    "/usr/bin/FeelingSurfViewer",
    "/tmp/feelingsurf/FeelingSurfViewer",
    "/tmp/feelingsurf/usr/bin/FeelingSurfViewer",
    "/tmp/feelingsurf/opt/FeelingSurfViewer/FeelingSurfViewer",
]
# Will be resolved after ensure step
RESOLVED_FS_BIN = None

# Log buffers (per viewer, keep last N lines) — dynamic for up to 5 FS instances
LOG_BUFFERS = {
    "combined": [],
    "9hits": [],
    "health": [],
    "setup": [],
}
# Pre-populate feelingsurf-1..5 dynamically (supports FEELINGSURF_INSTANCES up to 5)
for _i in range(1, 6):
    LOG_BUFFERS[f"feelingsurf-{_i}"] = []
LOG_LOCK = threading.Lock()
MAX_LOG_LINES = 400

def log(kind: str, msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] [{kind}] {msg}"
    with LOG_LOCK:
        for k in ("combined", kind):
            if k not in LOG_BUFFERS:
                LOG_BUFFERS[k] = []
            LOG_BUFFERS[k].append(line)
            if len(LOG_BUFFERS[k]) > MAX_LOG_LINES:
                LOG_BUFFERS[k] = LOG_BUFFERS[k][-MAX_LOG_LINES:]
    print(line, flush=True)

def get_logs(kind="combined", last=60):
    with LOG_LOCK:
        buf = LOG_BUFFERS.get(kind, [])
        if not buf:
            return f"No logs yet for {kind} — starting..."
        return "\n".join(buf[-last:])

# --------------------------------------------------------------------------- #
# Helpers — memory, pid checks, http
# --------------------------------------------------------------------------- #
def _read_int(path, default=0):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except Exception:
        return default

def _pid_alive(pid):
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def _exe(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""

def detect_mem_limit_mb():
    # try cgroup, else MemTotal
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(p, "r") as fh:
                raw = fh.read().strip()
            if raw and raw != "max":
                v = int(raw)
                if 0 < v < 1 << 40:
                    return v // (1024*1024)
        except Exception:
            pass
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0

MEM_LIMIT_MB = detect_mem_limit_mb()

# The dashboard reports ordinary process RSS for visibility only. It never
# takes action based on this value and does not need a sidecar/guardian.
MEMORY_PEAK_MB = 0.0


def _proc_parent_map():
    """Return a best-effort parent -> children map from /proc."""
    children = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return children
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                raw = fh.read()
            # The comm field can contain spaces and parentheses. Everything
            # after its final ')' starts with state, then ppid.
            right = raw.rfind(b")")
            fields = raw[right + 2 :].split()
            parent = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(parent, []).append(pid)
    return children


def _process_tree(root_pid):
    """Return root_pid and all descendants visible to this process."""
    if root_pid <= 1:
        return set()
    children = _proc_parent_map()
    result = set()
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        if pid in result:
            continue
        result.add(pid)
        pending.extend(children.get(pid, ()))
    return result


def _rss_mb(pid):
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _process_tree_rss_mb(root_pid):
    if root_pid <= 1:
        return None
    pids = _process_tree(root_pid)
    if not pids:
        return None
    return round(sum(_rss_mb(pid) for pid in pids), 1)


def _safe_process_rss(pid):
    if pid <= 1 or not _pid_alive(pid):
        return None
    return _process_tree_rss_mb(pid)

log("setup", f"Detected memory limit ~{MEM_LIMIT_MB} MB — native HF mode")

# --------------------------------------------------------------------------- #
# Ensure viewers are present (download for HF Gradio runtime)
# --------------------------------------------------------------------------- #
def ensure_9hits():
    global NH_DIR, NH_BIN
    if os.path.exists(NH_BIN) and os.access(NH_BIN, os.X_OK):
        log("setup", f"9Hits viewer found at {NH_BIN}")
        return True

    # Also check alternative baked path
    baked = "/etc/9hitsv6-linux64.tar.bz2"
    if os.path.exists(baked):
        log("setup", f"Extracting baked 9Hits tarball {baked} → {NH_DIR} (Docker path)")
        try:
            os.makedirs("/tmp/nhextract", exist_ok=True)
            subprocess.run(["tar", "-xjf", baked, "-C", "/tmp/nhextract"], check=True, timeout=120)
            # locate nhviewer
            found = None
            if os.path.exists("/tmp/nhextract/nhviewer"):
                found = "/tmp/nhextract"
            else:
                for e in os.listdir("/tmp/nhextract"):
                    p = os.path.join("/tmp/nhextract", e)
                    if os.path.isdir(p) and os.path.exists(os.path.join(p, "nhviewer")):
                        found = p
                        break
            if found:
                shutil.rmtree(NH_DIR, ignore_errors=True)
                shutil.copytree(found, NH_DIR, dirs_exist_ok=True)
                os.chmod(NH_BIN, 0o755)
                subprocess.run(["chmod", "-R", "a+rwX", NH_DIR], check=False)
                shutil.rmtree("/tmp/nhextract", ignore_errors=True)
                log("setup", f"9Hits extracted to {NH_DIR}")
                return True
        except Exception as e:
            log("setup", f"Failed to extract baked tarball: {e}")

    # Download path for HF Gradio
    urls = []
    dl = os.environ.get("DEFAULT_DL", "").strip()
    if dl:
        urls.append(dl)
    # official v6 URL
    urls.append("https://dl.9hits.com/9hitsv6-linux64.tar.bz2")
    # legacy fallbacks
    urls.append("https://f.9hits.com/9hviewer/9hviewer-linux-x64.tar.bz2")
    urls.append("https://rs.9hits.com/9hviewer/9hits-linux-x64-2.5.1.tar.bz2")

    NH_DIR = os.environ["NH_DIR"]  # may be /tmp/9hits
    os.makedirs(NH_DIR, exist_ok=True)
    for url in urls:
        if not url:
            continue
        target = "/tmp/nhviewer-download.tar.bz2"
        log("setup", f"Trying 9Hits download: {url}")
        try:
            # wget with timeout, or urllib fallback
            if shutil.which("wget"):
                rc = subprocess.run(["wget", "-q", "--tries=2", "--timeout=30", "-O", target, url], timeout=60)
                if rc.returncode != 0 or not os.path.exists(target) or os.path.getsize(target) < 1024*1024:
                    raise RuntimeError(f"wget failed rc={rc.returncode}")
            else:
                urllib.request.urlretrieve(url, target)
            if os.path.getsize(target) < 1024*1024:
                raise RuntimeError("download too small")
            log("setup", f"Downloaded {os.path.getsize(target)//(1024*1024)} MB → extracting to {NH_DIR}")
            # try tar
            os.makedirs("/tmp/nhextract2", exist_ok=True)
            # clean
            for e in os.listdir("/tmp/nhextract2"):
                p = os.path.join("/tmp/nhextract2", e)
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            subprocess.run(["tar", "-xjf", target, "-C", "/tmp/nhextract2"], check=True, timeout=120)
            # find
            found = None
            if os.path.exists("/tmp/nhextract2/nhviewer"):
                found = "/tmp/nhextract2"
            else:
                for e in os.listdir("/tmp/nhextract2"):
                    p = os.path.join("/tmp/nhextract2", e)
                    if os.path.isdir(p) and os.path.exists(os.path.join(p, "nhviewer")):
                        found = p
                        break
            if not found:
                # maybe tar had single dir already stripped?
                # try listing
                candidates = []
                for root, dirs, files in os.walk("/tmp/nhextract2"):
                    if "nhviewer" in files:
                        candidates.append(root)
                        break
                if candidates:
                    found = candidates[0]
            if found:
                # copy
                if found != NH_DIR:
                    # if NH_DIR exists, remove first
                    if os.path.exists(NH_DIR):
                        shutil.rmtree(NH_DIR, ignore_errors=True)
                    shutil.copytree(found, NH_DIR, dirs_exist_ok=True)
                # ensure perms
                nh_bin_cand = os.path.join(NH_DIR, "nhviewer")
                if os.path.exists(nh_bin_cand):
                    os.chmod(nh_bin_cand, 0o755)
                    subprocess.run(["chmod", "-R", "a+rwX", NH_DIR], check=False)
                    log("setup", f"9Hits installed to {NH_DIR} from {url}")
                    try:
                        os.remove(target)
                    except: pass
                    shutil.rmtree("/tmp/nhextract2", ignore_errors=True)
                    NH_BIN = nh_bin_cand
                    os.environ["NH_BIN"] = NH_BIN
                    return True
            log("setup", f"Extracted but nhviewer not found from {url} (checked /tmp/nhextract2)")
        except Exception as e:
            log("setup", f"Download/extract failed for {url}: {e}")
            try:
                if os.path.exists(target):
                    os.remove(target)
            except: pass
            continue
    log("setup", "WARNING: 9Hits viewer not available — will retry every 5 min (check ACCESS_KEY and network)")
    return False

def ensure_feelingsurf():
    global RESOLVED_FS_BIN
    # Check Docker path first
    for cand in FS_BIN_CANDIDATES:
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            RESOLVED_FS_BIN = cand
            log("setup", f"FeelingSurf found at {cand}")
            return True
    # Check if /usr/bin/FeelingSurfViewer exists via which
    w = shutil.which("FeelingSurfViewer")
    if w and os.access(w, os.X_OK):
        RESOLVED_FS_BIN = w
        log("setup", f"FeelingSurf found via which: {w}")
        return True

    # Need to download deb and extract without root (dpkg -x)
    arch = "amd64"
    try:
        # dpkg --print-architecture
        out = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            a = out.stdout.strip()
            if a in ("amd64", "arm64", "armhf"):
                arch = a
            elif a == "x86_64":
                arch = "amd64"
    except Exception:
        pass
    # uname fallback
    if arch == "amd64":
        try:
            m = os.uname().machine
            if m in ("aarch64", "arm64"):
                arch = "arm64"
        except Exception:
            pass

    url = f"https://github.com/feelingsurf/viewer/releases/download/{FSVIEWER_VERSION}/FeelingSurfViewer-linux-{arch}-{FSVIEWER_VERSION}.deb"
    deb = "/tmp/feelingsurf.deb"
    extract_dir = "/tmp/feelingsurf"
    log("setup", f"FeelingSurf not found — downloading {url} → {deb} (arch={arch})")
    try:
        if shutil.which("wget"):
            rc = subprocess.run(["wget", "-q", "--tries=2", "--timeout=30", "-O", deb, url], timeout=90)
            if rc.returncode != 0:
                raise RuntimeError(f"wget rc={rc.returncode}")
        else:
            urllib.request.urlretrieve(url, deb)
        if not os.path.exists(deb) or os.path.getsize(deb) < 1024*1024:
            raise RuntimeError("deb too small or missing")
        log("setup", f"Downloaded FeelingSurf deb {os.path.getsize(deb)//(1024*1024)} MB")
        os.makedirs(extract_dir, exist_ok=True)
        # try dpkg -x (no root needed), else ar + tar
        extracted = False
        if shutil.which("dpkg"):
            rc = subprocess.run(["dpkg", "-x", deb, extract_dir], timeout=60)
            if rc.returncode == 0:
                extracted = True
        if not extracted and shutil.which("ar"):
            # ar x deb + tar
            tmp_ar = "/tmp/fs_ar"
            os.makedirs(tmp_ar, exist_ok=True)
            rc = subprocess.run(["ar", "x", deb], cwd=tmp_ar, timeout=30)
            if rc.returncode == 0:
                # find data.tar.*
                for f in os.listdir(tmp_ar):
                    if f.startswith("data.tar"):
                        subprocess.run(["tar", "-xf", os.path.join(tmp_ar, f), "-C", extract_dir], check=True, timeout=60)
                        extracted = True
                        break
            shutil.rmtree(tmp_ar, ignore_errors=True)
        if not extracted:
            # fallback: try bsdtar or 7z?
            log("setup", "Failed to extract deb via dpkg/ar — trying tar directly")
            subprocess.run(["tar", "-xf", deb, "-C", extract_dir], check=False, timeout=60)
        # locate binary
        candidates = []
        for root, dirs, files in os.walk(extract_dir):
            if "FeelingSurfViewer" in files:
                candidates.append(os.path.join(root, "FeelingSurfViewer"))
        if candidates:
            # prefer the one that is executable
            bin_path = candidates[0]
            # Ensure executable
            os.chmod(bin_path, 0o755)
            RESOLVED_FS_BIN = bin_path
            log("setup", f"FeelingSurf extracted to {bin_path}")
            # also copy deps? deb should have placed libs
            return True
        log("setup", f"FeelingSurf deb extracted but binary not found in {extract_dir} (listed {os.listdir(extract_dir)[:10]})")
    except Exception as e:
        log("setup", f"FeelingSurf download/extract failed: {e}")
    log("setup", "WARNING: FeelingSurf not available — will run 9Hits only until token/network available")
    return False

# Initial ensure is now lazy — done inside launch threads via retry loops.
# We only do a quick local check without network to set RESOLVED_FS_BIN if already present,
# so HF import stays fast.
try:
    for _cand in FS_BIN_CANDIDATES:
        if os.path.exists(_cand) and os.access(_cand, os.X_OK):
            RESOLVED_FS_BIN = _cand
            break
    if not RESOLVED_FS_BIN:
        w = shutil.which("FeelingSurfViewer")
        if w and os.access(w, os.X_OK):
            RESOLVED_FS_BIN = w
except Exception:
    pass
# (Full downloads happen in launch_* threads, not at import)

# --------------------------------------------------------------------------- #
# Viewer launch helpers
# --------------------------------------------------------------------------- #
def run_process_with_logs(cmd, env, log_key, cwd=None, restart_delay=10):
    """Generic supervisor: run cmd forever, restart on exit, capture logs."""
    while True:
        try:
            log(log_key, f"Launching: {' '.join(cmd[:6])}... (env PORT={env.get('PORT','?')})")
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            # write pid file for health_server compatibility
            if log_key == "9hits":
                try:
                    with open("/tmp/viewer.pid", "w") as fh:
                        fh.write(str(proc.pid))
                    with open("/tmp/viewer.state", "w") as fh:
                        fh.write("run")
                except: pass
            elif log_key.startswith("feelingsurf"):
                idx = log_key.split("-")[-1]
                try:
                    with open(f"/tmp/feelingsurf-{idx}.pid", "w") as fh:
                        fh.write(str(proc.pid))
                    # also write legacy for health_server (first instance)
                    if idx == "1":
                        with open("/tmp/feelingsurf.pid", "w") as fh2:
                            fh2.write(str(proc.pid))
                except: pass
            # stream logs
            for line in iter(proc.stdout.readline, ""):
                if line:
                    clean = line.rstrip()
                    if clean:
                        log(log_key, clean)
                        # also update heartbeat for 9Hits watchdog
                        if log_key == "9hits":
                            try:
                                pathlib.Path("/tmp/viewer.lastoutput").touch()
                            except: pass
            proc.wait()
            code = proc.returncode
        except FileNotFoundError as e:
            log(log_key, f"Binary not found: {e} — retrying in {restart_delay}s")
            code = 127
        except Exception as e:
            log(log_key, f"Supervisor error: {e} — retrying in {restart_delay}s")
            code = 1
        # cleanup pid files
        try:
            if log_key == "9hits":
                if os.path.exists("/tmp/viewer.pid"):
                    os.remove("/tmp/viewer.pid")
                pathlib.Path("/tmp/viewer.state").write_text("down")
                # count restarts
                c = _read_int("/tmp/viewer.restarts", 0)
                pathlib.Path("/tmp/viewer.restarts").write_text(str(c+1))
            elif log_key.startswith("feelingsurf"):
                idx = log_key.split("-")[-1]
                try:
                    os.remove(f"/tmp/feelingsurf-{idx}.pid")
                except: pass
                if idx == "1":
                    try:
                        os.remove("/tmp/feelingsurf.pid")
                    except: pass
                c = _read_int(f"/tmp/feelingsurf-{idx}.restarts", 0)
                pathlib.Path(f"/tmp/feelingsurf-{idx}.restarts").write_text(str(c+1))
                if idx == "1":
                    cc = _read_int("/tmp/feelingsurf.restarts", 0)
                    pathlib.Path("/tmp/feelingsurf.restarts").write_text(str(cc+1))
        except: pass
        log(log_key, f"Exited (code {code}) — restarting in {restart_delay}s")
        # wait but allow interrupt via TERM
        try:
            time.sleep(restart_delay)
        except: pass
        # re-ensure binary exists before relaunch
        if log_key == "9hits":
            if not os.path.exists(NH_BIN):
                log(log_key, "nhviewer missing — re-attempting download")
                ensure_9hits()
        elif log_key.startswith("feelingsurf"):
            if not RESOLVED_FS_BIN or not os.path.exists(RESOLVED_FS_BIN):
                log(log_key, "FeelingSurf binary missing — re-attempting download")
                ensure_feelingsurf()

def launch_9hits():
    """Launch 9Hits via start.sh for Docker, or direct nhviewer for HF."""
    # Prefer start.sh if present and Docker image (has /opt/9hits and Xvfb)
    start_sh = os.path.join(os.path.dirname(__file__), "start.sh")
    # Also check /start.sh (Docker WORKDIR /)
    if not os.path.exists(start_sh):
        if os.path.exists("/start.sh"):
            start_sh = "/start.sh"
    # Decide mode: if start.sh exists and we are in Docker-like env with xvfb,
    # use it with FEELINGSURF_ENABLED=no (we handle FS separately)
    use_start_sh = os.path.exists(start_sh) and shutil.which("Xvfb") and os.path.exists(NH_BIN)
    # But on HF, start.sh expects many deps; we can still use it if we ensure download
    # However start.sh's nh_supervisor writes to /tmp/viewer.pid etc, which we want.
    # For FREE PLAN we want 1 session only — ensure env already set
    env = os.environ.copy()
    # Health server port separate from Gradio
    env["PORT"] = str(PORT_HEALTH)
    env["NINEHITS_ENABLED"] = "yes"
    # Disable FeelingSurf inside start.sh to avoid duplicate single instance
    # We'll run 3× ourselves, so tell start.sh not to.
    env["FEELINGSURF_ENABLED"] = "no"
    # Ensure 1 session only
    env["SYSTEM_SESSION"] = env.get("SYSTEM_SESSION", "yes")
    env["CLEAR_ALL_SESSIONS"] = env.get("CLEAR_ALL_SESSIONS", "yes")
    # Don't set proxy sessions — keep 0
    # Hide browser yes for headless
    env["HIDE_BROWSER"] = env.get("HIDE_BROWSER", "yes")
    # HF has ample RAM: always use normal viewer arguments and never start
    # start.sh's small-host guardian/scheduler in the Docker fallback.
    env["LOW_MEMORY"] = "off"
    env["DUAL_VIEWER_MODE"] = "off"
    # Make sure reset interval set
    if not env.get("RESET_INTERVAL"):
        env["RESET_INTERVAL"] = "2h"

    if use_start_sh and os.path.exists("/usr/bin/Xvfb"):
        # Use start.sh as supervisor for the baked Docker viewer (native mode).
        # But we need to run it without exec-ing health_server on same PORT? It will run health on PORT_HEALTH
        # We run it in a thread via run_process_with_logs
        log("9hits", f"Using start.sh supervisor (Docker mode) — NH_DIR={NH_DIR}")
        run_process_with_logs(["/bin/bash", start_sh], env, "9hits", restart_delay=int(env.get("SUPERVISOR_DELAY", "10")))
    else:
        # HF direct mode: run nhviewer ourselves (simpler than full start.sh)
        # Need to ensure Xvfb
        if not shutil.which("Xvfb"):
            log("9hits", "Xvfb not found — cannot run 9Hits headless (install xvfb)")
            while True:
                time.sleep(60)
        # Ensure viewer exists
        while not os.path.exists(NH_BIN):
            log("9hits", f"Waiting for nhviewer at {NH_BIN} — retry download in 60s")
            time.sleep(60)
            ensure_9hits()
        # Build args like start.sh
        # We emulate start.sh NH_ARGS
        display = env.get("NH_DISPLAY", ":99")
        # Start Xvfb thread
        def xvfb_loop():
            while True:
                try:
                    dnum = display.lstrip(":").split(".")[0]
                    # clean lock
                    for p in [f"/tmp/.X{dnum}-lock", f"/tmp/.X11-unix/X{dnum}"]:
                        try:
                            os.remove(p)
                        except: pass
                    resolution = env.get("NH_RESOLUTION", "1920x1080x24")
                    log("9hits", f"Starting Xvfb {display} {resolution}")
                    proc = subprocess.Popen(["Xvfb", display, "-screen", "0", resolution, "-nolisten", "tcp"])
                    # write pid
                    pathlib.Path("/tmp/xvfb.pid").write_text(str(proc.pid))
                    proc.wait()
                    log("9hits", f"Xvfb exited code {proc.returncode} — restart in 3s")
                except Exception as e:
                    log("9hits", f"Xvfb error: {e}")
                time.sleep(3)
        threading.Thread(target=xvfb_loop, daemon=True).start()
        # wait for display
        for _ in range(30):
            if os.path.exists(f"/tmp/.X11-unix/X{display.lstrip(':').split('.')[0]}"):
                break
            time.sleep(1)
        # Build nhviewer args
        while True:
            args = []
            # access-key etc
            ak = env.get("ACCESS_KEY", "")
            if ak:
                args.append(f"--access-key={ak}")
            else:
                log("9hits", "WARNING: ACCESS_KEY not set — viewer will show 'User not found!'")
            # system session
            if env.get("SYSTEM_SESSION", "yes").lower() in ("1","yes","true","on"):
                args.append("--system-session")
            if env.get("CLEAR_ALL_SESSIONS", "yes").lower() in ("1","yes","true","on"):
                args.append("--clear-all-sessions")
            if env.get("SESSION_NOTE"):
                args.append(f"--session-note={env['SESSION_NOTE']}")
            if env.get("NOTE"):
                args.append(f"--note={env['NOTE']}")
            if env.get("HIDE_BROWSER"):
                args.append(f"--hide-browser={env['HIDE_BROWSER']}")
            # cache limit
            cl = env.get("CACHE_LIMIT", "0")
            # 9hits expects bytes, 0 means ?
            try:
                args.append(f"--cache-limit={int(cl) if cl else 0}")
            except:
                args.append("--cache-limit=0")
            # reset interval
            if env.get("RESET_INTERVAL"):
                args.append(f"--reset-interval={env['RESET_INTERVAL']}")

            # Add LOW_MEMORY flags if needed (like start.sh)
            # Check mem limit: if <1024 then add
            mem_flags = []
            if env.get("LOW_MEMORY", "auto") != "off":
                low_on = False
                if env.get("LOW_MEMORY") in ("balanced","extreme"):
                    low_on = True
                elif env.get("LOW_MEMORY") == "auto" and MEM_LIMIT_MB and MEM_LIMIT_MB < 1024:
                    low_on = True
                if low_on:
                    mem_flags = [
                        "--disable-gpu","--disable-dev-shm-usage","--disable-extensions",
                        "--disable-background-networking","--disable-sync",
                        "--disable-component-extensions-with-background-pages",
                        "--renderer-process-limit=1","--enable-low-end-device-mode","--memory-model=low",
                        "--js-flags=--max-old-space-size=64",
                        "--disk-cache-size=1048576","--media-cache-size=1048576",
                        "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints",
                    ]
                    log("9hits", f"LOW_MEMORY={env.get('LOW_MEMORY')} → applying {len(mem_flags)} Chromium flags")
            # EXTRA_ARGS
            if env.get("EXTRA_ARGS"):
                args.extend(env["EXTRA_ARGS"].split())
            # Init pass first. Redact the built-in key from logs; the viewer
            # still receives the real argument below.
            display_env = env.copy()
            display_env["DISPLAY"] = display
            safe_args = [
                "--access-key=****" if item.startswith("--access-key=") else item
                for item in args[:3]
            ]
            # init pass
            try:
                log("9hits", f"Init pass: nhviewer {' '.join(safe_args)}... --exit-on-init")
                init_cmd = [NH_BIN] + args + ["--exit-on-init"]
                # bounded by INIT_TIMEOUT
                timeout = int(env.get("INIT_TIMEOUT", "300"))
                proc = subprocess.run(init_cmd, env=display_env, cwd=NH_DIR, timeout=timeout+15, capture_output=True, text=True)
                if proc.stdout:
                    for line in proc.stdout.splitlines():
                        log("9hits", line)
                if proc.stderr:
                    for line in proc.stderr.splitlines():
                        log("9hits", line)
                log("9hits", f"Init pass exit code {proc.returncode}")
            except subprocess.TimeoutExpired:
                log("9hits", "Init pass timed out — continuing to run pass")
            except Exception as e:
                log("9hits", f"Init pass error: {e}")

            # Run pass
            run_args = ["--auto-start", "--in-loop", "--render-to-terminal"]
            if env.get("RESET_INTERVAL"):
                run_args.append(f"--reset-interval={env['RESET_INTERVAL']}")
            run_args.extend(mem_flags)
            if env.get("NH_RUN_EXTRA_ARGS"):
                run_args.extend(env["NH_RUN_EXTRA_ARGS"].split())
            # Use run_pty.py if present for TTY + watchdog
            run_pty = os.path.join(os.path.dirname(__file__), "run_pty.py")
            if not os.path.exists(run_pty):
                run_pty = "/run_pty.py"
            cmd = None
            if os.path.exists(run_pty) and os.path.exists(NH_BIN):
                # use run_pty wrapper
                cmd = ["python3", run_pty, "--heartbeat-file", "/tmp/viewer.lastoutput", "--watchdog-stuck", env.get("NH_WATCHDOG_STUCK","600"), "--", NH_BIN] + run_args
            else:
                cmd = [NH_BIN] + run_args
            log("9hits", f"Run pass: {' '.join(cmd[:8])}... (config hidden)")
            # set state
            pathlib.Path("/tmp/viewer.state").write_text("run")
            try:
                proc = subprocess.Popen(cmd, env=display_env, cwd=NH_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                pathlib.Path("/tmp/viewer.pid").write_text(str(proc.pid))
                # stream
                for line in iter(proc.stdout.readline, ""):
                    if line:
                        log("9hits", line.rstrip())
                        try:
                            pathlib.Path("/tmp/viewer.lastoutput").touch()
                        except: pass
                proc.wait()
                code = proc.returncode
            except Exception as e:
                log("9hits", f"Run pass error: {e}")
                code = 1
            try:
                if os.path.exists("/tmp/viewer.pid"):
                    os.remove("/tmp/viewer.pid")
            except: pass
            pathlib.Path("/tmp/viewer.state").write_text("down")
            c = _read_int("/tmp/viewer.restarts", 0)
            pathlib.Path("/tmp/viewer.restarts").write_text(str(c+1))
            log("9hits", f"Viewer exited code {code} — restarting in {env.get('SUPERVISOR_DELAY','10')}s")
            time.sleep(int(env.get("SUPERVISOR_DELAY", "10")))

def launch_feelingsurf_instance(idx):
    """Launch one FeelingSurf viewer (idx 1..N) with its own DISPLAY and PORT."""
    disp = f":{97+idx}"  # :98, :99? but 9Hits uses :99, so start at :98 downward? Use :98, :97, :96
    # To avoid clash with 9Hits :99, we use :98, :97, :96
    disp = f":{99-idx}"  # idx1 -> :98, idx2 -> :97, idx3 -> :96
    port = 3000 + (idx-1)
    log_key = f"feelingsurf-{idx}"
    env = os.environ.copy()
    env["FEELINGSURF_DISPLAY"] = disp
    env["FEELINGSURF_PORT"] = str(port)
    env["DISPLAY"] = disp  # for direct launch
    env["healthcheck_port"] = str(port)
    env["access_token"] = env.get("access_token", env.get("ACCESS_TOKEN", ""))
    env["FS_RESOLUTION"] = env.get("FS_RESOLUTION", "1920x1080x24")
    env["FS_SHARE_DISPLAY"] = "no"
    # Ensure FS binary
    # Wait for binary
    tries = 0
    while not RESOLVED_FS_BIN or not os.path.exists(RESOLVED_FS_BIN):
        if tries % 10 == 0:
            log(log_key, f"Waiting for FeelingSurf binary (attempt {tries}) — token {'set' if env['access_token'] else 'MISSING'}")
            ensure_feelingsurf()
        time.sleep(6)
        tries += 1
        if tries > 30 and not env["access_token"]:
            log(log_key, "ACCESS_TOKEN not set — cannot authenticate, but will keep trying")
    if not env["access_token"]:
        log(log_key, "WARNING: ACCESS_TOKEN/access_token not set — viewer will fail auth")
    # Use feelingsurf-run.sh if available (handles Xvfb + flags), else direct
    run_sh = os.path.join(os.path.dirname(__file__), "feelingsurf-run.sh")
    if not os.path.exists(run_sh):
        run_sh = "/feelingsurf-run.sh"
    # If run_sh exists, use it (it handles Xvfb etc) but we need to pass FS_BIN path?
    # Our extracted binary path may not be /usr/bin/FeelingSurfViewer, so we need to
    # make symlink or set PATH override. Simplest: ensure symlink at /usr/bin if we have perms else patch run_sh
    fs_bin = RESOLVED_FS_BIN
    # Try to create symlink at /usr/bin if not exists and we are root
    try:
        if fs_bin != "/usr/bin/FeelingSurfViewer" and not os.path.exists("/usr/bin/FeelingSurfViewer"):
            if os.getuid() == 0:
                os.symlink(fs_bin, "/usr/bin/FeelingSurfViewer")
                log(log_key, f"Symlinked {fs_bin} → /usr/bin/FeelingSurfViewer")
            else:
                # Patch run_sh invocation to use fs_bin via env override? run_sh hardcodes /usr/bin/FeelingSurfViewer
                # So we will launch directly instead of via run_sh when not root
                run_sh = None
    except Exception as e:
        log(log_key, f"Symlink failed: {e}")
        if fs_bin != "/usr/bin/FeelingSurfViewer":
            run_sh = None

    if run_sh and os.path.exists(run_sh):
        # Use run_sh but set env so it uses our DISPLAY/PORT
        # run_sh internally will start Xvfb on disp and then FeelingSurfViewer
        # We need to ensure FS_MEM_FLAGS etc are passed
        # Mimic start.sh's FS_MEM_FLAGS handling
        # Native HF mode leaves the viewer flags untouched.
        # We'll just run via supervisor loop
        cmd = ["/bin/bash", run_sh]
        log(log_key, f"Launching via {run_sh} on {disp} port {port} (bin={fs_bin})")
        run_process_with_logs(cmd, env, log_key, restart_delay=int(env.get("SUPERVISOR_DELAY","10")))
    else:
        # Direct launch: handle Xvfb ourselves + launch binary
        # Start Xvfb
        def xvfb_loop_fs():
            while True:
                try:
                    dnum = disp.lstrip(":")
                    for p in [f"/tmp/.X{dnum}-lock", f"/tmp/.X11-unix/X{dnum}"]:
                        try: os.remove(p)
                        except: pass
                    log(log_key, f"Starting Xvfb {disp} {env['FS_RESOLUTION']}")
                    proc = subprocess.Popen(["Xvfb", disp, "-screen", "0", env["FS_RESOLUTION"], "-nolisten", "unix"])
                    proc.wait()
                    log(log_key, f"Xvfb exited {proc.returncode} — restart 3s")
                except Exception as e:
                    log(log_key, f"Xvfb error: {e}")
                time.sleep(3)
        threading.Thread(target=xvfb_loop_fs, daemon=True).start()
        time.sleep(2)
        # Determine flags like feelingsurf-run.sh
        while True:
            fs_flags = []
            # LOW_MEMORY flags
            low = env.get("LOW_MEMORY", "auto")
            low_on = False
            if low in ("balanced","extreme"):
                low_on = True
            elif low == "auto" and MEM_LIMIT_MB and MEM_LIMIT_MB < 1024:
                low_on = True
            if low_on:
                fs_flags = [
                    "--disable-extensions","--disable-background-networking","--disable-sync",
                    "--disable-component-extensions-with-background-pages",
                    "--renderer-process-limit=1","--enable-low-end-device-mode","--memory-model=low",
                    "--js-flags=--max-old-space-size=64",
                    "--disk-cache-size=1048576","--media-cache-size=1048576",
                    "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints",
                ]
            sp_args = []
            if env.get("LOW_MEMORY") == "extreme" and env.get("FS_SP","yes").lower() in ("1","yes","true","on"):
                sp_args = ["--single-process","--in-process-gpu"]
            gl_args = []
            if env.get("FS_GL_MODE","swiftshader") == "disable-gpu":
                gl_args = ["--disable-gpu"]
            else:
                gl_args = ["--use-gl=angle","--use-angle=swiftshader"]
            extra = env.get("FS_EXTRA_FLAGS","").split() if env.get("FS_EXTRA_FLAGS") else []
            cmd = [fs_bin, "--disable-dev-shm-usage","--no-sandbox"] + gl_args + fs_flags + sp_args + extra
            log(log_key, f"Launching FeelingSurfViewer on {disp} :{port} — flags {len(cmd)}")
            env2 = env.copy()
            env2["DISPLAY"] = disp
            try:
                proc = subprocess.Popen(cmd, env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                pathlib.Path(f"/tmp/feelingsurf-{idx}.pid").write_text(str(proc.pid))
                if idx == 1:
                    pathlib.Path("/tmp/feelingsurf.pid").write_text(str(proc.pid))
                for line in iter(proc.stdout.readline, ""):
                    if line:
                        log(log_key, line.rstrip())
                proc.wait()
                code = proc.returncode
            except Exception as e:
                log(log_key, f"Launch error: {e}")
                code = 1
            try:
                pathlib.Path(f"/tmp/feelingsurf-{idx}.pid").unlink(missing_ok=True)
                if idx == 1:
                    pathlib.Path("/tmp/feelingsurf.pid").unlink(missing_ok=True)
            except: pass
            c = _read_int(f"/tmp/feelingsurf-{idx}.restarts", 0)
            pathlib.Path(f"/tmp/feelingsurf-{idx}.restarts").write_text(str(c+1))
            if idx == 1:
                cc = _read_int("/tmp/feelingsurf.restarts", 0)
                pathlib.Path("/tmp/feelingsurf.restarts").write_text(str(cc+1))
            log(log_key, f"Exited {code} — restart in 10s")
            time.sleep(10)

# --------------------------------------------------------------------------- #
# Health / status helpers for Gradio
# --------------------------------------------------------------------------- #
def get_9hits_status():
    pid = _read_int("/tmp/viewer.pid")
    alive = _pid_alive(pid)
    state = "unknown"
    try:
        state = pathlib.Path("/tmp/viewer.state").read_text().strip() or "unknown"
    except OSError:
        pass
    restarts = _read_int("/tmp/viewer.restarts", 0)
    silent = None
    try:
        silent = int(time.time() - pathlib.Path("/tmp/viewer.lastoutput").stat().st_mtime)
    except OSError:
        pass
    return {
        "alive": alive,
        "pid": pid if alive else 0,
        "phase": state,
        "restarts": restarts,
        "silent": silent,
        "rss": _safe_process_rss(pid),
    }


def get_feelingsurf_status(idx):
    pid = _read_int(f"/tmp/feelingsurf-{idx}.pid")
    if pid == 0:
        pid = _read_int("/tmp/feelingsurf.pid") if idx == 1 else 0
    alive = _pid_alive(pid)
    # Check HTTP health for instance 1; the other instances are represented by
    # their managed process because they use separate ports/displays.
    http_ok = None
    if alive and idx == 1:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 3000 + (idx - 1), timeout=1)
            conn.request("HEAD", "/")
            response = conn.getresponse()
            http_ok = response.status < 500
            conn.close()
        except (OSError, http.client.HTTPException):
            http_ok = False
    restarts = _read_int(f"/tmp/feelingsurf-{idx}.restarts", 0)
    if idx == 1 and restarts == 0:
        restarts = _read_int("/tmp/feelingsurf.restarts", 0)
    return {
        "alive": alive,
        "pid": pid if alive else 0,
        "http": http_ok,
        "restarts": restarts,
        "rss": _safe_process_rss(pid),
    }


def get_mem_status():
    """Return read-only RSS telemetry for the native HF runtime."""
    global MEMORY_PEAK_MB
    used = _process_tree_rss_mb(os.getpid()) or 0.0
    MEMORY_PEAK_MB = max(MEMORY_PEAK_MB, used)
    ninehits_pid = _read_int("/tmp/viewer.pid")
    feelingsurf_rss = 0.0
    for idx in range(1, FEELINGSURF_INSTANCES + 1):
        pid = _read_int(f"/tmp/feelingsurf-{idx}.pid")
        if pid == 0 and idx == 1:
            pid = _read_int("/tmp/feelingsurf.pid")
        value = _safe_process_rss(pid)
        if value is not None:
            feelingsurf_rss += value
    return {
        "memory_used_mb": round(used, 1),
        "memory_limit_mb": MEM_LIMIT_MB or None,
        "memory_peak_mb": round(MEMORY_PEAK_MB, 1),
        "hard_threshold_mb": None,
        "ninehits_rss_mb": _safe_process_rss(ninehits_pid),
        "feelingsurf_rss_mb": round(feelingsurf_rss, 1) if feelingsurf_rss else None,
        "configured_mode": "off",
        "effective_mode": "off",
        "active_viewer": "both",
        "time_slice_seconds": None,
        "next_flip_in_seconds": None,
        "interventions": 0,
        "last_target": None,
    }


def get_combined_status():
    s9 = get_9hits_status()
    fss = [get_feelingsurf_status(i + 1) for i in range(FEELINGSURF_INSTANCES)]
    mem = get_mem_status()
    any_fs = any(f["alive"] for f in fss)
    all_ok = s9["alive"] and any_fs
    status = "ok" if all_ok else ("starting" if (s9["alive"] or any_fs) else "down")
    return {
        "9hits": s9,
        "feelingsurf": fss,
        "mem": mem,
        "status": status,
    }

# --------------------------------------------------------------------------- #
# Gradio UI
# --------------------------------------------------------------------------- #
def build_ui():
    if not gr:
        return None
    with gr.Blocks(title="hits4me FREE 1+3 — HF Gradio", theme=gr.themes.Soft(), css="""
        .hf-header {background: linear-gradient(90deg,#1f6feb22,#58a6ff22); border:1px solid #30363d; border-radius:12px; padding:16px}
        .badge {display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; font-weight:700; margin-right:6px}
        .badge.ok {background:#23863633; color:#3fb950; border:1px solid #23863655}
        .badge.warn {background:#9e6a0311; color:#d29922; border:1px solid #9e6a0344}
        .badge.err {background:#f8514911; color:#f85149; border:1px solid #f8514944}
        .badge.neutral {background:#21262d; color:#8b949e; border:1px solid #30363d}
        .log-box {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px}
    """) as demo:
        gr.Markdown(f"""
        <div class="hf-header">
        <h1 style="margin:0">🌐 hits4me — HF Gradio <span style="font-size:14px; color:#8b949e">native 16 GB runtime</span></h1>
        <div style="margin-top:6px; color:#8b949e; font-size:13px">
        <span class="badge ok">9Hits ×1 system session</span>
        <span class="badge ok">FeelingSurf ×3 parallel</span>
        <span class="badge neutral">16 GB RAM · 2 vCPU · native concurrent</span>
        <span class="badge neutral">{'✅ spaces' if SPACES_AVAILABLE else '⚠️ spaces missing'}</span>
        <span class="badge neutral">CLEAN CLOUD IP (no proxy pool needed)</span>
        </div>
        <div style="margin-top:8px; font-size:12px; color:#8b949e">
        💡 Viewers run as ordinary CPU processes with native flags. The built-in <code>ACCESS_KEY</code> and <code>ACCESS_TOKEN</code> are used automatically; optional Space variables can override them. ZeroGPU support is optional and only applies to the manual ping below.
        </div>
        </div>
        """)

        with gr.Row():
            status_html = gr.HTML(value="<div style='padding:12px; color:#8b949e'>Loading status…</div>", label="Live Status")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📊 Memory (read-only process RSS)")
                mem_html = gr.HTML(value="<div style='color:#8b949e'>waiting for process telemetry…</div>")
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ Config — FREE PLAN")
                gr.Markdown(f"""
                | Key | Value (env) | Note |
                |-----|-------------|------|
                | **Hardware** | **HF Gradio** | CPU/native viewer processes; ZeroGPU is optional |
                | **9Hits sessions** | **1** system | `SYSTEM_SESSION=yes`, `EX_PROXY_SESSIONS=0` |
                | **FeelingSurf instances** | **{FEELINGSURF_INSTANCES}** | `FEELINGSURF_INSTANCES={FEELINGSURF_INSTANCES}` (same token) |
                | **DUAL_VIEWER_MODE** | `off` | native concurrent processes; no scheduler |
                | **LOW_MEMORY** | `off` | normal upstream viewer flags |
                | **RESET_INTERVAL** | `2h` | viewer self-restart |
                | **NH_DISPLAY** | `:99` | 9Hits Xvfb |
                | **FS displays** | `:98,:97,:96` | 1 per FeelingSurf |
                | **GRADIO PORT** | `{PORT_GRADIO}` | externally exposed |
                | **HEALTH PORT** | `{PORT_HEALTH}` | `/health` (internal) |
                | **NH_DIR** | `{NH_DIR}` | auto `/tmp/9hits` on HF |
                | **`spaces` GPU** | `{'✅' if SPACES_AVAILABLE else '❌ pip install spaces'}` | dummy `@spaces.GPU` keeps ZeroGPU alive (viewers use CPU) |
                """)

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### ⚡ Optional ZeroGPU check")
                gr.Markdown("If this Space exposes **ZeroGPU**, this optional ping checks it. Viewers remain ordinary CPU processes and do not depend on the GPU quota.")
                with gr.Row():
                    zerogpu_input = gr.Textbox(label="Ping message", value="hello", scale=3, placeholder="hello")
                    zerogpu_btn = gr.Button("⚡ Ping ZeroGPU (10s)", variant="secondary", scale=1)
                zerogpu_out = gr.Textbox(label="ZeroGPU result (GPU quota 10s)", interactive=False, lines=2, placeholder="click Ping to test ZeroGPU…")
                zerogpu_btn.click(fn=zerogpu_ping_wrapper, inputs=zerogpu_input, outputs=zerogpu_out)
                gr.Markdown(f"<span style='font-size:11px; color:#8b949e'>{'✅ spaces installed — dummy @spaces.GPU keeps ZeroGPU alive' if SPACES_AVAILABLE else '❌ spaces not installed — add `spaces` to requirements.txt'} · quota 3.5 min/day free (Pro 8×) · viewers stay CPU → quota not used</span>")
            with gr.Column(scale=1):
                gr.Markdown("### 🔑 Credentials")
                gr.Markdown(f"""
                - `ACCESS_KEY` **{'✅ built-in' if os.environ.get('ACCESS_KEY') else '❌ missing'}** · `ACCESS_TOKEN` **{'✅ built-in' if os.environ.get('ACCESS_TOKEN') or os.environ.get('access_token') else '❌ missing'}**
                - Environment variables remain optional overrides.
                - Python 3.10/3.12 recommended; ZeroGPU is optional.
                """)

        with gr.Tabs():
            with gr.TabItem("📜 Combined Logs", elem_id="tab-combined"):
                log_combined = gr.Textbox(label="Combined (9Hits + 3×FeelingSurf + setup)", value=lambda: get_logs("combined", 120), lines=18, max_lines=24, every=3, interactive=False, elem_classes=["log-box"])
            with gr.TabItem("🎯 9Hits (1 session)", elem_id="tab-9hits"):
                log_9hits = gr.Textbox(label="9Hits Viewer — 1 system session", value=lambda: get_logs("9hits", 100), lines=18, every=3, interactive=False, elem_classes=["log-box"])
                gr.Markdown("""
                **Free plan tip:** Keep only the system session. The 9Hits public pool is closed and shared proxies trigger `Duplicate USER on IP`. One clean cloud IP = reliable.
                """)
            with gr.TabItem("🌊 FeelingSurf #1", elem_id="tab-fs1"):
                log_fs1 = gr.Textbox(label="FeelingSurf #1 — port 3000 / :98", value=lambda: get_logs("feelingsurf-1", 80), lines=14, every=3, interactive=False, elem_classes=["log-box"])
            with gr.TabItem("🌊 FeelingSurf #2", elem_id="tab-fs2"):
                log_fs2 = gr.Textbox(label="FeelingSurf #2 — port 3001 / :97", value=lambda: get_logs("feelingsurf-2", 80), lines=14, every=3, interactive=False, elem_classes=["log-box"])
            with gr.TabItem("🌊 FeelingSurf #3", elem_id="tab-fs3"):
                log_fs3 = gr.Textbox(label="FeelingSurf #3 — port 3002 / :96", value=lambda: get_logs("feelingsurf-3", 80), lines=14, every=3, interactive=False, elem_classes=["log-box"])
            with gr.TabItem("⚙️ Setup & Health", elem_id="tab-setup"):
                log_setup = gr.Textbox(label="Setup / Downloads / Health", value=lambda: get_logs("setup", 80) + "\n\n--- health ---\n" + get_logs("health", 30), lines=16, every=3, interactive=False, elem_classes=["log-box"])
                gr.Markdown(f"""
                **Health endpoints:**
                - Internal: `GET http://localhost:{PORT_HEALTH}/health` (combined JSON, from `health_server.py`)
                - Gradio: `GET http://localhost:{PORT_GRADIO}/` (this UI) — HF Spaces health check uses this.
                For uptime bots, monitor this Gradio URL or the internal `/health` via a forwarded check.

                **Secrets checklist:**
                - `ACCESS_KEY` available? **{ '✅ built-in/override' if os.environ.get('ACCESS_KEY') else '❌ missing — Space will show User not found!' }**
                - `ACCESS_TOKEN` / `access_token` available? **{ '✅ built-in/override' if os.environ.get('ACCESS_TOKEN') or os.environ.get('access_token') else '❌ missing — FeelingSurf will fail auth' }**
                """)

        gr.Markdown("""
        <div style="text-align:center; color:#484f58; font-size:11px; margin-top:12px">
        HF NATIVE: 1×9Hits system session + 3×FeelingSurf (same token) · native concurrent processes on 16GB · <a href="/health" target="_blank" style="color:#58a6ff">/health</a> (port 10000) · logs update every 3s · build: app_hf.py
        </div>
        """)

        # ------------------------------------------------------------------- #
        # JS-free polling via gr.HTML every — we render status HTML every 2s
        # ------------------------------------------------------------------- #
        def render_status():
            st = get_combined_status()
            s9 = st["9hits"]
            fss = st["feelingsurf"]
            mem = st["mem"]
            # badges
            overall = st["status"]
            overall_badge = "ok" if overall == "ok" else ("warn" if overall == "starting" else "err")
            # 9Hits line
            nh_icon = "🟢" if s9["alive"] else "🔴"
            nh_state = s9["phase"]
            nh_pid = s9["pid"]
            nh_rest = s9["restarts"]
            nh_silent = s9["silent"]
            nh_rss = f'{s9["rss"]} MB' if s9["rss"] is not None else "—"
            # FS lines
            fs_lines = ""
            for i, fs in enumerate(fss, start=1):
                icon = "🟢" if fs["alive"] else "🔴"
                http_s = ""
                if fs["http"] is True:
                    http_s = " · http ✅"
                elif fs["http"] is False:
                    http_s = " · http ❌"
                fs_lines += f"<div style='padding:4px 0; border-bottom:1px dashed #21262d; display:flex; justify-content:space-between'><span>{icon} <b>FeelingSurf #{i}</b> <span style='color:#8b949e; font-size:11px'>:{99-i} · port {3000+i-1} · pid {fs['pid']} · restarts {fs['restarts']}{http_s}</span></span><span style='font-weight:600'>{fs['rss'] or '—'} </span></div>"
            # mem bar
            used = mem.get("memory_used_mb")
            limit = mem.get("memory_limit_mb") or MEM_LIMIT_MB
            peak = mem.get("memory_peak_mb")
            hard = mem.get("hard_threshold_mb")
            # Memory is read-only telemetry; no process is restarted based on it.
            mem_line = "<div style='color:#8b949e; font-size:12px'>normal process RSS telemetry — no memory guardian or low-memory tuning</div>"
            if used is not None and limit:
                pct = min(100, (used/limit*100) if limit else 0)
                threshold_marker = ""
                if hard and limit:
                    th_pct = min(100, hard / limit * 100)
                    threshold_marker = f"<div style=\"position:absolute; top:0; bottom:0; left:{th_pct}%; width:2px; background:#f85149\"></div>"
                color = "#3fb950" if pct < 75 else ("#d29922" if pct < 90 else "#f85149")
                mem_line = f"""
                <div style="margin:6px 0 2px; background:#21262d; border-radius:7px; height:14px; position:relative; overflow:hidden">
                  <div style="width:{pct}%; background:{color}; height:100%; transition:width .5s"></div>
                  {threshold_marker}
                </div>
                <div style="display:flex; justify-content:space-between; font-size:11px; color:#8b949e"><span>0</span><span>{pct:.0f}% of {limit} MB (threshold {hard or 'none'} MB)</span><span>{limit} MB</span></div>
                <div style="display:flex; gap:12px; font-size:11px; color:#8b949e; margin-top:6px"><span>used <b style='color:#e6edf3'>{used} MB</b></span><span>peak <b style='color:#e6edf3'>{peak or '—'} MB</b></span><span>limit <b style='color:#e6edf3'>{limit} MB</b></span><span>mode <b style='color:#e6edf3'>{mem.get('effective_mode') or mem.get('configured_mode') or 'off'}</b></span></div>
                """
            # config warnings
            ak_ok = bool(os.environ.get("ACCESS_KEY"))
            at_ok = bool(os.environ.get("ACCESS_TOKEN") or os.environ.get("access_token"))
            warn = ""
            if not ak_ok or not at_ok:
                warn = "<div style='margin-top:8px; padding:8px; background:#f8514911; border:1px solid #f8514944; border-radius:8px; color:#f85149; font-size:12px'>⚠️ Missing secrets: "
                if not ak_ok:
                    warn += " <b>ACCESS_KEY</b> (9Hits) "
                if not at_ok:
                    warn += " <b>ACCESS_TOKEN</b> (FeelingSurf) "
                warn += " — set in Space Settings → Variables and secrets, then restart Space.</div>"

            html = f"""
            <div style="background:#161b22; border:1px solid #30363d; border-radius:10px; padding:14px 16px">
              <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px">
                <span class="badge {overall_badge}">{overall.upper()}</span>
                <span style="font-weight:700">Status — FREE 1+3</span>
                <span style="color:#8b949e; font-size:12px">9Hits { '✅' if s9['alive'] else '❌' } · FeelingSurf {sum(1 for f in fss if f['alive'])}/{len(fss)} alive · uptime {int(time.time()-STARTED_AT)//60}m</span>
              </div>
              <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px">
                <div style="background:#0d1117; border:1px solid #21262d; border-radius:8px; padding:10px">
                  <div style="font-size:11px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px">9Hits Viewer v6 — 1 system session</div>
                  <div style="display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed #21262d"><span style="color:#8b949e">running</span><span style="font-weight:600">{s9['alive']} · {nh_icon} {nh_state}</span></div>
                  <div style="display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed #21262d"><span style="color:#8b949e">pid / restarts</span><span style="font-weight:600">{nh_pid or '—'} / {nh_rest}</span></div>
                  <div style="display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed #21262d"><span style="color:#8b949e">silent (s)</span><span style="font-weight:600">{nh_silent if nh_silent is not None else '—'}</span></div>
                  <div style="display:flex; justify-content:space-between; padding:3px 0"><span style="color:#8b949e">RSS</span><span style="font-weight:600">{nh_rss}</span></div>
                  <div style="margin-top:8px; font-size:11px; color:#8b949e">SYSTEM_SESSION=yes · CLEAR_ALL_SESSIONS=yes · no proxy (free IP)</div>
                </div>
                <div style="background:#0d1117; border:1px solid #21262d; border-radius:8px; padding:10px">
                  <div style="font-size:11px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px">FeelingSurf — 3× parallel (same token)</div>
                  {fs_lines}
                  <div style="margin-top:8px; font-size:11px; color:#8b949e">Same <code>access_token</code> ×3 — official <code>docker-compose.yml</code> multi-container pattern. 16GB: concurrent.</div>
                </div>
              </div>
              <div style="margin-top:12px; background:#0d1117; border:1px solid #21262d; border-radius:8px; padding:10px">
                <div style="font-size:11px; color:#8b949e; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px">Container memory (ordinary process RSS)</div>
                {mem_line}
              </div>
              {warn}
            </div>
            """
            return html

        def render_mem():
            mem = get_mem_status()
            if not mem:
                return "<div style='color:#8b949e; font-size:12px; padding:10px; background:#0d1117; border:1px solid #21262d; border-radius:8px'>process RSS telemetry is not available yet — runtime limit ~%s MB</div>" % MEM_LIMIT_MB
            used = mem.get("memory_used_mb", "—")
            limit = mem.get("memory_limit_mb") or "unlimited"
            peak = mem.get("memory_peak_mb", "—")
            hard = mem.get("hard_threshold_mb") or "none"
            nh = mem.get("ninehits_rss_mb", "—")
            fs = mem.get("feelingsurf_rss_mb", "—")
            mode = mem.get("effective_mode", mem.get("configured_mode", "off"))
            active = mem.get("active_viewer", "both")
            nxt = mem.get("next_flip_in_seconds") or "none"
            interventions = mem.get("interventions", 0)
            return f"""
            <div style="background:#0d1117; border:1px solid #21262d; border-radius:8px; padding:12px; font-size:13px">
              <div style="display:flex; justify-content:space-between"><span style="color:#8b949e">used / limit</span><span style="font-weight:700">{used} / {limit} MB</span></div>
              <div style="display:flex; justify-content:space-between"><span style="color:#8b949e">peak / threshold</span><span>{peak} / {hard} MB</span></div>
              <div style="display:flex; justify-content:space-between"><span style="color:#8b949e">9Hits / FeelingSurf RSS</span><span>{nh} / {fs} MB</span></div>
              <div style="display:flex; justify-content:space-between"><span style="color:#8b949e">mode / active</span><span>{mode} / {active}</span></div>
              <div style="display:flex; justify-content:space-between"><span style="color:#8b949e">next flip / interventions</span><span>{nxt} s / {interventions}</span></div>
              <div style="margin-top:8px; font-size:11px; color:#8b949e">RSS is shown for visibility only. Native HF mode has no memory guardian, low-memory flags, or time-slice scheduler.</div>
            </div>
            """

        # Use gr.HTML with `every` for auto-refresh (Gradio 4.44)
        # status_html will be refreshed every 2s
        # We need to hook an update function: use a dummy textbox with every
        # Alternative: use gr.HTML(value=render_status, every=2) if supported
        # Gradio 4.44 supports `every` on components that have value as callable
        # So we re-define with callable + every
        # We'll create hidden update trick via gr.Textbox every -> but simpler: create new HTML with callable
        # The earlier status_html is placeholder; we now create refreshable ones via .then? Easier: use gr.Timer
        # For compatibility, we use gr.HTML with callable if every param exists
        try:
            # Recreate with callable + every (supported in 4.x)
            status_html = gr.HTML(value=render_status, every=2)
            mem_html = gr.HTML(value=render_mem, every=3)
        except Exception:
            # Fallback: keep manual refresh button
            with gr.Row():
                refresh_btn = gr.Button("🔄 Refresh status", size="sm")
                refresh_btn.click(fn=render_status, outputs=status_html)
                refresh_btn.click(fn=render_mem, outputs=mem_html)

    return demo

STARTED_AT = time.time()

# --------------------------------------------------------------------------- #
# Main — start supervisors + Gradio
# --------------------------------------------------------------------------- #
def main():
    log("setup", f"=== hits4me HF FREE 1+3 — Gradio on :{PORT_GRADIO} — health on :{PORT_HEALTH} ===")
    log("setup", f"Env: NINEHITS_ENABLED={os.environ.get('NINEHITS_ENABLED')} FEELINGSURF_ENABLED={os.environ.get('FEELINGSURF_ENABLED')} FEELINGSURF_INSTANCES={FEELINGSURF_INSTANCES}")
    log("setup", f"ACCESS_KEY={'set' if os.environ.get('ACCESS_KEY') else 'MISSING'} ACCESS_TOKEN={'set' if os.environ.get('ACCESS_TOKEN') or os.environ.get('access_token') else 'MISSING'}")
    log("setup", f"NH_DIR={NH_DIR} NH_BIN={NH_BIN} FS_BIN={RESOLVED_FS_BIN or 'to be resolved'}")
    log("setup", f"MEM LIMIT ~{MEM_LIMIT_MB} MB — DUAL_VIEWER_MODE={os.environ.get('DUAL_VIEWER_MODE')} LOW_MEMORY={os.environ.get('LOW_MEMORY')}")

    # Start health_server early (if available) so /health is up even before viewers
    try:
        health_py = os.path.join(os.path.dirname(__file__), "health_server.py")
        if not os.path.exists(health_py):
            health_py = "/health_server.py"
        if os.path.exists(health_py):
            env_h = os.environ.copy()
            env_h["PORT"] = str(PORT_HEALTH)
            # Let the health process inspect the complete native HF process tree
            # for read-only RSS telemetry.
            env_h["SUPERVISOR_PID"] = str(os.getpid())
            subprocess.Popen(["python3", health_py], env=env_h)
            log("health", f"health_server pre-started on :{PORT_HEALTH}")
        else:
            log("health", "health_server.py not found — /health will not be available")
    except Exception as e:
        log("health", f"health_server start failed: {e}")

    # Start 9Hits supervisor thread
    t9 = threading.Thread(target=launch_9hits, daemon=True, name="9hits-supervisor")
    t9.start()
    log("setup", "9Hits supervisor thread started (1 system session)")

    # Start FeelingSurf instances (3×)
    # Only if token present? Still start but will warn — token may be added later via env
    for i in range(1, FEELINGSURF_INSTANCES+1):
        # stagger starts by 4s to avoid Xvfb port clash
        def _starter(idx=i):
            time.sleep((idx-1)*4)
            launch_feelingsurf_instance(idx)
        th = threading.Thread(target=_starter, daemon=True, name=f"feelingsurf-{i}")
        th.start()
        log("setup", f"FeelingSurf supervisor #{i} thread started (port {3000+i-1} display :{99-i})")

    # Build and launch Gradio
    if gr:
        demo = build_ui()
        if demo:
            # HF Spaces expects to listen on 0.0.0.0:$PORT
            # We also allow health on PORT_HEALTH via separate process, so no conflict
            log("setup", f"Launching Gradio on 0.0.0.0:{PORT_GRADIO}")
            demo.queue().launch(server_name="0.0.0.0", server_port=PORT_GRADIO, show_error=True, share=False)
            return
    # Fallback if gradio missing: just wait forever (viewers still running via threads)
    log("setup", "Gradio not available — waiting forever with viewers running (health on :%s)" % PORT_HEALTH)
    while True:
        time.sleep(60)

if __name__ == "__main__":
    # Handle SIGTERM gracefully
    def _sigterm(signum, frame):
        log("setup", f"Received signal {signum} — exiting")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    main()
