#!/bin/bash
# Claim tiles from a shared queue directory and run them on one host, forever.
#
#   queue_worker_persist.sh <queue dir> <mac|pc> <port> <threads> [wait-file]
#
# Same queue layout and environment as queue_worker.sh (LABEL_PREFIX, RUN_ARGS), plus:
#  - <queue>/priority/ is claimed before todo/; its files sort in claim order and a
#    leading "NN-" is dropped from the tile name;
#  - no new tile starts while implementation/work/pipeline_control/pause-<host> exists
#    (run_attempt.py holds a running tile at the same flag and compute_dashboard.py
#    freezes its engine, so pausing loses no work);
#  - a failed tile is retried once (run_attempt.py stages a new -v<N> attempt, so
#    nothing is overwritten);
#  - done/<tile> records the source attempt and the completed run attempt;
#  - on the Mac the engine runs at nice 10 so interactive work stays responsive;
#  - a rented Linux host (any <host> but mac/pc, see run_attempt.py) is checked over SSH before a tile is
#    claimed, and a tile whose host stopped answering (Spot machine reclaimed, exit 75) goes back to the
#    queue instead of counting as a failure.
# Sleeps while the queue is empty and exits only when <queue>/STOP exists.
set -u
QUEUE=$1; HOST=$2; PORT=$3; THREADS=$4; WAIT=${5:-}
LABEL_PREFIX=${LABEL_PREFIX:-hf583}; RUN_ARGS=${RUN_ARGS:-}
HERE=$(cd "$(dirname "$0")" && pwd)
CONTROL=$(cd "$HERE/../../../.." && pwd)/work/pipeline_control
NICE=0; [ "$HOST" = mac ] && NICE=10
CLOUD=0; [ "$HOST" != mac ] && [ "$HOST" != pc ] && CLOUD=1
host_up() { ssh -F "$HOME/.ssh/quietla_cloud_config" -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true < /dev/null > /dev/null 2>&1; }
mkdir -p "$QUEUE"/{todo,priority,running,done,failed,logs,retries} "$CONTROL"
if [ -n "$WAIT" ]; then
  until grep -q "BENCH DONE" "$WAIT" 2>/dev/null; do sleep 30; done
fi
while true; do
  if [ -f "$CONTROL/pause-$HOST" ]; then sleep 10; continue; fi
  if [ "$CLOUD" = 1 ] && ! host_up; then sleep 60; continue; fi
  dir=priority; next=$(ls "$QUEUE/priority" 2>/dev/null | sort | head -1)
  if [ -z "$next" ]; then dir=todo; next=$(ls "$QUEUE/todo" 2>/dev/null | sort | head -1); fi
  if [ -z "$next" ]; then [ -f "$QUEUE/STOP" ] && break; sleep 60; continue; fi
  tile=${next#[0-9][0-9]-}
  mv "$QUEUE/$dir/$next" "$QUEUE/running/$tile.$HOST" 2>/dev/null || continue
  source_attempt=$(head -1 "$QUEUE/running/$tile.$HOST")
  log="$QUEUE/logs/$tile.$HOST.log"
  tag=""; [ "$dir" = priority ] && tag=" (priority)"
  echo "$(date -u +%FT%TZ) start $tile on $HOST$tag" >> "$QUEUE/worker-$HOST.log"
  start=$(date +%s)
  caffeinate -i nice -n "$NICE" python3 "$HERE/run_attempt.py" --source-attempt "$source_attempt" \
      --label "$LABEL_PREFIX-$HOST$THREADS" --host "$HOST" --port "$PORT" --threads "$THREADS" $RUN_ARGS > "$log" 2>&1
  rc=$?
  if [ "$rc" = 0 ]; then
    printf '%s\n%s\n' "$source_attempt" "$(tail -1 "$log")" > "$QUEUE/done/$tile"
    rm -f "$QUEUE/running/$tile.$HOST"
    echo "$(date -u +%FT%TZ) done $tile on $HOST in $(( $(date +%s) - start ))s -> $(tail -1 "$log")" >> "$QUEUE/worker-$HOST.log"
  elif [ "$CLOUD" = 1 ] && { [ "$rc" = 75 ] || ! host_up; }; then
    mv "$QUEUE/running/$tile.$HOST" "$QUEUE/$dir/$next"
    echo "$(date -u +%FT%TZ) HOST GONE: $tile re-queued ($HOST stopped answering after $(( $(date +%s) - start ))s)" >> "$QUEUE/worker-$HOST.log"
    sleep 60
  elif [ "$(cat "$QUEUE/retries/$tile" 2>/dev/null || echo 0)" -lt 1 ]; then
    echo 1 > "$QUEUE/retries/$tile"
    cp "$log" "$log.try1"
    mv "$QUEUE/running/$tile.$HOST" "$QUEUE/$dir/$next"
    echo "$(date -u +%FT%TZ) RETRY $tile after failure on $HOST after $(( $(date +%s) - start ))s (see $log.try1)" >> "$QUEUE/worker-$HOST.log"
  else
    mv "$QUEUE/running/$tile.$HOST" "$QUEUE/failed/$tile"
    echo "$(date -u +%FT%TZ) FAILED $tile on $HOST after $(( $(date +%s) - start ))s (see $log)" >> "$QUEUE/worker-$HOST.log"
  fi
done
echo "$(date -u +%FT%TZ) worker $HOST idle: queue empty" >> "$QUEUE/worker-$HOST.log"
