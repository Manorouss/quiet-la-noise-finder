#!/bin/sh
set -eu
if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  echo "usage: $0 '<NoiseModelling compiled classpath>'" >&2
  exit 2
fi
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
replay_dir=$(mktemp -d "${TMPDIR:-/tmp}/quiet-la-hf-replay.XXXXXX")
trap 'rm -rf "$replay_dir"' EXIT HUP INT TERM
javac -cp "$1" -d "$replay_dir" "$bundle_dir/AgroundHFReplay.java"
java -cp "$replay_dir:$1" AgroundHFReplay < "$bundle_dir/h_regression_inputs.tsv"
java -cp "$replay_dir:$1" AgroundHFReplay < "$bundle_dir/f_regression_inputs.tsv"
