FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    E_INVOICE_DATA_DIR=/data \
    E_INVOICE_BROWSER_HEADLESS=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY --from=frontend-builder /frontend/dist ./frontend/dist
COPY scripts ./scripts
COPY run.sh /run.sh
RUN chmod +x /run.sh && mkdir -p /data

ARG BUILD_VERSION=1.0.1
# Home Assistant Supervisor overrides this for each published target. The default
# matches this project's Apple Silicon development host for direct local builds.
ARG BUILD_ARCH=aarch64
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"
ENV E_INVOICE_VERSION=1.0.1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["/run.sh"]
