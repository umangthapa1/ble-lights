"""In-place terminal dashboard with an independently editable command prompt."""

from __future__ import annotations

import asyncio
from collections import deque
import re
import time
from typing import Any, Callable

from prompt_toolkit import Application
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


def plain_text(text: str) -> str:
    """Strip console color codes and control characters before displaying logs."""

    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return "".join(char for char in text if char.isprintable() or char == "\n")


class TerminalHUD:
    def __init__(
        self,
        snapshot: Callable[[], dict[str, Any]],
        submit: Callable[[str], None],
        shutdown: asyncio.Event,
        *,
        input=None,
        output=None,
    ) -> None:
        self.snapshot = snapshot
        self.submit = submit
        self.shutdown = shutdown
        self.events: deque[str] = deque(maxlen=120)
        self.follow_log = True
        self.log = TextArea(read_only=True, scrollbar=False, wrap_lines=False,
                            height=Dimension(min=1), style="class:log")
        self.command = TextArea(
            height=1, prompt="  › ", multiline=False,
            history=InMemoryHistory(), accept_handler=self._accept,
            style="class:input",
        )
        keys = KeyBindings()

        @keys.add("c-c")
        @keys.add("c-d")
        def stop(event):
            self.shutdown.set()

        @keys.add("pageup")
        def older(event):
            self.follow_log = False
            self.log.buffer.cursor_up(count=5)

        @keys.add("pagedown")
        def newer(event):
            self.log.buffer.cursor_down(count=5)
            self.follow_log = self.log.buffer.document.is_cursor_at_the_end

        @keys.add("c-l")
        def latest(event):
            self.follow_log = True
            self.log.buffer.cursor_position = len(self.log.text)

        self.app = Application(
            layout=Layout(HSplit([
                Window(FormattedTextControl(self._header), height=9),
                Window(FormattedTextControl(self._log_title), height=1, style="class:label"),
                self.log,
                Window(FormattedTextControl(self._command_title),
                       height=1, style="class:muted"),
                self.command,
                Window(FormattedTextControl("  ↑↓ history   PgUp/PgDn log   Ctrl+L latest   Ctrl+C quit"),
                       height=1, style="class:muted"),
            ]), focused_element=self.command),
            key_bindings=keys, full_screen=True, refresh_interval=0.1,
            input=input, output=output,
            style=Style.from_dict({
                "": "bg:#080f1b #c8d5e2",
                "title": "bold #73e6ff", "label": "bold #6cc8e8",
                "good": "#8ce8b0", "busy": "#ffcb73", "muted": "#71869c",
                "log": "bg:#0b1422 #b9c8d7", "input": "bg:#17283b #f5f8fb",
            }),
        )

    def _width(self) -> int:
        """Use the available width so the dashboard reads as one intentional panel."""

        return max(20, self.app.output.get_size().columns or 80)

    @staticmethod
    def _fit(text: str, width: int) -> str:
        if len(text) > width:
            return text[:max(0, width - 1)] + "…"
        return text.ljust(width)

    def _box_row(self, text: str, width: int, style: str = ""):
        return [(style, "│" + self._fit(" " + text, width - 2) + "│")]

    def _columns(self, values: tuple[str, str, str], width: int, style: str = ""):
        inside = width - 2
        first = max(1, (inside - 2) // 3)
        second = first
        third = max(1, inside - first - second - 2)
        cells = [self._fit(" " + value, size) for value, size in zip(values, (first, second, third))]
        return [(style, "│" + "│".join(cells) + "│")]

    def _header(self):
        data = self.snapshot()
        width = self._width()
        connected = data["connection"] == "CONNECTED"
        spinner = "◐◓◑◒"[int(time.monotonic() * 6) % 4]
        indicator = "●" if connected else spinner
        brightness = data["brightness"]
        cell_width = max(10, (width - 2) // 3)
        bar_width = max(5, min(24, cell_width - 9))
        filled = round(brightness / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        top = "╭" + "─" * (width - 2) + "╮"
        title = f"✦ {data['name'].upper()}  /  CORE STATUS"
        divider = "├" + "─" * max(1, (width - 2) // 3) + "┼" + "─" * max(1, (width - 2) // 3) + "┼" + "─" * max(1, width - 2 - 2 * ((width - 2) // 3)) + "┤"
        rows = [
            [("class:title", top)],
            self._box_row(title + "   " + ("ONLINE" if connected else data["connection"]), width, "class:good" if connected else "class:busy"),
            [("class:muted", divider)],
            self._columns((f"BLUETOOTH  {indicator} {data['connection']}",
                           f"MIC  {data['mic']}  {data['waveform']}",
                           f"PROFILE  {data['personality'].upper()}"), width),
            self._columns((f"LIGHT  {data['power']} · {data['color'].upper()}",
                           f"SCENE  {data['scene'].upper()}",
                           f"LEVEL  {bar} {brightness}%"), width),
            self._columns((f"VOICE  {data['voice']}",
                           f"SPEECH  {data['feedback']}",
                           f"TIMER  {data['timer']}"), width),
            self._columns((f"ALARM  {data['alarm']}",
                           f"DEVICE  {data['device']}",
                           "HUD  LIVE"), width, "class:muted"),
            self._box_row(f"LOCAL  {data['clock']}  ·  {data['calendar']}", width, "class:label"),
            [("class:title", "╰" + "─" * (width - 2) + "╯")],
        ]
        rendered = []
        for row in rows:
            rendered.extend(row)
            rendered.append(("", "\n"))
        return rendered

    def _log_title(self):
        count = len(self.events)
        prefix = f"  ACTIVITY LOG  ·  {count:02d} EVENTS  ·  LIVE "
        return prefix + "─" * max(0, self._width() - len(prefix))

    def _command_title(self):
        prefix = "  ── COMMAND "
        return prefix + "─" * max(0, self._width() - len(prefix))

    def _accept(self, buffer) -> bool:
        text = buffer.text.strip()
        if text:
            self.submit(text)
        buffer.reset()
        return False  # The buffer is cleared manually, preserving its history.

    def write(self, text: str) -> None:
        for line in plain_text(text).splitlines():
            event = self._event_line(line)
            if event:
                self.events.append(event)
        text = "\n".join(self.events) or "  Waiting for system events…"
        cursor = len(text) if self.follow_log else min(self.log.buffer.cursor_position, len(text))
        self.log.buffer.set_document(Document(text, cursor), bypass_readonly=True)
        self.app.invalidate()

    def _event_line(self, line: str) -> str | None:
        """Turn verbose scrolling-console output into compact HUD activity."""

        body = line.strip()
        if (
            not body
            or body.startswith("MIC ")
            or body.startswith(("╭", "╰", "│", "DEVICE", "VOICE"))
            or "Still listening" in body
        ):
            return None

        tag = "INFO"
        if body.startswith("INPUT RECEIVED"):
            tag, body = "INPUT", body.removeprefix("INPUT RECEIVED").strip()
        elif body.startswith("LIGHT"):
            tag, body = "LIGHT", body.removeprefix("LIGHT").strip()
        elif body.startswith("↳"):
            tag, body = "STATE", body.removeprefix("↳").strip()
        elif body.startswith("Speaking via"):
            tag = "VOICE"
        elif body.startswith("Recognized via"):
            tag = "STT"
        elif body.startswith("△"):
            tag, body = "WARN", body[1:].strip()
        elif body.startswith("×"):
            tag, body = "ERROR", body[1:].strip()
        elif body.startswith("●"):
            tag, body = "OK", body[1:].strip()
        elif body.startswith("◇"):
            tag, body = "INFO", body[1:].strip()
        elif body.startswith("TIP"):
            tag = "HELP"

        timestamp = time.strftime("%H:%M:%S")
        return f"{timestamp}  {tag:<5}  {body}"

    async def run(self) -> None:
        app_task = asyncio.create_task(
            self.app.run_async(set_exception_handler=False, handle_sigint=False),
        )
        shutdown_task = asyncio.create_task(self.shutdown.wait())
        try:
            done, _ = await asyncio.wait(
                (app_task, shutdown_task), return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_task in done and not app_task.done():
                self.app.exit()
            await app_task
        except EOFError:
            pass
        finally:
            shutdown_task.cancel()
            await asyncio.gather(shutdown_task, return_exceptions=True)
            self.shutdown.set()

    def close(self) -> None:
        if self.app.is_running and not self.app.is_done:
            self.app.exit()
