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

import os
import sys
import urllib.request


def download(url: str, dest_path: str):
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "expiry-alert-system"})
    with urllib.request.urlopen(req) as response, open(dest_path, "wb") as out_file:
        out_file.write(response.read())


if __name__ == "__main__":
    url = os.environ.get("EXCEL_URL")
    dest = os.environ.get("EXCEL_PATH", "data/expiry_source.xlsx")

    if not url:
        print("EXCEL_URL not set - skipping download (assuming the file already exists locally).")
        sys.exit(0)

    print(f"Downloading Excel file to {dest} ...")
    download(url, dest)
    print("Download complete.")