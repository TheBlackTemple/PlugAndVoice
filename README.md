# Plug and Voice

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/logo.png)

A lightweight VST3 plugin host for your microphone. Load your plugins, apply your chain, and your processed audio becomes a virtual device usable in every app, call, and game — no per-app configuration needed.

Built on [Spotify's Pedalboard](https://github.com/spotify/pedalboard). Windows only.

---

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/pav_main.png)

---

## Why this exists

Voicemeeter, Cantabile, Element, these are full audio suites. They have manuals. They have routing graphs. They break things. If all you want is to run your mic through a few VST plugins and be done with it, none of them are the right tool.

Plug and Voice does one thing: takes an input device, runs it through your VST3 chain, and outputs it as a virtual device everything else can use. That's the whole app. 

No accounts. No telemetry. No installer that touches your Windows audio stack. Copy the folder to a new machine and it works.

You get consistent audio quality across applications. Meetings, games, recordings, all of them can be routed the same way.

PAV also allows quick-loading presets on a key-bind, so you can apply a full chain of effects on the fly.

If you know what a VST plugin is and you own a few, this is for you. Think Amplitube or Guitar Rig. Knobs and signal chains, not a DAW.

If you have an microphone setup in OBS that you wanted to use everywhere else but it only works on OBS, this is also for you.

---

## Features

**Core**
- VST3 plugin host — load and chain any VST3 plugins
- **VST editor support** — open the native plugin UI for any plugin that has one, with draggable windows and multi-instance support (patched from Pedalboard, which does not support this natively)
- WASAPI recommended, MME supported
- Exclusive (private) mode — keep your mic private from other apps

**Signal chain**
- Per-plugin dB metering — see exactly what each plugin contributes
- xRun counter and driver buffer ms display
- dB gauge themes
- Native gain control at input and output *(planned)*
- Horizontal metering bars mode *(planned)*
- Full chain ms *(planned)*

**Session management**
- Session-based — one persistent state, restored on every restart
- Autosave on every engine reload — quick and often
- Preset save and load — store and recall full sessions
- Keybinds — start/stop, mute, and per-preset quick-load binds

**Stability**
- Callback watchdogs — handles device hangups and stream failures gracefully
- Solid chain management — no hot-swapping, sync approach to avoid state corruption

**Setup**
- Device picker with recommended device hints
- Configurable folder paths — `./vst3`, presets, and user data all in one place
- Run on Windows startup
- Hide on tray

---

## Requirements

