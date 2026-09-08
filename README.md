# sb-ae9 — Sound Blaster AE-9 on Linux

Out-of-tree Linux driver support for the Creative Sound Blaster AE-9 (PCI `1102:0010`,
dual CA0132 codecs, ES9038Q2M headphone DAC, SABRE9006 line-out DAC, external
Audio Control Module). Based on the kernel's `snd-hda-codec-ca0132`, plus a small fix to
the HDA core that stops the card from hard-locking the machine.

**Status: working.** Built and running on Linux 7.0 (Ubuntu kernel 7.0.0-31-generic) with
the physical card and ACM: headphone output through the ACM, volume knob and dB display,
SBX button, playback under PipeWire, no issues so far. Read *Known issues* and *Hazards*
before installing. This is not (yet) upstream; the plan is to get it there.

## What works
- **Headphone output through the ACM** (ES9038Q2M path), 48 kHz, under PipeWire.
- **Volume knob**: clockwise = up, two detents per point, Windows' 101-point loudness curve
  (−90 … 0 dB). The ACM display shows the level in Windows' format (`-25.5`), `MUTE` while
  muted, `-HP-` / `-SP-` when the output is switched.
- **Knob press**: short press (< 1.5 s) toggles mute; 1.5–3 s hold toggles the output
  (headphones ⇄ rear line-out); longer presses are ignored.
- **SBX button**: short press toggles the DSP effects (`Enable OutFX`); the button light shows
  the real effects state, off at boot. Presses of 5 s or more are ignored (Windows uses them
  for a display option).
- **Effects**: off by default ("Direct Mode"-like clean path). When on, each SBX processor
  (Crystalizer, Dialog Plus, Smart Volume, X-Bass, Surround, Equalizer) has its own switch
  and level in the mixer — see *Effects* below.
- **Master volume** on the hardware amp with the Windows curve; the DSP volume stage is fixed
  at 0 dB, so the knob and `Master` are the only level control.
- **Desktop integration**: the card comes up on its analog profile by itself, PipeWire's
  default sink is the AE-9 at unity gain, and WirePlumber is kept off the hardware mixer.
- **Stability**: the HDA-core posted-write fix (the stock kernel hard-locks on this card),
  the IOMMU requirement below, the codec held out of runtime suspend so the ACM stays alive,
  and serialized access to the card's DSP command engine.
- **Packaging**: DKMS (rebuilds on kernel updates, signs for Secure Boot), a firmware
  extractor for the one DSP segment that cannot be redistributed, an uninstaller.

## Known issues / not done
- **Rear line-out (speakers) is silent.** The SABRE9006 path is configured but no analog output yet.
- Headphone amp gain stage (IEM / Normal / High) is not controllable yet; the module runs in
  its power-on gain. **With sensitive IEMs start low** (the defaults set about −25 dB).
- Effects run at Creative's stock levels, not your Windows SBX profile; there is no import.
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
   resets both DACs. `acm=3` is the UART-only mode. See *Diagnostics*.

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

1. Download **Sound Blaster Command** for the AE-9 (file `AECMDMasterInstaller_3.4.92.00.exe`,
   ~138 MB) from Creative's download page:
   <https://support.creative.com/downloads/download.aspx?nDownloadId=100330>.
   If that link is broken, find it here: <https://support.creative.com/Downloads/searchdownloads.aspx?filename=SB#>
   (search "AE-9", package "Sound Blaster Command"). The page has a licence click and a
   captcha, so this step is manual. No VM, nothing is executed.
2. `sudo ./install.sh --exe ~/Downloads/AECMDMasterInstaller_3.4.92.00.exe`
   (or run `tools/ae9-firmware-extract.sh` yourself). It unpacks two Inno Setup layers
   with `innoextract` (fetched automatically if not installed), locates the segment by
   signature, verifies its SHA-256 against the known-good value, and installs
   `/lib/firmware/ae9-dsp-output-overlay.bin`. A mounted Windows partition or a bare
   `CtxHda.sys` also work (`--windows DIR`, `--sys FILE`).

## Install
```
git clone https://github.com/verzic/sb-ae9 && cd sb-ae9
sudo ./install.sh --exe ~/Downloads/AECMDMasterInstaller_3.4.92.00.exe
# AMD: add iommu=pt (Hazards, 2), then
sudo reboot
```
`install.sh` adds the DKMS module set (`sb-ae9`), extracts and installs the firmware segment
(`--no-firmware` skips that step if it is already in place), installs the modprobe drop-in,
the WirePlumber rule and the `ae9-defaults` user service, and warns about Secure Boot and
`iommu=pt`. After the reboot the card comes up by itself.

