#!/usr/bin/env python3
"""
Fetch a proxy list from a URL (e.g. Webshare download link) and output it in
9Hits format: server:port;user;pass|server2:port;user2;pass2

Stdlib only, no external dependencies.
"""

import sys
import urllib.error
import urllib.request


class ProxyFetchError(Exception):
    """Raised by fetch() when the list cannot be downloaded or parsed."""


def fetch(url, timeout=60):
    """Download ``url`` and return a 9Hits-formatted proxy list string.

    Importable counterpart of the CLI (used by start.sh / run_native.py).
    Raises ProxyFetchError with a human readable message on failure.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "hits4me-fetch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read()
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        if err_body:
            raise ProxyFetchError(f"HTTP error {e.code}: {err_body[:300]}")
        raise ProxyFetchError(f"HTTP error {e.code}: {e.reason}")
    except Exception as e:
        raise ProxyFetchError(f"Failed to fetch proxy list: {e}")

    try:
        body = raw_body.decode("utf-8", errors="replace").strip()
    except Exception as e:
        raise ProxyFetchError(f"Failed to decode response: {e}")

    if not body:
        raise ProxyFetchError("Empty response received from proxy URL")

    # Check for JSON error response (e.g. Webshare {"download_token": [...]})
    if body.startswith("{"):
        raise ProxyFetchError(f"Proxy list returned error response: {body[:300]}")

    proxies = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 3)
        if len(parts) == 4:
            ip, port, user, password = parts
            proxies.append(f"{ip}:{port};{user};{password}")
        elif len(parts) == 2:
            ip, port = parts
            proxies.append(f"{ip}:{port}")
        else:
            sys.stderr.write(f"Skipping malformed line: {line[:50]}\n")

    if not proxies:
        raise ProxyFetchError("No valid proxies found in response")

    return "|".join(proxies)


def fetch_and_convert(url):
    try:
        result = fetch(url)
    except ProxyFetchError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)

    sys.stdout.write(result + "\n")
    sys.exit(0)


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        sys.stderr.write("Usage: fetch_proxy_list.py <URL>\n")
        sys.exit(1)
    fetch_and_convert(sys.argv[1].strip())


if __name__ == "__main__":
    main()
