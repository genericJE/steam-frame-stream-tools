#!/usr/bin/env bash
# Link test matrix for the Steam Frame dongle link, with packet accounting.
# For each run it records: packets the app sent, what the transmitting radio says it
# got acknowledged (only drivers that report TX status, e.g. mt7921e), what the
# headset's mac80211 counted for that station, and what the receiving app got.
#   linktest.sh OUTFILE [dongle] [alt:IFACE:IP] [up] [lan:HOST_LAN_IP]
set -u
OUT=$1; shift
HERE=$(dirname "$(readlink -f "$0")")
FR="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlMaster=auto -o ControlPath=${XDG_RUNTIME_DIR:-/tmp}/frame-cm-%r@%h:%p -o ControlPersist=15m steamos@frame"
DONGLE_IF=${DONGLE_IF:-$(for i in /sys/class/net/*; do [ "$(basename "$(readlink -f "$i/device/driver" 2>/dev/null)")" = rtw89_8852cu ] && basename "$i"; done)}; FRAME_AP_IP=10.35.78.1
log() { echo "$*" | tee -a "$OUT"; }

txstat() { iw dev "$1" station dump | awk '/tx packets/{p=$3} /tx retries/{r=$3} /tx failed/{f=$3} /signal:/{s=$2} /tx bitrate/{b=$3"Mbit/MCS"$7"/NSS"$9} END{print p,r,f,s,b}'; }
frx() { $FR "iw dev $2 station get $1 2>/dev/null | awk '/rx packets/{print \$3}'"; }

run_down() { # label iface src_ip frame_if sta_mac mbit secs hz port
  local label=$1 ifc=$2 src=$3 fif=$4 mac=$5 mbit=$6 secs=$7 hz=$8 port=$9
  local h0 h1 f0 f1
  h0=$(txstat "$ifc"); f0=$(frx "$mac" "$fif")
  $FR "python3 /tmp/udptest.py rx --port $port --punch $src --seconds $((secs+6))" > ${TMPDIR:-/tmp}/lt.rx 2>&1 &
  sleep 1.5
  python3 "$HERE/udptest.py" tx --port "$port" --dest "$FRAME_AP_IP" --bind-dev "$ifc" --mbit "$mbit" --seconds "$secs" --burst-hz "$hz" > ${TMPDIR:-/tmp}/lt.tx 2>&1
  wait
  h1=$(txstat "$ifc"); f1=$(frx "$mac" "$fif")
  python3 - "$label" "$h0" "$h1" "$f0" "$f1" <<'PY' | tee -a "$OUT"
import sys,json
label,h0,h1,f0,f1=sys.argv[1:6]
a=h0.split(); b=h1.split(); j=None
for l in open('${TMPDIR:-/tmp}/lt.rx'):
    if l.startswith('{'): j=json.loads(l)
if not j or not j.get('received'):
    print(f"{label}: no data received"); sys.exit()
lost=j['sent']-j['received']
print(f"{label:44s} sent {j['sent']:6d} | app lost {lost:5d} ({j['loss_pct']:5.2f}%) holes {j['loss_events']:4d} median {j['loss_burst_median']} max {j['loss_burst_max']} | "
      f"tx radio: handed-off/acked {int(b[0])-int(a[0]):6d} failed {int(b[2])-int(a[2])} retries {int(b[1])-int(a[1]):5d} | headset mac80211 rx {int(f1)-int(f0):6d} | "
      f"delay p50 {j['delay_ms_p50']} p99 {j['delay_ms_p99']} max {j['delay_ms_max']} ms | tx {b[3]} dBm {b[4]}")
print("   RAW " + json.dumps(j))
PY
}

log "# linktest $(date -Is)  host $(uname -r)"
log "# headset: $($FR 'cat /sys/module/ath12k/parameters/rx_reorder_timeout | sed s/^/ath12k.rx_reorder_timeout=/; . /etc/os-release; echo " SteamOS build $BUILD_ID"' | tr '\n' ' ')"
for spec in "$@"; do
  case $spec in
    dongle)
      mac=$(cat /sys/class/net/$DONGLE_IF/address); src=$(ip -4 -o addr show $DONGLE_IF | awk '{print $4}' | cut -d/ -f1)
      log "## PC dongle ($DONGLE_IF, rtw89_8852cu; this driver does not report TX status) -> headset AP"
      run_down "dongle steady 100 Mbit/s"            $DONGLE_IF $src wlanap $mac 100  10 0   6200
      run_down "dongle 25-pkt bursts @360Hz (100M)"  $DONGLE_IF $src wlanap $mac 100  10 360 6200
      run_down "dongle 99-pkt bursts @90Hz (100M)"   $DONGLE_IF $src wlanap $mac 99.8 10 90  6200
      run_down "dongle 298-pkt bursts @30Hz (100M)"  $DONGLE_IF $src wlanap $mac 100  10 30  6200
      run_down "dongle 600-pkt bursts @10Hz (67M)"   $DONGLE_IF $src wlanap $mac 67.2 10 10  6200
      ;;
    alt:*)
      ifc=$(echo "$spec" | cut -d: -f2); src=$(echo "$spec" | cut -d: -f3); mac=$(cat /sys/class/net/$ifc/address)
      log "## PC $ifc ($(ethtool -i $ifc | awk '/^driver/{print $2}'); reports TX status) -> headset AP, same channel"
      run_down "$ifc steady 100 Mbit/s"              $ifc $src wlanap $mac 100  10 0   6300
      run_down "$ifc steady 300 Mbit/s"              $ifc $src wlanap $mac 300  10 0   6300
      run_down "$ifc 25-pkt bursts @360Hz (100M)"    $ifc $src wlanap $mac 100  10 360 6300
      run_down "$ifc 99-pkt bursts @90Hz (100M)"     $ifc $src wlanap $mac 99.8 10 90  6300
      run_down "$ifc 298-pkt bursts @30Hz (100M)"    $ifc $src wlanap $mac 100  10 30  6300
      run_down "$ifc 600-pkt bursts @10Hz (67M)"     $ifc $src wlanap $mac 67.2 10 10  6300
      ;;
    up)
      src=$(ip -4 -o addr show $DONGLE_IF | awk '{print $4}' | cut -d/ -f1)
      log "## headset AP -> PC dongle (reverse direction)"
      for s in "100 30" "67.2 10"; do set -- $s
        python3 "$HERE/udptest.py" rx --port 6400 --seconds 16 > ${TMPDIR:-/tmp}/lt.rx 2>&1 &
        sleep 0.5; $FR "python3 /tmp/udptest.py tx --port 6400 --dest $src --mbit $1 --seconds 10 --burst-hz $2" > /dev/null 2>&1; wait
        python3 - "$1" "$2" <<'PY' | tee -a "$OUT"
import sys,json
j=None
for l in open('${TMPDIR:-/tmp}/lt.rx'):
    if l.startswith('{'): j=json.loads(l)
print(f"headset->PC {sys.argv[1]} Mbit/s in {sys.argv[2]} Hz bursts          sent {j['sent']:6d} | app lost {j['sent']-j['received']:5d} ({j['loss_pct']:5.2f}%) | delay p50 {j['delay_ms_p50']} p99 {j['delay_ms_p99']} max {j['delay_ms_max']} ms")
print("   RAW " + json.dumps(j))
PY
      done
      ;;
  esac
done
