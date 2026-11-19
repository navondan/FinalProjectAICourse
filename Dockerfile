# One Dockerfile, three images, one per entry point:
#
#   docker build --target mcp            -> MCP tool server        (port 9876, /health)
#   docker build --target api            -> agent HTTP API         (port 8000, /health, /ready)
#   docker build --target renewal-worker -> renewal worker         (no port, HEALTHCHECK NONE)
#
# Every image is tagged with the commit it was built from (see compose.yaml and
# scripts/build-images.ps1|.sh), so a running container can be traced back to source.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Fixed non-root uid: root in a container is root on the host kernel for anything that
# escapes, and a fixed uid keeps bind-mounted file ownership predictable.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# Dependencies first, source second: source edits must not re-run the slow install.
COPY pyproject.toml README.md ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

COPY novaops/ ./novaops/
RUN pip install --no-deps .

# The dataset seeds the SQLite database; the database and FAISS index themselves live
# in the /app/data volume so every service sees the same state.
COPY novaops-enterprise-agent-dataset/ ./novaops-enterprise-agent-dataset/
RUN mkdir -p /app/data && chown appuser:appuser /app/data

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA
ENV NOVAOPS_GIT_SHA=$GIT_SHA


# --- MCP tool server ------------------------------------------------------------
FROM base AS mcp
USER appuser
EXPOSE 9876
HEALTHCHECK --interval=15s --timeout=4s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9876/health', timeout=3)"
# 0.0.0.0 is required in a container: 127.0.0.1 is reachable only from inside it.
CMD ["python", "-m", "novaops.cli", "serve", "--host", "0.0.0.0", "--port", "9876"]


# --- agent HTTP API (Maya + Webex) ---------------------------------------------
FROM base AS api
USER appuser
EXPOSE 8000
# Image-level check is liveness (/health). compose.yaml overrides it with /ready,
# because what a dependent service needs to know is whether traffic can be sent.
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "novaops.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]


# --- renewal worker -------------------------------------------------------------
FROM base AS renewal-worker
USER appuser
# A worker listens on nothing, so any HTTP check would report it unhealthy forever.
HEALTHCHECK NONE
CMD ["python", "-m", "novaops.workflows.renewal.worker"]
