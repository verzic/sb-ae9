// SPDX-License-Identifier: GPL-2.0
/*
 * ae9gpio - poke the AE-9's ca0113 glue block (BAR2) while the real driver
 * owns the card. Maps BAR2 with a plain ioremap (no PCI claim), so it can
 * coexist with snd_hda_intel. Read-only unless pin= is given.
 *   dump=1              : print BAR2 0x000-0x03f, 0x300-0x33f, 0xc00-0xc1f
 *   pin=N enable=0|1    : write GPIO command (N & 0xf) | (enable << 8) to 0x320
 *   uart=0xNN           : write one byte to the UART THR (0xc00) and read LSR/RBR
 * Returns -EAGAIN from init on purpose so it never stays loaded.
 */
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/io.h>
#include <linux/delay.h>

static int dump;
module_param(dump, int, 0444);
static int pin = -1;
module_param(pin, int, 0444);
static int enable = 1;
module_param(enable, int, 0444);
static int uart = -1;
module_param(uart, int, 0444);
static int acm;
module_param(acm, int, 0444);
MODULE_PARM_DESC(acm, "1 = power the ACM (GPIO5) + Windows GPIO sequence + UART 115200 + probe F0 81 00 F7; 2 = also unlock + LED on + display 'AE-9'; 3 = UART only, no GPIO (safe on a live card; use with the driver's ACM service parked)");

static char *acmset;
module_param(acmset, charp, 0444);
MODULE_PARM_DESC(acmset, "ACM set register bits: \"reg,value,mask\" -> F0 03 03 reg value mask F7 (items: reg2&0x04=HP sel, reg7&0x01=SP sel, reg2&0x40=item3)");
static int acmget = -1;
module_param(acmget, int, 0444);
MODULE_PARM_DESC(acmget, "ACM read register N: F0 83 01 N F7 -> RX 6, [4]=value");
static char *acmtext;
module_param(acmtext, charp, 0444);
MODULE_PARM_DESC(acmtext, "ACM display text (up to 8 chars): F0 11 09 <text> 00 F7");

static int mcr = -1;
module_param(mcr, int, 0444);
static char *raw;
module_param(raw, charp, 0444);
MODULE_PARM_DESC(raw, "send one sysex frame to the ACM and print the reply: comma-separated hex bytes, e.g. raw=f0,22,02,01,00,f7 (use with acm=3)");
static int i2cdump = -1;
module_param(i2cdump, int, 0444);
MODULE_PARM_DESC(i2cdump, "command-engine I2C group to dump (0x48=ES9038 HP DAC, 0x49=SABRE9006 line DAC): regs 0x40-0x43 then 0x00-0x15");
static char *i2c;
module_param(i2c, charp, 0444);
MODULE_PARM_DESC(i2c, "command-engine I2C write: \"group,reg,val\" (same sequence as the driver's ca0113_mmio_command_set)");

/* ---- ca0113 command engine (BAR2 0x2xx/0x8xx): byte-exact copies of the
 * driver's ca0113_mmio_command_get / _set so readings are comparable ---- */
static void ce_w(void __iomem *b2, unsigned int off, unsigned int val)
{
	writel(val, b2 + off);
	readl(b2 + off);
}

static unsigned int ce_get(void __iomem *b2, unsigned int group, unsigned int reg)
{
	unsigned int val;

	ce_w(b2, 0x210, 0x0000007e);
	ce_w(b2, 0x210, 0x0000005a);
	readl(b2 + 0x210);
	ce_w(b2, 0x20c, 0x00800001);
	ce_w(b2, 0x804, group);
	ce_w(b2, 0x20c, 0x00800003);
	ce_w(b2, 0x204, reg & 0xff);
	msleep(20);
	readl(b2 + 0x860); readl(b2 + 0x854); readl(b2 + 0x840);
	ce_w(b2, 0x20c, 0x00800003);
	ce_w(b2, 0x208, 0x0000ffff);
	msleep(20);
	readl(b2 + 0x860); readl(b2 + 0x854); readl(b2 + 0x840);
	val = readl(b2 + 0x208);
	ce_w(b2, 0x20c, 0x00800002);
	ce_w(b2, 0x210, 0x00000000);
	readl(b2 + 0x210);
	return val & 0xff;
}

