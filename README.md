# Lumen — voice-controlled Bluetooth light

Lumen is a polished terminal controller for a Bluetooth LE RGB light. It
listens for short voice commands, fades color and brightness changes smoothly,
reconnects when the light disappears, and always keeps a typed command prompt
available as a fallback.

See [`commands.md`](commands.md) for the complete voice and keyboard command
reference.

## Features

- Voice input through your system microphone with automatic ambient-noise
  calibration.
- Visible `MIC · LISTENING`, `CAPTURED`, and `INPUT RECEIVED` feedback so you
  always know when audio was heard and exactly what was recognized.
- Clear microphone, speech-service, Bluetooth, and recognition feedback.
- Keyboard commands when you prefer typing or the microphone is unavailable.
- A full-screen Jarvis-style terminal HUD with a live connection, microphone,
  voice, light, timer, alarm, and assistant-status dashboard.
- An editable command prompt with command history, scrollable event logs, and
  `Ctrl+L` to jump back to the newest event.
- Natural phrases such as `turn the light warm`, `make it brighter`, and
  `set brightness to 40%`.
- Scene presets: sleep, night, relax, focus, reading, movie, and party.
- Brightness percentages spoken as digits or words: `brightness 20`,
  `brightness fifty`, or `twenty five percent`.
- Animated ambient effects: jungle, ocean, fireplace, rainbow, breathing, and
  candle, thunder, and police, with color-tinted breathing and pulse effects.
- Timed actions such as `sleep in 30 minutes` and `turn off in 1 hour`.
- Undo, repeat, restore, toggle, timer status, and natural power aliases.
- Visual alarms with snooze, acknowledge, dismiss, and cancel controls.
- Natural offline Piper voices with cached confirmations, adjustable volume,
  alarms-only mode, microphone echo suppression, and eSpeak fallback.
- Personality profiles (`professional`, `minimal`, `playful`, `sarcastic`, and
  `cinematic`) with configurable assistant naming.
- Local time, date, and weekday queries with personality-aware replies and a
  live clock/calendar in the HUD.
- `start listening` and `stop listening` controls for pausing and resuming voice
  input while keeping the typed HUD command prompt available.
- Smooth fades instead of abrupt color changes.
- Automatic Bluetooth scanning and reconnecting.
- `--list-mics` plus `MIC_DEVICE` support for choosing the correct input.
- Optional offline recognition through PocketSphinx when Google recognition is
  unavailable.

## Setup

Python 3.10 or newer is recommended. On Linux, install the PortAudio
development package before installing Python dependencies if PyAudio has to
build from source:

```sh
sudo apt install portaudio19-dev
```

Then install the project:

