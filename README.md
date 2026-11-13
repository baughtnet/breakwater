# Breakwater

Home internet safety for a family: filtering, monitoring, time limits and a parent dashboard,
self-hosted on one or more small machines on your own network.

A breakwater is the wall that shelters a harbour from open ocean while boats still come and go.

## What it does

- **Blocks** adult content, gambling, dating, malware and filter-bypass tools for kids' devices, and
  forces SafeSearch and YouTube Restricted Mode. Individual apps (TikTok, Roblox, Discord, 130 more) can
  be blocked per person, always or only during homework time.
- **Controls time**: pause a device or a person in one tap, bedtime per weekday, daily limits, and
  "30 more minutes".
- **Monitors**: sites and apps with estimated time in use, data volume, blocked attempts. On devices that
  trust the Breakwater certificate it also shows pages, search terms and YouTube videos.
- **Alerts** on worrying searches (self-harm, explicit, drugs, violence, bypass tools), repeated
  attempts to reach blocked content, and new devices. Optional push through ntfy.
- **Adults too**: a lighter tier (ads, trackers, malware) with monitoring switched off, plus any sites
  or apps an adult chooses to block for themselves.
- **Survives a reboot**: run it on two or more machines and a shared address moves between them in
  seconds.

## How it works

Three layers, because no single one is enough:

| Layer | Component | Job |
|---|---|---|
| DNS filter | two AdGuard Home instances (Family and Basic) | block by category, log every lookup |
| Gateway | Linux routing, nftables, dnsmasq, keepalived | make the filter unavoidable, pause devices, count traffic |
| Inspection | mitmproxy with the Breakwater addon | pages, searches, videos, keyword blocks |

Breakwater hands out addresses (DHCP). Kids' and unknown devices are given Breakwater as their gateway;
adults keep the router. `core/` holds policy and history and serves the dashboard; `gateway/` enforces.

```
core/      policy engine, history, API, dashboard server (Python, FastAPI, SQLite)
gateway/   firewall, DHCP, failover, inspecting proxy (Python, nftables, dnsmasq, keepalived, mitmproxy)
web/       parent dashboard (React, TypeScript)
lab/       a simulated house in Docker, with end-to-end tests
deploy/    node setup and migration scripts
docs/      deployment, device onboarding, limits
```

## Try the dashboard with made-up data

Nothing here touches your network.

```sh
python3 -m venv .venv && .venv/bin/pip install -r core/requirements.txt
(cd web && npm ci && npm run build)
cd core && BW_DEMO=1 BW_ADGUARD=0 BW_DATA=../data/demo BW_HTTP_HOST=127.0.0.1 ../.venv/bin/python -m breakwater.main
```

Open <http://localhost:8080> and sign in as `demo` / `breakwater-demo`.

## Run the simulated house

A fake router, two Breakwater nodes and four fake devices, entirely inside Docker:

```sh
cd lab
docker compose up -d --build
../.venv/bin/pip install pytest httpx
../.venv/bin/python -m pytest -x -q test_lab.py    # about two minutes
```

The dashboards are at <http://localhost:8081> (primary) and <http://localhost:8082> (standby).
The tests create the account `parent` / `lab-parent-password`.

## Deploy for real

See [docs/deploy.md](docs/deploy.md). Then [docs/devices.md](docs/devices.md) for setting up each
device, and [docs/limits.md](docs/limits.md) for what Breakwater cannot do.

## Tests

```sh
cd core && ../.venv/bin/python -m pytest -q       # policy, history, API
cd gateway && ../.venv/bin/python -m pytest -q    # firewall/DHCP rendering, URL parsing
cd web && npm run typecheck
```

## Third-party pieces

Breakwater runs unmodified [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome) (GPL-3.0) and
[mitmproxy](https://mitmproxy.org) (MIT) as separate programs. `core/breakwater/data/services.json` is
a snapshot of AdGuard Home's blockable-services catalogue, used to name and categorise apps. Blocklists
are downloaded at run time from the [HaGeZi](https://github.com/hagezi/dns-blocklists) project.
