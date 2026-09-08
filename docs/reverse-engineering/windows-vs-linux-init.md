# Sound Blaster AE-9: Windows driver init vs. Linux QUIRK_AE9 path

Source: `capture/ae9-20260903-210512.trace.ops.txt` (17,075 decoded ops; `ops:NNNN` = line
number in that file), `capture/ae9-20260903-210512.trace.i2c.c`, and
`hda-ca0132/ca0132.c` (kernel 7.0 + QUIRK_AE9 patch; `ca0132.c:NNNN`). RIRB responses were not
captured, so every "read" below is known only by its address. Firmware images compared:
`/lib/firmware/ctefx.bin` (Linux) and `reference/ctefx-from-ctxhda-6.0.105.0065.bin` (Windows).

## 0. Summary of what matters

1. **Linux starts DSP streams before the ASI/PLL/port configuration exists; Windows never does.**
   Windows configures param 3, flag 22, verb 0x724/0x725, ASI, PLL bytes, GPIO 1 and conn point
   0x70 *first* (ops:2258-2275) and only then starts stream 0x0c, then 0x18 (ops:2276-2292).
   Linux `ae9_setup_defaults` (ca0132.c:8579) calls `ae5_setup_defaults` (8364) first, which starts
   0x0c/0x03/0x04 (`ca0132_alt_start_dsp_audio_streams`, 7625) with nothing configured, then
   runs the AE-5 ASI/PLL/DAC values (wrong PLL bytes, ASI=4/7, 0x48 regs 0x0a=0x05/0x0b=0x12),
   then the correct AE-9 values. The s3boun3t note "cfg0/1/2 immutable after fw download"
   suggests the first stream start latches the configuration.
2. **Windows never touches streams 0x03/0x04, never frees DSP DMA channels, never writes
   0x18b098/0x18b09c/0x18b03c, never writes 8051 SFRs 0x90/0x93.** Linux does all of these on
   the AE-9 (`ca0132_alt_free_active_dma_channels` 7570, `ca0132_alt_dsp_initial_mic_setup`
   7912, `ca0132_alt_select_in` 5134, `ae5_post_dsp_register_set` 7944, `ae5_register_set` 9571,
   `ca0132_alt_select_out_quirk_set` 4705).
3. **Windows sends a MASTERCONTROL (0x80) start-up sequence right after `dsp_set_run_state`**:
   req 0x0c=3 (+2 GETs), an overlay download to XRAM 0x3f3a0, req 0x0a=0, 0x0b=1, 0x0c=4 (+GETs),
   0x0c=5 (+GETs), 0x0c=0 (+GETs), then `chipio_write(0x100e34, 0xf1000000)` (ops:2084-2196,
   repeated identically on resume ops:9003-9111). None of this exists in the Linux 7.0 driver
   (no `0x80/0x0c` anywhere, no overlay loading, no second 0x100e34 write).
4. **Windows writes chipio 0x18b008 = 0xf8 then 0xf0 at codec init (ops:1428-1430) and
   re-writes 0x18b008 = 0xf0 right before every stream start (ops:10030-10031).** The Linux
   AE-5/AE-9 path never writes 0x18b008 (only the AE-7 path does, ca0132.c:9672).
5. The DSP program differs: Linux runs `ctefx.bin` (no `ctefx-desktop.bin` installed); the Windows
   blob has the same segment layout and identical HCI-write segment but 187,457 differing bytes
   in the program segment. Drop-in test: copy the reference blob to
   `/lib/firmware/ctefx-desktop.bin`.
6. Codec 2 (cad2) is **not** part of the output path: zero cad2 verbs in phase 4 and phase 9
   (only enumeration/format probing in phases 3/7, D3 in phase 6). Linux's inert treatment is
   right.
7. Playback start on Windows is only: 0x18b008 read/write 0xf0, `SET_CVT_FORMAT nid 0x02`,
   output-mode re-apply (param 0x0d = 0xa4 + ES9038 THD/trim registers), `SET_STREAM nid 0x02
   tag 1`, SD run, `0x96/0x1a..0x1d = 1.0` (ops:10030-10101). No stream/conn-point work at
   play time; the routing is entirely done at init.

## 1. Windows init after the firmware download (phase 4)

Pre-conditions from phase 3 (before the download; ops line refs) that are relevant later:

