#!/usr/bin/env bash
# sb-ae9 installer: builds the two kernel modules with DKMS for every installed kernel,
# extracts the DSP firmware segment from Creative's driver, and installs the session
# defaults. Re-run after pulling updates. Undo with ./uninstall.sh.
#
#   sudo ./install.sh [--exe /path/to/AECMDMasterInstaller_*.exe] [--no-firmware]
#
# Requirements: kernel >= 6.17 headers (linux-headers-$(uname -r)), dkms, python3,
# pipewire + wireplumber. On AMD systems with the IOMMU enabled, add `iommu=pt` to the
# kernel command line (see README, it is not optional).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
SRC=$(cd "$(dirname "$0")" && pwd)
VER=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' "$SRC/dkms.conf")
EXE=""; FW=1
while [ $# -gt 0 ]; do case "$1" in --exe) EXE=$2; shift 2;; --no-firmware) FW=0; shift;; *) echo "unknown option $1"; exit 2;; esac; done
usr="${SUDO_USER:-}"; [ -n "$usr" ] && [ "$usr" != root ] || { echo "run via sudo from your normal user (the session defaults are installed for that user)"; exit 1; }
uid=$(id -u "$usr"); home=$(getent passwd "$usr" | cut -d: -f6)

echo "== 1/5 kernel modules (DKMS) =="
for t in dkms make gcc python3; do command -v $t >/dev/null || { echo "missing: $t"; exit 1; }; done
[ -d "/lib/modules/$(uname -r)/build" ] || { echo "missing kernel headers for $(uname -r)"; exit 1; }
rm -rf "/usr/src/sb-ae9-$VER"; mkdir -p "/usr/src/sb-ae9-$VER"
cp -r "$SRC/driver" "$SRC/dkms.conf" "/usr/src/sb-ae9-$VER/"
dkms remove "sb-ae9/$VER" --all >/dev/null 2>&1 || true
dkms add "sb-ae9/$VER"
dkms build "sb-ae9/$VER" -k "$(uname -r)"
dkms install "sb-ae9/$VER" -k "$(uname -r)" --force
for m in snd-hda-core snd-hda-codec-ca0132; do
  modinfo -n "$m" | grep -q '/updates/' || { echo "depmod did not pick up the $m override"; exit 1; }
done
echo "modules installed under /lib/modules/$(uname -r)/updates (DKMS rebuilds them for new kernels)"

echo "== 2/5 DSP firmware segment =="
if [ "$FW" = 1 ]; then
  if [ -f /lib/firmware/ae9-dsp-output-overlay.bin ]; then echo "already present: /lib/firmware/ae9-dsp-output-overlay.bin"
  else
    if [ -n "$EXE" ]; then sudo -u "$usr" HOME="$home" "$SRC/tools/ae9-firmware-extract.sh" --exe "$EXE" --out /tmp/ae9-overlay.$$ 
    else sudo -u "$usr" HOME="$home" "$SRC/tools/ae9-firmware-extract.sh" --out /tmp/ae9-overlay.$$; fi
    install -m 644 /tmp/ae9-overlay.$$ /lib/firmware/ae9-dsp-output-overlay.bin; rm -f /tmp/ae9-overlay.$$
    echo "installed /lib/firmware/ae9-dsp-output-overlay.bin"
  fi
fi

echo "== 3/5 module options + WirePlumber rule =="
install -m 644 "$SRC/install/sb-ae9.conf" /etc/modprobe.d/sb-ae9.conf
if [ -d /usr/share/wireplumber/main.lua.d ]; then      # WirePlumber 0.4.x
  install -D -m 644 "$SRC/install/51-ae9-soft-mixer.lua" "$home/.config/wireplumber/main.lua.d/51-ae9-soft-mixer.lua"
  chown -R "$usr:" "$home/.config/wireplumber"
else                                                      # WirePlumber 0.5+
  install -D -m 644 "$SRC/install/51-ae9-soft-mixer.conf" "$home/.config/wireplumber/wireplumber.conf.d/51-ae9-soft-mixer.conf"
  chown -R "$usr:" "$home/.config/wireplumber"
fi

echo "== 4/5 session defaults (user service) =="
install -D -m 755 "$SRC/install/ae9-defaults.sh" /usr/local/libexec/sb-ae9/ae9-defaults.sh
install -D -m 644 "$SRC/install/ae9-defaults.service" "$home/.config/systemd/user/ae9-defaults.service"
chown -R "$usr:" "$home/.config/systemd"
sudo -u "$usr" XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user daemon-reload 2>/dev/null || true
sudo -u "$usr" XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user enable ae9-defaults.service 2>/dev/null || true

echo "== 5/5 checks =="
if [ -d /sys/firmware/efi ] && command -v mokutil >/dev/null && mokutil --sb-state 2>/dev/null | grep -q enabled; then
  echo "Secure Boot is ON: DKMS signs modules with its own key; enrol it once if not done (see README: 'Secure Boot')."
fi
if ! grep -q 'iommu=pt' /proc/cmdline && grep -qi amd /proc/cpuinfo && [ -d /sys/class/iommu ] && [ -n "$(ls /sys/class/iommu 2>/dev/null)" ]; then
  echo "WARNING: AMD IOMMU active without iommu=pt -- playback WILL drop out. Add iommu=pt to GRUB_CMDLINE_LINUX_DEFAULT and update-grub (README)."
fi
echo "Done. Reboot; the card comes up by itself. Then: dmesg | grep 'AE-9'"
