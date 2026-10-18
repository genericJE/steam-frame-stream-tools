#!/usr/bin/env python3
"""Minimal UDP link tester (no dependencies), for measuring loss, loss bursts and
queueing delay on the Steam Frame dongle link in either direction.

  receiver:  udptest.py rx --port 5999 [--punch SENDER_IP] --seconds 14 [--dump FILE]
  sender:    udptest.py tx --port 5999 [--dest IP | --wait-punch] --mbit 100 --seconds 10
                           [--size 1400] [--burst-hz 90]

--punch / --wait-punch let the receiver open the path through its own stateful
firewall: it sends hello packets to the sender, which replies to that address.
--burst-hz N sends each 1/N s worth of packets back-to-back (video-frame-like);
0 paces packets evenly. Receiver prints one JSON summary line.
"""
import argparse, json, socket, struct, sys, time

HDR = struct.Struct("!4sIQII")  # magic, seq, send_ns, burst_id, idx_in_burst
MAGIC = b"SFLT"
END = b"SFLE"


def tx(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
    if a.bind_dev:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, a.bind_dev.encode() + b"\0")
    s.bind(("0.0.0.0", a.port))
    socks = [s]
    for i in range(1, a.flows):
        x = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        x.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        if a.bind_dev:
            x.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, a.bind_dev.encode() + b"\0")
        x.bind(("0.0.0.0", a.port + i))
        socks.append(x)
    nf = len(socks)
    if a.wait_punch:
        s.settimeout(30)
        data, dest = s.recvfrom(64)
        s.settimeout(None)
    else:
        dest = (a.dest, a.port)
    pps = a.mbit * 1e6 / 8 / a.size
    total = int(pps * a.seconds)
    pad = b"\0" * (a.size - HDR.size)
    sent = 0
    t0 = time.perf_counter_ns()
    if a.burst_hz > 0:
        per_burst = max(1, round(pps / a.burst_hz))
        period = 1e9 / a.burst_hz
        nb = int(a.seconds * a.burst_hz)
        for b in range(nb):
            target = t0 + b * period
            while True:
                now = time.perf_counter_ns()
                if now >= target:
                    break
                if target - now > 300_000:
                    time.sleep(0.0001)
            if a.burst_mbit > 0:
                # pace packets inside the burst at this instantaneous rate
                gap_in = a.size * 8 / (a.burst_mbit * 1e6) * 1e9
                tb = time.perf_counter_ns()
                for i in range(per_burst):
                    while time.perf_counter_ns() < tb + i * gap_in:
                        pass
                    s.sendto(HDR.pack(MAGIC, sent, time.time_ns(), b, i) + pad, dest)
                    sent += 1
            else:
                for i in range(per_burst):
                    socks[sent % nf].sendto(HDR.pack(MAGIC, sent, time.time_ns(), b, i) + pad, dest)
                    sent += 1
    else:
        gap = 1e9 / pps
        for i in range(total):
            target = t0 + i * gap
            while True:
                now = time.perf_counter_ns()
                if now >= target:
                    break
                if target - now > 300_000:      # low rates: sleep instead of spinning a core
                    time.sleep((target - now - 200_000) / 1e9)
            socks[sent % nf].sendto(HDR.pack(MAGIC, sent, time.time_ns(), 0, 0) + pad, dest)
            sent += 1
    dur = (time.perf_counter_ns() - t0) / 1e9
    time.sleep(0.3)
    for _ in range(20):
        s.sendto(HDR.pack(END, sent, time.time_ns(), 0, 0), dest)
        time.sleep(0.02)
    print(json.dumps({"role": "tx", "sent": sent, "seconds": round(dur, 3),
                      "mbit": round(sent * a.size * 8 / dur / 1e6, 1), "size": a.size,
                      "burst_hz": a.burst_hz, "dest": dest[0]}))


def pctl(v, p):
    return v[min(len(v) - 1, int(p / 100 * len(v)))] if v else None


