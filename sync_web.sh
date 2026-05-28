#!/bin/bash
set -e
SRC="$(cd "$(dirname "$0")" && pwd)/badminton_booker/web"
DST="$(cd "$(dirname "$0")" && pwd)/ios/BadmintonBooker/Resources/Web"
for f in index.html app.js styles.css; do
  cp "$SRC/$f" "$DST/$f"
  echo "synced $f"
done
