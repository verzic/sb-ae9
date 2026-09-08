#!/usr/bin/env bash
# ae9-firmware-extract.sh -- carve the AE-9 DSP output-connect segment out of Creative's
# own Windows driver and install it as a Linux firmware file.
#
# The Linux driver needs a 432-byte DSP program patch that only exists inside CtxHda.sys.
# It is Creative's code, so this project does not ship it; you obtain it from the driver
# package you are licensed to use (same model as b43-fwcutter). Nothing is executed.
#
# Sources, tried in this order unless one is given:
#   --exe FILE      Creative's "Sound Blaster Command" installer (AECMDMasterInstaller_*.exe)
#                   from https://support.creative.com (product: Sound Blaster AE-9). Two Inno
#                   Setup layers deep; needs `innoextract` (apt/dnf/pacman package, or the
#                   static build is fetched automatically into a temp dir).
#   --sys FILE      a CtxHda.sys you already have (64-bit build)
#   --windows DIR   a mounted Windows partition; CtxHda.sys is found in its DriverStore
#   (auto)          ~/Downloads/AECMDMasterInstaller*.exe, then /media /mnt /run/media Windows mounts
#   --out FILE      write here instead of /lib/firmware/ae9-dsp-output-overlay.bin (no root needed)
set -euo pipefail
OUT=/lib/firmware/ae9-dsp-output-overlay.bin
EXE=""; SYS=""; WIN=""
while [ $# -gt 0 ]; do case "$1" in
  --exe) EXE=$2; shift 2;; --sys) SYS=$2; shift 2;; --windows) WIN=$2; shift 2;; --out) OUT=$2; shift 2;;
  -h|--help) sed -n 2,20p "$0"; exit 0;; *) echo "unknown option $1"; exit 2;; esac; done
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
say() { echo ">>> $*" >&2; }

innoextract_bin() {
  if command -v innoextract >/dev/null; then command -v innoextract; return; fi
  say "innoextract not installed; fetching the static build (dscharrer/innoextract 1.9) into a temp dir"
  local url=https://github.com/dscharrer/innoextract/releases/download/1.9/innoextract-1.9-linux.tar.xz
  curl -fsSL "$url" -o "$WORK/inno.tar.xz" && tar xf "$WORK/inno.tar.xz" -C "$WORK"
  local b; b=$(find "$WORK" -type f -name innoextract -path "*amd64*" | head -1)
  [ -x "$b" ] || { echo "could not obtain innoextract; install it with your package manager"; exit 1; }
  echo "$b"
}

from_exe() {  # $1 = Command installer -> prints path of CtxHda.sys
  local ie; ie=$(innoextract_bin)
  say "unpacking $(basename "$1") (layer 1: Sound Blaster Command)"
  "$ie" -q -I "tmp/AE9Driver" -d "$WORK/l1" "$1" >/dev/null
  local inner; inner=$(find "$WORK/l1" -iname "AESeriesDriverInstaller*.exe" | head -1)
  [ -n "$inner" ] || { echo "AESeriesDriverInstaller.exe not found inside the installer"; exit 1; }
  say "unpacking $(basename "$inner") (layer 2: AE-Series driver)"
  "$ie" -q -I "pf/Creative/Sound Blaster AE-Series Driver/AESeriesDriver/Driver/AMD64/CtxHda.sys" -d "$WORK/l2" "$inner" >/dev/null
  find "$WORK/l2" -name CtxHda.sys | head -1
}

from_windows() {  # $1 = mount point
  find "$1/Windows/System32/DriverStore/FileRepository" "$1/Windows/System32/drivers" -iname CtxHda.sys -size +1000k 2>/dev/null | head -1
}

if [ -z "$SYS" ]; then
  if [ -n "$EXE" ]; then SYS=$(from_exe "$EXE")
  elif [ -n "$WIN" ]; then SYS=$(from_windows "$WIN")
  else
    cand=$(ls -t ~/Downloads/AECMDMasterInstaller*.exe 2>/dev/null | head -1)
    if [ -n "$cand" ]; then say "found $cand"; SYS=$(from_exe "$cand")
    else for m in /media/*/* /mnt/* /run/media/*/*; do [ -d "$m/Windows" ] && SYS=$(from_windows "$m") && [ -n "$SYS" ] && { say "found CtxHda.sys on Windows mount $m"; break; }; done; fi
  fi
fi
[ -n "${SYS:-}" ] && [ -f "$SYS" ] || { echo "No CtxHda.sys found. Download 'Sound Blaster Command' for the AE-9 from support.creative.com and run: $0 --exe ~/Downloads/AECMDMasterInstaller_3.4.92.00.exe"; exit 1; }
say "source: $SYS ($(stat -c %s "$SYS") bytes)"

python3 - "$SYS" "$WORK/overlay.bin" <<'PY'
import sys, hashlib, struct
data = open(sys.argv[1], 'rb').read()
# The segment starts with the DSP DMA header of its first chunk: 39 instruction patches to
# program RAM 0x0b7b10, followed by a 21-word jump table to XRAM 0x3f3a0 whose first word is
# 0x00800082. Known-good sha256 (driver builds 6.0.105.0065 and the Command 3.4.92 bundle):
KNOWN = "ecc0740bde7ead2b" 
SIZE = 432
# locate by the jump-table marker 0x00800082 preceded by a valid segment header layout
marker = struct.pack('<I', 0x00800082)
cands = []
pos = data.find(marker)
while pos != -1:
    start = pos - 336           # the marker sits 336 bytes into the 432-byte segment
    if start >= 0:
        seg = data[start:start + SIZE]
        if len(seg) == SIZE:
            cands.append((start, seg))
    pos = data.find(marker, pos + 1)
best = None
for start, seg in cands:
    h = hashlib.sha256(seg).hexdigest()
    if h.startswith(KNOWN):
        best = (start, seg, h); break
if best is None and cands:
    print("WARNING: no candidate matches the known hash; this may be a newer/older driver build.")
    print("         Candidates at:", ", ".join(hex(s) for s, _ in cands), "- refusing to guess.")
    sys.exit(3)
if best is None:
    print("segment not found in this file"); sys.exit(4)
start, seg, h = best
open(sys.argv[2], 'wb').write(seg)
print(f">>> segment found at 0x{start:x}, {SIZE} bytes, sha256 {h[:16]}... (matches known-good)")
PY

if [ -w "$(dirname "$OUT")" ] || [ "$(id -u)" = 0 ]; then
  install -m 644 "$WORK/overlay.bin" "$OUT"; say "installed $OUT"
else
  say "need root to write $OUT; re-run with sudo, or use --out to choose a path"; exit 1
fi
