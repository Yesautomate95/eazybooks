# Use slim Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (for psycopg2, pyodbc, etc.)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    unixodbc-dev \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .

# Install CPU-only PyTorch FIRST (much smaller than GPU version ~200MB vs 2GB)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install Python dependencies
RUN pip install --no-cache-dir --timeout 1000 -r requirements.txt

# Copy application code
COPY . .

# Expose port (Railway uses $PORT env var)
EXPOSE 8080

# Run the application using shell to read $PORT env var.
# ws-ping-interval/timeout are an explicit second layer of WebSocket keepalive
# (the app-level heartbeat in the Electron bridge is the primary mechanism).
CMD uvicorn run:app --host 0.0.0.0 --port ${PORT:-8080} --ws-ping-interval 20 --ws-ping-timeout 20
