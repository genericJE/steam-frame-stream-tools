#!/usr/bin/env bash
# Tune the Steam Frame Wireless Adapter's (rtw89_8852cu) hardware TX limits so a
# failed frame is retried before the headset's 2 ms rx_reorder_timeout expires.
# Writes two CMAC registers through rtw89 debugfs (Zen kernel: CONFIG_RTW89_DEBUGFS):
#   0xC610 R_AX_AMPDU_AGG_LIMIT  bits 31:24 = max A-MPDU air time, 32 us units (fw default 0xA5 = 5280 us)
#   0xC62C R_AX_TXCNT            bits 29:24 = short retry limit, 21:16 = long retry limit (fw default 32/32)
# Usage (root; run via pkexec):
#   dongle-agg-tune.sh show
#   dongle-agg-tune.sh set <ampdu_us> <retries>     e.g. set 512 6
#   dongle-agg-tune.sh restore                      fw defaults 5280 / 32
set -u
R=""
for p in /sys/kernel/debug/ieee80211/phy*; do
  for n in "$p"/netdev:*; do
    [ -e "$n" ] || continue
    drv=$(basename "$(readlink -f "/sys/class/net/${n##*netdev:}/device/driver" 2>/dev/null)")
    [ "$drv" = rtw89_8852cu ] && R="$p/rtw89"
  done
done
[ -n "$R" ] && [ -w "$R/write_reg" ] || { echo "no rtw89 debugfs for the adapter (root + Zen kernel needed)" >&2; exit 1; }

rd() { echo "$1 4" > "$R/read_reg"; cat "$R/read_reg" | sed -n 's/.*=0x\([0-9a-fA-F]*\).*/\1/p'; }
show() {
  local agg txc
  agg=$((16#$(rd 0xC610))); txc=$((16#$(rd 0xC62C)))
  printf "AMPDU_AGG_LIMIT 0xC610=0x%08x: max A-MPDU time %d us, ra_try_agg %d, rts_max_agg %d, max_agg_num %d\n" \
    "$agg" $(( ((agg>>24)&0xff)*32 )) $(( (agg>>16)&0xff )) $(( (agg>>8)&0xff )) $(( agg&0xff ))
  printf "TXCNT           0xC62C=0x%08x: short retry limit %d, long retry limit %d\n" \
    "$txc" $(( (txc>>24)&0x3f )) $(( (txc>>16)&0x3f ))
}
set_vals() {
  local us=$1 rt=$2 agg txc units
  units=$(( us / 32 )); [ "$units" -lt 1 ] && units=1; [ "$units" -gt 255 ] && units=255
  [ "$rt" -lt 1 ] && rt=1; [ "$rt" -gt 63 ] && rt=63
  agg=$((16#$(rd 0xC610))); txc=$((16#$(rd 0xC62C)))
  agg=$(( (agg & 0x00ffffff) | (units << 24) ))
  txc=$(( (txc & 0xc0c0ffff) | (rt << 24) | (rt << 16) ))
  printf "0xC610 0x%08x 4\n" "$agg" > "$R/write_reg"
  printf "0xC62C 0x%08x 4\n" "$txc" > "$R/write_reg"
}
case "${1:-show}" in
  show) show ;;
  set) set_vals "${2:?ampdu_us}" "${3:?retries}"; show ;;
  restore) set_vals 5280 32; show ;;
  *) echo "usage: $0 show | set <ampdu_us> <retries> | restore" >&2; exit 2 ;;
esac
