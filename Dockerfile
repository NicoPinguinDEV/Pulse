# Pulse TeamOS production image. Python version = this FROM line (Dependabot bumps it); CI runs the full test suite
# on it as well (python-syntax.yml: "Regressionstests mit der Python-Version des Images").
FROM python:3.15-slim

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

# O-01: run as an unprivileged user. PULSE_UID changes it (the data directory on the host must belong to it).
ARG PULSE_UID=1000
RUN useradd --create-home --uid "${PULSE_UID}" --shell /usr/sbin/nologin pulse

# Phase 13 (D-06 / SEC R-05): the code belongs to root and is only readable for the container user – together
# with `read_only: true` in compose.yaml a compromised process cannot change *.py files any more. Byte code is
# compiled at build time because the code folder is not writable at runtime.
COPY . /app
RUN chmod -R a+rX,go-w /app && python -m compileall -q /app

# Runtime data lives only in /data (compose: bind mount of /opt/pulse/data). TMPDIR on the data disk keeps large
# temporary files (backup restore staging) out of the RAM-backed /tmp.
RUN install -d -o pulse -g pulse -m 750 /data
ENV PULSE_DATA_DIR=/data \
    TMPDIR=/data/tmp \
    XDG_CACHE_HOME=/tmp/.cache

# Commit shown in the system status (no .git in the container). Set by deploy/remote_deploy.sh and the
# boot update via compose build args; empty for a plain `docker build`.
ARG PULSE_GIT_SHA=""
ARG PULSE_GIT_BRANCH=""
ENV PULSE_GIT_SHA=${PULSE_GIT_SHA} \
    PULSE_GIT_BRANCH=${PULSE_GIT_BRANCH}
USER pulse

EXPOSE 25095
# O-01: /healthz answers 200 once Discord, the guild and the database are ready (503 before).
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.getenv('SERVER_PORT','25095'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=8).status == 200 else 1)"
CMD ["python", "main.py"]
