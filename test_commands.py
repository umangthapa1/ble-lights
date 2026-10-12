"""Command-level checks; no microphone, network, or Bluetooth hardware needed."""

import copy
from datetime import datetime
import unittest
from unittest.mock import patch

from main import (
    COLORS,
    EFFECTS,
    alarm_rgb,
    apply_command,
    apply_due_alarm,
    apply_due_timer,
    effect_rgb,
    hud_snapshot,
    local_clock,
    personality_response,
    scene_label,
    target_rgb,
    timer_label,
)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "on": True,
            "color": COLORS["warm"],
            "color_name": "warm",
            "brightness": 1.0,
            "mode": None,
            "effect": None,
            "effect_color": None,
            "timer": None,
            "alarm": None,
        }

    def test_sleep_is_dim_and_warm_and_restores_after_power_off(self):
        self.state["on"] = False
        self.state["color"] = COLORS["blue"]
        result = apply_command("turn on sleep mode", self.state)
        self.assertTrue(result.changed)
        self.assertFalse(result.should_exit)
        self.assertEqual(self.state["color"], COLORS["warm"])
        self.assertEqual(self.state["brightness"], 0.10)
        self.assertEqual(scene_label(self.state), "sleep")
        self.assertEqual(target_rgb(self.state), (25.5, 14.0, 4.0))
        apply_command("off", self.state)
        self.assertEqual(target_rgb(self.state), (0.0, 0.0, 0.0))
        apply_command("on", self.state)
        self.assertEqual(scene_label(self.state), "sleep")
        self.assertEqual(self.state["brightness"], 0.10)

    def test_other_modes_and_aliases(self):
        cases = [
            ("night light", "night", "warm", 0.05),
            ("chill mode", "relax", "warm", 0.35),
            ("study mode", "focus", "white", 1.0),
            ("reading mode", "reading", "warm", 0.80),
            ("cinema scene", "movie", "purple", 0.20),
            ("party mode", "party", "pink", 0.85),
            ("bedtime", "sleep", "warm", 0.10),
        ]
        for command, mode, color, brightness in cases:
            with self.subTest(command=command):
                self.assertTrue(apply_command(command, self.state).changed)
                self.assertEqual(self.state["mode"], mode)
                self.assertEqual(self.state["color"], COLORS[color])
                self.assertEqual(self.state["brightness"], brightness)
                self.assertTrue(self.state["on"])

    def test_numeric_brightness_changes_the_led_target(self):
        for value in (10, 20, 30, 40, 50, 29, 75, 100):
            with self.subTest(value=value):
                result = apply_command(f"brightness {value}", self.state)
                self.assertTrue(result.changed)
                self.assertEqual(self.state["brightness"], value / 100)
                self.assertIn(f"at {value}%", result.summary)
                self.assertAlmostEqual(target_rgb(self.state)[0], 255 * value / 100)

    def test_spoken_numbers_and_percentage_phrases(self):
        cases = {
            "brightness ten": 10,
            "brightness twenty": 20,
            "brightness thirty": 30,
            "brightness forty": 40,
            "brightness fifty percent": 50,
            "set brightness to twenty five": 25,
            "brightness one hundred": 100,
            "bright 50": 50,
            "britness 30": 30,
            "intensity at 40": 40,
            "make the lights 20%": 20,
            "seventy five percent": 75,
            "30": 30,
        }
        for command, value in cases.items():
            with self.subTest(command=command):
                apply_command(command, self.state)
                self.assertEqual(self.state["brightness"], value / 100)

    def test_scene_overrides_and_commands_are_applied_in_order(self):
        apply_command("sleep mode brightness 30 off", self.state)
        self.assertFalse(self.state["on"])
        self.assertEqual(self.state["color"], COLORS["warm"])
        self.assertEqual(self.state["brightness"], 0.30)
        self.assertEqual(scene_label(self.state), "sleep (adjusted)")
        apply_command("on", self.state)
        self.assertTrue(self.state["on"])
        apply_command("brightness 50 movie mode", self.state)
        self.assertEqual(self.state["brightness"], 0.20)
        self.assertEqual(scene_label(self.state), "movie")
        apply_command("blue", self.state)
        self.assertEqual(self.state["color"], COLORS["blue"])
        self.assertEqual(scene_label(self.state), "")

    def test_zero_restores_previous_brightness_and_large_values_are_capped(self):
        apply_command("brightness 40", self.state)
        apply_command("brightness zero", self.state)
        self.assertFalse(self.state["on"])
        self.assertEqual(self.state["brightness"], 0.40)
        apply_command("on", self.state)
        self.assertEqual(target_rgb(self.state)[0], 102.0)
        apply_command("brightness 150", self.state)
        self.assertEqual(self.state["brightness"], 1.0)
        apply_command("brightness -10", self.state)
        self.assertFalse(self.state["on"])

    def test_dimming_a_night_scene_does_not_increase_brightness(self):
        apply_command("night", self.state)
        apply_command("dimmer", self.state)
        self.assertEqual(self.state["brightness"], 0.01)
        apply_command("brighter", self.state)
        self.assertAlmostEqual(self.state["brightness"], 0.21)
        apply_command("half", self.state)
        self.assertEqual(self.state["brightness"], 0.5)
        apply_command("full", self.state)
        self.assertEqual(self.state["brightness"], 1.0)

    def test_ambient_effects_animate_and_can_be_stopped(self):
        for effect in EFFECTS:
            with self.subTest(effect=effect):
                result = apply_command(f"{effect} mode", self.state)
                self.assertTrue(result.changed)
                self.assertEqual(self.state["effect"], effect)
                self.assertEqual(scene_label(self.state), f"{effect} effect")
                first = effect_rgb(effect, 0.0)
                second = effect_rgb(effect, 5.0)
                self.assertTrue(all(0 <= channel <= 255 for channel in first + second))
        apply_command("stop effect", self.state)
        self.assertIsNone(self.state["effect"])
        self.assertEqual(scene_label(self.state), "")

    def test_effect_can_use_a_spoken_base_color(self):
        apply_command("breathing blue", self.state)
        self.assertEqual(self.state["effect"], "breathing")
        self.assertEqual(self.state["effect_color"], COLORS["blue"])
        self.assertEqual(self.state["color_name"], "blue")
        purple_before = effect_rgb("breathing", 0, COLORS["purple"])
        purple_after = effect_rgb("breathing", 1, COLORS["purple"])
        self.assertNotEqual(purple_before, purple_after)

    def test_thunder_and_police_effect_aliases(self):
        for command, effect in (("thunder mode", "thunder"), ("lightning", "thunder"), ("police mode", "police"), ("emergency", "police")):
            with self.subTest(command=command):
                result = apply_command(command, self.state)
                self.assertTrue(result.changed)
                self.assertEqual(self.state["effect"], effect)
                for moment in (0.0, 1.0, 2.5, 5.0):
                    color = effect_rgb(effect, moment)
                    self.assertTrue(all(0 <= channel <= 255 for channel in color))
                    if effect == "thunder":
                        self.assertIn(color, ((0.0, 0.0, 0.0), (255.0, 255.0, 255.0)))

    def test_advanced_effects_and_aliases(self):
        cases = {
            "aurora mode": "aurora",
            "northern lights": "aurora",
            "lava mode": "lava",
            "molten": "lava",
            "rain mode": "rain",
            "cloud mode": "cloud",
            "starlight": "starlight",
            "stars": "starlight",
            "matrix mode": "matrix",
            "hacker": "matrix",
            "warning mode": "warning",
            "alert": "warning",
        }
        for command, effect in cases.items():
            with self.subTest(command=command):
                self.assertTrue(apply_command(command, self.state).changed)
                self.assertEqual(self.state["effect"], effect)
                color = effect_rgb(effect, 2.0)
                self.assertTrue(all(0 <= channel <= 255 for channel in color))

    def test_visual_alarm_can_ring_acknowledge_and_snooze(self):
        result = apply_command("alarm in 10 minutes", self.state)
        self.assertTrue(result.changed)
        self.assertFalse(self.state["alarm"]["active"])
        deadline = self.state["alarm"]["deadline"]
        self.assertIsNone(apply_due_alarm(self.state, deadline - 1))
        self.assertIn("Alarm ringing", apply_due_alarm(self.state, deadline + 1))
        self.assertTrue(self.state["alarm"]["active"])
        red_flash = alarm_rgb(0.2)
        self.assertTrue(all(0 <= channel <= 255 for channel in red_flash))
        snooze = apply_command("snooze for 5 minutes", self.state)
        self.assertTrue(snooze.changed)
        self.assertFalse(self.state["alarm"]["active"])
        dismiss = apply_command("acknowledge alarm", self.state)
        self.assertTrue(dismiss.changed)
        self.assertIsNone(self.state["alarm"])

        apply_command("set an alarm in 1 minute", self.state)
        self.assertFalse(apply_command("alarm status", self.state).changed)
        self.assertTrue(apply_command("cancel alarm", self.state).changed)

    def test_normal_light_state_without_alarm_is_safe(self):
        self.assertIsNone(self.state["alarm"])
        self.assertEqual(target_rgb(self.state), (255.0, 140.0, 40.0))
        self.state["effect"] = "ocean"
        ocean_target = target_rgb(self.state, 1.0)
        self.assertTrue(all(0 <= channel <= 255 for channel in ocean_target))

    def test_light_blue_and_natural_aliases(self):
        for command in ("light blue", "light-blue", "lightblue", "sky blue", "ice blue"):
            with self.subTest(command=command):
                result = apply_command(command, self.state)
                self.assertTrue(result.changed)
                self.assertEqual(self.state["color"], COLORS["light_blue"])
                self.assertEqual(self.state["color_name"], "light_blue")
                self.assertIn("light blue", result.summary)

    def test_timer_commands_apply_later_without_blocking_the_light(self):
        result = apply_command("sleep in 30 minutes", self.state)
        self.assertTrue(result.changed)
        self.assertEqual(self.state["timer"]["action"], "sleep")
        self.assertEqual(self.state["color"], COLORS["warm"])
        deadline = self.state["timer"]["deadline"]
        self.assertIsNone(apply_due_timer(self.state, deadline - 1))
        message = apply_due_timer(self.state, deadline + 1)
        self.assertEqual(message, "Timer finished — sleep mode is on.")
        self.assertEqual(self.state["mode"], "sleep")
        self.assertIsNone(self.state["timer"])

        apply_command("turn off in 2 hours", self.state)
        self.assertEqual(self.state["timer"]["action"], "off")
        apply_command("cancel timer", self.state)
        self.assertIsNone(self.state["timer"])

        apply_command("dim to 10% in 20 minutes", self.state)
        self.assertEqual(self.state["timer"]["action"], "command")
        self.assertEqual(self.state["timer"]["command"], "dim to 10%")
        self.assertIn("dim to 10%", timer_label(self.state, self.state["timer"]["deadline"]))
        apply_command("cancel timer", self.state)

        apply_command("ocean mode for 5 minutes", self.state)
        self.assertEqual(self.state["effect"], "ocean")
        self.assertEqual(self.state["timer"]["action"], "off")
        apply_command("cancel timer", self.state)

    def test_undo_repeat_and_control_aliases(self):
        apply_command("blue", self.state)
        apply_command("brightness 30", self.state)
        apply_command("undo", self.state)
        self.assertEqual(self.state["brightness"], 1.0)
        apply_command("repeat", self.state)
        self.assertEqual(self.state["brightness"], 0.30)
        apply_command("toggle", self.state)
        self.assertFalse(self.state["on"])
        apply_command("toggle light", self.state)
        self.assertTrue(self.state["on"])
        apply_command("lights out", self.state)
        self.assertFalse(self.state["on"])
        apply_command("wake me up", self.state)
        self.assertTrue(self.state["on"])

    def test_queries_and_unrecognized_input_leave_the_state_unchanged(self):
        apply_command("sleep mode", self.state)
        before = copy.deepcopy(self.state)
        for command in ("modes", "help", "status", "brightness banana", "there are 20 chairs"):
            with self.subTest(command=command):
                self.assertFalse(apply_command(command, self.state).changed)
                self.assertEqual(self.state, before)
        self.assertIn("sleep mode", apply_command("status", self.state).summary)
        self.assertTrue(apply_command("quit", self.state).should_exit)

    def test_personality_profiles_and_assistant_name_do_not_enter_light_history(self):
        apply_command("blue", self.state)
        light = copy.deepcopy(self.state)
        history = copy.deepcopy(self.state["_history"])

        self.assertTrue(apply_command("personality playful", self.state).changed)
        self.assertEqual(self.state["personality"], "playful")
        self.assertEqual(personality_response("Lights on.", self.state), "Done — Lights on.")
        self.assertTrue(apply_command("set assistant name Jarvis", self.state).changed)
        self.assertEqual(self.state["assistant_name"], "Jarvis")
        self.assertEqual(light_snapshot_fields(self.state), light_snapshot_fields(light))
        self.assertEqual(self.state["_history"], history)

        apply_command("personality jarvis", self.state)
        self.assertEqual(self.state["personality"], "cinematic")
        self.assertEqual(personality_response("Lights on.", self.state), "As you wish. Lights on.")
        self.assertFalse(apply_command("personality banana", self.state).changed)

    def test_hud_snapshot_contains_live_assistant_and_light_state(self):
        self.state.update(
            assistant_name="Jarvis", personality="cinematic", _connection="CONNECTED",
            _mic_status="READY", _mic_phase="listening",
        )
        apply_command("brightness 40", self.state)
        snapshot = hud_snapshot(self.state)
        self.assertEqual(snapshot["name"], "Jarvis")
        self.assertEqual(snapshot["connection"], "CONNECTED")
        self.assertEqual(snapshot["brightness"], 40)
        self.assertEqual(snapshot["rgb"], (102, 56, 16))

    def test_clock_queries_answer_without_changing_light_history_or_timers(self):
        apply_command("ocean mode", self.state)
        apply_command("off in 20 minutes", self.state)
        apply_command("alarm in 10 minutes", self.state)
        before = copy.deepcopy(self.state)
        cases = {
            "time": "The time is 9:42 PM.",
            "What is the time?": "The time is 9:42 PM.",
            "what’s the time": "The time is 9:42 PM.",
            "what time is it now": "The time is 9:42 PM.",
            "Can you please tell me the time?": "The time is 9:42 PM.",
            "date": "Today's date is October 10, 2026.",
            "What's today's date?": "Today's date is October 10, 2026.",
            "what is the date today": "Today's date is October 10, 2026.",
            "day": "Today is Saturday.",
            "what day is it": "Today is Saturday.",
            "what day is today please": "Today is Saturday.",
            "what day of the week is it": "Today is Saturday.",
        }
        with patch("main.datetime") as clock:
            clock.now.return_value.astimezone.return_value = datetime(2026, 10, 10, 21, 42, 3)
            for command, answer in cases.items():
                with self.subTest(command=command):
                    result = apply_command(command, self.state)
                    self.assertFalse(result.changed)
                    self.assertEqual(result.category, "clock")
                    self.assertEqual(result.summary, answer)
                    self.assertEqual(result.spoken, answer)
                    self.assertEqual(self.state, before)

        for command in ("time remaining", "timer status", "time in 5 minutes", "date for 5 minutes"):
            with self.subTest(command=command):
                result = apply_command(command, self.state)
                self.assertNotEqual(result.category, "clock")
                self.assertFalse(result.changed)
                self.assertEqual(self.state, before)

    def test_local_clock_and_hud_roll_over_at_midnight_and_handle_noon(self):
        cases = [
            (datetime(2028, 2, 28, 23, 59, 59), "11:59 PM", "11:59:59 PM", "Monday", "February 28, 2028"),
            (datetime(2028, 2, 29, 0, 0, 0), "12:00 AM", "12:00:00 AM", "Tuesday", "February 29, 2028"),
            (datetime(2028, 2, 29, 12, 0, 1), "12:00 PM", "12:00:01 PM", "Tuesday", "February 29, 2028"),
        ]
        with patch("main.datetime") as clock:
            for now, short_time, full_time, day, date in cases:
                with self.subTest(now=now):
                    clock.now.return_value.astimezone.return_value = now
                    self.assertEqual(local_clock(), {"time": short_time, "clock": full_time, "day": day, "date": date})
                    snapshot = hud_snapshot(self.state)
                    self.assertEqual(snapshot["clock"], full_time)
                    self.assertEqual(snapshot["calendar"], f"{day} · {date}")


def light_snapshot_fields(state):
    return {key: state.get(key) for key in ("on", "color", "color_name", "brightness", "mode", "effect", "effect_color", "timer", "alarm")}


if __name__ == "__main__":
    unittest.main()
