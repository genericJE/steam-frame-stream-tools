#!/usr/bin/env bash
# How much of the stream reaches the headset on each path while it is running (read-only, no root on the PC).
#   path-delivery.sh [SECONDS]      default 60
# Reads interface counters on both ends at the start and end of the window. The PC's dongle interface counts
# GSO batches as one packet, so the dongle path is compared in bytes and against the LAN path's packet count.
# The LAN figures include unrelated traffic on both ends; next to a stream of tens of thousands of packets per
# second that is small.
set -eu
SECS=${1:-60}; IF=${IF:-$(for i in /sys/class/net/*; do [ "$(basename "$(readlink -f "$i/device/driver" 2>/dev/null)")" = rtw89_8852cu ] && basename "$i"; done)}; LAN=${LAN:-enp15s0}
CM="-o BatchMode=yes -o ConnectTimeout=8 -o ControlMaster=auto -o ControlPersist=120 -o ControlPath=${XDG_RUNTIME_DIR:-/tmp}/frame-pd-%r@%h"
remote='echo $(date +%s.%N) $(cat /sys/class/net/wlanap/statistics/rx_packets /sys/class/net/wlanap/statistics/rx_bytes /sys/class/net/wlan0/statistics/rx_packets /sys/class/net/wlan0/statistics/rx_bytes) $(awk "/^Udp: [0-9]/{print \$2, \$6}" /proc/net/snmp)'
local_snap() { echo "$(date +%s.%N) $(cat /sys/class/net/$IF/statistics/tx_packets /sys/class/net/$IF/statistics/tx_bytes /sys/class/net/$LAN/statistics/tx_packets /sys/class/net/$LAN/statistics/tx_bytes) $(awk '/^Udp: [0-9]/{print $5, $7}' /proc/net/snmp) $(iw dev $IF station dump -v | awk '/TXQs:/{t=1} t && $1=="0"{print $5; exit}')"; }
ssh $CM steamos@frame true
snap() { local a b r; a=$(local_snap); r=$(ssh $CM steamos@frame "$remote"); b=$(local_snap); echo "$a|$r|$b"; }
s1=$(snap); sleep "$SECS"; s2=$(snap)
python3 - "$s1" "$s2" <<'PY'
import sys
def parse(s):
    a, r, b = [list(map(float, x.split())) for x in s.split("|")]
    mid = [(x + y) / 2 for x, y in zip(a, b)]          # PC counters interpolated to the moment of the headset read
    return mid, r, b[0] - a[0]
(m1, r1, w1), (m2, r2, w2) = parse(sys.argv[1]), parse(sys.argv[2])
dt = m2[0] - m1[0]
print(f"window {dt:.1f} s (snapshot round trips {w1*1000:.0f} ms and {w2*1000:.0f} ms)")
d_sent_b, d_got_b, d_got_p = m2[2] - m1[2], r2[2] - r1[2], r2[1] - r1[1]
l_sent_p, l_sent_b, l_got_p, l_got_b = m2[3] - m1[3], m2[4] - m1[4], r2[3] - r1[3], r2[4] - r1[4]
print(f"LAN -> router -> headset wlan0:  PC sent {l_sent_p:.0f} packets ({l_sent_b*8/dt/1e6:.1f} Mbit/s), headset received {l_got_p:.0f} ({l_got_b*8/dt/1e6:.1f} Mbit/s), missing {l_sent_p-l_got_p:.0f} ({100*(l_sent_p-l_got_p)/l_sent_p:.2f} %)")
print(f"dongle -> headset wlanap:        PC handed over {d_sent_b*8/dt/1e6:.1f} Mbit/s, headset received {d_got_p:.0f} packets ({d_got_b*8/dt/1e6:.1f} Mbit/s) = {100*d_got_p/l_got_p:.1f} % of the packets that arrived over the LAN")
print(f"PC UDP send-buffer errors {m2[6]-m1[6]:.0f}, Wi-Fi queue drops (A-MSDUs) {m2[7]-m1[7]:.0f}; headset UDP receive-buffer errors {r2[6]-r1[6]:.0f}")
PY
