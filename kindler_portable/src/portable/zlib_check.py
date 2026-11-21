import asyncio
import json
import logging
import time
from collections.abc import Callable

import flet as ft
import flet_webview as fwv

from kindler import z_lib
from portable.theme import BORDER, GUTTER, Palette, mono

PROBE_PREFIX = "kindler-probe:"
PROBE_SCRIPT = """(() => {
  const markers = ["diamwall", "checking your browser", "verifying your browser"];
  const root = document.documentElement;
  const head = root ? root.outerHTML.slice(0, 4000).toLowerCase() : "";
  const challenge = markers.some((marker) => head.includes(marker));
  const state = {challenge, ready: document.readyState, cookies: document.cookie};
  console.log("kindler-probe:" + JSON.stringify(state));
})();"""
TIMEOUT_SECONDS = 90
PROBE_INTERVAL_SECONDS = 1.5
logger = logging.getLogger(__name__)


def parse_cookies(header: str) -> dict[str, str]:
    cookies = {}
    for part in header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies


class WebChallenge:
    def __init__(self, page: ft.Page, palette: Callable[[], Palette]) -> None:
        self.page = page
        self.palette = palette

    async def solve(self, mirror: str) -> tuple[dict[str, str], str]:
        loop = asyncio.get_running_loop()
        passed: asyncio.Future[dict[str, str]] = loop.create_future()
        closed: asyncio.Future[None] = loop.create_future()

        def on_console(event: fwv.WebViewConsoleMessageEvent) -> None:
            if passed.done() or not event.message.startswith(PROBE_PREFIX):
                return
            state = json.loads(event.message.removeprefix(PROBE_PREFIX))
            if not state["challenge"] and state["ready"] == "complete" and state["cookies"]:
                passed.set_result(parse_cookies(state["cookies"]))

        def on_dismiss(_: ft.Event) -> None:
            if not closed.done():
                closed.set_result(None)

        palette = self.palette()
        webview = fwv.WebView(url=f"{mirror}/", on_console_message=on_console, expand=True)
        sheet = ft.BottomSheet(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Checking Z-Library",
                            size=22,
                            weight=ft.FontWeight.W_700,
                            color=palette.ink,
                        ),
                        ft.Text(
                            "Z-Library asks every new device to pass a quick browser check. "
                            "It usually finishes on its own in a few seconds.",
                            size=13,
                            color=palette.ink_muted,
                        ),
                        ft.Container(
                            content=webview,
                            height=360,
                            border=ft.Border.all(BORDER, palette.line),
                            clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        ),
                        ft.Text("CLOSE TO CANCEL", style=mono(10, palette.ink_subtle)),
                    ],
                    spacing=12,
                    tight=True,
                ),
                padding=ft.Padding.only(left=GUTTER, right=GUTTER, top=8, bottom=20),
            ),
            bgcolor=palette.bg,
            show_drag_handle=True,
            dismissible=True,
            shape=ft.RoundedRectangleBorder(radius=0),
            on_dismiss=on_dismiss,
        )
        self.page.show_dialog(sheet)
        try:
            deadline = time.monotonic() + TIMEOUT_SECONDS
            while not passed.done():
                if closed.done():
                    raise z_lib.ZLibraryBlocked(
                        "The Z-Library check was closed before it finished."
                    )
                if time.monotonic() > deadline:
                    raise z_lib.ZLibraryBlocked(
                        "Z-Library's browser check did not finish. Try another mirror in Settings."
                    )
                await asyncio.sleep(PROBE_INTERVAL_SECONDS)
                try:
                    await webview.run_javascript(PROBE_SCRIPT)
                except Exception:
                    logger.debug("The Z-Library page is not ready for the probe yet", exc_info=True)
            user_agent = await webview.get_user_agent() or z_lib.DEFAULT_USER_AGENT
            return passed.result(), user_agent
        finally:
            if not closed.done():
                self.page.pop_dialog()
