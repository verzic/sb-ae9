# Sound Blaster AE-9 on Linux — Investigation (2026-09-03)

## Hardware identity (verified)

| Item | Value |
|---|---|
| PCI address | 0000:0a:00.0 |
| PCI ID | 1102:0010 (Creative HD Audio controller, "CTHDA") rev 01 |
| Subsystem | 1102:0071 |
| Model (from Windows INF) | **Sound Blaster AE-9** (`Creative.SoundBlasterXAE9`) |
| BARs | 2 x 16 KiB MMIO at 0xf2000000 / 0xf2004000 (64-bit, non-prefetchable) |
| IOMMU group | 17 |
| Kernel | 7.0.0-30-generic (Ubuntu HWE) |
| Driver bound | none (see blacklist below) |

Related IDs from the Windows INF (`ctxhda.inf`, DriverVer 11/24/2022 6.0.105.0065):

- `PCI\VEN_1102&DEV_0010&SUBSYS_00711102` -> "Sound Blaster Audio Controller" (HDABusFilter, CtxHdb.sys)
- `HDAUDIO\FUNC_01&VEN_1102&DEV_0011/0013/0015&SUBSYS_11020071` -> "Sound Blaster AE-9" (SoundCore3D, CtxHda.sys)
- `HDAUDIO\FUNC_01&VEN_1102&DEV_0011/0013/0015&SUBSYS_11020072` -> "Sound Blaster AE-9s" (MSHDAudio = generic MS HDA class driver; analogous to the ZxR "DBPro" second codec)
- 0073/0074 are the AE-9PE variants.

The codec is a CA0132 (Sound Core3D); Linux `snd-hda-codec-ca0132` matches `hdaudio:v11020011`.

## Why Linux sees nothing today

1. `/etc/modprobe.d/blacklist-snd.conf` contains `blacklist snd_hda_intel` and `blacklist snd_soc_core`.
   This is a leftover from VFIO passthrough of this exact card (`/etc/modprobe.d/vfio.conf.bak` lists `1102:0010`).
   It also hides the AMD and NVIDIA HDA controllers; only USB audio works now.
2. `snd_hda_intel` in kernel 7.0 *does* claim 1102:0010 (`sound/hda/controllers/intel.c`, AZX_DRIVER_CTHDA).
3. `snd-hda-codec-ca0132` has no quirk for subsystem 0x0071. The quirk table covers SBZ/ZxR/R3D/R3Di/AE-5/AE-5 Plus/AE-7 only,
   so the codec would fall through to QUIRK_NONE (Alienware/laptop-style generic init), which is wrong for a desktop card.
4. Firmware: only `/lib/firmware/ctefx.bin.zst` and `ctspeq.bin.zst` are installed. The driver prefers `ctefx-desktop.bin`
   for desktop cards and falls back to `ctefx.bin`.

## Prior art

- Linux Mint forum thread (2024): adding `SND_PCI_QUIRK(0x1102, 0x0071, "Sound Blaster AE-9", QUIRK_AE7)` compiled and
  produced AE-7 style sinks, but **no audio worked**, and the poster noted extra initialisation Windows does at startup.
  https://forums.linuxmint.com/viewtopic.php?t=435701
- AE-5 patch series (Conmanx360, 2018) and AE-7 support (5.10) are the closest in-tree references.
  https://lkml.iu.edu/hypermail/linux/kernel/1809.2/02714.html
- No upstream AE-9 support as of kernel 7.0.

## What is different about the AE-9 (from public specs + driver strings)

- DAC: ESS ES9038PRO (AE-7 uses ES9018, AE-5 uses ES9016). DAC register writes go through the CA0113 MMIO/I2C path
  in `ca0132.c` (`ca0113_mmio_command_set`, `ae7_post_dsp_*`, `ae5_*` functions); ES9038 register map differs.
- Audio Control Module (ACM): external box with mic preamp, headphone jack, volume knob. `CtxHda.sys` contains the strings
  `AE-9`, `Acm1`, `portacm`, so the Windows driver has dedicated ACM handling.
- Second codec function "AE-9s" (subsystem 0x0072) driven by the generic Microsoft HDA driver on Windows.
  Whether this appears on the same HDA link or is the ACM is unknown until we dump the codec graph.

## Reference material collected

`~/sb-ae9-linux/reference/`

- `windows-driver-6.0.105.0065/` — ctxhda.inf, CtxHda.ini, CtxMLX64.hda, CtxRFX64.hda, and AMD64/ binaries
  (CtxHda.sys 1.2 MB main driver, CtxHdb.sys bus filter, CtxHdC64.dll, CtxRFX64.dll, CtxDco64.dll, CtxSvc64.exe, ...)
- `windows-driver-6.0.105.0055/` — older INF/INI (09/07/2020) for diffing
- `kernel-7.0/` — `ca0132.c`, `ca0132_regs.h` (codec driver) and `intel.c` (HDA controller) from v7.0

Windows C: is mounted read-only at `/media/m/ACDC6691697845A8` (udisks, ntfs3). Full driver package also at
`/media/m/ACDC6691697845A8/Program Files (x86)/Creative/Sound Blaster AE-Series Driver/`.

## Recommended next steps (need root; sudo is not available to the agent in this session)

1. Un-blacklist HDA and load the stock driver to get a codec dump:
   ```
   sudo mv /etc/modprobe.d/blacklist-snd.conf /etc/modprobe.d/blacklist-snd.conf.bak
   sudo modprobe snd_hda_intel
   dmesg | grep -iE 'hda|ca0132|0a:00'
   ls /proc/asound/; cat /proc/asound/card*/codec#* > ~/sb-ae9-linux/reference/codec-dump-stock.txt
   ```
   The codec dump (node IDs, pin configs, subsystem IDs of every codec on the link) is the single most important input
   for writing the AE-9 quirk.
2. Build `snd-hda-codec-ca0132` out-of-tree with a `QUIRK_AE9` entry (start from QUIRK_AE7 code paths) rather than
   rebuilding the whole kernel.
3. Reverse-engineer `CtxHda.sys` (Ghidra) for: AE-9 DAC (ES9038) init sequence, ACM port init (`portacm`/`Acm1`),
   output-select MMIO values, and the embedded DSP firmware blob (compare to ctefx.bin).
4. Optionally capture HDA verb traffic from Windows (e.g. run Windows in the existing VM with the card passed through and
   trace via QEMU) to get the exact init sequence instead of static RE.

## Incident 2026-09-03 13:46 — un-blacklisting snd_hda_intel hard-locked the machine

What happened (from journals of boots -1 and 0):

1. The blacklist rename worked; `modprobe snd_hda_intel` then probed 0a:00.0.
2. The controller init (`snd_hdac_bus_init_cmd_io` -> `azx_clear_corbrp`) polls the CORB read-pointer register up to
   1000 times **under `spin_lock_irq`**. Every MMIO read of the AE-9 returned all-ones after ~0.22 s, so the loop ran
   ~3.7 minutes with interrupts off: soft lockups on the udev worker, then a hard lockup (boot -1 died at 13:47:34).
3. Boot 0 auto-loaded the driver (no blacklist any more) and repeated the stall: `CORB reset timeout#2, CORBRP = 65535`,
   `no codecs initialized`. Ten seconds into the stall the sibling chipset USB controller **0e:00.0 died**
   (`xHCI host controller not responding, assume dead`). That controller carried the mouse and the iFi GO blu.
4. Afterwards the card's PCIe-to-PCI bridge 09:00.0 and the card itself return 0xFF even in config space
   (lspci: `!!! Unknown header type 7f`), while the downstream port 08:00.0 still reports link up (2.5 GT/s x1).
   PME polling of the dead bridge keeps hogging CPUs (`pci_pme_list_scan hogged CPU`).

Conclusions:

- The AE-9 answers PCI config cycles but not memory cycles on this machine under Linux. That is a hardware/platform
  problem, not the missing codec quirk. The May 2025 blacklist was almost certainly created for exactly this hang.
- Same symptom class reported by others: Arch thread "How to prevent initialization of Sound Blaster AE-9 that
  prevents boot" (Intel board, card fine in Windows, no fix found): https://bbs.archlinux.org/viewtopic.php?id=300652
- Card sits behind an ASMedia ASM1083 PCIe-to-PCI bridge (on the card) behind the AMD 600-series chipset switch
  (00:02.1 -> 03:00.0 -> 04:08.0 -> 07:00.0 -> 08:00.0 -> 09:00.0 -> 0a:00.0). Firmware's window for that whole
  subtree conflicts with ACPI device AMDIF031 at 0xdc200000, so Linux re-assigns the subtree to 0xf2000000
  (kernel cmdline has `pci=realloc=off`; origin unknown). Siblings (USB, NIC, Wi-Fi, SATA) work at the new addresses.

