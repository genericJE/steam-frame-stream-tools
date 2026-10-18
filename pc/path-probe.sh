#!/usr/bin/env bash
# Low-rate delay probe on both paths to the headset while a stream is running: 200 small UDP packets per second
# over the dongle and 200 over the LAN, timestamped on both ends (about 0.3 Mbit/s per path, no root).
#   path-probe.sh [SECONDS_PER_CHUNK] [CHUNKS] [OUTDIR]        stop after the current chunk: touch OUTDIR/STOP
# Each chunk leaves probe-dongle-N.csv and probe-lan-N.csv (seq,send_ns,recv_ns,...) plus the receivers' summaries.
set -u
SECS=${1:-300}; CHUNKS=${2:-1}
HERE=$(dirname "$(readlink -f "$0")")
OUT=${3:-$HERE/../capture/$(date +%Y%m%d-%H%M%S)-probe}
IF=${IF:-$(for i in /sys/class/net/*; do [ "$(basename "$(readlink -f "$i/device/driver" 2>/dev/null)")" = rtw89_8852cu ] && basename "$i"; done)}; LAN=${LAN:-enp15s0}
PC_DONGLE_IP=$(ip -4 -o addr show dev "$IF" | awk '{sub(/\/.*/,"",$4); print $4; exit}')
PC_LAN_IP=$(ip -4 -o addr show dev "$LAN" | awk '{sub(/\/.*/,"",$4); print $4; exit}')
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 steamos@frame"
mkdir -p "$OUT"
scp -q -o BatchMode=yes "$HERE/udptest.py" steamos@frame:/tmp/udptest.py || exit 1
for n in $(seq 1 "$CHUNKS"); do
  [ -e "$OUT/STOP" ] && break
  python3 "$HERE/udptest.py" tx --port 5980 --wait-punch --mbit 0.32 --size 200 --seconds "$SECS" --bind-dev "$IF" > "$OUT/tx-dongle-$n.json" 2>&1 &
  python3 "$HERE/udptest.py" tx --port 5981 --wait-punch --mbit 0.32 --size 200 --seconds "$SECS" > "$OUT/tx-lan-$n.json" 2>&1 &
  sleep 0.3
  $SSH "python3 /tmp/udptest.py rx --port 5980 --punch $PC_DONGLE_IP --seconds $((SECS + 6)) --size-hint 200 --dump /tmp/probe-dongle.csv > /tmp/probe-dongle.json &
        python3 /tmp/udptest.py rx --port 5981 --punch $PC_LAN_IP --seconds $((SECS + 6)) --size-hint 200 --dump /tmp/probe-lan.csv > /tmp/probe-lan.json
        wait; echo dongle; cat /tmp/probe-dongle.json; echo lan; cat /tmp/probe-lan.json" > "$OUT/rx-$n.txt" 2>&1
  wait
  scp -q -o BatchMode=yes steamos@frame:/tmp/probe-dongle.csv "$OUT/probe-dongle-$n.csv"
  scp -q -o BatchMode=yes steamos@frame:/tmp/probe-lan.csv "$OUT/probe-lan-$n.csv"
  $SSH 'rm -f /tmp/probe-dongle.csv /tmp/probe-lan.csv /tmp/probe-dongle.json /tmp/probe-lan.json'
  echo "chunk $n done $(date +%T)"; cat "$OUT/rx-$n.txt"
done
