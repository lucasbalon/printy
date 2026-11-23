ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.21

# --- interface -----------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build --outDir /static --emptyOutDir

# --- add-on --------------------------------------------------------------------
FROM ${BUILD_FROM}
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/printy ./printy
COPY --from=frontend /static ./printy/static
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
