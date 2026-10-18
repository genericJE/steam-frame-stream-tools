#!/usr/bin/env bash
# Show or override the Steam Frame headset's ath12k RX reorder timeout (run from the PC, over SSH).
#   frame-reorder-timeout.sh status
#   frame-reorder-timeout.sh set <ms>     # 0 = driver defaults (100/100/100/40 ms); takes effect at next headset boot
#   frame-reorder-timeout.sh restore      # back to Valve's shipped value (2,2,2,2)
#   frame-reorder-timeout.sh pause        # remove the override AND stop the headset's self-healing unit from re-creating it
#   frame-reorder-timeout.sh resume       # let the self-healing unit re-create the override (0,0,0,0)
# The headset's user unit frame-reorder-override.service re-creates the override at every login unless
# ~/.config/frame-reorder-override.paused exists there; plain set/restore are undone at the next login, use pause/resume.
# The override is /etc/modprobe.d/ath12k.conf on the headset, which shadows /usr/lib/modprobe.d/ath12k.conf.
# The value is only read when the radio starts, so reboot the headset after set/restore.
set -eu
FR="ssh -o BatchMode=yes -o ConnectTimeout=8 steamos@frame"
case ${1:-status} in
  status)
    $FR 'echo "shipped : $(grep -E "^options" /usr/lib/modprobe.d/ath12k.conf)"
         echo "override: $( [ -f /etc/modprobe.d/ath12k.conf ] && grep -E "^options" /etc/modprobe.d/ath12k.conf || echo none)"
         echo "next boot: $(modprobe -c | grep -E "^options ath12k")"
         echo "live now : rx_reorder_timeout=$(cat /sys/module/ath12k/parameters/rx_reorder_timeout) ($(sudo dmesg | grep -i "rx reorder timeout" | tail -1 | sed "s/^.*ath12k: //"))"
         echo "sshd at boot: $(systemctl is-enabled sshd 2>&1)"
         echo "self-heal : $( [ -e ~/.config/frame-reorder-override.paused ] && echo PAUSED "(override stays removed)" || echo active "(re-creates 0,0,0,0 at login)")"' ;;
  pause)
    $FR 'touch ~/.config/frame-reorder-override.paused && ~/.local/bin/frame-reorder-override && modprobe -c | grep -E "^options ath12k"'
    echo "Paused. Reboot the headset for the shipped 2,2,2,2 to take effect." ;;
  resume)
    $FR 'rm -f ~/.config/frame-reorder-override.paused && ~/.local/bin/frame-reorder-override && modprobe -c | grep -E "^options ath12k"'
    echo "Resumed. Reboot the headset for the override to take effect." ;;
  set)
    ms=${2:?usage: set <ms>}; case $ms in ''|*[!0-9]*) echo "ms must be a number" >&2; exit 2;; esac
    [ "$ms" -le 200 ] || { echo "refusing values above 200 ms" >&2; exit 2; }
    $FR "printf '%s\n' '# Local override of /usr/lib/modprobe.d/ath12k.conf (shipped: rx_reorder_timeout=2,2,2,2).' '# Remove this file to return to the shipped value.' 'options ath12k rx_reorder_timeout=$ms,$ms,$ms,$ms' | sudo tee /etc/modprobe.d/ath12k.conf >/dev/null && modprobe -c | grep -E '^options ath12k'"
    echo "Written. Reboot the headset for it to take effect." ;;
  restore)
    $FR 'sudo rm -f /etc/modprobe.d/ath12k.conf; modprobe -c | grep -E "^options ath12k"'
    echo "Override removed. Reboot the headset for it to take effect." ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
