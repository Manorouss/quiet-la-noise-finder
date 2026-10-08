#!/bin/bash
# Replace reclaimed Google Cloud Spot machines of the county queue, one for one, with nobody watching.
# Called by compute_watchdog.sh every loop (in the background, it can take ~10 min); safe to run by hand any time.
#
#   auto_replace.sh                       one pass: replace what is missing, then exit
#   DRY_RUN=1 auto_replace.sh             print what it would do; creates, pauses, edits and logs nothing
#   DRY_RUN=1 AUTOREPLACE_PRETEND_MISSING=gk,gb auto_replace.sh     (test) treat these hosts as gone
#
# The fleet is pipeline_control/cloud_fleet.txt (name, gcloud configuration, machine type, spot|ondemand, region,
# zones). A host is replaced when it is a spot host of the fleet AND a line of cloud_hosts.txt (gcp_delete.sh drops
# that line, so a machine deleted on purpose is not brought back) AND its VM is missing or TERMINATED/STOPPED.
# Replacing = pause-<name> (content "auto_replace"), gcp_create.sh in the same region (other zones first, the zone it
# was lost in last, next zone on a stock-out), provision_host.sh (3 attempts), place label in cloud_hosts.txt,
# exactly one worker on the host's port, then the own pause flag is removed so the worker resumes. The old worker
# normally survives a reclaim (it logs HOST GONE and waits for the host), so no new one is needed.
#
# Rails: nothing happens while pipeline_control/STOP-autoreplace exists or when the queue is finished (todo,
# priority, running all empty); an owner's pause flag (any content but "auto_replace") is never touched and keeps
# its host from being replaced; never more than 96 vCPU per account, Spot vCPUs per region 64 (default) / 32
# (quietla2); the machine type and region come from the fleet file; at most 3 replacements per host and 15 per
# account in 24 h (then ALERT, no more for that account); a non-stock-out failure (quota, billing, permission, ...)
# backs the account off for 6 h (30 min for unknown errors); one run at a time (lockdir). ga (on demand) is never
# replaced, a missing ga only raises an ALERT. Events go to pipeline_control/watchdog.log; state in pipeline_control/autoreplace/.
set -u
PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/google-cloud-sdk/bin"
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../../../.." && pwd)
W=$ROOT/implementation/work
C=$W/pipeline_control
Q=$W/pipeline_queue/county_v3
P=$ROOT/implementation/apps/quiet-la-web/science/pipeline   # the production checkout runs the workers
LOG=$C/watchdog.log
FLEET=$C/cloud_fleet.txt
HOSTS=$C/cloud_hosts.txt
STATE=$C/autoreplace
LOCK=$STATE/lock
MARK=auto_replace
ENGINE_LIB=/Volumes/NoiseModelling/NoiseModelling.app/Contents/app/lib
ACCOUNT_CAP=96
PER_HOST_24H=3
PER_ACCOUNT_24H=15
HARD_BACKOFF=21600
SOFT_BACKOFF=1800
DRY=0; [ "${DRY_RUN:-0}" = 1 ] && DRY=1
PRETEND=""; [ "$DRY" = 1 ] && PRETEND=$(echo "${AUTOREPLACE_PRETEND_MISSING:-}" | tr ' ' ',')