static void ce_set(void __iomem *b2, unsigned int group, unsigned int reg, unsigned int val)
{
	ce_w(b2, 0x210, 0x0000007e);
	ce_w(b2, 0x210, 0x0000005a);
	readl(b2 + 0x210);
	writel(0x00800005, b2 + 0x20c); readl(b2 + 0x20c);
	writel(group, b2 + 0x804); readl(b2 + 0x804);
	writel(0x00800005, b2 + 0x20c); readl(b2 + 0x20c);
	writel((reg & 0xff) | (val << 8), b2 + 0x204); readl(b2 + 0x204);
	msleep(20);
	readl(b2 + 0x860); readl(b2 + 0x854); readl(b2 + 0x840);
	writel(0x00800004, b2 + 0x20c); readl(b2 + 0x20c);
	writel(0x00000000, b2 + 0x210); readl(b2 + 0x210);
	readl(b2 + 0x210);
}
MODULE_PARM_DESC(mcr, "write this value to the UART modem-control register (BAR2+0xc10): bit0 DTR, bit1 RTS, bit2 OUT1, bit3 OUT2");


/* ---- ACM link: 16550 at BAR2+0xc00, 4-byte register stride ---- */
#define U_RBR 0xc00
#define U_THR 0xc00
#define U_DLL 0xc00
#define U_DLM 0xc04
#define U_LCR 0xc0c
#define U_LSR 0xc14
#define U_STS 0xc7c	/* Creative status: bit0 tx busy, bit1 tx ready, bit3 rx avail */

static void acm_gpio(void __iomem *b2, u16 cmd)
{
	writew(cmd, b2 + 0x320);
	readw(b2 + 0x320);
}

static int acm_tx(void __iomem *b2, const u8 *p, int n, const char *what)
{
	int i, w;
	char hex[80]; int hn = 0;

	/* drain any stale RX first, as CtxHda does */
	for (i = 0; i < 64 && (readb(b2 + U_LSR) & 0x01); i++)
		(void)readb(b2 + U_RBR);
	for (i = 0; i < n; i++) {
		for (w = 0; w < 1000 && !(readb(b2 + U_LSR) & 0x20); w++)
			udelay(10);
		writeb(p[i], b2 + U_THR);
		readb(b2 + U_LCR);
		if (hn < 70) hn += scnprintf(hex + hn, sizeof(hex) - hn, "%02x ", p[i]);
	}
	pr_info("ae9gpio: ACM TX %-10s %s\n", what, hex);
	return 0;
}

static int acm_rx(void __iomem *b2, u8 *buf, int max, int first_wait_ms, const char *what)
{
	int n = 0, w;
	char hex[80]; int hn = 0;

	for (w = 0; w < first_wait_ms * 10 && !(readb(b2 + U_LSR) & 0x01); w++)
		udelay(100);
	while (n < max) {
		if (!(readb(b2 + U_LSR) & 0x01)) {
			for (w = 0; w < 200 && !(readb(b2 + U_LSR) & 0x01); w++)	/* 20 ms inter-byte */
				udelay(100);
			if (!(readb(b2 + U_LSR) & 0x01))
				break;
		}
		buf[n] = readb(b2 + U_RBR);
		if (hn < 70) hn += scnprintf(hex + hn, sizeof(hex) - hn, "%02x ", buf[n]);
		n++;
	}
	pr_info("ae9gpio: ACM RX %-10s %d bytes: %s%s\n", what, n, n ? hex : "", n ? "" : "(nothing)");
	return n;
}

static void acm_cmd(void __iomem *b2, const u8 *p, int n, const char *what)
{
	u8 ack[16];

	acm_tx(b2, p, n, what);
	acm_rx(b2, ack, 5, 300, "ack");
}

