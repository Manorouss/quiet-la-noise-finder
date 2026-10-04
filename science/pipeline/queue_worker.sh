#!/bin/bash
# Claim tiles from a shared queue directory and run them on one host.
#
#   queue_worker.sh <queue dir> <mac|pc> <port> <threads> [wait-file]
#
# The queue holds one file per tile in todo/, whose content is the staged
# source attempt path. A worker claims a tile by an atomic mv into running/,
# then moves it to done/ or failed/. An optional wait-file delays the start
# until that file contains "BENCH DONE" (used to avoid competing for cores).
set -u
QUEUE=$1; HOST=$2; PORT=$3; THREADS=$4; WAIT=${5:-}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$QUEUE"/{todo,running,done,failed,logs}
if [ -n "$WAIT" ]; then
  until grep -q "BENCH DONE" "$WAIT" 2>/dev/null; do sleep 30; done
fi
while true; do
  next=$(ls "$QUEUE/todo" 2>/dev/null | sort | head -1)
  [ -z "$next" ] && break
  mv "$QUEUE/todo/$next" "$QUEUE/running/$next.$HOST" 2>/dev/null || continue
  source_attempt=$(cat "$QUEUE/running/$next.$HOST")
  log="$QUEUE/logs/$next.$HOST.log"
  echo "$(date -u +%FT%TZ) start $next on $HOST" >> "$QUEUE/worker-$HOST.log"
  start=$(date +%s)
  if caffeinate -i python3 "$HERE/run_attempt.py" --source-attempt "$source_attempt" \
      --label "hf583-$HOST$THREADS" --host "$HOST" --port "$PORT" --threads "$THREADS" > "$log" 2>&1; then
    mv "$QUEUE/running/$next.$HOST" "$QUEUE/done/$next"
    echo "$(date -u +%FT%TZ) done $next on $HOST in $(( $(date +%s) - start ))s -> $(tail -1 "$log")" >> "$QUEUE/worker-$HOST.log"
  else
    mv "$QUEUE/running/$next.$HOST" "$QUEUE/failed/$next"
    echo "$(date -u +%FT%TZ) FAILED $next on $HOST after $(( $(date +%s) - start ))s (see $log)" >> "$QUEUE/worker-$HOST.log"
  fi
done
echo "$(date -u +%FT%TZ) worker $HOST idle: queue empty" >> "$QUEUE/worker-$HOST.log"
