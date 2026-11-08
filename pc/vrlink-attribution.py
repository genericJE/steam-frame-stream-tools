#!/usr/bin/env python3
"""Score a Steam Frame stream from the PC side only, using vrlink's [ProblemAttribution] reports.

    vrlink-attribution.py LOG [LOG...] [--since "YYYY-MM-DD HH:MM"] [--until "YYYY-MM-DD HH:MM"]
                          [--vs LOG [LOG...]] [--with] [--min-frames N]

Since SteamVR 2.18 the vrlink host driver logs, every ~15 s, how many frames it handled and what went wrong
with them: an "outcomes" line (what the player saw: NotEncoded, NotReceived, NotDecoded, NotDisplayed,
DisplayedLate, Mispredicted, DegateSkipped), a "causes" line (why: ClientRxLoss, RetransmitRequested,
StreamReset, NetworkSlow, ...) and one "<Outcome> (n) with: ..." line per outcome. The lines are in
~/.local/share/Steam/logs/vrserver.txt (prefixed "vrlink:") or driver_vrlink.txt depending on the build.
This tool sums them over a session and prints rates per 10,000 frames, so sessions of different length can
be compared without ssh access to the headset. --vs scores a second set of logs and prints both side by side.

Interval lines are summed; the "[ProblemAttribution session total]" block that vrlink prints at shutdown is
parsed separately and shown as a cross-check when it is present and no time window is used.

Method and first numbers: https://github.com/ValveSoftware/SteamOS/issues/2952#issuecomment-6698362057
"""
import argparse
import collections
import re
import sys
from datetime import datetime

TS = re.compile(r"^\w{3} (\w{3} \d\d \d{4} \d\d:\d\d:\d\d)\.\d+ ")
ATTR = re.compile(r"\[ProblemAttribution( session total)?\] ?(.*)$")
PAIRS = re.compile(r"([A-Za-z]+) (\d+)")
OUTCOME_ORDER = ["NotEncoded", "NotReceived", "NotDecoded", "NotDisplayed", "DisplayedLate", "Mispredicted",
                 "DegateSkipped"]


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else "%Y-%m-%d %H:%M")


class Arm:
    def __init__(self):
        self.frames = 0
        self.intervals = 0
        self.outcomes = collections.Counter()
        self.causes = collections.Counter()
        self.with_ = collections.defaultdict(collections.Counter)
        self.first = self.last = None
        self.total = None          # dict(frames, outcomes, causes) from the "session total" block
        self.files = []

    def add(self, kind, text, ts):
        if kind == "frames":
            m = re.match(r"(\d+) frames, (no problems|outcomes: ?(.*))", text)
            if not m:
                return
            self.frames += int(m.group(1))
            self.intervals += 1
            for k, v in PAIRS.findall(m.group(3) or ""):
                self.outcomes[k] += int(v)
            if ts:
                self.first = self.first or ts
                self.last = ts
        elif kind == "causes":
            for k, v in PAIRS.findall(text):
                self.causes[k] += int(v)
        elif kind == "with":
            m = re.match(r"([A-Za-z]+) \((\d+)\) with: ?(.*)", text)
            if m:
                for k, v in PAIRS.findall(m.group(3)):
                    self.with_[m.group(1)][k] += int(v)


def classify(text):
    if re.match(r"\d+ frames,", text):
        return "frames"
    if text.startswith("causes:"):
        return "causes"
    if re.match(r"[A-Za-z]+ \(\d+\) with:", text):
        return "with"
    return None


def load(files, since, until):
    arm, total = Arm(), Arm()
    for f in files:
        arm.files.append(f)
        try:
            fh = open(f, errors="replace")
        except OSError as e:
            sys.exit(f"cannot read {f}: {e}")
        with fh:
            for line in fh:
                m = ATTR.search(line)
                if not m:
                    continue
                ts = None
                t = TS.match(line)
                if t:
                    ts = datetime.strptime(t.group(1), "%b %d %Y %H:%M:%S")
                    if (since and ts < since) or (until and ts > until):
                        continue
                text = m.group(2).strip()
                kind = classify(text)
                if kind:
                    (total if m.group(1) else arm).add(kind, text, ts)
    if total.frames and not (since or until):
        arm.total = total
    return arm


