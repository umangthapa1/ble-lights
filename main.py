"""Lumen - a voice and keyboard controlled Bluetooth LED controller.

The app intentionally stays dependency-light: SpeechRecognition handles the
microphone and Bleak handles Bluetooth, while the terminal UI uses ANSI escape
codes from the standard library.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import colorsys
import copy
from contextlib import contextmanager
import math
import os
import random
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import speech_recognition as sr
from bleak import BleakClient, BleakScanner

from speech_feedback import DEFAULT_FEEDBACK_VOLUME, FeedbackFilteredStream, PlaybackGate, PlaybackInterrupted, SpokenFeedback
from terminal_hud import TerminalHUD


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

ADDRESS = os.getenv("LIGHT_ADDRESS", "BE:27:65:00:4E:06")
UUID = os.getenv("LIGHT_UUID", "0000fff3-0000-1000-8000-00805f9b34fb")

BRIGHT_STEP = 0.2
FADE_SPEED = 0.35
INTERVAL = 0.04
SWAP_RB = os.getenv("SWAP_RB", "false").lower() in {"1", "true", "yes", "on"}
LANGUAGE = os.getenv("VOICE_LANGUAGE", "en-US")
MIC_DEVICE = os.getenv("MIC_DEVICE", "").strip()


def environment_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


MIC_CALIBRATION_SECONDS = environment_float("MIC_CALIBRATION_SECONDS", 1.0)
MIC_PHRASE_LIMIT = environment_float("MIC_PHRASE_LIMIT", 4.0)
MIC_TEST_SECONDS = environment_float("MIC_TEST_SECONDS", 2.0)
MIC_ENERGY_THRESHOLD = environment_float("MIC_ENERGY_THRESHOLD", 450.0)
AUDIO_TARGET_RMS = environment_float("AUDIO_TARGET_RMS", 6000.0)

COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "yellow": (255, 200, 0),
    "orange": (255, 90, 0),
    "purple": (150, 0, 255),
    "pink": (255, 40, 140),
    "cyan": (0, 255, 255),
    "light_blue": (120, 200, 255),
    "warm": (255, 140, 40),
}

COLOR_ALIASES = {
    "violet": "purple",
    "magenta": "pink",
    "aqua": "cyan",
    "gold": "yellow",
    "amber": "orange",
    "lightblue": "light_blue",
    "skyblue": "light_blue",
    "iceblue": "light_blue",
}

SCENES: dict[str, tuple[str, float]] = {
    "sleep": ("warm", 0.10),
    "night": ("warm", 0.05),
    "relax": ("warm", 0.35),
    "focus": ("white", 1.0),
    "reading": ("warm", 0.80),
    "movie": ("purple", 0.20),
    "party": ("pink", 0.85),
}

SCENE_ALIASES = {
    "bedtime": "sleep",
    "nightlight": "night",
    "chill": "relax",
    "study": "focus",
    "work": "focus",
    "cinema": "movie",
}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

EFFECTS = {
    "jungle",
    "ocean",
    "fireplace",
    "rainbow",
    "breathing",
    "pulse",
    "candle",
    "thunder",
    "police",
    "aurora",
    "lava",
    "rain",
    "cloud",
    "starlight",
    "matrix",
    "warning",
}

EFFECT_ALIASES = {
    "forest": "jungle",
    "tropical": "jungle",
    "sea": "ocean",
    "underwater": "ocean",
    "fire": "fireplace",
    "flame": "fireplace",
    "spectrum": "rainbow",
    "breathe": "breathing",
    "pulsing": "pulse",
    "flicker": "candle",
    "storm": "thunder",
    "lightning": "thunder",
    "thunderstorm": "thunder",
    "emergency": "police",
    "cop": "police",
    "sirens": "police",
    "northern": "aurora",
    "northernlights": "aurora",
    "molten": "lava",
    "drizzle": "rain",
    "stormrain": "rain",
    "clouds": "cloud",
    "stars": "starlight",
    "hacker": "matrix",
    "alert": "warning",
    "caution": "warning",
}

THUNDER_CYCLE_SECONDS = 10.0
THUNDER_FLASH_SECONDS = 0.12
_THUNDER_SEED = random.SystemRandom().getrandbits(64)

COMMAND_ALIASES = {
    "lights out": "off",
    "kill the lights": "off",
    "turn off": "off",
    "turn on": "on",
    "turn the light off": "off",
    "turn the light on": "on",
    "turn the lights off": "off",
    "turn the lights on": "on",
    "lights off": "off",
    "lights on": "on",
    "wake up": "on",
    "wake me up": "on",
    "make it cozy": "relax",
    "make it chill": "relax",
    "full brightness": "full",
    "minimum brightness": "brightness 1",
    "toggle light": "toggle",
    "toggle the light": "toggle",
    "toggle the lights": "toggle",
    "show current state": "status",
    "show timer": "timer status",
    "color cycle": "rainbow",
    "color cycle mode": "rainbow",
    "northern lights": "aurora",
    "northern lights mode": "aurora",
    "visual alarm": "alarm",
}

LIGHT_FIELDS = ("on", "color", "color_name", "brightness", "mode", "effect", "effect_color", "timer", "alarm")

PERSONALITIES = {
    "professional": "",
    "minimal": "",
    "playful": "Done — ",
    "sarcastic": "Naturally. ",
    "cinematic": "As you wish. ",
}

PERSONALITY_ALIASES = {
    "default": "professional",
    "formal": "professional",
    "fun": "playful",
    "jarvis": "cinematic",
    "dramatic": "cinematic",
}

DEFAULT_ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Lumen").strip() or "Lumen"
DEFAULT_PERSONALITY = PERSONALITY_ALIASES.get(os.getenv("LUMEN_PERSONALITY", "professional").strip().lower(), "professional")


@dataclass
class CommandResult:
    """The user-facing result of parsing one command."""

    changed: bool
    summary: str
    should_exit: bool = False
    spoken: str | None = None
    category: str = "command"


class Console:
    """Small, readable terminal UI with a plain-text fallback."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"

    def __init__(self, dashboard: TerminalHUD | None = None) -> None:
        self.enabled = sys.stdout.isatty() and not os.getenv("NO_COLOR")
        self.dashboard = dashboard

    def style(self, text: str, color: str = "", *, bold: bool = False) -> str:
        if not self.enabled:
            return text
        prefix = (self.BOLD if bold else "") + color
        return f"{prefix}{text}{self.RESET}" if prefix else text

    def write(self, text: str = "") -> None:
        if self.dashboard:
            self.dashboard.write(text)
            return
        print(text, flush=True)

    def banner(self) -> None:
        self.write()
        self.write(self.style("╭──────────────────────────────────────────────────────────────╮", self.CYAN))
        self.write(self.style("│  ✦  L U M E N   /   V O I C E   L I G H T   C O N T R O L  │", self.CYAN, bold=True))
        self.write(self.style("│     Make the room feel right. Just say the word.              │", self.BLUE))
        self.write(self.style("╰──────────────────────────────────────────────────────────────╯", self.CYAN))
        self.write(f"  {self.style('DEVICE', self.DIM, bold=True)}  {ADDRESS}")
        self.write(f"  {self.style('VOICE', self.DIM, bold=True)}   {LANGUAGE}  ·  try 'ocean mode', 'sleep in 30 minutes', or 'undo'")
        self.write()

    def status(self, message: str, level: str = "info") -> None:
        colors = {
            "info": self.CYAN,
            "success": self.GREEN,
            "warning": self.YELLOW,
            "error": self.RED,
        }
        icons = {"info": "◇", "success": "●", "warning": "△", "error": "×"}
        color = colors.get(level, self.CYAN)
        icon = icons.get(level, "◇")
        self.write(f"  {self.style(icon, color, bold=True)}  {message}")

    def heard(self, text: str, source: str) -> None:
        source_label = "VOICE" if source == "voice" else "KEYBOARD"
        self.write(
            f"  {self.style('INPUT RECEIVED', self.MAGENTA, bold=True)}  "
            f"{self.style(source_label, self.DIM)}  {self.style(chr(34) + text + chr(34), self.WHITE)}"
        )

    def mic_indicator(self, phase: str = "listening", detail: str = "Waiting for your voice…") -> None:
        """Show the microphone's current state in a consistent, scannable line."""

        states = {
            "listening": ("●", "LISTENING", self.GREEN),
            "captured": ("◉", "CAPTURED", self.YELLOW),
            "offline": ("●", "LISTENING", self.YELLOW),
            "paused": ("○", "PAUSED", self.YELLOW),
        }
        icon, label, color = states.get(phase, states["listening"])
        self.write(
            f"  {self.style('MIC', self.CYAN, bold=True)}  "
            f"{self.style(icon, color, bold=True)} "
            f"{self.style(label, color, bold=True)}  {detail}"
        )

    def light_state(self, state: dict[str, Any], summary: str | None = None) -> None:
        r, g, b = target_rgb(state)
        power = self.style("ON", self.GREEN, bold=True) if state["on"] else self.style("OFF", self.DIM, bold=True)
        color_name = light_color_label(state)
        swatch = self.style("●", ansi_rgb(r, g, b)) if self.enabled else "●"
        line = (
            f"  {self.style('LIGHT', self.CYAN, bold=True)}  {swatch}  "
            f"{color_name.upper():<6}  {power}  "
            f"{round(state['brightness'] * 100):>3}% brightness"
        )
        scene = scene_label(state)
        if scene:
            line += f"  ·  {self.style(scene.upper(), self.MAGENTA)}"
        timer = timer_label(state)
        if timer:
            line += f"  ·  {self.style(timer.upper(), self.YELLOW)}"
        alarm = alarm_label(state)
        if alarm:
            line += f"  ·  {self.style(alarm.upper(), self.RED, bold=True)}"
        self.write(line)
        if summary:
            self.write(f"  {self.style('↳', self.DIM)}  {summary}")

    def hud(self, state: dict[str, Any], *, force: bool = False) -> None:
        """Render a compact Jarvis-style snapshot of the live system state."""

        if self.dashboard:
            self.dashboard.app.invalidate()
            return
        if not force:
            return

        assistant = str(state.get("assistant_name", "Lumen")).upper()
        connection = str(state.get("_connection", "SCANNING")).upper()
        mic = str(state.get("_mic_status", "STARTING")).upper()
        mode = state.get("feedback_mode", "off")
        volume = int(state.get("feedback_volume", DEFAULT_FEEDBACK_VOLUME))
        feedback = f"{mode.upper()} · {volume}%"
        scene = scene_label(state) or "manual"
        power = "ON" if state.get("on") else "OFF"
        color = pretty_color_name(light_color_label(state))
        brightness = round(float(state.get("brightness", 0.0)) * 100)
        timer = timer_label(state) or "none"
        alarm = alarm_label(state) or "none"
        personality = str(state.get("personality", "professional")).upper()
        clock = local_clock()

        lines = [
            f"{assistant} CORE  ·  {connection}",
            f"LIGHT       {power:<3} · {color.upper():<10} · {brightness:>3}% · {scene.upper()}",
            f"MIC         {mic:<10} · VOICE {LANGUAGE}",
            f"FEEDBACK    {feedback:<14} · PERSONALITY {personality}",
            f"TIMER       {timer}",
            f"ALARM       {alarm}",
            f"LOCAL       {clock['clock']} · {clock['day']} · {clock['date']}",
        ]
        width = 64
        self.write()
        self.write(self.style("╭" + "─" * width + "╮", self.CYAN))
        for line in lines:
            self.write(self.style("│ " + line[:width - 2].ljust(width - 2) + " │", self.CYAN))
        self.write(self.style("╰" + "─" * width + "╯", self.CYAN))

    def prompt_help(self, mic_ready: bool) -> None:
        if mic_ready:
            self.status("Microphone is live. Speak naturally; typed commands also work.", "success")
            self.mic_indicator()
        else:
            self.status("Voice input is unavailable, so use the typed command prompt below.", "warning")
        self.write(f"  {self.style('TIP', self.DIM, bold=True)}  Type `help` for examples · `quit` to exit")
        self.write()


