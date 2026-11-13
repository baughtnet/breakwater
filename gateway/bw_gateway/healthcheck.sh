#!/bin/sh
# keepalived health probe: the agent refreshes this file only while both filters answer and the proxy is up.
f="${BW_RUN_DIR:-/run/breakwater}/healthy"
[ -f "$f" ] && [ $(( $(date +%s) - $(stat -c %Y "$f") )) -lt 10 ]
