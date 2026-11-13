# What Breakwater does not do

Better to know these before relying on it.

## It only works at home

A phone on cellular data, a laptop at a friend's house or on a phone hotspot is outside Breakwater
entirely. Keep Family Link on Android devices as the layer that travels. A neighbour's open Wi-Fi or
a kid's own hotspot is a bypass no home system can prevent.

## Not everything can be seen

| Device or app | What you see |
|---|---|
| Windows or Linux PC, browser, certificate installed | pages, searches, videos, sites, time |
| Android, Chrome, certificate installed | pages, searches, videos, sites, time |
| Android apps (YouTube app, TikTok, games) | app or site name and time only |
| Consoles, TVs | service name and time only |
| Apps that pin their certificates | site name and time only |
| Anything end-to-end encrypted (message contents) | never |

Breakwater does not read messages, does not take screenshots and does not record keystrokes.

## Screen time is an estimate

Time "in use" is counted from traffic: a minute with real data flowing, attributed to the sites being
looked up around then. It is good at "about two hours, mostly YouTube" and not exact to the minute.

- A large download right after browsing can count as use.
- Offline play (a downloaded game or video) is invisible.
- Two devices used at once both count toward a daily limit.
- Devices that keep the router as gateway (adults, by default) are estimated from lookups only.

## Filters miss things and over-block things

Blocklists are long but never complete, and new sites appear daily. SafeSearch and YouTube Restricted
Mode are run by Google, Microsoft and others, not by Breakwater. Expect occasional surprises in both
directions; use **Always allow** and **Always block** under People to correct them.

## A determined teenager

Closed here: changing DNS servers, DNS-over-TLS, the well-known DNS-over-HTTPS services, QUIC, common
VPN ports, VPN and proxy sites, setting the router as gateway (once you block their address on the
router).

Still possible: a VPN on an unusual port to a server nobody has listed, a personal hotspot, borrowing
an unfiltered device, or copying an adult device's hardware address. Breakwater raises an alert when
it sees searches about bypassing and attempts to reach bypass sites, which is usually the earliest
sign.

## If every node is down

Kids' and unknown devices have no internet. That is deliberate (fail closed). Adults who already
have an address keep working until their lease renews. To restore service without Breakwater, turn
the router's DHCP back on.

## Privacy between adults

With monitoring **Off**, a profile's lookups are excluded from the filter's own log and nothing is
stored, not even counts. The dashboard still shows whether the device is currently on the network.
Whoever holds the parent password can change that setting, so the adults in the house should agree
on it.

## Standby nodes

Settings can only be changed on the primary. A standby can pause, resume and grant extra time while
the primary is down, and those changes merge back afterwards. A node added later starts with about
two weeks of history.
