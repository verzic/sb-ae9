#!/usr/bin/env python3
"""
Reconstruct high-level operations from an AE-9 capture: chipio writes/reads,
8051 exram writes, PLL/PMU writes, control flags/params, stream setup, SCP
messages to the DSP, standard HDA verbs, ca0113 I2C/GPIO, UART packets to the
control module, and HDA controller stream-descriptor programming.

Usage: ops.py <trace> [--device 0000:0a:00.0]
   or: ops.py --linux <hda-tracepoint-log>      (see trace-linux.sh)

Writes <trace>.ops.txt (one operation per line, "[phase] time cadN op ...")
and prints per-phase operation counts. Encodings follow the ca0132 driver
(chipio_set_control_flag, chipio_set_control_param_no_mutex, make_scp_header,
chipio_8051_write_direct, ...).
"""
import re
import sys
from collections import Counter

from decode import parse, assign_phases, split_verb

CHIP, DSP = 0x15, 0x16          # WIDGET_CHIP_CTRL, WIDGET_DSP_CTRL on the main codec

FLAGS = {0: "C_MGR", 1: "DMA", 2: "IDLE_ENABLE", 3: "TRACKER", 4: "SPDIF2OUT", 5: "DMIC",
         6: "ADC_B_96KHZ", 7: "ADC_C_96KHZ", 8: "DAC_96KHZ", 9: "DSP_96KHZ", 10: "SRC_CLOCK_196MHZ",
         11: "SRC_RATE_96KHZ", 12: "DECODE_LOOP", 13: "DAC1_DEEMPHASIS", 14: "DAC2_DEEMPHASIS",
         15: "ADC_A_DEEMPHASIS?", 16: "MICVOL_SEL?", 17: "SPDIF1_SEL?", 18: "SPDIF2_SEL?",
         19: "SPDIF1_LOOP?", 20: "SPDIF2_LOOP?", 21: "PORT_A_COMMON_MODE?", 22: "PORT_D_COMMON_MODE?",
         23: "PORT_A_10KOHM_LOAD?", 24: "PORT_D_10KOHM_LOAD?", 25: "ADC_A_DEEMPHASIS?"}
PARAMS = {1: "VIP_SOURCE", 2: "SPDIF1_SOURCE", 8: "PORTA_160OHM_GAIN", 10: "PORTD_160OHM_GAIN",
          23: "ASI", 24: "STREAM_ID", 25: "STREAM_SOURCE_CONN_POINT", 26: "STREAM_DEST_CONN_POINT",
          27: "STREAMS_CHANNELS", 28: "STREAM_CONTROL", 29: "CONN_POINT_ID",
          30: "CONN_POINT_SAMPLE_RATE", 31: "NODE_ID"}
STD = {0x705: "SET_POWER", 0x706: "SET_STREAM", 0x707: "SET_PIN_CTL", 0x70C: "SET_EAPD",
       0x701: "SET_CONN_SEL", 0x70D: "SET_DIGI_CVT1", 0x70E: "SET_DIGI_CVT2", 0x715: "SET_GPIO_DATA",
       0x716: "SET_GPIO_MASK", 0x717: "SET_GPIO_DIR", 0x71C: "SET_CONFIG_DEF0", 0x7FF: "RESET",
       0x703: "SET_PROC_STATE", 0x704: "SET_SDI_SEL", 0x708: "SET_UNSOL_ENABLE", 0x70A: "SET_BEEP",
       0x70B: "SET_VOLUME_KNOB", 0x709: "SET_PIN_SENSE", 0x711: "SET_STRIPE_CTL", 0x71F: "SET_CVT_CHAN_COUNT"}


