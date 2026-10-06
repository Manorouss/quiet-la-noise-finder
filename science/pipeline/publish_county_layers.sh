#!/bin/bash
# Keep the public county map current: every INTERVAL seconds (default 2 h) build assets for
# newly computed tiles (release_county_tiles.py, which runs the ceiling QA), rebuild the PMTiles
# layers, and upload the files whose checksum differs from the published layers.json to the R2 bucket
# that /map reads. Stops when
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
PUBLIC=https://pub-11d80fa8a6864ca29d637d47515be7cc.r2.dev/county
INTERVAL=${INTERVAL:-7200}
mkdir -p "$CONTROL"
cd "$ROOT/implementation/apps/quiet-la-web"

upload() {  # upload <relative path> <cache seconds>
  local f=$1 ct=application/octet-stream
  case $f in *.json|*.geojson) ct=application/json;; esac
  # wrangler uploads at most 315 MB in one request; an rclone remote named r2 (S3 API with an R2 access
  # key, set up by the owner) uploads in parts and is used whenever it exists. --ignore-times: always a full
  # upload, so the headers are set (an unchanged file would only get its timestamp updated, losing Cache-Control).
  if command -v rclone > /dev/null && rclone listremotes 2> /dev/null | grep -qx "r2:"; then
    rclone copyto "$LAYERS/$f" "r2:$BUCKET/$f" --ignore-times --s3-no-check-bucket --s3-upload-cutoff 100M --s3-chunk-size 64M \
      --header-upload "Content-Type: $ct" --header-upload "Cache-Control: public, max-age=$2" > /dev/null 2>&1
  elif [ "$(stat -f %z "$LAYERS/$f")" -gt 314572800 ]; then
    echo "$(date -u +%FT%TZ) $f is over 300 MB: wrangler cannot upload it; set up the rclone remote r2 (HANDOFF.md)" >> "$LOG"; return 1
  else
    npx wrangler r2 object put "$BUCKET/$f" --file "$LAYERS/$f" --content-type "$ct" --cache-control "public, max-age=$2" --remote > /dev/null 2>&1
  fi
}

changed_files() {  # files whose sha256 differs from the layers.json now on R2 (all data files if it cannot be read)
  curl -s -f --max-time 60 "$PUBLIC/layers.json" -o "$CONTROL/published_layers.json" || rm -f "$CONTROL/published_layers.json"
  python3 - "$LAYERS/layers.json" "$CONTROL/published_layers.json" <<'PY'
import json, sys
new = json.load(open(sys.argv[1]))["files"]
try:
    old = json.load(open(sys.argv[2]))["files"]
except (OSError, ValueError, KeyError):
    old = {name: {} for name in new if name.startswith(("basemap/", "context/"))}  # uploaded once by hand
for name, meta in new.items():
    prev = old.get(name)
    if prev is not None and (prev.get("sha256") == meta["sha256"] or (not prev and name.startswith(("basemap/", "context/")))):
        continue
    print(name)
PY
}

publish_once() {
  local changed tiles
  if ! python3 "$HERE/release_county_tiles.py" --jobs 2 >> "$LOG" 2>&1; then
    echo "$(date -u +%FT%TZ) release FAILED; nothing published" >> "$LOG"; return 1
  fi
  tiles=$(python3 -c "import json; print(len(json.load(open('$LAYERS/layers.json'))['tiles']))")
  # Replaced tiles (a newer model for the same cell) change files without changing the tile count.
  changed=$(changed_files)
  if [ -z "$changed" ]; then echo "$(date -u +%FT%TZ) no changes ($tiles tiles)" >> "$LOG"; return 0; fi
  # Data files first, layers.json last, so a reader never sees a manifest ahead of its data.
  for f in $changed; do
    upload "$f" 300 || { echo "$(date -u +%FT%TZ) upload FAILED: $f" >> "$LOG"; return 1; }
  done
  upload layers.json 60 && echo "$(date -u +%FT%TZ) published $tiles tiles (changed: $(echo $changed | tr '\n' ' '))" >> "$LOG"
  # Accuracy check against the public monitors and measurements after every publish (science/qa/validate_monitors.py),
  # so the bias by period is always current: implementation/work/pipeline_control/validation.json (+ .log).
  pgrep -f "validate_monitors.py" > /dev/null || nohup "$HERE/geo-python" "$HERE/../qa/validate_monitors.py" --json "$CONTROL/validation.json" > "$CONTROL/validation.log" 2>&1 &
}

if [ "${1:-}" = "--once" ]; then publish_once; exit $?; fi
while [ ! -f "$CONTROL/STOP-publish" ]; do
  publish_once
  sleep "$INTERVAL"
done
