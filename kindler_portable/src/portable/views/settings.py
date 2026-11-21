import flet as ft

from kindler.preferences import preferences
from portable.context import Context
from portable.store import DEFAULT_MIRROR, StoreError, is_email
from portable.theme import BORDER_THIN, GUTTER, SHADOW_SMALL, mono
from portable.widgets import (
    button,
    card,
    field,
    icon_button,
    pressable,
    section_title,
    update_if_mounted,
)

THEMES = {
    "system": ("System", ft.Icons.BRIGHTNESS_AUTO_OUTLINED),
    "light": ("Light", ft.Icons.LIGHT_MODE_OUTLINED),
    "dark": ("Dark", ft.Icons.DARK_MODE_OUTLINED),
}
METHODS = {
    "share": ("Kindle app", "Share sheet, no setup", ft.Icons.IOS_SHARE),
    "email": ("E-mail", "Send to Kindle address", ft.Icons.MAIL_OUTLINE),
}


class SettingsView:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        palette = ctx.palette
        self.kindle = field(
            "Kindle address",
            palette,
            value=preferences.kindle_address,
            hint="you_123@kindle.com",
            keyboard=ft.KeyboardType.EMAIL,
        )
        self.smtp_host = field("SMTP server", palette, value=preferences.smtp_host, expand=True)
        self.smtp_port = field(
            "Port", palette, value=str(preferences.smtp_port), keyboard=ft.KeyboardType.NUMBER
        )
        self.smtp_port.width = 96
        self.smtp_user = field(
            "Your e-mail", palette, value=preferences.smtp_user, keyboard=ft.KeyboardType.EMAIL
        )
        self.smtp_password = field(
            "App password",
            palette,
            hint="Saved, leave blank to keep" if preferences.smtp_password else None,
            password=True,
        )
        self.account_email = field(
            "Z-Library e-mail", palette, keyboard=ft.KeyboardType.EMAIL, hint="reader@example.com"
        )
        self.account_password = field("Password", palette, password=True)
        self.mirror = field(
            "Mirror", palette, value=preferences.zlib_mirror, hint=DEFAULT_MIRROR, expand=True
        )
        self.methods = ft.Row(spacing=10)
        self.email_box = ft.Column(spacing=12)
        self.accounts = ft.Column(spacing=10)
        self.themes = ft.Row(spacing=10)
        self.root = ft.ListView(
            controls=[
                ft.Text("Settings", size=30, weight=ft.FontWeight.W_700, color=palette.ink),
                self._section(
                    "Sending",
                    ft.Icons.SEND,
                    [
                        self.methods,
                        self.kindle,
                        self.email_box,
                        button(
                            "Save sending",
                            palette,
                            ctx.device,
                            self.save_sending,
                            icon=ft.Icons.CHECK,
                            kind="secondary",
                        ),
                    ],
                ),
                self._section(
                    "Z-Library",
                    ft.Icons.LOCAL_LIBRARY_OUTLINED,
                    [
                        ft.Text(
                            "Add one or more accounts and pick the active one. The first search "
                            "opens a quick browser check inside the app.",
                            size=13,
                            color=palette.ink_muted,
                        ),
                        self.accounts,
                        self.account_email,
                        self.account_password,
                        button(
                            "Add account",
                            palette,
                            ctx.device,
                            self.add_account,
                            icon=ft.Icons.PERSON_ADD_ALT,
                            kind="plain",
                        ),
                        ft.Row(
                            [
                                self.mirror,
                                icon_button(
                                    ft.Icons.CHECK,
                                    palette,
                                    ctx.device,
                                    self.save_mirror,
                                    tooltip="Save mirror",
                                    size=50,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                    ],
                ),
                self._section("Appearance", ft.Icons.PALETTE_OUTLINED, [self.themes]),
                ft.Text(
                    "Kindle accepts EPUB, PDF, DOCX, TXT, RTF and HTML. Books are downloaded to "
                    "this phone and shared or e-mailed straight from it.",
                    size=12,
                    color=palette.ink_subtle,
                ),
                ft.Container(height=40),
            ],
            spacing=20,
            padding=ft.Padding.only(left=GUTTER, right=GUTTER - 4, top=12),
            expand=True,
        )
        self.render()
        ctx.spawn(self.load_accounts)

    def _section(self, title: str, icon: ft.IconData, controls: list[ft.Control]) -> ft.Control:
        return card(
            ft.Column([section_title(title, self.ctx.palette, icon), *controls], spacing=14),
            self.ctx.palette,
        )

    def render(self) -> None:
        palette = self.ctx.palette
        self.methods.controls = [
            self._option(
                name,
                detail,
                icon,
                preferences.send_method == key,
                self._method_picker(key),
            )
            for key, (name, detail, icon) in METHODS.items()
        ]
        self.email_box.controls = (
            [
                ft.Row([self.smtp_host, self.smtp_port], spacing=10),
                self.smtp_user,
                self.smtp_password,
                ft.Text(
                    "Use an app password, and add this e-mail to Amazon's Approved Personal "
                    "Document E-mail List.",
                    size=12,
                    color=palette.ink_muted,
                ),
            ]
            if preferences.send_method == "email"
            else []
        )
        self.themes.controls = [
            self._option(name, None, icon, preferences.theme == key, self._theme_picker(key))
            for key, (name, icon) in THEMES.items()
        ]
        update_if_mounted(self.root)

    def _option(
        self, name: str, detail: str | None, icon: ft.IconData, active: bool, on_press
    ) -> ft.Control:
        palette = self.ctx.palette
        fg = palette.on_accent if active else palette.ink
        lines: list[ft.Control] = [
            ft.Icon(icon, size=20, color=fg),
            ft.Text(name, size=14, weight=ft.FontWeight.W_700, color=fg),
        ]
        if detail:
            lines.append(
                ft.Text(detail, size=11, color=palette.on_accent if active else palette.ink_muted)
            )
        return pressable(
            ft.Column(lines, spacing=4),
            palette,
            self.ctx.device,
            on_press,
            bgcolor=palette.accent if active else palette.surface,
            padding=12,
            depth=SHADOW_SMALL,
            border=BORDER_THIN,
            expand=True,
        )

    async def load_accounts(self) -> None:
        palette = self.ctx.palette
        accounts = await self.ctx.store.accounts()
        rows: list[ft.Control] = []
        for account in accounts:
            active = account.email == preferences.zlib_active
            rows.append(
                pressable(
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.RADIO_BUTTON_CHECKED
                                if active
                                else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                color=palette.accent if active else palette.ink_subtle,
                                size=20,
                            ),
                            ft.Text(
                                account.email,
                                size=14,
                                weight=ft.FontWeight.W_600,
                                color=palette.ink,
                                expand=True,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text("ACTIVE", style=mono(10, palette.accent))
                            if active
                            else ft.Container(),
                            ft.IconButton(
                                ft.Icons.DELETE_OUTLINE,
                                icon_color=palette.hot,
                                tooltip="Remove account",
                                on_click=self._remover(account.email),
                            ),
                        ],
                        spacing=10,
                    ),
                    palette,
                    self.ctx.device,
                    self._chooser(account.email),
                    bgcolor=palette.accent_soft if active else palette.surface,
                    padding=ft.Padding.only(left=12, right=4, top=4, bottom=4),
                    depth=SHADOW_SMALL,
                    border=BORDER_THIN,
                )
            )
        if not rows:
            rows.append(ft.Text("No accounts yet.", size=13, color=palette.ink_subtle))
        self.accounts.controls = rows
        update_if_mounted(self.root)

    async def save_sending(self) -> None:
        kindle = (self.kindle.value or "").strip()
        if kindle and not is_email(kindle):
            await self._problem("The Kindle address doesn't look right.")
            return
        changes: dict[str, object] = {"kindle_address": kindle}
        if preferences.send_method == "email":
            try:
                port = int(self.smtp_port.value or "587")
            except ValueError:
                await self._problem("The port must be a number.")
                return
            user = (self.smtp_user.value or "").strip()
            if user and not is_email(user):
                await self._problem("Your e-mail doesn't look right.")
                return
            changes.update(
                smtp_host=(self.smtp_host.value or "").strip() or "smtp.gmail.com",
                smtp_port=port,
                smtp_user=user,
                smtp_password=self.smtp_password.value or preferences.smtp_password,
            )
        await self.ctx.store.save(**changes)
        self.smtp_password.value = ""
        self.smtp_password.hint_text = (
            "Saved, leave blank to keep" if preferences.smtp_password else None
        )
        await self.ctx.device.success()
        self.ctx.notify("Saved.")
        await self.ctx.store.refresh()

    async def add_account(self) -> None:
        try:
            await self.ctx.store.add_account(
                self.account_email.value or "", self.account_password.value or ""
            )
        except StoreError as exc:
            await self._problem(str(exc))
            return
        self.account_email.value = ""
        self.account_password.value = ""
        await self.ctx.device.success()
        await self.load_accounts()

    async def save_mirror(self) -> None:
        mirror = (self.mirror.value or "").strip().rstrip("/")
        if mirror and not mirror.startswith(("https://", "http://")):
            await self._problem("The mirror must start with https://")
            return
        await self.ctx.store.save(zlib_mirror=mirror)
        await self.ctx.device.success()
        self.ctx.notify("Mirror saved.")

    async def _problem(self, message: str) -> None:
        await self.ctx.device.warn()
        self.ctx.notify(message, error=True)

    def _method_picker(self, method: str):
        async def pick() -> None:
            await self.ctx.store.save(send_method=method)
            await self.ctx.device.select()
            self.render()
            await self.ctx.store.refresh()

        return pick

    def _theme_picker(self, theme: str):
        async def pick() -> None:
            await self.ctx.store.save(theme=theme)
            await self.ctx.device.select()
            await self.ctx.restyle()

        return pick

    def _chooser(self, email: str):
        async def choose() -> None:
            await self.ctx.store.choose_account(email)
            await self.ctx.device.select()
            await self.load_accounts()

        return choose

    def _remover(self, email: str):
        async def remove(_: ft.Event) -> None:
            await self.ctx.store.remove_account(email)
            await self.ctx.device.warn()
            await self.load_accounts()

        return remove
