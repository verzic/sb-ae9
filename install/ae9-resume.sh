#!/usr/bin/env bash
# Post-resume fix for the Sound Blaster AE-9.
#
# WirePlumber's route restore mutes Front (and sometimes Surround) a few
# seconds after the codec comes back from suspend. The card then looks fine --
# Master on, PipeWire sink at 100%, stream running -- but nothing is audible
# until Front is unmuted by hand. Wait for the route restore to settle and
# unmute the HDA amp switches the driver uses. The Master level, the selected
# output and the effects state are left exactly as the user had them.
set -u

sleep 8

CARD=""
for _ in $(seq 1 20); do
	for f in /proc/asound/card*/id; do
		if grep -qx Creative "$f" 2>/dev/null; then
			CARD=${f#/proc/asound/card}
			CARD=${CARD%%/*}
			break
		fi
	done
	[ -n "$CARD" ] && break
	sleep 0.5
done
[ -z "$CARD" ] && exit 0

for c in PCM Front Surround; do
	amixer -c"$CARD" sset "$c" unmute >/dev/null 2>&1
done

echo "ae9-resume: unmuted PCM/Front/Surround on card $CARD"
