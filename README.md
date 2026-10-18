# steam-frame-stream-tools

Streaming test, capture and scoring tools for the Steam Frame on a Linux desktop.

Scripts I used to track down stream drops when streaming SteamVR from a Fedora PC to a
Steam Frame over the Steam Frame Wireless Adapter (USB 28de:2432, Linux driver
`rtw89_8852cu`). They are read-mostly diagnostics plus a few deliberately small knobs.
Nothing here is from Valve; see the issues linked below for the findings they support.

- Hypothesis and numbers: the headset ships `ath12k rx_reorder_timeout=2,2,2,2`, tuned for a
  dongle driver that restricts retries, which the in-tree `rtw89` does not do. See SteamOS tracker issue
  [#2952](https://github.com/ValveSoftware/SteamOS/issues/2952) and SteamVR-for-Linux
  [#965](https://github.com/ValveSoftware/SteamVR-for-Linux/issues/965).
- Right-eye flicker is a separate, PC-side encoder fault: SteamVR-for-Linux
  [#938](https://github.com/ValveSoftware/SteamVR-for-Linux/issues/938).

Tested only on: Fedora 44, kernel 7.2.x (Fedora and Zen), SteamVR 2.17/2.18, Steam Frame OS
0.4 to 0.5. Interface names are detected (driver `rtw89_8852cu`) or taken from `IF=` /
`--if`; headset interface names (`wlanap`, `wlan0`) are SteamOS's own.

## Assumptions

- The PC reaches the headset over SSH as `steamos@frame` with key auth (put a `Host frame`
  entry in `~/.ssh/config`). SteamOS on the Frame has sshd disabled and the `steamos` password
  blank; updates disable sshd again and remove `/etc/sudoers.d` drop-ins. `echo | sudo -S -k -p "" cmd`
  works with the blank password; the headset script relies on that.
- Root on the PC is needed only for the rtw89 debugfs knobs (`dongle-agg-tune.sh`,
  `frame-adapter-lowlatency`), which also need a kernel with `CONFIG_RTW89_DEBUGFS`
  (Fedora's stock kernel does not have it; the Zen kernel from COPR `binarytree/linux-zen-fedora` does).
- Python 3, `iw`, `ip`, `journalctl`. No other dependencies.

## pc/ — run on the PC

Capture and scoring

| Script | What it does |
|---|---|
| `capture.sh [label]` | 1 Hz telemetry while you play: dongle RSSI/MCS/NSS, interface and UDP counters, socket backlog, GPU/VCN load, CPU, plus `iw event`, `ip monitor`, journal, and rotation-proof copies of the SteamVR logs. No root. Stop with `touch <outdir>/STOP`. |
| `analyze_capture.py DIR [--timeline]` | Summarises a capture directory. |
| `vrlink-episodes.py LOG "since" "until" [--list]` | Scores the headset's vrlink log (`journalctl -t vrlink` export or `~/.local/share/Steam/logs/vrlink.txt` copied from the headset): FEC retries, aborts, decoder resets per minute; groups abort lines into episodes. The main metric used in the issues. |
| `frame-session-score.sh "since" ["until"]` | Same idea, pulls the log from the headset over SSH first. |
| `episode-view.py` | Looks at one episode in detail with the probe CSVs. |

Link tests (no SteamVR needed)

| Script | What it does |
|---|---|
| `udptest.py tx\|rx ...` | UDP sender/receiver with burst mode, pacing inside bursts, multiple flows, per-packet timeline. Copy to the headset's `/tmp` for the receiving side. |
| `run_udp.sh down\|up MBIT SECS [BURST_HZ] [SIZE]` | One run PC->headset or headset->PC with interface and station counters on both ends. |
| `linktest.sh OUTFILE ...` | A recorded matrix of such runs. |
| `path-probe.sh`, `probe-analyze.py`, `probe-joint.py` | 200 small probes per second on the dongle path and the LAN path while a stream runs, timestamped on both ends; joins the result with the vrlink log. |
| `path-delivery.sh [SECS]` | How much of the running stream reaches the headset on each path. |
| `txq-sampler.py OUT.csv` | 10 Hz samples of bytes to the dongle and LAN, dongle queue, signal. |
| `frame-rx-sampler.py` | Headset side, 20 Hz receive counters (copy to the headset). |

Knobs

| Script | What it does |
|---|---|
| `frame-reorder-timeout.sh status\|set <ms>\|restore\|pause\|resume` | Shows or changes the headset's `ath12k rx_reorder_timeout` over SSH (0 = driver default 100/100/100/40 ms; Valve ships 2,2,2,2). Takes effect at the next headset boot. `pause`/`resume` control the self-healing unit in `headset/`. |
| `dongle-agg-tune.sh show\|set <ampdu_us> <retries>\|restore` | Reads or writes the adapter's max A-MPDU air time (`R_AX_AMPDU_AGG_LIMIT` 0xC610) and data retry limits (`R_AX_TXCNT` 0xC62C) through rtw89 debugfs. Firmware defaults are 5280 µs and 32. Values revert whenever the adapter re-associates. Root. |
| `frame-adapter-lowlatency` + `.service` | Older home-made copy of SteamOS's low-latency mode for the adapter (EDCA, scan/power-save off, A-MSDU off). Superseded by installing Valve's own helper, see `holo-helper/`. |
| `after-reboot-check.sh` | Waits for the headset to come back after a reboot, confirms the live reorder timeout, reruns the link tests if nobody is streaming. |

Flicker

| Script | What it does |
|---|---|
| `flicker-test.sh prepare\|watch\|collect\|restore` | Records the PC encoder's H.265 output (`VRLINK_DUMP_BITSTREAM=1`) at a fixed bitrate. |
| `bitstream-oldframe.py FILE.h265 --from S` | Finds frames where one eye shows a picture 2 to 4 frames old (this is what showed the right-eye flicker is made on the PC). |
| `bitstream-eyes.py FILE.h265` | Black parts and whole-picture repeats per eye. |

## headset/ — lives on the Frame, survives OS updates

| File | What it does |
|---|---|
| `frame-reorder-override` | Put in `~/.local/bin`. At every login it re-creates `/etc/modprobe.d/ath12k.conf` with `rx_reorder_timeout=0,0,0,0`, the passwordless-sudo drop-in and the sshd enable when an OS update removed them. Honours a pause flag (`~/.config/frame-reorder-override.paused`). A second reboot after each update is still needed because the module reads the value only when the radio starts. |
| `frame-reorder-override.service` | User unit (`~/.config/systemd/user`, enable with `systemctl --user enable`) that runs the script at login. |

## holo-helper/ — Valve's low-latency helper on a non-SteamOS host

The Steam client calls `/usr/bin/holo-polkit-helpers/holo-realtek-firmware-toggles set-low-latency-mode on`
when the adapter connects; on SteamOS it comes from the package `holo-realtek-firmware-toggles`
(repo holo-main). `README.md` there describes what it sets, `install-holo-helper.sh <extracted-package-dir>`
installs the script and its polkit policy and pins the Zen kernel as default (`pin-zen.sh`), because the helper
needs `CONFIG_RTW89_DEBUGFS`. The package itself is Valve's and is not included here.

## Method, in short

Start `capture.sh` before a session, play, stop it, copy the headset's vrlink log, run
`vrlink-episodes.py` over the session window, and change one thing at a time between sessions.
The scorer's baselines from my sessions are in the issue text.

## License

MIT, see `LICENSE`.