def rx(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    s.bind(("0.0.0.0", a.port))
    if a.punch:
        for k in range(3):
            for i in range(a.flows):
                s.sendto(b"hello", (a.punch, a.port + i))
    s.settimeout(0.5)
    buf = bytearray(65536)
    recs = []
    sent_total = None
    deadline = time.monotonic() + a.seconds
    while time.monotonic() < deadline:
        try:
            n = s.recv_into(buf)
        except socket.timeout:
            if sent_total is not None:
                break
            continue
        now = time.time_ns()
        if n < HDR.size:
            continue
        magic, seq, send_ns, burst, idx = HDR.unpack_from(buf)
        if magic == END:
            sent_total = seq
            continue
        if magic == MAGIC:
            recs.append((seq, send_ns, now, burst, idx))
    if not recs:
        print(json.dumps({"role": "rx", "received": 0}))
        return
    if sent_total is None:
        sent_total = max(r[0] for r in recs) + 1
    got = len(recs)
    seen = set()
    dup = reorder = 0
    last = -1
    for seq, *_ in recs:
        if seq in seen:
            dup += 1
        seen.add(seq)
        if seq < last:
            reorder += 1
        last = max(last, seq)
    missing = sorted(set(range(sent_total)) - seen)
    bursts = []
    if missing:
        start = prev = missing[0]
        for m in missing[1:]:
            if m != prev + 1:
                bursts.append(prev - start + 1)
                start = m
            prev = m
        bursts.append(prev - start + 1)
    bursts.sort()
    delays = [r[2] - r[1] for r in recs]
    base = min(delays)
    rel = sorted((d - base) / 1e6 for d in delays)
    arr = [r[2] for r in recs]
    gaps = sorted((b - a_) / 1e6 for a_, b in zip(arr, arr[1:]))
    out = {
        "role": "rx", "sent": sent_total, "received": got - dup,
        "loss_pct": round(100 * len(missing) / sent_total, 3) if sent_total else None,
        "dup": dup, "reordered": reorder,
        "loss_events": len(bursts),
        "loss_burst_median": pctl(bursts, 50), "loss_burst_p90": pctl(bursts, 90), "loss_burst_max": bursts[-1] if bursts else 0,
        "delay_ms_p50": round(pctl(rel, 50), 2), "delay_ms_p95": round(pctl(rel, 95), 2),
        "delay_ms_p99": round(pctl(rel, 99), 2), "delay_ms_max": round(rel[-1], 2),
        "gap_ms_max": round(gaps[-1], 2) if gaps else None,
        "gaps_over_20ms": sum(1 for g in gaps if g > 20), "gaps_over_50ms": sum(1 for g in gaps if g > 50),
        "rx_seconds": round((arr[-1] - arr[0]) / 1e9, 3),
        "rx_mbit": round(sum(1 for _ in recs) * a.size_hint * 8 / max(1e-9, (arr[-1] - arr[0]) / 1e9) / 1e6, 1),
    }
    # video-frame view: a burst (frame) is late if its last packet lands > X ms after its first was sent
    frames = {}
    for seq, send_ns, now, burst, idx in recs:
        f = frames.setdefault(burst, [send_ns, now, 0])
        f[0] = min(f[0], send_ns); f[1] = max(f[1], now); f[2] += 1
    if len(frames) > 1:
        fl = sorted((v[1] - v[0] - base) / 1e6 for v in frames.values())
        out.update({"frames": len(frames), "frame_ms_p50": round(pctl(fl, 50), 2), "frame_ms_p95": round(pctl(fl, 95), 2),
                    "frame_ms_p99": round(pctl(fl, 99), 2), "frame_ms_max": round(fl[-1], 2),
                    "frames_over_11ms": sum(1 for x in fl if x > 11.1), "frames_over_22ms": sum(1 for x in fl if x > 22.2)})
    per_burst = max(r[4] for r in recs) + 1
    if per_burst > 1 and missing:
        # where inside a burst the losses fall (10 equal slices of the burst)
        hist = [0] * 10
        for m in missing:
            hist[min(9, (m % per_burst) * 10 // per_burst)] += 1
        out["burst_packets"] = per_burst
        out["lost_by_burst_position"] = hist
        hit = len({m // per_burst for m in missing})
        out["bursts_with_loss"] = hit
    if a.timeline:
        # per-bin worst delay and loss, to see when stalls happen
        t_first = recs[0][1]
        nb = {}
        for seq, send_ns, now, burst, idx in recs:
            b = int((send_ns - t_first) / 1e9 / a.timeline)
            e = nb.setdefault(b, [0, 0.0])
            e[0] += 1
            e[1] = max(e[1], (now - send_ns - base) / 1e6)
        out["timeline_bin_s"] = a.timeline
        out["timeline_rx_count"] = [nb.get(i, [0, 0])[0] for i in range(max(nb) + 1)]
        out["timeline_max_delay_ms"] = [round(nb.get(i, [0, 0])[1], 1) for i in range(max(nb) + 1)]
    print(json.dumps(out))
    if a.dump:
        with open(a.dump, "w") as f:
            f.write("seq,send_ns,recv_ns,burst,idx\n")
            for r in recs:
                f.write(",".join(map(str, r)) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("role", choices=["tx", "rx"])
    p.add_argument("--port", type=int, default=5999)
    p.add_argument("--dest")
    p.add_argument("--wait-punch", action="store_true")
    p.add_argument("--punch")
    p.add_argument("--mbit", type=float, default=100)
    p.add_argument("--seconds", type=float, default=10)
    p.add_argument("--size", type=int, default=1400)
    p.add_argument("--size-hint", type=int, default=1400)
    p.add_argument("--burst-hz", type=float, default=0)
    p.add_argument("--burst-mbit", type=float, default=0, help="pace packets inside each burst at this rate (0 = back-to-back)")
    p.add_argument("--bind-dev", help="send through this interface (SO_BINDTODEVICE)")
    p.add_argument("--flows", type=int, default=1, help="spread packets over this many UDP source ports (defeats per-flow A-MSDU aggregation)")
    p.add_argument("--dump")
    p.add_argument("--timeline", type=float, default=0, help="bin size in seconds for a delay/loss timeline")
    a = p.parse_args()
    (tx if a.role == "tx" else rx)(a)


if __name__ == "__main__":
    main()