```sh
python3 -m venv .venv
. .venv/bin/activate  # bash/zsh
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For fish, activate with `source .venv/bin/activate.fish`. On Windows, use
`.venv\\Scripts\\activate`. On macOS, install PortAudio with Homebrew
(`brew install portaudio`) if the audio dependency needs to build.

Bluetooth access and a working microphone are required. Google speech
recognition still uses internet access during normal active listening. The
project installs PocketSphinx for offline standby wake-phrase detection and
offline recognition through `requirements.txt`:

```sh
python -m pip install pocketsphinx
```

## Spoken feedback (optional)

Lumen prefers **Piper**, a local neural speech engine, using the US English
`en_US-lessac-medium` voice. Install it in the same Python environment as Lumen
and download the voice once from the project root:

```sh
python -m pip install -r requirements-voice.txt
python -m piper.download_voices en_US-lessac-medium --data-dir voices
```

With a uv-managed environment you can instead install with
`uv pip install -r requirements-voice.txt`.

Both the `.onnx` model and its `.onnx.json` config must be present in `voices/`.
They are ignored by Git; a fresh clone needs this download. Voice attribution
and model details are in [`voices/README.md`](voices/README.md).

On Linux, playback uses the first available command from **pw-play**, **paplay**,
or **aplay**. CachyOS/Arch with PipeWire normally already has `pw-play`; if needed,
install it with `sudo pacman -S pipewire-audio`. On Debian/Ubuntu, `sudo apt
install alsa-utils` provides `aplay`.

Piper loads the voice once in a dedicated background process and reuses it for
later phrases. Up to 32 recent text/volume combinations (16 MiB total) are cached
in memory. The first phrase includes model startup time; repeated confirmations
skip synthesis entirely. Speech generation leaves microphone input available;
only audible playback and a 100 ms echo cooldown suppress microphone commands.

If Piper or its voice/player is unavailable, Lumen reports the reason and uses
**eSpeak NG** as a fallback. Install the fallback with `sudo pacman -S espeak-ng`
on CachyOS/Arch or `sudo apt install espeak-ng` on Debian/Ubuntu. An existing
`espeak` executable is also accepted. Everything runs offline after installation
and the voice download, with no API key.

Feedback starts **off at every launch**. Start Lumen and enable it by saying or
typing the following in Lumen's command prompt:

```text
enable spoken feedback
feedback volume 30
ocean mode
feedback status
```

Replies match what you asked for: `ocean mode` says “Ocean mode enabled.”,
`brightness 30` says “Brightness 30 percent.”, and `lights on` says “Lights on.”
Combined commands such as `ocean mode brightness 30` confirm both settings.
Use `status` for a full light-state announcement. `feedback status` shows the
selected engine and voice, and playback logs say `Speaking via Piper`.

If there is no speech, check `feedback status`: the mode must be `all` for light
confirmations, with volume above 0%. Changing volume alone does not enable
feedback. Use `only announce alarms` to restrict announcements, or `mute
feedback` to turn them off.

For another downloaded voice, set `PIPER_MODEL` to its `.onnx` path. Its matching
`.onnx.json` file must be next to it. `TTS_ENGINE=espeak` explicitly selects the
fallback; the default `TTS_ENGINE=auto` prefers Piper.

- Feedback starts **muted**, with a volume setting of **70%**. These settings
  last for the current session.
- `feedback volume 0` silences output; values up to 100 adjust only Lumen's
  speech volume, not light brightness or the system volume. Changing volume
  does not enable a muted mode.
- `only announce alarms` speaks alarm setup, snooze/dismissal, and a single
  announcement when an alarm fires. Ordinary light/timer confirmations are
  silent. The mode change itself is confirmed aloud.
- `mute feedback` immediately cancels playing and queued speech. Visual alarms
  and light controls continue to work.
- Audio is spoken in the background. While Lumen talks (plus a short echo
  cooldown), microphone commands are suppressed and captured audio is drained
  so it cannot trigger its own commands. Speak after the confirmation ends;
  keyboard commands remain available during playback.
- If Piper synthesis/playback fails, Lumen tries eSpeak for that announcement
  and subsequent speech. Use `enable spoken feedback` again to retry Piper.
  If neither engine works, the terminal and light controller remain available.
- Muting or exiting cancels pending speech; shutdown also stops the model
  process. Cancelling an in-progress synthesis may require loading the model
  again for the next uncached phrase.

## Choose a microphone

List the microphones visible to PortAudio:

```sh
python main.py --list-mics
```

Open the selected input and measure its signal before starting the light:

```sh
python main.py --test-mic
```

The test reports whether audio frames arrive and shows average/peak input
levels. Make some noise during the test so a muted or silent input is easy to
spot.

Use either the device number or part of its name:

```sh
MIC_DEVICE=2 python main.py
MIC_DEVICE="USB microphone" python main.py
```

The app uses the system default when `MIC_DEVICE` is not set. If access is
denied or no microphone is connected, Lumen stays usable with typed commands
instead of crashing.

## Configure and run

Edit the defaults in `main.py`, or configure them with environment variables:

```sh
LIGHT_ADDRESS="BE:27:65:00:4E:06" python main.py
```

Available settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LIGHT_ADDRESS` | Bluetooth address of the light | `BE:27:65:00:4E:06` |
| `LIGHT_UUID` | Writable GATT characteristic | controller UUID |
| `SWAP_RB` | Swap red and blue channels | `false` |
| `VOICE_LANGUAGE` | Speech recognition language | `en-US` |
| `MIC_DEVICE` | Device index or name fragment | system default |
| `ASSISTANT_NAME` | Name shown in the HUD and used by assistant queries | `Lumen` |
| `LUMEN_PERSONALITY` | Starting response profile; `jarvis` aliases to `cinematic` | `professional` |
| `NO_HUD` | Disable the full-screen HUD and use the scrolling terminal | unset |
| `WAKE_PHRASE` | Wake phrase used in standby mode | `hey lumen` |
| `MIC_CALIBRATION_SECONDS` | Ambient-noise calibration length | `1` |
| `MIC_PHRASE_LIMIT` | Maximum length of each phrase | `4` |
| `MIC_TEST_SECONDS` | Duration of `--test-mic` | `2` |
| `MIC_ENERGY_THRESHOLD` | Minimum voice level after calibration | `450` |
| `AUDIO_TARGET_RMS` | Recognition volume normalization target | `6000` |
| `TTS_ENGINE` | `auto` prefers Piper; `espeak` forces the fallback | `auto` |
| `PIPER_MODEL` | Path to a Piper `.onnx` model (with adjacent `.onnx.json`) | `voices/en_US-lessac-medium.onnx`, relative to the project |