now() { date +%s; }
note() {
  if [ "$DRY" = 1 ]; then echo "DRY_RUN log line: $*"; else echo "$(date -u +%FT%TZ) $*" >> "$LOG"; fi
}
say() { if [ "$DRY" = 1 ]; then echo "DRY_RUN: $*"; fi; return 0; }
# alert_once <key> <seconds> <message>: an ALERT line at most once per <seconds> for <key> (no repeating noise)
alert_once() {
  if [ "$DRY" = 1 ]; then note "ALERT $3"; return 0; fi
  local f="$STATE/alerted-$1" last=0
  [ -f "$f" ] && last=$(cat "$f" 2> /dev/null)
  case "$last" in ''|*[!0-9]*) last=0;; esac
  [ $(( $(now) - last )) -ge "$2" ] || return 0
  now > "$f"
  note "ALERT $3"
}
region_of() { echo "${1%-*}"; }
place_of() { case "$1" in us-west1) echo Oregon;; us-central1) echo Iowa;; us-east1) echo "South Carolina";; *) echo "$1";; esac; }
spot_region_cap() { case "$1" in quietla2) echo 32;; *) echo 64;; esac; }
cpus_of_type() { local t=${1##*-}; case "$t" in ''|*[!0-9]*) echo 999;; *) echo "$t";; esac; }
field() { echo "$1" | cut -f"$2"; }

# run_limited <seconds> <stdout file> <stderr file> command...: like timeout(1), which macOS does not have
run_limited() {
  local secs=$1 out=$2 err=$3 pid n=0
  shift 3
  "$@" > "$out" 2> "$err" < /dev/null &
  pid=$!
  while kill -0 "$pid" 2> /dev/null; do
    if [ "$n" -ge $(( secs * 2 )) ]; then
      kill "$pid" 2> /dev/null; sleep 1; kill -9 "$pid" 2> /dev/null; wait "$pid" 2> /dev/null
      return 124
    fi
    sleep 0.5; n=$((n + 1))
  done
  wait "$pid"
}

# ---------------------------------------------------------------- preconditions
if [ -f "$C/STOP-autoreplace" ]; then say "STOP-autoreplace exists: nothing to do"; exit 0; fi
queue_empty() {
  local d
  for d in todo priority running; do [ -n "$(ls "$Q/$d" 2> /dev/null)" ] && return 1; done
  return 0
}
if queue_empty; then say "queue finished (todo, priority and running are empty): the fleet is deleted by hand, not replaced"; exit 0; fi
[ -f "$FLEET" ] || { say "no fleet file $FLEET"; exit 0; }

# ---------------------------------------------------------------- lock (one run at a time)
TMP=""
release_lock() {
  [ -n "$TMP" ] && rm -rf "$TMP"
  if [ "$DRY" = 0 ] && [ "$(cat "$LOCK/pid" 2> /dev/null)" = "$$" ]; then rm -rf "$LOCK"; fi
}
acquire_lock() {
  mkdir -p "$STATE/logs"
  if mkdir "$LOCK" 2> /dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  local pid; pid=$(cat "$LOCK/pid" 2> /dev/null)
  if [ -n "$pid" ]; then
    if kill -0 "$pid" 2> /dev/null && ps -p "$pid" -o command= 2> /dev/null | grep -q auto_replace; then
      if [ $(( $(now) - $(stat -f %m "$LOCK") )) -gt 10800 ]; then alert_once lockage 21600 "auto_replace.sh (pid $pid) has been running for over 3 h; not starting a second one"; fi
      return 1
    fi
  else
    # a lock that has no pid yet is being created right now (or the writer died at that instant)
    [ $(( $(now) - $(stat -f %m "$LOCK" 2> /dev/null || now) )) -lt 120 ] && return 1
  fi
  # stale: take it over by renaming (atomic, so only one process wins), then look at what we moved
  mv "$LOCK" "$LOCK.stale.$$" 2> /dev/null || return 1
  if [ "$(cat "$LOCK.stale.$$/pid" 2> /dev/null)" != "$pid" ]; then mv "$LOCK.stale.$$" "$LOCK" 2> /dev/null; return 1; fi
  rm -rf "$LOCK.stale.$$"
  mkdir "$LOCK" 2> /dev/null || return 1
  echo $$ > "$LOCK/pid"
  note "auto_replace.sh: removed a stale lock (pid ${pid:-unknown} is not a running auto_replace.sh)"
  return 0
}
TMP=$(mktemp -d "${TMPDIR:-/tmp}/auto_replace.XXXXXX") || exit 1
trap release_lock EXIT
trap 'exit 1' INT TERM HUP
if [ "$DRY" = 0 ]; then
  acquire_lock || exit 0
  find "$STATE/logs" -type f -mtime +7 -delete 2> /dev/null   # create/provision logs of old replacements
fi

command -v gcloud > /dev/null || { alert_once nogcloud 21600 "auto_replace: gcloud not found in PATH, cannot look for reclaimed machines"; exit 0; }

# ---------------------------------------------------------------- fleet and account helpers
FLEET_ROWS=()
while read -r a b c d e f rest; do
  case "$a" in ""|\#*) continue;; esac
  [ -n "${f:-}" ] || continue
  FLEET_ROWS+=("$a $b $c $d $e $f")
done < "$FLEET"
CONFIGS=$(for r in ${FLEET_ROWS[@]+"${FLEET_ROWS[@]}"}; do set -- $r; echo "$2"; done | sort -u)

in_hosts_file() { awk -v n="$1" '$1 == n { found = 1 } END { exit !found }' "$HOSTS" 2> /dev/null; }
host_threads() { awk -v n="$1" '$1 == n { print $2 }' "$HOSTS"; }
host_port() { awk -v n="$1" '$1 == n { print $3 }' "$HOSTS"; }
host_label() { awk -v n="$1" '$1 == n { $1 = $2 = $3 = ""; sub(/^ +/, ""); print }' "$HOSTS"; }

# list_account <cfg>: instances with label quietla=county as tab separated name, zone, status, type, model
list_account() {
  local cfg=$1 raw="$TMP/raw-$1.tsv" err="$TMP/raw-$1.err"
  rm -f "$TMP/ok-$cfg"
  run_limited 120 "$raw" "$err" env CLOUDSDK_ACTIVE_CONFIG_NAME="$cfg" gcloud compute instances list \
    --filter labels.quietla=county --format 'value(name,zone,status,machineType.basename(),scheduling.provisioningModel)' || return 1
  awk -F'\t' 'NF >= 4 { n = split($2, z, "/"); m = split($4, t, "/"); print $1 "\t" z[n] "\t" $3 "\t" t[m] "\t" $5 }' "$raw" > "$TMP/inst-$cfg.tsv"
  # live = counts against quotas: not stopped, and not a host the dry run pretends is gone
  awk -F'\t' -v skip=",$PRETEND," '$3 != "TERMINATED" && $3 != "STOPPED" && $3 != "SUSPENDED" && !index(skip, "," $1 ",")' "$TMP/inst-$cfg.tsv" > "$TMP/live-$cfg.tsv"
  : > "$TMP/ok-$cfg"
}
# inst_row_raw <cfg> <name>: the VM of that name as a row (a running one wins over a stopped one), or nothing
inst_row_raw() {
  awk -F'\t' -v n="$2" '$1 == n {
      if ($3 == "RUNNING" || $3 == "PROVISIONING" || $3 == "STAGING" || $3 == "STOPPING" || $3 == "REPAIRING") { print; found = 1; exit }
      else if (dead == "") dead = $0 }
    END { if (!found && dead != "") print dead }' "$TMP/inst-$1.tsv"
}
inst_row() {
  case ",$PRETEND," in *",$2,"*) return 0;; esac
  inst_row_raw "$1" "$2"
}
acct_cpus() { awk -F'\t' '{ t = $4; sub(/.*-/, "", t); s += t + 0 } END { print s + 0 }' "$TMP/live-$1.tsv"; }
region_spot_cpus() {
  awk -F'\t' -v r="$2" '$5 != "STANDARD" { z = $2; sub(/-[a-z]$/, "", z); if (z == r) { t = $4; sub(/.*-/, "", t); s += t + 0 } } END { print s + 0 }' "$TMP/live-$1.tsv"
}
zone_load() { awk -F'\t' -v z="$2" '$2 == z { c++ } END { print c + 0 }' "$TMP/live-$1.tsv"; }
# zone_order <cfg> <zones, comma separated> <zone it was lost in>: least loaded zone of the account first, the zone it was lost in last
zone_order() {
  local z idx=0 key
  for z in $(echo "$2" | tr ',' ' '); do
    idx=$((idx + 1)); key=$(zone_load "$1" "$z")
    [ "$z" = "$3" ] && key=$((key + 100))
    echo "$key $idx $z"
  done | sort -n -k1,1 -k2,2 | awk '{ print $3 }'
}
count_recent() { # <history column: 2 name | 3 account> <value>
  [ -f "$STATE/history" ] || { echo 0; return; }
  awk -v t="$(( $(now) - 86400 ))" -v f="$1" -v v="$2" '$1 >= t && $f == v { c++ } END { print c + 0 }' "$STATE/history"
}
backoff_left() { # seconds left of the account's back-off (0 = none)
  local until; until=$(cat "$STATE/backoff-$1" 2> /dev/null)
  case "$until" in ''|*[!0-9]*) echo 0; return;; esac
  [ "$until" -gt "$(now)" ] && echo $(( until - $(now) )) || echo 0
}
set_backoff() { # <cfg> <seconds>
  [ "$DRY" = 1 ] && return 0
  echo $(( $(now) + $2 )) > "$STATE/backoff-$1"
}

