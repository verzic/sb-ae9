# CtxHda.sys 6.0.105.0065 — Audio Control Module (ACM) power-on, detection and link protocol

Static analysis of `reference/windows-driver-6.0.105.0065/AMD64/CtxHda.sys` (1.2 MB, x86-64, no PDB)
cross-checked against `CtxHdb.sys` from the same package. Nothing was executed. All addresses are
image VAs (ImageBase 0x10000). Sections: `.text` 0x11000, `.rdata` 0x46000 (file +0x10c00),
`.data` 0x117000 (file +0x11200), `PAGE` 0x12d000.

Legend: **[V]** verified by reading the instructions; **[I]** inferred from naming, defaults or
structure; **[?]** open.


---------------------------------------------------------------------------------------

## 0. Executive summary

* **The ACM is powered by ca0113 GPIO pin 5** (16-bit write `0x0105` to BAR2+0x320 = on,
  `0x0005` = off), driven through CtxHdb's HdGp interface. This is the only "12 V" control the
  driver has; the `Acm12VLow*` / `AcmEnable12VLowWait` parameters govern how long the pin is held
  low between power-cycles. **[V]** for the pin/register, **[I]** that pin 5 is the 12 V rail.
* **The ACM link is the 16550 UART at BAR2+0xc00, re-programmed to 115200 baud**
  (divisor 13 with the 24 MHz clock). The 31250-baud programming done by CtxHdb at start is the
  MIDI default; CtxHda's ACM object calls "set baud(115200)" before the first probe. **[V]**
* **The protocol is SysEx-framed: `F0 <cmd> <len> <payload[len]> F7`.** Bit 7 of `<cmd>` set means
  "query"; the reply carries `<cmd>&0x7f`. Detection is a poll of `F0 81 00 F7` every
  `AcmPollIntervalMs` (500 ms); the reply's byte 3 is the present flag. **[V]** framing and bytes,
  **[I]** the cmd/len reading of the header (consistent across all 22 packets).
* **48 V phantom power is a UART command, not a GPIO:** `F0 05 03 02 01 <0|1> F7`, restored from
  the `Acm48V` registry value at init; readback with `F0 85 01 02 F7`. **[V]**
* **AE-9-specific:** subsystem 0x11020071 selects board class vtable 0x113db0 (ctor 0x34814) which
  sets `board+0x120 = 1` ("has ACM"); the ACM object exists only for that class. SUBSYS 0x11020072 is
  not referenced anywhere in CtxHda. **[V]**

---------------------------------------------------------------------------------------

## 1. Method

* `objdump -d -M intel` (PE via binutils), `.pdata` for function bounds, `strings -el -t x` for
  UTF-16 names, a Python helper (`xr.py` / `q.py`) resolving RIP-relative xrefs, IAT names, call
  graph and vtables (runs of function pointers in `.rdata`).
* Class identification is by vtable; the compiler laid vtables out contiguously so some "runs"
  span two classes (noted where relevant).
* Registry parameters were followed from the `Acm*` strings to the reader at 0x41034, then to the
  object fields, then to every consumer of those fields.
* The CtxHdb side was re-disassembled (`hdb/`) to resolve exactly which BAR2 registers each
  interface slot touches, rather than relying on the earlier report's IID table (see §2.2 for a
  correction to that report's note 7).

---------------------------------------------------------------------------------------

## 2. Object model (who talks to what)

### 2.1 Adapter → CtxHdb interfaces (function 0x11508) **[V]**

CtxHda never touches BAR2 itself. At adapter start it:

1. 0x2c17c: `IoGetDeviceObjectPointer(L"\Device\CTXHDB")`, internal IOCTL 0x3F3E0004 (version,
   12 bytes, must be ≥ {expected}), 0x3F3E0008, then 0x3F3E0044 which returns a **function pointer**
   (CtxHdb 0x1a454).
2. Calls that pointer with `(IID 0x112260 = {0C82ABA4-A410-4500-A4E1-E96C4F5A937F}, L"" , &root)` →
   the CtxHdb **controller object** (`adapter+0x370`).
3. `root->QueryInterface(IID, &out)` five times (CtxHdb 0x124f0, whose `this` is the controller's
   second vtable at object+8, which is why the analysis of CtxHdb read the field offsets one slot
   low):

| CtxHda IID @ | GUID | CtxHdb field | Object | stored at |
|---|---|---|---|---|
| 0x112230 | E18C8C99-3C9D-496F-B619-7E9F0523C4B9 | ctrl+0xc0 | **HdUa — UART** (vtable 0x16320) | `adapter+0x378` |
| 0x112220 | A42A1869-C28F-41B1-A291-74AA02B9C01F | ctrl+0xb8 | **HdGp — GPIO** (vtable 0x16260) | `adapter+0x380` |
| 0x112210 | C60D74EB-D7B8-45B0-8FD1-DED2F8877DA4 | controller itself | BAR2 read/write accessors | `adapter+0x388` |
| 0x112240 | 01E63F10-1988-4E3C-B8D0-68B908677931 | ctrl+0xd0 | HdIc — command engine (0x800) | `adapter+0x390` |
| 0x112250 | F326ED5A-1337-4332-951E-75028DA6D134 | ctrl+0xc8 | HdIs — I2S (0x400) | `adapter+0x398` |

Immediately after getting the UART, if registry `Uart` (0x44880 key / `PollInterval` value) is set
(`adapter+0xd8`), CtxHda calls HdUa slot +0x18 = CtxHdb 0x13e44 (the 31250-baud init) and creates a
0x150-byte `DbMg` MIDI object. This is **not** the ACM path.

### 2.2 Board object (AE-9 class) **[V]**

Factory 0x3053c (called from 0x11930 at 0x11c07) switches on VEN/DEV and SUBSYS:

| SUBSYS | ctor | vtable |
|---|---|---|
| 0x11020051 / 0x11020061 | … | 0x114180 (xref 0x3059e) |
| **0x11020071** | **0x34814** (`board+0x120 = 1`, `+0x148 = 1`) | **0x113db0** |
| 0x11020073 | 0x34814 with different args | 0x113db0 |
| 0x11020081 | 0x3d43c | 0x114930 |
| 0x11020191 / 0x10251374 | 0x3a094 | 0x114560 |

Board Init = vtable slot +0x60 = 0x34afc, called at 0x11c5f with
`(this, HAL=adapter+0x10, HdIc, controller, HdGp, HdUa, HdIs, misc)`. Field map used below:

| board field | content |
|---|---|
| +0x18 | HAL object (vtable 0x112680, ctor 0x1c1c8) — codec verbs/DSP |
| +0x20 | HdIc (command engine) |
| +0x28 | controller (BAR2 accessors) |
| **+0x30** | **HdGp (GPIO)** |
| **+0x38** | **HdUa (UART)** |
| +0x40 | HdIs |
| +0x58 | object with vtable 0x113c50 (DAC/amp control, role not analysed) |
| **+0x70** | **ACM object** (only if `+0x120 != 0`) |
| +0x120 | "has ACM" (=1 for SUBSYS 0071) |
| +0x15c | registry `HwInternalLed` → written to GPIO 4 at D0 |

Board vtable slots the ACM uses (all thunk through the same base implementations):

| slot | impl | meaning (CtxHdb call) |
|---|---|---|
| +0x190 → +0x290 | 0x30fb8 | `HdUa->SetBaud(edx)` (slot +0x40 = 0x14154) |
| +0x198 → +0x298 | 0x30fd8 | rx byte available and no error (`HdUa +0x28 && !+0x30`) |
| +0x1a8 → +0x2a8 | 0x31048 | read one byte (`HdUa +0x48` = RBR) |
| +0x1b8 → +0x2b8 | 0x3108c | read N bytes, per byte ≤1000 status polls (returns count) |
| +0x1c0 → +0x2c0 | 0x31128 | write N bytes, per byte wait tx-ready ≤1000 polls (returns count) |
| +0x230 / +0x238 → +0x270 / +0x278 | 0x30ee8 / 0x30f28 | registry DWORD read / write under `HwSettings` |
| **+0x258** | **0x35db8** | **`HdGp->SetGpio(5, on)`** — ACM power |
| +0xf0 | 0x35754 | output-mode change (speaker/HP) → object +0x58 |
| +0x210/+0x218/+0x220 | 0x1a33c/0x317c4/0x317e0 | HAL property helpers (button actions) |

### 2.3 ACM object **[V]**

* Allocated in 0x34afc at 0x34c63: `ExAllocatePoolWithTag(NonPaged, 0x2f8, '_MCA')`, vtable
  **0x115110** (21 slots, 0x3e8b0 … 0x3f180; the 0x415a0… entries after +0xa0 belong to the next
  class). Parent pointer `acm+0x10 = board`, `acm+0x8 = HAL`.