Start it with:

```sh
python main.py
```

Try saying or typing:

- `red`, `blue`, `warm`, `purple`
- `turn it on`, `turn it off`
- `brighter`, `dimmer`, `half`, `full`
- `brightness 10`, `brightness 20`, `brightness 30`, `brightness 40`, `brightness 50`
- `set brightness to 40%`, `brightness fifty percent`, `brightness one hundred`
- `sleep mode`, `relax mode`, `focus mode`, `movie mode`
- `jungle mode`, `ocean mode`, `fireplace mode`, `rainbow mode`
- `breathing blue`, `pulse red`, `stop effect`, `solid mode`
- `thunder mode`, `police mode`, `lightning`, `emergency`
- `aurora mode`, `lava mode`, `rain mode`, `cloud mode`, `starlight mode`
- `matrix mode`, `warning mode`, `alert`
- `sleep in 30 minutes`, `turn off in 1 hour`, `wake up in 10 minutes`
- `dim to 10% in 20 minutes`, `ocean mode for 5 minutes`, `timer 45 seconds`
- `toggle`, `stop effect`, `cancel timer`
- `undo`, `restore previous setting`, `repeat last command`, `timer status`
- `alarm in 10 minutes`, `alarm status`, `snooze`, `acknowledge alarm`
- `enable spoken feedback`, `feedback volume 30`, `only announce alarms`, `mute feedback`
- `hud`, `dashboard`, `personality playful`, `set assistant name Jarvis`
- `what time is it`, `what is today's date`, `what day is it`
- `start listening`, `stop listening`, `resume listening`, `pause listening`
- `modes`, `effects`, `status`, `help`, `quit`

## Scene modes

Say or type a mode name, optionally with `mode` or `scene`:

| Mode | Color | Brightness | Also accepts |
| --- | --- | --- | --- |
| `sleep` | Warm | 10% | `bedtime` |
| `night` | Warm | 5% | `nightlight`, `night light` |
| `relax` | Warm | 35% | `chill` |
| `focus` | White | 100% | `study`, `work` |
| `reading` | Warm | 80% | |
| `movie` | Purple | 20% | `cinema` |
| `party` | Pink | 85% | |

These are fixed color/brightness presets with smooth transitions. Selecting a
mode turns the light on, and the terminal shows its name beside the light state.
`off` and `on` preserve your last scene and brightness.

You can adjust a scene in the same command: `sleep mode brightness 20` selects
warm lighting at 20%. The display marks modified presets as `SLEEP (ADJUSTED)`.
A direct color command, such as `blue`, switches back to manual color control.

## Brightness commands

Values are percentages, so `brightness 10` means 10% and `brightness 50` means
50%. Digits, number words, and a standalone percentage all work:

```text
brightness 10
set brightness to 50 percent
brightness twenty five
brightness one hundred
30%
fifty percent
```

Positive brightness commands turn the light on while preserving its color.
Values above 100 are capped at 100; `brightness 0` turns it off and preserves
the previous nonzero brightness for the next `on` command. Commands are applied
in order, so `sleep mode brightness 30 off` ends with the light off, ready to
restore warm lighting at 30%.

## Animated effects

Effects continuously change the RGB target while preserving the normal smooth
fade behavior:

| Effect | Atmosphere |
| --- | --- |
| `jungle` | Deep greens with firefly-like golden shimmer |
| `ocean` | Slow blue/cyan underwater movement |
| `fireplace` | Warm orange/red flame flicker |
| `rainbow` | Gradual full-spectrum color cycle |
| `breathing` | Slow blue-violet pulse |
| `pulse` | Faster pulse using the selected color |
| `candle` | Subtle amber candle flicker |
| `thunder` | Pitch-black room with two random pure-white lightning flashes per cycle |
| `police` | Alternating red/blue emergency pulse |