class Ops:
    def __init__(self):
        self.lines = []
        self.counts = Counter()
        self.chip_addr = 0
        self.chip_lo = None
        self.pend_read = False
        self.x51_addr = 0
        self.scp = []           # accumulating SCP 32-bit words (low half pending in scp_lo)
        self.scp_lo = None
        self.ex_id = None
        self.uart = []
        self.uart_rx = []
        self.i2c_group = None
        self.polls = Counter()

    def emit(self, e, cad, text):
        kind = text.split()[0]
        self.counts[(e['phase'], kind)] += 1
        self.lines.append(f"[{e['phase']}] {e['ts']:.6f} cad{cad} {text}")

    # ---- codec verbs ----------------------------------------------------
    def verb(self, e):
        cad, nid, v20 = e['cad'], e['nid'], e['v20']
        verb, pay = split_verb(v20)
        if cad == 1 and nid == CHIP:
            return self.chipio(e, cad, verb, pay, v20)
        if cad == 1 and nid == DSP:
            return self.dspio(e, cad, verb, pay, v20)
        if verb in (0xF01,) and nid in (CHIP, DSP):
            self.polls[(cad, nid)] += 1
            return
        self.std(e, cad, nid, verb, pay, v20)

    def std(self, e, cad, nid, verb, pay, v20):
        top = v20 >> 16
        if top == 0x2:
            self.emit(e, cad, f"SET_CVT_FORMAT nid=0x{nid:02x} fmt=0x{pay:04x}")
        elif top == 0x3:
            self.emit(e, cad, f"SET_AMP nid=0x{nid:02x} val=0x{pay:04x}")
        elif verb == 0x706:
            self.emit(e, cad, f"SET_STREAM nid=0x{nid:02x} stream={pay >> 4} ch={pay & 0xf}")
        elif verb in STD:
            self.emit(e, cad, f"{STD[verb]} nid=0x{nid:02x} val=0x{pay:02x}")
        elif verb >= 0xF00 or top in (0xA, 0xB):
            self.emit(e, cad, f"GET nid=0x{nid:02x} verb=0x{verb:03x} val=0x{pay:x}")
        else:
            self.emit(e, cad, f"VERB nid=0x{nid:02x} verb=0x{verb:03x} val=0x{pay:x}")

    def chipio(self, e, cad, verb, pay, v20):
        top = v20 >> 16
        if top == 0x0:
            self.chip_addr = (self.chip_addr & 0xffff0000) | pay
        elif top == 0x1:
            self.chip_addr = (self.chip_addr & 0xffff) | (pay << 16)
        elif top == 0x2:
            self.emit(e, cad, f"CHIPIO_STREAM_FORMAT val=0x{pay:04x}")
        elif top == 0x3:
            self.chip_lo = pay
        elif top == 0x4:
            val = (pay << 16) | (self.chip_lo or 0)
            self.emit(e, cad, f"CHIPIO_WRITE addr=0x{self.chip_addr:06x} val=0x{val:08x}")
            self.chip_addr += 4
            self.chip_lo = None
        elif top == 0x5:
            self.emit(e, cad, f"8051_DIRECT_WRITE addr=0x{pay:02x} val=0x{(v20 >> 8) & 0xff:02x}")
        elif top == 0xD:
            self.emit(e, cad, f"8051_DIRECT_READ addr=0x{pay:02x}")
        elif verb == 0x702:
            self.pend_read = True
        elif verb == 0xF03:
            self.emit(e, cad, f"CHIPIO_READ addr=0x{self.chip_addr:06x}")
            self.chip_addr += 4
            self.pend_read = False
        elif verb == 0x70D:
            self.x51_addr = (self.x51_addr & 0xff00) | pay
        elif verb == 0x70E:
            self.x51_addr = (self.x51_addr & 0xff) | (pay << 8)
        elif verb == 0x707:
            self.emit(e, cad, f"8051_EXRAM_WRITE addr=0x{self.x51_addr:04x} val=0x{pay:02x}")
            self.x51_addr += 1
        elif verb == 0xF07:
            self.emit(e, cad, f"8051_EXRAM_READ addr=0x{self.x51_addr:04x}")
            self.x51_addr += 1
        elif verb == 0xF08:
            self.emit(e, cad, f"8051_PMEM_READ addr=0x{self.x51_addr:04x}")
        elif verb == 0x709:
            self.emit(e, cad, f"8051_IRAM_WRITE addr=0x{self.x51_addr:04x} val=0x{pay:02x}")
        elif verb == 0xF09:
            self.emit(e, cad, f"8051_IRAM_READ addr=0x{self.x51_addr:04x}")
        elif verb == 0x70C:
            self.emit(e, cad, f"PLL_PMU_WRITE addr=0x{self.x51_addr:04x} val=0x{pay:02x}")
        elif verb == 0xF0C:
            self.emit(e, cad, f"PLL_PMU_READ addr=0x{self.x51_addr:04x}")
        elif verb == 0x70F:
            self.emit(e, cad, f"FLAG {FLAGS.get(pay & 0x7f, '?')}({pay & 0x7f})={(pay >> 7) & 1}")
        elif verb == 0xF0F:
            self.emit(e, cad, f"FLAGS_GET val=0x{pay:02x}")
        elif verb == 0x710:
            self.emit(e, cad, f"PARAM {PARAMS.get(pay & 0x1f, '?')}({pay & 0x1f})={pay >> 5}")
        elif verb == 0xF10:
            self.emit(e, cad, f"PARAM_GET {PARAMS.get(pay & 0x1f, '?')}({pay & 0x1f})")
        elif verb == 0x717:
            self.ex_id = pay
        elif verb == 0x718:
            self.emit(e, cad, f"PARAM_EX {PARAMS.get(self.ex_id, '?')}({self.ex_id})={pay}")
        elif verb == 0xF17:
            self.emit(e, cad, f"PARAM_EX_ID_GET val={pay}")
        elif verb == 0xF18:
            self.emit(e, cad, f"PARAM_EX_VALUE_GET id={self.ex_id}")
        elif verb == 0x711:
            self.emit(e, cad, f"PORT_ALLOC_CONFIG val=0x{pay:02x}")
        elif verb == 0x712:
            self.emit(e, cad, f"PORT_ALLOC val=0x{pay:02x}")
        elif verb == 0xF12:
            self.emit(e, cad, f"PORT_ALLOC_GET val=0x{pay:02x}")
        elif verb == 0x713:
            self.emit(e, cad, f"PORT_FREE val=0x{pay:02x}")
        elif verb == 0x70A:
            self.emit(e, cad, f"CT_EXTENSIONS_ENABLE val=0x{pay:02x}")
        elif verb == 0xF00:
            self.emit(e, cad, f"CHIPIO_GET_PARAMETER id=0x{pay:02x}")
        elif verb == 0xF01:
            self.polls[(cad, CHIP)] += 1
        elif verb in (0x788, 0x789, 0x78A, 0x78D):
            names = {0x788: "DMIC_CTL", 0x789: "DMIC_PIN", 0x78A: "DMIC_MCLK", 0x78D: "EAPD_SEL"}
            self.emit(e, cad, f"{names[verb]} val=0x{pay:02x}")
        else:
            self.std(e, cad, CHIP, verb, pay, v20)

    def dspio(self, e, cad, verb, pay, v20):
        top = v20 >> 16
        if top == 0x0:
            self.scp_lo = pay
        elif top == 0x1:
            word = (pay << 16) | (self.scp_lo or 0)
            self.scp_lo = None
            self.scp.append(word)
            hdr = self.scp[0]
            size = (hdr >> 27) & 0x1f
            if len(self.scp) >= 1 + size:
                data = " ".join(f"0x{w:08x}" for w in self.scp[1:])
                self.emit(e, cad, f"SCP target=0x{hdr & 0xff:02x} src=0x{(hdr >> 8) & 0xff:02x} "
                                  f"req=0x{(hdr >> 17) & 0x7f:02x} get={(hdr >> 16) & 1} "
                                  f"dev={(hdr >> 24) & 1} n={size} [{data}]")
                self.scp = []
        elif verb == 0x703:
            self.emit(e, cad, f"DSP_INIT val=0x{pay:02x}")
        elif verb == 0x704:
            self.polls[(cad, 'scp_count_query')] += 1
        elif verb == 0xF04:
            self.polls[(cad, 'scp_read_count')] += 1
        elif verb == 0x702:
            self.polls[(cad, 'scp_post_read')] += 1
        elif verb == 0xF02:
            self.emit(e, cad, "SCP_READ_WORD")
        elif verb == 0xF01:
            self.polls[(cad, DSP)] += 1
        else:
            self.std(e, cad, DSP, verb, pay, v20)

    # ---- BAR accesses ---------------------------------------------------
    def region(self, e):
        r, off, val, w = e['r'], e['off'], e['val'], e['kind'] == 'w'
        if r == 2:
            if off == 0xc00:
                if w:
                    self.uart.append(val & 0xff)
                    if val & 0xff == 0xf7 or len(self.uart) > 64:
                        self.emit(e, 0, "UART_TX " + " ".join(f"{b:02x}" for b in self.uart))
                        self.uart = []
                else:
                    self.uart_rx.append(val & 0xff)
                    if val & 0xff == 0xf7 or len(self.uart_rx) > 64:
                        self.emit(e, 0, "UART_RX " + " ".join(f"{b:02x}" for b in self.uart_rx))
                        self.uart_rx = []
                return
            if not w:
                return
            if off == 0x804:
                self.i2c_group = val & 0x3ff
            elif off == 0x204:
                self.emit(e, 0, f"I2C group=0x{self.i2c_group if self.i2c_group is not None else 0:02x} "
                                f"reg=0x{val & 0xff:02x} val=0x{(val >> 8) & 0xff:02x}")
            elif off == 0x320:
                self.emit(e, 0, f"GPIO pin={val & 0xf} en={(val >> 8) & 1}")
            elif off in (0x200, 0x208, 0x20c, 0x210):
                self.polls[('bar2', off)] += 1
            elif 0xc04 <= off <= 0xc1c:
                self.emit(e, 0, f"UART_REG off=0x{off:03x} val=0x{val:02x}")
            else:
                self.emit(e, 0, f"BAR2 off=0x{off:03x} val=0x{val:08x}")
        elif r == 0 and w:
            if off >= 0x80 and (off - 0x80) % 0x20 in (0x0, 0x12):
                sd = (off - 0x80) // 0x20
                reg = "CTL" if (off - 0x80) % 0x20 == 0 else "FMT"
                if reg == "CTL":
                    self.emit(e, 0, f"HDA_SD{sd}_CTL val=0x{val:x} stream={(val >> 20) & 0xf} run={(val >> 1) & 1}")
                else:
                    self.emit(e, 0, f"HDA_SD{sd}_FMT val=0x{val:04x}")


