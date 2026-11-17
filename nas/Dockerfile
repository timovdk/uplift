# Uplift feed service (see nas/README.md). Build from the repository root:
#   docker build -f nas/Dockerfile -t uplift-feed .
FROM python:3.12-slim

WORKDIR /app
COPY nas/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts/ scripts/
COPY nas/uplift_feed.py nas/
# Fallback station list until the service has built its own (monthly).
COPY data/stations.json data/

ENV PYTHONUNBUFFERED=1 HOME=/tmp DATA_DIR=/data
# Unhealthy when no message (not even the 10-minute heartbeat) came in for 15 minutes.
HEALTHCHECK --interval=5m --timeout=10s --start-period=15m \
    CMD python -c "import os,sys,time; sys.exit(time.time() - os.path.getmtime('/tmp/alive') > 900)"
CMD ["python", "nas/uplift_feed.py"]
