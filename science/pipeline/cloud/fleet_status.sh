#!/bin/bash
# One-command status of the county compute fleet, about 15 lines. Read only: it changes nothing (no flags, no files,
# no gcloud call but "instances list"). Usage: fleet_status.sh
set -u
PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/google-cloud-sdk/bin"
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../../../.." && pwd)
W=$ROOT/implementation/work
C=$W/pipeline_control
Q=$W/pipeline_queue/county_v3
LOG=$C/watchdog.log
FLEET=$C/cloud_fleet.txt
HOSTS=$C/cloud_hosts.txt
STATE=$C/autoreplace
MARK=auto_replace
TMP=$(mktemp -d "${TMPDIR:-/tmp}/fleet_status.XXXXXX") || exit 1
trap 'rm -rf "$TMP"' EXIT
FMT='value(name,zone,status,machineType.basename(),scheduling.provisioningModel)'

# instance lists of both accounts, fetched at the same time
CONFIGS=$(awk '$1 !~ /^#/ && NF >= 6 { print $2 }' "$FLEET" 2> /dev/null | sort -u)
[ -n "$CONFIGS" ] || CONFIGS="default quietla2"
for cfg in $CONFIGS; do
  ( CLOUDSDK_ACTIVE_CONFIG_NAME=$cfg gcloud compute instances list --filter labels.quietla=county --format "$FMT" > "$TMP/$cfg.tsv" 2> "$TMP/$cfg.err" < /dev/null; echo $? > "$TMP/$cfg.rc" ) &
done
curl -s -m 5 localhost:8765/api/status > "$TMP/status.json" 2> /dev/null &
wait

echo "== fleet status $(date -u +%FT%TZ) =="
# 1. queue and progress (progress page API)
python3 - "$TMP/status.json" "$Q" <<'PY'
import json, os, sys
try:
    d = json.load(open(sys.argv[1]))
    q, s = d["queue"], d["summary"]
    eta = s.get("eta_days")
    print(f"queue: todo {q['todo']}, priority {q['priority']}, running {q['running']}, failed {q['failed']} | cells {s['cells_done']}/{s['cells_total']} | "
          f"rate {s.get('rate_now', 0):.0f} pts/s now ({s['rate']['all']:.0f} measured) | eta {'%.1f d' % eta if eta else 'n/a'} | machines_working {s.get('machines_working')}")
except Exception as e:
    n = lambda k: len(os.listdir(os.path.join(sys.argv[2], k))) if os.path.isdir(os.path.join(sys.argv[2], k)) else -1
    print(f"queue: progress page :8765 does not answer ({type(e).__name__}); by folder: todo {n('todo')}, priority {n('priority')}, running {n('running')}, failed {n('failed')}")
PY

# 2. instances per account, and whether every hosts-file entry runs
short() { sed 's/us-west1-/w1-/g; s/us-central1-/c1-/g; s/us-east1-/e1-/g'; }
for cfg in $CONFIGS; do
  if [ "$(cat "$TMP/$cfg.rc" 2> /dev/null)" != 0 ]; then echo "account $cfg: LIST FAILED: $(head -c 150 "$TMP/$cfg.err" | tr '\n' ' ')"; continue; fi
  cpus=$(awk -F'\t' '$3 != "TERMINATED" && $3 != "STOPPED" { t = $4; sub(/.*-/, "", t); s += t + 0 } END { print s + 0 }' "$TMP/$cfg.tsv")
  echo "account $cfg ($cpus/96 vCPU): $(sort "$TMP/$cfg.tsv" | awk -F'\t' '{ s = ($3 == "RUNNING") ? "up" : tolower($3); p = ($5 == "STANDARD") ? "*" : ""; printf "%s%s:%s:%s ", $1, p, $2, s }' | short)"
