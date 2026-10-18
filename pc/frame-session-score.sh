#!/usr/bin/env bash
# Score a streaming session from the headset's vrlink client log: packet-loss events per minute.
#   frame-session-score.sh "2026-10-01 18:31" "2026-10-01 19:14"
#   frame-session-score.sh "2026-10-04 09:15"            # until now
# Reads the journal (identifier vrlink) and, for the current boot, ~/.local/share/Steam/logs/vrlink*.txt.
set -eu
SINCE=${1:?usage: frame-session-score.sh "YYYY-MM-DD HH:MM" ["YYYY-MM-DD HH:MM"]}; UNTIL=${2:-}
ssh -o BatchMode=yes -o ConnectTimeout=8 steamos@frame python3 - "$(printf %q "$SINCE")" "$(printf %q "$UNTIL")" <<'PY'
import sys, re, subprocess, glob, os, collections
from datetime import datetime
since = datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M")
until = datetime.strptime(sys.argv[2], "%Y-%m-%d %H:%M") if len(sys.argv) > 2 and sys.argv[2] else datetime.now()
lines = {}   # (timestamp, text) de-duplicated across sources
def add(ts, text):
    if since <= ts <= until:
        lines[(ts, text)] = 1
# journal: "Oct 01 18:36:12.385385 frame vrlink[123]: - text"
out = subprocess.run(["journalctl", "-t", "vrlink", "--since", since.strftime("%Y-%m-%d %H:%M:%S"),
                      "--until", until.strftime("%Y-%m-%d %H:%M:%S"), "--no-pager", "-o", "short-iso-precise"],
                     capture_output=True, text=True).stdout
for l in out.splitlines():
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)\S* \S+ vrlink\[\d+\]: - (.*)", l)
    if m:
        add(datetime.strptime(m.group(1)[:26], "%Y-%m-%dT%H:%M:%S.%f"), m.group(2))
# files: "Sun Oct 04 2026 09:18:11.286892 [Info] - text"
for f in glob.glob(os.path.expanduser("~/.local/share/Steam/logs/vrlink*.txt")):
    for l in open(f, errors="replace"):
        m = re.match(r"\w{3} (\w{3} \d\d \d{4} \d\d:\d\d:\d\d\.\d+) \[\w+\] - (.*)", l)
        if m:
            add(datetime.strptime(m.group(1), "%b %d %Y %H:%M:%S.%f"), m.group(2).rstrip())
pm = collections.defaultdict(lambda: [0, 0, 0, 0]); pk = 0
for (ts, text) in lines:
    k = ts.strftime("%m-%d %H:%M")
    if "substantive retry" in text:
        pm[k][0] += 1
        m = re.search(r"\((\d+) packets\)", text); pk += int(m.group(1)) if m else 0
    elif "too many packets were missing" in text: pm[k][1] += 1
    elif "HardReset: Called" in text: pm[k][2] += 1
    elif "Asking remote side for a new IFrame" in text: pm[k][3] += 1
    else: pm[k]
tot = [sum(v[i] for v in pm.values()) for i in range(4)]
n = len(pm)
print("headset rx_reorder_timeout (live): " + open("/sys/module/ath12k/parameters/rx_reorder_timeout").read().strip())
print(f"window {since:%Y-%m-%d %H:%M} .. {until:%H:%M}: {len(lines)} vrlink log lines over {n} minutes")
print(f"FEC retries: {tot[0]} ({tot[0]/n if n else 0:.1f}/min, {pk} packets re-requested)   stream aborts: {tot[1]}   IDR requests: {tot[3]}   decoder hard resets: {tot[2]}")
print("per minute (retries/aborts/resets):")
for k in sorted(pm): print(f"  {k} {pm[k][0]:4d} {pm[k][1]:4d} {pm[k][2]:2d}")
PY
