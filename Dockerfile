FROM ghcr.io/astral-sh/uv:0.8.22 AS uv

FROM node:22-bookworm-slim AS calc-builder

WORKDIR /calc
COPY calc/ ./
RUN npm ci --ignore-scripts \
    && npm ci --prefix calc --ignore-scripts
RUN npm run build

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SLINK_DATA_DIR=/data/runs \
    SLINK_ROM_DIR=/data/roms

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 slink \
    && useradd --uid 10001 --gid slink --create-home --shell /usr/sbin/nologin slink

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY requirements.txt requirements-cloud.txt ./
RUN uv pip install --system --no-cache --requirements requirements-cloud.txt

COPY --chown=slink:slink . .
COPY --from=calc-builder --chown=slink:slink /calc/dist ./calc/dist
RUN mkdir -p /data/runs /data/roms && chown -R slink:slink /data

USER 10001:10001

VOLUME ["/data"]
EXPOSE 8090 54321 54322 54323 54324 54325

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=3).read()"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "server.manager", "--host", "0.0.0.0", "--port", "8090"]
