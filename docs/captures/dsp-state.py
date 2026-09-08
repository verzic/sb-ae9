#!/usr/bin/env python3
"""
Live AE-9 DSP state snapshot via the codec's vendor (chipio / 8051) verbs.
Run as root:  sudo python3 ~/sb-ae9-linux/capture/dsp-state.py
Answers: did the 0x3f3a0 output-connect overlay land? are the DSP DMA channels
started/active (s3boun3t's wall was "output chans frozen, ACTIVE=0")? are the
DSP stream state bytes non-zero? Output also saved to capture/dsp-state-<ts>.txt.
NOTE: this moves the chip's address register behind the driver's back; reboot
before the next playback rather than trusting the driver's cached address.
"""
import fcntl, struct, glob, os, re, sys, time
IOC = (3 << 30) | (8 << 16) | (ord('H') << 8) | 0x11   # HDA_IOCTL_VERB_WRITE
NID = 0x15                                               # WIDGET_CHIP_CTRL

def card():
    for p in glob.glob('/proc/asound/card*/id'):
        if 'Creative' in open(p).read():
            return int(re.search(r'card(\d+)', p).group(1))
    sys.exit("no Creative card is up (run go.sh first)")

def verb(f, v, p):
    b = bytearray(struct.pack('II', (NID << 20) | (v << 8) | p, 0))
    fcntl.ioctl(f, IOC, b)
    return struct.unpack('II', b)[1]

def chipio(f, a):   # ADDRESS_LOW 0x000 / HIGH 0x100, HIC_POST_READ 0x702, STATUS 0xF01, HIC_READ_DATA 0xF03
    st = (verb(f, 0x000, a & 0xffff), verb(f, 0x100, a >> 16), verb(f, 0x702, 0), verb(f, 0xF01, 0))
    return verb(f, 0xF03, 0), st

def exram(f, a):    # 8051 ADDRESS_LOW 0x70D / HIGH 0x70E, DATA_READ 0xF07
    st = (verb(f, 0x70D, a & 0xff), verb(f, 0x70E, (a >> 8) & 0xff))
    return verb(f, 0xF07, 0), st

ROWS = [
    ("--- output-connect overlay (XRAM, DMA-loaded; 3f3a0 is also written explicitly) ---", None, None, None),
    ("overlay word0  0x3f3a0", 'c', 0x03f3a0, 0x00800082),
    ("overlay word1  0x3f3a4", 'c', 0x03f3a4, 0x00000000),
    ("overlay word5  0x3f3b4  <== DMA-only proof", 'c', 0x03f3b4, 0x04000086),
    ("overlay word10 0x3f3c8  <== DMA-only proof", 'c', 0x03f3c8, 0x04000089),
    ("overlay PRAM   0x0b7b10 (may not be chipio-readable)", 'c', 0x0b7b10, 0x01008001),
    ("--- DSP run state ---", None, None, None),
    ("DSP_DBGCNTL    0x100e30 (EXEC bits set)", 'c', 0x100e30, 0xfff83c0f),
    ("HCI 0x100e34   (core enable)", 'c', 0x100e34, 0xf1000000),
    ("--- DSP DMA controller (Windows polls these after every download) ---", None, None, None),
    ("DSPDMAC CHNLSTART 0x110ff0 (bit n = chan n started)", 'c', 0x110ff0, None),
    ("DSPDMAC CHNLPROP  0x110ff8", 'c', 0x110ff8, None),
    ("DSPDMAC ACTIVE    0x110ffc (bit n = chan n ACTIVE; s3boun3t saw 0)", 'c', 0x110ffc, None),
    ("DSPDMAC IRQCNT    0x110f0c", 'c', 0x110f0c, None),
    ("DMACFG ch0 0x110f00 / XFRCNT ch0 0x110f08", 'c', 0x110f00, None),
    ("XFRCNT ch0 0x110f08", 'c', 0x110f08, None),
    ("DMACFG ch1 0x110f10", 'c', 0x110f10, None),
    ("XFRCNT ch1 0x110f18", 'c', 0x110f18, None),
    ("DMACFG ch2 0x110f20", 'c', 0x110f20, None),
    ("XFRCNT ch2 0x110f28", 'c', 0x110f28, None),
    ("--- chip port / clock enables ---", None, None, None),
    ("0x18b008 (port enable, want f0)", 'c', 0x18b008, 0x000000f0),
    ("0x18b030 (RMW, Windows 0x27)", 'c', 0x18b030, 0x00000027),
    ("0x18b0a4 (want c2)", 'c', 0x18b0a4, 0x000000c2),
    ("--- 8051 exram DSP stream-state bytes (Windows: non-zero = stream running) ---", None, None, None),
    ("stream 0x0c state  exram 0x07ac", 'x', 0x07ac, None),
    ("stream 0x18 state  exram 0x0824", 'x', 0x0824, None),
]

def main():
    if os.geteuid() != 0:
        sys.exit("run with sudo (the hwdep node needs root from this context)")
    n = card(); dev = f'/dev/snd/hwC{n}D1'
    ts = time.strftime('%Y%m%d-%H%M%S')
    out = [f"# AE-9 live DSP state {ts}  ({dev})"]
    # the codec runtime-suspends after playback and answers vendor verbs with
    # 0xffffffff while in D3: pin it to D0 for the duration of the snapshot
    pm = f'/sys/bus/hdaudio/devices/hdaudioC{n}D1/power/control'
    old_pm = open(pm).read().strip()
    open(pm, 'w').write('on')
    # a runtime resume re-runs the codec init (up to ~12 s); let it settle
    import subprocess
    t0 = time.time(); last = ''
    while time.time() - t0 < 20:
        d = subprocess.run(['dmesg'], capture_output=True, text=True).stdout.rstrip().split('\n')[-1]
        if 'converter bring-up done' in d or (d == last and time.time() - t0 > 3):
            break
        last = d; time.sleep(1)
    time.sleep(1)
    with open(dev, 'rb') as f:
        verb(f, 0x705, 0x00)          # SET_POWER D0 on the widget (AFG wakes with it)
        time.sleep(0.2)
        for label, kind, addr, want in ROWS:
            if kind is None:
                out.append(label); continue
            val, st = chipio(f, addr) if kind == 'c' else exram(f, addr)
            bad = any(s != 0 for s in st)
            w = 'NOSTAT' if bad else ('' if want is None else ('OK' if val == want else f'MISMATCH want {want:08x}'))
            out.append(f"  {label:58s} = 0x{val:08x}  {w}")
    open(pm, 'w').write(old_pm)   # restore runtime PM
    txt = '\n'.join(out)
    print(txt)
    p = f'dsp-state-{ts}.txt'
    open(p, 'w').write(txt + '\n'); os.chown(p, 1000, 1000)
    print(f"\nsaved {p}")

if __name__ == '__main__':
    main()
