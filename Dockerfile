# ==============================================================================
# Azure Cost Exporter — Dockerfile
#
# Builds a minimal image for the Azure Cost Exporter.
# Based on python:3.12-slim to keep the image size small.
#
# Usage :
#   docker build -t azure-cost-exporter .
#   docker run -e SUBSCRIPTION_ID=... -e RESOURCE_GROUP=... azure-cost-exporter
# ==============================================================================

FROM python:3.12-slim

WORKDIR /app

# Install dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Expose the Prometheus metrics port
EXPOSE 9101

# Health check — verifies that the metrics endpoint is reachable
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9101/metrics')" || exit 1

CMD ["python", "app.py"]
