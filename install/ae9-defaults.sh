#!/usr/bin/env bash
# Per-session defaults for the Sound Blaster AE-9: headphone output on the ACM,
# effects off, Master at a safe level, the AE-9 as PipeWire's default sink at unity
# gain. Runs from the ae9-defaults user service; safe to re-run by hand.
#   AE9_OUT=Speakers  -> select the rear line-out instead of the ACM headphones
#   AE9_LEVEL=<0-99>  -> Master level (Windows curve; 18 ~ -25 dB, 44 ~ -12 dB, 99 = 0 dB)
# Order matters: WirePlumber's route restore writes the card's routing control a few
# seconds after it takes the device, so we wait for its sink node to exist first.
set -u
OUT=${AE9_OUT:-Headphone}; LEVEL=${AE9_LEVEL:-18}

find_card() { for f in /proc/asound/card*/id; do grep -qx Creative "$f" 2>/dev/null && { c=${f#/proc/asound/card}; echo "${c%%/*}"; return; }; done; }
find_sink() { pw-dump 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d=[]
devs={o["id"] for o in d if o.get("info",{}).get("props",{}).get("device.vendor.id")=="0x1102" and o["info"]["props"].get("device.product.id")=="0x0010"}
# the card exposes two sinks (analog + iec958/SPDIF): take the ANALOG one
for o in d:
    p=o.get("info",{}).get("props",{})
    if p.get("media.class")=="Audio/Sink" and p.get("device.id") in devs and "analog" in p.get("node.name","") and "iec958" not in p.get("node.name",""):
        print(o["id"]); break' 2>/dev/null; }
route_ctl() { amixer -c"$1" scontrols 2>/dev/null | grep -q "'AE-9 Output'" && echo "AE-9 Output" || echo "Output Select"; }
apply_routing() {
  local ctl; ctl=$(route_ctl "$CARD")
  amixer -c"$CARD" sset "$ctl" "$OUT" >/dev/null 2>&1
  amixer -c"$CARD" sset 'Enable OutFX' off >/dev/null 2>&1
  for c in PCM Front Surround; do amixer -c"$CARD" sset "$c" unmute >/dev/null 2>&1; amixer -c"$CARD" sset "$c" 100% >/dev/null 2>&1; done
  amixer -c"$CARD" sset Master unmute >/dev/null 2>&1; amixer -c"$CARD" sset Master "$LEVEL" >/dev/null 2>&1
  echo "ae9-defaults: card $CARD -> $ctl=$OUT, OutFX off, Master $LEVEL"
}
current_out() { amixer -c"$CARD" sget "$(route_ctl "$CARD")" 2>/dev/null | sed -n "s/.*Item0: '\(.*\)'/\1/p"; }

# PipeWire device id of the AE-9, its active profile, and the index of the analog profile
dev_info() { pw-dump 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d=[]
for o in d:
    p=o.get("info",{}).get("props",{})
    if p.get("device.vendor.id")=="0x1102" and p.get("device.product.id")=="0x0010":
        pr=o["info"].get("params",{})
        act=(pr.get("Profile") or [{}])[0].get("name","")
        want=None
        for name in ("output:analog-stereo+input:analog-stereo","output:analog-stereo"):
            for e in pr.get("EnumProfile",[]):
                if e.get("name")==name: want=e.get("index"); break
            if want is not None: break
        print(o["id"], act, "" if want is None else want); break' 2>/dev/null; }

CARD=""; for i in $(seq 1 40); do CARD=$(find_card); [ -n "$CARD" ] && break; sleep 0.5; done
[ -z "$CARD" ] && { echo "ae9-defaults: Creative card not found"; exit 1; }
# 1. make sure the card runs its ANALOG profile. Without the ACP-mapped "Output Select" element
#    the analog routes read "unavailable" and WirePlumber auto-selects the S/PDIF profile.
for i in $(seq 1 40); do read -r DEVID ACTIVE WANT <<<"$(dev_info)"; [ -n "${DEVID:-}" ] && break; sleep 0.5; done
if [ -n "${DEVID:-}" ] && [ -n "${WANT:-}" ] && [[ "${ACTIVE:-}" != output:analog-stereo* ]]; then
  wpctl set-profile "$DEVID" "$WANT" 2>/dev/null && echo "ae9-defaults: device $DEVID profile -> analog (index $WANT, was '${ACTIVE:-none}')"
fi
# 2. wait until WirePlumber has created the AE-9 analog sink (= profile + route restore done), then a grace period
ID=""; for i in $(seq 1 40); do ID=$(find_sink); [ -n "$ID" ] && break; sleep 0.5; done
[ -n "$ID" ] && sleep 3 || echo "ae9-defaults: AE-9 sink not seen in PipeWire (routing applied anyway)"
# 3. routing + levels
apply_routing
# 4. PipeWire default sink at unity
if [ -n "$ID" ]; then wpctl set-default "$ID" 2>/dev/null; wpctl set-volume "$ID" 1.0 2>/dev/null; echo "ae9-defaults: PipeWire default sink = $ID at unity"; fi
# 5. verify once more after a late route restore, and put it back if it moved
sleep 10
if [ "$(current_out)" != "$OUT" ]; then echo "ae9-defaults: routing was changed to '$(current_out)' after we set it; re-applying"; apply_routing; fi
