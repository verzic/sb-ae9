#!/bin/bash
# Load the ae9gpio diagnostic module once with the given parameters and print what it logged.
# usage: sudo ./poke.sh acm=3 acmget=2
#        sudo ./poke.sh acm=3 raw=f0,22,02,01,00,f7
#        sudo ./poke.sh i2cdump=0x48
# Build first with `make`. The module never stays loaded (insmod returns -EAGAIN by design).
# READ THE HAZARDS IN THE README: park the driver's ACM service first
# (echo 0 > /sys/module/snd_hda_codec_ca0132/parameters/ae9_acm_poll) and never use acm=1/2
# on a live card -- its GPIO pulse resets both DACs. acm=3 is the UART-only mode.
set -uo pipefail; cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
KO=ae9gpio.ko; [ -f "$KO" ] || { echo "build it first: make"; exit 1; }
# Secure Boot: sign with the shim MOK if present (Ubuntu/Debian layout); DKMS keys as fallback.
if mokutil --sb-state 2>/dev/null | grep -q enabled && ! modinfo "$KO" | grep -q '^signer'; then
  SIGN=/usr/src/linux-headers-$(uname -r)/scripts/sign-file
  for k in /var/lib/shim-signed/mok/MOK /var/lib/dkms/mok; do
    if [ -f "$k.priv" ] || [ -f "$k.key" ]; then
      priv=$k.priv; [ -f "$priv" ] || priv=$k.key; pub=$k.der; [ -f "$pub" ] || pub=$k.pub
      "$SIGN" sha256 "$priv" "$pub" "$KO" && break
    fi
  done
fi
mark="ae9gpio-$(date +%s)"; echo "$mark" > /dev/kmsg
insmod "$KO" "$@" 2>/dev/null   # -EAGAIN expected
sleep 0.3; dmesg | sed -n "/$mark/,\$p" | grep ae9gpio
