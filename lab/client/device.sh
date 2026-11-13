#!/bin/sh
# Behave like a real device joining Wi-Fi: drop Docker's address and ask the LAN's DHCP server for one.
cat > /udhcpc.sh <<'HOOK'
#!/bin/sh
/usr/share/udhcpc/default.script "$@"
# Docker bind-mounts resolv.conf, so the stock script can't replace it; write the offered DNS in place.
case "$1" in bound|renew) : > /etc/resolv.conf; for s in $dns; do echo "nameserver $s" >> /etc/resolv.conf; done ;; esac
HOOK
chmod +x /udhcpc.sh
ip addr flush dev eth0
exec udhcpc -i eth0 -f -t 0 -T 3 -s /udhcpc.sh -x "hostname:$(hostname)"
