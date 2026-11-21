import asyncio

import flet as ft

from kindler import library
from kindler.preferences import preferences
from kindler.workflow import result_key
from portable.context import Context
from portable.store import SEARCH_FORMATS, preferred_format
from portable.theme import BORDER_THIN, EASE, FAST, GUTTER, MEDIUM, SHADOW_SMALL, SLOW, mono
from portable.widgets import (
    button,
    card,
    cover_image,
    empty_state,
    field,
    icon_button,
    pressable,
    select,
    spinner,
    tag,
    update_if_mounted,
)


class ResultCard:
    def __init__(self, view: "SearchView", result: library.SearchResult) -> None:
        self.view = view
        self.result = result
        self.key = result_key(result)
        self.formats = ft.Row(spacing=6, wrap=True, run_spacing=6)
        self.tags = ft.Row(spacing=6, wrap=True, run_spacing=4)
        self.check = ft.Container(
            width=28,
            height=28,
            alignment=ft.Alignment.CENTER,
            animate=ft.Animation(FAST, EASE),
            animate_scale=ft.Animation(MEDIUM, ft.AnimationCurve.EASE_OUT_BACK),
        )
        palette = view.ctx.palette
        body = ft.Row(
            [
                cover_image(result.cover_url, palette, 58, 84),
                ft.Column(
                    [
                        ft.Text(
                            result.title,
                            size=15,
                            weight=ft.FontWeight.W_700,
                            color=palette.ink,
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            result.author,
                            size=13,
                            color=palette.ink_muted,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        self.tags,
                        ft.Container(content=self.formats, padding=ft.Padding.only(top=4)),
                    ],
                    spacing=4,
                    expand=True,
                ),
                ft.GestureDetector(
                    content=ft.Container(content=self.check, padding=4),
                    on_tap=self.toggle,
                    mouse_cursor=ft.MouseCursor.CLICK,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.START,
            spacing=12,
        )
        self.root = card(body, palette, padding=12)
        self.refresh()

    async def toggle(self, _: ft.Event) -> None:
        await self.view.ctx.device.select()
        self.view.toggle(self.result)

    def refresh(self) -> None:
        ctx = self.view.ctx
        palette = ctx.palette
        selected = self.key in self.view.selected
        fetching = self.key in ctx.store.fetching
        self.check.bgcolor = palette.accent if selected else palette.surface
        self.check.border = ft.Border.all(BORDER_THIN, palette.line)
        self.check.scale = 1.08 if selected else 1
        self.check.content = (
            ft.Icon(ft.Icons.CHECK, size=18, color=palette.on_accent) if selected else None
        )

        source = library.SOURCES.get(self.result.source, self.result.source)
        tags: list[ft.Control] = [tag(source, palette.ink_muted, palette.bg_alt, palette)]
        if self.result.language:
            tags.append(tag(self.result.language, palette.ink_muted, palette.bg_alt, palette))
        sent_at = ctx.store.search.marks.get(self.key)
        if sent_at:
            tags.append(
                tag(f"Sent {sent_at:%d.%m}", palette.success_ink, palette.success_bg, palette)
            )
        self.tags.controls = tags

        if fetching:
            self.formats.controls = [
                ft.Row(
                    [
                        spinner(palette, 16),
                        ft.Text("Downloading", style=mono(11, palette.ink_muted)),
                    ],
                    spacing=8,
                )
            ]
        else:
            self.formats.controls = [
                pressable(
                    ft.Text(name.upper(), style=mono(11, palette.ink)),
                    palette,
                    ctx.device,
                    self._fetcher(name),
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    depth=SHADOW_SMALL,
                    border=BORDER_THIN,
                    bgcolor=palette.accent_soft if name == "epub" else palette.surface,
                )
                for name in self.result.formats
            ]

    def _fetcher(self, book_format: str):
        async def fetch() -> None:
            await self.view.fetch([(self.result, book_format)])

        return fetch


class SearchView:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.selected: dict[str, library.SearchResult] = {}
        self.cards: dict[str, ResultCard] = {}
        self._status_boxes: dict[str, ft.Container] = {}
        self._status_signatures: dict[str, str] = {}
        state = ctx.store.search
        palette = ctx.palette
        self.query = field(
            "Title, author or ISBN",
            palette,
            value=state.query,
            on_submit=self.submit,
            expand=True,
        )
        self.sources = ft.Row(spacing=8, scroll=ft.ScrollMode.HIDDEN)
        self.language = select(
            library.LANGUAGES,
            state.language,
            palette,
            self.on_language,
            title="Language",
            expand=True,
        )
        self.book_format = select(
            SEARCH_FORMATS, state.book_format, palette, self.on_format, title="Format", expand=True
        )
        self.account = ft.Container(animate_size=ft.Animation(MEDIUM, EASE))
        self.results = ft.Column(spacing=14)
        self.status = ft.AnimatedSwitcher(
            content=ft.Container(),
            duration=MEDIUM,
            reverse_duration=FAST,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=EASE,
        )
        self.more = ft.Container()
        self.bar_count = ft.Text(style=mono(12, palette.bg))
        self.bar = ft.Container(
            content=ft.Row(
                [
                    self.bar_count,
                    ft.Container(expand=True),
                    ft.TextButton(
                        "Clear",
                        on_click=self.clear_selection,
                        style=ft.ButtonStyle(color=palette.bg),
                    ),
                    button(
                        "Add",
                        palette,
                        ctx.device,
                        self.add_selected,
                        icon=ft.Icons.PLAYLIST_ADD,
                        kind="sun",
                        compact=True,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=palette.ink,
            padding=ft.Padding.only(left=16, right=8, top=8, bottom=8),
            border=ft.Border.all(BORDER_THIN, palette.line),
            margin=ft.Margin.symmetric(horizontal=GUTTER, vertical=12),
            offset=ft.Offset(0, 2),
            opacity=0,
            animate_offset=ft.Animation(SLOW, EASE),
            animate_opacity=ft.Animation(MEDIUM, EASE),
            left=0,
            right=0,
            bottom=0,
        )
        scroll = ft.ListView(
            controls=[
                ft.Column(
                    [
                        ft.Text(
                            "Find a book",
                            size=30,
                            weight=ft.FontWeight.W_700,
                            color=palette.ink,
                        ),
                        ft.Text(
                            "Search free catalogues and your Z-Library account.",
                            size=14,
                            color=palette.ink_muted,
                        ),
                    ],
                    spacing=2,
                ),
                ft.Row(
                    [
                        self.query,
                        icon_button(
                            ft.Icons.SEARCH,
                            palette,
                            ctx.device,
                            self.search,
                            tooltip="Search",
                            kind="primary",
                            size=54,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                self.sources,
                ft.Row([self.language, self.book_format], spacing=10),
                self.account,
                self.status,
                self.results,
                self.more,
                ft.Container(height=90),
            ],
            spacing=16,
            padding=ft.Padding.only(left=GUTTER, right=GUTTER - 4, top=12),
            expand=True,
        )
        self.root = ft.Stack([scroll, self.bar], expand=True)
        ctx.store.subscribe(self.render)
        self.render()
        ctx.spawn(self.load_accounts)

    async def submit(self, _: ft.Event) -> None:
        await self.search()

    async def search(self) -> None:
        query = (self.query.value or "").strip()
        if not query:
            self.ctx.notify("Type a title or an author first.")
            return
        state = self.ctx.store.search
        state.query = query
        state.next_cursor = None
        self.selected.clear()
        self.cards.clear()
        await self.ctx.device.tap()
        await self.ctx.store.run_search()

    async def load_more(self) -> None:
        await self.ctx.store.run_search(append=True)

    async def pick_source(self, source: str) -> None:
        state = self.ctx.store.search
        if state.source == source:
            return
        state.source = source
        await self.ctx.device.select()
        self.render()
        await self.load_accounts()
        if state.query:
            await self.search()

    async def on_language(self, event: ft.Event) -> None:
        self.ctx.store.search.language = event.control.value or ""
        if self.ctx.store.search.query:
            await self.search()

    async def on_format(self, event: ft.Event) -> None:
        self.ctx.store.search.book_format = event.control.value or ""
        if self.ctx.store.search.query:
            await self.search()

    async def load_accounts(self) -> None:
        palette = self.ctx.palette
        accounts = await self.ctx.store.accounts()
        show = self.ctx.store.search.source == "zlib" and len(accounts) > 1
        self.account.content = (
            select(
                {account.email: account.email for account in accounts},
                preferences.zlib_active,
                palette,
                self.on_account,
                title="Z-Library account",
                expand=True,
            )
            if show
            else None
        )
        update_if_mounted(self.account)

    async def on_account(self, event: ft.Event) -> None:
        await self.ctx.store.choose_account(event.control.value or "")
        await self.ctx.device.select()
        self.ctx.notify(f"Using {event.control.value}")

    def toggle(self, result: library.SearchResult) -> None:
        key = result_key(result)
        if key in self.selected:
            del self.selected[key]
        else:
            self.selected[key] = result
        self.render()

    async def clear_selection(self, _: ft.Event) -> None:
        self.selected.clear()
        self.render()

    async def add_selected(self) -> None:
        picks = [(result, preferred_format(result)) for result in self.selected.values()]
        self.selected.clear()
        self.render()
        await self.fetch(picks)

    async def fetch(self, picks: list[tuple[library.SearchResult, str]]) -> None:
        outcomes = await asyncio.gather(
            *(self.ctx.store.fetch(result, book_format) for result, book_format in picks),
            return_exceptions=True,
        )
        added = 0
        problems: list[str] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                problems.append(str(outcome) or "Download failed.")
            elif outcome.status == "review":
                added += 1
            else:
                problems.append(outcome.error or "Download failed.")
        if added:
            await self.ctx.device.success()
            noun = "book" if added == 1 else "books"
            self.ctx.notify(f"Added {added} {noun} to the queue.")
        if problems:
            await self.ctx.device.warn()
            self.ctx.notify(problems[0], error=True)

    def render(self) -> None:
        palette = self.ctx.palette
        state = self.ctx.store.search
        self.sources.controls = [
            pressable(
                ft.Text(
                    label.upper(),
                    style=mono(11, palette.on_accent if state.source == code else palette.ink),
                ),
                palette,
                self.ctx.device,
                self._source_picker(code),
                bgcolor=palette.accent if state.source == code else palette.surface,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                depth=SHADOW_SMALL,
                border=BORDER_THIN,
            )
            for code, label in library.SOURCES.items()
        ]

        status = self._status()
        if self.status.content is not status:
            self.status.content = status
        keys = []
        for result in state.results:
            key = result_key(result)
            keys.append(key)
            if key not in self.cards:
                self.cards[key] = ResultCard(self, result)
            else:
                self.cards[key].refresh()
        self.results.controls = [self.cards[key].root for key in keys]

        self.more.content = (
            button(
                "Load more",
                palette,
                self.ctx.device,
                self.load_more,
                kind="secondary",
                busy=state.loading,
                expand=True,
            )
            if state.next_cursor and state.results
            else None
        )

        count = len(self.selected)
        self.bar_count.value = f"{count} SELECTED"
        self.bar.offset = ft.Offset(0, 0 if count else 2)
        self.bar.opacity = 1 if count else 0
        update_if_mounted(self.root)

    def _status(self) -> ft.Control:
        key, signature = self._status_state()
        box = self._status_boxes.setdefault(key, ft.Container(key=key))
        if self._status_signatures.get(key) != signature:
            box.content = self._status_body(key)
            self._status_signatures[key] = signature
        return box

    def _status_state(self) -> tuple[str, str]:
        state = self.ctx.store.search
        if state.loading and not state.results:
            return "loading", ""
        if state.error:
            return "error", state.error
        if state.searched and not state.results:
            return "empty", state.query
        if not state.searched:
            return "idle", ""
        return "results", ""

    def _status_body(self, key: str) -> ft.Control | None:
        palette = self.ctx.palette
        state = self.ctx.store.search
        if key == "loading":
            return ft.Container(
                content=ft.Row(
                    [spinner(palette), ft.Text("Searching…", color=palette.ink_muted, size=15)],
                    spacing=12,
                ),
                padding=ft.Padding.symmetric(vertical=24),
            )
        if key == "error":
            return card(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.WIFI_OFF, color=palette.hot, size=20),
                                ft.Text(state.error or "", color=palette.ink, size=14, expand=True),
                            ],
                            spacing=10,
                        ),
                        button(
                            "Try again",
                            palette,
                            self.ctx.device,
                            self.search,
                            icon=ft.Icons.REFRESH,
                            kind="plain",
                            compact=True,
                        ),
                    ],
                    spacing=12,
                ),
                palette,
                bgcolor=palette.failed_bg,
            )
        if key == "empty":
            return empty_state(
                ft.Icons.SEARCH_OFF,
                "Nothing found",
                f"No Kindle-ready books for “{state.query}”. Try another source or spelling.",
                palette,
            )
        if key == "idle":
            return empty_state(
                ft.Icons.AUTO_STORIES,
                "What are we reading?",
                "Pick a source, search, then tap a format or select a few books at once.",
                palette,
            )
        return None

    def _source_picker(self, source: str):
        async def pick() -> None:
            await self.pick_source(source)

        return pick