def ansi_rgb(r: int, g: int, b: int) -> str:
    """Return a true-color foreground sequence for the terminal swatch."""

    channels = (max(0, min(255, int(round(value)))) for value in (r, g, b))
    red, green, blue = channels
    return f"\033[38;2;{red};{green};{blue}m"


def color_name_for(rgb: tuple[int, int, int]) -> str:
    for name, value in COLORS.items():
        if value == rgb:
            return name
    return "custom"


def scene_label(state: dict[str, Any]) -> str:
    effect = state.get("effect")
    if effect in EFFECTS:
        return f"{effect} effect"
    mode = state.get("mode")
    if mode not in SCENES:
        return ""
    color_name, brightness = SCENES[mode]
    adjusted = state["color"] != COLORS[color_name] or abs(state["brightness"] - brightness) > 0.0001
    return f"{mode} (adjusted)" if adjusted else mode


def light_color_label(state: dict[str, Any]) -> str:
    effect = state.get("effect")
    if effect in EFFECTS:
        base_color = state.get("effect_color")
        return pretty_color_name(color_name_for(base_color)) if effect in {"breathing", "pulse"} and base_color else effect
    return pretty_color_name(state.get("color_name", color_name_for(state["color"])))


def pretty_color_name(name: str) -> str:
    return name.replace("_", " ")


