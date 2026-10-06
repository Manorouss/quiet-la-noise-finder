#!/bin/bash
# Prepare a rented Linux host (Ubuntu 22.04/24.04, x86 or arm64) to run NoiseModelling tiles for the county
# queue: Java 21, the engine jars from the pinned 6.0 image, the loopback launcher, the hf_v1 overlay and the
# model-v3 WPS scripts, all under /opt/quietla. The host must be an entry of ~/.ssh/quietla_cloud_config.
#
#   provision_host.sh <host name>
#
# Afterwards add "<name> <threads> <local port>" to implementation/work/pipeline_control/cloud_hosts.txt and run
# compute_up.sh (or test one tile first:
#   run_attempt.py --source-attempt <package> --label v3-<name>16 --host <name> --port <local port> --threads 16 \
#     --no-vertical --terrain-downscale 1 --max-error-db 0.1 --atmo).
set -eu
NAME=$1
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../../../.." && pwd)
WORK=$ROOT/implementation/work
CFG=$HOME/.ssh/quietla_cloud_config
SSH=(ssh -F "$CFG" -o BatchMode=yes "$NAME")
RSYNC=(rsync -az --delete -e "ssh -F $CFG -o BatchMode=yes")
LIB=/Volumes/NoiseModelling/NoiseModelling.app/Contents/app/lib
HELPER=$WORK/campaign/noisemodelling_loopback_launcher/noisemodelling-loopback-launcher.jar
OVERLAY=$WORK/campaign/tarzana_full_mixed_road_v1/sentinel_forensics/r02_c04_source_diagnostic_v1/engine_overlay_hf_v1/runtime_overlay/classes
SCRIPTS=$WORK/engine_scripts_v3/scripts
[ -d "$LIB" ] || { echo "engine image not mounted ($LIB)"; exit 1; }

"${SSH[@]}" 'sudo apt-get update -qq > /dev/null && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openjdk-21-jre-headless rsync > /dev/null && sudo mkdir -p /opt/quietla && sudo chown "$USER" /opt/quietla && mkdir -p /opt/quietla/attempts && java -version 2>&1 | head -1 && nproc && free -g | head -2'
"${RSYNC[@]}" "$LIB/" "$NAME:/opt/quietla/nm-lib/"
"${RSYNC[@]}" "$HELPER" "$NAME:/opt/quietla/helper/"
"${RSYNC[@]}" "$OVERLAY/" "$NAME:/opt/quietla/overlays/hf_v1/"
"${RSYNC[@]}" "$SCRIPTS/" "$NAME:/opt/quietla/engine_scripts_v3/scripts/"
"${SSH[@]}" 'ls /opt/quietla/nm-lib | wc -l; find /opt/quietla/engine_scripts_v3 -name "*.groovy" | wc -l; ls /opt/quietla/overlays/hf_v1 /opt/quietla/helper'
echo "$NAME provisioned"
