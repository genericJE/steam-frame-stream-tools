#!/usr/bin/bash
# Install Valve's holo-realtek-firmware-toggles helper (from SteamOS holo-main, 1.4-1) so the Steam client
# can enable the Steam Frame adapter's low-latency mode itself, make the Zen kernel the default boot entry
# (the helper needs CONFIG_RTW89_DEBUGFS), and retire the home-made frame-adapter-lowlatency.service.
set -eu
SRC=${1:?usage: install-holo-helper.sh <dir with the extracted holo-realtek-firmware-toggles package (usr/bin/..., usr/share/...)>}
echo "== boot entries"; grep -H "^version" /boot/loader/entries/*.conf
echo "== grubenv before"; grub2-editenv list | grep -i saved || echo "(no saved_entry)"
echo "== grubby default before: $(grubby --default-kernel)"

echo "== installing helper"
install -d -m 755 /usr/bin/holo-polkit-helpers
install -m 755 -o root -g root "$SRC/usr/bin/holo-polkit-helpers/holo-realtek-firmware-toggles" /usr/bin/holo-polkit-helpers/holo-realtek-firmware-toggles
install -m 644 -o root -g root "$SRC/usr/share/polkit-1/actions/org.valve.holo-realtek-firmware-toggles.policy" /usr/share/polkit-1/actions/org.valve.holo-realtek-firmware-toggles.policy
restorecon -Rv /usr/bin/holo-polkit-helpers /usr/share/polkit-1/actions/org.valve.holo-realtek-firmware-toggles.policy || true
ls -laZ /usr/bin/holo-polkit-helpers/ /usr/share/polkit-1/actions/org.valve.holo-realtek-firmware-toggles.policy

echo "== retiring frame-adapter-lowlatency.service"
systemctl disable --now frame-adapter-lowlatency.service
systemctl is-enabled frame-adapter-lowlatency.service || true

echo "== making Zen the default boot entry"
grubby --set-default /boot/vmlinuz-7.2.9-zen1.fc44.x86_64
echo "grubby default after: $(grubby --default-kernel)"
grub2-editenv list | grep -i saved || true

echo "== helper self-test on the running kernel (expected to fail here: no rtw89 debugfs on $(uname -r))"
/usr/bin/holo-polkit-helpers/holo-realtek-firmware-toggles list || echo "exit $?"
echo "== done"
