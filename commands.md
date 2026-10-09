# Lumen command reference

Lumen accepts the same commands from the microphone and the keyboard prompt.
Voice phrases do not need to match these examples exactly; the parser accepts
natural variations such as `turn the lights off`, `make it brighter`, and
`sleep mode`.

## Quick start

```text
on
off
toggle
red
blue
warm
brightness 50
sleep mode
ocean mode
status
help
quit
```

## Power controls

| Command | What it does |
| --- | --- |
| `on` | Turn the light on and restore the last color, brightness, or effect |
| `off` | Turn the light off while preserving the current settings |
| `toggle` | Switch between on and off |
| `turn on` | Alias for `on` |
| `turn off` | Alias for `off` |
| `turn the light on` | Alias for `on` |
| `turn the light off` | Alias for `off` |
| `turn the lights on` | Alias for `on` |
| `turn the lights off` | Alias for `off` |
| `lights on` | Alias for `on` |
| `lights off` | Alias for `off` |
| `lights out` | Turn the light off |
| `kill the lights` | Turn the light off |
| `wake up` | Turn the light on |
| `wake me up` | Turn the light on |

## Colors

```text
red
green
blue
light blue
white
yellow
orange
purple
pink
cyan
warm
```

Color aliases:

| Alias | Color |
| --- | --- |
| `violet` | Purple |
| `magenta` | Pink |
| `aqua` | Cyan |
| `gold` | Yellow |
| `amber` | Orange |
| `sky blue` | Light blue |
| `ice blue` | Light blue |

`light-blue` and `lightblue` are also accepted.

Selecting a direct color exits animated effects and named scenes.

## Brightness

Brightness is expressed as a percentage from 0 to 100. Values above 100 are
capped at 100.

```text
brightness 10
brightness 20
brightness 30
brightness 40
brightness 50
brightness 75%
set brightness to 40%
brightness fifty
brightness twenty five percent
brightness one hundred
30%
fifty percent
```

Relative brightness commands:

```text
brighter
brighten
bright
make it brighter
dimmer
dim
darker
make it dimmer
```

Preset brightness commands:

```text
half
full
full brightness
minimum brightness
```

`brightness 0` turns the light off and keeps the previous nonzero brightness
for the next `on` command.

## Scenes

Scenes are fixed color and brightness presets. Every scene turns the light on.

| Scene | Color | Brightness | Aliases |
| --- | --- | ---: | --- |
| `sleep` | Warm | 10% | `bedtime` |
| `night` | Warm | 5% | `nightlight`, `night light` |
| `relax` | Warm | 35% | `chill`, `make it cozy`, `make it chill` |
| `focus` | White | 100% | `study`, `work` |
| `reading` | Warm | 80% | — |
| `movie` | Purple | 20% | `cinema` |
| `party` | Pink | 85% | — |

Examples:

```text
sleep
sleep mode
night light
focus scene
movie mode
make it cozy
sleep mode brightness 20
```

Changing the brightness of a scene keeps the scene name and displays it as
`SCENE (ADJUSTED)`.

## Animated effects

Effects continuously change the RGB target while the normal smooth fade stays
active.

| Effect | Description | Aliases |
| --- | --- | --- |
| `jungle` | Green forest tones with golden firefly shimmer | `forest`, `tropical` |
| `ocean` | Slow blue/cyan underwater movement | `sea`, `underwater` |
| `fireplace` | Warm orange/red flame flicker | `fire`, `flame` |
| `rainbow` | Slow full-spectrum color cycle | `spectrum`, `color cycle` |
| `breathing` | Slow brightness pulse | `breathe` |
| `pulse` | Faster brightness pulse | `pulsing` |
| `candle` | Subtle amber flicker | `flicker` |
| `thunder` | Pitch-black room with two random pure-white lightning flashes per cycle | `storm`, `lightning`, `thunderstorm` |
| `police` | Alternating red/blue emergency pulse | `emergency`, `cop`, `sirens` |
| `aurora` | Slow green/cyan/purple northern-lights drift | `northern`, `northern lights` |
| `lava` | Molten red/orange heat movement | `molten` |
| `rain` | Deep blue rain with bright droplet shimmer | `drizzle`, `stormrain` |
| `cloud` | Slow silver-gray cloud drift | `clouds` |
| `starlight` | Dark sky with random star twinkles | `stars` |
| `matrix` | Dark green hacker-style pulse | `hacker` |
| `warning` | Amber/orange warning pulse | `alert`, `caution` |

