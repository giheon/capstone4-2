FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/app/.venv
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.12.22
COPY pyproject.toml uv.lock .python-version ./
COPY backend/ backend/
RUN uv sync --frozen --no-dev
COPY configs/ configs/
COPY scripts/download_model.py scripts/download_model.py
RUN uv run --no-sync python scripts/download_model.py
COPY --from=frontend /frontend/dist frontend/dist
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "privframe.api:app", "--host", "0.0.0.0", "--port", "8000"]