done
cat "$TMP"/*.tsv > "$TMP/all.tsv" 2> /dev/null
notrunning=$(awk -v tsv="$TMP/all.tsv" 'BEGIN { while ((getline l < tsv) > 0) { split(l, f, "\t"); if (f[3] == "RUNNING") up[f[1]] = 1 } }
                $1 !~ /^#/ && NF >= 3 { total++; if (!($1 in up)) bad = bad " " $1 } END { printf "%d/%d running%s", total - split(bad, x, " "), total, bad ? "; NOT RUNNING:" bad : "" }' "$HOSTS" 2> /dev/null)
echo "cloud_hosts.txt entries: $notrunning   (* = on demand)"

# 3. pause flags
flags=""
for f in "$C"/pause-*; do
  [ -e "$f" ] || continue
  who=owner; [ "$(head -1 "$f" 2> /dev/null)" = "$MARK" ] && who=AUTO
  flags="$flags $(basename "$f")($who)"
done
echo "pause flags:${flags:- none}"

# 4. workers: exactly one per host and port
ps -axo command= | awk -v q="$Q" '$1 == "/bin/bash" && $2 ~ /queue_worker_persist\.sh$/ && $3 == q { n[$4 ":" $5]++ } END { for (k in n) print k, n[k] }' | sort > "$TMP/workers.txt"
expected=$( { printf 'mac:9133\npc:9134\npc:9135\n'; awk '$1 !~ /^#/ && NF >= 3 { print $1 ":" $3 }' "$HOSTS" 2> /dev/null; } )
problems=""
for e in $expected; do
  n=$(awk -v k="$e" '$1 == k { print $2 }' "$TMP/workers.txt"); n=${n:-0}
  [ "$n" = 1 ] || problems="$problems $e x$n"
done
echo "workers: $(awk '{ s += $2 } END { print s + 0 }' "$TMP/workers.txt") running for $(echo "$expected" | wc -l | tr -d ' ') hosts; $([ -n "$problems" ] && echo "PROBLEM (host:port xcount):$problems" || echo "exactly one per host and port")"

# 5. watchdog and auto replacement
wd=$(pgrep -f compute_watchdog.sh | tr '\n' ' ')
stops=""; for s in STOP-watchdog STOP-autoreplace; do [ -e "$C/$s" ] && stops="$stops $s"; done
lock="idle"; [ -d "$STATE/lock" ] && lock="RUNNING now (pid $(cat "$STATE/lock/pid" 2> /dev/null))"
rep24=$( [ -f "$STATE/history" ] && awk -v t="$(( $(date +%s) - 86400 ))" '$1 >= t { n[$3]++ } END { for (c in n) printf "%s %d, ", c, n[c] }' "$STATE/history" )
back=""; for b in "$STATE"/backoff-*; do [ -e "$b" ] || continue; left=$(( $(cat "$b") - $(date +%s) )); [ "$left" -gt 0 ] && back="$back $(basename "$b" | sed 's/backoff-//') backed off $((left / 60)) min,"; done
pend=""; for p in "$STATE"/pending-*; do [ -e "$p" ] || continue; pend="$pend $(basename "$p" | sed 's/pending-//')"; done
echo "watchdog pid(s): ${wd:-NONE}$([ "$(echo $wd | wc -w)" -gt 1 ] && echo ' (MORE THAN ONE)')${stops:+; stop flags:$stops} | auto_replace: $lock; replacements in 24 h: ${rep24:-none}${back:+ $back}${pend:+ being replaced:$pend}"

# 6. ALERT / REPLACED lines of the last 3 h (a tile "running for N min" alert repeats every loop: shown once with a count)
cut=$(date -u -v-3H +%FT%TZ)
awk -v cut="$cut" '$1 >= cut && /ALERT|REPLAC|RESTARTED|stale lock|stopped .* duplicate/ {
    if ($0 ~ /has been running for/) { k = $3; c[k]++; last[k] = $0; if (!(k in seen)) { seen[k] = 1; keys[++nk] = k } next }
    out[++m] = $0 }
  END { for (i = 1; i <= nk; i++) printf "%s (%d alerts)\n", last[keys[i]], c[keys[i]]; for (j = 1; j <= m; j++) print out[j] }' "$LOG" 2> /dev/null | tail -8 > "$TMP/events.txt"
if [ -s "$TMP/events.txt" ]; then echo "ALERT/REPLACED/RESTARTED, last 3 h:"; sed 's/^/  /' "$TMP/events.txt"; else echo "ALERT/REPLACED/RESTARTED, last 3 h: none"; fi

# 7. other things listening on the engine port of the Mac worker (not our concern, only counted)
echo "processes matching 'port 9133': $(pgrep -fl 'port 9133' 2> /dev/null | wc -l | tr -d ' ')"