Examples:

```text
jungle mode
fireplace mode
rainbow
breathing blue
pulse red
thunder mode
police mode
aurora mode
lava mode
rain mode
cloud mode
starlight mode
matrix mode
warning mode
ocean mode brightness 40
```

`breathing` and `pulse` accept a color. Other effects use their own color
palette.

Stop an effect and return to the last solid light setting:

```text
stop effect
stop effects
stop animation
solid mode
```

## Timers

Timers run in the background and do not block Bluetooth reconnects or voice
input. A new timer replaces the previous timer.

Power timers:

```text
sleep in 30 minutes
turn off in 1 hour
wake up in 10 minutes
timer 45 seconds
set timer for half an hour
```

Scene, effect, color, and brightness timers:

```text
ocean mode for 5 minutes
fireplace for 20 minutes
blue in 10 minutes
dim to 10% in 20 minutes
brightness 50 in 1 hour
```

`for` starts the action immediately and turns the light off when the timer
ends. `in` waits and then applies the action.

Timer controls:

```text
timer
timer status
show timer
time remaining
cancel timer
clear timer
stop timer
```

The active timer appears beside the light state with a countdown.

## Alarms

Alarms are visual and work without an extra sound dependency. When an alarm
fires, the light takes over with a repeating red/white alert pattern until it
is acknowledged or dismissed.

```text
alarm in 10 minutes
set an alarm in 1 hour
wake me with an alarm in 30 seconds
alarm status
snooze
snooze for 5 minutes
acknowledge alarm
dismiss alarm
stop alarm
cancel alarm
```

The alarm indicator appears beside the light state. While it is active, the
alarm pattern takes priority over scenes and effects. Acknowledge or dismiss it
to restore the previous light state.

## Spoken feedback

Install `espeak-ng` first (CachyOS/Arch: `sudo pacman -S espeak-ng`;
Debian/Ubuntu: `sudo apt install espeak-ng`). Speech output is offline. See the
[README setup instructions](README.md#spoken-feedback-optional).

| Command | What it does |
| --- | --- |
| `enable spoken feedback` | Speak short light, timer, and alarm confirmations |
| `enable feedback`, `unmute feedback` | Aliases for enabling all confirmations |
| `feedback volume 30` | Set speech volume to 30%; accepts values 0–100 |
| `set feedback volume to thirty percent` | Spoken-number form of the volume command |
| `only announce alarms` | Speak only alarm updates and announcements |
| `feedback alarms only` | Alias for alarms-only feedback |
| `mute feedback` | Stop playing/queued speech and disable announcements |
| `disable spoken feedback`, `disable feedback` | Aliases for mute |
| `feedback status` | Show the speech mode and volume in the terminal |

Examples of spoken confirmations:

- `ocean mode` → “Ocean mode enabled. Brightness 100 percent.” (at full brightness)
- `alarm in ten minutes` → “Alarm set for 10 minutes.”
- When the alarm fires → “Your alarm is ringing. Say acknowledge alarm or snooze.”

Feedback starts muted at a configured volume of 70% on each launch. Setting
volume does not enable feedback; volume 0 is silent. Alarms-only mode includes
setting, snoozing, and dismissing alarms, plus one announcement at each alarm
activation. It does not repeatedly speak over the visual alarm.

The microphone ignores audio during Lumen's speech and a short echo cooldown;
wait for the confirmation to finish before speaking again. You can type commands
at any time. Speech settings are separate from the light's brightness and
undo/repeat history. Feedback controls apply immediately and cannot be scheduled
with a timer.

## History and repeat

Lumen keeps up to 20 successful light changes in memory while it is running.

```text
undo
restore
restore previous setting
repeat
repeat last command
again
```

`undo` restores the previous light state. `repeat` runs the last successful
light command again. History resets when the app exits.

## Information and help

```text
help
commands
modes
scenes
effects
list effects
status
show status
show current state
```

## Exit commands

These stop the program cleanly and turn the light off:

```text
quit
exit
stop
goodbye
shutdown
```

Use `stop effect` to stop only an animation; the exact command `stop` exits
Lumen.
