#!/bin/sh
# Re-assert the Sound Blaster AE-9's "Front Playback Switch" after
# PipeWire/WirePlumber starts.
#
# WirePlumber's route restore can leave that switch off when the graph comes
# up: the sink reports unmuted with a normal volume, but the ALSA amp is muted
# and the card is silent.  Keep checking for a few seconds so a late restore is
# caught as well; after that the user's own mute state is left alone.
#
# Installed by install.sh as /usr/local/libexec/sb-ae9/ae9-unmute.sh and run
# from the user unit ae9-unmute.service (WantedBy=pipewire.service).

for d in /proc/asound/card*/; do
	[ -r "$d/codec#1" ] || continue
	grep -qs 'Sound Blaster AE-9' "$d/codec#1" || continue
	idx=${d%/}
	idx=${idx##*card}

	for _ in 1 2 3 4 5 6; do
		sleep 1
		id=$(amixer -c "$idx" controls 2>/dev/null |
		     sed -n "s/^numid=\([0-9]*\).*name='Front Playback Switch'/\1/p" |
		     head -n1)
		[ -n "$id" ] || continue
		if ! amixer -c "$idx" cget numid="$id" 2>/dev/null | grep -q 'values=on'; then
			amixer -c "$idx" cset numid="$id" on >/dev/null 2>&1
		fi
	done
	break
done

exit 0
