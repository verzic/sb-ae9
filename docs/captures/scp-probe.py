#!/usr/bin/env python3
"""
No-reboot test of the SCP reply hypothesis, done right.

The AE-9 codec runtime-suspends after playback. Raw hwdep verbs do NOT trigger a
runtime resume, so against a suspended codec every verb reads 0xffffffff (this is
what fooled the first version of this script). So: force controller + codec power
to 'on', trigger a real resume, CONFIRM with a sanity verb (AFG vendor id must be
0x11020011), and only then send the MASTERCONTROL GET and poll the reply queue the
Windows way. If the sanity verb fails, userspace cannot reach the DSP on this boot
and the only reliable test is the rebuilt in-driver logging (one reboot).
  sudo python3 ~/sb-ae9-linux/capture/scp-probe.py
"""
import fcntl, struct, glob, os, re, sys, time, subprocess
IOC = (3 << 30) | (8 << 16) | (ord('H') << 8) | 0x11
QUEUE_EMPTY = 3
BAD = 0xffffffff

def card():
    for p in glob.glob('/proc/asound/card*/id'):
        if 'Creative' in open(p).read():
            return int(re.search(r'card(\d+)', p).group(1))
    sys.exit("no Creative card is up")

def verb(f, nid, v, p):
    b = bytearray(struct.pack('II', (nid << 20) | (v << 8) | p, 0))
    fcntl.ioctl(f, IOC, b)
    return struct.unpack('II', b)[1]

def rstat(path):
    try: return open(path + '/power/runtime_status').read().strip()
    except Exception: return '?'

def main():
    if os.geteuid() != 0: sys.exit("run with sudo")
    n = card(); dev = f'/dev/snd/hwC{n}D1'
    out = f"scp-probe-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    log = open(out, 'w'); os.chown(out, 1000, 1000)
    def P(*a):
        s = ' '.join(str(x) for x in a); print(s); log.write(s + '\n'); log.flush()
    P(f"# scp-probe {out}  ({dev})")

    ctl_pci = '/sys/bus/pci/devices/0000:0a:00.0'
    ctl_cdc = f'/sys/bus/hdaudio/devices/hdaudioC{n}D1'
    saved = {}
    for base in (ctl_pci, ctl_cdc):
        try:
            saved[base] = open(base + '/power/control').read().strip()
            open(base + '/power/control', 'w').write('on')
        except Exception as e:
            P(f"  warn: cannot pin {base}: {e}")
    # a proc read forces the codec through a real driver-side resume
    subprocess.run(['cat', f'/proc/asound/card{n}/codec#1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 8:
        if rstat(ctl_pci) == 'active' and rstat(ctl_cdc) == 'active': break
        time.sleep(0.2)
    P(f"  power: controller={rstat(ctl_pci)} codec={rstat(ctl_cdc)}")

    try:
        with open(dev, 'rb') as f:
            vid = verb(f, 0x01, 0xf00, 0x00)      # sanity: AFG vendor id
            P(f"  sanity AFG vendor-id = 0x{vid:08x}  (want 0x11020011)")
            if vid == BAD or vid != 0x11020011:
                P("\n=== VERDICT ===")
                P("  hwdep cannot reach the codec on this boot (verbs read back garbage even at D0).")
                P("  Userspace probing is not possible here. Reboot into the rebuilt driver instead;")
                P("  its init-time logging (AE-9: MC state N GETi -> value, overlay readback) is the")
                P("  reliable test and runs while the codec is guaranteed powered.")
                return

            # drain the response queue, counting only genuine (non-0xffffffff) reads
            drained = 0
            for _ in range(16):
                verb(f, 0x16, 0x702, 0)
                st = verb(f, 0x16, 0xf01, 0)
                if st == QUEUE_EMPTY or st == BAD: break
                verb(f, 0x16, 0xf02, 0); drained += 1
            P(f"  drained {drained} stale reply word(s)")

            def probe(src):
                hdr = (0x0c << 17) | (1 << 16) | (src << 8) | 0x80
                P(f"\n=== SCP 0x80 req 0x0c GET src=0x{src:02x} (header 0x{hdr:08x}) ===")
                lo = verb(f, 0x16, 0x000, hdr & 0xffff)
                hi = verb(f, 0x16, 0x100, hdr >> 16)
                stt = verb(f, 0x16, 0xf01, 0)
                P(f"  send status LOW/HIGH/STATUS = 0x{lo:x}/0x{hi:x}/0x{stt:x}  (0=ok, 2=cmd-queue-full)")
                if BAD in (lo, hi, stt):
                    P("  send itself returned garbage -> link not carrying verbs; abort")
                    return None
                t = time.time()
                while time.time() - t < 0.25:
                    verb(f, 0x16, 0x702, 0)
                    st = verb(f, 0x16, 0xf01, 0)
                    if st == BAD: P("  status read garbage during poll; abort"); return None
                    if st != QUEUE_EMPTY:
                        h = verb(f, 0x16, 0xf02, 0)
                        dt = (time.time() - t) * 1000
                        P(f"  REPLY after {dt:.1f} ms: header=0x{h:08x} "
                          f"(target=0x{h&0xff:02x} src=0x{(h>>8)&0xff:02x} nwords={(h>>27)&0x1f})")
                        return h
                    time.sleep(0.0005)
                P("  NO REPLY within 250 ms (queue stayed empty)")
                return None

            r0 = probe(0x00)     # loader context (Windows uses this for the go-sequence)
            time.sleep(0.05)
            r1 = probe(0x20)     # the Linux driver's normal source id
            P("\n=== VERDICT ===")
            def ok(h): return h is not None and h != BAD and (h & 0xff) == 0x80
            if ok(r0):
                P("  src 0x00 ANSWERED with a valid MASTERCONTROL reply -> the DSP queues loader-")
                P("  context replies and the driver simply never polled for them. The polling fix")
                P("  in the rebuilt driver is the correct fix.")
            elif ok(r1):
                P("  only src 0x20 answered -> DSP alive but rejects the loader-context (src 0x00)")
                P("  GET; the go-sequence's source id or header needs adjustment.")
            else:
                P("  NO valid reply to either src -> the DSP is not answering these SCP GETs at all;")
                P("  the problem is upstream of the go-sequence (DSP run-state / SCP path), not the")
                P("  reply-polling. Do NOT spend a reboot on the polling fix expecting sound.")
    finally:
        for base, v in saved.items():
            try: open(base + '/power/control', 'w').write(v)
            except Exception: pass
        P(f"\nsaved {out}")

if __name__ == '__main__':
    main()
