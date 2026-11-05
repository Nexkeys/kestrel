# syntax=docker/dockerfile:1
# =============================================================================
# Stage 1 -- Build dependencies
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies for MetaTrader5 (Wine is needed because MT5 is Windows software)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wine64 \
    wget \
    curl \
    gnupg2 \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install pip dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Stage 2 -- Runtime
# =============================================================================
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Kestrel FX Trading Bot"
LABEL org.opencontainers.image.description="FX Scanner and Trading Bot for MetaTrader 5"
LABEL org.opencontainers.image.version="1.0.0"

WORKDIR /app

# Runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wine64 \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy built dependencies from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create non-root user for safety (MT5 requires a display/terminal context -- adjust as needed)
RUN useradd -m kestrel && chown -R kestrel:kestrel /app
USER kestrel

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# =============================================================================
# IMPORTANT -- MT5 Terminal Requirement
# =============================================================================
# This container does NOT include the MT5 terminal binary. The MT5 terminal
# must be running on the host machine (Windows) or accessible via a remote
# Windows VPS. The Python MetaTrader5 package communicates with the terminal
# via IPC (named pipes / shared memory) which requires the MT5 terminal to
# be running.
#
# Recommended deployment:
#   1. Run MT5 on a Windows VPS or dedicated Windows server 24/7
#   2. Mount the MT5 terminal folder into the container at /app/mt5
#   3. Set terminal_path to the path of Terminal64.exe inside the container
#
# Example docker-compose override:
#   volumes:
#     - /path/to/mt5:/app/mt5:ro
#   environment:
#     - KESTREL_TERMINAL_PATH=/app/mt5/Terminal64.exe
#
# For a pure Linux deployment, consider using MetaTrader 5 Web API (via a
# broker that exposes it) instead of the Python package.
# =============================================================================

# Default command -- override with docker run or compose
CMD ["python", "main.py"]