# pause flags: the owner's carry a timestamp (or nothing); ours carry exactly the marker line
flag_exists() { [ -e "$C/pause-$1" ]; }
flag_ours() { [ -f "$C/pause-$1" ] && [ "$(head -1 "$C/pause-$1" 2> /dev/null)" = "$MARK" ]; }
flag_make() { # true when pause-<name> exists afterwards and is ours; never overwrites another flag
  flag_ours "$1" && return 0
  flag_exists "$1" && return 1
  ( set -C; printf '%s\n' "$MARK" > "$C/pause-$1" ) 2> /dev/null
  flag_ours "$1"
}
flag_remove() { flag_ours "$1" && rm -f "$C/pause-$1"; return 0; }

# ---------------------------------------------------------------- worker, label, tunnel
worker_pids() { pgrep -f "queue_worker_persist.sh $Q $1 $2 " 2> /dev/null; }
# ensure_one_worker <name> <threads> <port>: start one when there is none; extra idle ones (no tile running) are stopped
ensure_one_worker() {
  local name=$1 threads=$2 port=$3 pids n busy="" idle="" p
  pids=$(worker_pids "$name" "$port"); n=$(echo "$pids" | grep -c .)
  if [ "$n" = 0 ]; then
    LABEL_PREFIX=v3 RUN_ARGS="--no-vertical --terrain-downscale 1 --max-error-db 0.1 --atmo" nohup /bin/bash "$P/queue_worker_persist.sh" "$Q" "$name" "$port" "$threads" > /dev/null 2>&1 &
    disown 2> /dev/null
    sleep 1
    note "started $name worker :$port ($threads threads)"
    return 0
  fi
  [ "$n" = 1 ] && return 0
  for p in $pids; do
    if pgrep -P "$p" caffeinate > /dev/null 2>&1; then busy="$busy $p"; else idle="$idle $p"; fi
  done
  if [ -z "$busy" ]; then set -- $idle; shift; idle="$*"; fi   # all idle: keep the first
  if [ $(echo $busy | wc -w) -gt 1 ]; then alert_once "dupworker-$name" 3600 "$name has $n workers on port $port and several are running tiles: stop the extra ones by hand"; return 0; fi
  for p in $idle; do kill "$p" 2> /dev/null; done
  note "stopped $(echo $idle | wc -w | tr -d ' ') duplicate idle worker(s) of $name (port $port)"
}
fix_place_label() { # <name> <region>
  local name=$1 place; place=$(place_of "$2")
  [ "$(host_label "$name" | sed 's/.*· //')" = "$place" ] && return 0
  python3 - "$HOSTS" "$name" "$place" <<'PY'
import os, sys, tempfile
path, name, place = sys.argv[1:4]
lines = open(path).read().split("\n")
out = []
for line in lines:
    parts = line.split()
    if parts and parts[0] == name and "·" in line:
        line = line.rsplit("·", 1)[0] + "· " + place
    out.append(line)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as f:
    f.write("\n".join(out))
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
  note "cloud_hosts.txt: $name place label is now $place"
}
drop_stale_tunnel() { # <name> <port>: an engine tunnel left over from the lost machine would block the new one (ExitOnForwardFailure)
  pgrep -f -- "--host $1 --port $2 " > /dev/null 2>&1 && return 0   # a tile is running on it: leave it
  local p
  for p in $(pgrep -f -- "-L $2:127.0.0.1:$2 $1 " 2> /dev/null); do kill "$p" 2> /dev/null && note "stopped a stale engine tunnel of $1 (pid $p)"; done
  return 0
}
host_ready() { # provisioned the way queue_worker_persist.sh host_up expects it, plus the engine jars
  ssh -F "$HOME/.ssh/quietla_cloud_config" -o ConnectTimeout=10 -o BatchMode=yes "$1" 'test -d /opt/quietla/engine_scripts_v3/scripts && test -w /opt/quietla && test -n "$(ls /opt/quietla/nm-lib 2>/dev/null | head -1)"' < /dev/null > /dev/null 2>&1
}
provision() { # <name>: up to 3 attempts (a long rsync to a far region sometimes drops; the script is idempotent)
  local name=$1 i=1 log="$STATE/logs/provision-$1-$(date +%Y%m%dT%H%M%S).log"
  while [ "$i" -le 3 ]; do
    echo "== attempt $i $(date -u +%FT%TZ)" >> "$log"
    "$HERE/provision_host.sh" "$name" >> "$log" 2>&1 < /dev/null && { host_ready "$name" && return 0; }
    [ "$i" -lt 3 ] && sleep "${PROVISION_RETRY_WAIT:-30}"
    i=$((i + 1))
  done
  return 1
}

