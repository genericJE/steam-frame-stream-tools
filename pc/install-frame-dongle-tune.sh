#!/usr/bin/bash
# Install (or remove) the persistent dongle tune: register tool + NetworkManager dispatcher hook.
#   pkexec ./install-frame-dongle-tune.sh [AMPDU_US RETRIES]   (default 512 6)
#   pkexec ./install-frame-dongle-tune.sh --uninstall
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "${1:-}" = --uninstall ]; then
  rm -f /etc/NetworkManager/dispatcher.d/90-frame-dongle-tune /usr/local/sbin/frame-dongle-tune /etc/default/frame-dongle-tune
  echo "removed (registers keep their current values until the next reconnect)"; exit 0
fi
AMPDU_US=${1:-512}; RETRIES=${2:-6}
install -o root -g root -m 755 "$HERE/dongle-agg-tune.sh" /usr/local/sbin/frame-dongle-tune
install -o root -g root -m 755 "$HERE/frame-dongle-tune.dispatcher" /etc/NetworkManager/dispatcher.d/90-frame-dongle-tune
printf 'AMPDU_US=%s\nRETRIES=%s\n' "$AMPDU_US" "$RETRIES" > /etc/default/frame-dongle-tune
restorecon -F /etc/NetworkManager/dispatcher.d/90-frame-dongle-tune /usr/local/sbin/frame-dongle-tune /etc/default/frame-dongle-tune 2>/dev/null || true
echo "installed: /etc/NetworkManager/dispatcher.d/90-frame-dongle-tune -> frame-dongle-tune set $AMPDU_US $RETRIES"
