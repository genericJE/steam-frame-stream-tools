#!/usr/bin/env python3
"""Summarize a headset-side vrlink client log (journal export or Steam/logs/vrlink.txt) for one time window.

    vrlink-episodes.py LOGFILE "YYYY-MM-DD HH:MM[:SS]" "YYYY-MM-DD HH:MM[:SS]" [--list] [--gap SECONDS]

The client writes one "too many packets were missing" line per packet it gives up on, so one bad moment can
produce hundreds of lines. This tool groups those lines into episodes (lines closer than --gap seconds, default 1)
and reports rates per minute of stream, so sessions of different length can be compared.
"""
import argparse
import collections
import re
import sys
from datetime import datetime

PATTERNS = [
    ("abort", "too many packets were missing"),
    ("unrecoverable", "ExperiencedAnUnrecoverableError"),
    ("fec_retry", "substantive retry"),
    ("frame_dropped", "was entirely dropped"),
    ("backtrack_reset", "outside of backtrack window"),
    ("idr_request", "Asking remote side for a new IFrame"),
    ("decoder_hard_reset", "HardReset: Called"),
    ("late_metadata", "received late metadata"),
    ("audio_underflow", "CAudioJitterBuffer underflow"),
    ("frame_id_mismatch", "iRXFrameID"),
]
# "Oct 01 18:36:12.385385 frame vrlink[123]: - text"  (journalctl -o short-precise; year taken from the window)
JOURNAL = re.compile(r"^(\w{3} \d\d \d\d:\d\d:\d\d\.\d+) \S+ vrlink\[\d+\]: - (.*)")
# "Sun Oct 04 2026 09:18:11.286892 [Info] - text"      (Steam/logs/vrlink.txt)
FILE = re.compile(r"^\w{3} (\w{3} \d\d \d{4} \d\d:\d\d:\d\d\.\d+) \[\w+\] - (.*)")


def parse_time(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"bad time: {s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logfile")
    ap.add_argument("since", type=parse_time)
    ap.add_argument("until", type=parse_time)
    ap.add_argument("--gap", type=float, default=1.0, help="seconds without an abort line that end an episode")
    ap.add_argument("--list", action="store_true", help="print every episode")
    a = ap.parse_args()
    path, since, until, gap = a.logfile, a.since, a.until, a.gap

    events, retry_packets, lines = [], 0, 0
    for line in open(path, errors="replace"):
        m = FILE.match(line)
        if m:
            ts = datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f")
        else:
            m = JOURNAL.match(line)
            if not m:
                continue
            ts = datetime.strptime(f"{since.year} {m.group(1)}", "%Y %b %d %H:%M:%S.%f")
        if not since <= ts <= until:
            continue
        lines += 1
        text = m.group(2)
        for name, needle in PATTERNS:
            if needle in text:
                events.append((ts, name))
                if name == "fec_retry":
                    p = re.search(r"\((\d+) packets\)", text)
                    retry_packets += int(p.group(1)) if p else 0
                break
    events.sort()

    minutes = (until - since).total_seconds() / 60
    totals = collections.Counter(name for _, name in events)
    episodes = []  # [start, end, Counter]
    for ts, name in events:
        if name not in ("abort", "unrecoverable"):
            continue
        if episodes and (ts - episodes[-1][1]).total_seconds() < gap:
            episodes[-1][1] = ts
            episodes[-1][2][name] += 1
        else:
            episodes.append([ts, ts, collections.Counter({name: 1})])

    print(f"{path}")
    print(f"window {since:%Y-%m-%d %H:%M:%S} .. {until:%H:%M:%S} ({minutes:.1f} min), {lines} log lines")
    print(f"{'event':22s} {'count':>7s} {'per minute':>11s}")
    for name, _ in PATTERNS:
        print(f"{name:22s} {totals[name]:7d} {totals[name] / minutes:11.2f}")
    print(f"{'packets re-requested':22s} {retry_packets:7d} {retry_packets / minutes:11.2f}")
    print(f"{'abort episodes':22s} {len(episodes):7d} {len(episodes) / minutes:11.2f}   (abort/unrecoverable lines closer than {gap:g} s)")
    busy = {ts.replace(second=0, microsecond=0) for ts, name in events if name in ("abort", "unrecoverable")}
    retry_minutes = {ts.replace(second=0, microsecond=0) for ts, name in events if name == "fec_retry"}
    print(f"minutes with an abort: {len(busy)} of {minutes:.0f}; minutes with a FEC retry: {len(retry_minutes)} of {minutes:.0f}")
    if episodes:
        durs = sorted((e - s).total_seconds() for s, e, _ in episodes)
        gaps = sorted((b[0] - a[1]).total_seconds() for a, b in zip(episodes, episodes[1:]))
        print(f"episode length: median {durs[len(durs) // 2]:.2f} s, longest {durs[-1]:.2f} s; "
              f"episodes longer than 1 s: {sum(1 for d in durs if d > 1)}")
        if gaps:
            print(f"time between episodes: median {gaps[len(gaps) // 2]:.1f} s, longest {gaps[-1]:.1f} s")
    if a.list:
        print("\nstart          length_s  aborts  unrecoverable  other events from 2 s before to 3 s after")
        for s, e, c in episodes:
            other = collections.Counter(n for t, n in events if n not in ("abort", "unrecoverable")
                                        and -2 <= (t - s).total_seconds() and (t - e).total_seconds() <= 3)
            print(f"{s:%H:%M:%S.%f}"[:12], f"{(e - s).total_seconds():9.2f} {c['abort']:7d} {c['unrecoverable']:14d}  "
                  + ", ".join(f"{k} {v}" for k, v in other.most_common()))


if __name__ == "__main__":
    main()
