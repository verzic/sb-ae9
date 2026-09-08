-- Sound Blaster AE-9 (sb-ae9 driver), WirePlumber 0.4.x (Lua config).
-- Keep WirePlumber's ACP off the card's hardware mixer: the stock headphone path
-- mutes 'Front' (this card's only DAC output) and rewrites 'Output Select', which
-- drives the ACM's headphone/speaker relay. Routing = ae9-defaults.sh, master
-- volume = the ACM knob; PipeWire does per-stream volume in software.
-- Matched by PCI vendor/product (0x1102:0x0010 = AE-9), not by bus address.
table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "device.name", "matches", "alsa_card.pci-*" },
      { "device.vendor.id", "equals", "0x1102" },
      { "device.product.id", "equals", "0x0010" },
    },
  },
  apply_properties = {
    ["api.alsa.soft-mixer"] = true,
    ["api.acp.auto-port"] = false,
    ["api.acp.auto-profile"] = false,   -- analog profiles read "unavailable" (jack-only ports); ae9-defaults selects it
  },
})
