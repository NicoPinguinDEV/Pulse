# Pulse TeamOS production image. Keep Python 3.14 to match the current runtime.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libopus0 tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

# Runtime state is persisted on the host through the compose bind mount.
COPY . /app

EXPOSE 25095
CMD ["python", "main.py"]

# /healthz confirms the web process is reachable from inside the container.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('SERVER_PORT', '25095') + '/healthz', timeout=3).close()"]

# Non-root execution is deferred until D-06 moves persistent state out of the
# current compose bind mount (./:/app); USER before that would break host-state writes.