* Slots: +0x00 dtor 0x3e8b0/0x3e8ec, +0x08 **Init** 0x3e918, +0x10 **Start** 0x3eb80,
  +0x18 **PowerState** 0x3ec60, +0x20 0x3eca4 (set detect interval), +0x28 0x3ecc4 (arm periodic
  timer), +0x30 0x3ed20 (cancel timer), +0x48 0x3ed8c (LED/item 3), +0x50 0x3ee10 (blink
  `F0 21`), +0x60 0x3ee80 (show volume dB), +0x68 0x3ef14 (show percent), +0x70 0x3ef6c (show
  text), +0x78 0x3efb8 (set items 5/6), +0x80 0x3f02c (read HP/SP state), +0x88 0x3f05c
  (blink 2), +0xa0 0x3f180 (volume cache for display).
* Board calls: Init at 0x34d53 (board Init), Start at 0x34f61 (board D0 handler 0x34f04),
  PowerState at 0x35003 (board D3 handler 0x34fcc).

---------------------------------------------------------------------------------------

## 3. Hardware primitives (CtxHdb side, resolved for exactness) **[V]**

`B2[x]` = BAR2 + x (the CtxHdb mapped[1] VA). All accesses are followed by a read-back.

### 3.1 HdGp (GPIO, vtable 0x16260)

| slot | fn | operation |
|---|---|---|
| +0x18 | 0x13888 | get: pins 0-3 → 1; pins 4-7 → `!(B2[0x100] bit(pin-4))` |
| +0x20 | 0x138c0 | pins 4-7: value≠0 → `B2[0x100] &= ~bit(pin-4)`, value 0 → set bit; pins 0-3 → error |
| +0x28 / +0x30 | 0x13938 / 0x13964 | read bit / set-clear bit `pin` in `B2[0x304]` (byte) |
| +0x38 / +0x40 | 0x139cc / 0x139f8 | same on `B2[0x308]` |
| +0x48 / +0x50 | 0x13a60 / 0x13a8c | same on `B2[0x309]` |
| +0x58 / +0x60 | 0x13af4 / 0x13b70 | read / write 16-bit `B2[0x310 + 2*pin]` |
| +0x68 / +0x70 | 0x13bf8 / 0x13c24 | read bit / write `1<<pin` to `B2[0x30c]` |
| +0x78 / +0x80 | 0x13c64 / 0x13c90 | read bit / set-clear bit in `B2[0x300]` |
| +0x88 | 0x13cf8 | read 32-bit `B2[0x320]` |
| **+0x90** | **0x13d10** | **`B2[0x320] (u16) = pin \| (value ? 0x100 : 0)`**, pin ≤ 7 — identical to Linux `ca0113_mmio_gpio_set()` |
| +0x98 | 0x13d6c | store (callback, context) at +0x20/+0x28 — CtxHda passes a no-op (0x31828) |

### 3.2 HdUa (UART, vtable 0x16320), 24 MHz clock, 32-bit registers at stride 4

| slot | fn | operation |
|---|---|---|
| +0x18 | 0x13e44 | full init at 31250 baud (documented as B4d in `ctxhdb-analysis.md`): `B2[0x1c] \|= 0x80`; set baud; `LCR(0xc0c) \|= 3; &= ~4; &= ~8`; `FCR(0xc08) \|= 1; \|= 0x30; &= ~0xc0; \|= 6; &= ~8`; `IER(0xc04) \|= 0x80` |
| +0x20 | 0x140e4 | tx-ready = `(B2[0xc7c] >> 1) & 1` |
| +0x28 | 0x140fc | rx-avail = `B2[0xc7c] bit3 && B2[0xc14] bit0` (LSR.DR) |
| +0x30 | 0x14130 | rx-error = `B2[0xc14] bit7` |
| +0x38 | 0x14148 | return current baud (`this+0x20`) |
| **+0x40** | **0x14154** | **set baud**: wait tx idle (0x13fe8: poll `B2[0xc7c] bit0 == 0`, ≤1000 × 320 µs); `div = 24000000 / (baud*16)` (0 if baud 0); `LCR \|= 0x80`; `DLL(0xc00) = div & 0xff`; `DLM(0xc04) = div >> 8`; `LCR &= ~0x80`. For 115200: **div = 13 (0x0d)** |
| +0x48 | 0x1422c | read byte = `B2[0xc00]` (RBR) |
| +0x50 | 0x14240 | write byte: `B2[0xc00] = b` (THR) |
| +0x58/+0x60/+0x68 | 0x14268/0x142c4/0x14320 | IER bit1 / bit0 / … enable with callback (unused by ACM) |

---------------------------------------------------------------------------------------

## 4. Ordered hardware operations

### 4.1 Prerequisite: CtxHdb device start (from `ctxhdb-analysis.md`) **[V, other report]**
BAR0/BAR2 mapped, HdGp/HdUa/HdIc/HdIs objects created, HdUa init at 31250 baud (B4d), CodecConfig.
Linux equivalent: everything the operator already replicates, plus the UART init sequence above.

### 4.2 CtxHda board D0 (0x34f04) — runs before the ACM is started **[V]**

```
0x36338   read registry HwInternalLed → board+0x15c
0x364b0   GPIO/engine init:
            for pin in 0..7:  HdGp+0x20(pin,1)  → B2[0x100] &= ~(1<<(pin-4)) for pins 4-7 (pins 0-3 return error, ignored)
                              HdGp+0x30(pin,1)  → B2[0x304] |= 1<<pin      (net: B2[0x304] = 0xff)
            HdIc+0x40(0); HdIc+0x48(1)             (command-engine mode/flag, CtxHdb 0x147a0/0x147d0)
            HdGp+0x90(5, board+0x120)              → B2[0x320] = 0x0105     ← ACM POWER ON (pin 5 = 1)
            0x35de4 (once):  B2[0x320] = 0x0000 ; KeDelayExecutionThread(1 ms) ; B2[0x320] = 0x0100   (pin 0 pulse: AntiPop)
            obj+0x58 -> +0x78
            0x35e58(1):      B2[0x320] = 0x0002 (pin 2 = 0) ; obj+0x58 -> +0x70(1,0) ; B2[0x320] = 0x0103 (pin 3 = 1)
sub-objects Start: board+0x48, +0x58, +0x78 (DAC/amp objects, not analysed)
board+0x70 -> ACM Start (0x3eb80)      ← see 4.3
sub-objects Start: board+0x60, +0x68
HdGp+0x90(4, board+0x15c)              → B2[0x320] = 0x0104 | (HwInternalLed<<8)   (pin 4)
obj+0x58 -> +0x80
```

The pin numbers 0/2/3/4/5 here are hard-coded in the AE-9 board class; the INF `*Pin` names
(`AntiPop_MutePin=0`, `HPAMP_SHDNPin=4`, `ExternalDACReset_Pin=5`, …) are read by a *different*
object (0x23f30) and applied to the **CA0132 codec GPIOs via verbs 0x715/0x716/0x717
(and 0xF15/0xF16 reads)** in 0x245f8 — they are not the ca0113 BAR2 pins. Whether the INF's
"pin 5 = ExternalDACReset" coincides with ca0113 pin 5 is therefore not established by this
driver; on the ca0113 side pin 5 is unambiguously the line the ACM state machine power-cycles. **[V]**
for both facts, **[I]** that ca0113 pin 5 is the 12 V rail enable.

### 4.3 ACM Init (0x3e918) and Start (0x3eb80) **[V]**

Init (from board Init, no hardware access): sets the defaults in §6, initialises mutexes at
+0xf8/+0x138/+0x170/+0x1a8/+0x2a8, event +0x20, timer +0x40, then 0x3f2f8 → 0x41034 overrides the
defaults from registry (`HwSettings` DWORDs) and loads `Acm48V` → +0x25c, `AcmEncoderLED` →
+0x28c/+0x250, `AcmDisplay` → +0x290.

Start (board D0, after the GPIO block above):

```
acm+0xec = 1 (enabled), +0xf0/+0xf4/+0xc4 = 0
0x3f2f8                              re-read parameters
+0xe4 = PollIntervalMs / TurboPollIntervalMs        (= 10 with defaults)
board+0x190(0x1c200)  →  HdUa->SetBaud(115200)      ← wait tx idle, DLAB, DLL=0x0d, DLM=0, DLAB off
0x3fcd8               →  one immediate detect attempt (4.4)
PsCreateSystemThread(0x413b4)                        poll thread
vtable+0x28 (0x3ecc4) →  KeSetTimerEx(period = AcmPollIntervalMs = 500 ms)
```

No delay is inserted between GPIO 5 = 1 (in 0x364b0) and the first probe other than the
sub-object starts in between.

### 4.4 Detection state machine (0x3fcd8, called every poll tick while state ≠ 1) **[V]**

State `acm+0xe8`: 0 = not detected, 1 = transient (fresh ACM being initialised), 2 = detected.

