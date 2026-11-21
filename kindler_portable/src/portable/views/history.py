import flet as ft

from kindler import library
from kindler.db import Job
from portable.context import Context
from portable.theme import BORDER_THIN, EASE, GUTTER, MEDIUM, mono
from portable.widgets import button, card, cover_image, empty_state, spinner, update_if_mounted


class HistoryView:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        palette = ctx.palette
        self.count = ft.Text(style=mono(12, palette.ink_muted))
        self.rows: dict[str, ft.Dismissible] = {}
        self.list_box = ft.Column(spacing=12, key="list")
        self.empty_box = ft.Container(
            content=empty_state(
                ft.Icons.HISTORY,
                "Nothing sent yet",
                "Books you send to your Kindle are listed here.",
                palette,
                button("Find a book", palette, ctx.device, self.open_search, icon=ft.Icons.SEARCH),
            ),
            key="empty",
        )
        self.body = ft.AnimatedSwitcher(
            content=ft.Container(content=spinner(palette), padding=24, key="loading"),
            duration=MEDIUM,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=EASE,
        )
        self.root = ft.ListView(
            controls=[
                ft.Row(
                    [
                        ft.Text("Sent", size=30, weight=ft.FontWeight.W_700, color=palette.ink),
                        ft.Container(expand=True),
                        self.count,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.body,
                ft.Container(height=40),
            ],
            spacing=16,
            padding=ft.Padding.only(left=GUTTER, right=GUTTER - 4, top=12),
            expand=True,
        )

    async def load(self) -> None:
        jobs = await self.ctx.store.history()
        self.count.value = f"{len(jobs)} BOOKS" if jobs else ""
        self.rows = {job.id: self.rows.get(job.id) or self._row(job) for job in jobs}
        self.list_box.controls = list(self.rows.values())
        body = self.list_box if jobs else self.empty_box
        if self.body.content is not body:
            self.body.content = body
        update_if_mounted(self.root)

    def _row(self, job: Job) -> ft.Dismissible:
        palette = self.ctx.palette
        sent_at = job.sent_at or job.created_at
        source = library.SOURCES.get(job.source or "", "") if job.source != "upload" else "Upload"
        details = " · ".join(part for part in (source, f"{sent_at:%d %b %Y}") if part)
        content = ft.Row(
            [
                cover_image(job.cover, palette, 40, 58),
                ft.Column(
                    [
                        ft.Text(
                            job.title or job.filename,
                            size=14,
                            weight=ft.FontWeight.W_700,
                            color=palette.ink,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            job.author or "Unknown author",
                            size=12,
                            color=palette.ink_muted,
                            max_lines=1,
                        ),
                        ft.Text(details.upper(), style=mono(10, palette.ink_subtle)),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Icon(ft.Icons.CHECK_CIRCLE, color=palette.success_ink, size=20),
            ],
            spacing=12,
        )
        return ft.Dismissible(
            content=card(content, palette, padding=10, depth=3, border=BORDER_THIN),
            key=f"sent-{job.id}",
            dismiss_direction=ft.DismissDirection.END_TO_START,
            background=ft.Container(),
            secondary_background=ft.Container(
                content=ft.Row(
                    [
                        ft.Text("FORGET", style=mono(12, palette.on_accent)),
                        ft.Icon(ft.Icons.DELETE_OUTLINE, color=palette.on_accent),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=8,
                ),
                bgcolor=palette.hot,
                padding=ft.Padding.only(right=20),
            ),
            on_dismiss=self._forgetter(job),
        )

    async def open_search(self) -> None:
        await self.ctx.go_to(0)

    def _forgetter(self, job: Job):
        async def forget(_: ft.Event) -> None:
            await self.ctx.store.forget(job)
            await self.load()
            await self.ctx.device.warn()

        return forget
