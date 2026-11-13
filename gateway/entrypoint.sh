#!/bin/sh
set -e
CA_DIR="${BW_CA_DIR:-/ca}"
mkdir -p "$CA_DIR" /run/breakwater

# The lab has no "host network": point the container's default route at the simulated router.
if [ -n "$BW_LAB_ROUTE" ]; then
  ip route replace default via "$BW_ROUTER_IP"
  echo "nameserver 1.1.1.1" > /etc/resolv.conf
fi

# One CA per household, created on first start. Standby nodes get a copy at setup time.
if [ ! -f "$CA_DIR/mitmproxy-ca.pem" ]; then
  if [ "${BW_ROLE:-primary}" != "primary" ]; then
    echo "waiting for the household CA to be copied into $CA_DIR ..."
    while [ ! -f "$CA_DIR/mitmproxy-ca.pem" ]; do sleep 2; done
  else
    umask 077
    openssl req -x509 -new -nodes -newkey rsa:3072 -sha256 -days 3650 \
      -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" \
      -subj "/O=Breakwater/CN=Breakwater Home CA" \
      -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
    cat "$CA_DIR/ca.key" "$CA_DIR/ca.crt" > "$CA_DIR/mitmproxy-ca.pem.tmp"
    cp "$CA_DIR/ca.crt" "$CA_DIR/mitmproxy-ca-cert.pem"
    cp "$CA_DIR/ca.crt" "$CA_DIR/mitmproxy-ca-cert.cer"
    chmod 644 "$CA_DIR/mitmproxy-ca-cert.pem" "$CA_DIR/mitmproxy-ca-cert.cer"
    rm -f "$CA_DIR/ca.key" "$CA_DIR/ca.crt"
    mv "$CA_DIR/mitmproxy-ca.pem.tmp" "$CA_DIR/mitmproxy-ca.pem"
    echo "generated household CA in $CA_DIR"
  fi
fi
exec python -m bw_gateway.agent
