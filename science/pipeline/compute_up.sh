#!/bin/bash
# Start whichever parts of the county compute are not running; safe to run any time.
# Parts (model v2, see county_models.py): the corridor job (lidar walls and bridge decks,
# corridor_products.py, until its plan is done), the tile builder (county_daemon.py), the three
# engine workers (Mac :9130 x8, PC :9131 x16, PC :9132 x8), the progress page
# (compute_dashboard.py, port 8765), the 2-hourly public map publisher
# (publish_county_layers.sh → R2) and the watchdog (compute_watchdog.sh, restarts stopped parts).
# After a Mac restart nothing is running: tiles that were mid-run go back to the
# queue (their partial attempts stay; reruns get a new -v<N>) and stale PC engines
# are stopped. Pause flags are kept, so a paused machine stays paused. The NoiseModelling 6.0 engine
# runs from its disk image, which a restart unmounts: it is re-mounted (read-only, checksum-pinned)
# and no worker starts without it (every tile would fail at once).
set -u
cd "$(dirname "$0")/../../../../.."
ROOT=$PWD
P=$ROOT/implementation/apps/quiet-la-web/science/pipeline
Q=$ROOT/implementation/work/pipeline_queue/county_v2
CORRIDOR=$ROOT/implementation/work/source_cache/corridor_v2
C=$ROOT/implementation/work/pipeline_control
SSH=(ssh -F "$HOME/.ssh/quietla_pc_config" -o ConnectTimeout=10 quietla-pc)
mkdir -p "$C" "$Q"/{todo,priority,running,done,failed,logs,retries}
alive() { pgrep -f "$1" > /dev/null; }

if ! alive "queue_worker_persist.sh $Q " && ! alive "run_attempt.py"; then
  for entry in "$Q"/running/*; do
    [ -e "$entry" ] || continue
    tile=$(basename "$entry"); tile=${tile%.*}
    mv "$entry" "$Q/todo/$tile" && echo "requeued $tile"
  done
  rm -f "$C"/held-* "$C"/frozen-*
  for port in 9131 9132; do
    "${SSH[@]}" "powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force }\"" 2>/dev/null
  done
fi
if ! alive "corridor_products.py" && [ "$(python3 -c "import json,pathlib,sys; p=json.load(open('$CORRIDOR/plan.json')); print(sum(not pathlib.Path('$CORRIDOR/blocks/'+k+'/done.json').exists() for k in p['blocks']))")" != 0 ]; then
  nohup nice -n 10 "$P/geo-python-net" -W ignore "$P/../lidar/corridor_products.py" --out "$CORRIDOR" --jobs 2 > "$CORRIDOR/run.out" 2>&1 &
  echo "started corridor job (lidar walls and bridge decks)"
fi
alive "county_daemon.py --model county-v2" || { nohup python3 "$P/county_daemon.py" --model county-v2 --queue "$Q" > /dev/null 2>&1 & echo "started tile builder"; }
ENGINE=/Volumes/NoiseModelling/NoiseModelling.app/Contents/MacOS/NoiseModelling
DMG=$ROOT/implementation/work/NoiseModelling-6.0.0.dmg
if [ ! -x "$ENGINE" ]; then
  if [ "$(shasum -a 256 "$DMG" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$DMG.sha256")" ]; then
    hdiutil attach -nobrowse -readonly -noverify "$DMG" > /dev/null 2>&1 && echo "mounted the NoiseModelling 6.0 disk image"
  else
    echo "ALERT NoiseModelling disk image checksum mismatch: not mounted"
  fi
fi
if [ -x "$ENGINE" ]; then
  for spec in "mac 9130 8" "pc 9131 16" "pc 9132 8"; do
    set -- $spec
    alive "queue_worker_persist.sh $Q $1 $2 " || {
      LABEL_PREFIX=v2 RUN_ARGS="--no-vertical --terrain-downscale 1" nohup /bin/bash "$P/queue_worker_persist.sh" "$Q" "$1" "$2" "$3" > /dev/null 2>&1 &
      echo "started $1 worker :$2 ($3 threads)"; }
  done
else
  echo "ALERT NoiseModelling engine unavailable ($ENGINE): workers not started"
fi
alive "publish_county_layers.sh" || { nohup /bin/bash "$P/publish_county_layers.sh" > /dev/null 2>&1 & echo "started 2-hourly map publishing"; }
alive "compute_dashboard.py" || { nohup python3 "$P/compute_dashboard.py" >> "$C/dashboard.log" 2>&1 & echo "started progress page"; }
# macOS pgrep skips its own ancestors, so when the watchdog runs this script it cannot see itself.
[ -n "${QUIET_LA_WATCHDOG:-}" ] || alive "compute_watchdog.sh" || { nohup /bin/bash "$P/compute_watchdog.sh" > /dev/null 2>&1 & echo "started watchdog"; }
echo "Progress page: http://localhost:8765/ (from the PC or a phone: http://$(ipconfig getifaddr en0 || echo '<mac-ip>'):8765/)"
