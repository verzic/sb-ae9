# Sound Blaster AE-9 — Direct Mode plan

**Status:** living document · 2026-11-03 · M0/M1/M2 done, M3 gated
**Baseline:** `work/ae9-output-switch` / `work/ae9-direct` @ `dc9b50f` (PR #4), GUI `ae9-fx-gui` @ `abbcf73` (PR #1)
**Purpose:** decide *what* is feasible, *in which order* to build it and *what it costs* — before (more) code.

---

## 0. TL;DR

- **Direct Mode at 44.1 / 48 / 88.2 / 96 / 192 kHz is implemented and verified** on the physical card (PR #4, `ae9_direct=1`). The DSP/I2S chain now follows the stream rate instead of staying pinned at 96 kHz. Measured pitch is exact at every rate; BAR2 `0x100` carries the expected rate code.
- **176.4 kHz does not work** with either candidate rate code (4 or 5): the stream runs but the output is silent. It is deliberately not exposed.
- **384 kHz is still unproven** and is gated behind one cheap experiment (M0b). The codec does not advertise 384 kHz (`AC_PAR_PCM` has no bit 11), the HDA link's 8× rate is not in the specification, and no Windows capture of Direct Mode at 384 kHz exists. If the gate fails, we stop at 192 kHz.
- **No DSP bypass beyond the DSP**: the audio still passes through the DSP; "Direct Mode" here means *the DSP and the I2S chain run at the stream's native rate*, not that the DSP is removed from the path.
- The stable default is unchanged: `ae9_direct=0`, 44.1–96 kHz, 96 kHz DSP path. Direct Mode is opt-in.

---

## 1. Definition of success

Three independent levels:

| Level | Meaning | Status |
|---|---|---|
| **L1 — Direct ≤192k** | Audio reaches the ES9038 at the stream's native rate (44.1 / 48 / 88.2 / 96 / 192), no wrong SRC, no DSP effects. Stereo. | **Done** for 44.1/48/88.2/96/192; 176.4 excluded |
| **L2 — 384k stereo** | Real 32-bit / 384 kHz, like Windows. | **Unproven** — gated by M0b |
| **L3 — DSD64** | DoP or native DSD through the ES9038. | Optional, out of scope |

**Definition of done (L1):** `aplay -D hw:Creative,0 -f S32_LE -r <rate> -c 2` plays a correct tone at each rate, without xruns, the chain is re-clocked in `prepare`, and `dmesg` logs the rate that was requested (not 96k).

**Definition of done (L2):** the same at 384k, with a 10-minute soak, no xruns and no pitch drift.

---

## 2. What Direct Mode is (and is not)

On Windows, "Direct Mode" is two things together:

1. **Bypass of the DSP effects** (SBX, EQ, CrystalVoice, Scout) — a clean path.
2. **Rate following and high rates** up to 384k stereo (DSP mode stops at 96k; the official specs say DSP mode max 96k, Direct Mode max 384k stereo).

On Linux today:

- The "clean path" already exists when the effects are off (default), **but** the DSP stays in the path and used to run at a fixed internal rate of **96 kHz**.
- PCM0 (analog) exposed **44.1–96k** only; PCM1 (S/PDIF) reached 192k.
- The `0x189000/0x189024` I2S/HCI descriptors were **pinned** to the 96k values (`0x1f101 / 0x14004`).
- So every 44.1k stream was resampled somewhere towards 96k. That is what M1 changes.

What we have **not** proven: that the samples are bit-identical end to end. We proved that the rate chain follows and the pitch is exact; proving bit-exactness would need a digital capture of the DSP output, which the card does not expose.

---

## 3. Evidence

| Fact | Source |
|---|---|
| The DSP is in the path: HDA stream → DSP (stream `0x05`) → I2S (stream `0x18`) → ES9038 | `docs/reverse-engineering/ctxhda-acm-analysis.md` §10.5, s3boun3t wiki "DSP & ChipIO" |
| I2S/PLL is configured per rate through `0x189000/4/8`, `0x189024`, the PLL bytes and a BAR2 rate code | `ctxhda` §10.5, §11.2 |
| Per-rate values exist for 44.1/48, 88.2/96, 176.4/192, 384, 768 | `ctxhda` §10.5 table |
| The BAR2 `0x100` bits 31:29 rate code is 48k=0, 44.1k=1, 96k=2, 88.2k=3, 192k=4 | `ctxhda` §11.2 (`HdIs+0x40`, `0x150b8`) |
| Windows' per-rate sequence is ASI 8 → PLL trim → descriptors → DAC mode → rate code → full PLL → ASI 0xf | `ctxhda` §11.4 corrected step 8 |
| The codec does not advertise 384k (`AC_PAR_PCM` bit 11) | M0a probe, below |
| The codec accepts and echoes `SET_STREAM_FORMAT 0x3841` | M0a probe — **not proof** that the link carries it |
| The HDA hwdep requires `CAP_SYS_RAWIO` (`hda_hwdep_open()` returns `-EACCES`) | kernel `sound/pci/hda/hda_hwdep.c`; confirmed empirically |
| The ACM UART and the DSP share BAR2 — hence the mutex and the "no hammering" rule | `ca0132.c` comments, `docs/INVESTIGATION.md` |

### 3.1 M0a codec probe (2026-11-03, kernel 7.2.14)

Raw data: `/tmp/opencode/m0-codec.txt` (regenerate with the `hda-mini` helper + `m0-codec-probe.sh`).

| NID | `AC_PAR_PCM` | Declared rates | Declared bits |
|---|---|---|---|
| 0x02 (analog out) | `0x000a01e0` | 44.1 / 48 / 88.2 / 96k | 16 / 24 |
| 0x03 (second out) | `0x001f05ec` | 16k / 22.05 / 44.1 / 48 / 88.2 / 96 / 192k | 8 / 16 / 20 / 24 / 32 |
| 0x05 (S/PDIF) | `0x001e05e0` | 44.1 / 48 / 88.2 / 96 / 192k | 16 / 20 / 24 / 32 |

- **No 384k bit (bit 11) anywhere.**
- `SET_STREAM_FORMAT 0x3841` → `GET_STREAM_FORMAT` returns `0x3841` on all three NIDs, but the verb is not validated: the codec stores what it is given. It proves the verb path works, not that the hardware runs 384k.
- `AC_PAR_STREAM` = `0x1` (PCM) on 0x02/0x03, `0x5` on 0x05.

### 3.2 BAR2 rate register

BAR2 is only 16 KB (base `0xf7200000`), so the `0x189xxx` "registers" are **DSP chipio addresses**, not BAR2. The register Windows writes for the rate is BAR2 `0x100` bits 31:29. Measured with a small read-only `ioremap` module (`tools/acm-tool/ae9gpio.c`, temporary copy with the bus fixed to `29`):

| State | BAR2 `0x100` | Bits 31:29 |
|---|---|---|
| idle | `0x00000700` | 0 |
| 44.1k playback (Direct) | `0x20000700` | 1 |
| 48k playback (Direct) | `0x00000700` | 0 |
| 88.2k playback (Direct) | `0x60000700` | 3 |
| 96k playback (Direct) | `0x40000700` | 2 |
| 192k playback (Direct) | `0x80000700` | 4 |

Before M1 the register stayed at `0x00000700` even during 96k and 192k playback, because the driver never wrote it.

### 3.3 Per-rate I2S values

| Rate | `0x189000/4/8` | `0x189024` | `0x189028` | BAR2 code | GPIO1 |
|---|---|---|---|---|---|
| 44.1k | `0x0001f100` | `0x00008005` | `0x0002000f` | 1 | 0 |
| 48k | `0x0001f100` | `0x00008005` | `0x0002000f` | 0 | 0 |
| 88.2k | `0x0001f101` | `0x00014004` | `0x0002000f` | 3 | 0 |
| 96k | `0x0001f101` | `0x00014004` | `0x0002000f` | 2 | 0 |
| 176.4k | `0x0001f102` | `0x00022003` | `0x0002000f` | ? | 0 |
| 192k | `0x0001f102` | `0x00022003` | `0x0002000f` | 4 | 0 |
| 384k | `0x0001f103` | `0x00031002` | `0x0002000f` | ? | 1 |
| 768k | `0x0001f103` | `0x00231002` | `0x0002000f` | ? | 1 |

PLL (common to all rates, as at boot): `0x44=c8, 0x43=cc, 0x45=cb, 0x40=c7, 0x42=cd, 0x41=ce, 0x51=db`.
384k adds (per the Windows decode): ES9038 `0x01=0xc1` (mode 2) and `0x0b = (read(0x0b) & 0x0f) | 0x20`.

---

## 4. Open questions

### U1 — Can the transport carry 384k? (blocks L2)

An HDA link sends samples in 48k frames; the standard reaches 4× (192k). 384k needs an **8× multiplier**, which the spec does not define. We do not know whether this controller/codec pair accepts it.

**Experiment (M0b):** write `SET_STREAM_FORMAT 0x3841` to the stream NID and then actually play a 384k stream. The codec echo is not enough (see §3.1); the real signal is whether the controller programs SDxFMT, the stream starts, LPIB advances, and the DSP produces audio at the right pitch.

**What it needs:**
- a 384k entry in `driver/hda-core/device.c` `rate_bits[]` (the out-of-tree `snd-hda-core` **is** the installed module, so this is feasible),
- `AC_PAR_PCM_RATE_BITS` extended (11 → 12) only if the codec really reports bit 11 — it does not,
- a rate mask on the ca0132 PCM including 384k,
- a `ae9_i2s_rate_set()` entry with the 384k descriptors and rate code.

**If it fails:** stop at 192k. The only fallback is a dedicated DMA/I2S path (architecture C), which is not worth the risk.

### U2 — What exactly is Windows' "Direct Mode ON" sequence?

We now know and use the **rate block** (step 8 of I2SM Start). We still do not have a Windows capture that shows:
- whether Direct Mode changes `0x80/0x00` (`FLOAT_ONE` vs `FLOAT_THREE`),
- whether it clears the SRC/DSP flags (`CONTROL_FLAG_SRC_RATE_96KHZ`, `DSP_96KHZ`, `DAC_96KHZ`),
- whether it changes the ASI transition,
- what it does for 176.4k and 384k specifically.

The current Linux driver uses `FLOAT_THREE` and ASI 0xf after the per-rate write; audio works, so this combination is at least functional for 44.1–192k.

### U3 — Does the DSP pass a native rate through?

**Answered: yes, empirically.** With the I2S clocked at 44.1k and a 44.1k stream, the analog output measures 1000.00 Hz, not the 2177 Hz that a 96k DSP feed would produce. The DSP follows the rate. (Whether it still resamples internally at the same rate is not observable.)

### U4 — Side effects on ACM / output switching

The output path (`ae9_apply_output_path`, `ca0132.c:9555`) writes ES9038 `0x1d` (front mute). The rate change additionally writes `0x01`/`0x0a`. The output switching was re-checked after M1: still exclusive (headphone unmuted, line-out muted). Watch for pops at stream start/stop; the driver does not mute `0x07` around the re-clock yet.

---

## 5. Architectures

### A. Rate following through the DSP (implemented for L1)

Keep the current HDA→DSP→I2S path, but in `ca0132_playback_pcm_prepare()`:
- read `runtime->rate`,
- write the matching I2S/PLL/rate-code set,
- restore 96 kHz in `cleanup`.

**Pros:** small risk, works with the existing stable path, covers 44.1–192k.
**Cons:** no 384k (the HDA PCM does not expose it), 176.4k unsupported.

### B. HDA 8× rate (required for L2)

Patch the out-of-tree `hda-core`:
- `rate_bits[]`: add `{384000, SNDRV_PCM_RATE_KNOT, HDA_RATE(48, 8, 1)}`,
- `snd_hdac_is_supported_format()` for 8×,
- a rate mask on the ca0132 PCM.

**Pros:** if the hardware accepts it, this is the clean solution.
**Cons:** unknown whether the link/controller accepts it; touches `snd-hda-core` for the whole card.

### C. Dedicated DMA / direct I2S (L2 fallback)

The DSP has its own DMA channels (`0x110f00+`, `CHNLSTART`, `ACTIVE`) and the ca0113 has an "HDA stream sniffer". In theory the controller could write I2S directly. **Not recommended:** unknown protocol, high risk of a hard lock, and low value.

**Decision:** A (done) → B (gated) → C only if a clear Windows capture shows a direct DMA path.

---

## 6. Phases

### M0 — Feasibility and instrumentation — **done**

Delivered:
- `hda-verb`-equivalent helper (`/tmp/opencode/hda-mini.c`): builds the HDA command word exactly like `snd_hdac_make_cmd()`, including 16-bit payload verbs.
- Codec probe script/log: `AC_PAR_PCM`, `AC_PAR_STREAM`, `GET_STREAM_FORMAT`, and SET/GET round-trips for 192k and 384k on NIDs 0x02/0x03/0x05.
- BAR2 `0x100` reads during playback (temporary `ae9gpio` build with the PCI bus fixed to `29`).
- Loopback measurement rig: `tone.py` (S32_LE sine generator) and `measure.py` (interpolated zero-crossing frequency meter).

Result: no declared 384k; the SET/GET echo is not evidence; the rate code lives in BAR2 `0x100`. M0b (real 384k stream) remains open and is the gate for L2.

### M1 — Rate following — **done**

Implemented in `driver/ca0132/ca0132.c`:
1. `ae9_i2s_rate_set(codec, rate)` — the Windows rate block: ASI 8, PLL trim, connection rates `0x70`/`0xd0`, `0x189000/4/8`, `0x189024`, `0x189028`, ES9038 `0x01`/`0x0a`, BAR2 `0x100` code, full PLL, ASI 0xf.
2. Hook in `ca0132_playback_pcm_prepare()`; restore 96 kHz in `ca0132_playback_pcm_cleanup()`.
3. `ae9_direct` module parameter (default 0, writable).
4. Analog PCM rates mask extended to 192k in `build_pcms()` when `ae9_direct=1`.
5. `codec_info()` log line per rate change.

Verified (physical loopback, 1 kHz tone, capture `hw:Creative,4` @ 48k):

| Stream rate | Measured | BAR2[0x100] |
|---|---|---|
| 44100 | 1000.00 Hz | `0x20000700` |
| 48000 | 1000.00 Hz | `0x00000700` |
| 88200 | 1000.00 Hz | `0x60000700` |
| 96000 | 1000.00 Hz | `0x40000700` |
| 192000 | 1000.00 Hz | `0x80000700` |
| 176400 | silence | — (codes 4 and 5 tried) |

Known limitations: 176.4k unsupported; no explicit DAC mute around the re-clock; `ae9_direct` is a module parameter, not yet a mixer/GUI control.

### M2 — Direct Mode semantics + GUI — **done (core)**

Done:
1. Mixer control `AE-9 Direct Mode` (boolean) bound to the `ae9_direct` state, plus the module parameter — toggling needs no module reload and applies at the next stream start.
2. GUI (`tools/ae9-fx`, PR #1): **AE-9 Direct Mode** switch on the Format page, a live **Hardware rate** row read from `/proc/asound/<card>/pcm0p/sub0/hw_params`, and SBX switched off when Direct Mode is enabled.
3. The analog PCM always advertises 44.1–192 kHz now; the switch only changes whether the chain follows.
4. README: support matrix, module parameter and GUI notes.

Still open:
- the driver does not *lock* the effect controls while Direct Mode is on (the GUI only turns SBX off),
- no stereo-only constraint yet (Direct Mode is a stereo path in Windows),
- the SRC/DSP flags and `MASTERCONTROL 0x80/0x00` still follow the existing 96 kHz-era values (U2),
- a true non-resampling PipeWire path: the GUI already manages the graph's allowed rates, but `pw-play --target` / `aplay -D hw:Creative,0` remain the only guaranteed bit-perfect routes.

**Exit:** the user listens at 44.1/48/…/192 without DSP intervention and sees the rate.

### M3 — 384k (gate-dependent) — **blocked on M0b**

1. `hda-core` patch: 384k rate table entry and `snd_hdac_is_supported_format()`.
2. Driver: 384k entry in `ae9_i2s_rate_set()` (descriptors `0x1f103/0x31002`, GPIO1=1, DAC `0x01=0xc1`, `0x0b |= 0x20`).
3. If the link needs another frame clock, experiments on BAR2 `0x100` bits 31:29 and the PLL.
4. 10-minute soak, xruns, temperature, ACM.

**Exit:** L2 done, or a documented "not supported" with evidence.

### M4 — DSD64 (optional)

DoP through 176.4/192k or native DSD through the ES9038 mode. Only after L2.

### M5 — Hardening, docs, PR

- README support matrix.
- This document kept current.
- `install.sh`/`uninstall.sh` for the PipeWire direct node (if added).
- PR #4 / later PRs carry the commits in order M1 → M3 with the test tables.

---

## 7. Tests and verification

| What | How |
|---|---|
| Rate following | 1 kHz tone at each rate; pitch must stay 1000 Hz (a 96k feed at 44.1k would show ~2177 Hz) |
| No silence | `aplay` + `dmesg \| grep AE-9` + `cat /proc/asound/card0/pcm0p/sub0/hw_params` |
| No xruns | 10 minutes of `aplay -D hw:Creative,0`, xrun count |
| Rate code | read BAR2 `0x100` bits 31:29 during playback (`ae9gpio dump=1`) |
| Output switching | `amixer -c Creative sset 'AE-9 Output' Speakers/Headphone` stays exclusive |
| ACM | knob/display after a rate change (BAR2 is shared) |
| Suspend/resume | suspend → resume → rate test again (the unmute hook must not leave the chain at 96k wrongly) |
| 384k | same, plus FFT sweep for aliasing |

**Loopback rig (used for M1):** 3.5 mm cable from the ACM headphone output to the rear blue line-in; play a S32_LE sine with `aplay -D hw:0,0`, record with `arecord -D hw:0,4 -f S32_LE -r 48000`, measure the fundamental. Keep the ADC out of clipping (tone at -20 dBFS is enough).

**Pitfall:** What U Hear is fixed at 48k; it is not suitable for 192k/384k measurements. Use the physical loopback or an external ADC.

---

## 8. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Hard lock from HDA posted writes | Medium | Keep `patches/0001`; test with SSH/a rescue kernel; no tests without a backup |
| BAR2 conflict (ACM UART) | Medium | All rate changes under the same mutex; no polling during a change |
| 384k not supported by the link | High | Gate M0b; stop at 192k without touching the stable path |
| The Direct Mode switch breaks the effects | Medium | Separate state, restore on off, tests |
| Wrong Windows values (`FLOAT_ONE` vs `FLOAT_THREE`) | Medium | Do not guess; capture (U2) or A/B test |
| Pops at stream start/stop | Medium | Add an explicit DAC `0x07` mute/unmute around the re-clock |
| Time | — | M1/M2 have value on their own; L2 is optional |

---

## 9. What I need from you

To close U2 and the 384k question, ideally **one Windows capture with Direct Mode ON, 384k stereo**:

1. The **SDxFMT** of the playback stream (BAR0 + `0x60`, `0x80`, … per stream index) — does Windows send an 8× rate?
2. BAR2 `0x100` (bits 31:29), `0x189000/4/8`, `0x189024`, `0x189028` at stream start.
3. `0x80/0x00` (MASTERCONTROL) and the SRC/DSP flags.
4. Whether LPIB advances, and at what rate.
5. Ideally the same capture at 192k Direct for comparison.

Without a Windows machine we proceed with M0b probes and document the unknown.

---

## 10. Deliverables and branches

| Branch | Content |
|---|---|
| `work/ae9-direct` | M0 probe notes + M1 (and M3 if the gate passes) |
| `docs/reverse-engineering/direct-mode-plan.md` | This document |
| `tools/ae9-fx` (PR #1 branch) | Direct Mode switch + rate display (M2) |
| PR #4 | Driver commit `dc9b50f` (M1) |

The stable default is unchanged: `ae9_direct=0`.

---

## 11. Time estimate

| Phase | Days | Comment |
|---|---|---|
| M0 | 1–2 | Done: decided the 384k question is still open |
| M1 | 2–4 | Done: the real gain |
| M2 | 3–5 | UI + semantics |
| M3 | 3–7 | Only if M0b passes |
| M4 | 0–3 | Optional |
| M5 | 1–2 | Docs/PR |
| **Total (without M3/M4)** | **7–13** | L1 complete |
| **With M3** | **10–20** | L2 if the hardware cooperates |

---

## Appendix A — HDA format encoding (for probes)

`AC_FMT`: bits 3:0 = channels−1, 6:4 = bits/sample (4 = 32-bit), 10:8 = div−1, 13:11 = mult−1, 14 = base (0 = 48k).

| Target | Format |
|---|---|
| 192k, 2ch, 32-bit | `0x2203` |
| 384k, 2ch, 32-bit | `0x3841` |
| 384k, 2ch, 24-bit | `0x3831` |
| 384k, 2ch, 16-bit | `0x3811` |

> `0x3841` = `HDA_RATE(48, 8, 1) = 7 << 11 = 0x3800`, `AC_FMT_BITS_32 = 0x40`, channels−1 = 1.

`hda-mini` and the probe scripts are currently in `/tmp/opencode/`; they should move to `tools/` if we keep using them.

## Appendix B — Code map

| What | Where |
|---|---|
| PCM prepare (rate hook) | `driver/ca0132/ca0132.c:4142` |
| Rate follower `ae9_i2s_rate_set()` | `driver/ca0132/ca0132.c` (before `ae9_post_dsp_asi_pll_setup()`) |
| PLL bytes | `driver/ca0132/ca0132.c` `ae9_pll_set()` |
| DSP defaults (`0x80/0x00`) | `driver/ca0132/ca0132.c:9104` |
| Output path / ES9038 mute | `driver/ca0132/ca0132.c:~9555` |
| Rate mask extension | `ca0132_build_pcms()` |
| HDA rate table | `driver/hda-core/device.c:702` |
| HDA PCM caps query | `driver/hda-core/device.c:888` |
| GUI | `tools/ae9-fx` |
| PipeWire sources | `install/20-ae9-inputs.conf` |

## Appendix C — Sources

- `docs/reverse-engineering/ctxhda-acm-analysis.md` §10.5, §11.2, §11.4
- `docs/reverse-engineering/ctxhdb-analysis.md` (BAR2 `0x100` map)
- `docs/reverse-engineering/windows-vs-linux-init.md` W1–W10
- `docs/INVESTIGATION.md`
- s3boun3t wiki: "DSP & ChipIO Architecture", "ES9038Q2M Register Map", "Windows vs Linux Init Comparison" — https://github.com/s3boun3t/ae9_build/wiki
- HDA spec and the kernel `sound/hda/.../device.c` comments on rate bits
