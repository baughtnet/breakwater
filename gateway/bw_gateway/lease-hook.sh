#!/bin/sh
# Called by dnsmasq on every lease change: <add|old|del> <mac> <ip> [hostname]
# Appends one line to a spool the agent drains: ts event mac ip hostname expires
echo "$(date +%s) $1 $2 $3 ${4:--} ${DNSMASQ_LEASE_EXPIRES:-0}" >> "${BW_RUN_DIR:-/run/breakwater}/lease-events"