```
if AcmEnable12VLowWait && lowWait>0 : lowWait-- ; return          (+0xd0, +0xcc)
board+0x258(1)            →  B2[0x320] = 0x0105                    (power on / keep on, every tick)
ok = probe()              →  TX F0 81 00 F7 ; RX 9 bytes ; present = rx[3] ; version = LE32(rx[4..7]) → +0x1e8
if ok && present:
    if 0x40080() ("fresh ACM"):     → +0x288 = -1 ; state = 1 ; TX F0 03 03 05 03 03 F7 (+ack) ; 0x3fe68 (4.5)
    failCount = 0 ; state = 2
elif state == 2:
    if ++failCount > AcmMaxDetectFailCount(5):
        state = 0 ; failCount = 0 ; board+0x258(0) → B2[0x320] = 0x0005 ; lowWait = Acm12VLowChangeMaxCounts(2)
else:   # state 0/1
    failCount = 0 ; ++notDetected
    thr = (resetCount <= 10) ? AcmMaxDetectToResetCount/10 (=12) : AcmMaxDetectToResetCount (120)
    if notDetected > thr:
        if resetCount < AcmMaxDetectResetTimeoutCount(20): resetCount++ ; board+0x258(0) ; lowWait = 2
        notDetected = 0
```

So a power-cycle is: pin 5 low, then high again on the next tick (500 ms later) unless
`AcmEnable12VLowWait` is set, in which case it stays low for `Acm12VLowChangeMaxCounts` ticks.
After 20 cycles without success the driver stops cycling and just polls.

`0x40080` ("fresh ACM" check): unlock (`F0 54 04 'A' 'c' 'm' '1' F7`, ack, `F0 54 04 '1' 'm' 'c' 'A' F7`,
ack) → `F0 D5 03 00 20 04 F7` → RX 11 bytes, value = LE32(rx[6..9]) → lock
(`F0 54 04 11 11 11 11 F7`, ack). If value == 0xAAAAAAAA: unlock, `F0 55 07 00 20 04 DE C0 AD DE F7`
(write 0xDEADC0DE at the same address), ack, lock, return 1; otherwise return 0 (ACM already
initialised → skip 4.5). **[V]** bytes; **[I]** that `00 20 04` is an address and 0xAAAAAAAA an
"uninitialised RAM" marker.

### 4.5 Post-detect initialisation (0x3fe68) — sent once per fresh ACM **[V]**

```
F0 32 03 02 <lo> <hi> F7      timing param 2 = AcmEncoderLongPressMs (3000)          + 5-byte ack
F0 43 04 <min lo><min hi><max lo><max hi> F7   encoder filter window (100 .. 500)    + ack
F0 32 03 01 <lo> <hi> F7      timing param 1 = AcmSBXLongPressMs (5000)              + ack
F0 05 03 02 01 <Acm48V> F7    48 V phantom power restore (0/1)                       + ack
F0 22 02 01 <acm+0x24c != 0> F7                                                       + ack
F0 22 02 02 <AcmEncoderLED> F7   encoder LED on/off                                   + ack
item 3 ← acm+0x248 : F0 03 03 02 <val?0x40:0> 40 F7   (table 0x1180a0: item3 = reg 2, mask 0x40) + ack
if AcmDisplay:  F0 11 09 'A' 'E' '-' '9' 00 00 00 00 00 F7   (13 bytes)              + ack
F0 21 03 02 <lo> <hi> F7      blink interval = AcmEncoderBlinkIntervalMs/10 if +0x270 else 0 + ack
read items 5,6 (F0 83 01 02 F7 / F0 83 01 07 F7) → HP/SP state → board+0xf0(0/3/4)
```

### 4.6 Periodic service (thread 0x413b4, every 500 ms; 50 ms "turbo" while the encoder moves) **[V]**

* every tick: 0x3fcd8 (probe keep-alive, above), then if state 2:
  * 0x3f8a8 encoder: if version ≥ 0x01957D60: `F0 C2 00 F7` → RX 8: dir = rx[3], steps = rx[4],
    ms = LE16(rx[5..6]) (acceleration via `AcmEncoderMsPerSteps2X/3X/4X`); else `F0 C1 00 F7` →
    RX 6: dir = rx[3], steps = rx[4]. Non-zero → volume steps via 0x40f18 → board +0xc8/+0xb0/+0xb8,
    and the timer is switched to `AcmTurboPollIntervalMs` for `AcmTurboPollTimeoutCounts` ticks.
  * every 10th tick (`+0xe4`): 0x3f494 (items 2,1,7 via `F0 83 01 <reg> F7`; then
    `F0 85 01 02 F7` → RX 7: if rx[4] ≠ 0 then 48V = rx[5] → +0x25c; mic-mute-on-connection
    logic), 0x3f02c/0x40198 (items 5,6 → HP/SP), 0x3f6b0 (buttons: for n in 1..3
    `F0 B1 01 n F7` → RX 8: rx[3] == n, pressed = rx[4] == 1, ms = LE16(rx[5..6]); button 1 long
    press toggles display, button 2 long press toggles LED, mid press toggles output, short press
    mute), 0x3fb70 (display timeouts → show volume / "-SP-" / "-HP-").

### 4.7 Shutdown / D3 (board 0x34fcc → ACM 0x3ec60 → 0x3f3c0, then 0x3656c) **[V]**

```
ACM (only for power state 3 or 4): save Acm48V/AcmEncoderLED to registry ;
      TX F0 52 02 CC DD F7 (+ack) ; cancel timer ; signal event ; wait for thread ; +0xf0 = 1
board 0x361dc: save HwInternalLed / HwDisplayLinear / Volume_%d
board 0x3656c: B2[0x320] = 0x0004 (pin 4 = 0) ; 0x0005 (pin 5 = 0 → ACM OFF) ; 0x0003 (pin 3 = 0) ;
               obj+0x58->+0x98 ; 0x0000 (pin 0 = 0)
```

Board D3 with state 0 (return to D0) re-runs 0x364b0, i.e. pin 5 goes high again.

---------------------------------------------------------------------------------------

## 5. UART link protocol (all packets found in `.data` 0x117fe8–0x118098 and their builders)

Transport rules (0x40260 / 0x402f0 / 0x40200): take mutex `acm+0x138`; **drain RX** (read while
rx-avail) ; write N bytes (per byte: wait tx-ready ≤1000 polls) ; read reply N bytes (per byte:
≤1000 polls of rx-avail-and-no-error, no sleeps) ; reply accepted only if all N bytes arrived and
`rx[0] == 0xF0`. The last 0x1e bytes of TX/RX are kept at `acm+0x1ec` / `acm+0x20a` for debugging.
"ack" = 0x40200: read **5** bytes and require `rx[4] == 0x00` (bytes 0–3 unchecked; a 6-byte
`F0 xx 02 st 00 F7` reply would satisfy this and leave `F7` to be drained next time) **[?]**.

Frame: `F0 <cmd> <len> <payload[len]> F7`, total = len + 4. `cmd | 0x80` = query whose reply is
`F0 <cmd&0x7f> <len'> … F7`.

| cmd | direction | bytes | meaning | builder |
|---|---|---|---|---|
| 0x81 | TX | `F0 81 00 F7` | **probe / presence**; RX 9: `[3]` present, `[4..7]` LE32 fw version | 0x4037c |
| 0x03 | TX | `F0 03 03 <reg> <value> <mask> F7` | **set register bits** (regs 2,4,5,7 seen) ; ack | 0x404e4 |
| 0x83 | TX | `F0 83 01 <reg> F7` | read register ; RX 6: `[4]` = value | 0x4044c |
| 0x05 | TX | `F0 05 03 02 01 <0/1> F7` | **48 V phantom power on/off** ; ack | 0x3fe68 |
| 0x85 | TX | `F0 85 01 02 F7` | read 48 V ; RX 7: `[4]` valid, `[5]` state | 0x3f494 |
| 0x11 | TX | `F0 11 09 <8 ASCII, NUL padded> 00 F7` | **display text** ("AE-9", "-SP-", "-HP-", "-10.5", 8 spaces = clear) ; ack | 0x40a24, 0x40578, 0x407fc |
| 0x21 | TX | `F0 21 03 <id> <lo> <hi> F7` | blink interval (id 2) ; ack | 0x40b10 |
| 0x22 | TX | `F0 22 02 <id> <0/1> F7` | id 1 = `acm+0x24c` flag, **id 2 = encoder LED** ; ack | 0x40bb0 |
| 0x32 | TX | `F0 32 03 <id> <lo> <hi> F7` | press-time params: 1 = SBX long press, 2 = encoder long press ; ack | 0x40c3c |
| 0x43 | TX | `F0 43 04 <min16> <max16> F7` | encoder filter window ; ack | 0x3fe68 |
| 0xB1 | TX | `F0 B1 01 <n> F7` | button n=1..3 state ; RX 8 | 0x3f6b0 |
| 0xB2 | — | `F0 B2 01 xx F7` | in `.data` 0x118038, **no code reference** | — |
| 0xC1 | TX | `F0 C1 00 F7` | encoder delta (old fw) ; RX 6: dir, steps | 0x3f8a8 |
| 0xC2 | TX | `F0 C2 00 F7` | encoder delta + ms (fw ≥ 0x01957D60) ; RX 8 | 0x3f8a8 |
| 0x54 | TX | `F0 54 04 41 63 6D 31 F7` + `F0 54 04 31 6D 63 41 F7` | **unlock** ("Acm1","1mcA") ; `F0 54 04 11 11 11 11 F7` = lock ; each acked | 0x40da0 |
| 0x55 | TX | `F0 55 07 00 20 04 DE C0 AD DE F7` | memory write (addr `00 20 04`, LE32 value) ; ack | 0x40cdc |
| 0xD5 | TX | `F0 D5 03 00 20 04 F7` | memory read ; RX 11: `[6..9]` LE32 | 0x40080 |
| 0x51 | — | `F0 51 02 AA BB F7` | in `.data` 0x118058, no code reference | — |
| 0x52 | TX | `F0 52 02 CC DD F7` | **shutdown / link close** ; ack | 0x3f3c0 |

