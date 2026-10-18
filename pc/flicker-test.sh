#!/usr/bin/env bash
# Right-eye flicker test: record what the PC's encoder sends (H.265 dump from driver_vrlink) at a fixed, low
# bitrate, then check the recording for one eye showing an older picture (tools/bitstream-oldframe.py) and for a
# part of the picture going stale or black (tools/bitstream-eyes.py).
#
#   flicker-test.sh prepare [MBIT]   SteamVR closed. Fixes the stream bitrate (default 60 Mbit/s) in steamvr.vrsettings
#                                    and prints the launch option to paste into SteamVR's properties.
#   flicker-test.sh watch            While testing. Shows the dump growing; stops SteamVR if the dump passes
#                                    LIMIT_GB (default 3) or free memory drops under 1.5 GB (/tmp is RAM here).
#   flicker-test.sh collect          SteamVR closed. Moves the dump to capture/<time>-bitstream/ and analyzes it.
#   flicker-test.sh restore          SteamVR closed. Puts steamvr.vrsettings back.
#
# The dump is switched on by the environment variable VRLINK_DUMP_BITSTREAM=1 (read by driver_vrlink.so at start;
# it writes /tmp/vrlink_bitstream_*.h265 from the first keyframe on, about 7.5 MB per second at 60 Mbit/s).
# Remove the launch option after the test, or every later SteamVR session fills /tmp.
set -u
HERE=$(dirname "$(readlink -f "$0")")
CFG=$HOME/.local/share/Steam/config/steamvr.vrsettings
BAK=$CFG.flicker-test.bak
running() { pgrep -x vrserver >/dev/null; }

case ${1:-} in
  prepare)
    running && { echo "SteamVR is running. Close it first."; exit 1; }
    MBIT=${2:-60}
    [ -e "$BAK" ] || cp -p "$CFG" "$BAK"
    python3 - "$CFG" "$MBIT" <<'PY'
import json, sys
path, mbit = sys.argv[1], int(sys.argv[2])
cfg = json.load(open(path))
sec = cfg.setdefault("driver_vrlink", {})
sec["automaticBandwidth"] = False
sec["targetBandwidth"] = mbit
json.dump(cfg, open(path, "w"), indent=3)
print(f"driver_vrlink: automaticBandwidth=false, targetBandwidth={mbit} (backup: {path}.flicker-test.bak)")
PY
    echo
    echo "Now, in Steam: Library > SteamVR > Properties > Launch options, paste:"
    echo "    VRLINK_DUMP_BITSTREAM=1 %command%"
    echo "Then start SteamVR as usual, run '$0 watch' in a terminal, and play for 3 to 5 minutes."
    ;;
  watch)
    LIMIT=${LIMIT_GB:-3}
    until running; do echo "waiting for SteamVR ..."; sleep 3; done
    tr '\0' '\n' < /proc/"$(pgrep -x vrserver | head -1)"/environ | grep -q '^VRLINK_DUMP_BITSTREAM=1$' \
      && echo "dump switch is set" || echo "WARNING: VRLINK_DUMP_BITSTREAM=1 is not in SteamVR's environment (launch option missing?)"
    while running; do
      size=$(du -cm /tmp/vrlink_bitstream_* 2>/dev/null | tail -1 | cut -f1); size=${size:-0}
      free=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
      echo "$(date +%T)  dump ${size} MB  free memory ${free} MB"
      if [ "$size" -ge $((LIMIT * 1024)) ] || [ "$free" -lt 1500 ]; then
        echo "limit reached: stopping SteamVR"; pkill -TERM -x vrmonitor; sleep 5; pkill -TERM -x vrserver; break
      fi
      sleep 5
    done
    echo "SteamVR has stopped. Run '$0 collect'."
    ;;
  collect)
    running && { echo "SteamVR is still running. Close it first."; exit 1; }
    ls /tmp/vrlink_bitstream_* >/dev/null 2>&1 || { echo "no /tmp/vrlink_bitstream_* found"; exit 1; }
    OUT=$HERE/../capture/$(date +%Y%m%d-%H%M%S)-bitstream; mkdir -p "$OUT"
    mv /tmp/vrlink_bitstream_* "$OUT"/
    cp -p "$HOME"/.local/share/Steam/logs/driver_vrlink.txt "$OUT"/ 2>/dev/null
    for f in "$OUT"/vrlink_bitstream_*; do
      echo "== $f ($(du -m "$f" | cut -f1) MB)"
      "$HERE/bitstream-oldframe.py" "$f" --keep "$OUT/oldframe-stats" --csv "$OUT/old-frames.csv" | tee "$OUT/analysis-oldframe.txt"
      "$HERE/bitstream-eyes.py" "$f" | tee "$OUT/analysis-stale-black.txt"
    done
    echo "Now remove the launch option from SteamVR's properties, and run '$0 restore' to go back to automatic bitrate."
    ;;
  restore)
    running && { echo "SteamVR is running. Close it first."; exit 1; }
    [ -e "$BAK" ] && mv "$BAK" "$CFG" && echo "steamvr.vrsettings restored" || echo "no backup found; nothing changed"
    echo "Check that SteamVR's launch options no longer contain VRLINK_DUMP_BITSTREAM."
    ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