Mitigation (in place as a file to install): `ae9-nobind.conf` -> `/etc/modprobe.d/`, `options snd-hda-intel enable=1,0,1,1`.
This keeps NVIDIA/AMD HDA working and skips only the AE-9. Delete it to re-test the card.

Things to establish before any driver work (all need root or hands-on):

1. Is the 6-pin PCIe power cable connected to the AE-9? Creative requires it; without it parts of the card are unpowered.
2. Does the card work in Windows on this box right now (Device Manager, no code 10/12)? The Windows registry check
   could not run because the read-only mount vanished with the reboot; remount with
   `udisksctl mount -b /dev/nvme0n1p5 -o ro` and re-run the regipy script.
3. Try the card in a CPU-attached x16/x4 slot instead of a chipset slot, and/or BIOS: slot link speed Gen1/Gen2,
   Above-4G decoding / Resizable BAR off, CSM on. Also try booting without `pci=realloc=off`.
4. Only once MMIO reads answer (a signed test module, since Secure Boot lockdown blocks /dev/mem and setpci writes)
   does the codec quirk work start. MOK key exists at /var/lib/shim-signed/mok/MOK.priv (root only).

## Probe tool (2026-09-03 14:46) — `probe/ae9probe.ko`

Tiny PCI driver for 1102:0010 only. Enables memory decode, maps BAR0 (or BAR2 with `bar=2`), does six individually
timed reads (GCAP, VMIN, VMAJ, GCTL, STATESTS, CORBRP) with interrupts enabled, and prints the ASM1083 bridge's
secondary status (master-abort / target-abort bits) before and after. Worst case ~1.5 s if every read stalls.
Optional params: `set_master=1`, `bridge_reset=1` (secondary bus reset on 09:00.0 first).

Run: `sudo ./probe/run.sh [bar=2] [set_master=1] [bridge_reset=1]` — signs with the enrolled MOK key
(same key as the NVIDIA DKMS module), loads, captures dmesg to `reference/probe-<timestamp>.log`, unloads.
It refuses to run if the card already reads 0xFF in config space or is bound to another driver.

Reading the result:
- GCAP = 0xffff and ~200000 us per read: same failure as the stock driver (bus-level stall, then all-ones).
- GCAP = 0xffff and a few us per read: fast master abort — card not decoding the BAR address at all.
- GCAP plausible (e.g. 0x4401) and VMAJ = 1: card answers; the stock driver problem is elsewhere (IRQ, DMA, timing).
- Bridge sec_status RMA=1 after the read: the conventional PCI side master-aborted (no DEVSEL from the CA0132).

Test sequence (one variable per reboot, baseline first):
1. Baseline, current kernel args.
2. `pcie_aspm=off` added to the kernel command line (edit /etc/default/grub, update-grub).
3. `bar=2`, then `bridge_reset=1`, on whichever configuration answers.
4. Only if still dead: card in a CPU-attached slot, or BIOS PCIe slot speed forced to Gen1/Gen2.
Also capture once, in the healthy state: `sudo lspci -vvvxxxx -s 08:00.0 -s 09:00.0 -s 0a:00.0 > reference/lspci-healthy.txt`
(LnkCtl ASPM bits, PM state, bridge control) — non-root lspci cannot read capabilities.

## 2026-09-03 15:37 — baseline probe: the card answers MMIO

`probe-20260903-153753.log`: BAR0 at 0xf2000000 reads in 3 µs each. GCAP=0x6401 (6 out / 4 in streams, 64-bit OK),
VMAJ.VMIN=1.0, GCTL=0 (controller held in reset, power-on state), STATESTS=0x0006 (codecs at SDI addresses 1 and 2 =
"AE-9" and "AE-9s" functions), CORBRP=0. Bridge 09:00.0 secondary status clean (no master/target aborts) before and
after. Device came up with cmd=0x0000, PM D0, IRQ 24 assigned on enable.

So MMIO routing, the relocated BARs and the ASM1083 are fine. The stock driver dies *after* this point; its remaining
steps are: pci_set_master, snd_hdac_bus_reset_link (GCTL.CRST 0 -> 1), then CORB/RIRB setup + CORBRP polling.
Probe module now has `step=1` (exit reset only), `step=2` (enter+exit like the stock driver), `bar2dump=1` (read
BAR2 0x00-0x3c). If `step=1` makes reads go to 0xFFFF, taking the controller out of reset is the trigger.

Windows side: the INF installs `CtxHdb.sys` as a *lower filter* on the PCI controller device (HDABusFilter). Its
strings show it applies registry/built-in tables named PCIConfig / PCIeConfig / IDTConfig (offset/data pairs,
DoPCIConfig/DoPCIeConfig) and InitVerbs, keyed by CodecType_11020071 = "XAE9", and it maps IO space itself
(MmMapIoSpace). The INF only sets the CodecType tag; the tables are compiled in. Static analysis of CtxHdb.sys is
in progress -> `reference/ctxhdb-analysis.md`. Hypothesis: the filter performs a card-specific pre-init (PCI
config or BAR2 writes) that Linux never does, and without it the CTHDA controller wedges its PCI interface when
CRST is set.

## 2026-09-03 15:42 — bridge capability dump (`lspci-healthy.txt`) and BAR2

- 08:00.0 (AMD switch downstream port): LnkCtl ASPM Disabled, L1SS off, link 2.5 GT/s x1, DevSta CorrErr+ UnsupReq+.
- 09:00.0 (ASM1083 on the card): LnkCtl ASPM Disabled, MaxPayload 128, DevSta NonFatalErr+ (already this boot),
  secondary PCI bus 66MHz+, no aborts. => ASPM hypothesis ruled out; `pcie_aspm=off` test dropped.
- 0.2 s per stalled read during the crash ~= 2^24 cycles at 66 MHz: consistent with the bridge's retry/discard
  timer expiring on a target that keeps retrying, not with a fast master abort.
- BAR2 (0xf2004000) reads fine: 0x00=0x00000901 0x04=0x7172000b 0x08=0x00000004 0x10=0x00000001 0x1c=0x00880480
  0x30=0x00000096 0x34=0x080d490a 0x38=0x00000300, rest 0 (`probe-20260903-154152-bar2dump-1.log`).

## 2026-09-03 15:46 — KEY: full stock init replay does NOT hang (warm)

`probe-20260903-1546*-step-{2,3,4,5}.log`. Replayed the entire snd_hdac_bus_init_chip sequence with a timed read
after every write:
- step 2 enter+exit reset: OK, GCTL 0->1, both codecs present (STATESTS=0x0006).
- step 3 azx_int_clear (10x SD_STS, STATESTS, RIRBSTS, INTSTS): all 3 us, no abort.
- step 4 CORB setup + CORBRP reset poll: CORBRP went 0x8000 then 0x0000 cleanly (this is the exact op that returned
  0xFFFF / "CORB reset timeout, CORBRP=65535" during the boot-0 crash). No stall.
- step 5 CORBCTL run + RIRB setup + RIRBCTL DMA + GCTL.UNSOL, set_master=1: all writes 3 us, DMA engines started,
  post-run reads fine. Bridge secondary status clean throughout.

Conclusion: on a warm, quiescent system every register/DMA op the stock driver performs succeeds. The boot-time
hang is therefore condition-dependent, not sequence-dependent. Leading hypotheses:
1. Cold-boot / post-Windows-handoff timing: card (ES9038 + external ACM) not ready in the first seconds; early MMIO
   through the ASM1083 aborts (returns 0xFFFF). Warm, it is solid.
2. The stock driver's fatal amplifier: azx_clear_corbrp polls up to 1000x under spin_lock_irq (IRQs off), so a
   transient all-ones read becomes a multi-minute hard lockup + collateral xHCI death instead of a clean -EBUSY.

Decisive next experiment: bind the real snd_hda_intel to 0a:00.0 NOW (warm) under a timeout, capture, unbind.
- If it binds and a card appears: the driver works warm; the boot hang is cold-timing. Fix = a quirk that adds a
  delay / extra controller reset before init, or defer/az-probe retry. Card is likely usable on Linux.
- If it still hangs warm while the probe does not: difference is IRQ/MSI or the spinlock poll; fix targets that.
Risk: low (all ops proven warm) but non-zero; a hang needs a reboot and may drop chipset USB again. nobind stays
installed so the *next* boot is safe regardless.

## 2026-09-03 15:51 — codecs respond; and the INF says AE-9 == AE-5, not AE-7