Register-bit table for cmd 0x03/0x83 (0x1180a0, `{item, flag, reg, mask}`), used by 0x3fc08 (set)
and 0x3fc64 (get): item0 = reg2 & 0x01, item1 = reg2 & 0x10, item2 = reg2 & 0x20, item3 = reg2 & 0x40,
item4 = reg4 & 0x01, item5 = reg2 & 0x04, item6 = reg7 & 0x01, item7 = reg7 & 0x04, item8 = reg7 & 0x08.
Items 5/6 are read as the headphone/speaker selection (0x40198: item5 → 2, item6 → 1, else 0 →
board output mode 4/3/0); items 2/1/7 are polled as status; item 3 is set from `acm+0x248`;
items 5/6 are set by slot +0x78. The detect handshake writes reg 5 mask 3 value 3. **[V]** bytes,
**[I]** item semantics.

---------------------------------------------------------------------------------------

## 6. Registry parameters (reader 0x41034, defaults from 0x3e918) **[V]**

| name | field | default | consumer |
|---|---|---|---|
| AcmPollIntervalMs | +0x80 | 500 | timer period (0x3ecc4, 0x413b4) |
| AcmTurboPollIntervalMs | +0x84 | 50 | timer period while encoder active (0x3f8a8) |
| AcmTurboPollTimeoutCounts | +0xdc | 60 | ticks before leaving turbo (0x413b4) |
| AcmDetectPollIntervalMs | +0x88 | 2000 | stored; only setter 0x3eca4 found, no timer consumer **[?]** |
| AcmEncoderLongPressMs / MidPressMs | +0x8c / +0x90 | 3000 / 1500 | button 2 handling; +0x8c also sent as `F0 32 03 02` |
| AcmEncoderBlinkIntervalMs | +0x94 | 500 | `F0 21 03 02` (÷10) |
| AcmEncoderFilterWinMin / Max | +0x98 / +0x9a (u16) | 100 / 500 | `F0 43 04` |
| AcmShowSpeakerChangeMaxCounts / AcmShowAE9TextMaxCounts | +0x9c / +0xa4 | 10 / 10 | display timeouts (0x3fb70) |
| AcmSBXLongPressMs | +0xac | 5000 | `F0 32 03 01`, button 1 |
| AcmMaxDetectFailCount | +0xb0 | 5 | §4.4 |
| AcmMaxDetectToResetCount | +0xb8 | 120 | §4.4 |
| AcmMaxDetectResetTimeoutCount | +0xc0 | 20 | §4.4 |
| AcmEncoderMsPerSteps4X/3X/2X | +0x240/+0x242/+0x244 (u16) | 6 / 8 / 12 | encoder acceleration |
| AcmEnableMicMuteOnConnections | +0x294 | 0 | 0x3f494 |
| AcmMicUnMuteChangeMaxCounts | +0x29c | 2 | 0x3f494 |
| AcmEnable12VLowWait | +0xd0 | 0 | §4.4 (honour low-wait counter) |
| Acm12VLowChangeMaxCounts | +0xc8 | 2 | §4.4 (ticks held low) |
| Acm48V / AcmEncoderLED / AcmDisplay | +0x25c / +0x28c / +0x290 | (saved state) | read at Init, written at D3 (0x3f28c) |

Registry access goes through board slots +0x230/+0x238 (0x30ee8/0x30f28) under the `HwSettings`
key of the device's software key.

---------------------------------------------------------------------------------------

## 7. What is AE-9 specific

* SUBSYS 0x11020071 (and 0x11020073) → class 0x113db0 with `board+0x120 = 1`; 0x34a4c returns
  ID 0x58b instead of 0x18a when that flag is set (used by the property interface). No other
  subsystem ID enables the ACM object; 0x11020072 does not appear in CtxHda.