| ops | operation | Linux equivalent |
|---|---|---|
| 1263 | PLL/PMU 0x44 = 0xc8 (after 8051 direct reads 0xfc-0xff) | `ae5_register_set` 9571 writes SFR 0x93=0x10 and PLL 0x44=**0xc2**; Windows writes no SFR |
| 1264-1279 | BAR2 0x304=0x3f x4, 0x100=0x0e/0x304/0x100=0x0c/0x304/0x100=0x08/0x304=0x7f/0x100=0/0x304=0xff, 0x86c=0, 0x800=0x6b, 0x86c=1, 0x800=0x6b | `ae5_register_set` (same shape, plus 0x804=0x57, SFR 0x90=0/0x10 that Windows lacks) |
| 1280-1288 | GPIO5=1; GPIO0 0->1 (anti-pop, once); 0x48 reg7 rd -> 0x81; 0x49 0x0a=7; GPIO2=0; 0x48 0x1d=0x40; GPIO3=1 | `ae9_post_dsp_d0_prepare` 8453 (after DSP, fine) |
| 1289 | BAR2 0x1c = 0x00880480 | `ca0132_mmio_init_ae5` 9494 (AE-9 special case) |
| 1292-1296 | flag IDLE_ENABLE=0; 0x18b0a4 rd -> 0xc2; RESET x2 | `ca0132_init_chip` 8952 (identical) |
| 1331 | CT_EXTENSIONS_ENABLE=1 | `ca0132_base_init_verbs` 8882 |
| 1343-1396 | pin config defaults (0x0b 0x01017010, 0x0c/0x0d 0x414510f0, 0x0e 0x01c520f0, 0x0f 0x01017114, 0x10 0x01017011, 0x11 0x41a170ff, 0x12 0x01a170f0, 0x13 0x908700f0, 0x18 0x500000f0) | `ae5_pincfgs` 1253 (0x10/0x11/0x0c/0x0d/0x0e/0x18 bytes differ; cosmetic) |
| 1397 | PLL 0x49 = 0x88 | `ca0132_alt_init` 9660 |
| 1398-1427 | 8051 exram 0xfef0.. / direct 0xce,0xc9 / exram 0xfee8.. / 0x1920 / 0x09b7,0x09af,0x09b0,0x0973 | `ca0132_init_verbs0/1` 8899/8932 (byte-identical) |
| **1428-1430** | **0x18b008 rd -> 0xf8 -> 0xf0** | **absent for AE-5/AE-9** (AE-7 only, 9672-9673) |
| 1431-1432 | unsol enable nid 0x16 = 0x80; DSP_INIT | `dsp_reset` 2566 (in `dspload_image`) |
| 1433-1439 | params SPDIF1_SOURCE=0, 0=0, VIP_SOURCE=0, PORTA/PORTD_160OHM_GAIN=6; flags 20=1, **21=1** | `ca0132_init_params` 8711 (same); `ca0132_init_flags` 8678 sets 20=1 but **21=0** |
| **1440-1441** | **0x18b030 rd -> 0x27** (0x21 on resume, ops:8416: read-modify, low bits kept) | `ca0132_alt_init` 9661 writes **0x20** blindly |
| 1442-1446 | 0x15/0x6ff=0xc4; 0x01/0x793=0, 0x794=0x53; flag SPDIF2OUT=0 | verbs1 8948, `ca0132_gpio_init` 3791, init_flags |
| 1447-1450 | conn 10 (WUH) -> 48k, conn 11 -> 48k | `ca0132_init_params` |
| 1575-1672 | pin ctl: 0x11=0x04, 0x12=0x24, 0x13=0x20, **0x0b=0x00 + EAPD 0**, 0x0f=0xc0, 0x10=0xc0; amp 0x12 in = 1; unsol 0x0b/0x0f/0x10=0x83, 0x12=0x81, 0x13=0x82; 0x01/0x790=0x23 | `init_output` 7399 sets 0x0b/0x0f/0x10/0x11 = PIN_HP; `ca0132_alt_select_out` enables EAPD on 0x0b. Not in the analog path on this card (external DACs) |
| 1674-1690 | exram reads 0x18d3..0x1955; PLL 0x00=0xff; flags DSP/DAC/ADC_B/ADC_C/SRC_RATE 96k = 1; conn 10/11 -> 48k | `chipio_enable_clocks` 2039 (also writes PLL 5=0x0b, 6=0xff, not in Windows); init_flags |
| 1693-2068 | main image download: SD4 FMT 0x0047, `CHIPIO_STREAM_FORMAT 0x47`, `SET_STREAM 0x15 tag 1`, PORT_ALLOC_CONFIG 7 / PORT_ALLOC 0x10, DSPDMAC 0x110fxx loop, HCI writes 0x100d20=5, 0x100d24=0xc, 0x100d34=0xd, 0x100d38=0xe, 0x100d3c=0xf, 0x100e34=0xf1000000 (these six come from the image's HCI segment and are identical in ctefx.bin), PORT_FREE | `dspxfr_image` 3410 / `dspxfr_hci_write` 3174 |

### W1. DSP run + MASTERCONTROL start-up (ops:2080-2196) - NOT in Linux except the first line

```
2080 FLAGS_GET
2081 chipio_read  0x100e30                    DSP_DBGCNTL
2082 chipio_write 0x100e30 = 0xfff83c00       clear single-step bits   } = dsp_set_run_state()
2083 chipio_write 0x100e30 = 0xfff83c0f       set EXEC bits            }   ca0132.c:2531 (Linux OK)
2084 SCP 0x80 src=0x00 req=0x0c SET [3]       MASTERCONTROL req 12 = 3
2085 SCP 0x80 src=0x00 req=0x0c GET (+2 read words)   x2 (2085, 2088)
2093-2161  overlay download: SD4 FMT 0x0041, CHIPIO_STREAM_FORMAT 0x41, SET_STREAM 0x15 tag 1,
           SCP 0x80 req 0x0a GET (alloc DMA chan), PORT_ALLOC_CONFIG 0x01 / PORT_ALLOC 0x10,
           SCP 0x80 req 0x3a GET [3, 0xffffd080], reads DSPDMAC 0x110ff8/ffc/fc0/f0c,
           SCP 0x80 req 0x3b GET [0x004edec4, 0x80000000], reads 0x110f00/04/08, polls 0x110ff0,
           SCP 0x80 req 0x3a GET [3, 0], SCP 0x80 req 0x3b GET [0x0014fce9, 0], polls,
           chipio_write 0x03f3a0 = 0x00800082, PORT_FREE, SET_STREAM 0x15 tag 0
2163 SCP 0x80 src=0x00 req=0x0a SET [0]       free DMA channel 0 (Linux frees only ovly channels)
2174 SCP 0x80 src=0x00 req=0x0b SET [1]
2175 SCP 0x80 src=0x00 req=0x0c SET [4]  + GET x2
2182 SCP 0x80 src=0x00 req=0x0c SET [5]  + GET x2
2189 SCP 0x80 src=0x00 req=0x0c SET [0]  + GET x2
2196 chipio_write 0x100e34 = 0xf1000000       re-assert the HCI-segment value (DSP core enable?)
```
Interpretation: a DSP master-control state machine (3 -> [module load] -> 4 -> 5 -> 0) that is
walked once after every firmware start (identical on resume, ops:9003-9111). The 0x3a/0x3b GETs
carry arguments (module 3, an address/size pair) and are the "where do I put this overlay"
query; the overlay is a small XRAM image (chip addr 0x3f3a0 is XRAM, beyond the 0x3c000 words
of the main image's XRAM segment) and 0x00800082 is written to its first word afterwards. The
Linux driver has nothing of this (`grep 0x80, 0x0[abc]` finds nothing; no overlay files).

### W2. DSP module defaults before the analog bring-up (ops:2197-2232)

```
2198 conn 3  -> rate 11 (96k); 2200 conn 12 -> 11; 2202 SCP 0x80/0x00 = 3.0     mic1  } alt_init_analog_mics 7823
2204 conn 5  -> 11;            2206 conn 14 -> 11; 2208 SCP 0x80/0x01 = 3.0     mic2  } Linux sends 0.0 for 0x80/0x01
2209-2213 I2C 0x48 0x0d,0x16,0x17,0x18,0x19 = 0          ES9038 THD table, output mode 0 (HP_Mute=1)
2214-2217 I2C 0x48 0x11,0x12,0x13,0x14 = ff ff ff 7f     master trim 0 dB
2218 I2C 0x48 0x1d = 0x80                                 front muted (Front_Mute=1 at this point)
2219 SCP 0x80/0x0d = 0 ; 2220 0x80/0x0e = 0               } ae5_setup_defaults 8381-8382
2223 SCP 0x96/0x3c = 0 (headroom) ; 2224 0x31/0 = 2.0 (WUH src) ; 2225 0x32/0 = 2.0   } 8395-8403
2228-2232 conn 3/12 -> 96k, SCP 0x80/0x00 = 3.0 again
```
Not sent by Windows but sent by Linux here: 0x96/0x29,0x2a (harmless, Windows sends them later
in the speaker-tuning block), 0x37/0x08 and 0x37/0x10 = 1.0 (8390-8391, never seen on Windows),
`chipio_set_conn_rate(WUH, 48k)` again, ca0113 group 0x30 commands (8383/8385, device does not
exist on the AE-9), `ca0113_mmio_gpio_set(0, false)` (8384), and the whole
`ca0132_alt_start_dsp_audio_streams` / `ca0132_alt_dsp_initial_mic_setup` block (see section 3a).

### W3. Board D0.2: ca0113 pins, ACM power, DAC mute (ops:2233-2257)

```
2233-2234 GPIO 3 = 1 (x2)
2235-2246 BAR2 0x304=0xff x4; (0x100=0x00, 0x304=0xff) x4          pins 4-7 output config
2247-2250 BAR2 0x86c=0, 0x800=0x6b, 0x86c=1, 0x800=0x6b            command-engine mode re-init
2251 GPIO 5 = 1                                                     ACM 12 V
2252-2253 I2C 0x48 reg 0x07 read, write 0x81                        ES9038 mute (bit0)
2254 I2C 0x49 0x0a = 0x07                                           SABRE mute
2255 GPIO 2 = 0 ; 2256 I2C 0x48 0x1d = 0x40 ; 2257 GPIO 3 = 1
```
Linux: `ae9_post_dsp_d0_prepare` 8453 does 0x100 &= ~0xf, 0x304=0xff, GPIO5, anti-pop pulse,
0x07=0x81, 0x49 0x0a=7, GPIO2=0, 0x1d=0x40, GPIO3=1. Missing: the 0x86c/0x800 re-init (done
once at `ae5_register_set`), GPIO 3 before. Equivalent enough.

### W4. Board D0.3 (I2SM start): ASI / PLL / stream routing (ops:2258-2311)

```
2258 PARAM 3 = 3
2259 FLAG 22 (CONTROL_FLAG_ASI_96KHZ) = 1
2260 verb 0x15/0x724 = 0x83
2261 PARAM ASI(23) = 0
2262 verb 0x17/0x794 = 0x00
2263 8051 exram 0xfa92 = 0x22
2264-2270 PLL/PMU 0x44=c8 0x43=cc 0x45=cb 0x40=c7 0x42=cd 0x41=ce 0x51=db
2271 verb 0x15/0x725 = 0x81 ; 2272 GET 0xf25 (poll)
2273 GPIO 1 = 0                                                      192 kHz select off
2274-2275 conn 0x70 -> rate 11 (96k)
2276-2277 stream 0x0c channels = 6
2278-2279 stream 0x0c control = 1                                    <- FIRST stream start of the boot
2280-2282 stream 0x05 source 0x43 dest 0x00
2283-2285 stream 0x18 source 0x09 dest 0xd0
2286-2287 conn 0xd0 -> 96k
2288-2289 stream 0x18 channels = 6
2290 8051 exram read 0x0824 (= 0x734 + 10*0x18, stream state byte)
2291-2292 stream 0x18 control = 1
2293 PARAM_EX ASI = 8
2294-2296 PLL 0x40=c7 0x42=cd 0x41=ce            (no second 0x725 here, contrary to the static RE)
2297-2301 chipio 0x189000=0x0001f101 0x189004=0x0001f101 0x189008=0x0001f101 0x189024=0x00014004 0x189028=0x0002000f
2302 I2C 0x48 0x01 = 0xc0                          ES9038 input: 32-bit I2S, serial only
2303 I2C 0x48 0x0a = 0x02
2304-2310 PLL 0x44=c8 0x43=cc 0x45=cb 0x40=c7 0x42=cd 0x41=ce 0x51=db
2311 PARAM_EX ASI = 15
```
Linux `ae9_post_dsp_asi_pll_setup` 8495-8531 reproduces this list (extra 0x725 at 8523, no
0x0824 read, no 0x48 0x01/0x0a writes at this point) BUT it runs after `ae5_setup_defaults`
already did: `ae5_post_dsp_param_setup` 7969 (param 3 = **0**, flag 22, 0x724, ASI=0, exram),
`ae5_post_dsp_pll_setup` 7989 (**0x41=c8 0x45=cc 0x40=cb 0x43=c7 0x51=8d** - AE-5 bytes),
`ae5_post_dsp_stream_setup` 7998 (0x725, conn 0x70, stream 5, stream 0x18 6ch **started**,
**ASI=4**, PLL 0x43=c7, 0x48 0x01=**0x80**), `ae5_post_dsp_startup_data` 8022 (0x189xxx,
**ASI=7**, ES9038 0x0a=0x05, 0x0b=0x12, 0x04=0, 0x06=0x48, 0x07=0x83/0x80, 0x0f/0x10=0,
ca0113 GPIO 0 and **GPIO 1 = 1**, chipio **0x18b03c=0x12**), and before all that
`ca0132_alt_start_dsp_audio_streams` 7625 started 0x0c, 0x03, 0x04 with ASI unset.

### W5. Board D0.4: external DAC register init (ops:2312-2340)

ES9038Q2M (group 0x48), in order: 0x0b rd -> 0x20; 0x04=0x00; 0x06=0x40; 0x08=0xff; 0x1d=0x40;
0x0a=0x06; 0x0c=0x5f; 0x11=0xff; 0x12=0xff; 0x13=0xff; 0x14=0x7f; 0x0e rd -> 0x8a; 0x07 rd ->
0x81; 0x0f=0x00; 0x10=0x00; 0x0a=0x02.
SABRE9006AS (group 0x49): 0x18=0xff; 0x19=0xff; 0x1a=0x00; 0x1b=0x70; 0x00=0xff; 0x01=0xff;
0x06=0xff; 0x07=0xff; 0x10=0x7f; 0x0a=0x07.
Linux `ae9_post_dsp_dac_init` 8533: byte-identical (the assumed read-back values 0x20/0x8a/0x81
match what Windows wrote).

### W6. ACM start, LED, unmute (ops:2344-2482)

GPIO5=1 polls + UART protocol (ACM), GPIO 4 = 1 (ops:2414, internal LED), 0x48 0x07 read
(2419), UART `F0 22 02 02 01` LED on (2402), `F0 03 03 02 00 40` (2404: ACM reg 2 bit 6 = 0),
display "AE-9" (2406); then 0x48 0x07 = 0x80 (2481, unmute), 0x49 0x0a = 0x06 (2482).
Linux `ae9_post_dsp_finish` 8564: GPIO4, 0x07=0x80, 0x49 0x0a=6, then THD table with
0x19=0x01 (Windows keeps 0x19=0 everywhere in this capture; harmless).

### W7. I2SM worker re-issue (ops:2483-2501)

stream 5 src/dst, stream 0x18 src/dst, conn 0xd0 96k, 0x18 channels 6, exram read 0x0824
(state non-zero -> control not re-sent), PLL 7 bytes, ASI = 15. Not in Linux; informational.

### W8. Mic/volume/pin housekeeping (ops:2502-2915, 3056-3228)

- nid 0x07 (ADC): verbs 0x797/0x798 then in-amp 0x60da/0x605d, 0x50da/0x505d; SCP 0x37/1=0,
  0x37/2=3.0, 0x37/3=3.0 (mic volume module, Linux `ca0132_alt_vol_ctls` 0x37 reqs {2,3,1}).
- nid 0x12 in-amp 0x6001/0x5001.
- Master volume -10.5 dB: I2C 0x48 0x0f=0x15, 0x10=0x15; nid 0x02/0x03/0x04 verbs 0x797/0x798 +
  out-amp 0xa0da->0xa05a / 0x90da->0x905a (gain 0x5a); SCP 0x32/3 = 0x32/4 = 0xc1280000, 0x32/2 = 0;
  I2C 0x49 0x04,0x05,0x02,0x03 = 0x15 (SABRE channel volumes).  Linux never writes 0x49
  0x02-0x05 (SABRE volumes) - Windows leaves them at 0xff (mute) on shutdown (ops:7765-7775).
- Pin ctl / EAPD as in phase 3 (0x0b = 0, EAPD 0; 0x0f/0x10 = 0xc0), pin configs re-written,
  nid 0x04 power D0->D3.
- ops:3224-3227: 0x48 0x07 read x2; 0x49 0x0e read -> 0x01 (SABRE reg 0x0e bit0 set). Linux never
  writes 0x49 0x0e.

### W9. Output-mode apply / "select out" (ops:6374-6470, again 6529-6545)

```
6374 PARAM_EX 13 (0x0d, "dac2port") = 0xa4                    Linux quirk_out_set_data AE9 .dac2port 0xa4 (1464), applied at 4750
6375 I2C 0x48 0x1d = 0x40; 6378-6391 THD 0x0d,0x16..0x19 = 0; 0x0f/0x10 = 0x15; trims ff ff ff 7f
6459 SCP 0x80/0x04 = 8.0 (FLOAT_EIGHT: 2.0 speakers, no DSP)   ca0132_alt_select_out 4869
6460 SCP 0x96/0x18 = 0 ; 6461 0x8f/0x01 = 0 ; 6462 0x96/0x1f = 0 ; 6463 0x96/0x15 = 0 (bass redir)
6464 SCP 0x96/0x3a = 0 (unmute) ; GET 0x3a
6476-6489 SCP 0x96/0x1a,0x1b,0x1c,0x1d = 1.0 (full range)      ca0132_alt_set_full_range_speaker
```
Linux additionally writes ca0113 group 0x30 (absent device), 0x48 0x0d=0x40, 0x17=0, 0x19=0
(`ae5_ca0113_output_presets` 737), chipio 0x18b03c=0x12 (never on Windows), 0x96 invert reqs,
and the headphone-gain trims `ae5_headphone_gain_presets[0]` = ff 2c f5 32 (-8 dB; Windows 0 dB).

### W10. Effects / speaker tuning / EQ defaults (ops:6674-7129)

0x96 reqs 0x00-0x14 (effects defaults: 1.0, 0.667, 0, 0.5, 0, 0.74, 0, 1.0, 0.65, 0...) =
Linux `ca0132_effects[]` defaults loop (8413-8420); 0x96/0x16 = 80.0 (xover); 0x96/0x20-0x26,
0x29-0x2e, 0x31-0x36 = 0 (`ca0132_alt_init_speaker_tuning` 7778 sends the same set; delays are
the ae5 table there); 0x95/0x0a-0x11 (EQ). Equivalent.

### W11. Power-down at the end of phase 4 (ops:7733-7777) - shows the tear-down order

PARAM ASI=0; stream 0x18 control 0; stream 0x0c control 0 (after reading 0x0824/0x07ac);
GPIO1=0; 0x724=0x83; 0x48 0x07 |= 1; 0x49 0x0a=7 (x2); GPIO 4,5,3 = 0; 0x48 trims = 0,
0x0f/0x10 = 0xff, 0x07 |= 1, 0x0e = 0x0a; 0x49 0x02/0x04/0x03/0x05 = 0xff.
Phases 5-8 are D3 (I2C mutes, pin ctl 0, 0x793/0x794, cad2 D3) and the resume re-init
(phase 7 = cad2 enumeration, phase 8 = cad1 pre-DSP init identical to phase 3).

## 2. Playback start and output switching (phase 9)

Phase 9 = resume: full firmware re-download (ops:8642-8987, same as phase 4) then the identical
W1-W10 sequence (ops:8999-9297: run state, 0x0c=3, overlay, 0x0a/0x0b/0x0c, 0x100e34, mic conn
points now at rate 4 = 16 kHz for mic 1 because a capture client was open, D0.2/D0.3/D0.4,
unmute at 9409-9412, worker at 9413-9431, select_out at 9975-9993).

The first real playback (speaker test, 48 kHz / 24-bit / 6 ch):

```
9997-10011 SD4: FMT 0x0035 (48 kHz, 24-bit, 6 ch), CTL 0x1/0x8/0x18/0x1c (stream number byte not decoded)
10029 SCP 0x96/0x3a = 0
10030 chipio_read  0x18b008
10031 chipio_write 0x18b008 = 0x000000f0
10032 SET_CVT_FORMAT nid 0x02 fmt 0x0035
10033 PARAM_EX 0x0d = 0xa4 ; 10034 0x48 0x1d=0x40 ; 10037-10092 THD zeros + trims ff ff ff 7f (output-mode re-apply)
10084-10088 SCP 0x96/0x3a = 0, GET
10093 SET_STREAM nid 0x02 stream 1 ch 0
10094-10097 SD4 CTL 0x1c -> 0x1e (RUN)
10098-10101 SCP 0x96/0x1a,0x1b,0x1c,0x1d = 1.0
10102-10107 SD4 stop (0x1c, 0x1d) ; 10106 SCP 0x96/0x3a = 0
10110 SET_STREAM nid 0x02 stream 0
```
No STREAM_ID / CONN_POINT / DMA / PLL operation at play start. Linux
`ca0132_playback_pcm_prepare` 3937 -> `snd_hda_codec_setup_stream(dacs[0]=0x02, tag, 0, fmt)`
is the same verb pair; the difference is only the 0x18b008 write and the ES9038 re-apply.

Headphone switch: the operator's "headphone switch" produced no additional GPIO/I2C/SCP class
of operation in phase 9 beyond the select_out block (ops:9972-9993, 10137-10262: 0x96/0x3a=1.0
mute, param 0x0d=0xa4, THD/trims, 0x80/0x04=8.0, 0x96/0x18,0x1f,0x15=0, 0x8f/0x01=0,
0x96/0x1a-0x1d=1.0, effects, 0x95 EQ) and the ACM messages `F0 03 03 02 00 40 F7` (reg 2 bit 6
= 0, ops:9977/10035/10179) and display "-SP-" (ops:9973). The dac2port value stayed 0xa4
(speakers) throughout, 0x48 0x1d stayed 0x40 and the THD table stayed all-zero, so the
headphone path was never selected on the codec/DAC side during this capture (the ACM's own
button/relay handles the jack; the driver only mirrors it). ES9038 registers 0x0d/0x16-0x19 are
never non-zero anywhere in the capture.

## 3. Item-by-item comparison

### (a) Stream / connection-point routing

| Windows (phase 4 / 9) | value | Linux QUIRK_AE9 |
|---|---|---|
| conn 10 (WUH), 11 -> 48k (ops:1447, 1687) | rate 9 | `ca0132_init_params` 8711: same |
| conn 3, 12 (mic1 in/out) -> 96k; SCP 0x80/0 = 3.0 | rate 11 | `ca0132_alt_init_analog_mics` 7823: same (also `alt_select_in` 5134 repeats) |
| conn 5, 14 (mic2) -> 96k; SCP 0x80/1 = **3.0** | rate 11 | same rates, value **0.0** (7844) |
| conn 0x70 -> 96k (ops:2274) | rate 11 | `ae5_post_dsp_stream_setup` 8009 + `ae9_post_dsp_asi_pll_setup` 8509 |
| stream 0x0c: channels 6, control 1 (ops:2276-2279), after ASI/PLL | | 7659/7662 (before anything), 8510-8511 (again) |
| stream 0x05: src 0x43 dst 0x00 (ops:2280) | | 8011, 8512 |
| stream 0x18: src 0x09 dst 0xd0; conn 0xd0 96k; ch 6; control 1 (ops:2283-2292) | | 8013-8020, 8513-8516 |
| stream 0x03 / 0x04 | **never** | started 7662-7665, stopped/started 7917-7927, 5143-5190 (select_in), 5023-5073 (vipsource) |
| DMA channel free (SCP 0x80/0x0a SET) | only chan 0 after each download | `ca0132_alt_free_active_dma_channels` 7570 frees every channel that DSPDMAC_CHNLSTART (0x110ff0) reports active |
| ASI | 0 -> 8 -> 15 | 0 (7983) -> **4** (8015) -> **7** (8034) -> 0 (8502) -> 8 (8518) -> 15 (8530) |
| param 3 | 3 | **0** (7975) then 3 (8499) |
| param 0x0d (dac2port) at select_out | 0xa4 | 0xa4 (1464, 4750) - same |
| flag 22 ASI_96KHZ | 1 | 1 |
| flag 21 PORT_D_10KOHM_LOAD | 1 | 0 (8691) |

### (b) CHIPIO writes outside 0x110fxx

| addr | Windows | Linux AE-9 |
|---|---|---|
| 0x18b0a4 | rd, 0xc2 (phase 3/7) | 8969 same |
| **0x18b008** | rd, **0xf8, 0xf0** at init (1428); rd, **0xf0** before each stream start (10030) | **none** (AE-7 only, 9672) |
| **0x18b030** | rd, **0x27** (read-modify; 0x21 on resume) | **0x20** (9661) |
| 0x100e30 | rd, 0xfff83c00, 0xfff83c0f | `dsp_set_run_state` 2531 same |
| 0x100d20/24/34/38/3c, 0x100e34 | 5, 0xc, 0xd, 0xe, 0xf, 0xf1000000 during download | identical HCI segment in ctefx.bin, `dspxfr_hci_write` 3174 |
| **0x100e34** | **0xf1000000 again after the SCP start-up** (2196, 9111) | **none** |
| **0x03f3a0** | **0x00800082 after the overlay download** (2153, 9072) | **none** |
| 0x189000/004/008 | 0x0001f101 | 8039-8040 (000/004 only) + 8524-8526 (all three) |
| 0x189024 / 0x189028 | 0x00014004 / 0x0002000f | 8041-8042, 8527-8528 |
| 0x18b098 / 0x18b09c | **never** | 0x0c / 0x4c (7935-7936, 5197-5198) |
| 0x18b03c | **never** | 0x12 (8046, select_out quirk data 1478-1489) |

### (c) SCP messages (target, req, data)

Windows-only: 0x80 src 0 req 0x0c = 3, 4, 5, 0 (+GET after each); 0x80 req 0x0b = 1; 0x80 req
0x3a/0x3b GET with arguments (overlay); 0x80/0x01 = 3.0 (Linux 0.0); 0x80/0x3d GET (9145);
0x80/0x05 = 1.0 (9144, VIP); 0x95/* EQ set; 0x47/0 = 1.0 (10489).
Linux-only: 0x37/0x08 = 1.0, 0x37/0x10 = 1.0 (8390-8391); 0x96 invert reqs at select_out;
0x96/0x29,0x2a at 8379 (Windows sends them in W10 anyway).
Common: 0x80/0x00 = 3.0, 0x80/0x0d,0x0e = 0, 0x80/0x04 = 8.0, 0x96/0x3c = 0, 0x31/0 = 2.0,
0x32/0 = 2.0, 0x32/3,4,2 (volume), 0x37/1,2,3 (mic volume), 0x8f/0x01 = 0, 0x96/0x15,0x16,
0x18,0x1a-0x1d,0x1f,0x20-0x26,0x29-0x2e,0x31-0x36,0x3a, effects 0x96/0x00-0x14.

### (d) PLL/PMU and 8051

| | Windows | Linux |
|---|---|---|
| PLL 0x44 pre-DSP | 0xc8 (1263) | 0xc2 (9589 `ae5_register_set`, 7950 `ae5_post_dsp_register_set`) |
| PLL 0x49 | 0x88 | 0x88 (9660) |
| PLL 0x00 before download | 0xff | 0xff + **0x05=0x0b, 0x06=0xff** (2039) |
| PLL AE-9 set | 44=c8 43=cc 45=cb 40=c7 42=cd 41=ce 51=db (twice) + 40/42/41 in between | `ae9_pll_set` 8484 same, but preceded by AE-5 bytes 41=c8 45=cc 40=cb 43=c7 51=8d (7989) and 43=c7 (8017) |
| exram 0xfa92 | 0x22 | 0x22 (7985, 8504) |
| exram reads 0x0824 / 0x07ac (stream state) | before starting 0x18, at shutdown | none |
| 8051 SFR direct writes | only 0xce/0xc9 pairs from the init tables | additionally **0x93=0x10** (9586, 7949) and **0x90=0x00, 0x90=0x10** (9615-9616) |
| exram/direct init tables | identical to verbs0/verbs1 | 8899/8932 |

### (e) Codec 2 (cad2, subsystem 0x11020072)

Used only in phase 3 (ops:57-1088: RESET x2, SET_POWER D3 on every widget, pin ctl 0x0b/0x0f/
0x10 = 0, 0x0c = 0x40, 0x0e = 0x20, 0x11 = 0x22, EAPD 0x0b=0x02, amps 0x07/0x12 = 0x7000,
SET_DIGI_CVT1 on 0x05: 0x10 -> 0x11 -> 0x15 and 0x09: 0x10 -> 0x11, CVT2 0x12, vendor 0x73e=0x80,
0x72d=1, then the Windows HDA class driver's format-capability probing: SET_STREAM 0 +
SET_CVT_FORMAT sweeps on 0x05/0x08/0x09 in lock-step with HDA_SD0/SD4 FMT), phase 6 (D3) and
phase 7 (the same enumeration on resume). **Zero cad2 operations in phase 4 and phase 9**, i.e.
none during the analog bring-up, the DSP start-up or the playback test. The Linux DBPro-style
inert handling is correct; nothing on codec 2 is needed for analog output.

### (f) ES9038 (0x48) and SABRE (0x49)

Init sequence (W5): Linux `ae9_post_dsp_dac_init` matches byte for byte. Differences around it:
- Linux writes AE-5 values first (`ae5_post_dsp_register_set` 7965: 0x07=0x83;
  `ae5_post_dsp_stream_setup` 8020: 0x01=0x80; `ae5_post_dsp_startup_data` 8044-8055: 0x0a=0x05,
  0x0b=0x12, 0x04=0x00, 0x06=0x48, 0x07=0x83, 0x0f/0x10=0, 0x07=0x80; `ae5_register_set` 9628:
  0x07=0x83). Windows: 0x01=0xc0, 0x0a=0x02 (W4) before the register init.
- Windows re-applies THD (0x0d,0x16-0x19 = 0), trims (0x11-0x14 = ff ff ff 7f) and 0x1d at every
  output-mode change and every stream start; Linux `ae5_mmio_select_out` writes 0x0d=0x40 (spk),
  0x17=0, 0x19=0 and the headphone-gain trims (ff 2c f5 32 = -8 dB by default).
- Channel volumes: Windows 0x48 0x0f/0x10 = 0x15 and 0x49 0x02-0x05 = 0x15 (-10.5 dB); Linux sets
  0x48 0x0f/0x10 = 0 (0 dB) and never writes the SABRE volumes (they are 0xff = mute after a
  Windows shutdown or D3, ops:7765-7775; power-on default unknown).
- Windows 0x49 0x0e: read, write |1 (ops:3226-3227, 7840-7841, 9879-9880). Linux never.

### (g) HDA stream tag / format

Download: SD4 FMT 0x0047 (48 kHz, 32-bit, 8 ch), `CHIPIO_STREAM_FORMAT 0x0047`, `SET_STREAM
nid 0x15 stream 1`; overlay: 0x0041 (2 ch) with PORT_ALLOC_CONFIG 0x01. Playback: SD4 FMT 0x0035
(48 kHz, 24-bit, 6 ch), `SET_CVT_FORMAT nid 0x02 0x0035`, `SET_STREAM nid 0x02 stream 1`. Linux
uses the same nids (0x15 for the loader, dacs[0]=0x02 for playback); the tag value is whatever
the controller assigns and does not matter. The DSP-side conn points stay at 96 kHz for a
48 kHz stream (SRC inside the DSP), so Linux playing 48 kHz 2 ch on 0x02 is not a problem.

## 4. Ranked causes and the proposed fix

Ranking is by how far the Linux operation is from anything Windows ever does to this card,
weighted by whether it touches the DSP stream/DMA state that s3boun3t saw frozen.

1. **Stream 0x0c/0x03/0x04 are started (and DSP DMA channels freed) before the ASI/PLL/port
   configuration, then everything is re-done twice with AE-5 then AE-9 values.** Windows: one
   pass, config first, then start 0x0c and 0x18 exactly once, 0x03/0x04 untouched, no DMA-channel
   free. Fix: stop calling `ae5_setup_defaults` from `ae9_setup_defaults`; implement the Windows
   order (below); for QUIRK_AE9 skip `ca0132_alt_start_dsp_audio_streams`,
   `ca0132_alt_free_active_dma_channels`, `ca0132_alt_dsp_initial_mic_setup`, the 0x03/0x04
   handling in `ca0132_alt_select_in` at init, and all group-0x30 / 0x18b03c / 0x18b098 writes.
2. **MASTERCONTROL start-up sequence after `dsp_set_run_state` is missing** (0x0c=3, [overlay],
   0x0a=0, 0x0b=1, 0x0c=4, 0x0c=5, 0x0c=0, then 0x100e34=0xf1000000). Windows walks it on every
   firmware start; it is the only DSP-side "go" sequence in the capture. Add it (without the
   overlay first; the overlay image is one of the ~10 small blobs in CtxHda.sys and is loaded to
   XRAM 0x3f3a0, then word 0x3f3a0 = 0x00800082).
3. **chipio 0x18b008 = 0xf8, 0xf0 at init and = 0xf0 before every stream start** is missing on the
   AE-5/AE-9 path (present for AE-7). Together with 0x18b030 being read-modify-written to 0x27
   (Linux forces 0x20, clearing bits 0-2). These are chip-level clock/port enables next to
   0x18b0a4; cheap to add.
4. **Different DSP program**: Linux loads `ctefx.bin` (laptop image), Windows runs the blob saved
   as `reference/ctefx-from-ctxhda-6.0.105.0065.bin` (same layout, same HCI segment, 187 KB of
   program differences). Install it as `/lib/firmware/ctefx-desktop.bin` (the driver already
   prefers that name, ca0132.c:8757).
5. Extra Linux-only hardware pokes that Windows never issues on this board and that can affect
   the I2S/DAC side: 8051 SFR 0x90 = 0x00/0x10 and 0x93 = 0x10 (P1 port toggles), PLL 0x44 = 0xc2
   and PLL 0x05/0x06 writes, ca0113 GPIO 1 = 1 (192 kHz select) in `ae5_post_dsp_startup_data`,
   chipio 0x18b03c = 0x12 at every select_out, ES9038 0x0a=0x05 / 0x0b=0x12 / 0x06=0x48 before
   the real init, group-0x30 command-engine writes to a non-existent device. Remove for AE-9.
6. SABRE channel volumes (0x49 0x02-0x05) never written and 0x49 0x0e bit0 never set; ES9038
   trims left at the -8 dB "Low" preset. Not a cause of total silence unless the SABRE was left
   at 0xff by a previous Windows session, but write them anyway (0x15 or 0x00).
7. Minor: flag 21 = 1, 0x80/0x01 = 3.0, 0x37/0x08,0x10 not sent, pin ctl 0x0b = 0 / EAPD 0 on
   Windows (Linux drives 0x0b as speaker with EAPD 1 - irrelevant for the external DAC path).

### Proposed C sequence (replaces `ae9_setup_defaults`; helpers already exist)

```c
/* ---- alt_init additions for QUIRK_AE9 (ca0132_alt_init, before verbs0/verbs1) ---- */
chipio_8051_write_pll_pmu(codec, 0x49, 0x88);
snd_hda_sequence_write(codec, spec->chip_init_verbs);       /* verbs0 */
snd_hda_sequence_write(codec, spec->desktop_init_verbs);    /* verbs1 (incl. 0x6ff/0xc4) */
chipio_write(codec, 0x18b008, 0x000000f8);                  /* ops:1429 */
chipio_write(codec, 0x18b008, 0x000000f0);                  /* ops:1430 */
chipio_read(codec, 0x18b030, &tmp);
chipio_write(codec, 0x18b030, (tmp & 0x07) | 0x20);         /* ops:1440-1441 (0x27 seen) */
/* do NOT call ca0113_mmio_command_set(codec, 0x30, ...) on the AE-9 */
/* in ae5_register_set(): for AE9 use PLL 0x44 = 0xc8 and skip SFR 0x93/0x90 writes */

/* ---- after ca0132_download_dsp(): DSP master-control start-up (ops:2084-2196) ---- */
static void ae9_dsp_scp_startup(struct hda_codec *codec)
{
	unsigned int tmp, dummy;
	int i;

	tmp = 3;
	dspio_scp(codec, MASTERCONTROL, 0x00, 0x0c, SCP_SET, &tmp, 1, NULL, &dummy);
	for (i = 0; i < 2; i++)
		dspio_scp(codec, MASTERCONTROL, 0x00, 0x0c, SCP_GET, NULL, 0, &tmp, &dummy);
	/* (Windows loads the 0x3f3a0 XRAM overlay here with dspload_image(ovly=1),
	 *  then chipio_write(0x03f3a0, 0x00800082)) */
	tmp = 0; dspio_scp(codec, MASTERCONTROL, 0x00, 0x0a, SCP_SET, &tmp, 1, NULL, &dummy);
	tmp = 1; dspio_scp(codec, MASTERCONTROL, 0x00, 0x0b, SCP_SET, &tmp, 1, NULL, &dummy);
	static const unsigned int seq[] = { 4, 5, 0 };
	for (i = 0; i < 3; i++) {
		tmp = seq[i];
		dspio_scp(codec, MASTERCONTROL, 0x00, 0x0c, SCP_SET, &tmp, 1, NULL, &dummy);
		dspio_scp(codec, MASTERCONTROL, 0x00, 0x0c, SCP_GET, NULL, 0, &tmp, &dummy);
		dspio_scp(codec, MASTERCONTROL, 0x00, 0x0c, SCP_GET, NULL, 0, &tmp, &dummy);
	}
	chipio_write(codec, 0x100e34, 0xf1000000);                 /* ops:2196 */
}

/* ---- W2: DSP module defaults (ops:2198-2232) ---- */
static void ae9_dsp_defaults(struct hda_codec *codec)
{
	chipio_set_conn_rate(codec, MEM_CONNID_MICIN1, SR_96_000);
	chipio_set_conn_rate(codec, MEM_CONNID_MICOUT1, SR_96_000);
	dspio_set_uint_param(codec, 0x80, 0x00, FLOAT_THREE);
	chipio_set_conn_rate(codec, MEM_CONNID_MICIN2, SR_96_000);
	chipio_set_conn_rate(codec, MEM_CONNID_MICOUT2, SR_96_000);
	dspio_set_uint_param(codec, 0x80, 0x01, FLOAT_THREE);
	ae9_dac_write(codec, 0x0d, 0x00); ae9_dac_write(codec, 0x16, 0x00);
	ae9_dac_write(codec, 0x17, 0x00); ae9_dac_write(codec, 0x18, 0x00);
	ae9_dac_write(codec, 0x19, 0x00);
	ae9_dac_write(codec, 0x11, 0xff); ae9_dac_write(codec, 0x12, 0xff);
	ae9_dac_write(codec, 0x13, 0xff); ae9_dac_write(codec, 0x14, 0x7f);
	ae9_dac_write(codec, 0x1d, 0x80);
	dspio_set_uint_param(codec, 0x80, 0x0d, FLOAT_ZERO);
	dspio_set_uint_param(codec, 0x80, 0x0e, FLOAT_ZERO);
	dspio_set_uint_param(codec, 0x96, 0x3c, FLOAT_ZERO);
	dspio_set_uint_param(codec, 0x31, 0x00, FLOAT_TWO);
	dspio_set_uint_param(codec, 0x32, 0x00, FLOAT_TWO);
}

/* ---- W3: existing ae9_post_dsp_d0_prepare() plus the engine re-init ---- */
/* add before GPIO 5: ca0113_mmio_writel(spec, 0x86c, 0); writel(0x800, 0x6b);
 *                    writel(0x86c, 1); writel(0x800, 0x6b);   (ops:2247-2250) */

/* ---- W4: ASI/PLL, then the ONLY stream starts of the boot (ops:2258-2311) ---- */
static void ae9_post_dsp_asi_pll_setup(struct hda_codec *codec)
{
	struct ca0132_spec *spec = codec->spec;

	chipio_set_control_param(codec, 3, 3);
	chipio_set_control_flag(codec, CONTROL_FLAG_ASI_96KHZ, 1);
	snd_hda_codec_write(codec, WIDGET_CHIP_CTRL, 0, 0x724, 0x83);
	chipio_set_control_param(codec, CONTROL_PARAM_ASI, 0);
	snd_hda_codec_write(codec, 0x17, 0, 0x794, 0x00);
	chipio_8051_write_exram(codec, 0xfa92, 0x22);
	ae9_pll_set(codec);
	ae9_chip_ctrl_725(codec);
	ae9_gpio_cmd(spec, 0x0001);                          /* GPIO 1 = 0 */

	guard(mutex)(&spec->chipio_mutex);
	chipio_set_conn_rate_no_mutex(codec, 0x70, SR_96_000);
	chipio_set_stream_channels(codec, 0x0c, 6);
	chipio_set_stream_control(codec, 0x0c, 1);            /* first stream start */
	chipio_set_stream_source_dest(codec, 0x05, 0x43, 0x00);
	chipio_set_stream_source_dest(codec, 0x18, 0x09, 0xd0);
	chipio_set_conn_rate_no_mutex(codec, 0xd0, SR_96_000);
	chipio_set_stream_channels(codec, 0x18, 6);
	/* optional: chipio_8051_read_exram(codec, 0x0824, &st); start only if st == 0 */
	chipio_set_stream_control(codec, 0x18, 1);
	chipio_set_control_param_no_mutex(codec, CONTROL_PARAM_ASI, 8);
	chipio_8051_write_pll_pmu_no_mutex(codec, 0x40, 0xc7);
	chipio_8051_write_pll_pmu_no_mutex(codec, 0x42, 0xcd);
	chipio_8051_write_pll_pmu_no_mutex(codec, 0x41, 0xce);
	chipio_write_no_mutex(codec, 0x189000, 0x0001f101);
	chipio_write_no_mutex(codec, 0x189004, 0x0001f101);
	chipio_write_no_mutex(codec, 0x189008, 0x0001f101);
	chipio_write_no_mutex(codec, 0x189024, 0x00014004);
	chipio_write_no_mutex(codec, 0x189028, 0x0002000f);
	ae9_dac_write(codec, 0x01, 0xc0);                     /* ops:2302 */
	ae9_dac_write(codec, 0x0a, 0x02);                     /* ops:2303 */
	/* ae9_pll_set() again, no mutex variants */
	chipio_set_control_param_no_mutex(codec, CONTROL_PARAM_ASI, 0xf);
}

/* ---- W5/W6: existing ae9_post_dsp_dac_init() and ae9_post_dsp_finish() (keep 0x19 = 0) ---- */
/* add to finish: ae9_dev49_write(codec, 0x02..0x05, 0x00 or 0x15); ae9_dev49_write(codec, 0x0e, 0x01);
 *                ae9_dac_write(codec, 0x0f, 0x00); ae9_dac_write(codec, 0x10, 0x00); */

static void ae9_setup_defaults(struct hda_codec *codec)
{
	struct ca0132_spec *spec = codec->spec;
	int idx, i;

	if (spec->dsp_state != DSP_DOWNLOADED)
		return;
	ae9_dsp_scp_startup(codec);              /* W1 */
	ae9_dsp_defaults(codec);                 /* W2 */
	ae9_post_dsp_d0_prepare(codec);          /* W3 */
	ae9_post_dsp_asi_pll_setup(codec);       /* W4 */
	ae9_post_dsp_dac_init(codec);            /* W5 */
	ae9_post_dsp_finish(codec);              /* W6 */
	/* W10: effects defaults + speaker tuning, as Windows does after unmute */
	for (idx = 0; idx < OUT_EFFECTS_COUNT + IN_EFFECTS_COUNT + 1; idx++)
		for (i = 0; i <= ca0132_effects[idx].params; i++)
			dspio_set_uint_param(codec, ca0132_effects[idx].mid,
					     ca0132_effects[idx].reqs[i],
					     ca0132_effects[idx].def_vals[i]);
	ca0132_alt_init_speaker_tuning(codec);
	dspio_set_uint_param(codec, 0x8f, 0x01, FLOAT_ZERO);
}

/* ---- ca0132_init(): for QUIRK_AE9 keep ca0132_alt_select_out (param 0x0d = 0xa4 is right) but
 * in ca0132_alt_select_out_quirk_set / ae5_mmio_select_out skip group 0x30 and 0x18b03c, and
 * defer ca0132_alt_select_in (stream 0x03/0x04 stop/start, 0x18b098/0x18b09c) until a capture
 * stream is actually opened. ---- */

/* ---- playback: ca0132_playback_pcm_prepare (ops:10030-10093) ---- */
if (ca0132_quirk(spec) == QUIRK_AE9) {
	chipio_read(codec, 0x18b008, &tmp);
	chipio_write(codec, 0x18b008, 0x000000f0);
}
snd_hda_codec_setup_stream(codec, spec->dacs[0], stream_tag, 0, format);
```

Verification hooks that cost nothing: after W4 read exram 0x07ac (stream 0x0c) and 0x0824
(stream 0x18) - Windows stops a stream whose byte is non-zero - and read DSPDMAC_CHNLSTART
(0x110ff0) / DSPDMAC_ACTIVE to confirm that channels for 0x0c/0x18 came up, before enabling the
DACs.
