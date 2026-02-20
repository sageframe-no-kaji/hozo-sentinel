# ──────────────────────────────────────────────────────────────────────────────
# Hōzō — Wake-on-demand ZFS backup orchestrator
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

# Install system dependencies:
#   - zfsutils-linux  → zfs / zpool CLI tools (needed inside container for testing)
#   - mbuffer         → syncoid transfer optimization
#   - lzop            → syncoid compression
#   - openssh-client  → ssh / sftp
#   - etherwake       → optional WOL command-line fallback
RUN apt-get update && apt-get install -y --no-install-recommends \
        zfsutils-linux \
        mbuffer \
        lzop \
        openssh-client \
        etherwake \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install sanoid/syncoid (Perl-based, from PPA or source)
# Using the official install approach for Debian/Ubuntu
RUN apt-get update && apt-get install -y --no-install-recommends \
        libconfig-inifiles-perl \
        libcapture-tiny-perl \
        git \
    && git clone --depth=1 https://github.com/jimsalterjrs/sanoid.git /opt/sanoid \
    && ln -s /opt/sanoid/syncoid /usr/local/bin/syncoid \
    && apt-get remove -y git \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────────────────────
# Python application
# ──────────────────────────────────────────────────────────────────────────────
WORKDIR /app

# Copy and install Python dependencies first (layer cache optimization)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Create directories for configs and logs
RUN mkdir -p /etc/hozo /var/log/hozo

# Expose web UI port
EXPOSE 8000

# Default config path (override via volume mount or HOZO_CONFIG env var)
ENV HOZO_CONFIG=/etc/hozo/config.yaml

# Entry point: start the web UI + scheduler
CMD ["hozo", "--config", "/etc/hozo/config.yaml", "serve", "--host", "0.0.0.0", "--port", "8000"]