* The "AE-9" display string (0x114c38) is sent at every fresh-ACM init; there is no
  "XAE9" string in CtxHda (that key lives in CtxHdb's CodecConfig).
* GPIO use of the 0x113db0 class (hard-coded pins): 0 = anti-pop pulse, 2 = cleared at init,
  3 = set at init / cleared at D3, 4 = HwInternalLed at D0 / cleared at D3, 5 = ACM power,
  1 = 192 kHz DAC select (0x3da8d writes `board+0x100`), pins 6/7 untouched.

---------------------------------------------------------------------------------------

## 8. Open questions

1. **Ack format.** 0x40200 accepts a reply only when 5 bytes arrive and byte 4 is 0x00; under the
   `F0 cmd len … F7` framing this implies a 6-byte `F0 xx 02 <st> 00 F7` (F7 left in the FIFO and
   drained before the next TX) or a non-framed 5-byte ack. Needs a logic-analyser capture.
2. **Is ca0113 GPIO 5 the 12 V rail?** The driver names nothing; the inference rests on the
   `Acm12VLow*` parameters gating exactly the pin-5 low period and on the AE-9 class powering pin 5
   only when `+0x120` (has-ACM) is set. The INF's `ExternalDACReset_Pin=5` refers to the codec
   GPIO set (verbs 0x715–0x717), not to ca0113 pin 5.
3. **Probe reply layout.** Only `rx[3]` (present) and `rx[4..7]` (version) are consumed; bytes
   1–2 are presumably `01 05`. Version threshold 0x01957D60 selects the `C2` encoder query.
4. **Meaning of `00 20 04` in the 0x55/0xD5 packets** (address vs. bank/offset) and whether
   writing 0xDEADC0DE has side effects beyond marking the ACM as initialised.
5. **Item semantics** for registers 2/4/5/7 (table 0x1180a0) beyond the HP/SP pair.
6. **Timing.** No explicit delays exist between pin 5 = 1 and the first probe, nor after the
   baud change; the per-byte read timeout is 1000 status polls (µs-scale). If the ACM needs
   boot time after 12 V is applied, the driver simply relies on the 500 ms retry loop.
7. `AcmDetectPollIntervalMs` (+0x88) has no consumer other than its setter (0x3eca4).
8. Object `board+0x58` (vtable 0x113c50) and the HdIc calls in 0x364b0 were not analysed;
   they may matter for the ES9038PRO but not for the ACM link.

---------------------------------------------------------------------------------------

## 9. Function index

| VA | role |
|---|---|
| 0x11508 | adapter: open `\Device\CTXHDB`, obtain controller + 5 interfaces |
| 0x2c17c / 0x2c42c | CtxHdb IOCTL handshake (0x3F3E0004/0008/0044) / IOCTL sender |
| 0x11930 | adapter start: HAL ctor 0x1c1c8, board factory 0x3053c, board Init call (0x11c5f) |
| 0x3053c | board factory by SUBSYS; 0x34814 AE-9 ctor (vtable 0x113db0, `+0x120=1`) |
| 0x34afc | board Init: field wiring, ACM alloc (`_MCA`, 0x2f8) + ACM Init call (0x34d53), 0x364b0 |
| 0x364b0 / 0x3656c | board GPIO init (pin 5 on) / GPIO teardown (pins 4,5,3,0 off) |
| 0x35de4 / 0x35e58 | anti-pop pulse on pin 0 / pins 2,3 |
| 0x34f04 / 0x34fcc | board D0 (ACM Start at 0x34f61) / D3 (ACM PowerState at 0x35003) |
| 0x35db8 | board slot +0x258: `HdGp->SetGpio(5, on)` |
| 0x30fb8, 0x30fd8, 0x31048, 0x3108c, 0x31128 | board UART wrappers (set baud, rx-avail, read1, readN, writeN) |
| 0x30ee8 / 0x30f28 | registry DWORD read / write (`HwSettings`) |
| 0x3e918 / 0x3eb80 / 0x3ec60 / 0x3ecc4 / 0x3ed20 | ACM Init / Start / PowerState / arm timer / cancel timer |
| 0x41034 / 0x3f2f8 / 0x3f28c | ACM parameter reader / load state / save state |
| 0x413b4 | ACM poll thread |
| 0x3fcd8 | detect state machine |
| 0x4037c / 0x40080 / 0x3fe68 | probe / fresh-ACM check / post-detect init |
| 0x40da0 / 0x40cdc | unlock-lock / memory write |
| 0x404e4 / 0x4044c / 0x3fc08 / 0x3fc64 / 0x40fd0 | set reg bits / read reg / set item / get item / item table |
| 0x40c3c / 0x40bb0 / 0x40b10 / 0x40a24 / 0x40578 / 0x407fc | `F0 32` / `F0 22` / `F0 21` / text / volume-dB text / percent text |
| 0x3f8a8 / 0x3f494 / 0x3f6b0 / 0x3fb70 / 0x40198 | encoder / status+48V / buttons / display timeouts / HP-SP state |
| 0x3f3c0 | ACM shutdown (`F0 52 02 CC DD F7`, stop thread) |
| 0x40260 / 0x402f0 / 0x40200 / 0x3f25c | write packet (drain first) / read reply / read ack / take mutex |
| CtxHdb 0x124f0 | controller QueryInterface (IID → sub-object) |
| CtxHdb 0x13d10 | GPIO set: `B2[0x320] = pin \| on<<8` |
| CtxHdb 0x13e44 / 0x14154 / 0x1422c / 0x14240 / 0x140e4 / 0x140fc / 0x14130 | UART init / set baud / RBR / THR / tx-ready / rx-avail / rx-error |

Data: packet templates `.data` 0x117fe8–0x118098; item table 0x1180a0–0x1180e8; display strings
`.rdata` 0x114c38 ("AE-9"), 0x114c40 ("-SP-"), 0x114c48 ("-HP-"), 0x114c98 (8 spaces);
`Acm*` parameter names 0x114c50–0x1150d0; interface GUIDs 0x112210–0x112260.

---------------------------------------------------------------------------------------

## 10. Follow-up: analog output path — ACM headphone, rear line-out, external DACs

Same binary, same method. Trigger: on hardware, the ACM link works but the ACM headphone jack
**and** the rear line-out are silent with the Linux AE-5 profile. The findings below cover the
AE-9 board class (vtable 0x113db0, SUBSYS 0x11020071) only.

### 10.1 Corrections / additions to the object model **[V]**

* `adapter+0x390` (IID 01E63F10) = **HdIc** (command engine / I2C master, BAR2 0x200–0x210/0x800),
  `adapter+0x398` (IID F326ED5A) = **HdIs** (I2S block, BAR2 0x400). Board fields: `+0x20 = HdIc`,
  `+0x40 = HdIs` (ctor 0x34b4b / 0x34b68). Confirmed by the consumers: the DAC proxy is Init'ed
  with `board+0x20` and issues lock/set-address/write-with-subaddress; the I2S object is Init'ed
  with `board+0x40` and calls its width(0x20)/rate(96000) methods (CtxHdb 0x1506c / 0x150b8).
* `board+0x48` = **I2SM object** (tag `I2SM`, vtable 0x113cf0, 0x150 bytes) — the CA0132→I2S→DAC
  path controller. Init at 0x34d10: `(HAL, HdIs, HdGp, board)` → fields +0x20/+0x30/+0x38/+0x40.
  It also owns a system thread (0x34718, created by 0x34304) and a timer.
* `board+0x58` = **DAC proxy** (vtable 0x113c50) forwarding every call to two sub-objects created
  by its Init 0x326fc: `+0x20` = class 0x113bb0 = **I2C device at address 0x48** (the DAC),
  `+0x28` = class 0x113b10 = **I2C device at address 0x49** (second device, unidentified).
  Proxy Init receives `(HdIc, variant = (board->slot 0x2e8(8) >> 2) & 7)` at 0x34ce1–0x34cf7;
  `variant` lands in `dac+0x18` and only selects between two THD tables (§10.4).
* The HAL (`adapter+0x10`, vtable 0x112680) methods used here, resolved to CA0132 verbs on the
  vendor node (`hal+0x31c`, = 0x15):

| HAL slot | fn | verbs | Linux ca0132.c equivalent |
|---|---|---|---|
| +0x60 | 0x1a668 | generic `(nid, verb, payload, &resp)` | `snd_hda_codec_read/write` |
| +0xe0 | 0x1a908 | `0x707` to `nid` | pin-widget control (or 8051 data on 0x15) |
| +0x170 | 0x1af80 | `0x70F` ← `(value<<7)\|(flag&0x7f)` | `chipio_set_control_flag` |
| +0x180 | 0x1b344 | `0x710` ← `(val<<5)\|(id&0x1f)` if id<32 && val<8, else `0x717`=id, `0x718`=val | `chipio_set_control_param` |
| +0x1d8 / +0x1e8 | 0x1b6fc / 0x1b80c → 0x1b610 / 0x1b728 | `0x70D`=addr, then `0xF0C` read / `0x70C` write | `chipio_8051_read/write_pll_pmu` |
| +0x1f8 / +0x208 | 0x1b99c→0x1b840 / 0x1b9c8 | `0x000`,`0x001` addr lo/hi; `0x003`,`0x004` data lo/hi (write) / `0xF03`,`0xF04` (read) | `chipio_read` / `chipio_write` |
| +0x210 / +0x218 | 0x1bac8 / 0x1bb9c | `0x70D`,`0x70E` addr, `0xF07` read / `0x707` write | `chipio_8051_read/write_exram` |
| +0x240 / +0x290 | 0x1c00c / 0x1c16c | `0x724` ← `(b<<7)\|(port&0xf)` / `0xF24` read on NID 0x15 | (AE-7 "0x724, 0x83" write) |
| +0x250 / +0x258 | 0x1b180 / 0x1b210 | spinlock+wait / release | chipio mutex |
| +0x2e0 | 0x1dcc8→0x1ddbc | SCP packet (DSP) | `dspio_scp` |

Wrappers built on +0x180 (all **[V]**, identical to Linux):
0x278b4 = `chipio_set_conn_rate(conn, rate)` (params 0x1d/0x1e; 96000 → code 0xb),
0x27504 = `chipio_set_stream_channels(stream, n)` (0x18/0x1b),
0x27594 = `chipio_set_stream_source_dest(stream, src, dst)` (0x18/0x19/0x1a),
0x27718 = `chipio_set_stream_control(stream, 1)` (0x18/0x1c),
0x27648 = source/dest/channels in one (0x18/0x19/0x1a/0x1b).

### 10.2 The I2C-over-command-engine primitive **[V]**

`0x325c8(dac, reg, value, 1)` (address 0x48) and `0x31d60` (address 0x49) do:
`HdIc+0x18` lock (CtxHdb 0x144a0: `B2[0x210] = 0x7e`, `= 0x5a`, read back 0xaa) →
`HdIc+0x28` set address (0x14580: stores addr/width) → `HdIc+0x38` write-with-subaddress
(0x146c8: `B2[0x804] = (B2[0x804] & ~0x3ff) | addr`; `B2[0x20c]` bits 2:1 ← width code;
`B2[0x204] = (value << 8) | reg`; poll `B2[0x20c]` bit 23 ≤10 × 100 µs, then read 0x860/0x854/0x840)
→ `HdIc+0x20` unlock (0x14554 → 0x14ad8: `B2[0x20c] &= ~1`, `B2[0x210] = 0`).
Reads (`0x3262c` / `0x31dc4`) use the same lock, set-address(0x48|0x49) and `HdIc+0x30`.
This is byte-for-byte what Linux does in `ca0113_mmio_command_set(codec, group, target, value)`
with `group = 0x48` (or 0x49), `target = reg`, `value`.

### 10.3 Ordered sequence: what the AE-9 does after the DSP image is loaded **[V unless marked]**

Driver-level order (0x12d6c, PAGE section, runs on start and on every DSP recovery):

```
A  HAL->+0x310 (0x1e0c0)   BEFORE the image: chipio_read 0x189030/0x18902c; INF pin 8 (ExternalDACReset) ← 0;
                            sleep; chipio_write(0x189030, 0x82); sleep; chipio_write(0x18902c, 0x03); sleep;
                            INF pin 8 ← 1; (DoCodecRecovery/I2SRecovery_* registry logic, 8051 exram/pll writes,
                            chipio_read 0x18b0a4, set-param ×2, 0x707 to NIDs 0x14/0x15 on the codec object).
                            NOTE: on the AE-9 every INF-pin write is swallowed by the board override (§10.7),
                            so the "DAC reset" toggles here are no-ops for this card.
B  0x12d914 + 0x14ea8      pre-load PLL/pin setup and the DSP image download (LoadImage/DspLoaded/DSPLoadFail),
                            up to 3 attempts, HAL+0x198 + 10 ms between attempts.
C  HAL->+0x290, chipio_write(0x100e34, 0xf1000000) (0x12f88)                    [I: meaning unknown]
D  LineMic1/2Config (0x26f44/0x27140), 0x26cf4, INF pins 11,12,13,15 ← 1 (mute all; on AE-9 only 11 and
   12 do anything, see §10.6), LineMic1/2Monitor, VIPMasterControl (0x16ca0: param 1), SpdifOutSource
   (0x16fb0: param 2), THXMasterControl, 0x24824, MultiplexInput/Output (0x182e4/0x19134: INF pin 4 = ca0113 GPIO 3)
E  0x12290 → board D0 (slot +0x68 = 0x34f04)                                    ← the analog bring-up, below
F  0x190f0 (SpeakerMode registry) → board +0x78 (0x35084: speaker mode → I2SM+0x38 flag, ACM "-HP-"/"-SP-")
G  VIPSource, 0x223cc, PcRequestNewPowerState, 0x221f0 (PcRegisterSubdevice), InitDone=1
```

Board D0 (0x34f04), in order:

```
D0.1  0x36338: registry HwInternalLed → board+0x15c
D0.2  0x364b0:
        for pin 0..7: HdGp+0x20(pin,1) → B2[0x100] &= ~(1<<(pin-4)) (pins 4-7); HdGp+0x30(pin,1) → B2[0x304] |= 1<<pin
        HdIc+0x40(0) (CtxHdb 0x147a0 → 0x149bc "mode 0" engine re-init), HdIc+0x48(1) (0x147d0: B2 flag bit)
        B2[0x320] = 0x0105                        ca0113 GPIO 5 = 1  (ACM 12 V / "external" enable)
        0x35de4 (once): B2[0x320]=0x0000; 1 ms; B2[0x320]=0x0100     (pin 0 anti-pop pulse)
        proxy +0x78  → DAC 0x48: reg7 |= 0x01 (mute)        ; dev 0x49: reg 0x0a = 0x07
        0x35e58(1): B2[0x320]=0x0002 (pin 2 = 0); proxy +0x70(1,0) → DAC 0x48: reg 0x1d = 0x40 ; B2[0x320]=0x0103 (pin 3 = 1)
D0.3  I2SM Start (0x32dc0) — the "post-DSP ASI/PLL setup"; full list in §10.5
D0.4  proxy Start (0x32808) → DAC 0x48 init (0x31e3c) then device 0x49 init (0x31a38); full lists in §10.4
D0.5  board+0x78 object Start; ACM Start (§4.3); board+0x60/+0x68 Start
D0.6  B2[0x320] = 0x0104 | (HwInternalLed << 8)   (ca0113 GPIO 4 = internal LED on this class)
D0.7  proxy +0x80 → DAC 0x48: reg7 &= ~0x01 (unmute) ; dev 0x49: reg 0x0a = 0x06
```

There is **no** codec GPIO verb, no pin-widget-control change and no EAPD in D0 for this class.

### 10.4 External DAC programming (I2C address 0x48, via §10.2) **[V]**

Init 0x31e3c (D0.4), in order; `rd(r)` = read register r first:

```
0x0b = (rd(0x0b) & 0x0f) | 0x20
0x04 = 0x00
0x06 = 0x40
0x08 = 0xff
0x1d = 0x40
0x0a = 0x06
0x0c = 0x5f
0x11,0x12,0x13,0x14 = 0xff,0xff,0xff,0x7f     (32-bit "master trim" 0x7fffffff = 0 dB, via +0x40 = 0x320a0)
0x0e = rd(0x0e) | 0x80
0x07 = rd(0x07) | 0x01                       (mute, +0x78 = 0x324e0)
0x0f = 0x00 ; 0x10 = 0x00                    (channel volumes, +0x28 = 0x31fe4)
0x0a = 0x02
```
Later, at D0.7: `0x07 = rd(0x07) & ~0x01` (unmute, +0x80 = 0x32520).
Register map matches the **ES9038Q2M** layout (0x0f/0x10 volume 1/2, 0x11–0x14 master trim,
0x0a master mode, 0x0c DPLL, 0x0e soft-start, 0x07 mute) rather than the ES9038PRO's
(volume at 0x0f–0x16, trim at 0x17–0x1a) **[I]**.

Runtime writes:

| what | fn | registers |
|---|---|---|
| output-mode "THD" table (+0x60 = 0x32298, arg = DAC-mode) | tables 0x118f38/0x118f48/0x118f58, 5 bytes per variant | writes 0x0d, 0x16, 0x17, 0x18, 0x19 in that order |
| DAC-mode 3 (← board mode 4) | | `0x0d=0, 0x16=0, 0x17=0, 0x18=0, 0x19=1` (both variants) |
| DAC-mode 4 (← board mode 3) and 5 (other) | | variant 0: `0x0d=0x40, 0x16..0x19=0`; variant 1: `0x0d=0, 0x16=0xff, 0x17..0x19=0` |
| DAC-mode 1 / 2 | | `0x0d=0, 0x17=0, 0x19=2` / `0x0d=0, 0x17=0xfc, 0x19=0xf2` |
| DAC-mode 0 (HP_Mute=1) | | all five = 0 (table in bss) |
| master trim per mode | +0x40 = 0x320a0 | 0x11..0x14 ← LE32 `board+0x84+mode*4` |
| channel volume | +0x20 = 0x31f94 / +0x28 = 0x31fe4 | 0x0f, 0x10 |
| Front_Mute (INF index 12) | +0x70 = 0x324a4 | 0x1d ← 0x40 (unmuted) / 0x80 (muted) |
| +0x68 = 0x32460 | | reg `r9` ← 0xc3 (not reached from the AE-9 paths analysed) |

Second I2C device at address **0x49** (class 0x113b10, unidentified chip) **[V]** for the bytes:
init 0x31a38: `0x18=0xff, 0x19=0xff, 0x1a=0x00, 0x1b=0x70, 0x00=0xff, 0x01=0xff, 0x06=0xff, 0x07=0xff,
0x10=0x7f`, then `0x0a=0x07` (+0x78 "mute"); +0x80 "unmute": `0x0a=0x06`; +0x20 (0x31b0c):
`0x02=a, 0x04=a, 0x03=b, 0x05=b`; +0x48 (0x31c60): `0x0e = (rd(0x0e)&~1)|x`. Its +0x60 (output mode)
and +0x40 (trim) slots are stubs (0x41760). Whether this is a second DAC (e.g. for the ACM headphone
path) or something else cannot be told from the code; note that the AE-5 profile in Linux also
talks to a second group (0x30) which this driver never uses.

### 10.5 I2SM Start (0x32dc0) — post-DSP ASI / PLL setup **[V]**

```
1  0x34538: three registry DWORDs (names at 0x1134f0/0x113518/0x113548, not decoded)
2  chipio_set_control_param(3, 3)                          (HAL+0x180, param id 3)
3  chipio_set_control_flag(0x16, 1)                        (HAL+0x170 → 0x70F 0x96)  = CONTROL_FLAG_ASI_96KHZ [I: name]
4  0x3394c(1):
     HAL+0x240(3, 1)  → verb 0x724 payload 0x83 on NID 0x15            (Linux AE-7: "0x724, 0x83")
     chipio_set_control_param(0x17 /*ASI*/, 0)
     verb 0x794, payload 0, NID 0x17                                    (Linux AE-7: snd_hda_codec_write(0x17,0,0x794,0))
     chipio_8051_write_exram(0xfa92, 0x22)                              (Linux AE-7: identical)
     0x33ac8: pll_pmu writes  0x44←0xc8, 0x43←0xcc, 0x45←0xcb, 0x40←0xc7, 0x42←0xcd, 0x41←0xce, 0x51←0xdb
              (register numbers come from the ctor bytes I2SM+0x7b..+0x80 = 5,4,0,2,1,3; Linux AE-7 writes
               0x41←0xc8, 0x45←0xcc, 0x40←0xcb, 0x43←0xc7, 0x51←0x8d — different)
     0x340a4(0x81): verb 0x725 ← 0x81 on NID 0x15, then poll 0xF25 until it reads 0x81 (≤10)
5  HdGp+0x90(1, 0)  → B2[0x320] = 0x0001   (ca0113 GPIO 1 = 0; "ExternalDAC_192KSR_Sel" per INF naming)
6  0x34134(1): chipio_set_conn_rate(0x70, 96000); chipio_set_stream_channels(0x0c, 6);
              chipio_set_stream_control(0x0c, 1); 0x34250(0x0c): exram read 0x734+0x0c*10, 0x2779c, retry
7  0x39a48(1): chipio_set_stream_source_dest(0x05, 0x43, 0x00); chipio_set_stream_source_dest(0x18, 0x09, 0xd0);
              chipio_set_conn_rate(0xd0, 96000); chipio_set_stream_channels(0x18, 6);
              0x341e4(0x18): exram read + chipio_set_stream_control(0x18, 1); 0x34250(0x18)
8  0x337a8: I2SM rate = 96000, width 0x20, 6 ch:
     0x33bac(96000): chipio_set_control_param(0x17, 8);
                     pll_pmu 0x40←0xc7, 0x42←0xcd, 0x41←0xce; 0x340a4(0x81) again;
                     chipio_write(0x189000, 0x0001f101); (0x189004, 0x0001f101); (0x189008, 0x0001f101);
                     chipio_write(0x189024, 0x00014004); chipio_write(0x189028, 0x0002000f)
                     (44.1/48 k → 0x1f100/0x8005; 88.2/96 k → 0x1f101/0x14004; 176.4/192 k → 0x1f102/0x22003;
                      384 k → 0x1f103/0x31002; 768 k → 0x1f103/0x231002)
     proxy +0x50(1) / +0x58(0)  → DAC 0x48 slots (registry/volume housekeeping, no I2C traffic seen)
     HdIs+0x38(0x20)  → I2S word width 32 (CtxHdb 0x1506c)      ; HdIs+0x40(96000) → rate code into B2[0x4xx] bits 31:29 (0x150b8)
     0x33ac8 (pll_pmu set again, as in step 4)
     chipio_set_control_param(0x17 /*ASI*/, 0xf)                (Linux AE-7 ends with ASI = 4)
9  0x34304: worker thread 0x34718 + periodic timer (I2S recovery / rate follow), not analysed further
```
This is the same procedure Linux calls `ae7_post_dsp_asi_setup` / `ae7_post_dsp_asi_stream_setup`
/ `ae7_post_dsp_pll_setup`, with AE-9-specific PLL bytes and ASI = 0xf. The AE-9 class does
**not** call the AE-5 register set (`ca0113_mmio_command_set(0x30, …)`) anywhere; the only
command-engine groups used are 0x48 and 0x49.

### 10.6 Output selection / "headphone" **[V]**

* Board "output mode" `board+0x80` (0..4) is set through slot +0xf0 (0x35754) from two sources:
  (a) the ACM: item 6 (reg 7 bit 0) → mode 3, item 5 (reg 2 bit 2) → mode 4 (0x40198 → 0x3fe68
  / thread 0x413b4); (b) the user-mode property handler 0x226f0 (0x22758, device byte from the
  topology table `+0x5d`).
* 0x35754 does exactly this and nothing else: store mode (if ≤ 4); if `board+0x128` (HP_Mute) == 0:
  DAC-mode = {3→4, 4→3, else 5} → proxy +0x60 (§10.4 THD table on 0x48; stub on 0x49), then
  proxy +0x40(master trim `board+0x84+mode*4`).
* **No HDA pin-widget control, no EAPD, no connection-select, no SCP/DSP parameter and no BAR2
  write other than those I2C register writes happens on a mode change.** The pin/EAPD code that
  exists (0x21ec0: `0x707` on NID 0x11 / `+0x5d` NIDs, `0x70c` EAPD) is the generic CA0132
  topology handler driven by the user-mode property, not by the board class.
* "HP_Mute" (INF index 11) is the real headphone enable on this class — board override 0x357fc:
  `board+0x128 = v`; **ACM +0x48(0, !v) → `F0 03 03 02 <!v?0x40:0> 40 F7`** (ACM register 2 bit 6);
  v == 0 → DAC THD table for the current mode + master trim; v != 0 → DAC-mode 0 + trim 0x7fffffff.
  It is driven to 1 in the post-DSP sequence (0x13081), then by the topology (0x20bdc "HeadphoneOutput",
  0x24a4c mute-all/restore).
* "-HP-" / "-SP-" on the ACM display come from Windows' speaker configuration (board +0x78 =
  0x35084: SpeakerMode 0 or 7 → "-HP-", else "-SP-"), independent of the mode above.
