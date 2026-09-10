# ==============================================================================
# Production Dockerfile: ETS Expiry Alert System & Watchtower Platform
# ==============================================================================
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8501 \
    DB_PATH=data/expiry.db

WORKDIR /app

# Install system utilities if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Ensure data directory exists
RUN mkdir -p data

# Expose default Streamlit port
EXPOSE 8501

# Run Streamlit with production flags
ENTRYPOINT ["streamlit", "run", "dashboard/app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