Check: `dmesg | grep 'AE-9'` should show `converter bring-up done` and
`AE-9 ACM: present, firmware …, output headphone`, followed a few seconds later by
`AE-9 ACM: regs … (amp on …)`. The user service `ae9-defaults` selects the ACM headphones,
sets a safe Master level and makes the AE-9 PipeWire's default sink.

Uninstall: `sudo ./uninstall.sh` (the in-tree driver returns after a reboot; it does not
support the AE-9).

## Using it
The examples address the card by its ALSA id, `-c Creative`, which does not change between
boots (the numeric index can).

### Volume and mute
- The ACM knob, or `amixer -c Creative sset Master <0-99>` (Windows curve; 18 ≈ −25 dB,
  44 ≈ −12 dB, 99 = 0 dB). PipeWire's slider is software gain; keep it at 100 %.
- Mute: a short knob press, or `amixer -c Creative sset Master toggle`. The display shows `MUTE`.
- The session defaults set Master to 18 at every login. To change that, override the service
  environment: `systemctl --user edit ae9-defaults`, then add
  ```
  [Service]
  Environment=AE9_LEVEL=30
  ```
  (`AE9_OUT=Speakers` selects the rear line-out instead; not yet audible, see Known issues).

### Output
- `amixer -c Creative sset 'AE-9 Output' Headphone|Speakers`, or hold the knob 1.5–3 s.
- The control is deliberately not called "Output Select": the desktop's ALSA card profiles map
  that name onto sink ports and rewrite it on every route restore, flipping the ACM relay.

### Effects (SBX)
- Master switch: the SBX button, or `amixer -c Creative sset 'Enable OutFX' on|off`. The
  button light follows the real state. It is **off at every login** (the defaults service
  turns it off) so the clean path is what you get by default.
- **The per-effect controls only act while the master switch is on.** With the light off the
  driver forces every output processor off in the DSP; the individual switches just remember
  what to re-enable when it comes back on.
- Switching the master on enables the processors below at Creative's stock levels. Each has
  a switch (`sset 'FX: <name>' on|off`) and a level 0–100 (`sset 'FX: <name>' <n>`).

  | Processor | What it does | Stock | Notes |
  |---|---|---|---|
  | `FX: Crystalizer` | Restores transients: sharper attacks, brighter top, more punch | on, 65 | |
  | `FX: Dialog Plus` | Lifts the vocal / dialogue midrange | on, 50 | |
  | `FX: Smart Volume` | Loudness leveler: rides the gain up in quiet passages and down in loud ones | on, 74 | Causes the "volume going up and down" feel. `FX: Smart Volume Setting`: Normal / Loud / Night |
  | `FX: X-Bass` | Bass enhancement below the crossover | on, 50 | Crossover `FX: X-Bass Crossover` 1–100, default 8 (≈ 80 Hz) |
  | `FX: Surround` | Virtual surround / stereo widening | off, 67 | `Surround Channel Config` stays 2.0 for headphones |
  | `FX: Equalizer` | 10-band EQ by preset | on, Flat | `FX: Equalizer Preset`: Flat, Acoustic, Classical, Country, Dance, Jazz, New Age, Pop, Rock, Vocal. Flat is all zeros |

  Capture-side processors (`FX: Voice Focus`, `FX: Noise Reduction`, `FX: Mic SVM`) exist but
  the mic inputs are untested.
- Examples, with effects on and music playing:
  ```
  amixer -c Creative sset 'FX: Smart Volume' off     # stop the level pumping
  amixer -c Creative sset 'FX: X-Bass' off           # or a lower value, e.g. 20
  amixer -c Creative sset 'FX: Crystalizer' 30       # keep some sparkle, less punch
  amixer -c Creative sset 'FX: Equalizer Preset' Rock
  ```
  If a change does not seem to take, pause playback for a few seconds and resume: some DSP
  settings are picked up at stream start.
- Persistence: the `FX:` switches and levels are ordinary ALSA controls, so the distribution's
  ALSA state handling keeps them across reboots (on Ubuntu `alsactl` stores at shutdown and
  restores at boot; `sudo alsactl store` saves the current combination right away). The master
  switch, Master level and routing are re-applied by `ae9-defaults` at every login regardless.
  Not yet verified across a reboot on this card — check with `amixer -c Creative sget 'FX: X-Bass'`
  after the first one.