* The ACM's own HP/SP selection (items 5/6) is only ever *read* by the driver; the setter
  0x3efb8 (ACM +0x78) has no caller. What switches the ACM's headphone amplifier is therefore
  either the ACM firmware itself (its button) or reg 2 bit 6 above **[I]**.

### 10.7 CA0132 codec GPIO (verbs 0x715/0x716/0x717) on this class **[V]**

* INF value decode (0x23f30): `byte0` = pin (0xff = disabled), `byte2 != 0` → pin is a **ca0113 pin
  driven through HdGp** (0x18c50: +0x20(pin,1) clears the B2[0x100] mux bit, +0x30(pin,1) sets
  B2[0x304] bit, +0x90(pin,val) writes B2[0x320]); `byte2 == 0` → **codec GPIO on NID 1**;
  `byte3 bit0` = input (direction bit cleared), `byte3 bit1` = invert value.
  So: FP_DetectPin 0x03010002 = ca0113 pin 2, input, inverted; HPAMP_SHDNPin 0x02010004 = ca0113
  pin 4, output, inverted; Front_MutePin 0x00010007 = ca0113 pin 7; AntiPop 0 / 192KSR 1 /
  ExternalDACReset 5 = codec GPIO 0/1/5.
* Generic writer 0x245f8 (index, value): `0xF17`→`0x717` direction, `0xF16`→`0x716` enable,
  `0xF15`→`0x715` data on NID 1 (0x2476f–0x24803); reader 0x24824 (`0xF17/0xF16`). No 0x71A/0x71B
  (unsolicited/sticky) writes exist in the binary.