# finish_host <name> <cfg> <region> <zone> <ip> <prev zone> <provision result 0|1>: label, one worker, remove our flag, log
finish_host() {
  local name=$1 region=$3 zone=$4 ip=$5 prev=$6 rc=$7 threads port
  threads=$(host_threads "$name"); port=$(host_port "$name")
  if [ "$rc" != 0 ]; then
    now > "$STATE/provfail-$name"
    alert_once "provfail-$name" 3600 "replace $name: provisioning failed 3 times ($zone, ip $ip); the VM is kept with its pause flag, next try in 1 h (logs: $STATE/logs)"
    return 1
  fi
  rm -f "$STATE/provfail-$name"
  fix_place_label "$name" "$region"
  drop_stale_tunnel "$name" "$port"
  ensure_one_worker "$name" "$threads" "$port"
  flag_remove "$name"
  rm -f "$STATE/pending-$name"
  if [ -n "$prev" ]; then note "REPLACED $name $prev -> $zone (ip $ip)"
  else note "REPLACED $name (finished after an interrupted run) in $zone (ip $ip)"; fi
}

# ---------------------------------------------------------------- main
for cfg in $CONFIGS; do
  if list_account "$cfg"; then :; else
    n=$(( $(cat "$STATE/listfail-$cfg" 2> /dev/null || echo 0) + 1 ))
    [ "$DRY" = 1 ] || echo "$n" > "$STATE/listfail-$cfg"
    say "could not list the instances of $cfg: $(head -c 200 "$TMP/raw-$cfg.err")"
    [ "$n" -ge 3 ] && alert_once "listfail-$cfg" 10800 "auto_replace: cannot list instances of gcloud configuration $cfg ($n runs in a row): $(head -c 160 "$TMP/raw-$cfg.err" | tr '\n' ' ')"
    continue
  fi
  [ "$DRY" = 1 ] || rm -f "$STATE/listfail-$cfg"
