#!/usr/bin/env python3
"""Find frames in an encoder dump where one eye shows a picture from 2-4 frames earlier while it should have moved on.

    bitstream-oldframe.py DUMP.h265 [--fps 90] [--from SECONDS] [--keep DIR] [--csv FILE]

Layout assumed (as seen in the dump): four square tiles stacked, 0 = left fovea, 1 = left wide, 2 = right fovea,
3 = right wide. Only the two wide tiles are compared. For every frame n and each eye, ffmpeg measures the mean
absolute difference between the 136x136 grey tile at n and the same tile at n-1, n-2, n-3 and n-4.

"Old picture": the tile differs clearly from the previous frame (over 1.5) but is nearly identical to the frame
k = 2, 3 or 4 frames back (under a quarter of that, and under 0.6): the eye jumped back in time.
"""
import argparse
import collections
import os
import re
import subprocess
import sys
import tempfile


def run(path, fps, out):
    g = ("[0:v]split=2[l][r];"
         "[l]crop=iw:ih/4:0:ih/4,scale=136:136,format=gray,split=5[l0][l1][l2][l3][l4];"
         "[r]crop=iw:ih/4:0:3*ih/4,scale=136:136,format=gray,split=5[r0][r1][r2][r3][r4]")
    maps = []
    for e in "lr":
        g += f";[{e}0]split=4[{e}a1][{e}a2][{e}a3][{e}a4]"
        for k in (1, 2, 3, 4):
            g += (f";[{e}{k}]tpad=start={k}[{e}d{k}];[{e}a{k}][{e}d{k}]blend=all_mode=difference:shortest=1,signalstats,"
                  f"metadata=mode=print:key=lavfi.signalstats.YAVG:file={out}/{e}{k}.txt[o{e}{k}]")
            maps += ["-map", f"[o{e}{k}]", "-f", "null", "-"]
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-r", str(fps), "-i", path, "-filter_complex", g] + maps,
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit("ffmpeg failed: " + r.stderr[-600:])


def load(path):
    return [float(m.group(1)) for m in re.finditer(r"YAVG=([\d.]+)", open(path).read())]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--fps", type=float, default=90)
    ap.add_argument("--from", dest="start", type=float, default=0, help="ignore the first SECONDS (menus, loading)")
    ap.add_argument("--keep", help="directory for the per-frame numbers (reused if it already has them)")
    ap.add_argument("--csv", help="write every event to this file")
    a = ap.parse_args()
    out = a.keep or tempfile.mkdtemp(prefix="oldframe-")
    os.makedirs(out, exist_ok=True)
    if not all(os.path.exists(f"{out}/{e}{k}.txt") for e in "lr" for k in (1, 2, 3, 4)):
        run(a.file, a.fps, out)
    d = {e: {k: load(f"{out}/{e}{k}.txt") for k in (1, 2, 3, 4)} for e in "lr"}
    n = min(len(d[e][k]) for e in "lr" for k in (1, 2, 3, 4))
    first = max(5, int(a.start * a.fps))
    minutes = (n - first) / a.fps / 60
    names = {"l": "left eye", "r": "right eye"}
    print(f"{a.file}: frames {first}..{n} ({minutes:.1f} min at {a.fps:g} fps)")
    events = {"l": [], "r": []}
    for e in "lr":
        for i in range(first, n):
            d1 = d[e][1][i]
            if d1 < 1.5:
                continue
            k = min((2, 3, 4), key=lambda k: d[e][k][i])
            if d[e][k][i] < 0.25 * d1 and d[e][k][i] < 0.6:
                events[e].append((i, k, d1, d[e][k][i]))
    for e in "lr":
        moving = sum(1 for i in range(first, n) if d[e][1][i] >= 1.5)
        by = collections.Counter(k for _, k, _, _ in events[e])
        print(f"{names[e]:9s}: {moving} frames in motion; old picture shown in {len(events[e])} frames ({len(events[e]) / minutes:.1f} per minute)"
              + ("; frames back: " + ", ".join(f"{k}: {v}" for k, v in sorted(by.items())) if events[e] else ""))
    other = {"l": "r", "r": "l"}
    if a.csv:
        with open(a.csv, "w") as fh:
            fh.write("eye,frame,seconds,frames_back,diff_to_previous_frame,diff_to_old_frame,other_eye_diff_to_previous_frame\n")
            for e in "lr":
                for i, k, d1, dk in events[e]:
                    fh.write(f"{names[e].split()[0]},{i},{i / a.fps:.3f},{k},{d1:.2f},{dk:.2f},{d[other[e]][1][i]:.2f}\n")
    for e in "lr":
        if events[e]:
            print(f"{names[e]} events (frame, time, frames back, difference to previous frame, difference to the old frame, other eye's difference to its previous frame):")
            for i, k, d1, dk in events[e][:40]:
                print(f"   {i:6d}  {i / a.fps:7.2f} s  {k}  {d1:5.2f}  {dk:4.2f}  {d[other[e]][1][i]:5.2f}")
            gaps = sorted((b[0] - a_[0]) / a.fps for a_, b in zip(events[e], events[e][1:]))
            if gaps:
                print(f"   time between events: median {gaps[len(gaps) // 2]:.1f} s, shortest {gaps[0]:.2f} s, longest {gaps[-1]:.1f} s")


if __name__ == "__main__":
    main()
