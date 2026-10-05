#!/bin/bash
# Keep the county compute running unattended: every INTERVAL seconds (default 600) run compute_up.sh,
# which starts any stopped part and never removes a pause flag, and log problems to
# implementation/work/pipeline_control/watchdog.log: restarts, new failed tiles, tiles running for
# more than 2 h, corridor or publish failures, the PC not answering, pause flags appearing or going.
# Stops when implementation/work/pipeline_control/STOP-watchdog exists.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../../.." && pwd)
W=$ROOT/implementation/work
C=$W/pipeline_control
Q=$W/pipeline_queue/county_v2
LOG=$C/watchdog.log
INTERVAL=${INTERVAL:-600}
export QUIET_LA_WATCHDOG=1  # tells compute_up.sh not to start another watchdog
SSH=(ssh -F "$HOME/.ssh/quietla_pc_config" -o ConnectTimeout=15 -o BatchMode=yes quietla-pc)
note() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }
count() { local n; n=$(grep -c "$1" "$2" 2>/dev/null); echo "${n:-0}"; }

last_failed=$(ls "$Q/failed" 2>/dev/null | wc -l | tr -d ' ')
last_corridor=$(count FAILED "$W/source_cache/corridor_v2/corridor.log")
last_publish=$(count FAILED "$C/publish.log")
last_pauses=$(ls "$C"/pause-* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')
pc_down=0
note "watchdog start (every ${INTERVAL}s)"
while [ ! -f "$C/STOP-watchdog" ]; do
  started=$("$HERE/compute_up.sh" 2>&1 | grep -v "^Progress page")
  [ -n "$started" ] && note "RESTARTED: $(echo "$started" | tr '\n' ';')"
  failed=$(ls "$Q/failed" 2>/dev/null | wc -l | tr -d ' ')
  [ "$failed" -gt "$last_failed" ] && note "ALERT failed tiles: $failed (new: $(ls -t "$Q/failed" | head -$((failed - last_failed)) | tr '\n' ' '))"
  last_failed=$failed
  now=$(date +%s)
  for entry in "$Q"/running/*; do
    [ -e "$entry" ] || continue
    age=$(( (now - $(stat -f %m "$entry")) / 60 ))
    [ "$age" -gt 120 ] && note "ALERT $(basename "$entry") has been running for $age min"
  done
  corridor=$(count FAILED "$W/source_cache/corridor_v2/corridor.log")
  [ "$corridor" -gt "$last_corridor" ] && note "ALERT corridor block failure: $(grep FAILED "$W/source_cache/corridor_v2/corridor.log" | tail -1 | cut -c1-200)"
  last_corridor=$corridor
  publish=$(count FAILED "$C/publish.log")
  [ "$publish" -gt "$last_publish" ] && note "ALERT publish failure: $(grep FAILED "$C/publish.log" | tail -1 | cut -c1-200)"
  last_publish=$publish
  if "${SSH[@]}" "echo ok" < /dev/null > /dev/null 2>&1; then
    [ "$pc_down" = 1 ] && note "PC answers again"
    pc_down=0
  else
    [ "$pc_down" = 0 ] && note "ALERT PC does not answer over SSH"
    pc_down=1
  fi
  pauses=$(ls "$C"/pause-* 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')
  [ "$pauses" != "$last_pauses" ] && note "pause flags now: [${pauses}] (left as the owner set them)"
  last_pauses=$pauses
  sleep "$INTERVAL"
done
note "watchdog stopped (STOP-watchdog)"
