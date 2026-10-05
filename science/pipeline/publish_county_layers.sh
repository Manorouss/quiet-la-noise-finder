#!/bin/bash
# Keep the public county map current: every INTERVAL seconds (default 2 h) build assets for
# newly computed tiles (release_county_tiles.py, which runs the ceiling QA), rebuild the PMTiles
# layers, and upload changed files to the R2 bucket that /map reads. Stops when
# implementation/work/pipeline_control/STOP-publish exists. Never publishes a failed release.
#
#   publish_county_layers.sh            # loop
#   publish_county_layers.sh --once     # one release
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../../../.." && pwd)
LAYERS=$ROOT/implementation/work/county_layers/current
CONTROL=$ROOT/implementation/work/pipeline_control
LOG=$CONTROL/publish.log
BUCKET=quiet-la-data/county
INTERVAL=${INTERVAL:-7200}
mkdir -p "$CONTROL"
cd "$ROOT/implementation/apps/quiet-la-web"

upload() {  # upload <relative path> <cache seconds>
  local f=$1 ct=application/octet-stream
  case $f in *.json|*.geojson) ct=application/json;; esac
  # wrangler uploads at most 315 MB in one request; an rclone remote named r2 (S3 API with an R2 access
  # key, set up by the owner) uploads in parts and is used whenever it exists.
  if command -v rclone > /dev/null && rclone listremotes 2> /dev/null | grep -qx "r2:"; then
    rclone copyto "$LAYERS/$f" "r2:$BUCKET/$f" --s3-no-check-bucket --s3-upload-cutoff 100M --s3-chunk-size 64M \
      --header-upload "Content-Type: $ct" --header-upload "Cache-Control: public, max-age=$2" > /dev/null 2>&1
  elif [ "$(stat -f %z "$LAYERS/$f")" -gt 314572800 ]; then
    echo "$(date -u +%FT%TZ) $f is over 300 MB: wrangler cannot upload it; set up the rclone remote r2 (HANDOFF.md)" >> "$LOG"; return 1
  else
    npx wrangler r2 object put "$BUCKET/$f" --file "$LAYERS/$f" --content-type "$ct" --cache-control "public, max-age=$2" --remote > /dev/null 2>&1
  fi
}

publish_once() {
  local before after
  before=$(python3 -c "import json; print(len(json.load(open('$LAYERS/layers.json'))['tiles']))" 2>/dev/null || echo 0)
  if ! python3 "$HERE/release_county_tiles.py" --jobs 2 >> "$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) release FAILED; nothing published" >> "$LOG"; return 1
  fi
  after=$(python3 -c "import json; print(len(json.load(open('$LAYERS/layers.json'))['tiles']))")
  if [ "$after" = "$before" ]; then echo "$(date -u +%FT%TZ) no new tiles ($after)" >> "$LOG"; return 0; fi
  # Data files first, layers.json last, so a reader never sees a manifest ahead of its data.
  for f in receivers.pmtiles buildings.pmtiles roads.pmtiles field_d.pmtiles field_e.pmtiles field_n.pmtiles glow_d.pmtiles glow_e.pmtiles glow_n.pmtiles coverage.geojson coverage_outline.geojson; do
    upload "$f" 300 || { echo "$(date -u +%FT%TZ) upload FAILED: $f" >> "$LOG"; return 1; }
  done
  upload layers.json 60 && echo "$(date -u +%FT%TZ) published $after tiles (was $before)" >> "$LOG"
}

if [ "${1:-}" = "--once" ]; then publish_once; exit $?; fi
while [ ! -f "$CONTROL/STOP-publish" ]; do
  publish_once
  sleep "$INTERVAL"
done
