# sb-ae9 — Sound Blaster AE-9 on Linux

Out-of-tree Linux driver support for the Creative Sound Blaster AE-9 (PCI `1102:0010`,
dual CA0132 codecs, ES9038Q2M headphone DAC, SABRE9006 line-out DAC, external
Audio Control Module). Based on the kernel's `snd-hda-codec-ca0132`, plus a small fix to
the HDA core that stops the card from hard-locking the machine.

**Status: working, one machine, days not months.** Headphone output through the ACM,
ACM volume knob and dB display, mute-free playback under PipeWire. Read *Known issues*
before installing. This is not (yet) upstream; the plan is to get it there.

## What works
- Analog headphone output through the ACM (ES9038Q2M path), 48 kHz.
- ACM: volume knob (2 detents per point, Windows curve, −90 … 0 dB), dB display,
  headphone amp control, Windows-style keepalive.
- Master volume (Windows loudness curve), PipeWire/WirePlumber integration.
- No DSP effects by default ("Direct Mode"-like); effects can be switched on in the mixer.

## Known issues / not done
- **Rear line-out (speakers) is silent.** The SABRE9006 path is configured but no analog output yet.
- Headphone amp gain stage (IEM / Normal / High) is not controllable yet; the module runs in
  its power-on gain. **With sensitive IEMs start low** (the defaults set about −25 dB).
- SBX effects: occasional crackle reported with effects enabled; not yet re-tested after the
  IOMMU fix below.
- Bit-perfect / rate following (Windows "Direct Mode") not implemented: PipeWire resamples to 48 kHz.
- Mic inputs, S/PDIF, suspend/resume: untested.

## Hazards — read first
1. **Never load an unpatched HDA stack against this card.** The stock kernel's HDA core
   hard-locks the machine when it probes the AE-9 (posted-write bug, `patches/0001`); on
   some boards it also kills the chipset USB controller sharing the IOMMU group. This
   package installs the fixed core module as an override; do not remove it while the card
   is in the machine unless you blacklist `snd_hda_intel` for it.
2. **AMD IOMMU: `iommu=pt` is required.** The AE-9's HDA controller prefetches past the end of
   the audio buffer; with a translating IOMMU every buffer wrap is an aborted DMA read
   (`AMD-Vi: IO_PAGE_FAULT ... 0000:0a:00.0`), which crackles and eventually stalls the DSP
   stream. Add `iommu=pt` to `GRUB_CMDLINE_LINUX_DEFAULT`, run `update-grub`, reboot. VFIO
   passthrough keeps working with `iommu=pt`.
3. **The ACM can hang** (module unresponsive to all traffic). A warm reboot does not clear it:
   power the PC fully off, wait 30 s, reseat the ACM cable, cold boot.
4. **Do not use the diagnostic `tools/acm-tool` while the driver's ACM service is running**
   (UART collision), and never run its `acm=1/2` bring-up on a live card: the GPIO pulse
   resets both DACs. `acm=3` is the UART-only mode.

## Requirements
- Kernel **6.17 or newer** (the codec-driver API and `sound/hda/codecs` layout); headers
  for the running kernel; `dkms`, `gcc`, `make`, `python3`, `curl`.
- PipeWire + WirePlumber (0.4 Lua config or 0.5 conf.d — both shipped).
- Creative's Windows driver package for the DSP firmware segment (see next section).
- Secure Boot: DKMS signs the modules with its key; enrol it once (`mokutil --import
  /var/lib/dkms/mok.pub`, reboot, enrol) or use your distribution's DKMS signing setup.

## The firmware segment (why you need Creative's installer)
The DSP needs a 432-byte program patch that only exists inside Creative's `CtxHda.sys`.
It is Creative's code and is **not** in this repository. `tools/ae9-firmware-extract.sh`
carves it from the driver package you are licensed to use (the `b43-fwcutter` model):

1. Download **Sound Blaster Command** for the AE-9 from
   <https://support.creative.com> (product: Sound Blaster AE-9; file
   `AECMDMasterInstaller_3.4.92.00.exe`, ~138 MB). The page has a licence click and a
   captcha, so this step is manual. No VM, nothing is executed.
2. `sudo ./install.sh --exe ~/Downloads/AECMDMasterInstaller_3.4.92.00.exe`
   (or run `tools/ae9-firmware-extract.sh` yourself). It unpacks two Inno Setup layers
   with `innoextract` (fetched automatically if not installed), locates the segment by
   signature, verifies its SHA-256 against the known-good value, and installs
   `/lib/firmware/ae9-dsp-output-overlay.bin`. A mounted Windows partition or a bare
   `CtxHda.sys` also work (`--windows DIR`, `--sys FILE`).

## Install
```
git clone <this repo> && cd sb-ae9
sudo ./install.sh --exe ~/Downloads/AECMDMasterInstaller_3.4.92.00.exe
# AMD: add iommu=pt (Hazards, 2), then
sudo reboot
```
After the reboot the card comes up by itself. Check: `dmesg | grep 'AE-9'` should show
`converter bring-up done` and `AE-9 ACM: present`. The user service `ae9-defaults` selects
the ACM headphones, sets a safe Master level and makes the AE-9 PipeWire's default sink.

Uninstall: `sudo ./uninstall.sh` (the in-tree driver returns after a reboot; it does not
support the AE-9).

## Using it
- Volume: the ACM knob, or `amixer -c<N> sset Master <0-99>` (Windows curve; 18 ≈ −25 dB,
  44 ≈ −12 dB, 99 = 0 dB). PipeWire's slider is software gain; keep it at 100 %.
- Output: `amixer -c<N> sset 'Output Select' Headphone|Speakers` (speakers not yet audible).
- Effects: `amixer -c<N> sset 'Enable OutFX' on|off` plus the `FX:` switches.
- ACM service off (diagnostic): `ae9_acm_poll=0` in `/etc/modprobe.d/sb-ae9.conf`.

## Repository layout
- `driver/ca0132/` — the codec driver (kernel `sound/hda/codecs/ca0132.c` + AE-9 support).
- `driver/hda-core/` — kernel `sound/hda/core` + the posted-write fix, built as an override.
- `patches/0001-…` — the HDA core fix as a standalone kernel patch (upstream candidate).
- `patches/0002-…` — the codec changes as one diff against v7.0, for review.
- `tools/ae9-firmware-extract.sh` — firmware segment extractor; `tools/acm-tool/` — ACM/BAR2 diagnostics.
- `install/` — modprobe drop-in, WirePlumber rule, session defaults + user service.
- `docs/` — the investigation log, reverse-engineering notes for the ACM protocol and the
  Windows bring-up, and the decoded register captures the driver was derived from.

## How it was done
The Windows driver's register traffic to the card was captured and replayed: DSP master-control
sequence, ASI/PLL and DAC configuration, the output-connect patch, the ACM UART protocol
(sysex-style frames at 115200 baud over a 16550 in BAR2), the encoder acceleration and the
volume table. Details in `docs/`. Prior art: the `ae9_build` project documented the card and
the Windows registry; this work started where it stopped (the DSP output stayed silent).

## License
GPL-2.0 (derived from the Linux kernel). Creative's firmware and driver binaries are not
included and are not redistributed by any part of this project.