static void acm_bringup(void __iomem *b2, int level)
{
	static const u8 probe[]  = { 0xf0, 0x81, 0x00, 0xf7 };
	static const u8 unl1[]   = { 0xf0, 0x54, 0x04, 'A', 'c', 'm', '1', 0xf7 };
	static const u8 unl2[]   = { 0xf0, 0x54, 0x04, '1', 'm', 'c', 'A', 0xf7 };
	static const u8 reg5[]   = { 0xf0, 0x03, 0x03, 0x05, 0x03, 0x03, 0xf7 };
	static const u8 led_on[] = { 0xf0, 0x22, 0x02, 0x02, 0x01, 0xf7 };
	static const u8 disp[]   = { 0xf0, 0x11, 0x09, 'A', 'E', '-', '9', 0, 0, 0, 0, 0, 0xf7 };
	u8 rx[16]; int n, w;
	u8 lcr;

	pr_info("ae9gpio: ACM bring-up: 0x300=%08x 0x320=%08x 0x100=%08x 0x304=%08x\n",
		readl(b2 + 0x300), readl(b2 + 0x320), readl(b2 + 0x100), readl(b2 + 0x304));

	/* Windows board D0 sequence */
	if (level < 3) {			/* level 3 = UART only: NO GPIO (pin-0 pulse RESETS the DACs) */
		acm_gpio(b2, 0x0105);		/* GPIO 5 = 1 : ACM power on */
		acm_gpio(b2, 0x0000); msleep(1); acm_gpio(b2, 0x0100);	/* anti-pop pulse on pin 0 */
		acm_gpio(b2, 0x0002);		/* pin 2 = 0 */
		acm_gpio(b2, 0x0103);		/* pin 3 = 1 */
	}
	msleep(50);
	pr_info("ae9gpio: after GPIO seq: 0x300=%08x 0x320=%08x\n", readl(b2 + 0x300), readl(b2 + 0x320));

	/* set baud 115200: wait tx idle, DLAB, div=13 */
	for (w = 0; w < 1000 && (readb(b2 + U_STS) & 0x01); w++)
		udelay(320);
	lcr = readb(b2 + U_LCR);
	writeb(lcr | 0x80, b2 + U_LCR); readb(b2 + U_LCR);
	writeb(0x0d, b2 + U_DLL); readb(b2 + U_DLL);
	writeb(0x00, b2 + U_DLM); readb(b2 + U_DLM);
	writeb(lcr & ~0x80, b2 + U_LCR); readb(b2 + U_LCR);
	pr_info("ae9gpio: UART 115200: LCR=0x%02x STS(0xc7c)=0x%02x LSR=0x%02x\n",
		readb(b2 + U_LCR), readb(b2 + U_STS), readb(b2 + U_LSR));

	/* probe, as the 500 ms detect poll does; the ACM may need a moment after power */
	msleep(300);
	acm_tx(b2, probe, sizeof(probe), "probe");
	n = acm_rx(b2, rx, 9, 500, "probe");
	if (n >= 8)
		pr_info("ae9gpio: ACM present=%u firmware=0x%02x%02x%02x%02x\n", rx[3], rx[7], rx[6], rx[5], rx[4]);
	if (level < 2)
		return;
	if (n < 4) {
		pr_info("ae9gpio: no probe reply; retrying once after 1 s\n");
		msleep(1000);
		acm_tx(b2, probe, sizeof(probe), "probe");
		n = acm_rx(b2, rx, 9, 500, "probe");
		if (n < 4)
			return;
	}
	acm_cmd(b2, unl1, sizeof(unl1), "unlock1");
	acm_cmd(b2, unl2, sizeof(unl2), "unlock2");
	acm_cmd(b2, reg5, sizeof(reg5), "reg5");
	acm_cmd(b2, led_on, sizeof(led_on), "LED-on");
	acm_cmd(b2, disp, sizeof(disp), "display");
}

