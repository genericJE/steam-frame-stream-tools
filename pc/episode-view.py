#!/usr/bin/env python3
"""Show both paths around one moment of a stream at 50 ms resolution, from four recordings taken at the same time:

    --pc      txq-sampler.py output (PC: bytes handed to the dongle / LAN, Wi-Fi queue, signal)
    --hs      frame-rx-sampler.py output (headset: bytes received on wlanap / wlan0)
    --probe-dongle / --probe-lan   path-probe.sh dumps (per-probe one-way delay)
    --log     headset Steam/logs/vrlink.txt

    episode-view.py --at "2026-10-04 11:20:31.5" [--before 1] [--after 1] --pc ... --hs ... --log ...

Probe columns: worst delay of the probes sent in that 50 ms slot, "lost" if any did not arrive, "-" if no data.
Log column: R FEC retry, D frame entirely dropped, B backtrack reset, A abort, I IDR request, H decoder hard reset,
M late metadata, U audio underflow.
"""
import argparse
import bisect
import csv
import re
from datetime import datetime

SLOT = 0.05
MARKS = [("R", "substantive retry"), ("D", "was entirely dropped"), ("B", "outside of backtrack window"),
         ("A", "too many packets were missing"), ("I", "Asking remote side for a new IFrame"), ("H", "HardReset: Called"),
         ("M", "received late metadata"), ("U", "CAudioJitterBuffer underflow")]
FILE = re.compile(r"^\w{3} (\w{3} \d\d \d{4} \d\d:\d\d:\d\d\.\d+) \[\w+\] - (.*)")


def rate_series(path, tkey, keys):
    rows = list(csv.DictReader(open(path)))
    out = []
    for a, b in zip(rows, rows[1:]):
        try:
            ta, tb = float(a[tkey]), float(b[tkey])
            vals = {k: (float(b[k]) - float(a[k])) for k in keys}
        except ValueError:
            continue
        out.append((ta, tb, vals, b))
    return out


def overlap_rate(series, key, s0, s1):
    """bytes counted in [s0, s1), assuming a constant rate inside each sample interval"""
    total, covered = 0.0, 0.0
    for ta, tb, vals, _ in series:
        if tb <= s0 or ta >= s1:
            continue
        frac = (min(tb, s1) - max(ta, s0)) / (tb - ta)
        total += vals[key] * frac
        covered += min(tb, s1) - max(ta, s0)
    return None if covered < (s1 - s0) * 0.5 else total * 8 / (s1 - s0) / 1e6


def open_text(path):
    """open a dump that may have been compressed with xz or gzip"""
    if str(path).endswith(".xz"):
        import lzma
        return lzma.open(path, "rt")
    if str(path).endswith(".gz"):
        import gzip
        return gzip.open(path, "rt")
    return open(path)


def load_probes(paths):
    recs, sent_ranges = [], []
    for p in paths or []:
        rows = []
        with open_text(p) as fh:
            for row in csv.reader(fh):
                if row and row[0].isdigit():
                    rows.append((int(row[0]), int(row[1]) / 1e9, int(row[2]) / 1e9))
        if not rows:
            continue
        base = min(r[2] - r[1] for r in rows)
        rows.sort()
        # send times of lost probes are interpolated from their neighbours
        seqs = {r[0]: r for r in rows}
        first, last = rows[0], rows[-1]
        gap = (last[1] - first[1]) / max(1, last[0] - first[0])
        for seq in range(first[0], last[0] + 1):
            if seq in seqs:
                r = seqs[seq]
                recs.append((r[1], (r[2] - r[1] - base) * 1000))
            else:
                recs.append((first[1] + (seq - first[0]) * gap, None))
    recs.sort(key=lambda r: r[0])
    return recs


def probe_cell(recs, times, s0, s1):
    if not recs:
        return "    -"
    i, j = bisect.bisect_left(times, s0), bisect.bisect_left(times, s1)
    if i == j:
        return "    -"
    sel = recs[i:j]
    lost = sum(1 for r in sel if r[1] is None)
    worst = max((r[1] for r in sel if r[1] is not None), default=None)
    if lost:
        return f"{'lost' + str(lost):>5s}" if worst is None else f"{worst:3.0f}+L"
    return f"{worst:5.0f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", required=True)
    ap.add_argument("--before", type=float, default=1.0)
    ap.add_argument("--after", type=float, default=1.0)
    ap.add_argument("--pc")
    ap.add_argument("--hs")
    ap.add_argument("--probe-dongle", nargs="*")
    ap.add_argument("--probe-lan", nargs="*")
    ap.add_argument("--log")
    a = ap.parse_args()
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in a.at else "%Y-%m-%d %H:%M:%S"
    c = datetime.strptime(a.at, fmt).timestamp()

    pc = rate_series(a.pc, "epoch", ["dongle_tx_bytes", "lan_tx_bytes"]) if a.pc else []
    hs = rate_series(a.hs, "epoch", ["ap_rx_bytes", "sta_rx_bytes"]) if a.hs else []
    pd, pl = load_probes(a.probe_dongle), load_probes(a.probe_lan)
    pdt, plt = [r[0] for r in pd], [r[0] for r in pl]
    marks = {}
    if a.log:
        for line in open(a.log, errors="replace"):
            m = FILE.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f").timestamp()
            if c - a.before <= ts <= c + a.after:
                for ch, needle in MARKS:
                    if needle in m.group(2):
                        marks.setdefault(int((ts - (c - a.before)) / SLOT), []).append(ch)
                        break

    print("                 PC sends, Mbit/s   headset receives, Mbit/s   probe delay, ms    PC dongle queue")
    print("time            dongle     LAN      wlanap    wlan0          dongle    LAN      pkts  signal  PHY   log")
    n = int(round((a.before + a.after) / SLOT))
    for k in range(n):
        s0 = c - a.before + k * SLOT
        s1 = s0 + SLOT
        cell = lambda v: "    -" if v is None else f"{v:5.0f}"
        q = sig = phy = ""
        for ta, tb, _, row in pc:
            if s0 < tb <= s1:
                q, sig, phy = row["qpkts"], row["signal"], f"{float(row['phy']):.0f}x{row['nss']}" if row["phy"] else ""
        mk = marks.get(k, [])
        compact = "".join(f"{ch}{mk.count(ch) if mk.count(ch) > 1 else ''}" for ch, _ in MARKS if ch in mk)
        print(f"{datetime.fromtimestamp(s1):%H:%M:%S.%f}"[:12] + f"   {cell(overlap_rate(pc, 'dongle_tx_bytes', s0, s1))}   {cell(overlap_rate(pc, 'lan_tx_bytes', s0, s1))}"
              f"      {cell(overlap_rate(hs, 'ap_rx_bytes', s0, s1))}    {cell(overlap_rate(hs, 'sta_rx_bytes', s0, s1))}"
              f"          {probe_cell(pd, pdt, s0, s1)}  {probe_cell(pl, plt, s0, s1)}      {q:>4s}  {sig:>5s}  {phy:>6s}  {compact}")


if __name__ == "__main__":
    main()
