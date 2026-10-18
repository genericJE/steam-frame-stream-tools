#!/usr/bin/env python3
"""Summarize a capture.sh directory: radio link, bitrate, encoder load, and
SteamVR vrlink stream events (resets, connection state) on one timeline.

    analyze_capture.py <capture-dir> [--timeline]
"""
import csv
import re
import statistics as st
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


def num(row, key):
    v = row.get(key, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(values, p):
    values = sorted(values)
    if not values:
        return float("nan")
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def describe(name, values, unit=""):
    values = [v for v in values if v is not None]
    if not values:
        print(f"  {name:26s} (no data)")
        return
    print(f"  {name:26s} min {min(values):8.1f}  p5 {pct(values, 5):8.1f}  median {st.median(values):8.1f}"
          f"  p95 {pct(values, 95):8.1f}  max {max(values):8.1f} {unit}")


LOG_TS = re.compile(r"^\w{3} (\w{3} \d{2} \d{4} \d{2}:\d{2}:\d{2}\.\d{6}) ")
EVENTS = [
    ("reset_request", "Reset video stream because we had a stream reset request"),
    ("frame_id_mismatch", "Reset video stream because frame ID was not what we expected"),
    ("reset_skipped_pending", "Skipping ResetVideoStream because we have one pending"),
    ("session_params_recreated", "Created vkCreateVideoSessionParametersKHR"),
    ("connection_active", "Connection active at"),
    ("connection_inactive", "Connection inactive"),
    ("new_session", "New Session detected"),
    ("network_change", "Received event indicating network change"),
    ("texture_missing", "can't find texture"),
    ("fec_retry", "SVLFEC: substantive retry"),
    ("frame_dropped", "was entirely dropped"),
    ("forced_reset_timeout", "Timed out waiting for another accepted video packet"),
    ("retransmit_abort", "too many packets were missing"),
]


def parse_log_events(path, t0, t1):
    out = []
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        m = LOG_TS.match(line)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f").timestamp()
        if ts < t0 or ts > t1:
            continue
        for name, needle in EVENTS:
            if needle in line:
                out.append((ts, name, line[m.end():].strip()))
                break
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = Path(sys.argv[1])
    timeline = "--timeline" in sys.argv
    rows = list(csv.DictReader(open(d / "samples.csv")))
    if len(rows) < 2:
        sys.exit("not enough samples")
    t0, t1 = float(rows[0]["epoch"]), float(rows[-1]["epoch"])
    print(f"capture {d.name}: {len(rows)} samples, "
          f"{datetime.fromtimestamp(t0):%Y-%m-%d %H:%M:%S} .. {datetime.fromtimestamp(t1):%H:%M:%S} "
          f"({(t1 - t0) / 60:.1f} min)")

    # Per-interval rates from counters.
    tx_mbit, rx_mbit, enc_pct, cpu_pct = [], [], [], []
    for a, b in zip(rows, rows[1:]):
        dt = float(b["epoch"]) - float(a["epoch"])
        if dt <= 0:
            continue
        for key, dst, scale in (("if_tx_bytes", tx_mbit, 8 / 1e6), ("if_rx_bytes", rx_mbit, 8 / 1e6)):
            x, y = num(a, key), num(b, key)
            if x is not None and y is not None and y >= x:
                dst.append((y - x) * scale / dt)
        x, y = num(a, "vrserver_enc_ns"), num(b, "vrserver_enc_ns")
        if x is not None and y is not None and y >= x and a["vrserver_pid"] == b["vrserver_pid"]:
            enc_pct.append((y - x) / 1e9 / dt * 100)
        tx_, ty = num(a, "cpu_total_jiffies"), num(b, "cpu_total_jiffies")
        ix, iy = num(a, "cpu_idle_jiffies"), num(b, "cpu_idle_jiffies")
        if None not in (tx_, ty, ix, iy) and ty > tx_:
            cpu_pct.append(100 * (1 - (iy - ix) / (ty - tx_)))

    print("\nRadio link (dongle, as seen by the host):")
    describe("signal dBm", [num(r, "signal_dbm") for r in rows])
    describe("tx PHY rate Mbit/s", [num(r, "tx_mbit") for r in rows])
    describe("rx PHY rate Mbit/s", [num(r, "rx_mbit") for r in rows])
    mcs = Counter((r["tx_mcs"], r["tx_nss"]) for r in rows if r["tx_mcs"])
    total = sum(mcs.values()) or 1
    print("  tx HE-MCS/NSS share       " + ", ".join(
        f"MCS{m}x{n}: {c * 100 // total}%" for (m, n), c in mcs.most_common(6)))
    below = sum(1 for r in rows if (num(r, "signal_dbm") or 0) <= -70)
    print(f"  samples at or below -70 dBm (NetworkManager bgscan threshold): {below} / {len(rows)}")

    def delta(key):
        a, b = num(rows[0], key), num(rows[-1], key)
        return None if a is None or b is None else int(b - a)
    print("\nCounters over the capture:")
    for key in ("sta_rx_drop_misc", "sta_tx_retries", "sta_tx_failed", "sta_beacon_loss",
                "if_tx_dropped", "if_tx_errors", "udp_sndbuf_err", "udp_rcvbuf_err", "udp_in_err"):
        print(f"  {key:26s} {delta(key)}")
    sk = [num(r, "sk_drops") for r in rows if num(r, "sk_drops") is not None]
    if sk:
        print(f"  {'vrlink socket drops (max)':26s} {int(max(sk))}")

    print("\nThroughput and load:")
    describe("stream tx Mbit/s", tx_mbit)
    describe("uplink rx Mbit/s", rx_mbit)
    describe("vcn_busy_percent", [num(r, "vcn_busy_pct") for r in rows], "%")
    describe("vrserver enc engine", enc_pct, "% of wall")
    describe("gpu_busy_percent", [num(r, "gpu_busy_pct") for r in rows], "%")
    describe("cpu busy (all cores)", cpu_pct, "%")
    describe("PSI cpu some avg10", [num(r, "psi_cpu_some_avg10") for r in rows])

    # Events.
    logs = d / "steamvr-logs"
    ev = parse_log_events(logs / "driver_vrlink.follow.txt", t0, t1)
    counts = Counter(name for _, name, _ in ev)
    print("\nvrlink driver events inside the capture window:")
    for name, _ in EVENTS:
        if counts[name]:
            print(f"  {name:26s} {counts[name]}")
    if not ev:
        print("  (none)")

    waits = 0
    comp = logs / "vrcompositor.follow.txt"
    if comp.exists():
        for line in comp.read_text(errors="replace").splitlines():
            m = LOG_TS.match(line)
            if not m or "WaitForAcquire timed out" not in line:
                continue
            ts = datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f").timestamp()
            if t0 <= ts <= t1:
                s = re.search(r"\[(\d+) messages suppressed\]", line)
                waits += 1 + (int(s.group(1)) if s else 0)
    print(f"  compositor 'WaitForAcquire timed out' (incl. suppressed): {waits}")

    scans = Counter()
    iw = d / "iw_events.log"
    if iw.exists():
        for line in iw.read_text(errors="replace").splitlines():
            m = re.match(r"^[\d.]+: (\S+) \(phy #\d+\): scan started", line)
            if m:
                scans[m.group(1)] += 1
    print("  nl80211 scans started: " + (", ".join(f"{k}: {v}" for k, v in scans.items()) or "none"))

    if timeline:
        # One line per 10 s bucket with any stream event or weak signal.
        print("\nTimeline (10 s buckets with events):")
        buckets = {}
        for r in rows:
            b = int(float(r["epoch"]) // 10)
            buckets.setdefault(b, {"sig": [], "tx": [], "vcn": [], "ev": Counter()})
            for key, dst in (("signal_dbm", "sig"), ("tx_mbit", "tx"), ("vcn_busy_pct", "vcn")):
                v = num(r, key)
                if v is not None:
                    buckets[b][dst].append(v)
        for ts, name, _ in ev:
            b = int(ts // 10)
            buckets.setdefault(b, {"sig": [], "tx": [], "vcn": [], "ev": Counter()})["ev"][name] += 1
        for b in sorted(buckets):
            v = buckets[b]
            if not v["ev"]:
                continue
            sig = f"{min(v['sig']):.0f}" if v["sig"] else "?"
            phy = f"{min(v['tx']):.0f}" if v["tx"] else "?"
            vcn = f"{max(v['vcn']):.0f}" if v["vcn"] else "?"
            evs = ", ".join(f"{k} x{c}" for k, c in v["ev"].items())
            print(f"  {datetime.fromtimestamp(b * 10):%H:%M:%S}  min signal {sig} dBm  min PHY {phy}  max vcn {vcn}%  {evs}")


if __name__ == "__main__":
    main()
