FROM node:24-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Mock imports are resolved by the bundler even when mock mode is disabled.
COPY fixtures/ /app/fixtures/
COPY backend/data/ /app/backend/data/
ENV VITE_USE_MOCK=0
RUN npm run build

FROM python:3.12-slim-bookworm AS app
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY fixtures/ ./fixtures/
COPY --from=frontend-build /app/frontend/dist/ ./frontend/dist/
EXPOSE 10000
# Stock and orders are process-local: one worker and one Render instance.
CMD ["sh", "-c", "exec python -m uvicorn backend.api.app:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