`probe-20260903-155104-verbs-1.log`: immediate-command verbs to codec addresses 1 and 2 both return
**VENDOR_ID = 0x11020011** (Creative CA0132 Sound Core3D, exactly the `hdaudio:v11020011` alias that
snd-hda-codec-ca0132 binds to), REV_ID = 0x00100918 (maj 1, min 0, rev 0x09, step 0x18), NODE_COUNT = 0x00010001
(one function group at node 0x01). Verb latency 39-60 us. (The two 0x0000 answers were bad verb choices on my
part: CONFIG_DEF and VENDOR_ID are not valid on those nodes.)

=> Controller, link, codec command interface and both codecs all work warm. Nothing here is incompatible.

### Quirk-base decision from Creative's own INF (`[CodecConfig.AddReg]`)

The GPIO/mapping block for XAE9 (subsys 11020071) is **byte-identical to XAE5** (11020051), and also to
XAE3/XAE5PLUS/XAE9PE:

    AntiPop_MutePin            0x00000000
    ExternalDACReset_Pin       0x00000005
    ExternalDAC_192KSR_SelPin  0x00000001
    FP_DetectPin               0x03010002
    Front_MutePin              0x00010007
    HPAMP_SHDNPin              0x02010004
    UseSPDIFDAC                1
    UseAltDACMapping           0     AltDACMapping        0xff010200
    UseAltPortMapping          1     AltPortMultiChMapping 0x02020100
    AltPortDualHPMapping       0x02020001

XAE7 (11020081) differs on exactly three: UseSPDIFDAC=0, UseAltDACMapping=1, AltPortDualHPMapping=0x02020100.
AE-9 additionally has Mic1MaxBoost=+20dB (like AE-7) and, unlike AE-5, no LineOutEx MaxVolume clamps.

**=> QUIRK_AE5 is the better base for a QUIRK_AE9, not QUIRK_AE7.** The Linux Mint attempt used QUIRK_AE7, which
may be part of why it produced sinks but no sound. (Caveat: the DAC differs - AE-9 uses ES9038PRO vs AE-5's
ES9016/ES9018 - so the ca0113/DAC command sequences still need verification against CtxHda.sys.)

Probe now has `dump=1`: full codec graph (function group, subsystem id, AFG/proc/GPIO caps, GPIO mask/dir/data,
and every widget with wcaps, amp caps, pin caps, pin defaults, connection lists). This is the artifact the quirk
work needs and replaces the `/proc/asound/card*/codec#*` dump we could never get from the stock driver.

## 2026-09-03 16:00 — codec dump + CtxHdb.sys analysis: the driver design is now determined

### Codec dump (`probe-20260903-155929-dump-1.log`)

Two CA0132 codecs, both vendor 0x11020011, rev 0x00100918, one AFG each at node 0x01, 23 widgets each
(0x02-0x18), AFG caps 0x00010101, Proc caps 0.

| | codec @SDI1 | codec @SDI2 |
|---|---|---|
| Subsystem Id | **0x11020071** (AE-9 main) | **0x11020072** (AE-9s daughterboard) |
| GPIO cap | 0xc0000003 (3 GPIOs), data=0x4 | 0xc0000000 (none) |
| pins wired to real jacks (conn=0) | 0x0b Speaker, 0x12 Mic In | 0x0c SPDIF Out, 0x0e SPDIF In, 0x11 Line In |
| node 0x05 digital converter | 0x00000000 | **0x00801215 (active)** |
| node 0x0b EAPD | 0x00 | 0x02 |
| node 0x08 (ADC) | Amp-In 0x00031700 | no amp |

Nodes 0x15/0x16 are the vendor-defined widgets ca0132.c calls WIDGET_CHIP_CTL / WIDGET_DSP_CTL. Node 0x15
"Digital" reads 0x000052f7 (codec 1) / 0x000093fa (codec 2).

Codec 2's shape (ADC 0x08, Line-In pin 0x11, dig out 0x05, dig in 0x09) matches ca0132.c's existing
**QUIRK_ZXR_DBPRO**配置 almost exactly, and Windows binds that codec to the *generic* MS HDA driver, not to
SoundCore3D. So the AE-9 is structurally the ZxR + DBPro case: one full codec plus one minimal secondary.

### CtxHdb.sys (Creative's Windows lower filter) — `reference/ctxhdb-analysis.md`

What it does at IRP_MN_START_DEVICE on this exact card, before Microsoft's HDA bus driver runs:
- **A.** 16-bit PCI config write **cfg[0x40] = 0x0080**, gated on VEN/DEV == 1102:0010. Our card currently
  reads **0x8080** there, so Windows clears bit 15. Nothing in Linux does this.
- **B.** BAR2 (ca0113) init: address list `0x400,0x42c,0x46c,0x4ac,0x4ec,0x43c,0x47c,0x4bc,0x4fc,0x408,0x100,
  0x410,0x40c,0x100,0x100,0x830,0x86c,0x800,...,0xc00,0xc04,0xc08,...` — **register-for-register identical to
  `ca0113_mmio_init_address_ae5[]` / `_data_ae5[]` already in Linux's ca0132.c.** (The 0xc00 block is a 16550
  UART run at 31250 baud, i.e. MIDI rate — very likely the link to the external Audio Control Module.)
- **C.** Controller pass: BAR2[0x1c] RMW, CRST cycle (only if already out of reset), read STATESTS, then
  CORBCTL=0, RIRBCTL=0, IRS=0x05/0x00/0x04, read codec vendor+subsystem IDs, and finally CORBCTL=0x03,
  RIRBCTL=0x07, **GCTL.CRST=0** — the controller is handed to the bus driver *in reset*.
- Built-in verb tables are only sent when the codec vendor ID is 0x11020013 or 0x11020015. Ours is
  **0x11020011**, so **no proprietary verb table is used on this card at all.** Nothing secret is required.

**=> Linux already contains the entire BAR2 init this card needs; it is gated behind QUIRK_AE5.** The AE-9 has
never reached it only because no quirk maps subsystem 0x0071. This independently confirms the INF finding that
AE-5, not AE-7, is the correct base.

### Consequences for the driver

1. No ca0132.c patch is needed for a first functional test: `snd-hda-intel model=ae5` forces QUIRK_AE5 via
   `ca0132_quirk_models[]`, which is exactly the wanted behaviour.
2. The only genuinely missing pre-init is cfg[0x40]=0x0080 (+ the controller pass). Implemented as
   `ae9probe preinit=1`.
3. A proper upstream patch later: QUIRK_AE9 (clone of AE5 + its own pincfgs/name) and an `ae9_detect_quirk()`
   keyed on codec subsystem id, mapping 0x11020072 to a DBPro-style minimal secondary, mirroring
   `sbz_detect_quirk()`.

## 2026-09-03 16:08 — AE-5 quirk attempt CRASHED (warm). Cold-timing hypothesis falsified.

`try-ae5-quirk.sh` ran preinit successfully, then `modprobe snd_hda_intel enable=1,1,1,1 model=ae5,...`
hard-locked the machine at exactly the same place as the boot crashes. Last journal line:
`snd_hda_intel 0000:0a:00.0: Force to non-snoop mode`, then nothing (hard lockup, nothing flushed).
No pstore/kdump configured, so no post-mortem was captured.

**This falsifies the cold-boot / power-up-timing hypothesis.** The stock driver hangs warm, on a quiescent
system, immediately after a full replay of Creative's own filter init. Cold vs warm is not the variable.

### Two concrete discoveries from the preinit logs

1. **cfg[0x40] was already 0x0080.** My earlier reading of 0x8080 from the lspci hex dump was wrong (that
   dump shows bytes 0x40=0x80, 0x41=0x80, but the live 16-bit read is 0x0080). So this config write is a
   no-op on this card and is NOT the missing piece.
2. **The AE-5 BAR2 table is wrong for the AE-9 at offset 0x1c.** `ca0113_mmio_init_data_ae5[]` hardcodes
   `0x00880680` at address 0x01c (entries 22 and 36). The AE-9 powers up with `0x00880480`. After the AE-5
   table ran, **STATESTS went 0x0006 -> 0x0002: the second codec disappeared.** Creative's filter never does
   this - its C1 step is a mask-and-OR (`&0xff00ffff | 0x00880000`) that *preserves* the card's low half.
   This matches CtxHdb's C3 branch, which only writes an absolute 0x00880480/0x00880680 on a reference board.
   => An AE-9 quirk must preserve 0x1c's low half, not use the AE-5 constant. `ae9probe preinit` now does.

### New leading hypothesis: access *timing*, not access *content*

Everything the stock driver does, our probe has done successfully - but never at the same speed. Every probe
access is separated by a `dev_info()` (tens of microseconds of printk). The stock driver runs
`azx_int_clear()` + `snd_hdac_bus_init_cmd_io()` as a tight back-to-back burst under `spin_lock_irq`.

