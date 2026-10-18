#!/usr/bin/env bash
# One UDP run across the dongle link with packet accounting on both ends.
#   run_udp.sh down|up MBIT SECONDS [BURST_HZ] [SIZE]
# down = PC -> headset (the video direction), up = headset -> PC.
set -u
DIR=${1:-down}; MBIT=${2:-50}; SECS=${3:-8}; BURST=${4:-0}; SIZE=${5:-1400}
IF=${IF:-$(for i in /sys/class/net/*; do [ "$(basename "$(readlink -f "$i/device/driver" 2>/dev/null)")" = rtw89_8852cu ] && basename "$i"; done)}; HOST_IP=${HOST_IP:-$(ip -4 -o addr show dev "$IF" 2>/dev/null | awk "{print \$4}" | cut -d/ -f1)}; FRAME_IP=10.35.78.1; PORT=5999
FR="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlMaster=auto -o ControlPath=${XDG_RUNTIME_DIR:-/tmp}/frame-cm-%r@%h:%p -o ControlPersist=15m steamos@frame"
HERE=$(dirname "$(readlink -f "$0")")

host_snap() { echo "h_if_tx=$(cat /sys/class/net/$IF/statistics/tx_packets) h_if_rx=$(cat /sys/class/net/$IF/statistics/rx_packets) $(iw dev $IF station dump | awk '/tx packets/{printf "h_sta_tx=%s ",$3} /rx packets/{printf "h_sta_rx=%s ",$3} /rx drop misc/{printf "h_rx_drop=%s ",$4} /signal:/{printf "h_sig=%s ",$2} /tx bitrate/{printf "h_txrate=%s/MCS%s/NSS%s ",$3,$7,$9}')"; }
frame_snap() { $FR 'echo "f_if_rx=$(cat /sys/class/net/wlanap/statistics/rx_packets) f_if_tx=$(cat /sys/class/net/wlanap/statistics/tx_packets) f_if_rxdrop=$(cat /sys/class/net/wlanap/statistics/rx_dropped) $(iw dev wlanap station dump | awk "/rx packets/{printf \"f_sta_rx=%s \",\$3} /tx packets/{printf \"f_sta_tx=%s \",\$3} /rx drop misc/{printf \"f_rx_drop=%s \",\$4} /last ack signal/{printf \"f_acksig=%s \",\$4} /rx bitrate/{printf \"f_rxrate=%s/MCS%s/NSS%s \",\$3,\$7,\$9} /tx bitrate/{printf \"f_txrate=%s/MCS%s/NSS%s \",\$3,\$7,\$9}")"'; }
delta() { # name before after
  python3 - "$@" <<'PY'
import sys,re
b=dict(re.findall(r'(\w+)=(\S+)',sys.argv[1])); a=dict(re.findall(r'(\w+)=(\S+)',sys.argv[2]))
out=[]
for k in b:
    try: out.append(f"{k}:{int(a[k])-int(b[k])}")
    except Exception: out.append(f"{k}:{b[k]}->{a.get(k)}")
print("  "+"  ".join(out))
PY
}

H0=$(host_snap); F0=$(frame_snap)
if [ "$DIR" = down ]; then
  python3 "$HERE/udptest.py" tx --port $PORT --wait-punch --mbit $MBIT --seconds $SECS --burst-hz $BURST --size $SIZE ${BMBIT:+--burst-mbit $BMBIT} > ${TMPDIR:-/tmp}/udptx.out 2>&1 &
  TXPID=$!
  sleep 0.3
  $FR "python3 /tmp/udptest.py rx --port $PORT --punch $HOST_IP --seconds $((${SECS%.*}+5)) --size-hint $SIZE ${TL:+--timeline $TL}"
  wait $TXPID; cat ${TMPDIR:-/tmp}/udptx.out
else
  python3 "$HERE/udptest.py" rx --port $PORT --seconds $((${SECS%.*}+5)) --size-hint $SIZE ${TL:+--timeline $TL} > ${TMPDIR:-/tmp}/udprx.out 2>&1 &
  RXPID=$!
  sleep 0.3
  $FR "python3 /tmp/udptest.py tx --port $PORT --dest $HOST_IP --mbit $MBIT --seconds $SECS --burst-hz $BURST --size $SIZE"
  wait $RXPID; cat ${TMPDIR:-/tmp}/udprx.out
fi
H1=$(host_snap); F1=$(frame_snap)
echo "counter deltas (host, frame):"; delta "$H0" "$H1"; delta "$F0" "$F1"
