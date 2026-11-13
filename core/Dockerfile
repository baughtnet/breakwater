# Build context: repository root
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.13-slim-trixie
WORKDIR /app
COPY core/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY core/breakwater ./breakwater
COPY --from=web /web/dist ./web
RUN useradd --uid 1000 --create-home bw && mkdir -p /data && chown bw /data
USER bw
ENV PYTHONUNBUFFERED=1 BW_WEB_DIR=/app/web BW_DATA=/data
EXPOSE 8080
CMD ["python", "-m", "breakwater.main"]
