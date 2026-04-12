# Computer-use agent sandbox container
# Includes: Xvfb, x11vnc, ffmpeg, Python 3.11, no root at runtime
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    xvfb \
    x11vnc \
    ffmpeg \
    xdotool \
    scrot \
    dbus-x11 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 -s /bin/bash agent
WORKDIR /app

# Python packages
COPY requirements.txt* ./
RUN python3.11 -m pip install --no-cache-dir -e ".[dev]" 2>/dev/null || true

# Copy patterns
COPY patterns/ ./patterns/
COPY examples/ ./examples/

# Runtime env
ENV DISPLAY=:99
ENV SCREEN_WIDTH=1280
ENV SCREEN_HEIGHT=800
ENV PYTHONUNBUFFERED=1

# Entrypoint starts Xvfb then runs the agent
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER agent
EXPOSE 5999

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3.11", "examples/secure_agent_runner.py"]
