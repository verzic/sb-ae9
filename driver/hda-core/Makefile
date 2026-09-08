# Out-of-tree build of snd-hda-core from v7.0 sources + the AE-9 posted-write fix.
# Mirrors sound/hda/core/Makefile for this kernel's config (COMPONENT=y, I915=y).
snd-hda-core-y := hda_bus_type.o bus.o device.o sysfs.o \
	regmap.o controller.o stream.o array.o hdmi_chmap.o trace.o \
	component.o i915.o
CFLAGS_trace.o := -I$(src)
obj-m += snd-hda-core.o
KDIR ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
