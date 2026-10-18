#!/usr/bin/env python3
"""Joint view of the two probe streams from path-probe.sh: how often are BOTH paths late or lossy at the same time?

    probe-joint.py OUTDIR [--log headset-vrlink.txt] [--list]

vrlink sends every packet on both paths and the client can use whichever copy arrives first, so the stream only
suffers when both are bad together. Time is cut into 10 ms slots by send time; a slot's delay on a path is the
worst delay of the probes sent in it (lost probes count as infinitely late).
"""
import bisect
import csv
import glob
import os
import re
import sys
from datetime import datetime

SLOT = 0.010
INF = float("inf")


def open_text(path):
    """open a dump that may have been compressed with xz or gzip"""
    if str(path).endswith(".xz"):
        import lzma
        return lzma.open(path, "rt")
    if str(path).endswith(".gz"):
        import gzip
        return gzip.open(path, "rt")
    return open(path)


def load(paths):
    slots = {}
    lost = total = 0
    for p in sorted(paths):
        rows = []
        with open_text(p) as fh:
            for row in csv.reader(fh):
                if row and row[0].isdigit():
                    rows.append((int(row[0]), int(row[1]) / 1e9, int(row[2]) / 1e9))
        if not rows:
            continue
        base = min(r[2] - r[1] for r in rows)
        rows.sort()
        seqs = {r[0]: r for r in rows}
        first, last = rows[0], rows[-1]
        gap = (last[1] - first[1]) / max(1, last[0] - first[0])
        for seq in range(first[0], last[0] + 1):
            total += 1
            if seq in seqs:
                r = seqs[seq]
                t, d = r[1], (r[2] - r[1] - base) * 1000
            else:
                t, d = first[1] + (seq - first[0]) * gap, INF
                lost += 1
            k = int(t / SLOT)
            slots[k] = max(slots.get(k, 0.0), d)
    return slots, total, lost


def runs(keys):
    """group consecutive slot numbers"""
    out = []
    for k in sorted(keys):
        if out and k - out[-1][1] <= 1:
            out[-1][1] = k
        else:
            out.append([k, k])
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    d = args[0]
    log = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None
    if log in args:
        args.remove(log)
    dongle, dn, dl = load(glob.glob(os.path.join(d, "probe-dongle-*.csv*")))
    lan, ln, ll = load(glob.glob(os.path.join(d, "probe-lan-*.csv*")))
    common = sorted(set(dongle) & set(lan))
    if not common:
        sys.exit("no overlapping probe data")
    secs = len(common) * SLOT
    print(f"{d}: {datetime.fromtimestamp(common[0] * SLOT):%H:%M:%S} .. {datetime.fromtimestamp(common[-1] * SLOT):%H:%M:%S}, {secs / 60:.1f} min of slots with both probes")
    print(f"probes lost: dongle path {dl} of {dn} ({100 * dl / dn:.2f} %), LAN path {ll} of {ln} ({100 * ll / ln:.2f} %)")
    print(f"{'late by more than':>18s} {'dongle path':>12s} {'LAN path':>10s} {'both at once':>13s} {'if independent':>15s}   both-late events (count, longest)")
    for thr in (11, 22, 33, 50, 100):
        a = [k for k in common if dongle[k] > thr]
        b = [k for k in common if lan[k] > thr]
        both = [k for k in common if dongle[k] > thr and lan[k] > thr]
        r = runs(both)
        longest = max(((e - s + 1) * SLOT * 1000 for s, e in r), default=0)
        print(f"{thr:>15d} ms {100 * len(a) / len(common):11.2f}% {100 * len(b) / len(common):9.2f}% {100 * len(both) / len(common):12.3f}% "
              f"{100 * (len(a) / len(common)) * (len(b) / len(common)):14.3f}%   {len(r)} events, longest {longest:.0f} ms, {len(r) / (secs / 60):.1f}/min")
    best = sorted(min(dongle[k], lan[k]) for k in common)
    q = lambda p: best[min(len(best) - 1, int(p / 100 * len(best)))]
    print(f"delay of the faster copy, ms: p50 {q(50):.1f}  p90 {q(90):.1f}  p99 {q(99):.1f}  p99.9 {q(99.9):.1f}  max {best[-1]:.1f}")
    if log:
        pat = re.compile(r"^\w{3} (\w{3} \d\d \d{4} \d\d:\d\d:\d\d\.\d+) \[\w+\] - (.*)")
        events = []
        for line in open(log, errors="replace"):
            m = pat.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f").timestamp()
            if common[0] * SLOT <= ts <= common[-1] * SLOT:
                for name, needle in (("retry", "substantive retry"), ("abort", "too many packets were missing"), ("frame dropped", "was entirely dropped"), ("backtrack reset", "outside of backtrack window")):
                    if needle in m.group(2):
                        events.append((ts, name))
        # collapse log lines closer than 0.3 s into one moment
        moments = []
        for ts, name in sorted(events):
            if moments and ts - moments[-1][1] < 0.3:
                moments[-1][1] = ts
                moments[-1][2].add(name)
            else:
                moments.append([ts, ts, {name}])
        print(f"\nheadset log: {len(moments)} moments with a retry / dropped frame / abort in this window")
        hit = 0
        for s, e, names in moments:
            ks = range(int((s - 0.25) / SLOT), int(e / SLOT) + 1)
            wd = max((dongle.get(k, 0) for k in ks), default=0)
            wl = max((lan.get(k, 0) for k in ks), default=0)
            wb = max((min(dongle.get(k, 0), lan.get(k, 0)) for k in ks), default=0)
            hit += wb > 22
            if "--list" in sys.argv:
                f = lambda v: "lost" if v == INF else f"{v:.0f}"
                print(f"  {datetime.fromtimestamp(s):%H:%M:%S.%f}"[:14] + f"  worst delay in the 250 ms before: dongle {f(wd):>4s} ms, LAN {f(wl):>4s} ms, faster copy {f(wb):>4s} ms   {', '.join(sorted(names))}")
        print(f"  {hit} of {len(moments)} came within 250 ms after both paths were more than 22 ms late at once")
        # how often the same condition holds at an arbitrary moment (every 50 ms of the recording)
        with_loss = with_both = 0
        for s, e, _ in moments:
            ks = range(int((s - 0.25) / SLOT), int(e / SLOT) + 1)
            l = any(lan.get(k, 0) == INF for k in ks)
            dl = max((dongle.get(k, 0) for k in ks), default=0)
            with_loss += l
            with_both += l and dl > 30 and dl != INF
        n = base_loss = base_both = 0
        for k0 in range(common[0] + 25, common[-1], 5):
            ks = range(k0 - 25, k0 + 1)
            l = any(lan.get(k, 0) == INF for k in ks)
            dl = max((dongle.get(k, 0) for k in ks), default=0)
            n += 1
            base_loss += l
            base_both += l and dl > 30 and dl != INF
        print(f"  {with_loss} of {len(moments)} had a LAN probe lost in the 250 ms before "
              f"(true at an arbitrary moment {100 * base_loss / n:.1f} % of the time); "
              f"{with_both} of {len(moments)} had that and a dongle probe more than 30 ms late "
              f"(arbitrary moment: {100 * base_both / n:.1f} %)")


if __name__ == "__main__":
    main()
