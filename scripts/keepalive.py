"""
keepalive.py
============
Pings the Streamlit application health endpoint (/_stcore/health) to verify
availability and prevent Render free-tier instances from spinning down due to inactivity.

Can be run:
  1. As a Render Cron Job (e.g. every 10 minutes: */10 * * * *)
  2. As a standalone background daemon: python scripts/keepalive.py --daemon --interval 600
  3. One-off health check: python scripts/keepalive.py
"""

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def ping_health(url: str, timeout: int = 15) -> tuple[bool, int, float]:
    """
    Sends an HTTP GET request to the target healthcheck URL.
    Returns (is_success, status_code, latency_ms).
    """
    # Streamlit native healthcheck endpoint
    if not url.endswith("/_stcore/health") and not url.endswith("/"):
        url = url.rstrip("/") + "/_stcore/health"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ETS-Watchtower-KeepAlive/1.0 (Render Health Check)"},
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency = (time.perf_counter() - start) * 1000
            return (resp.status == 200, resp.status, latency)
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - start) * 1000
        return (False, e.code, latency)
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        print(f"[{datetime.now(timezone.utc).isoformat()}] Connection error: {e}", file=sys.stderr)
        return (False, 0, latency)


def main():
    parser = argparse.ArgumentParser(description="Keepalive ping for Render deployments.")
    parser.add_argument(
        "--url",
        default=os.environ.get("APP_URL", "http://localhost:8501"),
        help="Target base URL or health endpoint.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously in background daemon mode.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("KEEPALIVE_INTERVAL_SEC", "600")),
        help="Interval in seconds between pings in daemon mode (default 600s / 10 min).",
    )
    args = parser.parse_args()

    target_url = args.url
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not args.daemon:
        print(f"[{now_str}] Pinging {target_url} ...")
        success, code, latency = ping_health(target_url)
        if success:
            print(f"[{now_str}] SUCCESS: Server healthy (HTTP {code}, {latency:.1f}ms). Keepalive refreshed.")
            sys.exit(0)
        else:
            print(f"[{now_str}] WARNING: Server returned HTTP {code} ({latency:.1f}ms).", file=sys.stderr)
            sys.exit(1)

    print(f"Starting ETS KeepAlive Daemon for {target_url} (interval: {args.interval}s)...")
    while True:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        success, code, latency = ping_health(target_url)
        status_str = "HEALTHY" if success else "UNAVAILABLE"
        print(f"[{ts}] Ping {status_str} (HTTP {code}, {latency:.1f}ms)")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
