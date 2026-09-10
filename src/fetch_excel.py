"""
fetch_excel.py
==============
Downloads the source Excel file from an external URL before ingestion,
so the raw file never has to live inside the git repository.

Set the EXCEL_URL environment variable to a direct-download link:
  - OneDrive/SharePoint: convert your share link to a direct-download
    link (see README for the exact steps).
  - S3: generate a presigned URL, e.g.
      aws s3 presign s3://your-bucket/expiry.xlsx --expires-in 86400
    A presigned URL works directly with this script, no extra headers needed.
  - Any other HTTPS host serving the raw .xlsx bytes also works as-is.

If EXCEL_URL isn't set, this script does nothing - useful for local
development where you're pointing straight at a file already on disk.
"""

import ipaddress
import os
import socket
import sys
import urllib.parse
import urllib.request

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE_BYTES = 64 * 1024           # 64 KB
DEFAULT_TIMEOUT_SECONDS = 30


def validate_url(url: str) -> str:
    """
    Validates URL scheme and guards against SSRF by checking resolved IPs.
    Enforces HTTPS and rejects loopback, private, and cloud metadata addresses.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"Insecure scheme '{parsed.scheme}'. Only HTTPS is permitted.")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: missing hostname.")

    try:
        addr_info = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve hostname '{hostname}': {e}") from e

    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)

        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or str(ip) == "169.254.169.254"
        ):
            raise ValueError(
                f"Access to private, loopback, or metadata address '{ip_str}' is prohibited (SSRF guard)."
            )

    return url


def download(url: str, dest_path: str):
    """
    Downloads an Excel file securely from an HTTPS source with SSRF validation,
    streamed chunking, 50MB limit, and timeout protection.
    """
    validated_url = validate_url(url)
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    req = urllib.request.Request(
        validated_url,
        headers={"User-Agent": "ETS-Watchtower/1.0 (Enterprise Expiry Alert System)"},
    )

    total_bytes = 0
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"Remote file exceeds size cap ({int(content_length)} > {MAX_FILE_SIZE_BYTES} bytes)."
            )

        with open(dest_path, "wb") as out_file:
            while True:
                chunk = response.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE_BYTES:
                    raise ValueError(
                        f"Download exceeded maximum permitted size of {MAX_FILE_SIZE_BYTES} bytes."
                    )
                out_file.write(chunk)


if __name__ == "__main__":
    url = os.environ.get("EXCEL_URL")
    dest = os.environ.get("EXCEL_PATH", "data/expiry_source.xlsx")

    if not url:
        print("EXCEL_URL not set - skipping download (assuming the file already exists locally).")
        sys.exit(0)

    print(f"Downloading Excel file securely to {dest} ...")
    try:
        download(url, dest)
        print("Download complete.")
    except Exception as e:
        print(f"Error downloading source Excel: {e}", file=sys.stderr)
        sys.exit(1)