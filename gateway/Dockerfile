FROM python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
      nftables dnsmasq-base keepalived iproute2 openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir mitmproxy==12.3.1
WORKDIR /app
COPY bw_gateway ./bw_gateway
COPY entrypoint.sh ./
ENV PYTHONUNBUFFERED=1 BW_RUN_DIR=/run/breakwater
ENTRYPOINT ["/app/entrypoint.sh"]