The CA0132 is a *conventional PCI* device behind an ASMedia ASM1083 PCIe-to-PCI bridge. Rapid back-to-back
posted writes followed immediately by a read is exactly the pattern such a bridge handles worst. A stalled
read of ~0.2 s matches a PCI delayed-transaction/discard timer, not a fast master abort. And the original
crash signature fits: one CPU stuck in `snd_hdac_bus_init_cmd_io` with IRQs off, ten CPUs piled up in
`smp_call_function_many_cond` unable to complete the `set_memory_uc()` IPI that non-snoop mode
(`chip->uc_buffer = true`, from AZX_DCAPS_SNOOP_OFF on CTHDA) triggers for the ring buffers.

Test added: `ae9probe fast=1` (burst, IRQs on) and `fast=2` (burst, IRQs off). Both replay the exact stock
sequence with zero printk between accesses, and are hard-capped at 10 ms of wall clock plus a bounded
iteration count, so neither can reproduce the multi-minute IRQs-off spin.

Prediction: if timing is the cause, CORBRP reads return 0xffff in the burst test while the identical
spaced-out sequence (step=4) returns 0x8000/0x0000 correctly.

## 2026-09-03 16:20 — burst, IRQs-off and TCSEL all pass; DMA is the one untested path

- `fast=1` / `fast=2` (`probe-*-fast-{1,2}.log`): the exact stock int_clear + init_cmd_io burst with no printk,
  with and without IRQs, completes in 15 us, CORBRP 0x8000 -> 0x0000 correct. **Timing hypothesis falsified.**
- `tcsel=1` (`probe-*-tcsel-1.log`): azx_init_pci's RMW of cfg[0x44] is a no-op here (0x00 -> 0x00) and
  changes nothing. **TCSEL hypothesis falsified.** (AZX_DCAPS_NO_64BIT *is* in the CTHDA preset, so the
  GCAP 64OK oddity - bit set before reset, clear after - is moot for the stock driver.)

### What has never been exercised: bus-master DMA

Every probe test so far is MMIO only. In step 5 the CORB/RIRB engines were started but CORBWP == CORBRP == 0,
so the card never fetched anything: **this card has never performed a single DMA transaction under Linux.**
The stock driver's first DMA is the CORB fetch of its first verb, right where it dies, and the crash tail shows
STATESTS-based codec detection succeeded but every verb failed ("no AFG or MFG node found" on D1 and D2).

The platform makes DMA the prime suspect:
- AMD-Vi IOMMU is active in **Translated** mode (default domain, DMA-FQ).
- The AE-9 is behind an ASM1083 PCIe-to-PCI bridge; Linux's `quirk_use_pcie_bridge_dma_alias` makes its DMA
  appear with the *bridge's* requester ID (09:00.0).
- **IOMMU group 17 contains 14 devices, including the xHCI controller 0e:00.0** that carries the mouse and the
  GO blu - the exact device that died 10 s into both crashes. A faulting/aliased DMA from the card wedging the
  shared group is the first hypothesis that explains that collateral damage.

Test: `ae9probe corbverb=1` queues one verb in the CORB ring and waits (100 ms cap) for the card to (a) DMA-read
it (CORBRP advances to 1) and (b) DMA-write the response into the RIRB (RIRBWP advances, RIRB[0] = 0x11020011).
If (a) fails, the card cannot master the bus through the bridge/IOMMU; if (a) works but (b) fails, writes are
the problem. Cheap follow-up if DMA is the culprit: boot with `iommu=pt` (identity map for host devices, still
fine for VFIO) and re-run.

Operator note (16:22): the external Audio Control Module is currently unpowered under Linux. On Windows the
driver enables it; expect this to be a GPIO/EAPD/ca0113 step in the codec quirk, not a controller issue.

## 2026-09-03 16:27 — CORB/RIRB DMA works (`probe-*-corbverb-1.log`)

One verb queued at CORB[1], doorbell rung: CORBRP advanced to 1 (card DMA-read the command), RIRBWP advanced
to 1 and RIRB[2..3] = 0x11020011 / 0x00000001 (card DMA-wrote the response; resp_ex = codec addr 1), 43 us
round trip, bridge clean, no IOMMU fault. **Bus mastering through the ASM1083 and the AMD IOMMU works both
ways. DMA/IOMMU hypothesis falsified.**

Status: every individual operation in the stock probe path now passes in isolation (MMIO, reset, int_clear,
CORB/RIRB setup, CORBRP reset poll, burst timing, IRQs off, TCSEL, DMA read+write, verbs via ICW and via
CORB). Re-reading the boot-0 crash tail: codec_mask came out as exactly {1,2}, so the STATESTS read ~1 ms
after reset exit *worked*; MMIO died within the next few hundred microseconds, inside init_cmd_io.
Remaining sequence-level difference: the stock driver reaches the CORB writes ~1.5 ms after CRST=1; every
probe test so far waited >= 20 ms plus printk there, and Creative's filter sleeps 10 ms at the same point.
Test: `ae9probe stockinit=1` = snd_hdac_bus_init_chip with the stock timing, values (INTSTS 0xbfffffff,
RIRBCTL DMA|IRQ, INTCTL |= 0xc0000000, DPLBASE/DPUBASE), IRQs-off block, WC ring (dma_alloc_wc), 10 ms cap.
If this also passes, the cause is environmental (concurrent probes of the other 3 HDA controllers, or IRQ 24
shared with the ASM1083 and others) and the next step is capturing the real crash rather than more replay.

## 2026-09-03 16:31 — REPRODUCED under control (`probe-20260903-163130-stockinit-1.log`)

`stockinit=1` (stock post-reset timing ~1.5 ms, WC ring, IRQs-off block) wedged the card exactly like the
crashes: post-reset GCTL=0x01 and STATESTS=0x0006 read fine (3 us), then the first CORBRP poll returned
**0xffff after 220,614 us**, and from then on every read of the card and of the ASM1083's config space is
0xff at ~221 ms each (PCIe completion timeout). The 10 ms cap bailed after 2 reads (441 ms) and the machine
stayed up. Card + bridge stay dead until reboot.

The wedge happens between the STATESTS read and the first CORBRP read, i.e. during azx_int_clear + the CORB
base/size/WP/RP writes - the same window as the stock driver.

Differences between this failing block and the passing `fast=2` block (same writes, IRQs off):
1. delay after CRST=1: ~1.5 ms (stock) vs >= 20 ms + printk
2. INTSTS clear value: I wrote 0xbfffffff here (a mistake); stock writes 0x400000ff; fast=2 wrote 0xc00000ff
3. GCTL exit-reset via byte RMW (stock) vs 32-bit write
4. ring memory write-combining (dma_alloc_wc) vs coherent
5. pci_set_master before (also done in the passing step=5/corbverb tests, so unlikely)

Bisection plan (a FAIL costs a reboot, a PASS does not): `si_delay_us=` (1000 stock / 20000), `si_intsts=`
(default now the exact stock 0x400000ff), `si_wc=` (1/0).
- Run A: `stockinit=1 si_delay_us=20000` (everything stock except the delay). PASS expected.
- Run B: `stockinit=1` (exact stock incl. 0x400000ff). FAIL expected -> reboot. A pass + B fail = the delay
  after link reset is the root cause; fix = a CTHDA delay quirk in snd_hdac_bus_reset_link / hda_intel.
- If A also fails: reboot, bisect si_wc=0, then the byte-vs-dword GCTL write.

## 2026-09-03 17:16 — Run A FAILED: delay is not the cause (`probe-*-stockinit-1_si_delay_us-20000.log`)

stockinit with a 20 ms post-reset delay, exact stock INTSTS (0x400000ff) and WC ring wedged identically
(first CORBRP read 0xffff after 220,994 us, card + bridge dead). **Post-reset delay hypothesis falsified.**

The failing stockinit block and the passing fast=2 block now differ only in:
1. exit-reset write to GCTL: stock/stockinit = BYTE write (snd_hdac_chip_updateb) ; passing path = 32-bit write
2. INTSTS clear value: stockinit 0x400000ff (stock) ; passing path 0xc00000ff
3. ring memory: stockinit dma_alloc_wc ; passing path dma_alloc_coherent
(pci_set_master was already exercised in passing tests step=5/corbverb.)

Mechanistically, (1) is the best candidate for "the *bridge* wedges": a byte-enable write the CA0132's PCI
target never accepts would be retried forever by the ASM1083, jamming its buffers so later reads time out and
even its own config space stops answering. Note the AE-7 also ships as DEV_0010 and works with this exact
byte write on Linux, so if (1) is it, it is AE-9/rev-specific.

