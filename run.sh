#!/bin/sh

BASE="/mnt/us/extensions/kindle-wallpaper"

cd "$BASE" || exit 1

export LD_LIBRARY_PATH="$BASE/lib"

# Start each animation run with a fresh log.
: > "$BASE/wallpaper.log"

"$BASE/kindle-wallpaper" >>"$BASE/wallpaper.log" 2>&1 &

echo $! > "$BASE/wallpaper.pid"