def load_linux(path, device):
    """Parse a Linux hda tracepoint log (hda_send_cmd lines) into verb events."""
    rx = re.compile(r'^\s*\S+\s+\[\d+\]\s+\S*\s+(?P<ts>\d+\.\d+): hda_send_cmd: \[(?P<dev>[^\]]*):(?P<cad>\d+)\] val=0x(?P<val>[0-9a-f]+)')
    events = []
    with open(path, errors='replace') as f:
        for ln in f:
            m = rx.match(ln)
            if not m or m['dev'] != device:
                continue
            raw = int(m['val'], 16)
            events.append(dict(kind='c', ts=float(m['ts']), cad=int(m['cad']),
                               nid=(raw >> 20) & 0x7f, v20=raw & 0xfffff, raw=raw))
    return events


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    if argv[1] == '--linux':
        path = argv[2]
        device = argv[argv.index('--device') + 1] if '--device' in argv else '0000:0a:00.0'
        events = load_linux(path, device)
    else:
        path = argv[1]
        device = argv[argv.index('--device') + 1] if '--device' in argv else '0000:0a:00.0'
        events = parse(path, device)
    if not events:
        sys.exit("no events")
    nphases = assign_phases(events)
    ops = Ops()
    for e in events:
        if e['kind'] == 'c':
            ops.verb(e)
        elif e['kind'] in ('w', 'r'):
            ops.region(e)
    out = path + '.ops.txt'
    with open(out, 'w') as f:
        f.write("\n".join(ops.lines) + "\n")
    print(f"# {len(ops.lines)} operations -> {out}")
    for p in range(nphases):
        kinds = sorted(((k, n) for (ph, k), n in ops.counts.items() if ph == p), key=lambda t: -t[1])
        if kinds:
            print(f"phase {p}: " + ", ".join(f"{k}={n}" for k, n in kinds))
    print("polls/handshakes (not listed):", dict(ops.polls))


if __name__ == "__main__":
    main(sys.argv)
