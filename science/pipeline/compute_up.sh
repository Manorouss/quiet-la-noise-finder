#!/bin/bash
# Start whichever parts of the county compute are not running; safe to run any time.
# Parts: the tile builder (county_daemon.py), the three engine workers (Mac :9110 x8,
# PC :9111 x16, PC :9112 x8), the progress page (compute_dashboard.py, port 8765) and the
# 2-hourly public map publisher (publish_county_layers.sh → R2).
# After a Mac restart nothing is running: tiles that were mid-run go back to the
# queue (their partial attempts stay; reruns get a new -v<N>) and stale PC engines
# are stopped. Pause flags are kept, so a paused machine stays paused.
set -u
cd "$(dirname "$0")/../../../../.."
ROOT=$PWD
P=$ROOT/implementation/apps/quiet-la-web/science/pipeline
Q=$ROOT/implementation/work/pipeline_queue/county_main
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
  for port in 9111 9112; do
    "${SSH[@]}" "powershell -NoProfile -Command \"Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force }\"" 2>/dev/null
  done
fi
alive "county_daemon.py --queue" || { nohup python3 "$P/county_daemon.py" --queue "$Q" > /dev/null 2>&1 & echo "started tile builder"; }
for spec in "mac 9110 8" "pc 9111 16" "pc 9112 8"; do
  set -- $spec
  alive "queue_worker_persist.sh $Q $1 $2 " || {
    LABEL_PREFIX=nv RUN_ARGS=--no-vertical nohup /bin/bash "$P/queue_worker_persist.sh" "$Q" "$1" "$2" "$3" > /dev/null 2>&1 &
    echo "started $1 worker :$2 ($3 threads)"; }
done
alive "publish_county_layers.sh" || { nohup /bin/bash "$P/publish_county_layers.sh" > /dev/null 2>&1 & echo "started 2-hourly map publishing"; }
alive "compute_dashboard.py" || { nohup python3 "$P/compute_dashboard.py" >> "$C/dashboard.log" 2>&1 & echo "started progress page"; }
echo "Progress page: http://localhost:8765/ (from the PC or a phone: http://$(ipconfig getifaddr en0 || echo '<mac-ip>'):8765/)"