Aliases include `forest`, `tropical`, `sea`, `underwater`, `fire`, `flame`,
`spectrum`, `breathe`, and `flicker`. Stop an effect with:

```text
stop effect
solid mode
```

Breathing and pulse can take a color without losing the animation:

```text
breathing blue
pulse orange
```

## Timers

Timer actions run in the background while Bluetooth reconnects or the light is
being controlled:

```text
sleep in 30 minutes
turn off in 1 hour
wake up in 10 minutes
timer 45 seconds
dim to 10% in 20 minutes
ocean mode for 5 minutes
cancel timer
timer status
```

The active timer appears beside the light state and is applied automatically.
`sleep in ...` switches to the warm 10% sleep scene when it fires; `turn off
in ...` turns the light off; `wake up in ...` turns it on. Timers can schedule
scenes, effects, colors, and brightness changes. A new timer replaces the old
one.

## History and quick controls

Every successful light change keeps a short in-memory history:

```text
undo
restore previous setting
repeat last command
toggle
lights out
wake me up
```

`undo` restores the previous light state, while `repeat` replays the last
successful command. History resets when the app exits.

## Time, date, and day

Ask or type:

```text
what time is it
what's the time
what is today's date
what day is it
time
date
day
```

Lumen uses your computer's local clock and timezone. Time is shown in 12-hour
AM/PM format, dates include the month, day, and year, and day queries give the
weekday. For example: “The time is 9:42 PM.”, “Today's date is October 10,
2026.”, or “Today is Saturday.” The HUD's `LOCAL` row includes seconds and
updates the date and weekday automatically at midnight.

Answers appear in the terminal and use your selected personality. To hear them,
use `enable spoken feedback`; muted, zero-volume, and alarms-only settings
keep these answers silent. These queries work offline with typed input and
Piper/eSpeak output; microphone transcription uses your configured recognizer.
They leave light settings, timers, alarms, and undo/repeat history untouched.

## Microphone listening controls

Use these commands when you want to temporarily pause voice recognition:

```text
stop listening
pause listening
start listening
resume listening
```

`stop listening` enters standby: the microphone remains open, but normal voice
recognition is paused. Say the configured wake phrase followed by a command, for
example `Hey Lumen, turn the lights off`, to wake Lumen and run that command.
Standby sends captured phrases to Google's recognizer first, then accepts them
only when the recognized text begins with the wake phrase.

`stop microphone` is the hard privacy-off command. It closes the microphone
stream completely; resume it with the typed `start listening` command in the HUD.
Starting listening reopens the selected device and recalibrates ambient noise.
The HUD shows `READY`, `STANDBY`, `PAUSED`, or `OFFLINE` for the microphone
state. The default wake phrase is `hey lumen`; customize it with `WAKE_PHRASE`.

If Google is unavailable, standby falls back to the offline PocketSphinx package
included in `requirements.txt`.

## Terminal HUD and personalities

When Lumen runs in a terminal, it opens a full-screen dashboard. The header
updates ten times per second and shows Bluetooth connection state, microphone
activity, speech settings, light color and brightness, active timers, alarms,
local time, date, weekday, assistant name, and personality. The bottom command
field stays editable while the event log records recognition and connection
messages.

Use `PageUp`/`PageDown` to browse the event log, `Ctrl+L` to return to the latest
entry, and `Ctrl+C` to shut down. Type `hud` or `dashboard` to refresh the
dashboard after changing terminal dimensions. Set `NO_HUD=1` to use the
scrolling terminal interface instead.

Choose how confirmations sound during the current session:

```text
personality professional
personality minimal
personality playful
personality sarcastic
personality cinematic
personality jarvis
personality status
set assistant name Jarvis
what is your name
```

`professional` preserves Lumen's default confirmations. `minimal` trims common
phrases, while the other profiles add a distinct response style. Personality
settings apply to spoken confirmations and do not affect light undo/repeat
history. Set `ASSISTANT_NAME` and `LUMEN_PERSONALITY` to choose startup defaults.

Press `Ctrl+C` to stop. The light is turned off before Lumen exits.
