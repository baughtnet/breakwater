#!/bin/sh
# Stand-in for the consumer router: NAT to the internet, plus the "block these MACs" access-control
# feature that makes Breakwater the only way out for kids' devices.
WAN=$(ip -o -4 route show default | awk '{print $5}')
LAN=$(ip -o -4 addr show | awk -v ip="$ROUTER_IP" '$4 ~ "^"ip"/" {print $2}')
nft -f - <<NFT
table inet router {
  set blocked_macs { type ether_addr; elements = { ${BLOCKED_MACS} } }
  chain forward { type filter hook forward priority filter; policy accept;
    iifname "$LAN" ether saddr @blocked_macs drop
  }
  chain postrouting { type nat hook postrouting priority srcnat; oifname "$WAN" masquerade; }
}
NFT
echo "router up: lan=$LAN wan=$WAN"
exec sleep infinity