* **On the AE-9 class none of this is reached.** 0x245f8 first calls `board->slot 0x130(index, value)`
  and only proceeds if that returns 0xC0000002; the AE-9 implementation 0x357fc returns 0 for every
  index < 18 and handles only:
  index 4 (FPHPCenterLFE_Sel) → `board+0x12c`, ca0113 GPIO 3 ← value;
  index 11 (HP_Mute) → §10.6; index 12 (Front_Mute) → `board+0x124`, DAC 0x48 reg 0x1d ← 0x40/0x80.
  HPAMP_SHDN (15), AntiPop (10), 192KSR (9), ExternalDACReset (8), FP_Detect (0) are **no-ops** here;
  the class drives ca0113 pins 0/1/2/3/4/5 directly instead (§4.2, §10.3, §10.5).
* Every other `0x717` in the binary (0x1b3e3, 0x2be94…0x2c019, 0x24984) is `VENDOR_CHIPIO_PARAM_EX_ID_SET`
  on NID 0x15, not GPIO.

### 10.8 BAR2 writes on headphone selection besides GPIO **[V]**
None. The command-engine traffic of §10.4 (0x210/0x804/0x20c/0x204) is the only BAR2 activity;
B2[0x1c], B2[0x100], B2[0x304]/[0x308] are untouched on a mode change.

### 10.9 Differences from the Linux AE-5 profile that matter for this card **[V] unless marked**

1. ca0113 GPIO 5 must be 1 before the DAC is programmed (D0.2), and GPIO 1 = 0 (192 kHz select),
   GPIO 3 = 1, GPIO 2 = 0, anti-pop pulse on GPIO 0.
2. The DAC at group 0x48 is programmed with the ES9038Q2M-style set of §10.4 — the AE-5 profile's
   `ca0113_mmio_command_set(0x48, 0x07, 0x83)` / `0x0f/0x10/0x11 = 0` writes and its group-0x30 writes are
   not what this driver sends. Mute (reg 7 bit 0) must be cleared *after* the ASI/PLL setup (D0.7).
3. The post-DSP ASI/PLL setup is the AE-7-style one (§10.5) with different PLL bytes and ASI = 0xf,
   not the AE-5 `ae5_post_dsp_*` functions.
4. A second I2C device at 0x49 is initialised (§10.4); if it sits in the headphone path this is
   the most likely reason the ACM jack stays silent even after the DAC is alive **[I]**.
5. HP_Mute → ACM reg 2 bit 6 (`F0 03 03 02 40 40 F7`) is the only host-side "headphone on" sent to
   the ACM **[V]**; sending it is cheap to try.

### 10.10 Open questions

* Identity of the 0x49 device; meaning of DAC regs 0x0d/0x16–0x19 as used by the THD tables.
* Meaning of `chipio_write(0x100e34, 0xf1000000)` (step C) and of control param 3 / flag 0x16 / verb
  0x725 0x81 / verb 0x724 0x83.
* Whether ca0113 GPIO 5 is the DAC's reset/power as well as the ACM's 12 V (INF "ExternalDACReset_Pin=5"
  suggests so, but the INF entry is inert on this class).

---------------------------------------------------------------------------------------

## 11. Follow-up 2: exact register-level details (command-engine read, HdIs, stream waits, DAC 0x0a/0x0b)

