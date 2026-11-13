"""The gateway agent: turns the core's policy state into firewall rules, DHCP and failover,
supervises the proxy, and reports what the network is doing.

If the core is unreachable it keeps enforcing the last state it was given (also across restarts).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import blockpage, netconf

log = logging.getLogger("bw.agent")

RUN = Path(os.environ.get("BW_RUN_DIR", "/run/breakwater"))
DATA = Path(os.environ.get("BW_DATA", "/data"))
CA_DIR = os.environ.get("BW_CA_DIR", "/ca")
CORE_URL = os.environ.get("BW_CORE_URL", "http://127.0.0.1:8080").rstrip("/")
TOKEN = os.environ.get("BW_CLUSTER_TOKEN", "")
HERE = Path(__file__).resolve().parent


def find_interface(ip: str) -> str:
    """The interface that carries this node's LAN address."""
    out = subprocess.run(["ip", "-o", "-4", "addr", "show"], text=True, capture_output=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 3 and parts[3].split("/")[0] == ip:
            return parts[1]
    raise SystemExit(f"No interface has the address {ip}; set BW_LAN_IF or fix BW_NODE_IP.")


def env_net() -> netconf.Net:
    e = os.environ
    return netconf.Net(
        lan_if=e.get("BW_LAN_IF") or find_interface(e["BW_NODE_IP"]), lan_cidr=e["BW_LAN_CIDR"], router_ip=e["BW_ROUTER_IP"], node_ip=e["BW_NODE_IP"],
        vip=e.get("BW_VIP", ""), wan_if=e.get("BW_WAN_IF", ""), dhcp_start=e.get("BW_DHCP_START", ""),
        dhcp_end=e.get("BW_DHCP_END", ""), dhcp_lease=e.get("BW_DHCP_LEASE", "2h"),
        peers=[p.strip() for p in e.get("BW_PEER_IPS", "").split(",") if p.strip()],
        vrrp_priority=int(e.get("BW_VRRP_PRIORITY", "100")), vrrp_pass=e.get("BW_VRRP_PASS", TOKEN[:8] or "breakwtr"))


def sh(*cmd: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    if check and res.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {res.stderr.strip() or res.stdout.strip()}")
    return res


def core(path: str, body: Any = None, timeout: float = 5) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(CORE_URL + path, data=data, method="POST" if data else "GET",
                                 headers={"X-BW-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def dns_answers(port: int) -> bool:
    """Does the filter on this port answer a query at all?"""
    query = struct.pack(">HHHHHH", 0xB0A7, 0x0100, 1, 0, 0, 0) + b"\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1.5)
            s.sendto(query, ("127.0.0.1", port))
            return len(s.recv(512)) >= 12
    except OSError:
        return False


class Proc:
    """A supervised child process, restarted if it dies."""

    def __init__(self, name: str, cmd: list[str]):
        self.name, self.cmd = name, cmd
        self.p: subprocess.Popen | None = None
        self.started = 0.0

    def alive(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def ensure(self) -> None:
        if self.alive() or time.time() - self.started < 3:
            return
        if self.p is not None:
            log.warning("%s exited with %s; restarting", self.name, self.p.returncode)
        self.p = subprocess.Popen(self.cmd)
        self.started = time.time()

    def stop(self) -> None:
        if self.alive():
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.p = None

    def hup(self) -> None:
        if self.alive():
            self.p.send_signal(signal.SIGHUP)


class Agent:
    def __init__(self, net: netconf.Net):
        self.net = net
        self.state: dict[str, Any] = {"version": "", "quarantine": True, "devices": [], "passthrough": [], "tz": "UTC"}
        self.state_file = RUN / "state.json"
        self.cache_file = DATA / "state.json"
        self.applied = ""
        self.dnsmasq_conf = ""
        self.counters: dict[str, dict[str, int]] = {"up": {}, "down": {}}
        self.pending: list[dict[str, Any]] = []
        self.neigh: dict[str, str] = {}
        self.last_counters = self.last_neigh = 0.0
        self.dhcp_enabled = os.environ.get("BW_DHCP", "1") == "1"
        self.dnsmasq = Proc("dnsmasq", ["dnsmasq", "--keep-in-foreground", f"--conf-file={RUN}/dnsmasq.conf"])
        self.proxy = Proc("mitmdump", [
            "mitmdump", "--mode", "transparent", "--listen-host", "0.0.0.0", "--listen-port", str(netconf.PROXY_PORT),
            "--set", f"confdir={CA_DIR}", "--set", "block_global=false", "--set", "connection_strategy=lazy",
            "--set", "termlog_verbosity=warn", "--set", "flow_detail=0", "-s", str(HERE / "addon.py")])
        self.keepalived = Proc("keepalived", ["keepalived", "--dont-fork", "--log-console", "-f", f"{RUN}/keepalived.conf"])

    # ---------------------------------------------------------------- setup
    def setup(self) -> None:
        RUN.mkdir(parents=True, exist_ok=True)
        DATA.mkdir(parents=True, exist_ok=True)
        for key, val in (("net/ipv4/ip_forward", "1"), ("net/ipv4/conf/all/send_redirects", "0"),
                         (f"net/ipv4/conf/{self.net.lan_if}/send_redirects", "0"),
                         ("net/ipv4/conf/all/rp_filter", "2")):
            try:
                Path("/proc/sys", key).write_text(val)
            except OSError as exc:  # read-only /proc/sys: the container runtime must set it instead
                cur = Path("/proc/sys", key).read_text().strip() if Path("/proc/sys", key).exists() else "?"
                if cur != val:
                    log.warning("could not set %s=%s (currently %s): %s", key, val, cur, exc)
        if sh("nft", "list", "table", "inet", "bw_acct", check=False).returncode:
            sh("nft", "-f", "-", stdin=netconf.render_acct(self.net))
        self._docker_forward_rule()
        if self.cache_file.exists():
            try:
                self.state = json.loads(self.cache_file.read_text())
                log.info("loaded cached policy %s", self.state.get("version"))
            except ValueError:
                pass
        self.apply()
        blockpage.serve(netconf.BLOCKPAGE_PORT, self.state_file)
        if self.net.clustered:
            (RUN / "keepalived.conf").write_text(netconf.render_keepalived(self.net, str(HERE / "healthcheck.sh")))

    def _docker_forward_rule(self) -> None:
        """Docker sets the host's FORWARD policy to drop; let LAN traffic we route through anyway."""
        if sh("nft", "list", "chain", "ip", "filter", "DOCKER-USER", check=False).returncode:
            return
        listing = sh("nft", "list", "chain", "ip", "filter", "DOCKER-USER", check=False).stdout
        if "breakwater" not in listing:
            sh("nft", "insert", "rule", "ip", "filter", "DOCKER-USER", "iifname", self.net.lan_if, "oifname",
               self.net.wan_if, "accept", "comment", '"breakwater"', check=False)

    # ---------------------------------------------------------------- apply
    def apply(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        tmp.replace(self.state_file)
        sh("nft", "-f", "-", stdin=netconf.render_nft(self.net, self.state))
        (RUN / "dhcp-hosts").write_text(netconf.render_dhcp_hosts(self.state))
        conf = netconf.render_dnsmasq(self.net, self.state, f"{RUN}/dhcp-hosts", f"{DATA}/dnsmasq.leases",
                                      str(HERE / "lease-hook.sh"))
        if conf != self.dnsmasq_conf:
            (RUN / "dnsmasq.conf").write_text(conf)
            self.dnsmasq_conf = conf
            self.dnsmasq.stop()  # main config changed: restart rather than reload
        else:
            self.dnsmasq.hup()
        self.applied = self.state.get("version", "")

    def poll_state(self) -> bool:
        try:
            status, body = core(f"/internal/state?have={self.applied}")
        except OSError:
            return False
        if status == 200 and body:
            self.state = body
            try:
                self.apply()
                self.cache_file.write_text(json.dumps(body))
                log.info("applied policy %s (%d devices)", body["version"], len(body["devices"]))
            except Exception:
                log.exception("failed to apply policy")
        return status in (200, 304)

    # ----------------------------------------------------------- observation
    def vip_held(self) -> bool:
        if not self.net.clustered:
            return True
        out = sh("ip", "-4", "-o", "addr", "show", "dev", self.net.lan_if, check=False).stdout
        return f" {self.net.vip}/" in out

    def ipv6_leak(self) -> bool:
        """A v6 default route on the LAN means devices may have a path around the IPv4 gateway."""
        out = sh("ip", "-6", "route", "show", "default", "dev", self.net.lan_if, check=False).stdout
        return bool(out.strip())

    def read_neigh(self) -> list[list[str]]:
        out = sh("ip", "-4", "neigh", "show", "dev", self.net.lan_if, check=False).stdout
        pairs = []
        for line in out.splitlines():
            parts = line.split()
            if "lladdr" in parts and parts[-1] not in ("FAILED", "INCOMPLETE"):
                pairs.append([parts[0], parts[parts.index("lladdr") + 1].lower()])
        self.neigh = {ip: mac for ip, mac in pairs}
        return pairs

    def read_counters(self) -> list[list[Any]]:
        """Per-device byte deltas since the last sample: [mac, minute, up, down]."""
        ip_mac = dict(self.neigh)
        ip_mac.update({d["ip"]: d["mac"] for d in self.state.get("devices", []) if d.get("ip")})
        totals: dict[str, list[int]] = {}
        for direction in ("up", "down"):
            res = sh("nft", "-j", "list", "set", "inet", "bw_acct", direction, check=False)
            if res.returncode:
                continue
            sets = [o["set"] for o in json.loads(res.stdout)["nftables"] if "set" in o]
            seen: dict[str, int] = {}
            for el in (sets[0].get("elem") or []) if sets else []:
                e = el.get("elem", el)
                key, count = str(e["val"]).lower(), int(e["counter"]["bytes"])
                seen[key] = count
                prev = self.counters[direction].get(key, 0)
                delta = count - prev if count >= prev else count
                mac = key if direction == "up" else ip_mac.get(key)
                if delta > 0 and mac:
                    totals.setdefault(mac, [0, 0])[0 if direction == "up" else 1] += delta
            self.counters[direction] = seen
        minute = int(time.time()) // 60 * 60
        return [[mac, minute, up, down] for mac, (up, down) in totals.items()]

    def read_leases(self) -> list[dict[str, Any]]:
        spool = RUN / "lease-events"
        if not spool.exists():
            return []
        taken = spool.with_suffix(".reading")
        spool.replace(taken)
        facts = []
        for line in taken.read_text().splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            ts, event, mac, ip = float(parts[0]), parts[1], parts[2].lower(), parts[3]
            hostname = parts[4] if len(parts) > 4 and parts[4] != "-" else ""
            expires = float(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
            facts.append({"kind": "lease", "ts": ts, "data": {"mac": mac, "ip": ip, "hostname": hostname,
                                                             "expires": expires, "event": event}})
        taken.unlink()
        return facts

    # ----------------------------------------------------------------- loop
    def step(self) -> None:
        now = time.time()
        core_ok = self.poll_state()
        active = self.vip_held()

        self.proxy.ensure()
        if self.net.clustered:
            self.keepalived.ensure()
        want_dhcp = self.dhcp_enabled and active
        if want_dhcp and not self.dnsmasq.alive():
            self._seed_leases()
            self.dnsmasq.ensure()
        elif not want_dhcp and self.dnsmasq.alive():
            self.dnsmasq.stop()

        dns_ok = dns_answers(netconf.DNS_STRICT_PORT) and dns_answers(netconf.DNS_BASIC_PORT)
        if dns_ok and self.proxy.alive():
            (RUN / "healthy").write_text(str(now))

        self.pending += self.read_leases()
        if now - self.last_counters >= 60:
            self.last_counters = now
            rows = self.read_counters()
            if rows:
                self.pending.append({"kind": "usage", "ts": now, "data": {"rows": rows}})
        report: dict[str, Any] = {"status": {"vip_held": active, "dhcp": self.dnsmasq.alive(), "proxy": self.proxy.alive(),
                                             "dns": dns_ok, "ipv6_leak": self.ipv6_leak(), "policy": self.applied}}
        if now - self.last_neigh >= 20:
            self.last_neigh = now
            report["neigh"] = self.read_neigh()
        if core_ok:
            report["facts"] = self.pending[:500]
            try:
                status, _ = core("/internal/report", report)
                if status == 200:
                    del self.pending[:500]
            except OSError:
                pass
        del self.pending[:-5000]  # never grow without bound while the core is away

    def _seed_leases(self) -> None:
        """Before taking over DHCP, start from the cluster's lease table so addresses stay put."""
        try:
            status, leases = core("/internal/leases")
            if status == 200 and leases is not None:
                (DATA / "dnsmasq.leases").write_text(netconf.render_leases(leases, time.time()))
        except OSError:
            pass

    def run(self) -> None:
        self.setup()
        stop = False

        def on_term(*_: Any) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        while not stop:
            try:
                self.step()
            except Exception:
                log.exception("agent step failed")
            time.sleep(2)
        for proc in (self.keepalived, self.dnsmasq, self.proxy):
            proc.stop()
        # Leaving the firewall table in place on shutdown keeps devices filtered (fail-closed).


def main() -> None:
    logging.basicConfig(level=os.environ.get("BW_LOG", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    Agent(env_net()).run()


if __name__ == "__main__":
    main()
