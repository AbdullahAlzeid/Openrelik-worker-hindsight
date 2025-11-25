# Use the official Docker Hub Ubuntu base image
FROM ubuntu:24.04

# Prevent needing to configure debian packages, stopping the setup of
# the docker container.
RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections

# Install utilities needed for this worker.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-venv \
    p7zip-full \
    git \
    unzip \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Configure debugging
ARG OPENRELIK_PYDEBUG
ENV OPENRELIK_PYDEBUG=${OPENRELIK_PYDEBUG:-0}
ARG OPENRELIK_PYDEBUG_PORT
ENV OPENRELIK_PYDEBUG_PORT=${OPENRELIK_PYDEBUG_PORT:-5678}

# Set working directory
WORKDIR /openrelik

# Copy project files and install Python deps via uv (PEP 621)
COPY . ./
RUN python3 -m venv /openrelik/.venv \
    && . /openrelik/.venv/bin/activate \
    && pip install --no-cache-dir uv \
    && uv sync
ENV VIRTUAL_ENV=/openrelik/.venv PATH="/openrelik/.venv/bin:$PATH"

# ----------------------------------------------------------------------
# Install hindsight (browser artifact parser)
# ----------------------------------------------------------------------
# Install into the virtualenv using its pip
RUN . /openrelik/.venv/bin/activate \
    && pip install --no-cache-dir pyhindsight \
    && pip install --no-cache-dir git+https://github.com/cclgroupltd/ccl_chromium_reader.git \
    && chmod +x /openrelik/.venv/bin/hindsight.py /openrelik/.venv/bin/hindsight_gui.py
# ----------------------------------------------------------------------

# Default command if not run from docker-compose (and command being overridden)
CMD ["celery", "--app=src.hindsight_task", "worker", "--task-events", "--concurrency=1", "--loglevel=DEBUG", "-Q", "openrelik-worker-hindsight"]
