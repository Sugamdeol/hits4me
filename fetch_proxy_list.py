#!/usr/bin/env python3
"""
Fetch a proxy list from a URL (e.g. Webshare download link) and output it in
9Hits format: server:port;user;pass|server2:port;user2;pass2

Stdlib only, no external dependencies.
"""

import sys
import urllib.error
import urllib.request


def fetch_and_convert(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "hits4me-fetch/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_body = resp.read()
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        if err_body:
            sys.stderr.write(f"HTTP error {e.code}: {err_body[:300]}\n")
        else:
            sys.stderr.write(f"HTTP error {e.code}: {e.reason}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Failed to fetch proxy list: {e}\n")
        sys.exit(1)

    try:
        body = raw_body.decode("utf-8", errors="replace").strip()
    except Exception as e:
        sys.stderr.write(f"Failed to decode response: {e}\n")
        sys.exit(1)

    if not body:
        sys.stderr.write("Empty response received from proxy URL\n")
        sys.exit(1)

    # Check for JSON error response (e.g. Webshare {"download_token": [...]})
    if body.startswith("{"):
        sys.stderr.write(f"Proxy list returned error response: {body[:300]}\n")
        sys.exit(1)

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
        sys.stderr.write("No valid proxies found in response\n")
        sys.exit(1)

    sys.stdout.write("|".join(proxies) + "\n")
    sys.exit(0)


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        sys.stderr.write("Usage: fetch_proxy_list.py <URL>\n")
        sys.exit(1)
    fetch_and_convert(sys.argv[1].strip())


if __name__ == "__main__":
    main()
