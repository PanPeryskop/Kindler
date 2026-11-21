import flet as ft

from kindler import z_lib
from kindler.preferences import preferences
from portable.context import Context
from portable.device import Device
from portable.store import Store
from portable.theme import (
    BORDER,
    DARK,
    EASE,
    FAST,
    FONTS,
    GUTTER,
    LIGHT,
    MEDIUM,
    SHADOW_SMALL,
    Palette,
    hard_shadow,
    material_theme,
    mono,
    palette_for,
)
from portable.views.history import HistoryView
from portable.views.queue import QueueView
from portable.views.search import SearchView
from portable.views.settings import SettingsView
from portable.widgets import update_if_mounted
from portable.zlib_check import WebChallenge

TABS = (
    ("Search", ft.Icons.SEARCH, ft.Icons.MANAGE_SEARCH),
    ("Queue", ft.Icons.LAYERS_OUTLINED, ft.Icons.LAYERS),
    ("Sent", ft.Icons.HISTORY, ft.Icons.HISTORY_TOGGLE_OFF),
    ("Settings", ft.Icons.TUNE, ft.Icons.TUNE),
)
QUEUE_TAB = 1
HISTORY_TAB = 2


class Shell:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.index = 0
        self.device = Device(page)
        self.palette: Palette = LIGHT

    async def start(self) -> None:
        page = self.page
        page.title = "Kindler"
        page.fonts = FONTS
        page.padding = 0
        page.spacing = 0
        page.theme = material_theme(LIGHT)
        page.dark_theme = material_theme(DARK)
        await self.device.lock_portrait()
        self.store = Store(self.device, await self.device.data_directory())
        await self.store.start()
        if self.device.mobile:
            z_lib.use_challenge_solver(WebChallenge(page, lambda: self.palette).solve)
        page.on_platform_brightness_change = self.on_brightness
        await self.build()

    async def build(self) -> None:
        page = self.page
        self.palette = palette_for(preferences.theme, page.platform_brightness)
        palette = self.palette
        page.theme_mode = ft.ThemeMode.DARK if palette.dark else ft.ThemeMode.LIGHT
        page.bgcolor = palette.bg
        self.store.reset_listeners()
        ctx = Context(page, self.store, self.device, palette, self.go_to, self.restyle)
        self.views = [SearchView(ctx), QueueView(ctx), HistoryView(ctx), SettingsView(ctx)]
        self.tabs = [
            ft.Container(content=view.root, key=f"tab-{index}", expand=True)
            for index, view in enumerate(self.views)
        ]
        self.switcher = ft.AnimatedSwitcher(
            content=self.tabs[self.index],
            duration=MEDIUM,
            reverse_duration=FAST,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=EASE,
            switch_out_curve=EASE,
            expand=True,
        )
        self.nav = ft.NavigationBar(
            destinations=self._destinations(),
            selected_index=self.index,
            on_change=self.on_tab,
            bgcolor=palette.surface,
            indicator_color=palette.sun,
            indicator_shape=ft.RoundedRectangleBorder(radius=0),
            label_behavior=ft.NavigationBarLabelBehavior.ALWAYS_SHOW,
            elevation=0,
            border=ft.Border.only(top=ft.BorderSide(BORDER, palette.line)),
            animation_duration=MEDIUM,
        )
        page.navigation_bar = self.nav
        page.controls = [
            ft.SafeArea(
                content=ft.Column(
                    [
                        self._brand(),
                        self.switcher,
                        ft.Container(height=BORDER, bgcolor=palette.line),
                    ],
                    spacing=0,
                    expand=True,
                ),
                expand=True,
            )
        ]
        self.store.subscribe(self.refresh_badge)
        page.update()
        if self.index == HISTORY_TAB:
            await self.views[HISTORY_TAB].load()

    def _brand(self) -> ft.Control:
        palette = self.palette
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(ft.Icons.MENU_BOOK, size=18, color=palette.on_accent),
                        bgcolor=palette.accent,
                        width=34,
                        height=34,
                        alignment=ft.Alignment.CENTER,
                        border=ft.Border.all(BORDER, palette.line),
                        shadow=hard_shadow(palette, SHADOW_SMALL),
                    ),
                    ft.Text("KINDLER", style=mono(15, palette.ink, ft.FontWeight.W_800)),
                ],
                spacing=12,
            ),
            padding=ft.Padding.only(left=GUTTER, right=GUTTER, top=10, bottom=4),
        )

    def _destinations(self) -> list[ft.NavigationBarDestination]:
        palette = self.palette
        waiting = len(self.store.ready_jobs)
        destinations = []
        for index, (label, icon, selected_icon) in enumerate(TABS):
            badge = None
            if index == QUEUE_TAB and waiting:
                badge = ft.Badge(label=str(waiting), bgcolor=palette.hot, text_color="#FFFFFF")
            destinations.append(
                ft.NavigationBarDestination(
                    icon=ft.Icon(icon, color=palette.ink_muted, badge=badge),
                    selected_icon=ft.Icon(selected_icon, color="#1A1A1A", badge=badge),
                    label=label,
                )
            )
        return destinations

    def refresh_badge(self) -> None:
        self.nav.destinations = self._destinations()
        update_if_mounted(self.nav)

    async def on_tab(self, event: ft.Event) -> None:
        await self.go_to(event.control.selected_index)

    async def go_to(self, index: int) -> None:
        if index == self.index:
            return
        self.index = index
        self.nav.selected_index = index
        self.switcher.content = self.tabs[index]
        await self.device.select()
        self.page.update()
        if index == HISTORY_TAB:
            await self.views[HISTORY_TAB].load()

    async def restyle(self) -> None:
        await self.build()

    async def on_brightness(self, _: ft.Event) -> None:
        if preferences.theme == "system":
            await self.build()