done

PENDING=()      # dead spot hosts to replace
RECOVER=()      # live hosts still carrying our pause flag from an interrupted run
for r in ${FLEET_ROWS[@]+"${FLEET_ROWS[@]}"}; do
  set -- $r; name=$1; cfg=$2; type=$3; model=$4; region=$5; zones=$6
  [ -e "$TMP/ok-$cfg" ] || continue                       # no trustworthy list for this account: do nothing
  in_hosts_file "$name" || continue                        # deleted on purpose (gcp_delete.sh)
  row=$(inst_row "$cfg" "$name")
  status=$(field "$row" 3)
  case "$status" in
    RUNNING|PROVISIONING|STAGING|STOPPING|REPAIRING)
      [ "$DRY" = 1 ] || echo "$(field "$row" 2)" > "$STATE/zone-$name"
      if flag_ours "$name"; then RECOVER+=("$name"); fi
      continue;;
  esac
  if [ "$model" != spot ]; then
    alert_once "gone-$name" 10800 "$name is gone (on-demand machine, never replaced automatically): replace it by hand"
    continue
  fi
  if flag_exists "$name" && ! flag_ours "$name"; then say "$name is gone but pause-$name is the owner's: left alone"; continue; fi
  PENDING+=("$name")
done

# interrupted earlier run: the VM exists and our pause flag is still up
for name in ${RECOVER[@]+"${RECOVER[@]}"}; do
  set -- $(grep "^$name " "$FLEET"); cfg=$2; region=$5
  row=$(inst_row "$cfg" "$name"); zone=$(field "$row" 2)
  if [ "$DRY" = 1 ]; then say "$name is running and still has our pause flag: would check it is provisioned and release it"; continue; fi
  pf=$(cat "$STATE/provfail-$name" 2> /dev/null || echo 0)
  if host_ready "$name"; then rc=0
  elif [ $(( $(now) - pf )) -lt 3600 ]; then continue
  else provision "$name"; rc=$?; fi
  prev=""; ip=""
  if [ -f "$STATE/pending-$name" ]; then read -r prev newz ip rest < "$STATE/pending-$name"; fi
  [ -n "$ip" ] || ip=$(ssh -G -F "$HOME/.ssh/quietla_cloud_config" "$name" 2> /dev/null < /dev/null | awk '$1 == "hostname" { print $2 }')
  finish_host "$name" "$cfg" "$region" "$zone" "${ip:-?}" "$prev" "$rc"
