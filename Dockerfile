# Pulse TeamOS production image. Keep Python 3.14 to match the current runtime.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Berlin

WORKDIR /app

# ffmpeg + libopus0: voice-support audio (K-10) · tzdata: Europe/Berlin for shift and support times (O-01)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libopus0 tzdata \
    && rm -rf /var/lib/apt/lists/*

# Exactly the hash-checked versions CI tests and audits (security.yml) – not whatever the ranges in
# requirements.txt resolve to on build day, so a rebuild or rollback on the VPS gets the same packages.
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --upgrade pip \
    && python -m pip install --require-hashes -r /app/requirements.lock

# O-01: run as an unprivileged user. With the compose bind mount (./:/app) the host folder must belong to
# the same UID – see VPS_DEPLOYMENT.md ("chown -R 1000:1000 /opt/pulse/Pulse"). PULSE_UID changes it.
ARG PULSE_UID=1000
RUN useradd --create-home --uid "${PULSE_UID}" --shell /usr/sbin/nologin pulse

# Runtime state is persisted on the host through the compose bind mount.
COPY --chown=pulse:pulse . /app
USER pulse

EXPOSE 25095
# O-01: /healthz answers 200 once Discord, the guild and the database are ready (503 before).
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.getenv('SERVER_PORT','25095'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=8).status == 200 else 1)"
CMD ["python", "main.py"]