- Windows 10 (tested), likely works on Windows 7+ with WASAPI
- [VB-Cable](https://vb-audio.com/Cable/) — free and well known, not bundled for legal reasons (it's our competitors, ha!)
- Python 3.14, if you are running from source

---

## Getting Started

**Install VB-Cable**, highly recommended. The app will tell you if it's missing. If you know what you are doing and already own dedicated audio devices, you can skip it.

-Download it from [VB-Cable](https://vb-audio.com/Cable/)

-Right-click the .zip file and extract it into a folder.

-Run VBCABLE_Setup_x64.exe

-Give it some time to install, it might freeze for a few seconds

-Restart your PC

### Running the exe

> *Packaged releases coming soon — check back here for a one-click download.*

-Unzip the file by right-clicking the downloaded .zip and extracting the files.

-Drop your VST3 plugins into the `vst3` folder (you can configure path in settings)

-Run PlugAndVoice.exe

#### "Windows protected your PC"

You'll likely see a Windows SmartScreen warning on first launch. This happens because we don't have a code signing certificate — Microsoft charges an annual fee of $200 for those, and we're not paying it for an open source tool.

The warning does not mean the app is malicious. It means we didn't pay Microsoft to say it isn't.

Your options:
- Click **More info → Run anyway** to proceed
- Or skip the exe entirely: you have the full source, just run `python main.py` directly and SmartScreen never enters the picture

### Running from source

**Install dependencies**
```bash
pip install -r requirements.txt
```

**Drop your VST3 plugins** into the `vst3` folder (you can configure path in settings)

**Run the app**
```bash
python main.py
```

---

## Known VSTs

These have been tested and confirmed to work. Some might not render correctly in your screen due to Windows Layout Scaling. See notes on how to fix that below. 

---

### RNNoise
*Noise suppression — free*

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/rnnoise.png)

Real-time noise suppression using a recurrent neural network. Removes most background noise, keyboard sounds, clicks and more. Set and forget.

- [Wermans-version] (https://github.com/werman/noise-suppression-for-voice)
- [Download] (https://release-assets.githubusercontent.com/github-production-release-asset/118370558/2a5daff1-44ea-4d10-ad60-583bfc959dda)


---

### DOTEC Plugins
*Various — free tier available*

Japanese plug-in manufacturer. Clean, well-behaved plugins. Very retro.

- [Homepage] (https://www.dotec-audio.com/)

- [Download DeeGate] (https://dotec-audio.com/deegate.html)

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/deegate.jpg)

- [Download DeeGain] (https://dotec-audio.com/deegain.html)

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/deegain.jpg)

---

### Tokyo Dawn Records (TDR)
*EQ, dynamics, and more — free*

Some of the best free plugins available. TDR Nova (dynamic EQ) is a standout. TDR Molotok is gorgeous. Some resolution issues, see below.

- [Homepage] (https://www.tokyodawn.net/tokyo-dawn-labs/)

- [Download TDR Nova] (https://www.tokyodawn.net/tdr-nova/)

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/Seite-1-Kopie-2-1.png)

- [Download TDR Molotok] (https://www.tokyodawn.net/tdr-molotok/)

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/TDR-Molotok-2.png)


---

### Klanghelm
*Compressors and effects, free and paid*

Gorgeous retro plugins.

- [Homepage] (https://klanghelm.com/contents/common/main.html)

- [Download DC1A] (https://klanghelm.com/contents/products/DC1A)

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/DC1A3.jpg)

- [Download MJUC jr.] (https://klanghelm.com/contents/products/MJUCjr)

![Image](https://raw.githubusercontent.com/TheBlackTempleOrg/PAV_assets/refs/heads/master/MJUCjrbig.jpg)

---

Note that most installers drop files in a non-obvious path: 

`C:\Program Files\Common Files\VST3\`

`C:\Program Files\Common Files\VST3\<vendor>\`

`C:\Program Files (x86)\Common Files\VST3\`

We default to a "vst3" folder right next to the launcher for portability and flexibility, which is non standard. You can choose to use the default vendor path instead.

---

## Known Issues

### Plugin editor windows spawn at the top-left corner

Plugin windows may open at the top-left of your screen rather than where you'd expect. This is a Pedalboard limitation — it doesn't natively support VST editor windows, and the workarounds in place don't fully control positioning. Close and reopen the specific plugin window, or do an engine restart. The rest of the app stays running fine either way. No crashes, no freezing — just spatial confusion.

Upstream fix depends on Pedalboard updating from JUCE 6 to JUCE 8. Not in our hands.

### Visual scaling on non-100% displays

If your Windows display scale is set to 125% or 150%, some VST plugins will render incorrectly or get clipped. Fix: right-click desktop → Display Settings → set Scale to 100%.

This is a JUCE 6 limitation. Some plugins handle it fine, some don't. The tested plugins listed above scale correctly.

### Private mode

Private mode (aka Exclusive mode) is a way to load a device (a microphone, for example) in a way that is managed exclusively by a given application instead of Windows. This has a couple of side effects:

1.- No other application will be able to use your original raw microphone anymore. You will have to change your input device to the VB Cable Output or whichever you decide on.

2.- Latency can change, for better or worse. Professional equipment may benefit from exclusive mode, but generic microphones might see even higher latency.

3.- Private mode can be useful for privacy-conscious people or those who simply want to keep their default microphone inaccessible.

---

## FAQ

**What's the minimum Windows version?**
Tested on Windows 10. WASAPI has been available since Vista, so it will likely run on anything from Windows 7 onward. MME is also supported if WASAPI isn't available.

**Can I hook up something other than a mic?**
Yes — any input device works. Mic is just the primary use case.

**Will there be VST2 support?**
No. Pedalboard can't support VST2 due to licensing restrictions on Steinberg's end.

**What about Mac or Linux?**
Windows only. No plans to change this.

**Why not Voicemeeter, Element, or Cantabile?**
Those tools are built for complex routing and mixing scenarios. They're powerful but they require real investment to learn, and they've been known to interfere with existing audio setups. Plug and Voice is intentionally narrow — if you need a full suite, those exist. If you just want your plugins on your mic, this is faster.

**What about Elgato Wave Link 3?**
Windows 11 only. Hard pass.

**Why Python and not C++?**
Python is fast to write and debug. This is a focused tool, not a high-performance audio engine, and Python is more than fast enough for what it does. A C++ version built directly on JUCE is worth considering — it would resolve the scaling issues and give access to newer JUCE features — but that's a much larger undertaking.

**Was AI used to build this?**
Yes, mostly on implementation. Design was authored by conniptionzs. It took longer to design and review than to implement.

**So it was vibecoded?**
As much as paying an intern counts as vibecoding.

---

## License

MIT + Commons Clause. See [LICENSE](./LICENSE) for the full terms.

Free to use, modify, and distribute. Not free to sell.
