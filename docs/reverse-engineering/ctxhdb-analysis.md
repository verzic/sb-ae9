# CtxHdb.sys 6.0.105.0065 (AMD64) — static analysis of the Creative "HDABusFilter"

Target: `reference/windows-driver-6.0.105.0065/AMD64/CtxHdb.sys`
(64 056 bytes, sha256 `ec32b2c95b4f4d0673181802a88a6e21e8f194a6a7651957f8c50856a42e46d3`,
PE32+ kernel driver, link date 2022-12-14, no PDB, FileVersion 6.0.105.0065-1.05.0000).

Method: `objdump -d -M intel` of `.text`/`PAGE`/`INIT`, `objdump -p` for the import
table, a small Python annotator that resolved every RIP-relative operand to an IAT
slot / string / data address and built a cross-reference list, and a table parser for
`.data`. All addresses below are image VMAs with the on-disk ImageBase 0x10000
(section map: `.text` 0x11000, `.rdata` 0x16000, `.data` 0x17000, `PAGE` 0x1a000,
`INIT` 0x1b000). Nothing was executed. The code is unobfuscated MSVC output; every
function that touches hardware was followed to completion. Where something is inferred
rather than read it is marked **(inferred)**.

Working files (annotated disassembly, xrefs, table dump):

---------------------------------------------------------------------------------------

## 0. One-paragraph summary

The filter does three kinds of hardware work, in this order:

1. **At AddDevice (before IRP_MN_START_DEVICE)**: through `BUS_INTERFACE_STANDARD`
   it writes PCI config space of the controller: a single 16-bit write of **0x0080 to
   config offset 0x40** (only when VEN/DEV == 1102:0010), followed by any registry-supplied
   `PCIConfig_Offset_n/PCIConfig_Data_n` byte writes (the INF supplies none). A second
   routine looks for an IDT/Tundra **10e3:8111** PCIe-to-PCI bridge anywhere in the
   system and writes config byte 0x53 = 0x00 to it; the AE-9's ASMedia ASM1083 does not
   match, so nothing happens on this card.
2. **At START_DEVICE (after the PCI PDO has completed START) and again on every
   SET_POWER→D0**: it `MmMapIoSpace`s every memory resource of the device (BAR0 and BAR2,
   16 KiB each, non-cached), then (a) programs a block of BAR2 registers — an I2S block
   at 0x100/0x400–0x4ff, the "CA0113 MMIO command" block at 0x200–0x210/0x800–0x8ff, a
   16550-style UART at 0xc00 set to 31250 baud, and RMWs BAR2+0x1c — and (b) runs a
   codec pass on BAR0: GCTL.CRST reset cycle, CORBCTL=0/RIRBCTL=0, IRS byte writes
   0x05/0x00/0x04, reads vendor+subsystem IDs of codecs at SDI1/SDI2 with the
   immediate-command registers (0x60/0x64/0x68), optionally rewrites codec subsystem IDs
   and sends built-in verb tables, and finally leaves **CORBCTL=0x03, RIRBCTL=0x07 and
   GCTL.CRST=0** (controller back in reset) for hdaudbus to find.
3. On REMOVE it reverses a few BAR2 bits and unmaps.

The BAR2 sequence in (2a) is, register for register, the sequence Linux already carries
in `ca0132.c` as `ca0113_mmio_init_address_ae5/data_ae5` (the AE-5/AE-7 quirks), so
Linux's ca0132 codec driver already reproduces the filter's BAR2 work for those cards;
nothing in it is AE-9-specific except the BAR2+0x1c value (see §7).

No `XAE9` string and no `0x11020071` / `0x00711102` immediate exists in the binary.
Model-specific behaviour comes only from the registry (`CodecType_11020071 = "XAE9"`,
seeded by ctxhda.inf) and from IDs read at runtime.

---------------------------------------------------------------------------------------

## 1. Driver skeleton (verified)

