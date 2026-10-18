#!/usr/bin/env python3
"""Sample the PC's Wi-Fi transmit queue toward the headset several times a second (no root needed).

    txq-sampler.py OUT.csv [--hz 10] [--seconds 5400] [--if IFACE (default: the rtw89_8852cu interface)] [--lan enp15s0]

Stop early with:  touch <directory of OUT.csv>/STOP-TXQ

Columns: time, dongle signal and PHY rate, the mac80211 per-station queue for TID 0 (bytes and packets waiting,
cumulative CoDel drops / ECN marks / overlimit drops, packets sent), system-wide UDP OutDatagrams and SndbufErrors,
interface byte counters (dongle and LAN), and the send-buffer fill of the vrlink socket bound to the dongle (port 10400).
"""
import argparse
import os
import re
import subprocess
import time



def detect_dongle():
    """Name of the Steam Frame Wireless Adapter interface (driver rtw89_8852cu), or None."""
    import glob
    for d in glob.glob("/sys/class/net/*/device/driver"):
        if os.path.basename(os.path.realpath(d)) == "rtw89_8852cu":
            return d.split("/")[4]
    return None

def read_int(path):
    try:
        with open(path) as fh:
            return int(fh.read())
    except (OSError, ValueError):
        return ""


def station(ifname):
    out = subprocess.run(["iw", "dev", ifname, "station", "dump", "-v"], capture_output=True, text=True).stdout
    r = dict(signal="", phy="", mcs="", nss="", rx_drop_misc="", qbytes="", qpkts="", drops="", marks="", overlimit="", txq_packets="")
    in_txq = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("signal:"):
            r["signal"] = s.split()[1]
        elif s.startswith("tx bitrate:"):
            f = s.split()
            r["phy"] = f[2]
            for i, tok in enumerate(f):
                if tok.endswith("MCS") and i + 1 < len(f):
                    r["mcs"] = f[i + 1]
                if tok.endswith("NSS") and i + 1 < len(f):
                    r["nss"] = f[i + 1]
        elif s.startswith("rx drop misc:"):
            r["rx_drop_misc"] = s.split()[3]
        elif s.startswith("TXQs:"):
            in_txq = True
        elif in_txq and re.match(r"^0\s", s):
            f = s.split()
            if len(f) >= 10:
                r.update(qbytes=f[1], qpkts=f[2], drops=f[4], marks=f[5], overlimit=f[6], txq_packets=f[9])
            in_txq = False
    return r


def udp():
    with open("/proc/net/snmp") as fh:
        rows = [l.split() for l in fh if l.startswith("Udp:")]
    v = dict(zip(rows[0][1:], rows[1][1:]))
    return v.get("OutDatagrams", ""), v.get("SndbufErrors", "")


def vrlink_socket(ifname):
    out = subprocess.run(["ss", "-u", "-a", "-n", "-m"], capture_output=True, text=True).stdout.splitlines()
    for i, line in enumerate(out):
        if f"%{ifname}:10400" in line:
            m = re.search(r"skmem:\(([^)]*)\)", line + " " + (out[i + 1] if i + 1 < len(out) else ""))
            if m:
                f = {x[0]: x[1:] for x in m.group(1).split(",") if x[:1].isalpha() and x[1:2].isdigit()}
                return f.get("t", ""), f.get("d", "")   # t = bytes queued for transmit, d = drops
    return "", ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--hz", type=float, default=10)
    ap.add_argument("--seconds", type=float, default=5400)
    ap.add_argument("--if", dest="ifname", default=detect_dongle() or "wlan0")
    ap.add_argument("--lan", default="enp15s0")
    a = ap.parse_args()
    stop = os.path.join(os.path.dirname(os.path.abspath(a.out)), "STOP-TXQ")
    cols = ["epoch", "signal", "phy", "mcs", "nss", "rx_drop_misc", "qbytes", "qpkts", "drops", "marks", "overlimit", "txq_packets",
            "udp_out", "udp_sndbuf_err", "dongle_tx_bytes", "lan_tx_bytes", "sk_wmem", "sk_drops"]
    start = time.time()
    with open(a.out, "w", buffering=1) as fh:
        fh.write(",".join(cols) + "\n")
        n = 0
        while time.time() - start < a.seconds and not os.path.exists(stop):
            t = time.time()
            r = station(a.ifname)
            uo, us = udp()
            sw, sd = vrlink_socket(a.ifname) if n % 2 == 0 else ("", "")
            r.update(epoch=f"{t:.3f}", udp_out=uo, udp_sndbuf_err=us,
                     dongle_tx_bytes=read_int(f"/sys/class/net/{a.ifname}/statistics/tx_bytes"),
                     lan_tx_bytes=read_int(f"/sys/class/net/{a.lan}/statistics/tx_bytes"), sk_wmem=sw, sk_drops=sd)
            fh.write(",".join(str(r[c]) for c in cols) + "\n")
            n += 1
            time.sleep(max(0.0, start + n / a.hz - time.time()))
    if os.path.exists(stop):
        os.remove(stop)


if __name__ == "__main__":
    main()
