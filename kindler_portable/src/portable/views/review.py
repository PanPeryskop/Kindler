from collections.abc import Awaitable, Callable

import flet as ft

from kindler.db import Job
from portable.context import Context
from portable.store import StoreError
from portable.theme import BORDER, GUTTER
from portable.widgets import button, cover_image, field, pressable, update_if_mounted


class ReviewSheet:
    def __init__(self, ctx: Context, job: Job, on_send: Callable[[Job], Awaitable[None]]) -> None:
        self.ctx = ctx
        self.job = job
        palette = ctx.palette
        self.title = field("Title", palette, value=job.title or "")
        self.author = field("Author", palette, value=job.author or "")
        self.cover = ft.Container()
        self.actions = ft.Row(spacing=10)
        self.send = ft.Row()
        self.on_send = on_send
        self.sheet = ft.BottomSheet(
            content=ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "Book details", size=22, weight=ft.FontWeight.W_700, color=palette.ink
                        ),
                        ft.Text(job.filename, size=12, color=palette.ink_subtle, max_lines=1),
                        ft.Row(
                            [
                                self.cover,
                                ft.Column([self.title, self.author], spacing=12, expand=True),
                            ],
                            spacing=14,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        self.actions,
                        self.send,
                    ],
                    spacing=14,
                    tight=True,
                ),
                padding=ft.Padding.only(left=GUTTER, right=GUTTER, top=8, bottom=24),
            ),
            bgcolor=palette.bg,
            show_drag_handle=True,
            scrollable=True,
            shape=ft.RoundedRectangleBorder(radius=0),
            barrier_color=ft.Colors.with_opacity(0.55, "#000000"),
        )
        self._render(busy=None)

    def open(self) -> None:
        self.ctx.page.show_dialog(self.sheet)

    def _render(self, busy: str | None) -> None:
        palette = self.ctx.palette
        device = self.ctx.device
        self.cover.content = pressable(
            ft.Stack(
                [
                    cover_image(self.job.cover, palette, 92, 134),
                    ft.Container(
                        content=ft.Icon(ft.Icons.EDIT, size=14, color="#1A1A1A"),
                        bgcolor=palette.sun,
                        padding=4,
                        right=4,
                        bottom=4,
                        border=ft.Border.all(2, palette.line),
                    ),
                ]
            ),
            palette,
            device,
            self.change_cover,
            padding=0,
            border=BORDER,
        )
        self.actions.controls = [
            button(
                "Find details",
                palette,
                device,
                self.find,
                icon=ft.Icons.AUTO_FIX_HIGH,
                kind="plain",
                compact=True,
                busy=busy == "find",
                expand=True,
            ),
            button(
                "Save",
                palette,
                device,
                self.save,
                icon=ft.Icons.CHECK,
                kind="secondary",
                compact=True,
                expand=True,
            ),
        ]
        self.send.controls = [
            button(
                "Send to Kindle",
                palette,
                device,
                self.save_and_send,
                icon=ft.Icons.SEND,
                expand=True,
            )
        ]
        update_if_mounted(self.sheet)

    async def change_cover(self) -> None:
        picked = await self.ctx.device.pick_image()
        if picked is None or picked.bytes is None:
            return
        try:
            await self.ctx.store.set_cover(self.job.id, picked.bytes)
        except StoreError as exc:
            await self.ctx.device.warn()
            self.ctx.notify(str(exc), error=True)
            return
        self.job = await self.ctx.store.queue.require(self.job.id)
        await self.ctx.device.success()
        self._render(busy=None)

    async def find(self) -> None:
        self._render(busy="find")
        found = await self.ctx.store.find_metadata(
            self.job.id, self.title.value or "", self.author.value or ""
        )
        self.job = await self.ctx.store.queue.require(self.job.id)
        self.title.value = self.job.title or ""
        self.author.value = self.job.author or ""
        if found:
            await self.ctx.device.success()
            self.ctx.notify("Details and cover updated.")
        else:
            await self.ctx.device.warn()
            self.ctx.notify("No match in Open Library or Google Books.", error=True)
        self._render(busy=None)

    async def save(self) -> None:
        await self._store_details()
        await self.ctx.device.success()
        self.ctx.page.pop_dialog()

    async def save_and_send(self) -> None:
        await self._store_details()
        self.ctx.page.pop_dialog()
        await self.on_send(await self.ctx.store.queue.require(self.job.id))

    async def _store_details(self) -> None:
        await self.ctx.store.save_details(
            self.job.id, self.title.value or "", self.author.value or ""
        )
