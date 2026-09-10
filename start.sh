#!/usr/bin/env bash
set -e

echo "Starting ETS Expiry Alert System on port ${PORT:-8501}..."
exec streamlit run dashboard/app.py \
  --server.port "${PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true