static int __init ae9gpio_init(void)
{
	struct pci_dev *pdev = pci_get_domain_bus_and_slot(0, 0x0a, PCI_DEVFN(0, 0));
	resource_size_t base, len;
	void __iomem *b2;
	unsigned int i;

	if (!pdev) { pr_err("ae9gpio: 0a:00.0 not found\n"); return -ENODEV; }
	base = pci_resource_start(pdev, 2); len = pci_resource_len(pdev, 2);
	pci_dev_put(pdev);
	if (!base) return -ENODEV;
	b2 = ioremap(base, len);
	if (!b2) return -ENOMEM;
	pr_info("ae9gpio: BAR2 %pa (%llu bytes) mapped\n", &base, (u64)len);

	if (dump) {
		static const unsigned int rng[][2] = { {0x000, 0x040}, {0x300, 0x340}, {0xc00, 0xc20} };
		unsigned int r;

		for (r = 0; r < 3; r++)
			for (i = rng[r][0]; i < rng[r][1]; i += 16)
				pr_info("ae9gpio: %03x: %08x %08x %08x %08x\n", i,
					readl(b2 + i), readl(b2 + i + 4), readl(b2 + i + 8), readl(b2 + i + 12));
	}
	if (pin >= 0) {
		u16 cmd = (pin & 0xf) | ((enable ? 1 : 0) << 8);

		pr_info("ae9gpio: GPIO cmd 0x%04x -> 0x320 (pin %d %s); 0x320 before=0x%08x\n",
			cmd, pin, enable ? "ON" : "OFF", readl(b2 + 0x320));
		writew(cmd, b2 + 0x320);
		readw(b2 + 0x320);
		msleep(50);
		pr_info("ae9gpio: 0x320 after=0x%08x 0x300..30c: %08x %08x %08x %08x\n", readl(b2 + 0x320),
			readl(b2 + 0x300), readl(b2 + 0x304), readl(b2 + 0x308), readl(b2 + 0x30c));
	}
	/* 16550 at 4-byte register stride: 0xc00 RBR/THR, 0xc04 IER, 0xc08 IIR/FCR,
	 * 0xc0c LCR, 0xc10 MCR, 0xc14 LSR, 0xc18 MSR, 0xc1c SCR */
	if (acm)
		acm_bringup(b2, acm);

	if (acmset && *acmset) {
		unsigned int r, v, m;

		if (sscanf(acmset, "%i,%i,%i", &r, &v, &m) == 3) {
			u8 cmd[7] = { 0xf0, 0x03, 0x03, r, v, m, 0xf7 };
			acm_cmd(b2, cmd, sizeof(cmd), "setreg");
		} else {
			pr_err("ae9gpio: acmset wants reg,value,mask\n");
		}
	}
	if (acmget >= 0) {
		u8 cmd[5] = { 0xf0, 0x83, 0x01, acmget, 0xf7 };
		u8 rx[16]; int n;

		acm_tx(b2, cmd, sizeof(cmd), "getreg");
		n = acm_rx(b2, rx, 6, 300, "getreg");
		if (n >= 5)
			pr_info("ae9gpio: ACM reg %d = 0x%02x\n", acmget, rx[4]);
	}
	if (acmtext && *acmtext) {
		u8 cmd[13] = { 0xf0, 0x11, 0x09, 0, 0, 0, 0, 0, 0, 0, 0, 0xf7 };
		int k;

		for (k = 0; k < 8 && acmtext[k]; k++)
			cmd[3 + k] = acmtext[k];
		acm_cmd(b2, cmd, sizeof(cmd), "display");
	}

	if (raw && *raw) {
		u8 f[32]; int n = 0; char *lst = raw, *tok;
		u8 rx[32]; int got;

		while ((tok = strsep(&lst, ",")) != NULL && n < 32) {
			unsigned int b;
			if (*tok && sscanf(tok, "%x", &b) == 1) f[n++] = b;
		}
		if (n) {
			acm_tx(b2, f, n, "raw");
			got = acm_rx(b2, rx, sizeof(rx), 300, "raw");
			pr_info("ae9gpio: raw reply %d bytes: %*ph\n", got, got > 0 ? got : 0, rx);
		}
	}
	if (i2cdump >= 0) {
		static const unsigned int regs[] = { 0x40, 0x41, 0x42, 0x43,
			0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a,
			0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15 };
		char line[200];
		int pos = 0, k;

		for (k = 0; k < ARRAY_SIZE(regs); k++)
			pos += scnprintf(line + pos, sizeof(line) - pos, " %02x=%02x",
					 regs[k], ce_get(b2, i2cdump, regs[k]));
		pr_info("ae9gpio: I2C group 0x%02x:%s\n", i2cdump, line);
	}
	if (i2c && *i2c) {
		char *list = i2c, *item;

		/* one or more "group,reg,val" triples separated by ':' */
		while ((item = strsep(&list, ":")) != NULL) {
			unsigned int g, r, v;

			if (!*item)
				continue;
			if (sscanf(item, "%i,%i,%i", &g, &r, &v) == 3) {
				unsigned int before = ce_get(b2, g, r);

				ce_set(b2, g, r, v);
				pr_info("ae9gpio: I2C group 0x%02x reg 0x%02x: %02x -> wrote %02x -> reads %02x\n",
					g, r, before, v, ce_get(b2, g, r));
			} else {
				pr_err("ae9gpio: i2c item '%s' wants group,reg,val\n", item);
			}
		}
	}
	if (mcr >= 0) {
		pr_info("ae9gpio: MCR 0x%02x -> 0x%02x\n", readb(b2 + 0xc10), mcr & 0xff);
		writeb(mcr & 0xff, b2 + 0xc10);
		readb(b2 + 0xc10);
		msleep(200);
		pr_info("ae9gpio: after MCR: MCR=0x%02x MSR=0x%02x LSR=0x%02x 0x300=%08x 0x320=%08x\n",
			readb(b2 + 0xc10), readb(b2 + 0xc18), readb(b2 + 0xc14), readl(b2 + 0x300), readl(b2 + 0x320));
	}
	if (uart >= 0) {
		pr_info("ae9gpio: UART before: LSR=0x%02x MSR=0x%02x IER=0x%02x LCR=0x%02x MCR=0x%02x\n",
			readb(b2 + 0xc14), readb(b2 + 0xc18), readb(b2 + 0xc04), readb(b2 + 0xc0c), readb(b2 + 0xc10));
		writeb(uart & 0xff, b2 + 0xc00);
		readb(b2 + 0xc0c);
		for (i = 0; i < 20; i++) {
			msleep(10);
			if (readb(b2 + 0xc14) & 0x01)
				break;
		}
		pr_info("ae9gpio: UART after %u0 ms: LSR=0x%02x%s RBR=0x%02x\n", i + 1, readb(b2 + 0xc14),
			(readb(b2 + 0xc14) & 0x01) ? " DATA-READY" : "", readb(b2 + 0xc00));
	}
	iounmap(b2);
	return -EAGAIN;
}
module_init(ae9gpio_init);
MODULE_DESCRIPTION("AE-9 ca0113 glue-block poke tool");
MODULE_LICENSE("GPL");
