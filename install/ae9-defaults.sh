#!/usr/bin/env bash
# Per-session defaults for the Sound Blaster AE-9: headphone output on the ACM,
# effects off, Master at a safe level, the AE-9 as PipeWire's default sink at unity
# gain. Runs from the ae9-defaults user service; safe to re-run by hand.
#   AE9_OUT=Speakers  -> select the rear line-out instead of the ACM headphones
#   AE9_LEVEL=<0-99>  -> Master level (Windows curve; 18 ~ -25 dB, 44 ~ -12 dB, 99 = 0 dB)
set -u
OUT=${AE9_OUT:-Headphone}; LEVEL=${AE9_LEVEL:-18}
CARD=""
for i in $(seq 1 40); do
  for f in /proc/asound/card*/id; do grep -qx Creative "$f" 2>/dev/null && { CARD=${f#/proc/asound/card}; CARD=${CARD%%/*}; break; }; done
  [ -n "$CARD" ] && break; sleep 0.5
done
[ -z "$CARD" ] && { echo "ae9-defaults: Creative card not found"; exit 1; }
amixer -c"$CARD" sset 'Output Select' "$OUT" >/dev/null 2>&1
amixer -c"$CARD" sset 'Enable OutFX' off      >/dev/null 2>&1
for c in PCM Front Surround; do amixer -c"$CARD" sset "$c" unmute >/dev/null 2>&1; amixer -c"$CARD" sset "$c" 100% >/dev/null 2>&1; done
amixer -c"$CARD" sset Master unmute >/dev/null 2>&1; amixer -c"$CARD" sset Master "$LEVEL" >/dev/null 2>&1
echo "ae9-defaults: card $CARD -> Output Select=$OUT, OutFX off, Master $LEVEL"
# PipeWire: make the AE-9 the default sink at unity (the knob is the level; soft-mixer rule keeps ACP off the hardware mixer)
for i in $(seq 1 40); do
  ID=$(pw-dump 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d=[]
devs={o["id"] for o in d if o.get("info",{}).get("props",{}).get("device.vendor.id")=="0x1102" and o["info"]["props"].get("device.product.id")=="0x0010"}
for o in d:
    p=o.get("info",{}).get("props",{})
    if p.get("media.class")=="Audio/Sink" and p.get("device.id") in devs:
        print(o["id"]); break' 2>/dev/null)
  [ -n "$ID" ] && break; sleep 0.5
done
if [ -n "${ID:-}" ]; then wpctl set-default "$ID" 2>/dev/null; wpctl set-volume "$ID" 1.0 2>/dev/null; echo "ae9-defaults: PipeWire default sink = $ID at unity"; else echo "ae9-defaults: AE-9 sink not found in PipeWire yet"; fi
