import base64
from collections.abc import Awaitable, Callable

import flet as ft

from portable.device import Device
from portable.theme import (
    BODY_FONT,
    BORDER,
    BORDER_THIN,
    EASE,
    FAST,
    MEDIUM,
    SHADOW,
    SHADOW_SMALL,
    Palette,
    hard_shadow,
    mono,
)

Action = Callable[[], Awaitable[None]]

KINDS = ("primary", "secondary", "plain", "danger", "sun")


def colors_for(kind: str, palette: Palette) -> tuple[str, str]:
    return {
        "primary": (palette.accent, palette.on_accent),
        "secondary": (palette.ink, palette.bg),
        "plain": (palette.surface, palette.ink),
        "danger": (palette.hot, palette.on_accent),
        "sun": (palette.sun, "#1A1A1A"),
    }[kind]


def update_if_mounted(control: ft.BaseControl) -> None:
    parent: ft.BaseControl | None = control
    while parent is not None:
        if isinstance(parent, ft.Page):
            control.update()
            return
        parent = parent.parent


def pressable(
    content: ft.Control,
    palette: Palette,
    device: Device,
    on_press: Action | None,
    *,
    bgcolor: str | None = None,
    padding: ft.PaddingValue = 14,
    depth: int = SHADOW,
    border: int = BORDER,
    expand: bool | int | None = None,
    width: float | None = None,
    height: float | None = None,
    disabled: bool = False,
) -> ft.Control:
    rest_margin = ft.Margin.only(right=depth, bottom=depth)
    pressed_margin = ft.Margin.only(left=depth - 1, top=depth - 1, right=1, bottom=1)
    box = ft.Container(
        content=content,
        bgcolor=bgcolor or palette.surface,
        padding=padding,
        width=width,
        height=height,
        border=ft.Border.all(border, palette.line),
        shadow=hard_shadow(palette, depth),
        margin=rest_margin,
        opacity=0.45 if disabled else 1,
        animate=ft.Animation(FAST, EASE),
        animate_opacity=ft.Animation(MEDIUM, EASE),
    )

    def release() -> None:
        box.margin = rest_margin
        box.shadow = hard_shadow(palette, depth)
        box.update()

    async def on_down(_: ft.TapEvent) -> None:
        if disabled or on_press is None:
            return
        box.margin = pressed_margin
        box.shadow = hard_shadow(palette, 1)
        box.update()
        await device.tap()

    async def on_tap(_: ft.Event) -> None:
        if disabled or on_press is None:
            return
        release()
        await on_press()

    return ft.GestureDetector(
        content=box,
        expand=expand,
        mouse_cursor=ft.MouseCursor.BASIC if disabled else ft.MouseCursor.CLICK,
        on_tap_down=on_down,
        on_tap_cancel=lambda _: release(),
        on_tap=on_tap,
    )


def label(value: str, color: str, size: float = 15, weight: ft.FontWeight = ft.FontWeight.W_500):
    return ft.Text(value, size=size, weight=weight, color=color, font_family=BODY_FONT)


def button(
    text: str,
    palette: Palette,
    device: Device,
    on_press: Action | None,
    *,
    icon: ft.IconData | None = None,
    kind: str = "primary",
    expand: bool | int | None = None,
    compact: bool = False,
    busy: bool = False,
    disabled: bool = False,
) -> ft.Control:
    bg, fg = colors_for(kind, palette)
    leading: list[ft.Control] = []
    if busy:
        leading.append(ft.ProgressRing(width=16, height=16, stroke_width=2.5, color=fg))
    elif icon:
        leading.append(ft.Icon(icon, size=18, color=fg))
    row = ft.Row(
        [
            *leading,
            ft.Text(
                text.upper(),
                size=12 if compact else 13,
                weight=ft.FontWeight.W_700,
                color=fg,
                font_family=BODY_FONT,
                style=ft.TextStyle(letter_spacing=1.2),
            ),
        ],
        spacing=8,
        tight=True,
        alignment=ft.MainAxisAlignment.CENTER,
    )
    vertical = 10 if compact else 15
    return pressable(
        row,
        palette,
        device,
        None if busy else on_press,
        bgcolor=bg,
        padding=ft.Padding.symmetric(horizontal=14 if compact else 18, vertical=vertical),
        depth=SHADOW_SMALL if compact else SHADOW,
        expand=expand,
        disabled=disabled,
    )


def icon_button(
    icon: ft.IconData,
    palette: Palette,
    device: Device,
    on_press: Action | None,
    *,
    tooltip: str,
    kind: str = "plain",
    size: int = 40,
) -> ft.Control:
    bg, fg = colors_for(kind, palette)
    control = pressable(
        ft.Icon(icon, size=18, color=fg),
        palette,
        device,
        on_press,
        bgcolor=bg,
        padding=0,
        depth=SHADOW_SMALL,
        border=BORDER_THIN,
        width=size,
        height=size,
    )
    control.tooltip = tooltip
    return control


def card(
    content: ft.Control,
    palette: Palette,
    *,
    padding: ft.PaddingValue = 16,
    bgcolor: str | None = None,
    depth: int = SHADOW,
    border: int = BORDER,
) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=bgcolor or palette.surface,
        padding=padding,
        border=ft.Border.all(border, palette.line),
        shadow=hard_shadow(palette, depth),
        margin=ft.Margin.only(right=depth, bottom=depth),
        animate=ft.Animation(MEDIUM, EASE),
    )


