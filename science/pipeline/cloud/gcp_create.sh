#!/bin/bash
# Create one Google Cloud Spot VM for the county queue and register it as a rented host:
#   gcp_create.sh <name> <zone> [machine type] [image family]
# e.g. gcp_create.sh ga us-central1-a c3d-standard-16
# Spot: reclaimable any time (the worker re-queues the tile), billed per second, deleted (not stopped) on
# reclaim so nothing keeps costing. Ubuntu 24.04, 20 GB disk, no external services. The VM's public IP goes
# into ~/.ssh/quietla_cloud_config as "Host <name>" (user from the SSH key's metadata entry, default quietla).
# Then: provision_host.sh <name>, and add "<name> <threads> <local port>" to pipeline_control/cloud_hosts.txt.
set -eu
NAME=$1; ZONE=$2; TYPE=${3:-c3d-standard-16}; FAMILY=${4:-ubuntu-2404-lts-amd64}
USER_NAME=${GCP_SSH_USER:-quietla}
CFG=$HOME/.ssh/quietla_cloud_config
PROJECT=$(gcloud config get-value project 2> /dev/null)
[ -n "$PROJECT" ] || { echo "no gcloud project set (gcloud config set project <id>)"; exit 1; }
MODEL_FLAGS=(--provisioning-model SPOT --instance-termination-action DELETE)
[ "${GCP_MODEL:-spot}" = "ondemand" ] && MODEL_FLAGS=()   # GCP_MODEL=ondemand: regular (non-reclaimable) machine
gcloud compute instances create "$NAME" --zone "$ZONE" --machine-type "$TYPE" ${MODEL_FLAGS[@]+"${MODEL_FLAGS[@]}"} \
  --image-family "$FAMILY" --image-project ubuntu-os-cloud --boot-disk-size 20GB \
  --metadata "ssh-keys=$USER_NAME:$(cat "$HOME/.ssh/quietla_cloud.pub")" \
  --labels quietla=county --quiet
IP=$(gcloud compute instances describe "$NAME" --zone "$ZONE" --format 'value(networkInterfaces[0].accessConfigs[0].natIP)')
touch "$CFG"; chmod 600 "$CFG"
python3 - "$CFG" "$NAME" "$IP" "$USER_NAME" <<'PY'
import re, sys
path, name, ip, user = sys.argv[1:5]
text = open(path).read()
text = re.sub(rf"\nHost {re.escape(name)}\n(?:[ \t]+.*\n)*", "\n", "\n" + text).lstrip("\n")
text += f"\nHost {name}\n    HostName {ip}\n    User {user}\n    IdentityFile ~/.ssh/quietla_cloud\n    IdentitiesOnly yes\n    StrictHostKeyChecking accept-new\n    ServerAliveInterval 30\n"
open(path, "w").write(text)
PY
echo "$NAME $ZONE $TYPE $IP"
for i in $(seq 1 30); do
  ssh -F "$CFG" -o ConnectTimeout=10 -o BatchMode=yes "$NAME" true 2> /dev/null && { echo "$NAME answers over SSH"; exit 0; }
  sleep 10
done
echo "$NAME created but SSH did not answer within 5 min (check the firewall rule for port 22 and the SSH user)"; exit 1
