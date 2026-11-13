"""Render firewall, DHCP and failover configuration from the policy state. Pure text in, text out."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any

DNS_STRICT_PORT = 5301
DNS_BASIC_PORT = 5302
PROXY_PORT = 8085
BLOCKPAGE_PORT = 8086

# Well-known public resolvers that answer DNS-over-HTTPS on :443. Names are also blocked at the DNS layer;
# this catches clients that ship the addresses built in.
DOH_V4 = [
    "8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "1.1.1.2", "1.0.0.2", "1.1.1.3", "1.0.0.3",
    "9.9.9.9", "9.9.9.10", "9.9.9.11", "9.9.9.12", "149.112.112.9", "149.112.112.10", "149.112.112.11",
    "149.112.112.112", "208.67.222.222", "208.67.220.220", "208.67.222.123", "208.67.220.123",
    "94.140.14.14", "94.140.15.15", "94.140.14.15", "94.140.15.16", "94.140.14.140", "94.140.14.141",
    "45.90.28.0/22", "76.76.2.0/24", "76.76.10.0/24", "76.76.19.19", "76.223.122.150",
    "185.228.168.0/24", "185.228.169.0/24", "194.242.2.2", "194.242.2.3", "194.242.2.4", "194.242.2.5",
    "194.242.2.9", "146.112.41.2", "146.112.41.3", "104.16.248.249", "104.16.249.249",
]


@dataclass
class Net:
    lan_if: str
    lan_cidr: str
    router_ip: str
    node_ip: str
    vip: str = ""
    wan_if: str = ""  # defaults to lan_if (one-armed)
    dhcp_start: str = ""
    dhcp_end: str = ""
    dhcp_lease: str = "2h"
    peers: list[str] = field(default_factory=list)
    vrrp_priority: int = 100
    vrrp_pass: str = "breakwtr"
    vrrp_id: int = 77

    def __post_init__(self) -> None:
        self.vip = self.vip or self.node_ip
        self.wan_if = self.wan_if or self.lan_if
        net = ipaddress.ip_network(self.lan_cidr, strict=False)
        hosts = list(net.hosts())
        if not self.dhcp_start:
            self.dhcp_start = str(hosts[min(99, len(hosts) - 1)])
        if not self.dhcp_end:
            self.dhcp_end = str(hosts[min(219, len(hosts) - 1)])

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.lan_cidr, strict=False)  # type: ignore[return-value]

    @property
    def clustered(self) -> bool:
        return bool(self.peers)


def _set(name: str, typ: str, elements: list[str], flags: str = "") -> str:
    body = f"type {typ};" + (f" flags {flags};" if flags else "")
    if elements:
        body += " elements = { " + ", ".join(sorted(set(elements))) + " }"
    return f"  set {name} {{ {body} }}"


def render_nft(net: Net, state: dict[str, Any]) -> str:
    """The whole `inet breakwater` table, replaced atomically on every policy change."""
    devs = [d for d in state.get("devices", []) if d.get("mac")]
    quarantine = state.get("quarantine", True)
    strict = [d["mac"] for d in devs if d["tier"] == "strict"]
    relaxed = [d["mac"] for d in devs if d["tier"] != "strict"]
    locked = [d for d in devs if d["locked"] and d["route"] == "gateway"]
    inspect = [d["mac"] for d in devs if d["inspect"] and not d["locked"]]
    default_dns = DNS_STRICT_PORT if quarantine else DNS_BASIC_PORT
    lan, cidr = net.lan_if, str(net.network)
    lines = [
        "table inet breakwater",
        "delete table inet breakwater",
        "table inet breakwater {",
        _set("strict_macs", "ether_addr", strict),
        _set("relaxed_macs", "ether_addr", relaxed),
        _set("locked_macs", "ether_addr", [d["mac"] for d in locked]),
        _set("locked_ips", "ipv4_addr", [d["ip"] for d in locked if d.get("ip")]),
        _set("inspect_macs", "ether_addr", inspect),
        _set("doh_v4", "ipv4_addr", DOH_V4, flags="interval"),
        "",
        "  chain prerouting {",
        "    type nat hook prerouting priority dstnat; policy accept;",
        f'    iifname != "{lan}" return',
        "    # Every DNS query from the LAN lands on the right filter, whatever server the device asked for.",
        f"    meta l4proto {{ tcp, udp }} th dport 53 ether saddr @relaxed_macs redirect to :{DNS_BASIC_PORT}",
        f"    meta l4proto {{ tcp, udp }} th dport 53 ether saddr @strict_macs redirect to :{DNS_STRICT_PORT}",
        f"    meta l4proto {{ tcp, udp }} th dport 53 redirect to :{default_dns}",
        "    # Paused devices get the 'internet is paused' page for plain-HTTP requests (incl. captive-portal probes).",
        f"    ether saddr @locked_macs tcp dport 80 redirect to :{BLOCKPAGE_PORT}",
        "    # Inspected devices: web traffic to the internet goes through the proxy.",
        f"    ether saddr @inspect_macs ip daddr != {cidr} tcp dport {{ 80, 443 }} redirect to :{PROXY_PORT}",
        "  }",
        "",
        "  chain input {",
        "    type filter hook input priority filter; policy accept;",
        "    # The filter and proxy ports are only reachable via the redirects above, never directly.",
        f'    iifname "{lan}" meta l4proto {{ tcp, udp }} th dport {{ {DNS_STRICT_PORT}, {DNS_BASIC_PORT}, {PROXY_PORT} }} '
        "ct status & dnat == 0 drop",
        "  }",
        "",
        "  chain output {",
        "    type filter hook output priority filter; policy accept;",
        "    # Never tell a device 'the real router is a shorter path' - that would steer it around the filter.",
        "    icmp type redirect drop",
        "  }",
        "",
        "  chain forward {",
        "    type filter hook forward priority filter; policy accept;",
        "    ether saddr @locked_macs meta l4proto tcp reject with tcp reset",
        "    ether saddr @locked_macs reject with icmpx admin-prohibited",
        "    ip daddr @locked_ips drop",
        "    ether saddr @relaxed_macs accept",
        f"    ip saddr {cidr} ip daddr != {cidr} jump no_bypass" if quarantine
        else f"    ether saddr @strict_macs ip daddr != {cidr} jump no_bypass",
        f'    meta nfproto ipv6 iifname "{lan}" drop',
        "  }",
        "",
        "  chain no_bypass {",
        "    # Encrypted DNS, QUIC and common VPN transports would all tunnel past the filter.",
        "    tcp dport 853 reject with tcp reset",
        "    udp dport { 443, 853 } reject",
        "    ip daddr @doh_v4 tcp dport 443 reject with tcp reset",
        "    udp dport { 500, 1194, 1701, 4500, 51820 } reject",
        "    tcp dport { 1194, 1723 } reject with tcp reset",
        "  }",
        "",
        "  chain postrouting {",
        "    type nat hook postrouting priority srcnat; policy accept;",
        "    # Traffic we route for LAN devices leaves with this node's address, so replies come back through us.",
        f'    ip saddr {cidr} ip daddr != {cidr} oifname "{net.wan_if}" masquerade',
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def render_acct(net: Net) -> str:
    """Per-device byte counters. Lives in its own table so policy reloads never reset it."""
    cidr = str(net.network)
    return f"""table inet bw_acct {{
  set up {{ type ether_addr; size 8192; flags dynamic; counter; }}
  set down {{ type ipv4_addr; size 8192; flags dynamic; counter; }}
  chain acct_forward {{
    type filter hook forward priority -5; policy accept;
    ip saddr {cidr} ip daddr != {cidr} update @up {{ ether saddr counter }}
    ip daddr {cidr} ip saddr != {cidr} update @down {{ ip daddr counter }}
  }}
  chain acct_input {{
    type filter hook input priority -5; policy accept;
    iifname "{net.lan_if}" tcp dport {PROXY_PORT} update @up {{ ether saddr counter }}
  }}
  chain acct_output {{
    type filter hook output priority -5; policy accept;
    tcp sport {PROXY_PORT} ip daddr {cidr} update @down {{ ip daddr counter }}
  }}
}}
"""


def render_dnsmasq(net: Net, state: dict[str, Any], hosts_file: str, lease_file: str, hook: str) -> str:
    quarantine = state.get("quarantine", True)
    return "\n".join([
        "# Generated by Breakwater. DHCP only; DNS is answered by AdGuard Home.",
        "port=0",
        f"interface={net.lan_if}",
        "bind-interfaces",
        "user=root",
        "dhcp-authoritative",
        f"dhcp-range={net.dhcp_start},{net.dhcp_end},{net.network.netmask},{net.dhcp_lease}",
        f"dhcp-option=option:dns-server,{net.vip}",
        "# Unknown devices: through Breakwater when quarantine is on, straight to the router otherwise.",
        f"dhcp-option=option:router,{net.vip if quarantine else net.router_ip}",
        f"dhcp-option=tag:via_gateway,option:router,{net.vip}",
        f"dhcp-option=tag:via_router,option:router,{net.router_ip}",
        f"dhcp-hostsfile={hosts_file}",
        f"dhcp-leasefile={lease_file}",
        f"dhcp-script={hook}",
        "",
    ])


def render_dhcp_hosts(state: dict[str, Any]) -> str:
    lines = []
    for d in sorted(state.get("devices", []), key=lambda d: d["mac"]):
        if not d.get("mac") or not d.get("known"):
            continue
        parts = [d["mac"], f"set:via_{d['route']}"]
        if d.get("static") and d.get("ip"):
            parts.append(d["ip"])
        lines.append(",".join(parts))
    return "\n".join(lines) + "\n"


def render_keepalived(net: Net, health_script: str) -> str:
    peers = "\n".join(f"        {p}" for p in net.peers)
    return f"""global_defs {{
    script_user root
    enable_script_security
}}
vrrp_script bw_health {{
    script "{health_script}"
    interval 2
    fall 2
    rise 2
}}
vrrp_instance BREAKWATER {{
    state BACKUP
    interface {net.lan_if}
    virtual_router_id {net.vrrp_id}
    priority {net.vrrp_priority}
    advert_int 1
    unicast_src_ip {net.node_ip}
    unicast_peer {{
{peers}
    }}
    authentication {{
        auth_type PASS
        auth_pass {net.vrrp_pass[:8]}
    }}
    virtual_ipaddress {{
        {net.vip}/{net.network.prefixlen} dev {net.lan_if}
    }}
    track_script {{
        bw_health
    }}
}}
"""


def render_leases(leases: list[dict[str, Any]], now: float) -> str:
    """dnsmasq lease file from the cluster-wide lease table, so a node taking over keeps addresses stable."""
    lines = []
    for le in leases:
        expires = int(le.get("expires") or 0) or int(now + 7200)
        lines.append(f"{expires} {le['mac']} {le['ip']} {le.get('hostname') or '*'} *")
    return "\n".join(lines) + ("\n" if lines else "")
