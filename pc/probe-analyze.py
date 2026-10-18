#!/usr/bin/env python3
"""Analyze path-probe.sh dumps: delay, loss and "held then released together" events on each path.

    probe-analyze.py probe-dongle-1.csv[.xz] [probe-dongle-2.csv ...] [--list]

A hold is a run of five or more probes (sent 5 ms apart, so over about 20 ms or more) that arrived within 3 ms of
each other: the path delivered nothing for a while and then everything at once. How long the first probe of the
run was held is the length of the stall as the application sees it.

    --fine   also print the hold lengths in 5 ms bins
"""
import csv
import sys
from datetime import datetime


def open_text(path):
    """open a dump that may have been compressed with xz or gzip"""
    if str(path).endswith(".xz"):
        import lzma
        return lzma.open(path, "rt")
    if str(path).endswith(".gz"):
        import gzip
        return gzip.open(path, "rt")
    return open(path)


def load(path):
    recs = []
    with open_text(path) as fh:
        for row in csv.reader(fh):
            if not row or not row[0].isdigit():
                continue
            recs.append((int(row[0]), int(row[1]), int(row[2])))
    return recs


def analyze(path, show, fine=False):
    recs = load(path)
    if not recs:
        print(f"{path}: empty")
        return
    recs.sort(key=lambda r: r[2])
    sent = max(r[0] for r in recs) + 1
    base = min(r[2] - r[1] for r in recs)
    delays = sorted((r[2] - r[1] - base) / 1e6 for r in recs)
    q = lambda p: delays[min(len(delays) - 1, int(p / 100 * len(delays)))]
    t0, t1 = recs[0][2] / 1e9, recs[-1][2] / 1e9
    print(f"{path}: {datetime.fromtimestamp(t0):%H:%M:%S} .. {datetime.fromtimestamp(t1):%H:%M:%S}, sent {sent}, lost {sent - len(recs)} "
          f"({100 * (sent - len(recs)) / sent:.2f} %); delay ms p50 {q(50):.1f} p90 {q(90):.1f} p99 {q(99):.1f} p99.9 {q(99.9):.1f} max {delays[-1]:.1f}")
    over = [sum(1 for d in delays if d > x) for x in (11, 22, 50, 90)]
    print(f"   probes later than 11 / 22 / 50 / 90 ms: {over[0]} / {over[1]} / {over[2]} / {over[3]}  "
          f"({100 * over[0] / len(delays):.1f} / {100 * over[1] / len(delays):.1f} / {100 * over[2] / len(delays):.2f} / {100 * over[3] / len(delays):.2f} %)")
    holds = []          # (arrival time, held ms, probes released)
    run = [recs[0]]
    def close(run):
        if len(run) >= 3:
            span = (max(r[1] for r in run) - min(r[1] for r in run)) / 1e6
            if span >= 18:      # five probes at 5 ms spacing span 20 ms; allow for send jitter
                first = min(run, key=lambda r: r[1])
                holds.append((run[-1][2] / 1e9, (first[2] - first[1] - base) / 1e6, len(run)))
    for r in recs[1:]:
        if (r[2] - run[-1][2]) / 1e6 <= 3:
            run.append(r)
        else:
            close(run)
            run = [r]
    close(run)
    hist = [0] * 8
    edges = [20, 40, 60, 80, 95, 110, 150, 250]
    for _, held, _ in holds:
        for i in range(len(edges) - 1, -1, -1):
            if held >= edges[i]:
                hist[i] += 1
                break
    labels = ["20-40", "40-60", "60-80", "80-95", "95-110", "110-150", "150-250", ">250"]
    minutes = (t1 - t0) / 60
    print(f"   holds (5 or more probes arriving together): {len(holds)} ({len(holds) / minutes:.1f}/min); by how long the first probe was held, ms: "
          + "  ".join(f"{l}: {h}" for l, h in zip(labels, hist)))
    if fine:
        print("   5 ms bins from 20 ms: " + " ".join(str(sum(1 for _, held, _ in holds if lo <= held < lo + 5)) for lo in range(20, 135, 5))
              + f"; 135 ms or more: {sum(1 for _, held, _ in holds if held >= 135)}")
    if show:
        for t, held, n in holds:
            if held >= 60:
                print(f"     {datetime.fromtimestamp(t):%H:%M:%S.%f}"[:17] + f"  held {held:6.1f} ms, {n} probes released together")
    return holds


if __name__ == "__main__":
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not files:
        sys.exit(__doc__)
    for f in files:
        analyze(f, "--list" in sys.argv, "--fine" in sys.argv)
