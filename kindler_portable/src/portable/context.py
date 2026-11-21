from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import flet as ft

from portable.device import Device
from portable.store import Store
from portable.theme import Palette
from portable.widgets import toast


@dataclass
class Context:
    page: ft.Page
    store: Store
    device: Device
    palette: Palette
    go_to: Callable[[int], Awaitable[None]]
    restyle: Callable[[], Awaitable[None]]

    def notify(self, message: str, *, error: bool = False) -> None:
        toast(self.page, message, self.palette, error=error)

    def spawn(self, handler: Callable[..., Awaitable[object]], *args: object) -> None:
        self.page.run_task(handler, *args)
