# holo-realtek-firmware-toggles on this Fedora PC

Valve's SteamOS helper that the Steam client calls to put the Steam Frame wireless adapter (rtw89) into
"low latency mode". Source: SteamOS repo holo-main, package holo-realtek-firmware-toggles
(https://steamdeck-packages.steamos.cloud/archlinux-mirror/holo-main/os/x86_64/).

Installed 2026-10-18 (install-holo-helper.sh, version 1.4-1):
- /usr/bin/holo-polkit-helpers/holo-realtek-firmware-toggles  (what steamclient.so looks for; it runs
  `<helper> set-low-latency-mode <on|off>` and logs "Successfully set low latency mode" / "Failed to set ...")
- /usr/share/polkit-1/actions/org.valve.holo-realtek-firmware-toggles.policy (allow_active yes -> no prompt)
- frame-adapter-lowlatency.service disabled (kept on disk); it did the same job by hand while Steam could not.
- Zen kernel pinned as default (pin-zen.sh: DEFAULTKERNEL=kernel-zen-core in /etc/sysconfig/kernel), because the
  helper needs CONFIG_RTW89_DEBUGFS (/sys/kernel/debug/ieee80211/phy*/rtw89), which Fedora's kernel lacks.

What 1.4 low-latency mode does: HW_SCAN off, A-MSDU off, HE-MCS capped to 0-9 (5 and 6 GHz, 1-2 streams),
power save off (iw + firmware INACTIVE_PS), static packet-detection threshold -75 dBm.
Change 1.3-3 -> 1.4-1 (built 2026-10-13): aggressive EDCA no longer set on the adapter ("we're setting it
AP-side now", i.e. on the headset); p2p netdevs skipped when finding the interface.

Check: `/usr/bin/holo-polkit-helpers/holo-realtek-firmware-toggles list` (as user; no dialog expected) and
`grep "low latency mode" ~/.local/share/Steam/logs/remote_connections.txt | tail`.
Undo: `pkexec rm -r /usr/bin/holo-polkit-helpers /usr/share/polkit-1/actions/org.valve.holo-realtek-firmware-toggles.policy`
and `systemctl enable --now frame-adapter-lowlatency.service`.

Same day: Zen 7.2.11-zen1 installed via `dnf upgrade 'kernel-zen*'` and became the default on its own (pin verified);
headset reorder-timeout override paused (`tools/frame-reorder-timeout.sh pause`) to test Valve's stock 2 ms with the
real helper. Compare sessions with `tools/vrlink-episodes.py`; `resume` + headset reboot restores the known-good state.
