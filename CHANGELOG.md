Changelog — v0.1.0-alpha
Engine & Audio

WASAPI exclusive (private) mode with proper stream handling and fix prompts
Dual watchdog system — one for stream death, one for hanging callbacks
Restart attempt monitoring
Refactored callback logic for stability
Latency reporting improvements

Plugin Chain

Persistent chain — saves and restores across sessions
Autosave on every engine reload
dB metering per plugin, with theme support for gauge visuals
Gauge theme settings now persist between sessions
VST editor window position fix

Session & Presets

Preset save and load with overwrite confirmation
Fixed preset save, load, and new preset flow
Combo box UX fixes

Hotkeys & Keybinds

Keybind system for start/stop, mute, and per-preset quick-load

Settings & Paths

Path settings panel — configurable VST3, preset, and user data folders
Default user data path updated
Path fix on load
Windows Autorun
On tray behavior

UI & Branding

Full UI pass — labels, warnings, mute behavior, style cleanup
dB gauge themes with visual differentiation
Device picker with recommended device hints
Logo and branding added
Removed headless mode (may return)

Publishing & Legal

MIT + Commons Clause license
README with setup guide, known issues, and FAQ