All **[V]** from CtxHdb.sys / CtxHda.sys unless marked. `B2[x]` = BAR2 + x, 32-bit accesses,
every write followed by a read-back (omitted below). HdIc object fields: `+0x24` = I2C address
(set by set-address), `+0x25` = "subaddress width" (1), `+0x2c` = mode (0 after D0's `HdIc+0x40(0)`),
`+0x30` = BAR2 VA.

### 11.1 Command-engine primitives (CtxHdb HdIc, vtable 0x163b0)

```
lock      +0x18  0x144a0   wait mutex; B2[0x210]=0x7e; B2[0x210]=0x5a; if (B2[0x210] & 0xff) != 0xaa → fail;
                           B2[0x20c] bit0 = (mode != 0)            (mode 0 on the AE-9 → bit0 stays 0)
set-addr  +0x28  0x14580   this+0x24 = addr (0x48/0x49), this+0x25 = width (1)      — no MMIO
poll      –      0x14a50   up to 10×: sleep 100 µs, read B2[0x20c], stop when bit 23 set; then read
                           B2[0x860], B2[0x854], B2[0x840] (values discarded); returns "not timed out"
WRITE     +0x38  0x146c8   (reg, value, nbytes=1):
                           B2[0x804] = (B2[0x804] & ~0x3ff) | addr
                           B2[0x20c] = (B2[0x20c] & ~0x6) | (((nbytes + 1) & 3) << 1)      → 1-byte write: bits2:1 = 2  (0x4)
                           B2[0x204] = (value << 8) | reg
                           poll
READ      +0x30  0x14590   (reg, nbytes=1) — returns the byte in AL:
                           B2[0x804] = (B2[0x804] & ~0x3ff) | addr
                           B2[0x20c] = (B2[0x20c] & ~0x6) | (width(=1) << 1)               → bits2:1 = 1  (0x2)
                           B2[0x204] = reg                                                  (subaddress only)
                           if mode == 0: B2[0x200] = (addr << 16) | reg
                           poll
                           B2[0x20c] = (B2[0x20c] & ~0x6) | (nbytes(=1) << 1)               → bits2:1 = 1
                           B2[0x208] = 0xffff                                               (read strobe)
                           poll
                           result = B2[0x208]            ← the returned data (low byte for a 1-byte read)
unlock    +0x20  0x14554 → 0x14ad8   B2[0x20c] &= ~1; B2[0x210] = 0; release mutex
set mode  +0x40  0x147a0 → 0x149bc   (see ctxhdb-analysis B4c: 0x86c/0x89c handshake, 0x800, 0x804 &= ~0x1000, 0x20c &= ~1)
set flag  +0x48  0x147d0   B2[0x20c] bit0 = (arg != 0); this+0x2c = arg        (D0 calls it with 1 → bit0 = 1, mode = 1)
```
So relative to the Linux write primitive: bits 2:1 of 0x20c are the transfer-length code
(1 = one byte = subaddress only, 2 = subaddress + one data byte; a value of 4 is encoded as 0),
bit 0 is the mode/enable bit set by `HdIc+0x48(1)` in D0.2 (Linux's 0x00800005 sets it too),
bit 23 is the completion flag polled by the driver. For a read, 0x204 carries only the register
number, 0x208 ← 0xffff strobes the read, and **the data comes back in 0x208**; 0x840/0x854/0x860 are
read but never used. Note D0.2 first calls `HdIc+0x40(0)` (mode 0, so reads also write
`B2[0x200] = (0x48 << 16) | reg`) and then `HdIc+0x48(1)`, which sets mode = 1 — after that the
0x200 write is skipped. Both variants exist in the binary; the read path as executed on the AE-9
after D0.2 is the one **without** the 0x200 write **[V]** (order verified in 0x364b0).

CtxHda read-modify-writes on the DAC (0x3262c = lock, set-addr 0x48, READ, unlock; 0x325c8 =
lock, set-addr 0x48, WRITE, unlock): `0x0b = (rd(0x0b) & 0x0f) | 0x20` (0x31e5d, 0x3222f),
`0x0e = rd(0x0e) | 0x80` (0x31f00), `0x07 = rd(0x07) | 1` / `& ~1` (0x324f8 / 0x32538).

### 11.2 HdIs (I2S block, vtable 0x16420) — everything +0x38/+0x40 and friends touch

```
+0x18  0x14ea0  (ch 0..3, on)    B2[0x100] bit (12+ch) = on
+0x20  0x14f24  (ch 0..3, on)    B2[0x100] bit (4+ch)  = on
+0x28  0x14fa8  (ch 0..3, v)     B2[0x104] nibble field per channel (bits 4*ch.. / <<6 variant), v ≤ ?
+0x30  0x15140  (ch, a, b)       B2[0x104] byte field per channel = (a<<4)|b style pack (not used by the AE-9 path)
+0x38  0x1506c  (width)          accepts ONLY 0x10 or 0x18: B2[0x100] bit 28 = (width == 0x18).
                                 CtxHda passes 0x20 → returns 0x80070057 and writes NOTHING (return value ignored).
+0x40  0x150b8  (rate)           code: 48000→0, 44100→1, 96000→2, 88200→3, 192000→4, else error (no write);
                                 B2[0x100] = (B2[0x100] & 0x1fffffff) | (code << 29)   → 96000: bits 31:29 = 010 → |= 0x40000000
+0x48  0x14c38                    QueryInterface (not a hardware method)
```
So on the AE-9 the only HdIs write in D0.3 is **`B2[0x100] = (B2[0x100] & 0x1fffffff) | 0x40000000`**;
bit 28 keeps whatever CtxHdb's HdIs init left (B4a: `0x100 = (x & ~0x800) | 0x600`, `|= 0x0f`,
`|= 0x100`, i.e. bit 28 = 0). Registers 0x400/0x408/0x40c/0x410/0x42c…0x4fc are touched only by the
CtxHdb init (B4a) and the Linux AE-5 table already matches that; HdIs+0x40 adds only the
rate field in 0x100. Note the GPIO calls in D0.2 clear `B2[0x100]` bits 0–3 again
(`HdGp+0x20(pin 4..7, 1)`), after CtxHdb's init had set `|= 0x0f`; the final D0 value of the low
nibble is therefore 0x0 on this class **[V]**.

### 11.3 Stream readiness helpers (CtxHda 0x34250 / 0x341e4)

Both read one byte of 8051 XRAM through HAL+0x210 (0x1bac8: verb 0x70D = addr low, 0x70E = addr
high, 0xF07 = data, on NID 0x15 = Linux `chipio_8051_read_exram`) at address
**`0x734 + 10 * stream_id`** (0x0c → 0x7ac, 0x18 → 0x824). No delay, no retry loop on the read.

```
0x34250(id, repeat=0, value=0):  b = xram[0x734+10*id]; if b != 0: set_stream_control(id, 0) once
                                  (0x2779c: params 0x18 = id, 0x1c = 0); with repeat != 0 it would repeat b times.
0x341e4(id, x):                   b = xram[0x734+10*id]; if b == 0: set_stream_control(id, 1) (0x27718).
```
As called from D0.3: stream 0x0c → `set_stream_control(0x0c, 1)` unconditionally (0x27718 in
0x34134), then 0x34250(0x0c): *stop it again if the DSP's byte is non-zero*; stream 0x18 →
0x341e4(0x18): *start only if the byte is zero*, then 0x34250(0x18): *stop if non-zero*. The byte
therefore behaves as a DSP-owned "stream state/error" flag; its semantics are not visible in this
driver **[?]**. Skipping the two helpers is equivalent to what Linux `ae7_post_dsp_asi_stream_setup`
does (unconditional start of both streams); if the flag is non-zero after the start the Windows
driver would have stopped the stream, so reading `0x7ac`/`0x824` on Linux after the start and
logging the value is the cheap check. (The I2SM worker thread 0x34718 exists but was not analysed;
it may re-issue these later.)

### 11.4 DAC 0x48 register 0x0a / 0x0b / 0x01 — every writer in the binary

| site | fn | write |
|---|---|---|
| 0x31ec9 | init 0x31e3c (early) | `0x0a = 0x06` |
| 0x31f48 | init 0x31e3c (last write) | `0x0a = 0x02` |
| 0x32283 | +0x58 = 0x32260(x) — called from I2SM Start step 8 with x = 0 | `0x0a = (x ? 0x12 : 0x02)` → **0x02** |
| 0x3224a | +0x50 = 0x321dc(mode) — I2SM Start step 8 calls it with mode 1 | `0x01 = 0xc0` (mode 1); 0xc1 (2); 0xc3 (3); 0xcc (4) |
| 0x3223f | 0x321dc, mode 2 only | `0x0b = (rd(0x0b) & 0x0f) \| 0x20` |
| 0x31e6d | init | `0x0b = (rd(0x0b) & 0x0f) \| 0x20` |

So yes: `0x0a = 0x06` early in init, `0x0a = 0x02` at the end of init, and `0x0a = 0x02` again
from the I2SM start; `0x01 = 0xc0` is written in I2SM Start step 8 (proxy +0x50(1)) — this was
listed as "housekeeping" in §10.5 and is in fact a DAC write. Register 0x0b is never written
absolutely; the driver assumes whatever the chip's low nibble is (reset default) and only forces
bits 7:4 = 0010. If the read primitive is not available yet, `0x0b = 0x20` (low nibble 0 = reset
default per the ES9038Q2M map [I]) is the closest approximation.

Corrected I2SM Start step 8 (0x337a8) with the DAC writes shown:
```
chipio_set_control_param(0x17, 8); pll_pmu 0x40←c7, 0x42←cd, 0x41←ce; verb 0x725←0x81 (+poll 0xF25);
chipio_write 0x189000/4/8 ← 0x1f101, 0x189024 ← 0x14004, 0x189028 ← 0x2000f;
DAC 0x48: 0x01 = 0xc0 ; DAC 0x48: 0x0a = 0x02 ;
HdIs width(0x20) → no-op (rejected) ; HdIs rate(96000) → B2[0x100] bits 31:29 = 2 ;
pll_pmu 0x44←c8, 0x43←cc, 0x45←cb, 0x40←c7, 0x42←cd, 0x41←ce, 0x51←db ; chipio_set_control_param(0x17, 0xf)
```
