# Setting up each device

Every device is filtered as soon as it joins the network. The steps here are for the extras: giving it
an owner, keeping its identity stable, and (optionally) inspection.

## Every device

1. Connect it to the home network. It appears under **Devices** as "New".
2. Open it there, give it a name and choose who it belongs to.
3. For kids' devices, block its hardware address on the router (see deploy.md, step 5).

## Windows PCs (the school computers)

Best coverage of any platform.

1. Give the child a **standard** (non-administrator) account, so network settings and certificates
   can't be changed.
2. Sign in as the administrator, open `http://<breakwater-address>:8080/ca.cer` and install it:
   **Install Certificate, Local Machine, Trusted Root Certification Authorities**.
3. In the dashboard, open the device and switch on **Inspect web traffic**.
4. Chrome and Edge work immediately. For Firefox, set `security.enterprise_roots.enabled` to `true`.
5. Turn off "Use secure DNS" in the browser if a managed policy doesn't already. Breakwater blocks the
   known encrypted-DNS servers anyway; this just avoids slow fallbacks.

Visible with inspection on: every page title and address, searches, YouTube videos.

## Linux PCs

Same as Windows. Install the certificate system-wide (commands are on the **System** page), and
import it into Firefox or Chrome as well, since both keep their own stores. Give the child an account
without `sudo`.

## Android phones and tablets

1. Keep **Family Link** on the device. It covers the times the phone is on cellular or someone else's
   Wi-Fi, where Breakwater cannot see it.
2. In the Wi-Fi network's settings choose **Privacy: Use device MAC**.
3. Optional inspection: open `http://<breakwater-address>:8080/ca.cer`, then
   **Settings, Security, Encryption and credentials, Install a certificate, CA certificate**.
   Switch on **Inspect web traffic** in the dashboard.

Android only lets **browsers** trust a certificate you add. Chrome is inspected; the YouTube app,
TikTok, games and so on are not, and show up by app name and time only. If you want to see what is
watched on YouTube, either rely on the Family Link / YouTube supervised history, or have them use
YouTube in Chrome.

## Chromebooks

Filtering, time limits and pausing all work with no setup. Inspection needs the certificate imported
at `chrome://certificate-manager`; a supervised (Family Link) account may refuse it, in which case
leave inspection off.

## Consoles and TVs

Nothing to install and nothing can be. They get the Family filter, app blocking, pausing, bedtime and
time counting. Activity shows by service (Roblox, Netflix, YouTube) and time.

## Grown-ups' devices

Assign them to an adult profile. By default that means ads, trackers and malware blocked, nothing
recorded, and the router as gateway, so Breakwater is not in the path of their traffic at all. Each
adult can add their own blocked sites or apps under **People**.

## When something breaks on an inspected device

Some apps and sites refuse any certificate but their own (banking, some games, some school tools).
Breakwater notices the refusal and lets that host through untouched from then on; the **System** page
lists them. If a site still misbehaves, add it under **System, Never inspect**.

If *everything* fails on a device right after you switch inspection on, the certificate is not
installed correctly. Switch inspection off, fix it, and try again.
