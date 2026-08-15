#!/usr/bin/env python3
"""
Convert a Webshare proxy list (ip:port:user:pass) to 9Hits environment variables
(server:port;user;pass|server2:port;user2;pass2).

Stdlib only, no external dependencies.
"""

import argparse
import sys


def parse_and_convert(lines):
    proxies = []
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
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
            sys.stderr.write(f"Warning: skipping malformed line {line_no}: {line}\n")
    return proxies


def main():
    parser = argparse.ArgumentParser(
        description="Convert Webshare proxy list to 9Hits format (BULK_ADD_PROXY_LIST)."
    )
    parser.add_argument(
        "file",
        nargs="?",
        default="-",
        help="Input proxy list file (default: stdin)",
    )
    parser.add_argument(
        "--type",
        dest="proxy_type",
        default="socks5",
        choices=["http", "socks4", "socks5", "ssh"],
        help="Proxy type (default: socks5)",
    )
    args = parser.parse_args()

    if args.file == "-":
        lines = sys.stdin.readlines()
    else:
        try:
            with open(args.file, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as e:
            sys.stderr.write(f"Error reading file '{args.file}': {e}\n")
            sys.exit(1)

    proxies = parse_and_convert(lines)
    if not proxies:
        sys.stderr.write("Error: no valid proxies found\n")
        sys.exit(1)

    print(f"# {len(proxies)} proxies converted")
    print(f"BULK_ADD_PROXY_TYPE={args.proxy_type}")
    print(f"BULK_ADD_PROXY_LIST={'|'.join(proxies)}")


if __name__ == "__main__":
    main()
