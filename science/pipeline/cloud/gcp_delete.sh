#!/bin/bash
# Delete rented Google Cloud VMs of the county queue (label quietla=county) and drop them from the hosts
# file so no worker restarts for them:  gcp_delete.sh <name> [<name> ...] | --all
set -eu
C=$(cd "$(dirname "$0")/../../../../../.." && pwd)/implementation/work/pipeline_control
if [ "${1:-}" = "--all" ]; then
  mapfile -t ROWS < <(gcloud compute instances list --filter 'labels.quietla=county' --format 'value(name,zone)')
else
  ROWS=()
  for n in "$@"; do ROWS+=("$(gcloud compute instances list --filter "name=$n" --format 'value(name,zone)')"); done
fi
for row in "${ROWS[@]}"; do
  [ -n "$row" ] || continue
  set -- $row
  gcloud compute instances delete "$1" --zone "$2" --quiet && echo "deleted $1"
  [ -f "$C/cloud_hosts.txt" ] && sed -i '' "/^$1 /d" "$C/cloud_hosts.txt"
done