Bisect on top of the passing path (fast=2 after ae9_link_reset), one variable each; FAIL = reboot:
- F2: `fast=2 bytegctl=1`
- F1: `fast=2 f_intsts=0x400000ff`
- F3: `fast=2 f_wc=1`
- 17:20 F2 `fast=2 bytegctl=1`: PASS (`probe-*-fast-2_bytegctl-1.log`). Byte-wide GCTL exit-reset write is
  NOT the trigger. Remaining: F1 INTSTS value, F3 WC ring; also (4th) stockinit has no CORBRP/GCAP reads
  between the STATESTS read and int_clear, and allocates the ring (dma_alloc_wc) + pci_set_master *before*
  the reset rather than after.
- 17:21 F1 `fast=2 f_intsts=0x400000ff`: PASS. INTSTS value is NOT the trigger. Added stockinit knobs si_master, si_bytepoll, si_extrareads for failing-side bisection (a PASS there identifies the cause with no reboot).
- 17:22 F3 `fast=2 f_wc=1`: PASS. 17:23 `stockinit=1 si_bytepoll=0`: FAIL (byte polling not the cause).
- NEW (from a line-by-line diff of the two code paths, not memory): the passing fast block does ONE READ of
  GCTL between azx_int_clear's 13 writes and the 5 CORB writes; the failing stockinit block and the stock
  driver issue all 18 posted writes back-to-back and read only then (the CORBRP poll). Every other passing
  test read after every write. Hypothesis: >N consecutive posted writes with no intervening read, through the
  ASM1083 to the 66 MHz PCI target, jams the bridge's posted-write path -> later reads time out (0.2 s) and
  the bridge stops answering its own config space. Knob: `si_flushread=1`. A PASS confirms it (no reboot).

## Reboot-free recovery: `recover/ae9recover.ko` + `recover.sh` (2026-09-03 17:25)

Docker cannot help (same kernel, no hardware isolation); a VFIO VM would only protect the host from the stock
driver's lockup, which the capped probe already avoids. The real cost is the *hardware* wedge: card and ASM1083
answer 0xFF until reset. `ae9recover` removes the dead 09:00.0/0a:00.0 from the kernel, asserts a secondary
bus reset on the AMD switch downstream port 08:00.0 (PCIe hot reset of that one link; USB/NIC/SATA hang off
other ports), then `pci_rescan_bus()` re-enumerates them with fresh BAR assignment. It returns -EAGAIN from
init on purpose so it never stays loaded. Lockdown blocks setpci, so this has to be a signed module.
Usage after any wedge: `sudo ./recover/recover.sh` -> expect "RECOVERED"; then continue testing immediately.

## 2026-09-03 17:31 — ROOT CAUSE FOUND (`probe-20260903-173133-stockinit-1_si_flushread-1.log`)

`stockinit=1 si_flushread=1` PASSES: the stock `snd_hdac_bus_init_chip` block byte-for-byte (stock 1 ms
post-reset delay, stock INTSTS 0x400000ff, byte GCTL polling, pci_set_master, WC ring, IRQs off) completes in
30 us - CORBRP 0x8000 -> 0x0000, CORBCTL/RIRB/INTCTL/DPLBASE all programmed, bridge clean - with exactly ONE
change: a `readl(GCTL)` inserted between `azx_int_clear()`'s 13 posted writes and `init_cmd_io()`'s 5 posted
writes. Without it (18 consecutive posted writes, then the CORBRP read) the first read times out at ~220 ms
and the ASM1083 + card are dead until reset. Reproduced 5x, cured 1x, with every other variable held.

**Root cause: the Sound Blaster AE-9's on-card ASMedia ASM1083 PCIe-to-PCI bridge (or the CA0132's PCI target
behind it) wedges after a burst of >=N (14..18) consecutive posted MMIO writes with no intervening non-posted
transaction. The stock driver issues exactly such a burst once, at controller init, under spin_lock_irq,
then polls the dead register 1000x -> minutes of IRQs-off stall -> hard lockup, and the jammed bridge takes the
sibling xHCI (same switch/IOMMU group) down with it.**

Why other CTHDA cards survive: unknown - probably a different ASM1083 revision/strapping or the second codec
link on the AE-9 lengthening the CA0132's response to those writes. The AE-9 ships DEV_0010 rev 01.

Fix (kernel): flush posted writes with a read-back at the end of `azx_int_clear()` in sound/hda/core/controller.c
(one line, harmless everywhere), optionally also a read after the CORB base/size/WP writes in init_cmd_io().
See `patches/`.

### Tooling after the root cause (17:35)
- `recover/ae9recover.ko` v2: after the port hot reset revives the ASM1083, it also programs the bridge's bus
  numbers and pulses the bridge's *own* secondary bus reset (PCI RST# to the CA0132, 100 ms), waits 300 ms,
  peeks at the card's vendor ID through the bridge, and only then rescans. If the card does not answer it
  refuses to rescan (v1 scanned a dead bus: hundreds of 0.2 s timeouts, ~2 min of lag).
- `probe: stockinit=1 si_burstn=N`: N harmless back-to-back writes (CORBWP=0) then one timed read, to pin the
  exact threshold (known: 13+read+5 OK, 18 wedge).
- `patches/0001-ALSA-hda-flush-posted-writes-in-azx_int_clear.patch`: the fix (read-back of INTSTS at the end
  of azx_int_clear() in sound/hda/core/controller.c).

## 17:40 — fix built; codec-side burst risk identified

- `hda-core/snd-hda-core.ko`: v7.0 sound/hda/core + patch 0001, built out of tree. Exported-symbol CRCs
  identical to the stock module (103/103); imports identical bar a retpoline thunk. `install-core.sh` signs it
  with the MOK key and installs to /lib/modules/$(uname -r)/updates/ (depmod precedence); `revert-core.sh`.
- ca0132 out-of-tree build: `hda-ca0132/` with v7.0 ca0132.c + the sound/hda/common headers.
- RISK before any model=ae5 run: `ca0132_mmio_init_ae5()` writes the 37-entry ca0113 table to BAR2 with NO
  reads in between (`for (...) writel(data[i], mem_base + addr[i])`). Our preinit did those 37 writes with a
  readl after each (as Windows does) and passed; a raw 37-write burst is well past the 14..18 threshold seen on
  BAR0. The AE-9 quirk must read back after each BAR2 write (or after every few). Windows' driver follows every
  MMIO write with a read of the same register (MSVC volatile + fence) - which is exactly why Windows works.
  Test first: `stockinit=1 si_burstbar2=37` (and `si_burstn=16` for the BAR0 threshold).

## 17:50 — AE-9 codec quirk written and built (`hda-ca0132/`, `patches/0002-*.patch`)

QUIRK_AE9 = the AE-5 profile (INF-identical) plus the AE-9 specifics found today:
- PCI quirk `1102:0071 -> QUIRK_AE9`, model name "ae9", its own out_set_info entry (copy of AE-5), name
  "Sound Blaster AE-9"; every `case QUIRK_AE5:` and `== QUIRK_AE5` site gains an AE-9 twin.
- `ae9_detect_quirk()`: codec subsystem 0x11020072 (the "AE-9s" daughterboard) -> QUIRK_ZXR_DBPRO (minimal
  SPDIF/line-in secondary), mirroring `sbz_detect_quirk()` for the ZxR + DBPro.
