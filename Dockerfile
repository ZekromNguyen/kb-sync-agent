# Rebuild from scratch each scheduled run. Keeps the image lean and avoids
# stale layer caches masking dependency changes.
FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The job writes outputs under data/ and logs/. Create them so the container
# has writable targets even with a read-only filesystem mount.
RUN mkdir -p data/markdown data/state logs

# Run the job once and exit. Pass secrets via --env-file or -e at runtime:
#   docker run --env-file .env kb-mini-agent
ENTRYPOINT ["python", "main.py"]