def tag(text: str, fg: str, bg: str, palette: Palette) -> ft.Container:
    return ft.Container(
        content=ft.Text(text.upper(), style=mono(10, fg)),
        bgcolor=bg,
        padding=ft.Padding.symmetric(horizontal=7, vertical=3),
        border=ft.Border.all(BORDER_THIN, palette.line),
    )


def section_title(text: str, palette: Palette, icon: ft.IconData | None = None) -> ft.Row:
    parts: list[ft.Control] = [ft.Text(text.upper(), style=mono(11, palette.ink_muted))]
    if icon:
        parts.insert(0, ft.Icon(icon, size=14, color=palette.ink_muted))
    return ft.Row(parts, spacing=6)


def field(
    title: str,
    palette: Palette,
    *,
    value: str = "",
    hint: str | None = None,
    password: bool = False,
    keyboard: ft.KeyboardType = ft.KeyboardType.TEXT,
    on_submit: Callable[[ft.Event], Awaitable[None]] | None = None,
    autofocus: bool = False,
    expand: bool | int | None = None,
) -> ft.TextField:
    return ft.TextField(
        label=title,
        value=value,
        hint_text=hint,
        password=password,
        can_reveal_password=password,
        keyboard_type=keyboard,
        autofocus=autofocus,
        expand=expand,
        on_submit=on_submit,
        filled=True,
        fill_color=palette.surface,
        border_radius=0,
        border_width=BORDER_THIN,
        border_color=palette.line,
        focused_border_color=palette.accent,
        focused_border_width=BORDER,
        cursor_color=palette.accent,
        text_style=ft.TextStyle(size=15, color=palette.ink, font_family=BODY_FONT),
        label_style=mono(11, palette.ink_muted, ft.FontWeight.W_600),
        hint_style=ft.TextStyle(color=palette.ink_subtle, font_family=BODY_FONT),
        content_padding=ft.Padding.symmetric(horizontal=14, vertical=14),
    )


def select(
    options: dict[str, str],
    value: str,
    palette: Palette,
    on_select: Callable[[ft.Event], Awaitable[None]],
    *,
    title: str | None = None,
    expand: bool | int | None = None,
) -> ft.Dropdown:
    return ft.Dropdown(
        options=[ft.DropdownOption(key=key, text=text) for key, text in options.items()],
        value=value,
        label=title,
        on_select=on_select,
        expand=expand,
        filled=True,
        fill_color=palette.surface,
        bgcolor=palette.surface,
        border_radius=0,
        border_width=BORDER_THIN,
        border_color=palette.line,
        color=palette.ink,
        text_style=ft.TextStyle(size=14, color=palette.ink, font_family=BODY_FONT),
        label_style=mono(11, palette.ink_muted, ft.FontWeight.W_600),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=8),
    )


def cover_source(cover: str | None) -> str | bytes | None:
    if not cover:
        return None
    if cover.startswith("data:"):
        return base64.b64decode(cover.split(",", 1)[1])
    return cover


def cover_image(cover: str | None, palette: Palette, width: float, height: float) -> ft.Control:
    placeholder = ft.Container(
        content=ft.Icon(ft.Icons.MENU_BOOK, size=width * 0.4, color=palette.ink_subtle),
        bgcolor=palette.surface_alt,
        alignment=ft.Alignment.CENTER,
        width=width,
        height=height,
    )
    source = cover_source(cover)
    image: ft.Control = placeholder
    if source is not None:
        image = ft.Image(
            src=source,
            width=width,
            height=height,
            fit=ft.BoxFit.COVER,
            gapless_playback=True,
            error_content=placeholder,
        )
    return ft.Container(
        content=image,
        width=width,
        height=height,
        border=ft.Border.all(BORDER_THIN, palette.line),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )


def spinner(palette: Palette, size: float = 22) -> ft.ProgressRing:
    return ft.ProgressRing(width=size, height=size, stroke_width=3, color=palette.accent)


def empty_state(
    icon: ft.IconData, title: str, body: str, palette: Palette, action: ft.Control | None = None
) -> ft.Container:
    parts: list[ft.Control] = [
        ft.Container(
            content=ft.Icon(icon, size=34, color="#1A1A1A"),
            bgcolor=palette.sun,
            width=72,
            height=72,
            alignment=ft.Alignment.CENTER,
            border=ft.Border.all(BORDER, palette.line),
            shadow=hard_shadow(palette, SHADOW_SMALL),
            rotate=ft.Rotate(-0.06),
        ),
        ft.Text(
            title,
            size=20,
            weight=ft.FontWeight.W_700,
            color=palette.ink,
            text_align=ft.TextAlign.CENTER,
        ),
        ft.Text(body, size=14, color=palette.ink_muted, text_align=ft.TextAlign.CENTER),
    ]
    if action:
        parts.append(ft.Container(content=action, padding=ft.Padding.only(top=8)))
    return ft.Container(
        content=ft.Column(
            parts, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12, tight=True
        ),
        padding=ft.Padding.symmetric(horizontal=28, vertical=40),
        alignment=ft.Alignment.CENTER,
    )


def toast(page: ft.Page, message: str, palette: Palette, *, error: bool = False) -> None:
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(
                message,
                color=palette.on_accent if error else palette.bg,
                weight=ft.FontWeight.W_600,
                font_family=BODY_FONT,
            ),
            bgcolor=palette.hot if error else palette.ink,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=0),
            margin=ft.Margin.only(left=16, right=16, bottom=12),
            duration=ft.Duration(milliseconds=3200),
        )
    )
