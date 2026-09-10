-- Sound Blaster AE-9 (sb-ae9 driver), WirePlumber 0.4.x (Lua config).
-- The driver brings up the card's playback path only. Opening the AE-9's capture path
-- hard-locks the machine (2026-09: Discord fell back to "Default" input when a USB mic
-- was unplugged, opened this capture and the box froze). Hide every capture node of the
-- card so no application can select it, until capture is actually brought up.
-- Matched by the mixer (codec) name the driver sets, so other cards are untouched.
table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "alsa_input.pci-*" },
      { "alsa.mixer_name", "equals", "Creative Sound Blaster AE-9" },
    },
  },
  apply_properties = {
    ["node.disabled"] = true,
  },
})
