#!/usr/bin/env python3
"""Runs on the headset: sample receive counters of both Wi-Fi interfaces 20 times a second (read-only, no root).

    python3 frame-rx-sampler.py OUT.csv [SECONDS]        stop early with: touch /tmp/STOP-RXS

wlanap = the headset's own 6 GHz access point (PC dongle path), wlan0 = home Wi-Fi (PC LAN path).
"""
import os
import sys
import time

out = sys.argv[1]
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 5400
HZ = 20
FILES = [f"/sys/class/net/{i}/statistics/{c}" for i in ("wlanap", "wlan0") for c in ("rx_packets", "rx_bytes", "rx_dropped", "tx_packets")]
fds = [open(p) for p in FILES]
snmp = open("/proc/net/snmp")
start = time.time()
with open(out, "w", buffering=1 << 16) as fh:
    fh.write("epoch,ap_rx_packets,ap_rx_bytes,ap_rx_dropped,ap_tx_packets,sta_rx_packets,sta_rx_bytes,sta_rx_dropped,sta_tx_packets,udp_in,udp_rcvbuf_err\n")
    n = 0
    while time.time() - start < seconds and not os.path.exists("/tmp/STOP-RXS"):
        t = time.time()
        vals = []
        for f in fds:
            f.seek(0)
            vals.append(f.read().strip())
        snmp.seek(0)
        udp = [l.split() for l in snmp if l.startswith("Udp:")]
        fh.write(f"{t:.4f}," + ",".join(vals) + f",{udp[1][1]},{udp[1][5]}\n")
        n += 1
        if n % HZ == 0:
            fh.flush()
        time.sleep(max(0.0, start + n / HZ - time.time()))
if os.path.exists("/tmp/STOP-RXS"):
    os.remove("/tmp/STOP-RXS")
