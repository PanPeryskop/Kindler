import flet as ft

from kindler.db import Job
from kindler.preferences import preferences
from portable.context import Context
from portable.store import KINDLE_FORMATS, SendReport
from portable.theme import BORDER_THIN, EASE, GUTTER, MEDIUM, mono
from portable.views.review import ReviewSheet
from portable.widgets import (
    button,
    card,
    cover_image,
    empty_state,
    icon_button,
    spinner,
    tag,
    update_if_mounted,
)


class QueueView:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        palette = ctx.palette
        self.header_actions = ft.Row(spacing=10)
        self.method = ft.Container()
        self.items = ft.Column(spacing=14)
        self.rows: dict[str, ft.Dismissible] = {}
        self.list_box = ft.Container(content=self.items, key="jobs")
        self.empty_box = ft.Container(
            content=empty_state(
                ft.Icons.LAYERS_CLEAR,
                "Queue is empty",
                "Books you pick from search or add from your phone wait here.",
                palette,
                button("Find a book", palette, ctx.device, self.open_search, icon=ft.Icons.SEARCH),
            ),
            key="empty",
        )
        self.body = ft.AnimatedSwitcher(
            content=self.empty_box,
            duration=MEDIUM,
            transition=ft.AnimatedSwitcherTransition.FADE,
            switch_in_curve=EASE,
        )
        self.root = ft.ListView(
            controls=[
                ft.Row(
                    [
                        ft.Text("Queue", size=30, weight=ft.FontWeight.W_700, color=palette.ink),
                        ft.Container(expand=True),
                        self.header_actions,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self.method,
                self.body,
                ft.Container(height=40),
            ],
            spacing=16,
            padding=ft.Padding.only(left=GUTTER, right=GUTTER - 4, top=12),
            expand=True,
        )
        ctx.store.subscribe(self.render)
        self.render()

    def render(self) -> None:
        palette = self.ctx.palette
        device = self.ctx.device
        store = self.ctx.store
        ready = store.ready_jobs
        actions: list[ft.Control] = [
            icon_button(ft.Icons.UPLOAD_FILE, palette, device, self.add_files, tooltip="Add files")
        ]
        if len(ready) > 1:
            actions.append(
                button(
                    f"Send all {len(ready)}",
                    palette,
                    device,
                    self.send_all,
                    icon=ft.Icons.SEND,
                    compact=True,
                )
            )
        self.header_actions.controls = actions

        by_email = preferences.send_method == "email"
        target = preferences.kindle_address or "your Kindle address"
        self.method.content = ft.Row(
            [
                ft.Icon(
                    ft.Icons.MAIL_OUTLINE if by_email else ft.Icons.IOS_SHARE,
                    size=16,
                    color=palette.ink_muted,
                ),
                ft.Text(
                    f"E-mail to {target}" if by_email else "Share to the Kindle app",
                    size=13,
                    color=palette.ink_muted,
                    expand=True,
                ),
                ft.TextButton(
                    "Change",
                    on_click=self.open_settings,
                    style=ft.ButtonStyle(color=palette.accent),
                ),
            ],
            spacing=8,
        )

        current = {job.id for job in store.jobs}
        for job_id in self.rows.keys() - current:
            del self.rows[job_id]
        for job in store.jobs:
            content = card(self._job(job), palette, padding=12)
            row = self.rows.get(job.id)
            if row is None:
                self.rows[job.id] = self._row(job.id, content)
            else:
                row.content = content
        self.items.controls = [self.rows[job.id] for job in store.jobs]
        body = self.list_box if store.jobs else self.empty_box
        if self.body.content is not body:
            self.body.content = body
        update_if_mounted(self.root)

    def _row(self, job_id: str, content: ft.Control) -> ft.Dismissible:
        palette = self.ctx.palette
        return ft.Dismissible(
            content=content,
            key=job_id,
            dismiss_direction=ft.DismissDirection.END_TO_START,
            background=ft.Container(),
            secondary_background=ft.Container(
                content=ft.Row(
                    [
                        ft.Text("REMOVE", style=mono(12, palette.on_accent)),
                        ft.Icon(ft.Icons.DELETE_OUTLINE, color=palette.on_accent),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=8,
                ),
                bgcolor=palette.hot,
                padding=ft.Padding.only(right=20),
                border=ft.Border.all(BORDER_THIN, palette.line),
            ),
            on_dismiss=self._remover(job_id),
        )

    def _job(self, job: Job) -> ft.Control:
        palette = self.ctx.palette
        device = self.ctx.device
        sending = job.id in self.ctx.store.sending
        status = "sending" if sending else job.status
        labels = {
            "review": ("Ready", palette.accent, palette.accent_soft),
            "sending": ("Sending", palette.ink, palette.sun),
            "failed": ("Failed", palette.hot, palette.failed_bg),
        }
        text, fg, bg = labels.get(status, (status, palette.ink_muted, palette.bg_alt))
        extension = job.filename.rsplit(".", 1)[-1].lower() if "." in job.filename else ""

        info: list[ft.Control] = [
            ft.Text(
                job.title or job.filename,
                size=15,
                weight=ft.FontWeight.W_700,
                color=palette.ink,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Text(job.author or "Unknown author", size=13, color=palette.ink_muted, max_lines=1),
            ft.Row(
                [
                    tag(text, fg, bg, palette),
                    tag(extension or "file", palette.ink_muted, palette.bg_alt, palette),
                ],
                spacing=6,
            ),
        ]
        if extension and extension not in KINDLE_FORMATS:
            info.append(
                ft.Text(
                    f"Kindle can't open {extension.upper()} files.",
                    size=12,
                    color=palette.hot,
                )
            )
        if job.error:
            info.append(ft.Text(job.error, size=12, color=palette.hot))

        actions: list[ft.Control]
        if sending:
            actions = [
                ft.Row(
                    [spinner(palette, 16), ft.Text("Sending…", style=mono(11, palette.ink_muted))],
                    spacing=8,
                )
            ]
        elif job.status == "review":
            actions = [
                icon_button(
                    ft.Icons.EDIT_OUTLINED,
                    palette,
                    device,
                    self._editor(job),
                    tooltip="Edit details",
                ),
                button(
                    "Send", palette, device, self._sender(job), icon=ft.Icons.SEND, compact=True
                ),
            ]
        else:
            actions = [
                button(
                    "Retry",
                    palette,
                    device,
                    self._retrier(job),
                    icon=ft.Icons.REFRESH,
                    kind="plain",
                    compact=True,
                )
            ]

        return ft.Column(
            [
                ft.Row(
                    [
                        cover_image(job.cover, palette, 56, 80),
                        ft.Column(info, spacing=5, expand=True),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Row(actions, alignment=ft.MainAxisAlignment.END, spacing=10),
            ],
            spacing=10,
        )

    async def send_all(self) -> None:
        await self._send(self.ctx.store.ready_jobs)

    async def _send(self, jobs: list[Job]) -> None:
        report: SendReport = await self.ctx.store.send(jobs)
        if report.sent:
            await self.ctx.device.success()
        elif report.failed:
            await self.ctx.device.warn()
        self.ctx.notify(report.message, error=bool(report.failed) and not report.sent)

    async def _send_one(self, job: Job) -> None:
        await self._send([job])

    async def add_files(self) -> None:
        picked = await self.ctx.device.pick_books(list(KINDLE_FORMATS))
        if not picked:
            return
        added = 0
        for file in picked:
            job = await self.ctx.store.import_file(file)
            added += job.status == "review"
        if added:
            await self.ctx.device.success()
            noun = "book" if added == 1 else "books"
            self.ctx.notify(f"Added {added} {noun}.")

    async def open_search(self) -> None:
        await self.ctx.go_to(0)

    async def open_settings(self, _: ft.Event) -> None:
        await self.ctx.go_to(3)

    def _editor(self, job: Job):
        async def edit() -> None:
            ReviewSheet(self.ctx, job, self._send_one).open()

        return edit

    def _sender(self, job: Job):
        async def send() -> None:
            await self._send([job])

        return send

    def _retrier(self, job: Job):
        async def retry() -> None:
            await self.ctx.store.retry(job)
            await self.ctx.device.tap()

        return retry

    def _remover(self, job_id: str):
        async def remove(_: ft.Event) -> None:
            job = next((job for job in self.ctx.store.jobs if job.id == job_id), None)
            if job is None:
                return
            await self.ctx.store.remove(job)
            await self.ctx.device.warn()
            self.ctx.notify("Removed from the queue.")

        return remove
