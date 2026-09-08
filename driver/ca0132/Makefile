# Out-of-tree build of snd-hda-codec-ca0132 from v7.0 sources (+ AE-9 quirk).
snd-hda-codec-ca0132-y := ca0132.o
obj-m += snd-hda-codec-ca0132.o
KDIR ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