done

if [ ${#PENDING[@]} -eq 0 ]; then say "nothing to replace (${#FLEET_ROWS[@]} fleet hosts checked)"; exit 0; fi

# the engine image must be mounted for provision_host.sh: do not create a VM that cannot be prepared
if [ ! -d "$ENGINE_LIB" ]; then
  alert_once noimage 3600 "replace ${PENDING[*]}: the NoiseModelling engine image is not mounted, postponed (compute_up.sh mounts it)"
  say "engine image not mounted: would postpone"
  [ "$DRY" = 1 ] || exit 0
fi

CREATED=()
for name in "${PENDING[@]}"; do
  set -- $(grep "^$name " "$FLEET"); cfg=$2; type=$3; model=$4; region=$5; zones=$6
  [ -f "$C/STOP-autoreplace" ] && { say "STOP-autoreplace appeared: stopping"; break; }
  queue_empty && { say "queue finished: stopping"; break; }
  [ -e "$TMP/blocked-$cfg" ] && continue
  # a fresh look right before acting (somebody may have replaced it by hand, quota may have changed)
  if [ "$DRY" = 0 ]; then
    list_account "$cfg" || { say "listing $cfg failed"; : > "$TMP/blocked-$cfg"; continue; }
  fi
  row=$(inst_row "$cfg" "$name"); status=$(field "$row" 3)
  case "$status" in RUNNING|PROVISIONING|STAGING|STOPPING|REPAIRING) continue;; esac
  prev=$(field "$(inst_row_raw "$cfg" "$name")" 2)
  [ -n "$prev" ] || prev=$(cat "$STATE/zone-$name" 2> /dev/null)
  [ -n "$prev" ] || prev=${zones%%,*}

  left=$(backoff_left "$cfg")
  if [ "$left" -gt 0 ]; then say "account $cfg is backed off for $left s"; : > "$TMP/blocked-$cfg"; continue; fi
  if [ "$(count_recent 3 "$cfg")" -ge "$PER_ACCOUNT_24H" ]; then
    alert_once "acctlimit-$cfg" 21600 "replace: account $cfg already had $PER_ACCOUNT_24H replacements in 24 h; no more automatic replacements for it until some age out (gone: $name)"
    : > "$TMP/blocked-$cfg"; continue
  fi
  if [ "$(count_recent 2 "$name")" -ge "$PER_HOST_24H" ]; then
    alert_once "hostlimit-$name" 21600 "replace $name: already replaced $PER_HOST_24H times in 24 h, leaving it for a human"
    continue
  fi
  need=$(cpus_of_type "$type"); have=$(acct_cpus "$cfg")
  if [ $(( have + need )) -gt "$ACCOUNT_CAP" ]; then
    alert_once "cap-$name" 21600 "replace $name: account $cfg would exceed $ACCOUNT_CAP vCPU ($have in use + $need)"
    continue
  fi
  spot_have=$(region_spot_cpus "$cfg" "$region"); spot_cap=$(spot_region_cap "$cfg")
  if [ $(( spot_have + need )) -gt "$spot_cap" ]; then
    alert_once "spotcap-$name" 21600 "replace $name: Spot vCPUs in $region of $cfg would exceed $spot_cap ($spot_have in use + $need)"
    continue
  fi
  order=$(zone_order "$cfg" "$zones" "$prev" | tr '\n' ' ')

  if [ "$DRY" = 1 ]; then
    say "$name ($cfg, $type, Spot, $region) is gone; it was in $prev"
    say "  caps ok: $cfg uses $have of $ACCOUNT_CAP vCPU without it (+$need), Spot vCPUs in $region $spot_have of $spot_cap (+$need); replaced $(count_recent 2 "$name")x for this host, $(count_recent 3 "$cfg")x for the account in 24 h"
    say "  would: write $C/pause-$name containing '$MARK' (only if no flag is there)"
    first=${order%% *}
    say "  would: CLOUDSDK_ACTIVE_CONFIG_NAME=$cfg $HERE/gcp_create.sh $name $first $type      (zones in this order on a stock-out: $order)"
    say "  would: $HERE/provision_host.sh $name      (up to 3 attempts, then check /opt/quietla is ready)"
    say "  would: set the place in cloud_hosts.txt to '$(place_of "$region")' if different (now: '$(host_label "$name" | sed 's/.*· //')')"
    say "  would: make sure exactly one worker runs for $name port $(host_port "$name") ($(worker_pids "$name" "$(host_port "$name")" | grep -c .) running now; start $P/queue_worker_persist.sh $Q $name $(host_port "$name") $(host_threads "$name") if none)"
    say "  would: remove pause-$name again, only if it still contains '$MARK'"
    say "  would log: REPLACED $name $prev -> ${first} (ip <new ip>)"
    continue
  fi

  if ! flag_make "$name"; then say "pause-$name is somebody else's: skipped"; continue; fi
  # a stopped corpse of the same name would block creating it in the same zone: remove it first
  if [ "$status" = TERMINATED ] || [ "$status" = STOPPED ] || [ "$status" = SUSPENDED ]; then
    cz=$(field "$row" 2)
    if CLOUDSDK_ACTIVE_CONFIG_NAME=$cfg gcloud compute instances delete "$name" --zone "$cz" --quiet > "$STATE/logs/delete-$name.log" 2>&1 < /dev/null; then
      note "removed the stopped VM $name in $cz before replacing it"
    else
      alert_once "delfail-$name" 3600 "replace $name: could not remove the stopped VM in $cz: $(tail -c 200 "$STATE/logs/delete-$name.log" | tr '\n' ' ')"; continue
    fi
  fi

  created=0; newzone=""; ip=""; stockouts=""; stamp=$(date +%Y%m%dT%H%M%S)
  for zone in $order; do
    clog="$STATE/logs/create-$name-$stamp-$zone.log"
    run_limited 1500 "$clog" "$clog.err" env CLOUDSDK_ACTIVE_CONFIG_NAME="$cfg" "$HERE/gcp_create.sh" "$name" "$zone" "$type"
    crc=$?
    cat "$clog.err" >> "$clog" 2> /dev/null; rm -f "$clog.err"
    if [ "$crc" = 0 ]; then created=1; newzone=$zone; break; fi
    if grep -q "created but SSH did not answer" "$clog"; then created=1; newzone=$zone; break; fi
    if grep -qiE "ZONE_RESOURCE_POOL_EXHAUSTED|STOCKOUT|does not have enough resources|currently unavailable|resource pool exhausted|not available in (the )?zone|does not exist in zone" "$clog"; then
      stockouts="$stockouts $zone"; continue
    fi
    # the client may have failed after the VM was made: look before reporting a failure
    if list_account "$cfg"; then
      row=$(inst_row_raw "$cfg" "$name")
      case "$(field "$row" 3)" in RUNNING|PROVISIONING|STAGING) created=1; newzone=$(field "$row" 2); break;; esac
    fi
    reason=$(grep -iE "error|exceeded|denied|billing|quota" "$clog" | tail -2 | tr '\n' ' ' | cut -c1-260)
    if grep -qiE "billing" "$clog"; then span=$HARD_BACKOFF; kind=billing
    elif grep -qiE "quota" "$clog"; then span=$HARD_BACKOFF; kind=quota
    elif grep -qiE "PERMISSION_DENIED|permission|403|forbidden|not authorized|reauthenticat|credentials|login" "$clog"; then span=$HARD_BACKOFF; kind=permission
    else span=$SOFT_BACKOFF; kind=error; fi
    set_backoff "$cfg" "$span"
    note "ALERT replace $name failed ($kind in $zone): ${reason:-see $clog}; account $cfg backed off for $((span / 3600))h$(( span % 3600 / 60 ))m"
    : > "$TMP/blocked-$cfg"
    break
  done
  if [ "$created" = 0 ]; then
    if [ ! -e "$TMP/blocked-$cfg" ]; then
      alert_once "stockout-$name" 3600 "replace $name failed: no Spot capacity for $type in $region (tried$stockouts); pause-$name stays up, trying again next loop"
    fi
    continue
  fi
  echo "$(now) $name $cfg $newzone" >> "$STATE/history"
  ip=$(grep -E "^$name $newzone " "$clog" | awk '{ print $4 }' | tail -1)
  [ -n "$ip" ] || ip=$(ssh -G -F "$HOME/.ssh/quietla_cloud_config" "$name" 2> /dev/null < /dev/null | awk '$1 == "hostname" { print $2 }')
  echo "$newzone" > "$STATE/zone-$name"
  echo "$prev $newzone ${ip:-?} $(now)" > "$STATE/pending-$name"
  flag_make "$name" > /dev/null    # still ours? (the owner may have resumed the host while it was being made: put it back)
  note "REPLACING $name $prev -> $newzone (ip ${ip:-?}): provisioning"
  CREATED+=("$name")
done

[ "$DRY" = 1 ] && exit 0

# provision what was created, three machines at a time, then bring each into service
i=0
while [ "$i" -lt ${#CREATED[@]} ]; do
  batch=("${CREATED[@]:$i:3}")
  jobs_=()
  for name in "${batch[@]}"; do
    ( provision "$name"; echo $? > "$TMP/prov-$name" ) &
    jobs_+=($!)
  done
  for j in "${jobs_[@]}"; do wait "$j"; done
  i=$((i + 3))
done
for name in ${CREATED[@]+"${CREATED[@]}"}; do
  set -- $(grep "^$name " "$FLEET"); cfg=$2; region=$5
  read -r prev newzone ip rest < "$STATE/pending-$name"
  finish_host "$name" "$cfg" "$region" "$newzone" "$ip" "$prev" "$(cat "$TMP/prov-$name" 2> /dev/null || echo 1)"
done
exit 0
