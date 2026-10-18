#!/usr/bin/env python3
"""Look for one part of the picture going stale or black in an H.265 dump of the PC's encoder output.

    bitstream-eyes.py /tmp/vrlink_bitstream_XXXX.h265 [--tiles 4] [--fps 90] [--sheets DIR]

The stream is one tall picture per frame (1088x4352 here), four equal parts stacked. For every frame and every part
this measures the brightness (YAVG) and how much the part changed since the previous frame (YDIF, ffmpeg
signalstats on a 272-pixel-wide grey copy). It then reports

  stale:    a part that stops changing for a frame (YDIF under 15 % of its own median over the 30 frames around
            it) while it was in clear motion (that median over --motion, default 1.0) and at least one other part
            kept changing,
  repeated: the same, for all parts at once (the whole picture was sent twice),
  black:    a part darker than --black (default 12) in a frame where its own surroundings in time are brighter
            than twice that.

If the right eye freezes or blanks in the headset while this finds nothing, the fault is behind the encoder (network,
decoder, headset compositor). If it finds stale or black parts at those moments, the fault is on the PC before or in
the encoder. --sheets writes a small before / during / after picture for the first events.
"""
import argparse
import collections
import os
import re
import subprocess
import sys
import tempfile


def run_stats(path, tiles, fps, workdir):
    outs = []
    graph = [f"[0:v]split={tiles}" + "".join(f"[s{k}]" for k in range(tiles))]
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-r", str(fps), "-i", path]
    for k in range(tiles):
        out = os.path.join(workdir, f"tile{k}.txt")
        outs.append(out)
        graph.append(f"[s{k}]crop=iw:ih/{tiles}:0:{k}*ih/{tiles},scale=272:-2,format=gray,signalstats,"
                     f"metadata=mode=print:file={out}[o{k}]")
    cmd += ["-filter_complex", ";".join(graph)]
    for k in range(tiles):
        cmd += ["-map", f"[o{k}]", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ffmpeg failed: " + r.stderr[-800:])
    return outs


def parse(path):
    avg, dif = [], []
    for line in open(path):
        m = re.match(r"lavfi\.signalstats\.(YAVG|YDIF)=([\d.]+)", line)
        if m:
            (avg if m.group(1) == "YAVG" else dif).append(float(m.group(2)))
    return avg, dif


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--tiles", type=int, default=4)
    ap.add_argument("--fps", type=float, default=90)
    ap.add_argument("--motion", type=float, default=1.0)
    ap.add_argument("--black", type=float, default=12)
    ap.add_argument("--sheets", help="directory for before/during/after pictures of the first events")
    ap.add_argument("--keep", help="keep the per-frame statistics in this directory")
    a = ap.parse_args()

    workdir = a.keep or tempfile.mkdtemp(prefix="eyes-")
    os.makedirs(workdir, exist_ok=True)
    files = run_stats(a.file, a.tiles, a.fps, workdir)
    data = [parse(f) for f in files]
    n = min(len(d[0]) for d in data)
    if n < 3:
        sys.exit("no frames decoded")
    print(f"{a.file}: {n} frames = {n / a.fps:.1f} s at {a.fps:g} fps, {a.tiles} parts (0 = top)")
    for k in range(a.tiles):
        d = sorted(data[k][1][1:n])
        v = sorted(data[k][0][:n])
        print(f"  part {k}: brightness median {v[n // 2]:6.1f}; change per frame median {d[len(d) // 2]:5.2f}, p10 {d[len(d) // 10]:5.2f}, p90 {d[len(d) * 9 // 10]:5.2f}")

    def around(seq, i, w=15):
        v = sorted(seq[max(1, i - w):i] + seq[i + 1:i + 1 + w])
        return v[len(v) // 2] if v else 0.0

    stale, repeated, black = [], [], []
    for i in range(1, n - 2):      # the last frames of a dump can be cut off
        difs = [data[k][1][i] for k in range(a.tiles)]
        avgs = [data[k][0][i] for k in range(a.tiles)]
        frozen = [k for k in range(a.tiles) if around(data[k][1], i) >= a.motion and difs[k] < 0.15 * around(data[k][1], i)]
        if len(frozen) == a.tiles:
            repeated.append((i, frozen, difs))
        elif frozen:
            stale.append((i, frozen, difs))
        dark = [k for k in range(a.tiles) if avgs[k] < a.black and around(data[k][0], i) > 2 * a.black]
        if dark:
            black.append((i, dark, avgs))

    moving = sum(1 for i in range(1, n) if max(data[k][1][i] for k in range(a.tiles)) >= a.motion)
    minutes = n / a.fps / 60
    print(f"frames with clear motion somewhere: {moving} of {n - 1}")
    for name, ev in (("stale", stale), ("repeated", repeated), ("black", black)):
        per = collections.Counter(tuple(p) for _, p, _ in ev)
        print(f"{name} events: {len(ev)} ({len(ev) / minutes:.1f} per minute)" + ("; by parts: " + ", ".join(f"{list(k)}: {v}" for k, v in per.most_common()) if ev and name != "repeated" else ""))
        for i, parts, vals in ev[:12]:
            print(f"   frame {i:6d}  t={i / a.fps:7.2f} s  parts {parts}  values " + " ".join(f"{x:.2f}" for x in vals))

    if a.sheets and (stale or black or repeated):
        os.makedirs(a.sheets, exist_ok=True)
        for name, ev in (("stale", stale), ("black", black)):
            for i, parts, _ in ev[:4]:
                out = os.path.join(a.sheets, f"{name}-frame{i}.png")
                sel = f"select='between(n\\,{i - 1}\\,{i + 1})',scale=272:-2,tile=3x1"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-r", str(a.fps), "-i", a.file,
                                "-vf", sel, "-frames:v", "1", "-update", "1", out])
                print(f"   wrote {out} (frames {i - 1}, {i}, {i + 1} left to right)")


if __name__ == "__main__":
    main()