- `ca0132_mmio_init_ae5()`: for AE-9, BAR2+0x1c keeps its power-up low half (mask/OR like Creative's driver)
  instead of the AE-5 constant that switches off the second codec.
- Posted-write hygiene: all 29 `writel()` to BAR2 go through `ca0113_mmio_writel()` = writel + readl of the
  same register (Creative's driver does this for every MMIO write). Removes the 37-write init burst.
Built out of tree from v7.0 sources; Ubuntu's copy differs by one small patch (3 extra imports) - acceptable
for testing. Scripts: `install-codec.sh` / `revert-codec.sh` (updates/ override), `probe/try-ae9.sh` (bind
with both fixes installed, no model= needed).

Order of operations for the operator: (1) `stockinit=1 si_burstbar2=37` and `si_burstn=16` to characterise
the threshold (recover.sh v2 after a wedge); (2) `hda-core/install-core.sh` + `hda-ca0132/install-codec.sh`;
(3) reboot; (4) `probe/try-ae9.sh`.
- 17:41 `si_burstbar2=37` (37 writes to ONE BAR2 register): PASS. Same-address bursts likely merge; not a fair count. Added `si_burstsd=N` (distinct SD_STS registers).
- 17:45 recover v2: port SBR revives the ASM1083 (id 0x10801b21) but the card stays 0xff after the bridge's SBR (100 ms hold, 300 ms wait). v3: enable bridge COMMAND, SBR 100 ms then 1000 ms hold, poll card id up to 5 s each. If v3 fails, the wedge is inside the CA0132's PCI core and only a platform reset clears it -> reboot remains the recovery.
- 17:46 recover v3 (bridge COMMAND enabled, SBR 100 ms then 1000 ms, 5 s polling each): bridge revives every
  time, card NEVER answers again. => the wedge is inside the CA0132's PCI core; only a platform reset (reboot)
  clears it. `ae9recover` has no practical use for the card; kept for reference. **A wedge costs a reboot.**
- Bisection result (final): flush read at K=13 (after azx_int_clear) PASSES; at K=17 the read itself is already
  dead, so the poison is in writes 14-17 (CORBLBASE, CORBUBASE, CORBSIZE, CORBWP) following an unflushed
  int_clear. 16 distinct byte writes alone: fine. 37 same-address dword writes: fine. Not a plain count.
  Further narrowing (K=14..16) would cost one reboot each; deferred - the patch reads at K=13, which is proven.
- 17:50 Runtime-burst insurance added to patch 0001: `snd_hdac_stream_setup()` issues 6 consecutive mixed-width
  writes (SD_CTL, SD_CBL, SD_FORMAT, SD_LVI, SD_BDLPL, SD_BDLPU) and `snd_hdac_stream_cleanup()` 3; a read-back
  now follows each burst (stream.c). Not proven necessary; costs ~1 us per PCM prepare/cleanup. Core module
  rebuilt, exported CRCs still identical.
- 17:55 Review of patches 0001/0002 + ae9recover: 0001 approved (INTSTS read is side-effect free; added a
  defence-in-depth GCTL read at the end of init_cmd_io). 0002 CRITICAL: my regex only converted writel; two
  AE-9 init-path functions (ae5_register_set: 12 writeb; ae5_post_dsp_register_set: 12 writeb) still burst.
  Fixed: ca0113_mmio_writeb/writew helpers, all 17 byte + 2 word BAR2 writes converted; only the 3 helper
  bodies write raw now. Cosmetic: AE-9 has its own "applied" debug line. ae9recover MEDIUMs (lock span, struct
  pci_bus shallow copy) noted, not fixed - the tool cannot revive the card anyway.
- Suspend (deep S3) test ran with the devices *removed* by ae9recover, so "absent" after resume is not a
  verdict; needs `echo 1 > /sys/bus/pci/devices/0000:08:00.0/rescan`.

## 2026-09-03 18:28 — THE CARD IS UP UNDER THE STOCK DRIVER STACK (`try-ae9-20260903-182836.log`)

With patch 0001 (core read-backs) + patch 0002 (QUIRK_AE9) installed as module overrides, `modprobe
snd_hda_intel enable=1,1,1,1` bound the AE-9 with no wedge, no lockup:
- `CA0132: picked fixup for PCI SSID 1102:0071` -> codec D1 named "Sound Blaster AE-9", autoconfig
  line_outs 0x0b/0x0f/0x10, hp 0x11, mic 0x12, line 0x13, dig-out 0xc/0xd, dig-in 0xe.
- `ca0132 DSP downloaded and running` (ctefx.bin fallback; ctefx-desktop.bin absent).
- codec D2 (0x11020072) took the DBPro path: dig-out 0xc, Line 0x11, dig-in 0xe, no analog outs.
- card 1 "HDA Creative": device 0 CA0132 Analog, device 1 + 10 CA0132 Digital. Full mixer: Master/Front/
  Surround/Center/LFE, Output Select (Speakers/Headphone), AE-5: Headphone Gain, AE-5: Sound Filter, EQ,
  FX: Crystalizer/Surround/X-Bass/..., What U Hear, Mic Boost.
- Main codec GPIOs all disabled (io=3); pin 0x0b/0x0f/0x10 OUT, 0x12 IN VREF80.

Open items: (1) audible playback test on the card's line out, then Output Select=Headphone for the ACM;
(2) ACM power/enable (likely a GPIO or ca0113 write the AE-5 profile does not do); (3) enable the card at
boot (ae9-nobind.conf -> enable=1,1,1,1) after one controlled reboot; (4) install ctefx-desktop.bin;
(5) upstream: patches/0001 + 0002, with the wedge analysis as the cover letter.

## 18:31 — Audio Control Module (ACM) has no power under Linux

CtxHda.sys (the Windows codec driver) carries a full ACM subsystem (UTF-16 strings): Acm12VLowChangeMaxCounts,
Acm48V, AcmDetectPollIntervalMs, AcmDisplay, AcmEncoderLED/LongPress/MsPerSteps, AcmMaxDetectFailCount,
AcmPollIntervalMs, portacm, Acm1. So the ACM is a detected, polled peripheral with its own 12 V rail, encoder,
LED, display and 48 V phantom - almost certainly driven over the 16550 UART at BAR2+0xc00 (31250 baud) that the
AE-5 init table already configures. The AE-5 profile never powers/talks to it. Static RE of CtxHda.sys for the
power-on + detect + UART protocol is running -> `reference/ctxhda-acm-analysis.md`.
Empirical tool: `acm/poke.sh` (module maps BAR2 via plain ioremap while snd_hda_intel owns the card):
`dump=1` (read-only), `pin=N enable=0|1` (ca0113 GPIO cmd at 0x320), `uart=0xNN`. Unnamed GPIO pins 3 and 6 are
the first candidates for an ACM power enable (named: 0 antipop, 1 DAC 192k sel, 2 FP detect, 4 HP amp shdn,
5 DAC reset, 7 front mute).
- 18:36 poke results: BAR2+0x300 mirrors ca0113 GPIO output state (baseline 0x33 = pins 0,1,4,5 high; pin 3 on
  -> 0x3b; pin 6 on -> 0x7b). Pins 3 and 6, and UART MCR 0x03/0x0f (DTR/RTS/OUT1/OUT2) all had NO visible effect
  on the ACM; MSR stayed 0x00 (no modem-status lines from the link), LSR 0x40 (TX empty, no RX). The ACM is not
  a plain GPIO/power-line switch-on; it most likely needs the UART handshake/protocol from CtxHda.sys (RE in
  progress). State left: pins 3,6 on, MCR=0x0f (revert: pin=3 enable=0; pin=6 enable=0; mcr=0).

## 18:57 — ACM protocol extracted from CtxHda.sys (`reference/ctxhda-acm-analysis.md`)

- Power: **ca0113 GPIO 5 = 1** (`B2[0x320] = 0x0105`). Board D0 also: `B2[0x100] &= ~bit(pin-4)` for pins
  4-7, `B2[0x304] = 0xff`, anti-pop pulse on pin 0 (0x0000, 1 ms, 0x0100), pin 2 = 0, pin 3 = 1, later pin 4 =
  HwInternalLed. Linux baseline already has pin 5 high (0x300 = 0x33) - power alone is not enough.
- Link: the BAR2+0xc00 16550 (4-byte stride; Creative status reg at 0xc7c: bit0 tx busy, bit1 tx ready, bit3
  rx avail), re-tuned to **115200** (div 13 of 24 MHz; the AE-5 table leaves it at 31250 = MIDI).
- Framing `F0 <cmd> <len> <payload> F7`; cmd|0x80 = query; 5-byte ack per command.
- Detect every 500 ms: TX `F0 81 00 F7`, RX 9: byte3 = present, bytes 4-7 = LE32 firmware. Power-cycles GPIO 5
  after repeated misses.
- First contact: unlock `F0 54 04 'Acm1' F7` + `F0 54 04 '1mcA' F7`; `F0 03 03 05 03 03 F7`; timing params
  (0x32), encoder filter (0x43), **48 V phantom `F0 05 03 02 01 <0|1> F7`**, `F0 22 02 01 x`, **LED `F0 22 02 02
  <0|1> F7`**, **display `F0 11 09 'AE-9' 00.. F7`** (also "-SP-", "-HP-", "-10.5", 8 spaces), blink (0x21).
- Steady state: encoder `F0 C1/C2 00 F7`, buttons `F0 B1 01 n F7`, status `F0 83 01 <reg> F7`, 48 V readback
  `F0 85 01 02 F7`. Shutdown `F0 52 02 CC DD F7`, then GPIOs 4,5,3,0 low.
- Caveat: INF `*Pin` names are CA0132 codec GPIOs (verbs 0x715-0x717), not ca0113 pins.
Tool: `acm/poke.sh acm=1` (power + GPIO seq + 115200 + probe), `acm=2` (+ unlock, LED on, display "AE-9").

## 2026-09-03 19:00 — ACM ALIVE: display shows "AE-9", LED on

`acm/poke.sh acm=1`: after GPIO 5 on + Windows GPIO sequence + UART 115200, probe `F0 81 00 F7` answered
`F0 81 05 01 C8 89 95 01 F7` -> present=1, firmware 0x019589c8. `acm=2`: unlock 'Acm1'/'1mcA', reg5, LED on,
display "AE-9" -> **the module lit up and shows AE-9.** Whole path from PCI to the external module now proven
on Linux: controller fix (patch 0001) + QUIRK_AE9 (patch 0002) + ACM protocol (this).

Remaining engineering:
1. Audible test through the ACM (Output Select = Headphone) and the card's line outs.
2. Move the ACM handling into the QUIRK_AE9 code: power/GPIO seq + baud at init, 500 ms poll thread (probe,
   re-power after misses), first-contact init burst, encoder/button events -> volume/mute, 48 V phantom and LED
   as mixer controls, "-SP-"/"-HP-" display on output select, shutdown `F0 52 02 CC DD F7` on remove.
3. Enable the card at boot (ae9-nobind.conf enable=1,1,1,1) with one controlled reboot.
4. ctefx-desktop.bin; upstream submission (0001 core fix, 0002 quirk, 0003 ACM).
- 19:05 Headphone silence explained: (a) mixer 'HP/Speaker Auto Detect' is ON, so `ca0132_alt_select_out`
  ignores Output Select and stays SPEAKER_OUT while no jack is detected ('Headphone Jack' = off even with
  headphones in the ACM: the ACM jack detect does not reach codec pins 0x10/0x11); (b) even in HEADPHONE_OUT,
  with no jack detected the driver enables the *rear* HP pin 0x11 (out_pins[1]); the ACM headphone path is
  almost certainly the *front* HP pin 0x10 (AE-5 "Port D / FP Hp"); (c) the ACM itself has HP/SP selection
  bits (reg2&0x04 = HP, reg7&0x01 = SP) that Windows polls and mirrors, and its button toggles them.
  Tools: `acm/poke.sh acmget=N | acmset=reg,val,mask | acmtext=STR`; `acm/hdaverb.py` (hwdep verbs as the
  audio-group user). Volumes are fine (Master 80%, Front 0 dB, on).

## 19:42 — converter bring-up from CtxHda.sys (`ctxhda-acm-analysis.md` §10) implemented as `ae9_setup_defaults()`

Both AE-5 and AE-7 profiles leave every analog output silent (rear line-out too). Windows' AE-9 class does,
after DSP load: D0.2 (B2[0x100]&=~0x0f, 0x304=0xff, GPIO5 on, anti-pop pulse pin 0, DAC 0x48 reg7|=1 mute,
dev 0x49 reg 0x0a=7, pin2=0, DAC 0x1d=0x40, pin3=1); D0.3 AE-7-style ASI/PLL with AE-9 PLL bytes
(0x44:c8 0x43:cc 0x45:cb 0x40:c7 0x42:cd 0x41:ce 0x51:db), verbs 0x724<-0x83 / 0x725<-0x81 on NID 0x15,
0x794<-0 on 0x17, exram 0xfa92<-0x22, GPIO1=0, streams 0x0c/0x18 6ch@96k, chipio 0x189000..8<-0x1f101,
0x189024<-0x14004, 0x189028<-0x2000f, ASI=0xf; D0.4 ES9038 (I2C 0x48 via the ca0113 command engine)
register set + device 0x49 set; D0.6/7 GPIO4 LED, unmute (0x07=0x80, 0x49 0x0a=6). Neither codec GPIO
verbs nor pin-widget/EAPD changes are involved; headphone mode toward the ACM is `F0 03 03 02 40 40 F7`.
Caveat: three DAC registers are read-modify-write on Windows; v1 assumes ES9038Q2M defaults.
Implemented as ae5_setup_defaults() followed by the AE-9 sequence; dispatch `case QUIRK_AE9`.

## 2026-09-03 19:56 — Two external repos + read primitive; the real blocker identified

Operator linked https://github.com/s3boun3t/ae9_build and https://github.com/klimovich008/ae5-linux-control.

**s3boun3t/ae9_build is an independent AE-9 driver at the SAME blocker as us.** It confirms every hardware
fact we derived and adds the decisive one:
- HP DAC = ES9038Q2M @ cmd-engine group 0x48; surround DAC = SABRE9006AS (8-ch) @ group 0x49 (our "device 0x49").
- ACM has NO DAC: analog audio + I2C + 5V + HPD over the cable; XAMP amp, relay, OLED, knob only.
- Firmware **ctefx-desktop.bin** (655,856 B) = identical to the blob in CtxHda.sys; we run the ctefx.bin fallback.
- **BLOCKER (theirs, verbatim): "DSP DMA ch1/ch2 frozen at 0x00010001 ACTIVE=0 — firmware does not connect the
  internal output pipeline. Audio silence on ALL outputs." DSP takes input (stream 0x05/ch0 advances, DBGCTL
  changes) but emits nothing. "100% dans l'init boot."** cfg0/1/2 immutable after fw download.
- Windows does **~18,492 I2C writes** at init; the Linux driver ~30. The routing that connects DSP output
  stream 0x0c/0x18 to the DACs lives in that gap and is NOT publicly decoded by them either.
- Their install note: cold `poweroff`, never reboot (matches our wedge).
=> We are at parity with the only other known AE-9 effort, stuck at the identical unsolved step: the DSP's
   internal output pipeline never activates, so no register/DAC work downstream can produce sound.

**Command-engine READ primitive (subagent §11, for verifying DAC writes / doing RMW properly):**
lock (0x210=7e,5a,read 0xaa); `0x804=(&~0x3ff)|addr`; `0x20c=(&~6)|(1<<1)` (len code, subaddr-only);
`0x204=reg`; poll 0x20c bit23 (<=10x100us); `0x20c` len<<1; **`0x208=0xffff` strobe**; poll; **data=B2[0x208] low
byte**; unlock (0x20c&=~1; 0x210=0). HdIs rate: 96000 -> `B2[0x100]=(&0x1fffffff)|(2<<29)` = |0x40000000;
width 0x20 is rejected (writes nothing). Stream waits read 8051 XRAM `0x734+10*id`.

**"out of range cmd" errors (my 19:50 reload):** garbage nids (0xd740, 0x8a02, 0xffff) with pin verbs
f07/707/70c - a table walked with a bad index or DAC data fed as codec verbs by my ae9_setup_defaults; a
real bug in my added sequence, secondary to the DSP-output blocker above.

## 2026-09-03 20:05 — Capture plan for the Windows DAC init (the ~18k writes)

Feasibility on this box: root is on 11:00.0 (IOMMU group 19) so it survives; the card's group 17 also holds
NIC 0b (r8169), Wi-Fi 0c (mt7921), USB 0e (xHCI), SATA 0f. Operator has a keyboard on CPU USB (12:00.4,
outside grp 17), so a full-group VFIO passthrough is survivable. OVMF installed; win10.qcow2 present (23 GB).
No ACS-override sysfs; try `pcie_acs_override=downstream,multifunction` on cmdline to split the group (Ubuntu
HWE may honor it) so only 0a:00.0 passes.

Method (= what s3boun3t used): pass the card to a Windows guest with `vfio-pci,host=0000:0a:00.0,x-no-mmap=on`
so every BAR access is trapped (also sidesteps the posted-write wedge), and `-trace enable=vfio_region_write,
file=...`. Install/repair the Creative AE-9 driver in the guest -> it emits the full DAC/DSP init. Decode BAR2
0x804=group, 0x204=(val<<8)|reg into the ES9038 (0x48) / SABRE (0x49) I2C stream. Decoder:
`capture/decode.py <trace>` -> ready-to-paste ca0113_mmio_command_set() lines + GPIO. Trace lands on the Linux
host so it can be read here directly. Also copy ctefx-desktop.bin out of the Windows drive while there.
Alternative (no VFIO): native-Windows WinDbg breakpoint on CtxHdb write primitive 0x146c8 logging offset/value.

## 2026-09-03 20:35 — Capture tooling ready (plan recovered after a context loss)

Verified on this box: card alive after the 20:15 reboot and unbound (config `02 11 10 00`); IOMMU group 17
endpoints = card 0a, RTL8125 NIC 0b, MT7921 Wi-Fi 0c, chipset xHCI 0e (usb1/usb2 = mouse + GO blu), chipset
SATA 0f (no disks); keyboard on CPU USB 12:00.x, both system disks on 11:00.0, Windows NVMe on 02:00.0 (group
13) — all survive a whole-group passthrough. Kernel has no ACS override, so the whole group moves.
Windows 11 on nvme0n1p5 is NOT hibernated (hiberfil header zero), not BitLocker, driver 6.0.105.0065 +
Sound Blaster Command installed. OVMF here is the 4M build (`OVMF_CODE_4M.fd`/`OVMF_VARS_4M.fd`, not
`OVMF_CODE.fd`). QEMU is a local build 10.0.50 in /usr/local/bin (source ~/Downloads/qemu), trace backend
"log" (so `-trace file=` also receives `qemu_log()` output), `vfio-pci` with `x-no-mmap`.

**Gap in the original plan:** codec verbs (chipio/dspio/SCP = the DSP configuration) travel through the CORB
ring in guest RAM, invisible to `vfio_region_write`. Fixed with a 139-line local QEMU patch
(`capture/qemu-hda-corb-trace.patch`, applied + built in ~/Downloads/qemu/build): with `VFIO_HDA_TRACE=<dev>`
it tracks CORB/RIRB base + pointers on region 0 and mirrors every verb (`hda_corb ...`) and response
(`hda_rirb ...`) into the same trace file, in order with the BAR accesses.

Tooling in `~/sb-ae9-linux/capture/`:
- `bind-group.sh` (sudo): preflight (card alive, no disk behind 0f, group layout unchanged) then moves the
  five endpoints to vfio-pci. `restore-group.sh` (sudo): gives NIC/Wi-Fi/USB/SATA back, leaves the card unbound.
- `run-capture.sh [native|qcow2]` (sudo): `native` (default) boots the real Windows 11 disk through a
  throwaway qcow2 overlay (`-b /dev/nvme0n1 -F raw`) so the real disk is never written and the already
  installed Creative driver initialises the card at boot; `qcow2` boots ~/Downloads/win10.qcow2 with
  `ctxhda-driver.iso` (full DriverStore package incl. CtxHda64.cat) attached. Card passed with
  `x-no-mmap=on`, other four plain, `-trace enable=vfio_region_*`, `-msg timestamp=on`, GTK window.
  Arguments dry-run OK (no VFIO, timeout-killed).
- `decode.py <trace>` v2: filters by device, splits phases at >1 s gaps, emits `<trace>.i2c.c`
  (ca0113_mmio_command_set lines by phase), `<trace>.verbs.txt` (verb + paired response), `<trace>.bar2.txt`.
  Self-tested on synthetic lines.

**Firmware finding (static, no VM needed):** CtxHda.sys 6.0.105.0065 embeds ONE full DSP image at 0x3a4b0,
655,436 B, same size and segment layout as linux-firmware's ctefx.bin but DIFFERENT content (seg1 main program:
187,457 bytes differ; seg2: 12,058; seg3: 54). Saved as `reference/ctefx-from-ctxhda-6.0.105.0065.bin`
(sha256 90ba1de2...). So Linux has been running a different DSP program than Windows. It also embeds ~10 small
overlay images (chip addr 0xb7b10/0x3f3a0/0x0/0x40000, 0.4–60 KB; ctspeq.bin is the 0x40000-type) which
Windows may download on demand — the capture will show whether any is loaded at init.