### Session defaults service
`ae9-defaults` (user service, oneshot) runs at login: waits for the AE-9, selects its analog
profile if WirePlumber did not, selects `AE-9 Output` = Headphone, turns effects off, sets
PCM/Front/Surround to 100 % and Master to `AE9_LEVEL`, then makes the AE-9 analog sink
PipeWire's default at unity and re-checks once after a late route restore.
`systemctl --user restart --no-block ae9-defaults` re-applies everything (it takes up to
~45 s; `--no-block` returns at once). `journalctl --user -u ae9-defaults` shows what it did.

### Checking state
```
amixer -c Creative sget Master; amixer -c Creative sget 'AE-9 Output'; amixer -c Creative sget 'Enable OutFX'
wpctl status                       # the AE-9 analog sink should be the starred default
dmesg | grep 'AE-9 ACM'            # knob, button and keepalive log lines
```
Button events are logged as `AE-9 ACM: SBX button (146 ms) -> OutFX on`,
`knob press (158 ms) -> mute toggle`, `knob mid press (1744 ms) -> output toggle`.

### ACM service
`ae9_acm_poll=1` (default, `/etc/modprobe.d/sb-ae9.conf`) runs the ACM service: knob, display,
buttons, Windows-style keepalive. `ae9_acm_poll=0` initialises the module once and leaves it
alone (diagnostic baseline). It can be parked at runtime for the diagnostics tool with
`echo 0 | sudo tee /sys/module/snd_hda_codec_ca0132/parameters/ae9_acm_poll`; resuming needs
a reboot.

### Diagnostics (`tools/acm-tool`)
A throw-away kernel module that maps the card's BAR2 and talks to the ACM UART, the GPIO
block and the DAC I2C command engine directly. `make`, then `sudo ./poke.sh <params>`; it
loads once, prints its log lines and unloads. **Park the driver's ACM service first** and
read Hazard 4. Modes:
- `acm=3` — UART-only bring-up (safe on a live card). Combine with:
  `acmget=<reg>` read an ACM register; `acmset=<reg>,<value>,<mask>` write bits;
  `acmtext=<up to 8 chars>` write the display; `raw=f0,22,02,01,00,f7` send one frame
  and print the reply.
- `i2cdump=0x48` (ES9038Q2M headphone DAC) / `i2cdump=0x49` (SABRE9006 line-out DAC),
  `i2c=<group>,<reg>,<val>` — DAC register access through the command engine.
- `acm=1` / `acm=2` — full Windows-style bring-up **including the GPIO pulse that resets
  both DACs**; only for a card without the driver loaded.

## What the driver adds over the kernel's ca0132
For readers of `patches/0002` and `driver/ca0132/ca0132.c` (`QUIRK_AE9`):
- Card bring-up replayed from the Windows driver: DSP master-control sequence, ASI/PLL and
  DAC configuration over the BAR2 command engine, the 8051 router hooks and the
  output-connect overlay loaded via `request_firmware("ae9-dsp-output-overlay.bin")`.
- The ACM protocol over the 16550 in BAR2 (115200 baud, sysex-style frames): presence probe,
  keepalive frames, register read/write for the headphone-amp bit, display text, SBX light,
  button/knob event polling. ACM power-up writes GPIO pin 5 only; the Windows pin-0 pulse
  is never issued because it resets both DACs.
- Volume: Windows' 101-entry dB table applied on the DAC amp; the DSP volume stage parked at
  0 dB at bring-up (it latches at stream start); the knob writes the `Master` control.
- Controls: `AE-9 Output` (renamed so desktop card profiles cannot rewrite it), effects
  master default off with an explicit effects-off push at bring-up.
- Desktop: the headphone pin is marked "no presence detect", which gives ALSA a
  `Headphone Phantom Jack` (always plugged) so the analog profile is available and
  WirePlumber picks it and accepts the analog sink as default.
- Robustness: codec runtime PM held (the ACM service dies in D3 otherwise) and the ACM work
  rescheduled on resume; DSP command-engine access serialized with a mutex (an unserialized
  knob/mixer collision wedged the DSP); all UART waits sleep, no busy-waiting, work on the
  unbound workqueue; only the amp bit is written to the ACM (the module's own selector items
  are read-only).
- HDA core: `patches/0001` flushes posted writes in the interrupt clear path; without it the
  stock core hard-locks the machine on this controller.

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
