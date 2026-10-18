#!/usr/bin/env bash
# Wait for the headset to reboot, confirm the reorder timeout that is live afterwards, and
# if nobody is streaming at that moment, rerun the dongle link tests for comparison.
#   after-reboot-check.sh [MAX_WAIT_SECONDS]
set -u
HERE=$(dirname "$(readlink -f "$0")"); OUTDIR=$HERE/../linktest; LOG=$OUTDIR/after-reboot.log
MAXWAIT=${1:-7000}; IF=${IF:-$(for i in /sys/class/net/*; do [ "$(basename "$(readlink -f "$i/device/driver" 2>/dev/null)")" = rtw89_8852cu ] && basename "$i"; done)}
CM=${XDG_RUNTIME_DIR:-/tmp}/frame-cm-steamos@frame:22
fr() { ssh -o BatchMode=yes -o ConnectTimeout=5 -o ControlPath=none steamos@frame "$@" 2>/dev/null; }
say() { echo "$(date +%T) $*" | tee -a "$LOG"; }

: > "$LOG"
boot0=$(fr cat /proc/sys/kernel/random/boot_id)
[ -n "$boot0" ] || { say "headset not reachable at start"; exit 1; }
say "waiting for headset reboot (current boot $boot0, live timeout $(fr cat /sys/module/ath12k/parameters/rx_reorder_timeout))"
start=$(date +%s); boot1=
while [ $(( $(date +%s) - start )) -lt "$MAXWAIT" ]; do
  b=$(fr cat /proc/sys/kernel/random/boot_id)
  if [ -n "$b" ] && [ "$b" != "$boot0" ]; then boot1=$b; break; fi
  sleep 15
done
[ -n "$boot1" ] || { say "no reboot seen within ${MAXWAIT}s; override is still pending"; exit 0; }
ssh -O exit -o ControlPath="$CM" steamos@frame 2>/dev/null; rm -f "$CM"
say "headset rebooted (boot $boot1)"
say "live: rx_reorder_timeout=$(fr cat /sys/module/ath12k/parameters/rx_reorder_timeout); kernel: $(fr 'sudo dmesg | grep -i "rx reorder timeout" | tail -1 | sed "s/^.*ath12k: //"')"
say "sshd enabled: $(fr systemctl is-enabled sshd)"

# wait for the dongle link (up to 15 min)
for i in $(seq 1 90); do ping -c1 -W1 -I $IF 10.35.78.1 >/dev/null 2>&1 && break; sleep 10; done
if ! ping -c1 -W1 -I $IF 10.35.78.1 >/dev/null 2>&1; then say "dongle link did not come back within 15 min; skipping link tests"; exit 0; fi
say "dongle link is up: $(iw dev $IF link | awk '/signal/{printf "signal %s dBm ",$2} /tx bitrate/{printf "tx %s Mbit/s",$3}')"

# only test when nobody is streaming (idle stream is a few hundred kbit/s when the headset is not worn)
a=$(cat /sys/class/net/$IF/statistics/tx_bytes); sleep 10; b=$(cat /sys/class/net/$IF/statistics/tx_bytes)
kbit=$(( (b - a) * 8 / 10 / 1000 ))
if [ "$kbit" -gt 3000 ]; then say "stream is active (${kbit} kbit/s on the dongle); not running link tests now"; exit 0; fi
say "stream idle (${kbit} kbit/s); running link tests"
scp -q -o BatchMode=yes -o ControlPath=none "$HERE/udptest.py" steamos@frame:/tmp/udptest.py || { say "could not copy test tool"; exit 0; }
"$HERE/linktest.sh" "$OUTDIR/results-timeout-default.txt" dongle up >> "$LOG" 2>&1
say "done"
