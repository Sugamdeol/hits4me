#!/usr/bin/env python3
"""
Environment -> 9Hits viewer flag mapping.

Single source of truth shared by the Docker entrypoint (start.sh, via /nh.sh)
and the Docker-free native runner (run_native.py). The env var names and the
flags they map to are documented in README.md.

Stdlib only, no external dependencies.
"""

import os
import shlex

# Env var -> `--flag=value`. Order matters only for readable logs.
VALUE_FLAGS = (
    ("ACCESS_KEY", "access-key"),
    ("ALLOW_POPUPS", "allow-popups"),
    ("ALLOW_ADULT", "allow-adult"),
    ("ALLOW_CRYPTO", "allow-crypto"),
    ("HIDE_BROWSER", "hide-browser"),
    ("EX_PROXY_SESSIONS", "ex-proxy-sessions"),
    ("EX_PROXY_URL", "ex-proxy-url"),
    ("BULK_ADD_PROXY_LIST", "bulk-add-proxy-list"),
    ("BULK_ADD_PROXY_TYPE", "bulk-add-proxy-type"),
    ("SESSION_NOTE", "session-note"),
    ("NOTE", "note"),
    ("CACHE_PATH", "cache-path"),
    ("CACHE_LIMIT", "cache-limit"),
    ("HIDE_COLUMNS", "hide-columns"),
)

# Env var -> bare `--flag` when truthy.
BOOL_FLAGS = (
    ("SYSTEM_SESSION", "system-session"),
    ("CLEAR_ALL_SESSIONS", "clear-all-sessions"),
)

# Flags that only apply to the Docker entrypoint (/nh.sh), which drives its own
# install + supervision. The native runner handles these itself instead:
# RESET_INTERVAL becomes a run flag, INSTALL_DIR/DEFAULT_DL are CLI options and
# restarts are managed by ViewerSupervisor.
NH_VALUE_FLAGS = (
    ("INSTALL_DIR", "install-dir"),
    ("DEFAULT_DL", "default-dl"),
    ("RESTART_DELAY", "restart-delay"),
    ("RESET_INTERVAL", "reset-interval"),
    ("VNC_PW", "vnc-pw"),
    ("VNC_PORT", "vnc-port"),
)

NH_BOOL_FLAGS = (
    ("RE_INSTALL", "re-install"),
    ("VNC", "vnc"),
    ("NO_VNC_PW", "no-vnc-pw"),
)

TRUTHY = ("1", "yes", "true", "on")

# Flags whose values are secrets and must never be printed verbatim.
SECRET_FLAGS = ("--access-key", "--bulk-add-proxy-list", "--vnc-pw")


def is_truthy(value):
    return str(value or "").strip().lower() in TRUTHY


def env_str(name, default="", env=None):
    env = os.environ if env is None else env
    value = env.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def build_config_args(env=None, extra=None, include_nh_flags=False):
    """Build the *configuration* flags applied once on the init pass.

    These create/replace sessions and persist into the viewer's own config, so
    they must NOT be re-passed on every relaunch (that would re-create sessions
    or wipe them via --clear-all-sessions).

    ``include_nh_flags`` adds the install/supervision flags that only make
    sense for the Docker entrypoint's /nh.sh (see NH_VALUE_FLAGS).
    """
    env = os.environ if env is None else env
    args = []

    value_flags = VALUE_FLAGS + (NH_VALUE_FLAGS if include_nh_flags else ())
    bool_flags = BOOL_FLAGS + (NH_BOOL_FLAGS if include_nh_flags else ())

    for var, flag in value_flags:
        value = env_str(var, env=env)
        if value:
            args.append("--%s=%s" % (flag, value))

    for var, flag in bool_flags:
        if is_truthy(env.get(var)):
            args.append("--%s" % flag)

    # EXTRA_ARGS: raw space-separated flags appended as-is.
    extra_args = env_str("EXTRA_ARGS", env=env)
    if extra_args:
        args.extend(shlex.split(extra_args))

    if extra:
        args.extend(extra)

    return args


def build_run_flags(env=None):
    """Build the *runtime* flags used for every supervised relaunch."""
    env = os.environ if env is None else env
    flags = ["--auto-start", "--in-loop"]

    reset_interval = env_str("RESET_INTERVAL", env=env)
    if reset_interval:
        flags.append("--reset-interval=%s" % reset_interval)

    return flags


def redact(args):
    """Return ``args`` with secret values masked, safe to log."""
    out = []
    for arg in args:
        masked = arg
        for secret in SECRET_FLAGS:
            if arg.startswith(secret + "="):
                masked = secret + "=****"
                break
        out.append(masked)
    return out


def redacted_line(args):
    return " ".join(shlex.quote(a) for a in redact(args))


def estimate_sessions(env=None):
    """Rough session count, used for the low-RAM warning."""
    env = os.environ if env is None else env
    total = 0

    ex_sessions = env_str("EX_PROXY_SESSIONS", env=env)
    if ex_sessions.isdigit():
        total += int(ex_sessions)

    bulk = env_str("BULK_ADD_PROXY_LIST", env=env)
    if bulk:
        total += len([p for p in bulk.split("|") if p.strip()])

    if is_truthy(env.get("SYSTEM_SESSION")):
        total += 1

    return total