## 2026-09-03 21:05 — WINDOWS CAPTURE SUCCEEDED (capture/ae9-20260903-210512.trace, 57 MB)

Guest = native Windows 11 via overlay; had to boot bootmgfw from a GPT-partitioned helper image
(`capture/win-esp-gpt.img`; bootmgfw exits silently from an unpartitioned volume; the disk's own fallback
loader is Ubuntu's shim/GRUB). 53,429 BAR writes (35,061 BAR0 / 18,368 BAR2), 636,643 reads, 9,157 verbs
(CORB mirror), 0 RIRB responses (hooks were on the wrong register: RIRBSTS is 0x5d, RIRBWP read as a byte —
fixed in the patch + rebuilt for the next capture). Decoders: `capture/decode.py` (BAR2 I2C/GPIO, verbs) and
`capture/ops.py` (high-level ops: CHIPIO/8051/PLL/FLAG/PARAM/SCP/streams/I2C/GPIO/UART) ->
`<trace>.ops.txt` (17,075 ops), `.i2c.c`, `.verbs.txt`, `.bar2.txt`.

Headline numbers: the "18k I2C writes" of s3boun3t are 18k BAR2 writes, of which 16,110 are UART bytes to
the ACM (0xc00) and only 231 are DAC I2C (163 -> ES9038Q2M 0x48, 68 -> SABRE9006AS 0x49); 210 GPIO cmds.
Windows sends one verb at a time (CORBWP write + RIRBSTS poll per verb). Phases: 3 = driver start
(enumeration, codec2 pin/digital setup, 8051 writes, CT extensions, DSP_INIT), 4 = firmware download (DSPDMAC
0x110fxx + SD4 stream loop, standard) then SCP 0x80 startup, flags 96 kHz, conn-point rates, ES9038 init,
ACM handshake; 9 = playback tests. codec 2 (cad2, sub 0x0072) is actively used: SET_STREAM/format on nids
0x05 (dig out), 0x08 (ADC), 0x09 (dig in) + SET_DIGI_CVT1 toggles at every stream start/stop.
Linux-side counterpart: `capture/trace-linux.sh` (hda tracepoints) -> `ops.py --linux`.

## 2026-09-03 22:24 — Linux trace attempt wedged: card must be REBOOTED after a Windows capture

Ran restore-group.sh + installed ctefx-desktop.bin (from the .sys) + trace-linux.sh. The bring-up WEDGED:
`0000:0a:00.0: CORB reset timeout#2, CORBRP = 65535 / no codecs initialized`. Confirmed the patched core WAS
active (loaded snd_hda_core srcversion 702135E30E037061F288CDF == hda-core build, loaded from updates/ at the
20:15 boot; modules.dep prefers updates/snd-hda-core.ko). So the flush fix was in effect and the card wedged
anyway. Difference vs the 18:28 success: the card came straight from the Windows/VFIO guest (VFIO disables the
device on release -> command reg 0), never platform-reset. The flush fix is validated only from a COLD boot.
=> Workflow rule: a Linux bring-up must start from a reboot; never bring the AE-9 up under Linux in the same
boot as a VFIO/Windows capture. trace-linux.sh now detects "no Creative card" and tells the operator to reboot
instead of playing a tone into a dead card, and auto-detects the Creative card index (not hardcoded 1).
ctefx-desktop.bin (655,436 B, from CtxHda.sys) is now installed at /lib/firmware/ so the next boot's DSP load
uses the Windows program.

## 2026-09-03 22:35 — Capture-based AE-9 driver fix implemented (hda-ca0132/ca0132.c)

From reference/windows-vs-linux-init.md. Changes to the QUIRK_AE9 path (built clean; new codec
srcversion 6E30DB4C16EB4CCD4382739; ca0132.c.pre-capture-fix is the backup):
1. ca0132_alt_init: split QUIRK_AE9 from QUIRK_AE5. AE-9 now writes chipio 0x18b008 = 0xf8 then
   0xf0 and read-modifies 0x18b030 to (v & 7)|0x20 AFTER the verb sequences (Windows order), and
   no longer issues the ca0113 group-0x30 command (that device is absent on the AE-9).
2. NEW ae9_dsp_scp_startup(): the DSP master-control "go" sequence Windows runs after
   dsp_set_run_state (module 0x80 src 0, req 0x0c = 3/4/5/0, req 0x0a=0, 0x0b=1, then
   chipio 0x100e34=0xf1000000). This was entirely absent from the 7.0 driver and is the most
   likely reason the DSP output pipeline never activated (the blocker s3boun3t also hit). Overlay
   to XRAM 0x3f3a0 not reproduced.
3. NEW ae9_dsp_defaults(): W2 module defaults (conn rates, 0x80/0x00,0x01=3.0, ES9038 THD/trim,
   0x80/0x0d,0e / 0x96/0x3c / 0x31 / 0x32) WITHOUT starting any stream, freeing DMA channels, or
   touching group 0x30 — i.e. replaces the ae5_setup_defaults call that started streams before
   the routing existed (report cause #1).
4. ae9_setup_defaults rewritten to the Windows order: scp_startup, dsp_defaults, d0_prepare,
   asi_pll_setup (streams start here, after routing), dac_init, finish, effects+speaker-tuning
   defaults, 0x8f/0x01=0. No longer calls ae5_setup_defaults.
5. ae9_post_dsp_asi_pll_setup: added ES9038 0x01=0xc0 / 0x0a=0x02 (I2S input format), dropped the
   second 0x725 Windows does not send there.
Deferred: the per-playback chipio 0x18b008=0xf0 write and ES9038 re-apply in
ca0132_playback_pcm_prepare (report finding, refinement); the 0x3f3a0 overlay. Test: reboot,
then trace-linux.sh (go.sh signs+installs the new codec; ctefx-desktop.bin now present).
