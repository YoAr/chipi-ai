# One image: builds the React app, then runs FastAPI serving
#   /      the landing page   (frontend/landing/index.html)
#   /app/  the React app      (frontend/dist)
#   /api/  the API

# --- Stage 1: build the React app -----------------------------------------
FROM node:22-slim AS web
WORKDIR /srv/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime ------------------------------------------------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.7.1 /uv /usr/local/bin/uv
WORKDIR /srv

# Dependencies first so code changes don't reinstall them
COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project

COPY backend/ backend/
COPY fixtures/ fixtures/
COPY data/ data/
# Landing page, information pages and their assets (.dockerignore drops the old export files)
COPY frontend/landing/ frontend/landing/
COPY --from=web /srv/frontend/dist frontend/dist

ENV MOCK_MODE=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["/srv/backend/.venv/bin/uvicorn", "app.main:app", "--app-dir", "/srv/backend", "--host", "0.0.0.0", "--port", "8080"]
