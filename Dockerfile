# One image, two build steps: compile the Vue frontend, then run the API which
# serves that build. Railway runs this as the single web service; Postgres is a
# separate Railway database, reached through DATABASE_URL.

FROM node:22-slim
WORKDIR /app

# --- Frontend build ---------------------------------------------------------
# VITE_API_BASE is empty so the browser calls /api on the same origin, which the
# API serves. --include=dev keeps vue-tsc and vite even when NODE_ENV=production.
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci --include=dev
COPY frontend/ frontend/
ENV VITE_API_BASE=""
RUN cd frontend && npm run build

# --- API --------------------------------------------------------------------
# tsx runs the TypeScript source directly, so the dev deps stay in the image.
COPY api/package.json api/package-lock.json api/
RUN cd api && npm ci --include=dev
COPY api/ api/

# The API reads process.env.PORT; Railway sets it. It resolves frontend/dist as
# ../../frontend/dist from api/src, so the tree layout above must be preserved.
EXPOSE 3000
CMD ["npm", "--prefix", "api", "start"]
