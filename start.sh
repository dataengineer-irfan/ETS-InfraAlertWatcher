#!/usr/bin/env bash
set -e

echo "Ensuring Streamlit static index.html is patched..."
python scripts/patch_streamlit.py || true

echo "Starting ETS Expiry Alert System on port ${PORT:-8501}..."
exec streamlit run dashboard/app.py \
  --server.port "${PORT:-8501}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false \
  --server.enableWebsocketCompression false
