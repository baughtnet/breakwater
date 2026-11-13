# Deploying Breakwater

The cutover is staged so that each step is easy to undo. Budget an evening when nobody needs the
internet for an hour.

## What you need

- **A machine for the primary node.** A Debian 12/13 VM with 2 vCPUs, 2 GB RAM and 20 GB disk is
  plenty. Give it a **bridged** network interface (it must sit directly on the LAN) and a **fixed IP
  address** outside the router's DHCP range.
- **Optionally a second node** (another VM on a different host, a Raspberry Pi 4 or 5 on Ethernet, or a
  PC running Docker Engine). Wired, not Wi-Fi. Docker Desktop will not work: it cannot route LAN traffic.
- **One spare LAN address** for the shared virtual IP, if you run two or more nodes.
- Admin access to the router.

Example addresses used below: router `192.168.0.1`, virtual IP `192.168.0.2`, primary `192.168.0.5`,
standby `192.168.0.6`.

## 1. Set up the primary

```sh
git clone git@github.com:baughtnet/breakwater.git && cd breakwater
sudo ./deploy/setup-node.sh
sudo docker compose up -d --build
```

The script installs Docker if needed, sets the kernel options a router needs, and writes `.env`
with **DHCP switched off**. Open `http://192.168.0.5:8080`, create the parent account, and check
**System**: both filters should be answering and the blocklists loaded.

At this point nothing on your network has changed.

## 2. Bring over your existing AdGuard Home

```sh
python3 deploy/import-adguard.py http://<old-adguard-address>:<port>
```

This copies your custom rules, DNS rewrites and extra blocklists into Breakwater and prints your named
clients. The old instance is not modified. If it runs on the **same machine** as a Breakwater node,
stop it before step 4; otherwise retire it whenever you like after cutover.

## 3. (Optional) Add the standby

On the second machine:

```sh
git clone git@github.com:baughtnet/breakwater.git && cd breakwater
sudo ./deploy/setup-node.sh --standby        # paste the two secrets from the primary's .env
sudo scp -r <you>@192.168.0.5:breakwater/data/ca/. data/ca/
sudo docker compose up -d --build
```

Then on the **primary**, edit `.env` so the nodes know about each other, and restart:

```
BW_VIP=192.168.0.2
BW_PEERS=http://192.168.0.6:8080
BW_PEER_IPS=192.168.0.6
```

```sh
sudo docker compose up -d
```

**System** should now list both nodes, one "Active" and one "Standing by". Test it: stop the primary
(`sudo docker compose stop`) and the standby should become active within about five seconds.

## 4. Cut over

1. **Router: turn IPv6 off on the LAN side.** Otherwise devices have a second path to the internet
   that never touches Breakwater. The System page warns if it sees one.
2. **Router: turn its DHCP server off.**
3. **Every node:** set `BW_DHCP=1` in `.env`, then `sudo docker compose up -d`. Only the active node
   answers; the others take over if it goes away.
4. **Reconnect a device** (toggle its Wi-Fi). It should appear under **Devices** within seconds.

Now every device is "new": held to the Family filter and routed through Breakwater. Work through
**Devices** and assign each one to a person. Start with the adults' own devices so you are not
filtered yourself, and put printers, TVs and the like under **Household**.

A device picks up a changed gateway the next time it reconnects or renews (within two hours).

## 5. Close the back door

A kid who types the router's address in as their gateway by hand would skip Breakwater. To prevent it:

- In the router's access control (often called "parental controls" or "MAC filtering"), **block
  internet access for each kid device's hardware address**. The address is shown in the device's
  details in the dashboard.

Breakwater forwards kids' traffic under its own address, so the router still lets it through, but a
kid device talking to the router directly gets nothing.

Also set each kid device to use its **real hardware address** on your home Wi-Fi rather than a random
one (Android: Wi-Fi network settings, Privacy, "Use device MAC"). Otherwise it can reappear as a new
device.

## 6. Switch on inspection, device by device

Follow [devices.md](devices.md): install the certificate, then turn on **Inspect web traffic** for
that device.

## Rolling back

Any time something goes wrong:

1. Router: turn its DHCP server back **on**.
2. Every node: `sudo docker compose down` (or just set `BW_DHCP=0` and `up -d`).
3. Reconnect devices. They go back to using the router for everything.

The firewall table Breakwater adds (`inet breakwater`) disappears on reboot, or immediately with
`sudo nft delete table inet breakwater`.

## Day to day

- **Updating:** `git pull && sudo docker compose up -d --build`, one node at a time.
- **Backups:** `data/core/breakwater.db` (settings and history) and `data/ca/` (the certificate; if
  you lose it, every inspected device needs a new one installed). `.env` holds the secrets.
- **Remote access:** reach the dashboard over Tailscale or a VPN. Do not forward port 8080 from the
  internet.
- **Logs:** `sudo docker compose logs -f gateway core`.

## Things I could not test from the build machine

These were verified in the simulated house but not on real hardware; check them during cutover:

- **Docker's own firewall on a real host.** Docker sets the host's forwarding policy to "drop". The
  gateway adds an allow rule to Docker's `DOCKER-USER` chain for LAN traffic. If routed devices get
  DNS but no internet, look there first (`sudo nft list chain ip filter DOCKER-USER`).
- **Your router's behaviour** with DHCP off and with MAC blocking on.
- **Throughput.** The inspecting proxy is Python; on a 2-vCPU VM expect it to keep up with normal
  browsing and video. Bulk video hosts are passed through untouched to keep it that way.

## When OPNsense arrives

OPNsense can take over the gateway role properly: put kids' devices on their own VLAN, policy-route
that VLAN's traffic to Breakwater, force DNS to the virtual IP, and block the VLAN from reaching the
WAN directly. Breakwater keeps DNS filtering, inspection and the dashboard. At that point DHCP can
move to OPNsense (`BW_DHCP=0`), with static mappings handing kids' devices the virtual IP as gateway.