def duration_label(seconds: float) -> str:
    seconds = max(0, math.ceil(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def spoken_duration(seconds: float) -> str:
    hours, remainder = divmod(max(1, math.ceil(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = [f"{value} {unit}{'s' if value != 1 else ''}"
             for value, unit in ((hours, "hour"), (minutes, "minute"), (seconds, "second")) if value]
    return " and ".join(parts)


def spoken_light_state(
    state: dict[str, Any], *, include_setting: bool = True, include_brightness: bool = True,
) -> str:
    """Describe only the requested parts of the final light state."""

    if not state["on"]:
        return "Lights off."
    parts = []
    if include_setting:
        mode = state.get("effect") or state.get("mode")
        parts.append(f"{mode.capitalize()} mode enabled." if mode else f"{light_color_label(state).capitalize()} lights on.")
    if include_brightness:
        parts.append(f"Brightness {round(state['brightness'] * 100)} percent.")
    return " ".join(parts) or "Lights on."


def feedback_command(text: str, state: dict[str, Any]) -> CommandResult | None:
    """Handle speech settings outside lighting history and percentage parsing."""

    mode_commands = {
        "enable spoken feedback": "all", "enable feedback": "all", "unmute feedback": "all",
        "only announce alarms": "alarms", "feedback alarms only": "alarms",
        "mute feedback": "off", "disable spoken feedback": "off", "disable feedback": "off",
    }
    if text in mode_commands:
        mode = mode_commands[text]
        state["feedback_mode"] = mode
        state.setdefault("feedback_volume", DEFAULT_FEEDBACK_VOLUME)
        summary = {"all": "Spoken feedback enabled.", "alarms": "Only announcing alarms.", "off": "Spoken feedback muted."}[mode]
        return CommandResult(True, summary, spoken=summary if mode != "off" else None, category="feedback")

    if text == "feedback status":
        mode = state.get("feedback_mode", "off")
        volume = state.get("feedback_volume", DEFAULT_FEEDBACK_VOLUME)
        summary = f"Spoken feedback: {mode}, volume {volume}%."
        if mode == "off":
            summary += " Say or type 'enable spoken feedback' to hear confirmations."
        return CommandResult(False, summary, category="feedback")

    volume_match = re.fullmatch(r"(?:set )?feedback volume(?: to)? (.+)", text)
    if volume_match:
        tokens = re.findall(r"[a-z]+|-?\d+(?:\.\d+)?|%", volume_match[1])
        amount = read_percentage(tokens, 0)
        if amount:
            value, end = amount
            suffix = tokens[end:]
            if 0 <= value <= 100 and (not suffix or suffix in (["%"], ["percent"])):
                volume = round(value)
                state["feedback_volume"] = volume
                summary = f"Feedback volume set to {volume} percent."
                return CommandResult(True, summary, spoken=summary if volume else None, category="feedback")
        return CommandResult(False, "Use 'feedback volume 0' through 'feedback volume 100'.", category="feedback")

    if re.search(r"\bfeedback\b", text) or text.startswith("only announce"):
        return CommandResult(False, "Try 'enable spoken feedback', 'feedback volume 30', 'only announce alarms', or 'mute feedback'.", category="feedback")
    return None


def personality_response(
    text: str | None, state: dict[str, Any], category: str = "command",
) -> str | None:
    """Apply the selected response style without changing the underlying text."""

    if not text:
        return text
    personality = state.get("personality", "professional")
    if category == "clock":
        if personality == "minimal":
            return re.sub(r"^(?:The time is |Today's date is |Today is )", "", text)
        suffix = {
            "playful": " Consider yourself up to date!",
            "sarcastic": " Keeping track so you don't have to.",
            "cinematic": " At your service.",
        }.get(personality, "")
        return text + suffix
    if personality == "minimal":
        text = text.replace(" mode enabled.", " enabled.")
    prefix = PERSONALITIES.get(personality, PERSONALITIES["professional"])
    return f"{prefix}{text}" if prefix else text


def personality_command(text: str, state: dict[str, Any]) -> CommandResult | None:
    """Handle assistant personality and display-name settings."""

    if text in {"personality", "personality status", "show personality", "what is your personality"}:
        personality = state.get("personality", "professional")
        name = state.get("assistant_name", "Lumen")
        return CommandResult(False, f"{name}'s personality is {personality}.", category="personality")

    if text in {"assistant name", "show assistant name", "what is your name", "who are you"}:
        return CommandResult(False, f"I am {state.get('assistant_name', 'Lumen')}.", category="personality")

    personality_match = re.fullmatch(r"(?:set |use )?(?:the )?personality(?: to| as)? ([a-z][a-z0-9_-]*)", text)
    if personality_match:
        requested = personality_match[1]
        personality = PERSONALITY_ALIASES.get(requested, requested)
        if personality not in PERSONALITIES:
            choices = ", ".join(PERSONALITIES)
            return CommandResult(False, f"Unknown personality. Choose: {choices}.", category="personality")
        state["personality"] = personality
        summary = f"Personality set to {personality}."
        return CommandResult(True, summary, spoken=summary, category="personality")

    name_match = re.fullmatch(
        r"(?:set |rename )?(?:the )?(?:assistant name|yourself)(?: to| as)? ([a-z][a-z0-9 -]{0,23})",
        text,
    )
    if name_match:
        name = " ".join(part.capitalize() for part in name_match[1].split())
        state["assistant_name"] = name
        summary = f"Assistant name set to {name}."
        return CommandResult(True, summary, spoken=summary, category="personality")

    if re.match(r"(?:set |use )?(?:the )?personality\b", text) or re.match(
        r"(?:set |rename )?(?:the )?(?:assistant name|yourself)\b", text,
    ):
        return CommandResult(
            False,
            "Try 'personality playful', 'personality status', or 'set assistant name Jarvis'.",
            category="personality",
        )
    return None


def local_clock() -> dict[str, str]:
    """Read the system's local wall clock once for consistent time/date labels."""

    now = datetime.now().astimezone()
    hour = now.hour % 12 or 12
    period = "AM" if now.hour < 12 else "PM"
    return {
        "time": f"{hour}:{now.minute:02d} {period}",
        "clock": f"{hour}:{now.minute:02d}:{now.second:02d} {period}",
        "date": f"{now:%B} {now.day}, {now.year}",
        "day": f"{now:%A}",
    }


def clock_command(text: str) -> CommandResult | None:
    """Match complete clock queries without consuming timer or light commands."""

    query = text.replace("’", "'")
    query = re.sub(r"\bwhat'?s\b", "what is", query)
    query = re.sub(r"\btoday'?s\b", "today", query)
    query = re.sub(r"^(?:(?:can|could) you )?(?:please )?", "", query)
    query = re.sub(r",? please$", "", query)
    queries = {
        "time": {
            "time", "time now", "current time", "what is the time", "what is the current time",
            "what time is it", "what time is it now", "tell me the time", "tell me what time it is",
        },
        "date": {
            "date", "today date", "current date", "what is the date", "what is today date",
            "what is the date today", "what date is it", "what date is it today",
            "tell me the date", "tell me today date",
        },
        "day": {
            "day", "what day is it", "what day is today", "what day is it today",
            "what is the day", "what is the day today", "what day of the week is it",
            "tell me the day",
        },
    }
    for field, phrases in queries.items():
        if query in phrases:
            value = local_clock()[field]
            prefix = {"time": "The time is", "date": "Today's date is", "day": "Today is"}[field]
            answer = f"{prefix} {value}."
            return CommandResult(False, answer, spoken=answer, category="clock")
    return None


def timer_label(state: dict[str, Any], now: float | None = None) -> str:
    timer = state.get("timer")
    if not timer:
        return ""
    remaining = max(0, timer["deadline"] - (time.monotonic() if now is None else now))
    target = timer.get("label", timer["action"])
    return f"timer {duration_label(remaining)} → {target}"


def alarm_label(state: dict[str, Any], now: float | None = None) -> str:
    alarm = state.get("alarm")
    if not alarm:
        return ""
    if alarm.get("active"):
        return "alarm active"
    remaining = max(0, alarm["deadline"] - (time.monotonic() if now is None else now))
    return f"alarm {duration_label(remaining)}"


def alarm_rgb(now: float) -> tuple[float, float, float]:
    """A high-visibility visual alarm pattern with no audio dependency."""

    phase = now % 1.2
    if phase < 0.42:
        pulse = 0.3 + 0.7 * math.sin(math.pi * phase / 0.42) ** 2
        return 255 * pulse, 2, 3
    if phase < 0.58:
        return 0.0, 0.0, 0.0
    if phase < 0.88:
        pulse = 0.3 + 0.7 * math.sin(math.pi * (phase - 0.58) / 0.30) ** 2
        return 255 * pulse, 255 * pulse, 255 * pulse
    return 0.0, 0.0, 0.0


def effect_rgb(
    effect: str,
    now: float,
    base_color: tuple[int, int, int] | None = None,
) -> tuple[float, float, float]:
    """Return an animated RGB target for an ambient effect."""

    if effect == "jungle":
        wave = (math.sin(now * 0.9) + 1) / 2
        firefly = max(0.0, math.sin(now * 2.8) ** 8)
        return 8 + 26 * wave + 45 * firefly, 48 + 105 * wave + 70 * firefly, 12 + 20 * wave
    if effect == "ocean":
        wave = (math.sin(now * 0.75) + 1) / 2
        shimmer = (math.sin(now * 2.4 + 1.3) + 1) / 2
        return 4 + 12 * shimmer, 35 + 90 * wave, 100 + 125 * wave
    if effect == "fireplace":
        flicker = 0.76 + 0.14 * math.sin(now * 7.0) + 0.08 * math.sin(now * 13.0 + 0.8)
        heat = max(0.45, min(1.0, flicker))
        return 255 * heat, 30 + 105 * heat, 2 + 10 * (1 - heat)
    if effect == "rainbow":
        red, green, blue = colorsys.hsv_to_rgb((now * 0.055) % 1.0, 0.88, 1.0)
        return red * 255, green * 255, blue * 255
    if effect in {"breathing", "pulse"}:
        speed = 1.3 if effect == "breathing" else 3.0
        pulse = 0.18 + 0.82 * ((math.sin(now * speed) + 1) / 2)
        return tuple(channel * pulse for channel in (base_color or (180, 90, 255)))
    if effect == "candle":
        flicker = 0.62 + 0.16 * math.sin(now * 8.0) + 0.12 * math.sin(now * 17.0)
        heat = max(0.35, min(1.0, flicker))
        return 255 * heat, 72 * heat, 4
    if effect == "thunder":
        cycle = math.floor(now / THUNDER_CYCLE_SECONDS)
        phase = now % THUNDER_CYCLE_SECONDS
        # A fresh seed each launch, with a stable schedule inside each cycle.
        # Repeated reads (including the status display) cannot move a flash.
        rng = random.Random(_THUNDER_SEED + cycle)
        first_flash = rng.uniform(0.75, 4.0)
        second_flash = rng.uniform(6.0, 9.25)
        if any(start <= phase < start + THUNDER_FLASH_SECONDS for start in (first_flash, second_flash)):
            return 255.0, 255.0, 255.0
        return 0.0, 0.0, 0.0
    if effect == "police":
        phase = now % 1.6
        if phase < 0.55:
            pulse = 0.35 + 0.65 * math.sin(math.pi * phase / 0.55) ** 2
            return 255 * pulse, 4, 8
        if phase < 0.75:
            return 2, 2, 5
        if phase < 1.30:
            pulse = 0.35 + 0.65 * math.sin(math.pi * (phase - 0.75) / 0.55) ** 2
            return 5, 12, 255 * pulse
        return 2, 2, 5
    if effect == "aurora":
        hue = (0.34 + 0.12 * math.sin(now * 0.18) + 0.06 * math.sin(now * 0.43)) % 1.0
        saturation = 0.72 + 0.18 * ((math.sin(now * 0.55) + 1) / 2)
        value = 0.42 + 0.38 * ((math.sin(now * 0.31 + 1.0) + 1) / 2)
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
        return red * 255, green * 255, blue * 255
    if effect == "lava":
        heat = (math.sin(now * 0.55) + math.sin(now * 1.17 + 1.4) + 2) / 4
        glow = 0.45 + 0.55 * heat
        return 255 * glow, 20 + 95 * heat, 2
    if effect == "rain":
        rain_wave = (math.sin(now * 2.4) + 1) / 2
        drop = max(0.0, math.sin(now * 8.5 + 0.7) ** 12)
        return 2 + 12 * drop, 18 + 35 * rain_wave + 20 * drop, 80 + 100 * rain_wave + 75 * drop
    if effect == "cloud":
        drift = (math.sin(now * 0.22) + 1) / 2
        silver = 32 + 45 * drift
        return silver * 0.58, silver * 0.68, silver
    if effect == "starlight":
        cycle = math.floor(now / 7.0)
        phase = now % 7.0
        rng = random.Random(0x51A7 + cycle)
        stars = [rng.uniform(0.2, 6.8) for _ in range(7)]
        twinkle = max((math.exp(-((phase - star) / 0.08) ** 2) for star in stars), default=0.0)
        return 2 + 253 * twinkle, 3 + 252 * twinkle, 10 + 245 * twinkle
    if effect == "matrix":
        pulse = max(0.0, math.sin(now * 4.5) ** 10)
        wash = (math.sin(now * 0.4) + 1) / 2
        return 1 + 8 * wash, 24 + 90 * wash + 150 * pulse, 3 + 12 * pulse
    if effect == "warning":
        phase = now % 1.25
        if phase < 0.52:
            pulse = 0.25 + 0.75 * math.sin(math.pi * phase / 0.52) ** 2
            return 255 * pulse, 35 * pulse, 2
        if phase < 0.70:
            return 2, 2, 0
        pulse = 0.25 + 0.75 * math.sin(math.pi * (phase - 0.70) / 0.55) ** 2
        return 255 * pulse, 35 * pulse, 2
    return COLORS["warm"]


def apply_due_timer(state: dict[str, Any], now: float | None = None) -> str | None:
    timer = state.get("timer")
    current = time.monotonic() if now is None else now
    if not timer or current < timer["deadline"]:
        return None

    action = timer["action"]
    state["timer"] = None
    # Use the same command/history path as voice and keyboard input. Consume
    # the timer first so undo restores the light without re-arming this alarm.
    result = apply_command(timer.get("command", action), state)
    if action == "off":
        return "Timer finished — lights off."
    if action == "on":
        return "Timer finished — lights on."
    if action in SCENES:
        return f"Timer finished — {action} mode is on."
    return f"Timer finished — {result.summary}"


def apply_due_alarm(state: dict[str, Any], now: float | None = None) -> str | None:
    alarm = state.get("alarm")
    current = time.monotonic() if now is None else now
    if not alarm or alarm.get("active") or current < alarm["deadline"]:
        return None
    alarm["active"] = True
    alarm["triggered_at"] = current
    return "Alarm ringing — say acknowledge, dismiss, or snooze."


def schedule_alarm(text: str, state: dict[str, Any]) -> CommandResult | None:
    """Schedule a visual alarm without changing the current light state."""

    match = re.fullmatch(
        r"(?:set\s+)?(?:an\s+)?alarm\s+(?:in|after|for)\s+(.+)",
        text,
    )
    if not match:
        match = re.fullmatch(r"wake me(?:\s+up)?\s+with\s+(?:an\s+)?alarm\s+(?:in|after|for)\s+(.+)", text)
    if not match:
        return None

    duration = parse_duration(match.group(1))
    if duration is None:
        return CommandResult(False, "Use an alarm duration, e.g. 'alarm in 10 minutes'.")

    state["alarm"] = {
        "deadline": time.monotonic() + duration,
        "active": False,
        "label": "visual alarm",
    }
    return CommandResult(True, f"Alarm set for {duration_label(duration)} from now.",
                         spoken=f"Alarm set for {spoken_duration(duration)}.", category="alarm")


def snooze_alarm(text: str, state: dict[str, Any]) -> CommandResult | None:
    if not re.fullmatch(r"snooze(?:\s+alarm)?(?:\s+(?:for|in)\s+.+|\s+.+)?", text):
        return None
    alarm = state.get("alarm")
    if not alarm:
        return CommandResult(False, "No alarm is scheduled.")

    duration_text = re.sub(r"^snooze(?:\s+alarm)?\s*", "", text).strip()
    if duration_text.startswith(("for ", "in ")):
        duration_text = duration_text.split(" ", 1)[1]
    duration = parse_duration(duration_text) if duration_text else 300.0
    if duration is None:
        return CommandResult(False, "Use a snooze duration, e.g. 'snooze for 5 minutes'.")

    alarm.update(deadline=time.monotonic() + duration, active=False, snoozed=True)
    return CommandResult(True, f"Alarm snoozed for {duration_label(duration)}.",
                         spoken=f"Alarm snoozed for {spoken_duration(duration)}.", category="alarm")


def read_percentage(tokens: list[str], start: int) -> tuple[float, int] | None:
    """Read digits or spoken numbers without interpreting unrelated words."""

    if start >= len(tokens):
        return None
    word = tokens[start]
    if re.fullmatch(r"-?\d+(?:\.\d+)?", word):
        return float(word), start + 1
    if word == "hundred":
        return 100.0, start + 1
    if word not in NUMBER_WORDS:
        return None
    value = NUMBER_WORDS[word]
    end = start + 1
    if end < len(tokens):
        next_word = tokens[end]
        if value == 1 and next_word == "hundred":
            return 100.0, end + 1
        if value >= 20 and next_word in NUMBER_WORDS and 0 < NUMBER_WORDS[next_word] < 10:
            value += NUMBER_WORDS[next_word]
            end += 1
    return float(value), end


def build_color_cmd(r: int, g: int, b: int) -> bytearray:
    """Build the controller packet, optionally swapping red and blue."""

    r, g, b = (max(0, min(255, int(round(value)))) for value in (r, g, b))
    if SWAP_RB:
        r, b = b, r
    return bytearray([0x7E, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xEF])


def prepare_audio_for_recognition(audio: sr.AudioData) -> tuple[sr.AudioData, int, int]:
    """Center and gently normalize captured PCM before sending it to Google."""

    raw = audio.get_raw_data()
    width = audio.sample_width
    raw_level = audioop.rms(raw, width) if raw else 0
    try:
        centered = audioop.bias(raw, width, -audioop.avg(raw, width))
    except Exception:
        centered = raw

    centered_level = audioop.rms(centered, width) if centered else 0
    if not centered_level:
        return audio, raw_level, centered_level

    target = max(1000.0, min(12000.0, AUDIO_TARGET_RMS))
    gain = max(0.35, min(4.0, target / centered_level))
    cleaned = audioop.mul(centered, width, gain)
    return sr.AudioData(cleaned, audio.sample_rate, width), raw_level, audioop.rms(cleaned, width)


def target_rgb(state: dict[str, Any], now: float | None = None) -> tuple[float, float, float]:
    current = time.monotonic() if now is None else now
    alarm = state.get("alarm")
    if alarm and alarm.get("active"):
        return alarm_rgb(current)
    if not state["on"]:
        return 0.0, 0.0, 0.0
    brightness = state["brightness"]
    effect = state.get("effect")
    color = effect_rgb(effect, current, state.get("effect_color")) if effect in EFFECTS else state["color"]
    return tuple(channel * brightness for channel in color)


def parse_duration(text: str) -> float | None:
    """Read the whole duration, including 'twenty five minutes' and '1h 30m'."""

    units = {
        "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
        "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
        "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    }
    # Also accept a complete timer phrase for callers using this helper.
    duration = re.split(r"\b(?:in|after|for|timer)\s+", text.lower())[-1].strip()
    if not duration or re.search(r"[^a-z0-9\s.\-]", duration):
        return None
    tokens = re.findall(r"-?\d+(?:\.\d+)?|[a-z]+", duration)
    index, total = 0, 0.0
    while index < len(tokens):
        if tokens[index] == "half":
            amount, end = 0.5, index + 1
            if end < len(tokens) and tokens[end] in {"a", "an"}:
                end += 1
        elif tokens[index] in {"a", "an"}:
            amount, end = 1.0, index + 1
        else:
            parsed = read_percentage(tokens, index)
            if parsed is None:
                return None
            amount, end = parsed
        if not math.isfinite(amount) or amount < 0:
            return None
        if end == len(tokens) and index == 0:
            total = amount * 60  # Bare timer values default to minutes.
            break
        if end >= len(tokens) or tokens[end] not in units:
            return None
        total += amount * units[tokens[end]]
        index = end + 1
        if index < len(tokens) and tokens[index] == "and":
            index += 1
            if index == len(tokens):
                return None
    return total if math.isfinite(total) and total > 0 else None


def schedule_timer(text: str, state: dict[str, Any]) -> CommandResult | None:
    """Validate the whole timer before changing either the light or its alarm."""

    bare = re.fullmatch(r"(?:set (?:a |the )?)?timer(?: for)?\s+(.+)", text)
    timed = re.fullmatch(r"(.+?)\s+(in|after|for)\s+(.+)", text)
    if bare:
        command, relation, duration_text = "off", "in", bare[1]
    elif timed:
        command, relation, duration_text = timed.groups()
        command = normalize_command(command)
    else:
        return None

    duration = parse_duration(duration_text)
    if duration is None:
        return CommandResult(False, "Use a positive timer duration, e.g. 'off in 30 seconds' or 'sleep in twenty five minutes'.")

    # Preview using only light state: no shutdown, history, or timer commands
    # can be accidentally scheduled, and invalid requests leave state intact.
    preview = light_snapshot(state)
    result = _apply_command(command, preview, allow_timer=False)
    if not result.changed or result.should_exit or preview.get("timer") != state.get("timer"):
        return CommandResult(False, "Choose a light action for the timer, e.g. 'off in 1 hour', 'dim to 10% in 20 minutes', or 'ocean mode for 5 minutes'.")

    start_now = relation == "for"
    if start_now:
        state.update(preview)
    action_command = "off" if start_now else command
    action = action_command if action_command in {"on", "off", *SCENES} else "command"
    label = {"on": "lights on", "off": "lights off"}.get(action_command, action_command)
    replacing = state.get("timer") is not None
    state["timer"] = {
        "deadline": time.monotonic() + duration,
        "action": action,
        "command": action_command,
        "label": label,
    }
    summary = f"Timer set for {duration_label(duration)} → {label}."
    if start_now:
        summary = f"{command} started. {summary}"
    if replacing:
        summary += " Replaced the previous timer."
    return CommandResult(True, summary, spoken=f"Timer set for {spoken_duration(duration)}.")


def normalize_command(text: str) -> str:
    normalized = " ".join(text.lower().strip().rstrip(".!?").split())
    normalized = re.sub(r"\blight[\s-]+blue\b", "lightblue", normalized)
    normalized = re.sub(r"\b(sky|ice)[\s-]+blue\b", r"\1blue", normalized)
    return COMMAND_ALIASES.get(normalized, normalized)


def light_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    # Keep history metadata out of snapshots to avoid recursively copying it.
    return copy.deepcopy({key: state[key] for key in LIGHT_FIELDS if key in state})


def apply_command(text: str, state: dict[str, Any]) -> CommandResult:
    """Apply a command with bounded undo history and repeat support."""

    command = normalize_command(text)
    feedback_result = feedback_command(command, state)
    if feedback_result is not None:
        return feedback_result
    assistant_result = personality_command(command, state)
    if assistant_result is not None:
        return assistant_result
    if command in {"undo", "undo last command", "restore", "restore previous setting", "restore previous settings"}:
        history = state.get("_history", [])
        if not history:
            return CommandResult(False, "No previous setting to restore yet.")
        previous = history.pop()
        for field in LIGHT_FIELDS:
            if field in previous:
                state[field] = copy.deepcopy(previous[field])
            else:
                state.pop(field, None)
        timer = state.get("timer")
        if timer and timer["deadline"] <= time.monotonic():
            state["timer"] = None
        return CommandResult(True, "Previous setting restored.", spoken="Previous setting restored.")

    repeated = command in {"repeat", "repeat last command", "repeat the last command", "again"}
    if repeated:
        command = state.get("_last_command")
        if not command:
            return CommandResult(False, "No previous light command to repeat yet.")

    before = light_snapshot(state)
    result = _apply_command(command, state)
    if result.changed and not result.should_exit:
        if light_snapshot(state) != before:
            history = state.setdefault("_history", [])
            history.append(before)
            del history[:-20]
        state["_last_command"] = command
    if repeated:
        result.summary = f"Repeating '{command}': {result.summary}"
    return result


def _apply_command(text: str, state: dict[str, Any], *, allow_timer: bool = True) -> CommandResult:
    """Apply natural, short light commands without requiring exact phrasing."""

    raw = text.strip()
    normalized = normalize_command(raw)
    if not raw:
        return CommandResult(False, "I didn't receive a command.")

    clock_result = clock_command(normalized)
    if clock_result is not None:
        return clock_result

    if re.search(r"\bfeedback\b", normalized) or normalized.startswith("only announce"):
        return CommandResult(False, "Use feedback controls directly; they cannot be scheduled.")

    if (
        re.match(r"(?:set |use )?(?:the )?personality\b", normalized)
        or re.match(r"(?:set |rename )?(?:the )?(?:assistant name|yourself)\b", normalized)
        or normalized in {"assistant name", "show assistant name", "what is your name", "who are you"}
    ):
        return CommandResult(False, "Personality controls cannot be scheduled.")

    if normalized in {"quit", "exit", "stop", "goodbye", "shutdown"}:
        return CommandResult(False, "Shutting down gracefully.", should_exit=True)

    if normalized in {"hud", "dashboard", "show hud", "show dashboard", "refresh hud"}:
        return CommandResult(False, "Dashboard refreshed.")

    if normalized in {"help", "commands", "what can i say"}:
        return CommandResult(
            False,
            "Try: sleep mode, relax, focus, reading, movie, party, night, blue, light blue, "
            "jungle mode, ocean mode, fireplace mode, toggle, brightness 10, "
            "breathing blue, undo, restore previous setting, repeat last command, "
            "sleep in 30 minutes, turn off in 1 hour, or dim to 10% in 20 minutes. "
            "Enable spoken feedback, feedback volume 30, only announce alarms, mute feedback. "
            "Ask 'what time is it', 'what is today's date', or 'what day is it'. "
            "Try 'hud', 'personality playful', 'set assistant name Jarvis', 'modes', 'effects', "
            "'timer status', or 'feedback status' for details.",
        )

    if normalized in {"modes", "scenes", "list modes", "list scenes"}:
        return CommandResult(
            False,
            "Scenes: " + "; ".join(
                f"{name} = {color} at {round(brightness * 100)}%"
                for name, (color, brightness) in SCENES.items()
            ) + ". Effects: " + ", ".join(sorted(EFFECTS)) + ".",
        )

    if normalized in {"effects", "list effects"}:
        return CommandResult(False, "Effects: " + ", ".join(sorted(EFFECTS)) + ". Try 'breathing blue', 'pulse red', or 'ocean mode brightness 40'.")

    if normalized in {"alarm status", "show alarm", "show alarm status"}:
        alarm = state.get("alarm")
        return CommandResult(False, alarm_label(state) if alarm else "No alarm is scheduled.")

    if normalized in {"acknowledge alarm", "acknowledge", "dismiss alarm", "dismiss", "stop alarm", "cancel alarm"}:
        if not state.get("alarm"):
            return CommandResult(False, "No alarm is active.")
        was_active = state["alarm"].get("active", False)
        state["alarm"] = None
        summary = "Alarm dismissed." if was_active else "Alarm cancelled."
        return CommandResult(True, summary, spoken=summary, category="alarm")

    if allow_timer:
        snoozed = snooze_alarm(normalized, state)
        if snoozed is not None:
            return snoozed

    if allow_timer:
        alarm_scheduled = schedule_alarm(normalized, state)
        if alarm_scheduled is not None:
            return alarm_scheduled

    if normalized in {"stop effect", "stop effects", "static mode", "solid mode", "stop animation"}:
        state.update(effect=None, effect_color=None, color_name=color_name_for(state["color"]))
        return CommandResult(True, "Animated effect stopped; returning to the last solid color.", spoken="Effect stopped.")

    if normalized in {"cancel timer", "clear timer", "stop timer", "cancel the timer"}:
        had_timer = state.get("timer") is not None
        state["timer"] = None
        return CommandResult(had_timer, "Timer cancelled." if had_timer else "No timer was active.",
                             spoken="Timer cancelled." if had_timer else None)

    if normalized in {"timer", "timer status", "time remaining"}:
        return CommandResult(False, timer_label(state) or "No timer is active.")

    if allow_timer:
        scheduled = schedule_timer(normalized, state)
        if scheduled is not None:
            return scheduled
    elif re.search(r"\b(?:in|after|for|timer)\b", normalized):
        return CommandResult(False, "Nested timers are not supported.")

    if normalized in {"status", "show status", "what is the status"}:
        power = "on" if state["on"] else "off"
        scene = scene_label(state)
        timer = timer_label(state)
        scene_text = scene if not scene or scene.endswith("effect") else f"{scene} mode"
        return CommandResult(
            False,
            f"The light is {power}, {light_color_label(state)} "
            f"at {round(state['brightness'] * 100)}%"
            + (f", {scene_text}." if scene_text else ".")
            + (f" {timer}." if timer else ""),
            spoken=spoken_light_state(state),
        )

    tokens = re.findall(r"[a-z]+|-?\d+(?:\.\d+)?|%", normalized)
    recognized: list[str] = []
    spoken_fields: set[str] = set()
    effect_requested = any(token in EFFECTS or token in EFFECT_ALIASES for token in tokens)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        amount = None
        if token in {"brightness", "britness", "intensity", "bright", "dim"}:
            start = index + 1
            while start < len(tokens) and tokens[start] in {"to", "at", "of", "is"}:
                start += 1
            amount = read_percentage(tokens, start)
        else:
            candidate = read_percentage(tokens, index)
            if candidate:
                _, end = candidate
                if end == len(tokens) and index == 0:
                    amount = candidate  # A standalone "50" or "fifty".
                elif end < len(tokens) and tokens[end] in {"%", "percent", "percentage"}:
                    amount = candidate

        if amount is not None:
            percentage, index = amount
            percentage = max(0.0, min(100.0, percentage))
            if percentage == 0:
                # Preserve the last nonzero brightness so "on" can restore it.
                state["on"] = False
            else:
                state["brightness"] = max(0.01, percentage / 100)
                state["on"] = True
            recognized.append(f"brightness {round(percentage)}%")
            spoken_fields.add("brightness")
            continue

        if token in EFFECTS or token in EFFECT_ALIASES:
            effect = EFFECT_ALIASES.get(token, token)
            state.update(on=True, mode=None, effect=effect, effect_color=state.get("color", COLORS["warm"]))
            if effect == "thunder":
                state["brightness"] = 1.0
            recognized.append(f"{effect} effect")
            spoken_fields.add("setting")
        elif token in SCENES or token in SCENE_ALIASES:
            mode = SCENE_ALIASES.get(token, token)
            color_name, brightness = SCENES[mode]
            state.update(on=True, color=COLORS[color_name], color_name=color_name, brightness=brightness, mode=mode, effect=None, effect_color=None)
            recognized.append(f"{mode} mode")
            spoken_fields.add("setting")
        elif token in COLORS or token in COLOR_ALIASES:
            name = COLOR_ALIASES.get(token, token)
            if state.get("effect") in {"breathing", "pulse"} and effect_requested:
                state["effect_color"] = COLORS[name]
                state["color_name"] = name
                state["on"] = True
            else:
                state["color"] = COLORS[name]
                state["color_name"] = name
                state["on"] = True
                state["mode"] = None
                state["effect"] = None
                state["effect_color"] = None
            recognized.append(pretty_color_name(name))
            spoken_fields.add("setting")
        elif token == "on":
            state["on"] = True
            recognized.append("on")
        elif token == "off":
            state["on"] = False
            recognized.append("off")
        elif token == "toggle":
            state["on"] = not state["on"]
            recognized.append("on" if state["on"] else "off")
        elif token in {"brighter", "brighten", "bright", "up"}:
            state["brightness"] = min(1.0, state["brightness"] + BRIGHT_STEP)
            recognized.append("brighter")
            spoken_fields.add("brightness")
        elif token in {"dimmer", "dim", "darker", "down"}:
            state["brightness"] = max(0.01, state["brightness"] - BRIGHT_STEP)
            recognized.append("dimmer")
            spoken_fields.add("brightness")
        elif token == "half":
            state["brightness"] = 0.5
            recognized.append("50% brightness")
            spoken_fields.add("brightness")
        elif token in {"full", "max", "maximum"}:
            state["brightness"] = 1.0
            recognized.append("100% brightness")
            spoken_fields.add("brightness")
        index += 1

    if not recognized:
        return CommandResult(False, f"I didn't catch a light command in {raw!r}.")

    # The word "on" is intentionally allowed to restore the last color. Keep
    # the display name synchronized even for state dictionaries created by
    # callers outside this module.
    if state.get("effect") in {"breathing", "pulse"} and state.get("effect_color"):
        state["color_name"] = color_name_for(state["effect_color"])
    else:
        state["color_name"] = color_name_for(state["color"])
    power = "on" if state["on"] else "off"
    summary = f"{', '.join(dict.fromkeys(recognized))} → {power}, {light_color_label(state)} at {round(state['brightness'] * 100)}%"
    spoken = spoken_light_state(
        state, include_setting="setting" in spoken_fields, include_brightness="brightness" in spoken_fields,
    )
    return CommandResult(True, summary, spoken=spoken)


def microphone_names() -> list[str]:
    return [name for name, _ in microphone_devices()[0]]


def microphone_devices() -> tuple[list[tuple[str, int]], int | None]:
    """Return device names, input-channel counts, and the OS default index."""

    try:
        with silence_audio_backend_output():
            pyaudio_module = sr.Microphone.get_pyaudio()
            audio = pyaudio_module.PyAudio()
            try:
                devices = []
                for index in range(audio.get_device_count()):
                    info = audio.get_device_info_by_index(index)
                    devices.append(
                        (
                            str(info.get("name") or "Unknown device"),
                            int(info.get("maxInputChannels", 0)),
                        )
                    )
                try:
                    default_index = int(audio.get_default_input_device_info().get("index"))
                except Exception:
                    default_index = None
                return devices, default_index
            finally:
                audio.terminate()
    except Exception:
        return [], None


@contextmanager
def silence_audio_backend_output():
    """Hide noisy ALSA/CoreAudio messages while PortAudio probes devices.

    PortAudio writes many harmless device-probing messages directly to the
    process' stderr, so redirecting ``sys.stderr`` is not enough. The context
    is kept very short and only surrounds audio-device operations.
    """

    try:
        original_stderr = os.dup(2)
        quiet_stderr = os.open(os.devnull, os.O_WRONLY)
        os.dup2(quiet_stderr, 2)
    except OSError:
        yield
        return

    try:
        yield
    finally:
        os.dup2(original_stderr, 2)
        os.close(quiet_stderr)
        os.close(original_stderr)


class StableMicrophone(sr.AudioSource):
    """Microphone source that resolves names and streams in one PyAudio session.

    PortAudio can reorder virtual ALSA devices between separate PyAudio
    instances. Resolving ``MIC_DEVICE`` against the same instance that opens
    the stream prevents a valid device name from accidentally becoming an
    unrelated output or loopback device.
    """

    def __init__(self, requested: str = "") -> None:
        self.pyaudio_module = sr.Microphone.get_pyaudio()
        self.requested = requested.strip()
        self.audio = None
        self.stream = None
        self.device_index: int | None = None
        self.device_name = "system default"
        with silence_audio_backend_output():
            audio = self.pyaudio_module.PyAudio()
            try:
                self.device_index, self.device_name, _ = self.resolve_device(audio)
            finally:
                audio.terminate()

    def resolve_device(self, audio):
        requested = self.requested
        if requested.isdigit():
            index = int(requested)
            if not 0 <= index < audio.get_device_count():
                raise ValueError(f"MIC_DEVICE index {index} is not available")
            info = audio.get_device_info_by_index(index)
            if int(info.get("maxInputChannels", 0)) < 1:
                raise ValueError(f"MIC_DEVICE {index} is output-only: {info.get('name', 'unknown device')}")
        elif requested:
            needle = requested.casefold()
            info = None
            index = None
            for candidate_index in range(audio.get_device_count()):
                candidate = audio.get_device_info_by_index(candidate_index)
                if needle in str(candidate.get("name", "")).casefold() and int(candidate.get("maxInputChannels", 0)) > 0:
                    index, info = candidate_index, candidate
                    break
            if info is None or index is None:
                raise ValueError(f"No recording device matches MIC_DEVICE={requested!r}")
        else:
            info = audio.get_default_input_device_info()
            index = int(info["index"])
            if int(info.get("maxInputChannels", 0)) < 1:
                raise ValueError(f"The system default input is not recordable: {info.get('name', 'unknown device')}")

        return index, str(info.get("name") or "Unknown device"), info

    @property
    def label(self) -> str:
        if self.device_index is None:
            return self.device_name
        return f"{self.device_name} (#{self.device_index})"

    def __enter__(self):
        if self.stream is not None:
            raise AssertionError("This microphone is already open")
        with silence_audio_backend_output():
            self.audio = self.pyaudio_module.PyAudio()
            try:
                self.device_index, self.device_name, info = self.resolve_device(self.audio)
                self.format = self.pyaudio_module.paInt16
                self.SAMPLE_WIDTH = self.pyaudio_module.get_sample_size(self.format)
                self.SAMPLE_RATE = int(info.get("defaultSampleRate", 16000))
                self.CHUNK = 1024
                raw_stream = self.audio.open(
                    input_device_index=self.device_index,
                    channels=1,
                    format=self.format,
                    rate=self.SAMPLE_RATE,
                    frames_per_buffer=self.CHUNK,
                    input=True,
                )
                # Keep the original PCM stream as the primary recognition path.
                # The normalizer is applied only as a fallback after a provider
                # cannot understand the raw phrase.
                self.stream = sr.Microphone.MicrophoneStream(raw_stream)
            except Exception:
                self.audio.terminate()
                self.audio = None
                raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            with silence_audio_backend_output():
                if self.stream is not None:
                    self.stream.close()
        finally:
            self.stream = None
            if self.audio is not None:
                with silence_audio_backend_output():
                    self.audio.terminate()
                self.audio = None


def choose_microphone(names: list[str], requested: str = MIC_DEVICE) -> int | None:
    """Resolve MIC_DEVICE as an index or a case-insensitive name fragment."""

    requested = requested.strip()
    if not requested:
        return None

    if requested.isdigit():
        index = int(requested)
        if 0 <= index < len(names):
            return index
        raise ValueError(f"MIC_DEVICE index {index} is not available")

    needle = requested.casefold()
    for index, name in enumerate(names):
        if needle in name.casefold():
            return index
    raise ValueError(f"No microphone matches MIC_DEVICE={requested!r}")


def microphone_label(index: int | None, names: list[str]) -> str:
    if index is None:
        return "system default"
    if index < len(names):
        return f"{names[index]} (#{index})"
    return f"device #{index}"


def post_event(loop: asyncio.AbstractEventLoop, event_queue: asyncio.Queue, event: dict[str, Any]) -> None:
    """Safely move an event from a microphone/keyboard thread to asyncio."""

    try:
        loop.call_soon_threadsafe(event_queue.put_nowait, event)
    except RuntimeError:
        # The event loop has already closed during process shutdown.
        pass


def start_listening(
    loop: asyncio.AbstractEventLoop,
    event_queue: asyncio.Queue,
    playback_gate: PlaybackGate | None = None,
) -> Callable[..., Any] | None:
    """Start background microphone recognition, returning its stop callback.

    Microphone setup is deliberately defensive. A missing PortAudio device,
    denied permission, or invalid MIC_DEVICE should not prevent keyboard use or
    Bluetooth reconnection.
    """

    try:
        with silence_audio_backend_output():
            mic = StableMicrophone(MIC_DEVICE)
    except Exception as exc:
        post_event(loop, event_queue, {"kind": "status", "level": "warning", "message": f"Microphone unavailable: {exc}"})
        return None

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.55
    recognizer.non_speaking_duration = 0.25
    recognizer.phrase_threshold = 0.15
    recognizer.operation_timeout = 10.0
    calibration = max(0.2, min(5.0, MIC_CALIBRATION_SECONDS))

    post_event(
        loop,
        event_queue,
        {"kind": "status", "level": "info", "message": f"Calibrating microphone ({mic.label}) — stay quiet for {calibration:.1f}s…"},
    )
    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=calibration)
    except Exception as exc:
        post_event(loop, event_queue, {"kind": "status", "level": "warning", "message": f"Microphone calibration failed: {exc}"})
        return None

    # A loud ALSA/virtual input can make SpeechRecognition learn a threshold
    # higher than normal speech. Keep calibration useful, but cap the detector
    # so short commands like "red" and "off" can still open a phrase.
    calibrated_threshold = recognizer.energy_threshold
    recognizer.energy_threshold = max(
        80.0,
        min(1800.0, max(MIC_ENERGY_THRESHOLD, calibrated_threshold * 1.2)),
    )
    recognizer.dynamic_energy_threshold = False
    threshold_detail = f"Listening threshold: {recognizer.energy_threshold:.0f} (calibrated from {calibrated_threshold:.0f})"
    post_event(loop, event_queue, {"kind": "status", "level": "info", "message": threshold_detail})

    def on_audio(recognizer_instance: sr.Recognizer, audio: sr.AudioData, capture_token: int | None) -> None:
        try:
            cleaned_audio, raw_level, cleaned_level = prepare_audio_for_recognition(audio)
            level_detail = f"Audio captured (level {raw_level} → normalized {cleaned_level}) — transcribing your input…"
        except Exception:
            cleaned_audio = audio
            level_detail = "Audio captured — transcribing your input…"
        post_event(
            loop,
            event_queue,
            {"kind": "mic", "phase": "captured", "message": level_detail},
        )
        try:
            # Keep the original path first for compatibility with the version
            # that was already recognizing commands reliably. The centered and
            # normalized audio is a fallback for noisy/default ALSA routes.
            text = recognizer_instance.recognize_google(audio, language=LANGUAGE)
            provider = "online"
        except sr.UnknownValueError:
            if cleaned_audio is not audio:
                try:
                    text = recognizer_instance.recognize_google(cleaned_audio, language=LANGUAGE)
                    provider = "online normalized fallback"
                except sr.UnknownValueError:
                    post_event(loop, event_queue, {"kind": "status", "level": "info", "message": "I heard something, but couldn't make out the words."})
                    post_event(loop, event_queue, {"kind": "mic", "phase": "listening", "message": "Listening again…"})
                    return
                except sr.RequestError as google_error:
                    post_event(loop, event_queue, {"kind": "status", "level": "warning", "message": f"Speech service unavailable ({google_error}). Check internet or install optional PocketSphinx."})
                    post_event(loop, event_queue, {"kind": "mic", "phase": "offline", "message": "Waiting for your next command…"})
                    return
            else:
                post_event(loop, event_queue, {"kind": "status", "level": "info", "message": "I heard something, but couldn't make out the words."})
                post_event(loop, event_queue, {"kind": "mic", "phase": "listening", "message": "Listening again…"})
                return
        except sr.RequestError as google_error:
            # If the optional PocketSphinx package is installed, voice control
            # keeps working without internet. Otherwise give a useful action.
            try:
                text = recognizer_instance.recognize_sphinx(cleaned_audio, language=LANGUAGE)
                provider = "offline"
            except Exception:
                post_event(
                    loop,
                    event_queue,
                    {"kind": "status", "level": "warning", "message": f"Speech service unavailable ({google_error}). Check internet or install optional PocketSphinx."},
                )
                post_event(loop, event_queue, {"kind": "mic", "phase": "offline", "message": "Waiting for your next command…"})
                return
        except Exception as exc:
            post_event(loop, event_queue, {"kind": "status", "level": "warning", "message": f"Voice recognition error: {exc}"})
            post_event(loop, event_queue, {"kind": "mic", "phase": "listening", "message": "Listening again…"})
            return

        if playback_gate and not playback_gate.accepts(capture_token):
            return
        text = (text or "").strip()
        if text:
            post_event(loop, event_queue, {
                "kind": "command", "source": "voice", "provider": provider,
                "text": text, "capture_token": capture_token,
            })
        else:
            post_event(loop, event_queue, {"kind": "mic", "phase": "listening", "message": "No words recognized — listening again…"})

    running = threading.Event()
    running.set()
    stream_ready = threading.Event()
    startup_error: list[str] = []

    def listen_loop() -> None:
        try:
            # Opening this stream here (instead of relying on
            # listen_in_background's hidden thread) lets us report success only
            # after the microphone is genuinely open and ready to read.
            with mic as source:
                if playback_gate:
                    source.stream = FeedbackFilteredStream(source.stream, playback_gate)
                stream_ready.set()
                post_event(
                    loop,
                    event_queue,
                    {"kind": "status", "level": "success", "message": f"Microphone ready on {mic.label} — listening for commands."},
                )
                post_event(loop, event_queue, {"kind": "mic", "phase": "listening", "message": "Waiting for your voice…"})
                idle_windows = 0
                was_paused = False
                while running.is_set():
                    capture_token = playback_gate.token() if playback_gate else None
                    paused = playback_gate is not None and capture_token is None
                    if paused != was_paused:
                        post_event(loop, event_queue, {
                            "kind": "mic", "phase": "paused" if paused else "listening",
                            "message": "Spoken feedback playing…" if paused else "Waiting for your voice…",
                        })
                        was_paused = paused
                    try:
                        if paused:
                            # Drain one native chunk on this same thread, then
                            # recheck the gate instead of entering a one-second
                            # listen with an invalid capture token.
                            source.stream.read(source.CHUNK)
                            continue
                        audio = recognizer.listen(
                            source,
                            timeout=1,
                            phrase_time_limit=max(1.0, MIC_PHRASE_LIMIT),
                        )
                    except PlaybackInterrupted:
                        continue
                    except sr.WaitTimeoutError:
                        if playback_gate and playback_gate.token() is None:
                            continue
                        idle_windows += 1
                        if idle_windows % 10 == 0:
                            post_event(
                                loop,
                                event_queue,
                                {"kind": "mic", "phase": "listening", "message": f"Still listening… no voice detected above level {recognizer.energy_threshold:.0f}."},
                            )
                        continue
                    idle_windows = 0
                    if running.is_set() and (not playback_gate or playback_gate.accepts(capture_token)):
                        on_audio(recognizer, audio, capture_token)
        except Exception as exc:
            startup_error.append(str(exc))
            stream_ready.set()
            post_event(
                loop,
                event_queue,
                {"kind": "status", "level": "warning", "message": f"Microphone stream stopped: {exc}"},
            )
            post_event(loop, event_queue, {"kind": "mic", "phase": "offline", "message": "No active microphone stream."})

    listener_thread = threading.Thread(target=listen_loop, name="microphone-listener", daemon=True)
    listener_thread.start()

    if not stream_ready.wait(timeout=3.0):
        running.clear()
        listener_thread.join(timeout=1.0)
        post_event(loop, event_queue, {"kind": "status", "level": "warning", "message": "Microphone did not open within 3 seconds."})
        return None
    if startup_error:
        running.clear()
        listener_thread.join(timeout=1.0)
        return None

    def stop(wait_for_stop: bool = True) -> None:
        running.clear()
        if wait_for_stop:
            listener_thread.join(timeout=2.0)

    return stop


def start_keyboard_input(
    loop: asyncio.AbstractEventLoop,
    event_queue: asyncio.Queue,
) -> Callable[[], None] | None:
    """Add a typed-command fallback when the app is running in a terminal."""

    if not sys.stdin.isatty():
        return None

    stopped = threading.Event()

    def read_commands() -> None:
        while not stopped.is_set():
            try:
                text = input("  Type a command › ")
            except (EOFError, KeyboardInterrupt):
                break
            if text.strip():
                post_event(loop, event_queue, {"kind": "command", "source": "keyboard", "text": text.strip()})

    thread = threading.Thread(target=read_commands, name="keyboard-input", daemon=True)
    thread.start()

    def stop() -> None:
        stopped.set()

    return stop


def run_microphone_test() -> int:
    """Open the selected microphone and report whether it receives a signal."""

    try:
        with silence_audio_backend_output():
            mic = StableMicrophone(MIC_DEVICE)
    except Exception as exc:
        print(f"Microphone setup failed: {exc}")
        return 1

    seconds = max(0.5, min(10.0, MIC_TEST_SECONDS))
    print(f"Testing {mic.label} for {seconds:.1f}s — make some noise…")
    levels: list[int] = []

    try:
        with mic as source:
            deadline = time.monotonic() + seconds
            native_stream = getattr(source.stream, "pyaudio_stream", None)
            while time.monotonic() < deadline:
                # Polling keeps this diagnostic safe for virtual devices that
                # open successfully but do not deliver frames. Most importantly,
                # stream reads and stream closing stay on the same thread.
                if native_stream is not None and native_stream.get_read_available() <= 0:
                    time.sleep(0.02)
                    continue
                try:
                    chunk = source.stream.read(source.CHUNK, exception_on_overflow=False)
                except TypeError:
                    chunk = source.stream.read(source.CHUNK)
                if len(chunk) >= 2:
                    levels.append(audioop.rms(chunk, source.SAMPLE_WIDTH))
    except Exception as exc:
        print(f"Microphone stream failed: {exc}")
        return 1

    if not levels:
        print("The device opened but did not return audio frames during the test.")
        return 1

    average = sum(levels) / len(levels)
    peak = max(levels)
    print(f"Microphone opened successfully. Average level: {average:.0f} · peak level: {peak}")
    if peak <= 10:
        print("The stream is open, but the signal is very quiet. Check mute and input-volume settings.")
    elif peak >= 32000:
        print("Signal arrived, but it is heavily saturated. Lower the microphone/input gain and test again.")
    else:
        print("Signal detected — this microphone is ready for voice commands.")
    return 0


def hud_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Return the small, thread-safe view consumed by the live terminal HUD."""

    rgb = tuple(max(0, min(255, int(round(channel)))) for channel in target_rgb(state))
    clock = local_clock()
    mic = str(state.get("_mic_status", "STARTING")).upper()
    phase = str(state.get("_mic_phase", "listening"))
    waveforms = {
        "listening": "▂▃▂▃▂▃",
        "captured": "▂▅▇█▇▅",
        "paused": "▁▁▁▁▁▁",
        "offline": "× × × ×",
    }
    return {
        "name": state.get("assistant_name", "Lumen"),
        "connection": state.get("_connection", "SCANNING"),
        "mic": mic,
        "waveform": waveforms.get(phase, waveforms["listening"]),
        "voice": LANGUAGE,
        "speech": f"{str(state.get('feedback_mode', 'off')).upper()} · VOL {int(state.get('feedback_volume', DEFAULT_FEEDBACK_VOLUME))}%",
        "feedback": f"{str(state.get('feedback_mode', 'off')).upper()} · {int(state.get('feedback_volume', DEFAULT_FEEDBACK_VOLUME))}%",
        "personality": state.get("personality", "professional"),
        "power": "ON" if state.get("on") else "OFF",
        "color": light_color_label(state),
        "scene": scene_label(state) or "manual",
        "brightness": round(float(state.get("brightness", 0.0)) * 100),
        "rgb": rgb,
        "timer": timer_label(state) or "none",
        "alarm": alarm_label(state) or "none",
        "device": ADDRESS,
        "language": LANGUAGE,
        "clock": clock["clock"],
        "calendar": f"{clock['day']} · {clock['date']}",
    }


async def event_worker(
    event_queue: asyncio.Queue,
    state: dict[str, Any],
    shutdown: asyncio.Event,
    console: Console,
    feedback: SpokenFeedback | None = None,
) -> None:
    while not shutdown.is_set():
        event = await event_queue.get()
        kind = event.get("kind") if isinstance(event, dict) else "command"
        if kind == "mic":
            phase = event.get("phase", "listening")
            state["_mic_phase"] = phase
            state["_mic_status"] = {
                "listening": "READY",
                "captured": "CAPTURED",
                "offline": "OFFLINE",
                "paused": "PAUSED",
            }.get(phase, "READY")
            console.hud(state)
            if feedback and feedback.gate.token() is None and event.get("phase") == "listening":
                console.mic_indicator("paused", "Spoken feedback playing…")
            else:
                console.mic_indicator(event.get("phase", "listening"), event.get("message", "Waiting for your voice…"))
            continue
        if kind == "status":
            console.status(event["message"], event.get("level", "info"))
            continue

        if kind != "command":
            continue
        text = event.get("text", "") if isinstance(event, dict) else str(event)
        source = event.get("source", "voice") if isinstance(event, dict) else "voice"
        if feedback and source == "voice" and isinstance(event, dict) and "capture_token" in event:
            if not feedback.gate.accepts(event["capture_token"]):
                continue
        console.heard(text, source)
        result = apply_command(text, state)
        if result.should_exit:
            console.status(result.summary, "success")
            shutdown.set()
            continue
        if result.category == "clock":
            console.status(personality_response(result.summary, state, result.category))
        else:
            console.light_state(state, result.summary)
        if normalize_command(text) in {"hud", "dashboard", "show hud", "show dashboard", "refresh hud"}:
            console.hud(state, force=True)
        else:
            console.hud(state)
        if feedback:
            if result.category == "feedback" and result.changed:
                feedback.settings_changed()
            if normalize_command(text) == "feedback status":
                console.status(feedback.engine_status())
            feedback.say(personality_response(result.spoken, state, result.category), result.category)
        if source == "voice":
            provider = event.get("provider", "online") if isinstance(event, dict) else "online"
            console.status(f"Recognized via {provider} speech input.", "success")
            console.mic_indicator("listening", "Waiting for your next command…")


async def timer_worker(
    state: dict[str, Any],
    shutdown: asyncio.Event,
    console: Console,
    feedback: SpokenFeedback | None = None,
) -> None:
    while not shutdown.is_set():
        message = apply_due_timer(state)
        if message:
            console.status(message, "success")
            console.light_state(state, "Timer action applied")
            if feedback:
                feedback.say(personality_response(f"Timer finished. {spoken_light_state(state)}", state), "timer")
        alarm_message = apply_due_alarm(state)
        if alarm_message:
            console.status(alarm_message, "warning")
            console.light_state(state, "Visual alarm active")
            if feedback:
                feedback.say(
                    personality_response("Your alarm is ringing. Say acknowledge alarm or snooze.", state),
                    "alarm",
                )
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=0.25)
        except asyncio.TimeoutError:
            pass


async def wait_for_shutdown(shutdown: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(shutdown.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


async def turn_off(client: BleakClient) -> None:
    try:
        await client.write_gatt_char(UUID, build_color_cmd(0, 0, 0), response=False)
    except Exception:
        pass


async def run() -> None:
    state: dict[str, Any] = {
        "on": True,
        "color": COLORS["warm"],
        "color_name": "warm",
        "brightness": 1.0,
        "mode": None,
        "effect": None,
        "effect_color": None,
        "timer": None,
        "alarm": None,
        "feedback_mode": "off",
        "feedback_volume": DEFAULT_FEEDBACK_VOLUME,
        "assistant_name": DEFAULT_ASSISTANT_NAME,
        "personality": DEFAULT_PERSONALITY,
        "_connection": "BOOTING",
        "_mic_status": "STARTING",
        "_mic_phase": "listening",
    }

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()
    dashboard: TerminalHUD | None = None

    if sys.stdin.isatty() and sys.stdout.isatty() and not os.getenv("NO_HUD"):
        dashboard = TerminalHUD(
            lambda: hud_snapshot(state),
            lambda text: post_event(loop, event_queue, {"kind": "command", "source": "keyboard", "text": text}),
            shutdown,
        )
    console = Console(dashboard)
    console.banner()
    console.light_state(state, "Starting up")
    console.hud(state, force=True)
    console.status("Spoken feedback is off. Say or type 'enable spoken feedback' to hear confirmations.")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass

    feedback = SpokenFeedback(state, console.status)
    feedback.start()
    worker = asyncio.create_task(event_worker(event_queue, state, shutdown, console, feedback))
    timer_task = asyncio.create_task(timer_worker(state, shutdown, console, feedback))
    hud_task = asyncio.create_task(dashboard.run()) if dashboard else None
    mic_stop: Callable[..., Any] | None = None
    keyboard_stop: Callable[[], None] | None = None
    current: list[float] = [0.0, 0.0, 0.0]
    last_sent: tuple[int, int, int] | None = None

    try:
        # Run PortAudio calibration away from the asyncio loop so status events
        # and Bluetooth work stay responsive while the mic is being prepared.
        mic_stop = await asyncio.to_thread(start_listening, loop, event_queue, feedback.gate)
        keyboard_stop = None if dashboard else start_keyboard_input(loop, event_queue)
        state["_mic_status"] = "READY" if mic_stop is not None else "OFFLINE"
        console.hud(state, force=True)
        console.prompt_help(mic_stop is not None)

        while not shutdown.is_set():
            state["_connection"] = "SCANNING"
            console.hud(state, force=True)
            console.status("Looking for the light over Bluetooth…", "info")
            try:
                device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
            except Exception as exc:
                console.status(f"Bluetooth scan failed: {exc}. Retrying in 5s.", "warning")
                await wait_for_shutdown(shutdown, 5.0)
                continue

            if not device:
                state["_connection"] = "SEARCHING"
                console.status("Light not found. Make sure it is powered on; retrying in 5s.", "warning")
                console.hud(state, force=True)
                await wait_for_shutdown(shutdown, 5.0)
                continue

            last_sent = None
            try:
                async with BleakClient(device, timeout=20.0) as client:
                    state["_connection"] = "CONNECTED"
                    console.status(f"Connected to {ADDRESS}", "success")
                    console.hud(state, force=True)
                    while not shutdown.is_set():
                        target = target_rgb(state)
                        alarm = state.get("alarm") or {}
                        if state.get("effect") == "thunder" or alarm.get("active", False):
                            # Thunder and alarms are intentionally hard-edged:
                            # exact pattern targets with no fade smoothing.
                            current[:] = list(target)
                        else:
                            for index in range(3):
                                current[index] += (target[index] - current[index]) * FADE_SPEED
                                if abs(target[index] - current[index]) < 1:
                                    current[index] = target[index]

                        rgb = tuple(int(channel) for channel in current)
                        if rgb != last_sent:
                            await client.write_gatt_char(UUID, build_color_cmd(*rgb), response=False)
                            last_sent = rgb
                        await wait_for_shutdown(shutdown, INTERVAL)

                    await turn_off(client)
            except Exception as exc:
                if not shutdown.is_set():
                    state["_connection"] = "RECONNECTING"
                    console.status(f"Connection lost: {exc}. Reconnecting in 2s.", "warning")
                    console.hud(state, force=True)
                    await wait_for_shutdown(shutdown, 2.0)
    except KeyboardInterrupt:
        shutdown.set()
    finally:
        shutdown.set()
        if mic_stop:
            try:
                mic_stop(wait_for_stop=False)
            except Exception:
                pass
        if keyboard_stop:
            keyboard_stop()
        if dashboard:
            dashboard.close()
        if hud_task:
            await asyncio.gather(hud_task, return_exceptions=True)
        worker.cancel()
        timer_task.cancel()
        await asyncio.gather(worker, timer_task, return_exceptions=True)
        await feedback.close()
        console.status("Lumen signed off. Goodbye.", "success")


def print_microphones() -> None:
    devices, default_index = microphone_devices()
    if not devices:
        print("No microphone devices were reported by PortAudio.")
        return
    print("Available PortAudio devices (* = OS default, input = can record):")
    for index, (name, input_channels) in enumerate(devices):
        marker = "*" if index == default_index else " "
        kind = f"input ×{input_channels}" if input_channels else "output only"
        print(f" {marker} {index}: {name} [{kind}]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice and keyboard controlled Bluetooth LED")
    parser.add_argument("--list-mics", action="store_true", help="list microphone devices and exit")
    parser.add_argument("--test-mic", action="store_true", help="open the selected microphone and measure its input")
    args = parser.parse_args()
    if args.list_mics:
        print_microphones()
        return
    if args.test_mic:
        raise SystemExit(run_microphone_test())
    asyncio.run(run())


if __name__ == "__main__":
    main()