| Item | Address | Notes |
|---|---|---|
| Entry (`GsDriverEntry`) | 0x1b008 (INIT) | cookie init, jumps to 0x1a008 |
| DriverEntry | 0x1a008 (PAGE) | all `MajorFunction[]` = 0x1a214 (pass-through); CREATE/CLOSE = 0x1a338; **IRP_MJ_INTERNAL_DEVICE_CONTROL (0x0f)** = 0x1a380; IRP_MJ_POWER (0x16) = 0x1a2f4; IRP_MJ_PNP (0x1b) = 0x1a258; `AddDevice` = 0x1a0a0; `DriverUnload` = 0x1a1d8 |
| AddDevice | 0x1a0a0 | creates control device `\Device\CTXHDB` + `\DosDevices\CTXHDB` once (global 0x181b8), overwrites the registry-root buffer at 0x17110 with `\Registry\Machine\System\CurrentControlSet\Control\CtxHdb\` (call 0x151ec), allocates a 0x50-byte extension (tag `Filt`), 0x118c4 = `IoCreateDevice`(ext 0x208, FILE_DEVICE_UNKNOWN) + `IoAttachDeviceToDeviceStack`(PDO), links ext into global list 0x181b0, **then calls 0x1164c (config-space work) at 0x1a1b9** |
| PnP dispatch | 0x11978 | minor 0 START = 0x11ab8 (forward synchronously with completion routine 0x120e4, wait, then call 0x11d28, then set state 1 and complete); minor 2 REMOVE = 0x11a1d (forward, then `Release` controller object = destructor chain, detach, delete); QUERY_REMOVE/CANCEL_REMOVE/STOP/QUERY_STOP/CANCEL_STOP/SURPRISE_REMOVAL only track a state variable and pass down |
| Power dispatch | 0x11bbc | only `IRP_MN_SET_POWER` (minor 2) is special: forwarded synchronously; if it succeeded and `Parameters.Power.Type == DevicePowerState(1)` and `State == PowerDeviceD0(1)`: call 0x1164c(PDO), 0x12d48(controller), then if BAR2 mapped: 0x14cb8(HdIs), 0x14818(HdIc), HdUa->vtbl[3] (=0x13e44). Uses `IofCallDriver`, not `PoCallDriver` |
| Internal IOCTL | 0x1a380 | on the control device only: 0x3F3E0004 → fills {5,1,0}; 0x3F3E0008/0x0C → success; **0x3F3E0044** → returns function pointer 0x1a454 which hands out the controller object (IID at 0x16180) to another kernel driver **(inferred: CtxHda.sys)**; **0x3F3E0048** → 0x1172c reads 0x100 bytes (DEV 0010) / 0x200 bytes (DEV 0012) of config space; **0x3F3E004C** → 0x117d4 writes one config byte (`offset=[buf+0]`, `value=[buf+4]`) |

Imports of note and where they are used: `MmMapIoSpace` only at 0x12700 (in 0x126a4);
`MmUnmapIoSpace` only at 0x1249d (destructor 0x123cc); `IoBuildSynchronousFsdRequest`
only at 0x11079 (0x11008 = IRP_MN_QUERY_INTERFACE for `GUID_BUS_INTERFACE_STANDARD`
{496B8280-6F25-11D0-BEAF-08002BE2092F}, GUID at 0x16160) — **there is no
IRP_MN_READ_CONFIG/WRITE_CONFIG; all config access is `GetBusData`/`SetBusData`**
(`[iface+0x38]` / `[iface+0x30]`, context `[iface+0x8]`, `PCI_WHICHSPACE_CONFIG`);
`KeStallExecutionProcessor` only at 0x12b75/0x12bb5 (reset polling);
`KeDelayExecutionThread` at 0x12b17 (ms sleep helper 0x12afc), 0x14043 (UART idle wait),
0x148e6/0x14982/0x14a7b (HdIc polling). No interrupt is ever connected and no DMA is set
up: verbs go exclusively through the immediate command interface.

Device extension (0x50 bytes): +0 filter DO, +8 PDO, +0x10 next-lower DO, +0x18 PnP
state, +0x1c previous state, +0x20 controller object, +0x28 bus number, +0x2c function,
+0x30 device (from `DevicePropertyAddress`), +0x34 VEN, +0x38 DEV, +0x3c SUBSYS, +0x40
REV (parsed from the HardwareID string by 0x1554c/0x15498; for the AE-9 SUBSYS parses as
0x00711102), +0x48 next extension.

Controller object (0xf8 bytes, tag `BCtl`, vtables 0x161e0/0x16238): +0x20 memory-resource
count, +0x24 IRQ count, +0x28.. {phys 8, len 4, pad 4}×N, +0x68 first interrupt
descriptor, **+0x78 and +0x80 = mapped[0] (HDA registers, BAR0)**, **+0x88 = mapped[1]
(BAR2)**, +0x90.. mapped VA array, +0xb0 ext, +0xb8 HdGp, +0xc0 HdUa, +0xc8 HdIs,
+0xd0 HdIc, +0xd8 bus/fn/dev, +0xe4 VEN, +0xe8 DEV, +0xec SUBSYS, +0xf0 REV.
The controller vtable exports BAR2 read8/16/32 (0x12a44/0x12a58/0x12a6c) and
write8/16/32 (0x12a80/0x12aa8/0x12ad4, each followed by a read-back), getters for the
two VAs (0x123a4/0x123b8) and a QueryInterface (0x124f0) for the four sub-objects.

---------------------------------------------------------------------------------------

## 2. (a) Ordered hardware operations at device start — as they apply to the AE-9

Assumptions taken from the operator's probe logs and INF: VEN/DEV 1102:0010, SUBSYS
string `SUBSYS_00711102`, two 16 KiB memory BARs, BAR2 readable (BAR2+0x04 =
0x7172000b ≠ 0), bridge = ASMedia 1b21:1080, registry as seeded by ctxhda.inf
(`CtxHdb\CodecConfig\CodecType_11020071 = "XAE9"`, nothing else). Branches not taken on
this card are listed in §3 so they can be re-checked.

Notation: `cfgW[off]=v` 16-bit config write; `B2[off]` 32-bit BAR2 access unless a width
is given; `B0[off]` BAR0 (HDA) access; every MMIO write in this driver is immediately
followed by a read of the same register (MSVC volatile + `lock or [rsp],0` fence), which
is omitted below.

### Phase A — AddDevice, before START_DEVICE (0x1a0a0 → 0x1164c)

| # | Operation | Where |
|---|---|---|
| A1 | Registry reads `CtxHdb\HDB\DoPCIeConfig`, `DoPCIConfig`, `DoIDTConfig` (REG_DWORD, default 1 if absent). Note: `DoPCIeConfig` and `DoPCIConfig` are read into the **same** stack slot, so only `DoPCIConfig` survives; PCIeConfig is attempted unconditionally. | 0x1165d–0x116c9 |
| A2 | `IRP_MN_QUERY_INTERFACE(GUID_BUS_INTERFACE_STANDARD, size 0x40, v1)` to the PCI PDO. | 0x11008 |
| A3 | `PCIeConfig` (0x11214): `GetBusData` 4 bytes at cfg 0x00; **requires 0x00121102 (DEV 0012)** — AE-9 is DEV 0010 → returns STATUS_UNSUCCESSFUL, **no write**. (For DEV 0012 it would write `cfgB[0x98]=0x10` then `PCIeConfig_Offset_n/Data_n` bytes.) | 0x1123c–0x11273 |
| A4 | `PCIConfig` (0x112dc), gated by DoPCIConfig: `GetBusData` cfg[0x00..3] must be **0x00101102** ✓ → **`SetBusData` 2 bytes: `cfgW[0x40] = 0x0080`**. Then for n = 0,1,… while both `CtxHdb\HDB\PCIConfig_Offset_n` and `PCIConfig_Data_n` exist: `cfgB[Offset_n] = Data_n & 0xff`. INF seeds none → only the 0x40 write. | 0x1131d–0x11343, loop 0x11355–0x11395 |
| A5 | `DoIDTConfig` (0x1146c), gated by DoIDTConfig: `ObReferenceObjectByName("\Driver\PCI")`, `IoEnumerateDeviceObjectList`, for each PDO with `DO_BUS_ENUMERATED_DEVICE` parse HardwareID; if VEN 0x10e3 & DEV 0x8111 (IDT/Tundra Tsi381-class bridge) → query bus interface on **that** PDO and 0x113a4: `cfgB[0x53]=0x00` on the bridge + `IDTConfig_Offset_n/Data_n` bytes. ASM1083 ≠ 10e3:8111 → **no write on the AE-9**. | 0x114f9–0x11621, 0x113e5–0x11409 |

Nothing else happens before START. No MMIO is touched in Phase A.

### Phase B — IRP_MN_START_DEVICE (0x11ab8 → 0x11d28 → 0x126a4)

The START IRP is first sent **down** to the PCI PDO and waited for; only if it succeeds
does the filter do its work, and only then is the IRP completed back to hdaudbus. So all
of Phase B runs after the PDO is started but before hdaudbus's own start code sees a
completed START.

| # | Operation | Where |
|---|---|---|
| B1 | Reads device properties (HardwareID → VEN/DEV/SUBSYS/REV, BusNumber, Address). | 0x11d5d–0x11f2c |
| B2 | Walks `Parameters.StartDevice.AllocatedResourcesTranslated`: every `CmResourceTypeMemory` (3) descriptor is copied ({Start, Length}), the first `CmResourceTypeInterrupt` (2) is saved. | 0x11f38–0x11fd4 |
| B3 | Allocates the controller object and calls 0x126a4: **for i in memory resources: `MmMapIoSpace(Start_i, Length_i, MmNonCached)`** (0x12700). mapped[0] → HDA register base (+0x78/+0x80), mapped[1] → BAR2 (+0x88). The mapping length is exactly the resource length (16 KiB each on this card); there is no fixed offset and no BAR read from config space — the physical addresses come from the translated resource list, and the driver **assumes list order == BAR order** **(inferred; nothing checks it)**. | 0x126da–0x1275e |
| B4 | Only if VEN==0x1102 && DEV∈{0x0010,0x0012} && BAR2 mapped: creates the four BAR2 sub-objects and initialises three of them (order: HdIs, HdGp, HdIc, HdUa). | 0x1277b–0x129ef |
| B4a | **HdIs init** (0x14cb8, "I2S" block): `B2[0x400] \|= 1`; `B2[0x42c] &= ~1`; `B2[0x46c] &= ~1`; `B2[0x4ac] &= ~1`; `B2[0x4ec] &= ~1`; `B2[0x43c] = 0`; `B2[0x47c] = 0`; `B2[0x4bc] = 0`; `B2[0x4fc] = 0`; `B2[0x408] \|= 1`; `B2[0x100] = (B2[0x100] & ~0x800) \| 0x600`; `B2[0x410] = (B2[0x410] & ~0x0b) \| 0x14`; `B2[0x40c] \|= 1`; `B2[0x100] \|= 0x0f`; `B2[0x100] \|= 0x100`. All 32-bit RMW, no delays. | 0x14cb8–0x14e97 |
| B4b | HdGp ("GPIO") object created; **no hardware access at creation**. | 0x1284b–0x128a2 |
| B4c | **HdIc init** (0x14818, the "CA0113 MMIO command"/I2C block): `B2[0x830] \|= 0x240`; then 0x149bc(mode 0): 0x14894(0): `B2[0x86c] &= ~1`, poll ≤10× (100 µs `KeDelayExecutionThread` each) until `B2[0x89c] bit0 == 0` (if it never clears: abort the rest of 0x149bc); `B2[0x800] = (B2[0x800] & ~4) \| 2`; 0x14894(1): `B2[0x86c] \|= 1`, poll ≤10×100 µs until `B2[0x89c] bit0 == 1`, then `B2[0x800] \|= 0x21`, `B2[0x804] &= ~0x1000`; finally `B2[0x20c] &= ~1`. | 0x14818–0x14889, 0x14894–0x149b5, 0x149bc–0x14a49 |
| B4d | **HdUa init** (0x13e44, 16550-type UART at BAR2+0xc00, 24 MHz clock, registers at stride 4): `B2[0x1c] \|= 0x80`; 0x14154(31250 baud): wait TX idle (poll `B2[0xc7c] bit0 == 0`, ≤1000 × 320 µs); `B2[0xc0c] \|= 0x80` (DLAB); `B2[0xc00] = 0x30` (divisor 24 000 000/(31250·16)=48); `B2[0xc04] = 0x00`; `B2[0xc0c] &= ~0x80`; then wait idle; `B2[0xc0c] \|= 3`; wait; `B2[0xc0c] &= ~4`; wait; `B2[0xc0c] &= ~8`; `B2[0xc08] \|= 1`; `B2[0xc08] \|= 0x30`; `B2[0xc08] &= ~0xc0`; `B2[0xc08] \|= 6`; `B2[0xc08] &= ~8`; `B2[0xc04] \|= 0x80`. (This is MIDI baud; the UART object is otherwise unused by this driver.) | 0x13e44–0x13fd6, 0x13fe8–0x1406a, 0x14154–0x14224 |
| B5 | **CodecConfig** 0x12d48 — see the step list below. | 0x12d48–0x13320 |
| B6 | START IRP completed with the PDO's status. | 0x11b67–0x11b76 |

#### B5 / C2: `CodecConfig` (0x12d48), exact sequence

| # | Operation | Where |
|---|---|---|
| C1 | `B2[0x1c] = B2[0x1c] & 0xFF00FFFF`; then `B2[0x1c] \|= 0x00880000` (two 32-bit RMWs; on this card 0x00880480 → unchanged). | 0x12d7e–0x12dc6 |
| C2 | `esi = B2[0x04]` (32-bit read; 0x7172000b on the AE-9). | 0x12dd4 |
| C3 | Only if `B2[0x04]==0 && SUBSYS==0x00101102` (reference board, **not AE-9**): `B2[0x1c] = CodecConfig\CodecId_2 ? 0x00880480 : 0x00880680` (absolute write). | 0x12de4–0x12e3c |
| C4 | **Controller reset** 0x12b28: read `B0[0x08]` GCTL; if CRST already 1 → skip. Else `GCTL &= ~1`; poll ≤300 × `KeStallExecutionProcessor(1 µs)` until CRST reads 0; `GCTL \|= 1`; poll ≤300 × 1 µs until CRST reads 1 (timeout → STATUS_IO_TIMEOUT, **ignored by the caller**); `KeDelayExecutionThread(10 ms)`. | 0x12b28–0x12be4 |
| C5 | `mask = B0[0x0e]` (STATESTS, 16-bit) `& 0x7fff`. | 0x12e47–0x12e66 |
| C6 | Byte writes: `B0[0x4c] (CORBCTL) = 0x00`; `B0[0x5c] (RIRBCTL) = 0x00`; `B0[0x68] (IRS) = 0x05`; `B0[0x68] = 0x00`; `B0[0x68] = 0x04`. (IRS bit 2 is reserved in HDA 1.0a; some Intel PCH datasheets call it ICVER — meaning on the CTHDA unknown.) | 0x12e6e–0x12eb5 |
| C7 | Registry: `CtxHdb\CodecInfo\Codec1 = 0`, `Codec2 = 0` (REG_DWORD, via 0x15420 = `SetValueKey`). | 0x12eba–0x12f0b |
| C8 | If mask bit1 (codec @SDI1): verb **0x100F0000** (cad1, NID0, F00 param 0 = vendor ID) → `V1`; verb **0x101F2000** (F20 = subsystem ID) → `S1`; `CodecInfo\Codec1 = S1`. If `S1 == 0x11020010 \|\| S1 == 0x10EC0899`: read `CodecConfig\CodecId_1`; if ≠0 send **0x10172000\|b0, 0x10172100\|b1, 0x10172200\|b2, 0x10172300\|b3** (Set Subsystem ID bytes 0–3 of NID 1) and re-read F20. | 0x12f10–0x13073 |
| C9 | If mask bit2 (codec @SDI2): same with cad 2: **0x200F0000** → `V2`, **0x201F2000** → `S2`, `CodecInfo\Codec2 = S2`; if `S2 == 0x11020010` and `CodecId_2 ≠ 0`: **0x20172000..0x20172300** + re-read. | 0x13078–0x131d0 |
| C10 | `need = (B2[4]==0 && SUBSYS==0x00101102) \|\| (S2 == 0x1102003F && CodecConfig\F_InitCodec ≠ 0) \|\| V1 ∈ {0x11020013, 0x11020015} \|\| V2 ∈ {0x11020013, 0x11020015}`. | 0x131dd–0x13265 |
| C11 | If `need`: `KeDelayExecutionThread(200 ms)`; if `(S1 & 0xFFFF0000) == 0x11020000` → **0x13328(S1, V1∈{13,15})** (§2.1); if `(S2 & 0xFFFF0000) == 0x11020000` → **0x13570(S2, V2∈{13,15})** (§2.2). | 0x13267–0x132d5 |
| C12 | Always: `B0[0x4c] (CORBCTL) = 0x03`; `B0[0x5c] (RIRBCTL) = 0x07`; **`B0[0x08] GCTL &= ~1` (controller put back into reset)**. | 0x132da–0x1330b |

Each verb (0x12bec) is: wait ≤10 ms (1 ms sleeps) for `IRS.ICB==0`; `IRS = (IRS & ~1) | 2`
(16-bit RMW, clears IRV); `ICW (B0[0x60]) = verb` (32-bit); `IRS |= 1`; then wait ≤10 ms
for `IRS.IRV`, read `IRR (B0[0x64])` (0xFFFFFFFF on timeout); sleep 1 ms. So every verb
costs ≥1–2 ms; the codec-ID reads alone cost ~10 ms.

#### 2.1 `0x13328` — codec-1 init (subsys `S1`, flag `creative`)

1. Table base = `creative ? 0x17ef0 : 0x17c30` (four 0xb0-byte entries: UTF-16 name,
   +0xa0 pointer to verb-group array, +0xa8 group count).
2. Reads `CtxHdb\CodecConfig\CodecType_%08X` (S1) as a ≤0xa0-byte string
   (**INF: `CodecType_11020071 = "XAE9"`**) and compares it against the entry names
   **"Recon3D", "Zx", "ZxR", "SBAFX"**. `"XAE9"` matches none → the built-in tables
   are **not** sent on the AE-9 (nor on AE-5/AE-7/AE-9PE; those names are not in this
   binary either).
3. If `creative`: **0x13644(cad=1)** (§2.3).
4. Reads `CodecConfig\InitVerbs_%08X` (string = sub-key name); if present, sends
   `CodecConfig\<name>\Verb_0, Verb_1, …` (REG_DWORD, raw 32-bit verbs, no rewriting of
   cad) until the first missing value. INF seeds none.

#### 2.2 `0x13570` — codec-2 init (subsys `S2`, flag `creative`)

Only if **`S2 == 0x1102003F`** (ZxR-style second codec): sends the group array at
`creative ? 0x17b90 (7 groups) : 0x17b50 (4 groups)` with each verb forced to cad 2
(`(v & 0x0FFFFFFF) | 0x20000000`), 2 ms sleep after every group. Then if `creative`:
**0x13644(cad=2)**. For an AE-9 second function (S2 presumably 0x11020072) nothing but
the optional 0x13644 happens.

#### 2.3 `0x13644` — Creative codec "8051/PLL-PMU" bring-up (cad)

Up to 10 iterations: send `(cad<<28)|0x01570D09` (NID 0x15 = ca0132 `WIDGET_CHIP_CTRL`,
verb 0x70D = `VENDOR_CHIPIO_8051_ADDRESS_LOW`, data 0x09); send
`(cad<<28)|0x015F0C00` (verb 0xF0C = `VENDOR_CHIPIO_PLL_PMU_READ`); if the response is
not 0xFFFFFFFF and bit0 is set → done. Otherwise send group 0 (14 verbs @0x17918) and
group 1 (2 verbs @0x17950) with the cad patched in, 2 ms sleep after each group, and
retry. In ca0132.c terms group 0 is
`chipio_8051_write_pll_pmu(0x09,0x43); (0x17,0x00); (0x0a,0x98); (0x0b,0xff);
(0x0c,0x07); (0x09,0x13); (0x17,0x80)` and group 1 is `(0x09,0x23)`; the loop exits
when PLL/PMU register 0x09 reads back with bit 0 set.

### Phase C — IRP_MN_SET_POWER (DevicePowerState, D0) (0x11bbc)

Forward the IRP synchronously; on success **repeat Phase A steps A1–A5** (0x1164c on the
PDO, so `cfgW[0x40]=0x0080` is written a second time), **repeat CodecConfig C1–C12**,
then (if BAR2) **repeat B4a HdIs init, B4c HdIc init, B4d HdUa init** — note the order
differs from START: codec pass first, BAR2 block second. Windows sends D0 during the
normal start sequence, so on a cold boot everything in Phases A–C is normally executed
twice (once at START, once at D0) **(inferred from normal WDM power-up ordering)**.

### Phase D — IRP_MN_REMOVE_DEVICE (0x11a1d → controller `Release` → 0x123cc)

Destructors, in order: HdGp 0x137a4 — 8-bit RMW clears bits 0..7 of `B2[0x308]` and
`B2[0x309]` (16 byte writes); HdIs 0x14b80 — `B2[0x43c] |= 0x33`, `B2[0x47c] |= 0x33`,
`B2[0x4bc] |= 0x33`, `B2[0x4fc] |= 0x33`; HdIc 0x143ec → 0x14ad8 — only if a command
session was left open: `B2[0x20c] &= ~1`, `B2[0x210] = 0`; HdUa 0x13de0 —
`B2[0xc04] &= 0xFFFFFF78`; then `MmUnmapIoSpace` of every mapping.

---------------------------------------------------------------------------------------

## 3. Branches that exist but are not taken on the AE-9 (for re-checking)

* DEV 0012 controller (`PCIeConfig`): `cfgB[0x98] = 0x10` + `PCIeConfig_*` pairs; also
  config-space reads via IOCTL are 0x200 bytes instead of 0x100.
* SUBSYS 0x00101102 + `B2[0x04]==0` (bare reference board): absolute `B2[0x1c]` write
  (0x00880680 / 0x00880480) and forced codec init.
* Codec subsystem 0x11020010 (unprogrammed Creative codec) or 0x10EC0899 (Realtek
  ALC899, "SBAFX" = Audigy FX): subsystem-ID programming from `CodecId_1/2`.
* Codec vendor IDs 0x11020013 / 0x11020015 → forced 200 ms + 0x13644 bring-up (and
  tables if the CodecType name matches). The INF matches HDAUDIO DEV_0011/0013/0015 for
  the AE-9, so **whether this branch runs on the operator's card depends on the F00
  response of the codec** — see open questions.
* Second codec subsystem 0x1102003F (ZxR DBpro) → `F_InitCodec` and the 207-verb table.
* IDT 10e3:8111 bridge present → `cfgB[0x53]=0` on the bridge.

---------------------------------------------------------------------------------------

## 4. (b) Built-in tables in `.data`

Verb encoding: bits 31:28 codec address (patched by the sender: 0x13328 forces 1,
0x13570 forces 2, 0x13644 uses its argument), 27:20 NID, 19:8 verb ID, 7:0 payload
(for the 4-bit verbs 0x2/0x5 the payload is bits 15:0). NID 0x15 verbs 0x70x/0xF0x are
the ca0132 "chip I/O" vendor verbs (0x70A = CT_EXTENSIONS_ENABLE, 0x70C = PLL_PMU_WRITE,
0x70D = 8051_ADDRESS_LOW, 0x70E = 8051_ADDRESS_HIGH, 0xF0C = PLL_PMU_READ, 0x500 =
FLAG_CONTROL). Group pointers are 0x10-byte {verb-array ptr, count} records.

Shared verb arrays:

| Addr | n | Verbs (raw) |
|---|---|---|
| 0x17310 | 18 | `1027a1e4 1027a205 1027a30a 1037a1e4 1037a205 1037a30a 1057a1e0 1057a201 1057a30a 1077a1e4 1077a201 1077a30a 1097a1f0 1097a201 1097a30a 10c78c02 10e78b04 11570a00` |
| 0x17358 | 2 | `11570d09 11570c23` (8051 addr 0x09, PLL/PMU write 0x23) |
| 0x17360 | 34 | `1027a1e4 1027a205 1027a30a 1037a1e4 1037a205 1037a30a 1057a1e0 1057a201 1057a30a 1077a1e4 1077a201 1077a30a 1097a1f0 1097a201 1097a30a 10c78c02 10e78b04 11570d40 11570c02 11570d41 11570c02 11570d45 11570c02 11550390 11552393 10621831 10673e80 11570d43 11570c06 10679300 11552390 10670d01 11552290 11570a00` |
| 0x173e8 | 1 | `11570a01` (CT extensions enable = 1) |
| 0x173f0 | 17 | `1027a1e4 1027a205 1027a30a 1067a1e0 1067a205 1067a30a 1077a1e4 1077a201 1077a30a 10621831 10673e80 11570d44 11570c06 10679300 11552390 10670d01 11570a00` |
| 0x17440 | 72 | `10172041 10172100 10172202 10172311` (Set Subsystem ID = 0x11020041) `1017ff00 ×4` (function reset ×4) then pin-config verbs 0x71c–0x71f for NIDs 0x11–0x1f: `11171c00 11171d00 11171e13 11171f40 11271cf0 11271d11 11271e11 11271f41 11471c10 11471d40 11471e01 11471f01 11571c12 11571d10 11571e01 11571f01 11671c11 11671d60 11671e01 11671f01 11771c14 11771d20 11771e01 11771f01 11871c40 11871d90 11871ea1 11871f01 11971c50 11971d90 11971ea1 11971f02 11a71c60 11a71d30 11a71e81 11a71f01 11b71c20 11b71d40 11b71e21 11b71f02 11c71cf0 11c71d11 11c71e11 11c71f41 11d71c01 11d71de6 11d71e25 11d71f40 11e71c30 11e71d11 11e71e44 11e71f41 11f71c70 11f71d61 11f71ec4 11f71f41` then Realtek coef writes `12050007 12040180 12050001 1204c7aa 12050015 1204026a 12050008 12040071` — Realtek ALC899 ("SBAFX") table |
| 0x17560 | 27 | cad-2 8051 sequence: `21570df0 21570efe 21570775 215707d3 21570709 21570753 215707d4 215707ef 21570775 215707d3 21570709 21570702 21570737 21570778 21553cce 215575c9 21553dce 2155b7c9 21570de8 21570efe 21570702 21570768 21570762 21553ace 215546c9 21553bce 2155e8c9` |
| 0x175d0 | 207 | cad-2 (ZxR DBpro) full init, see `tables.py` output in the scratchpad; starts `21570f02 21570d49 21570c88 21570d20 21570e19 21570700 21571a05 21571b29 …`, ends with pin configs `20b71f41 20c71cf0 20c71d21 20c71e45 20c71f01 20d71f41 20e71cf0 20e71d11 20e71ec5 20e71f01 20f71f41 21071f42 21171cf0 21171d71 21171e94 21171f01 21271f41 21371f50 21871f50` |
| 0x17910 | 2 | `11570d09 11570c63` |
| 0x17918 | 14 | `11570d09 11570c43 11570d17 11570c00 11570d0a 11570c98 11570d0b 11570cff 11570d0c 11570c07 11570d09 11570c13 11570d17 11570c80` |
| 0x17950 | 2 | `11570d09 11570c23` |
| 0x17958 | 6 | `11570d00 11570cfe 11570d4b 11570c00 11570d54 11570c00` |

Codec-1 tables (0xb0-byte entries; name → groups):

| Table | Entry | Name | Groups (addr n) |
|---|---|---|---|
| A (non-Creative vendor, base 0x17c30) | 0x17c30 | Recon3D | 0x17358 ×2, 0x173e8 ×1, 0x17310 ×18 |
| | 0x17ce0 | Zx | 0x17358, 0x173e8, 0x17360 ×34 |
| | 0x17d90 | ZxR | 0x17358, 0x173e8, 0x173f0 ×17 |
| | 0x17e40 | SBAFX | 0x17440 ×72 |
| B (vendor 0x11020013/15, base 0x17ef0) | 0x17ef0 | Recon3D | 0x17910, 0x173e8, 0x17560 ×27, 0x17918 ×14, 0x17950, 0x17958 ×6, 0x17310 ×18 |
| | 0x17fa0 | Zx | 0x17910, 0x173e8, 0x17560, 0x17918, 0x17950, 0x17958, 0x17360 ×34 |
| | 0x18050 | ZxR | 0x17910, 0x173e8, 0x17560, 0x17918, 0x17950, 0x17958, 0x173f0 ×17 |
| | 0x18100 | SBAFX | 0x17440 ×72 |

Codec-2 tables (0x13570): non-Creative @0x17b50 = {0x17358, 0x173e8, 0x17560, 0x175d0 ×207};
Creative @0x17b90 = {0x17910, 0x173e8, 0x17560, 0x17918, 0x17950, 0x17958, 0x175d0 ×207}.
Retry table for 0x13644 @0x17c00 = {0x17918 ×14, 0x17950 ×2}.

Vendor-independent constants: `.rdata` GUIDs 0x16160 (`GUID_BUS_INTERFACE_STANDARD`),
0x16170 (`IID_IUnknown`), 0x16180 {0C82ABA4-A410-4500-A4E1-E96C4F5A937F} (controller
interface handed out by IOCTL 0x3F3E0044), 0x16190 {C60D74EB-…}, 0x161a0 {A42A1869-…}
(accepted by HdGp), 0x161b0 {E18C8C99-…} (HdUa), 0x161c0 {01E63F10-…} (HdIc),
0x161d0 {F326ED5A-…} (HdIs). Strings: registry names at 0x15c50–0x15ee0, the CtxHda→
CtxHdb root path buffer at 0x17110, device names at 0x1a4b0/0x1a4d0.

There are **no** config-space offset/data tables in `.rdata`/`.data`; the only built-in
config-space constants are the three immediates 0x40←0x0080 (DEV 0010), 0x98←0x10
(DEV 0012) and 0x53←0x00 (IDT bridge). Everything else is registry driven under
`HKLM\SYSTEM\CurrentControlSet\Control\CtxHdb\HDB\` (`DoPCIeConfig`, `DoPCIConfig`,
`DoIDTConfig`, `PCIeConfig_Offset_n/_Data_n`, `PCIConfig_Offset_n/_Data_n`,
`IDTConfig_Offset_n/_Data_n`), `…\CtxHdb\CodecConfig\` (`CodecId_1`, `CodecId_2`,
`F_InitCodec`, `CodecType_%08X`, `InitVerbs_%08X`, `<InitVerbs>\Verb_n`) and
`…\CtxHdb\CodecInfo\` (`Codec1`, `Codec2`, written by the driver).

---------------------------------------------------------------------------------------

## 5. BAR2 register map as used by this driver (all offsets relative to BAR2)

| Offset | Access | Used by | Meaning (from usage; names are the driver's tags) |
|---|---|---|---|
| 0x04 | r32 | 0x12d48 | identity/status word (0x7172000b on AE-9); ==0 selects "unprogrammed board" path |
| 0x1c | rmw32 | 0x12d48, HdUa | bits 23:16 ← 0x88; bit 7 set by UART init; Linux ca0132 writes 0x00880680 (AE-5/AE-7/R3D), 0x00880480 (ZxR); AE-9 currently reads 0x00880480 |
| 0x100 | rmw32 / byte bits | HdIs, HdGp | I2S control: init sets bits 10:9, clears 11, sets 3:0 and 8; HdIs methods: bits 7:4 per-channel, 15:12 per-channel, bit 28 = 24-bit, bits 31:29 = rate code (48k=0,44.1k=1,96k=2,88.2k=3,192k=4); HdGp reads/sets bits 4..7 of byte 0x100 |
| 0x104 | rmw32 | HdIs methods | per-channel nibbles/bytes |
| 0x200/0x204/0x208/0x20c/0x210 | w32 | HdIc | MMIO-command block: 0x210 ← 0x7e, 0x5a unlock (reads back 0xaa), 0x804 = group/address, 0x204 = value, 0x208 = 0xffff, 0x20c bit0/bits2:1/bit23 status — identical to Linux `ca0113_mmio_command_set()` |
| 0x300, 0x304, 0x308, 0x309, 0x30c, 0x310+2n, 0x320 | byte/word | HdGp | GPIO: bit set/clear on 0x300/0x304/0x308/0x309, 0x30c ← 1<<n, 16-bit at 0x310+2n, 16-bit 0x320 (Linux `ca0113_mmio_gpio_set` writes 0x320) |
| 0x400, 0x408, 0x40c, 0x410, 0x42c/0x46c/0x4ac/0x4ec, 0x43c/0x47c/0x4bc/0x4fc, 0x454+0x40n | rmw32 | HdIs | I2S enable/config, four channel blocks at 0x40 stride |
| 0x800, 0x804, 0x830, 0x840, 0x854, 0x860, 0x86c, 0x89c | rmw32 | HdIc | command engine control/status |
| 0xc00–0xc14, 0xc7c | rmw32 | HdUa | 16550 UART (THR/RBR/DLL, IER/DLM, IIR/FCR, LCR, MCR, LSR) + status at 0xc7c |

Comparison with Linux (`reference/kernel-7.0/ca0132.c`): `ca0113_mmio_init_address_ae5`
= 0x400,0x42c,0x46c,0x4ac,0x4ec,0x43c,0x47c,0x4bc,0x4fc,0x408,0x100,0x410,0x40c,0x100,
0x100,0x830,0x86c,0x800,0x86c,0x800,0x804,0x20c,0x01c,0xc0c,0xc00,0xc04,0xc0c×4,
0xc08×5,0xc04,0x01c with data 1,0,0,0,0,0,0,0,0,1,0x600,0x14,1,0x60f,0x70f,0xaff,0,
0x6b,1,0x6b,0x57,0x800000,0x00880680,0x80,0x30,0,0,3,3,3,1,0xf1,1,0xc7,0xc1,0x80,
0x00880680 — this is B4a + B4c + (BAR2+0x1c) + B4d of this filter written as absolute
values (a trace of it). So the BAR2 half of the filter is **already in Linux** for the
AE-5/AE-7 quirks; an AE-9 quirk can reuse `ca0132_mmio_init_ae5()` (with the 0x1c value
questioned in §7). What Linux does **not** do is the controller-side pass C4–C12, which
is hdaudbus/snd_hda_intel territory.

---------------------------------------------------------------------------------------

## 6. (c) Function index

| Address | Role |
|---|---|
| 0x11008 | QueryInterface(BUS_INTERFACE_STANDARD) on a PDO, returns 0x40-byte iface (tag `Adpt`) |
| 0x11124 | read `<prefix>_Offset_n` / `<prefix>_Data_n` from `CtxHdb\HDB` |
| 0x11214 / 0x112dc / 0x113a4 | PCIeConfig (DEV 0012) / PCIConfig (DEV 0010, cfg 0x40=0x0080) / IDTConfig (10e3:8111, cfg 0x53=0) |
| 0x1146c | DoIDTConfig: enumerate `\Driver\PCI` devices, apply 0x113a4 to matching bridges |
| 0x1164c | config-space driver: reads Do* flags, runs 0x11214/0x112dc/0x1146c |
| 0x1172c / 0x117d4 | IOCTL helpers: read whole config space / write one config byte |
| 0x118c4 | create+attach filter device |
| 0x11978 / 0x11bbc / 0x11b9c | PnP dispatch / power dispatch / generic pass-down |
| 0x11d28 | START work: properties, resources, controller object, calls 0x126a4 |
| 0x120e4 | completion routine (sets event) |
| 0x12110 / 0x121e8 / 0x1232c | PCI driver device-list enumerate / HardwareID parse / release |
| 0x123cc | controller destructor (sub-object release, `MmUnmapIoSpace`) |
| 0x124f0 | controller QueryInterface for sub-objects |
| 0x126a4 | **controller init: `MmMapIoSpace` loop, sub-object creation, calls 0x12d48** |
| 0x12a44…0x12ad4 | BAR2 read/write 8/16/32 accessors |
| 0x12afc | sleep(ms) via `KeDelayExecutionThread` |
| 0x12b28 | **GCTL.CRST reset cycle** (1 µs stalls, 10 ms sleep) |
| 0x12bec | **send verb via ICW/IRR/IRS** |
| 0x12d48 | **CodecConfig** (C1–C12) |
| 0x13328 / 0x13570 / 0x13644 | codec-1 table init / codec-2 table init / Creative 8051-PLL bring-up |
| 0x13768/0x137a4, 0x13800, 0x1380c | HdGp deleting dtor / dtor, AddRef stub, Release stub |
| 0x13888–0x13d6c | HdGp GPIO methods |
| 0x13da4/0x13de0, 0x13e44, 0x13fe8, 0x14154, 0x1422c–0x14320 | HdUa dtor, **init**, TX-idle wait, set baud, RBR/THR/IER methods |
| 0x143b0/0x143ec, 0x144a0, 0x14554, 0x14580, 0x14590, 0x146c8, 0x147a0, 0x147d0, 0x14818, 0x14894, 0x149bc, 0x14a50, 0x14ad8 | HdIc dtor, lock, unlock, set address, write, write+subaddr, set mode, set flag, **init**, engine on/off+poll, mode select, completion poll, session close |
| 0x14b44/0x14b80, 0x14cb8, 0x14ea0–0x15140 | HdIs dtor, **init**, channel/rate/width methods |
| 0x151ec / 0x15218 / 0x152e8 / 0x15420 | registry root rewrite / `PcNewRegistryKey` open / QueryValue / SetValue(REG_DWORD) |
| 0x15498 / 0x1554c | hex parse / `VEN_`/`DEV_`/`SUBSYS_`/`REV_` extractor |
| 0x1a008 / 0x1a0a0 / 0x1a1d8 / 0x1a214 / 0x1a258 / 0x1a2f4 / 0x1a338 / 0x1a380 / 0x1a454 | DriverEntry / AddDevice / Unload / pass-through / PnP entry / Power entry / Create-Close / Internal IOCTL / controller-interface getter |

---------------------------------------------------------------------------------------

## 7. (5) Per-model branching — answer

There is **no branch keyed on subsystem 0x11020071 or on the string "XAE9"** inside the
binary. Model selection is entirely: (1) controller DEV (0010 vs 0012) for the single
config-space constant; (2) the presence of an IDT 10e3:8111 bridge; (3) SUBSYS ==
0x00101102 for the reference-board path; (4) codec vendor IDs (0x11020013/0x11020015
→ Creative bring-up), codec subsystem IDs (0x11020010, 0x10EC0899, 0x1102003F) and the
registry string `CodecType_<codec subsystem>` which the INF sets to "XAE9" but which
this driver only understands as "Recon3D"/"Zx"/"ZxR"/"SBAFX". The "XAE9" name is
therefore consumed by some other component **(inferred: CtxHda.sys reads the same key)**
and has no effect in CtxHdb.sys.

---------------------------------------------------------------------------------------

## 8. What this means for the Linux hang (assessment, not verified on hardware)

Before hdaudbus ever touches BAR0, Windows has done, in this order: `cfgW[0x40]=0x0080`;
BAR2 I2S block init (B4a); BAR2 command-engine init (B4c, with polls that may time out
harmlessly); BAR2 UART init (B4d); `B2[0x1c]` bits 23:16 = 0x88; a CRST 0→1 cycle with a
10 ms settle; CORB/RIRB stopped; a handful of immediate-command verbs; then CRST←0 with
CORBCTL=3/RIRBCTL=7 left set. Candidates that the bare Linux probe does not do and that
precede the reset are only the config write to 0x40 and the BAR2 block; the reset cycle
itself is textbook. If snd_hda_intel hangs *after* CRST←1, the two cheapest experiments
are (1) `pci_write_config_word(pdev, 0x40, 0x0080)` before `azx_reset`, and (2) the
`ca0113_mmio_init_ae5` sequence on BAR2 before the reset (both are what Windows does).
Note also that `AZX_DCAPS_PRESET_CTHDA` in `intel.c` already disables MSI and 64-bit
DMA for this device, and that the filter never enables interrupts or DMA, so a hang in
those paths would be outside what Windows exercises at this stage.

---------------------------------------------------------------------------------------

## 9. (d) Open questions

1. **What does config offset 0x40 = 0x0080 do on the CTHDA?** It is vendor-specific
   space (0x40 lies past the standard header; the capability list position is not known
   from this binary). Windows writes it twice (AddDevice, D0). Read it in the probe
   before/after; try the write before reset.
2. **Codec vendor ID on the operator's AE-9 (0x11020011 vs 0x11020013/0x11020015).**
   The INF matches all three; the filter's forced 200 ms + 8051/PLL-PMU bring-up
   (0x13644) runs only for 0x13/0x15. `codec-dump-stock.txt` is empty; a single
   immediate-command F00/F20 read from the probe module answers this and also gives S1/S2.
3. **Semantics of BAR2+0x04 (0x7172000b), BAR2+0x1c bits and the 0x400/0x800/0xc00
   blocks** are only known by usage (I2S / command engine / UART). Whether the HDA link
   depends on any of them (e.g. a codec clock enable in 0x1c bits 23:16) cannot be
   decided statically.
4. **IRS byte writes 0x05/0x00/0x04** (bit 2 is reserved in the HDA spec) — unknown
   meaning on this controller; harmless to replicate.
5. **Runtime BAR2 use by CtxHda.sys.** This filter exports the controller object, GPIO,
   I2S, command-engine and UART interfaces via internal IOCTL 0x3F3E0044 and also lets
   another kernel driver read/write config space (0x3F3E0048/4C). The ES9038PRO DAC and
   ACM handling therefore live in CtxHda.sys and go through these BAR2 blocks (the same
   mechanism Linux ca0132 uses as `ca0113_mmio_command_set`/`gpio_set`); that driver has
   not been analysed here.
6. **Resource-order assumption** (mapped[0]=BAR0, mapped[1]=BAR2) is unchecked in the
   binary; harmless for Linux, but it is why BAR2 must be present for any of B4/B5 to run.
7. `0x124f0` maps IID 0x161a0 to `obj+0xb0` (the device-extension pointer) while HdGp's
   own QueryInterface accepts 0x161a0 — looks like an off-by-one-slot bug in the vendor
   code; irrelevant to hardware init, noted for completeness.