def rate(n, frames):
    return 10000.0 * n / frames if frames else 0.0


def fmt_ratio(a, b):
    if a == 0 and b == 0:
        return ""
    if b == 0:
        return "gone"
    if a == 0:
        return "new"
    r = b / a
    return f"{r:.2f}x" if r < 1 else f"+{100 * (r - 1):.0f}%"


def header(arm, label):
    span = ""
    if arm.first and arm.last:
        mins = (arm.last - arm.first).total_seconds() / 60
        span = f", {arm.first:%Y-%m-%d %H:%M} to {arm.last:%H:%M} ({mins:.0f} min)"
    print(f"{label}: {arm.frames} frames in {arm.intervals} intervals{span}")
    for f in arm.files:
        print(f"  {f}")


def print_table(title, keys, a, b, getter):
    if not keys:
        return
    print(f"\n{title:30s} {'per 10k':>10s}" + (f" {'per 10k':>10s} {'change':>8s}" if b else "") + f" {'count':>8s}")
    for k in keys:
        va, vb = getter(a, k), getter(b, k) if b else 0
        line = f"  {k:28s} {rate(va, a.frames):10.1f}"
        if b:
            line += f" {rate(vb, b.frames):10.1f} {fmt_ratio(rate(va, a.frames), rate(vb, b.frames)):>8s}"
        line += f" {va:8d}" + (f"/{vb}" if b else "")
        print(line)


def ordered(counter_a, counter_b=None):
    keys = set(counter_a) | set(counter_b or {})
    known = [k for k in OUTCOME_ORDER if k in keys]
    rest = sorted(keys - set(known), key=lambda k: -(counter_a[k] + (counter_b or {}).get(k, 0)))
    return known + rest


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 3)[3])
    ap.add_argument("logs", nargs="+", help="vrserver.txt / driver_vrlink.txt (and .previous.txt) of one session")
    ap.add_argument("--vs", nargs="+", metavar="LOG", help="logs of a second session to compare against")
    ap.add_argument("--since", type=parse_ts, help='only intervals at or after "YYYY-MM-DD HH:MM[:SS]"')
    ap.add_argument("--until", type=parse_ts, help='only intervals at or before "YYYY-MM-DD HH:MM[:SS]"')
    ap.add_argument("--with", dest="with_", action="store_true", help="also print which causes each outcome came with")
    ap.add_argument("--min-frames", type=int, default=1000, help="warn below this many frames (default 1000)")
    args = ap.parse_args()

    a = load(args.logs, args.since, args.until)
    b = load(args.vs, args.since, args.until) if args.vs else None
    if a.frames == 0:
        sys.exit("no [ProblemAttribution] interval lines found (SteamVR 2.18+ writes them to vrserver.txt or driver_vrlink.txt)")
    header(a, "A")
    if b:
        header(b, "B")
    for arm, label in ((a, "A"), (b, "B")):
        if arm and arm.frames < args.min_frames:
            print(f"warning: {label} has only {arm.frames} frames, rates will be noisy")

    print_table("outcomes (what the player saw)", ordered(a.outcomes, b and b.outcomes), a, b, lambda x, k: x.outcomes[k])
    cause_keys = sorted(set(a.causes) | set(b.causes if b else {}), key=lambda k: -(a.causes[k] + (b.causes[k] if b else 0)))
    print_table("causes", cause_keys, a, b, lambda x, k: x.causes[k])

    if args.with_:
        for arm, label in ((a, "A"), (b, "B")):
            if not arm:
                continue
            for outcome in ordered(arm.with_):
                print(f"\n{label} {outcome} ({arm.outcomes[outcome]}) came with, per 10k frames:")
                for k, v in arm.with_[outcome].most_common():
                    print(f"  {k:28s} {rate(v, arm.frames):10.1f} {v:8d}")

    for arm, label in ((a, "A"), (b, "B")):
        if arm and arm.total:
            t = arm.total
            diff = t.frames - arm.frames
            print(f"\n{label} session-total block: {t.frames} frames ({diff:+d} vs summed intervals)")
            for k in ordered(t.outcomes):
                if t.outcomes[k] != arm.outcomes[k]:
                    print(f"  {k}: total block {t.outcomes[k]} vs summed {arm.outcomes[k]}")


if __name__ == "__main__":
    main()
