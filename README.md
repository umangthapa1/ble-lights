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
- Offline spoken confirmations with adjustable volume, alarms-only mode, and
  microphone echo suppression.
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

Bluetooth access, a working microphone, and internet access for Google's
online speech recognizer are required. To add an offline fallback, install
PocketSphinx in the virtual environment:

```sh
python -m pip install pocketsphinx
```

## Spoken feedback (optional)

Install **eSpeak NG** for offline speech output. On CachyOS/Arch:

```sh
sudo pacman -S espeak-ng
```

On Debian/Ubuntu: `sudo apt install espeak-ng`. Lumen uses `espeak-ng` from
your PATH, or `espeak` if that is already installed. No additional Python
package, API key, or internet connection is needed for speech output.

Start Lumen and say or type:

```text
enable spoken feedback
feedback volume 30
ocean mode
alarm in ten minutes
only announce alarms
mute feedback
feedback status
```

You will hear short confirmations such as “Ocean mode enabled. Brightness
100 percent.” and “Alarm set for 10 minutes.”

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
- If the speech engine is missing or fails, Lumen shows a terminal message and
  keeps the light controller running. After installing the engine, use
  `enable spoken feedback` again to retry.

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
| `MIC_CALIBRATION_SECONDS` | Ambient-noise calibration length | `1` |
| `MIC_PHRASE_LIMIT` | Maximum length of each phrase | `4` |
| `MIC_TEST_SECONDS` | Duration of `--test-mic` | `2` |
| `MIC_ENERGY_THRESHOLD` | Minimum voice level after calibration | `450` |
| `AUDIO_TARGET_RMS` | Recognition volume normalization target | `6000` |

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

Press `Ctrl+C` to stop. The light is turned off before Lumen exits.
