#!/usr/bin/env bash
# Host-side telemetry capture for Steam Frame (SteamVR vrlink) streaming on Linux.
# Samples the dongle radio link, UDP socket, GPU/VCN encoder load and CPU once
# per second, records nl80211 / netlink / journal events, and keeps a rotation-
# proof copy of the SteamVR logs (SteamVR only keeps current + .previous).
#
#   capture.sh [label]            run until Ctrl-C, STOP file, or MAX_SECONDS
#   touch <outdir>/STOP           stop a running capture cleanly
#   MAX_SECONDS=3600 INTERVAL=1   tunables (env)
#
# No root needed. Read-only: changes no SteamVR / network / GPU state.
set -u

LABEL=${1:-session}
BASE=${BASE:-$HOME/steam-frame-debug/capture}
OUT="$BASE/$(date +%Y%m%d-%H%M%S)-$LABEL"
MAX_SECONDS=${MAX_SECONDS:-3600}
INTERVAL=${INTERVAL:-1}
STEAM_LOGS=${STEAM_LOGS:-$HOME/.local/share/Steam/logs}
mkdir -p "$OUT/steamvr-logs"

# Dongle = the rtw89_8852cu USB interface (Valve 28de:2432).
IF=${IF:-}
if [ -z "$IF" ]; then
  for i in /sys/class/net/*; do
    drv=$(basename "$(readlink -f "$i/device/driver" 2>/dev/null)" 2>/dev/null)
    [ "$drv" = rtw89_8852cu ] && IF=$(basename "$i") && break
  done
fi
# GPU that carries the encode session (first amdgpu card exposing vcn_busy_percent
# with a discrete-sized VRAM pool); override with CARD=/sys/class/drm/cardN.
CARD=${CARD:-}
if [ -z "$CARD" ]; then
  best=0
  for c in /sys/class/drm/card[0-9]; do
    [ -r "$c/device/vcn_busy_percent" ] || continue
    v=$(cat "$c/device/mem_info_vram_total" 2>/dev/null || echo 0)
    [ "$v" -gt "$best" ] && best=$v && CARD=$c
  done
fi
HWMON=$(ls -d "$CARD"/device/hwmon/hwmon* 2>/dev/null | head -1)

echo "capture dir : $OUT"
echo "dongle if   : ${IF:-<none found>}"
echo "gpu card    : ${CARD:-<none found>}"
{
  echo "start_iso=$(date -Is)"; echo "start_epoch=$(date +%s.%N)"
  echo "label=$LABEL"; echo "if=$IF"; echo "card=$CARD"; echo "interval=$INTERVAL"
  echo "kernel=$(uname -r)"
} > "$OUT/capture.meta"

PIDS=()
bg() { "$@" & PIDS+=($!); }

# Event streams (each line timestamped by the tool itself).
bg bash -c "exec iw event -t > '$OUT/iw_events.log' 2>&1"
bg bash -c "exec ip -ts monitor all > '$OUT/ip_monitor.log' 2>&1"
bg bash -c "exec journalctl -f -n 0 -o short-precise > '$OUT/journal.log' 2>&1"
# Rotation-proof copies of the SteamVR logs (full current content + follow).
for f in vrserver driver_vrlink vrcompositor vrmonitor vrclient_BONEWORKS vrcompositor-linux; do
  bg bash -c "exec tail -F -n +1 '$STEAM_LOGS/$f.txt' > '$OUT/steamvr-logs/$f.follow.txt' 2>/dev/null"
done

cleanup() {
  trap - INT TERM EXIT
  echo "end_iso=$(date -Is)" >> "$OUT/capture.meta"
  echo "end_epoch=$(date +%s.%N)" >> "$OUT/capture.meta"
  kill "${PIDS[@]}" 2>/dev/null
  wait 2>/dev/null
  # Final state snapshots.
  iw dev "$IF" station dump > "$OUT/station_dump.end.txt" 2>&1
  cp -p "$STEAM_LOGS"/vr*.txt "$STEAM_LOGS"/driver_vrlink*.txt "$OUT/steamvr-logs/" 2>/dev/null
  echo "capture stopped: $OUT"
  exit 0
}
trap cleanup INT TERM EXIT

iw dev "$IF" station dump > "$OUT/station_dump.start.txt" 2>&1
iw dev "$IF" link > "$OUT/link.start.txt" 2>&1

CSV="$OUT/samples.csv"
echo "epoch,iso,signal_dbm,signal_avg_dbm,tx_mbit,tx_mcs,tx_nss,rx_mbit,rx_mcs,rx_nss,sta_tx_packets,sta_rx_packets,sta_tx_bytes,sta_rx_bytes,sta_tx_retries,sta_tx_failed,sta_beacon_loss,sta_rx_drop_misc,if_tx_bytes,if_rx_bytes,if_tx_dropped,if_rx_dropped,if_tx_errors,udp_out_dgrams,udp_in_dgrams,udp_sndbuf_err,udp_rcvbuf_err,udp_in_err,sk_wmem_alloc,sk_wmem_queued,sk_drops,gpu_busy_pct,vcn_busy_pct,mem_busy_pct,vram_used_bytes,gpu_temp_mC,gpu_power_uW,gpu_sclk_hz,vrserver_pid,vrserver_enc_ns,vrserver_gfx_ns,vrserver_cpu_ticks,vrcompositor_pid,vrcompositor_gfx_ns,vrcompositor_cpu_ticks,cpu_total_jiffies,cpu_idle_jiffies,loadavg1,psi_cpu_some_avg10,psi_mem_some_avg10,psi_io_some_avg10" > "$CSV"

fdinfo_engine() { # pid engine -> summed ns over unique drm clients
  local pid=$1 eng=$2
  [ -d "/proc/$pid/fdinfo" ] || { echo; return; }
  awk -v eng="drm-engine-$eng:" '
    FNR==1 { id=""; }
    $1=="drm-client-id:" { id=$2 }
    $1==eng { if (id!="" && !(id in seen)) { seen[id]=1; sum+=$2 } }
    END { printf "%.0f", sum+0 }' /proc/"$pid"/fdinfo/* 2>/dev/null
}
cpu_ticks() { # pid -> utime+stime
  awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null
}

START=$(date +%s)
while :; do
  [ -e "$BASE/STOP" ] || [ -e "$OUT/STOP" ] && { rm -f "$BASE/STOP"; break; }
  now=$(date +%s)
  [ $((now - START)) -ge "$MAX_SECONDS" ] && break

  epoch=$(date +%s.%N); iso=$(date +%Y-%m-%dT%H:%M:%S.%3N)

  sta=$(iw dev "$IF" station dump 2>/dev/null | awk '
    function rate(line,   m,mcs,nss) { m=$3; mcs=""; nss="";
      for(i=1;i<=NF;i++){ if($i ~ /MCS$/) mcs=$(i+1); if($i ~ /NSS$/) nss=$(i+1) }
      return m "," mcs "," nss }
    /signal:/      { sig=$2 }
    /signal avg:/  { sigavg=$3 }
    /tx bitrate:/  { tx=rate($0) }
    /rx bitrate:/  { rx=rate($0) }
    /tx packets:/  { txp=$3 }  /rx packets:/ { rxp=$3 }
    /tx bytes:/    { txb=$3 }  /rx bytes:/   { rxb=$3 }
    /tx retries:/  { ret=$3 }  /tx failed:/  { fail=$3 }
    /beacon loss:/ { bl=$3 }   /rx drop misc:/ { dm=$4 }
    END { if (tx=="") tx=",,"; if (rx=="") rx=",,";
          printf "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s", sig,sigavg,tx,rx,txp,rxp,txb,rxb,ret,fail,bl,dm }')
  [ -z "$sta" ] && sta=",,,,,,,,,,,,,,,"

  s=/sys/class/net/$IF/statistics
  ifs="$(cat $s/tx_bytes 2>/dev/null),$(cat $s/rx_bytes 2>/dev/null),$(cat $s/tx_dropped 2>/dev/null),$(cat $s/rx_dropped 2>/dev/null),$(cat $s/tx_errors 2>/dev/null)"

  udp=$(awk '/^Udp:/ && $2 ~ /^[0-9]/ { printf "%s,%s,%s,%s,%s", $5,$2,$7,$6,$4 }' /proc/net/snmp)

  sk=$(ss -u -a -n -m 2>/dev/null | awk -v ifn="%$IF:10400" '
    index($0, ifn) { getline; if (match($0, /skmem:\(([^)]*)\)/)) { s=substr($0,RSTART+7,RLENGTH-8);
      n=split(s,a,","); for(i=1;i<=n;i++){ if(a[i] ~ /^t[0-9]/) t=substr(a[i],2); if(a[i] ~ /^w[0-9]/) w=substr(a[i],2); if(a[i] ~ /^d[0-9]/) d=substr(a[i],2) }
      printf "%s,%s,%s", t,w,d; found=1; exit } }
    END { if(!found) printf ",," }')

  d=$CARD/device
  gpu="$(cat $d/gpu_busy_percent 2>/dev/null),$(cat $d/vcn_busy_percent 2>/dev/null),$(cat $d/mem_busy_percent 2>/dev/null),$(cat $d/mem_info_vram_used 2>/dev/null),$(cat $HWMON/temp1_input 2>/dev/null),$(cat $HWMON/power1_average 2>/dev/null || cat $HWMON/power1_input 2>/dev/null),$(cat $HWMON/freq1_input 2>/dev/null)"

  vs=$(pgrep -x vrserver | head -1); vc=$(pgrep -x vrcompositor | head -1)
  vsm=","; vcm=","
  [ -n "$vs" ] && vsm="$(fdinfo_engine $vs enc),$(fdinfo_engine $vs gfx),$(cpu_ticks $vs)" || vsm=",,"
  [ -n "$vc" ] && vcm="$(fdinfo_engine $vc gfx),$(cpu_ticks $vc)"

  cpu=$(awk '/^cpu /{t=0; for(i=2;i<=NF;i++) t+=$i; printf "%s,%s", t, $5+$6; exit}' /proc/stat)
  la=$(cut -d' ' -f1 /proc/loadavg)
  psi() { awk '/^some/{ for(i=1;i<=NF;i++) if($i ~ /^avg10=/){ sub("avg10=","",$i); print $i } }' /proc/pressure/$1 2>/dev/null; }

  echo "$epoch,$iso,$sta,$ifs,$udp,$sk,$gpu,$vs,$vsm,$vc,$vcm,$cpu,$la,$(psi cpu),$(psi memory),$(psi io)" >> "$CSV"
  sleep "$INTERVAL"
done
