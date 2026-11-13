#!/usr/bin/env bash
# First-time setup for one Breakwater node. Run from the repository root on a Debian/Ubuntu/Raspberry Pi OS
# machine that has a fixed LAN address. Safe to re-run: it never overwrites an existing .env.
#
#   sudo ./deploy/setup-node.sh                 # primary
#   sudo ./deploy/setup-node.sh --standby       # an additional node (copy secrets + CA from the primary first)
set -euo pipefail
cd "$(dirname "$0")/.."

role=primary
[[ "${1:-}" == "--standby" ]] && role=standby
[[ $EUID -eq 0 ]] || { echo "Run with sudo: this script sets kernel options and installs Docker."; exit 1; }

ask() { local prompt=$1 default=$2 reply; read -r -p "$prompt [$default]: " reply; echo "${reply:-$default}"; }

# ---- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null; then
  echo "Installing Docker Engine..."
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null || { echo "The Docker Compose plugin is missing (apt install docker-compose-plugin)."; exit 1; }
if docker info 2>/dev/null | grep -q "Docker Desktop"; then
  echo "This is Docker Desktop, which runs containers inside its own VM and cannot route LAN traffic."
  echo "Use Docker Engine (or a small Debian VM / Raspberry Pi) for a Breakwater node."
  exit 1
fi

# ---- kernel settings a router needs -----------------------------------------
cat > /etc/sysctl.d/90-breakwater.conf <<SYSCTL
# Breakwater routes traffic for the devices behind it.
net.ipv4.ip_forward = 1
# Never tell devices "use the real router directly" - that would send them around the filter.
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
# Replies to the shared virtual IP may arrive on a path the strict check would reject.
net.ipv4.conf.all.rp_filter = 2
SYSCTL
sysctl --system >/dev/null
# "all" and the per-interface value are OR-ed for redirects, so existing interfaces need it too.
for f in /proc/sys/net/ipv4/conf/*/send_redirects; do echo 0 > "$f"; done
echo "Kernel settings applied."

# ---- .env ---------------------------------------------------------------------
if [[ -f .env ]]; then
  echo ".env already exists; leaving it untouched."
else
  route=$(ip -4 route show default | head -1)
  router_guess=$(awk '{print $3}' <<<"$route")
  dev=$(awk '{print $5}' <<<"$route")
  ip_cidr=$(ip -4 -o addr show dev "$dev" | awk '{print $4}' | head -1)
  node_ip=${ip_cidr%/*}
  lan=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$ip_cidr")
  prefix=${node_ip%.*}

  echo; echo "Answer for THIS machine. Press Enter to accept a suggestion."
  node_id=$(ask "Name for this node" "$(hostname -s)")
  node_ip=$(ask "This machine's LAN address (must be fixed, not from DHCP)" "$node_ip")
  lan=$(ask "Home network" "$lan")
  router=$(ask "Router address" "$router_guess")
  vip=$(ask "Shared address devices will use as gateway and DNS (unused address; with ONE node just use $node_ip)" "$node_ip")
  peers_ip=$(ask "LAN addresses of the OTHER Breakwater nodes, comma-separated (empty if none)" "")
  tz=$(ask "Time zone" "$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo America/Vancouver)")

  peers_url=""
  IFS=',' read -ra arr <<<"$peers_ip"
  for p in "${arr[@]}"; do p=${p// /}; [[ -n $p ]] && peers_url+="${peers_url:+,}http://$p:8080"; done

  if [[ $role == standby ]]; then
    echo; echo "A standby must share the primary's two secrets. Copy them from the primary's .env:"
    read -r -p "BW_CLUSTER_TOKEN: " token
    read -r -p "BW_ADGUARD_PASSWORD: " agpass
    priority=100
  else
    token=$(openssl rand -hex 24); agpass=$(openssl rand -hex 16); priority=150
  fi

  umask 077
  cat > .env <<ENV
BW_NODE_ID=$node_id
BW_ROLE=$role
BW_NODE_IP=$node_ip
BW_TZ=$tz
BW_HTTP_PORT=8080
BW_LAN_CIDR=$lan
BW_ROUTER_IP=$router
BW_VIP=$vip
# Start with DHCP off: bring the stack up, check the dashboard, then follow docs/deploy.md to cut over.
BW_DHCP=0
BW_DHCP_START=$prefix.100
BW_DHCP_END=$prefix.220
BW_DHCP_LEASE=2h
BW_PEERS=$peers_url
BW_PEER_IPS=${peers_ip// /}
BW_VRRP_PRIORITY=$priority
BW_CLUSTER_TOKEN=$token
BW_ADGUARD_PASSWORD=$agpass
ENV
  echo "Wrote .env (DHCP is off for now)."
fi

# ---- data directories -----------------------------------------------------------
mkdir -p data/core data/gateway data/ca data/adguard-strict/{conf,work} data/adguard-basic/{conf,work}
chown -R 1000:1000 data/core   # the core service runs unprivileged
if [[ $role == standby && ! -f data/ca/mitmproxy-ca.pem ]]; then
  echo
  echo "Before starting this standby, copy the household certificate from the primary so both nodes"
  echo "present the same one to inspected devices:"
  echo "    sudo scp -r <primary>:$(pwd)/data/ca/. data/ca/"
fi

echo
echo "Next:  docker compose up -d --build"
echo "Then open http://$(grep ^BW_NODE_IP .env | cut -d= -f2):8080 and continue with docs/deploy.md."
