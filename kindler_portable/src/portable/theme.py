from dataclasses import dataclass

import flet as ft

BODY_FONT = "Space Grotesk"
MONO_FONT = "JetBrains Mono"
FONTS = {BODY_FONT: "fonts/SpaceGrotesk.ttf", MONO_FONT: "fonts/JetBrainsMono.ttf"}

EASE = ft.AnimationCurve.EASE_OUT_QUINT
FAST = 120
MEDIUM = 220
SLOW = 380

BORDER = 3
BORDER_THIN = 2
SHADOW = 5
SHADOW_SMALL = 3
GAP = 12
GUTTER = 16


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    bg_alt: str
    surface: str
    surface_alt: str
    ink: str
    ink_muted: str
    ink_subtle: str
    line: str
    shadow: str
    accent: str
    accent_pressed: str
    accent_soft: str
    on_accent: str
    hot: str
    hot_soft: str
    sun: str
    lime: str
    success_bg: str
    success_ink: str
    failed_bg: str


LIGHT = Palette(
    dark=False,
    bg="#FFFDF5",
    bg_alt="#F0ECE2",
    surface="#FFFFFF",
    surface_alt="#FFF8E7",
    ink="#1A1A1A",
    ink_muted="#5E5E5E",
    ink_subtle="#9B9B9B",
    line="#1A1A1A",
    shadow="#1A1A1A",
    accent="#3D5AFE",
    accent_pressed="#2941D4",
    accent_soft="#E8ECFF",
    on_accent="#FFFFFF",
    hot="#FF2D55",
    hot_soft="#FFE5EA",
    sun="#FFD600",
    lime="#C6F221",
    success_bg="#DDFFF0",
    success_ink="#0A7B3E",
    failed_bg="#FFE5EA",
)

DARK = Palette(
    dark=True,
    bg="#121212",
    bg_alt="#1C1C1C",
    surface="#1A1A1A",
    surface_alt="#242424",
    ink="#EDEDED",
    ink_muted="#B8B8B8",
    ink_subtle="#7A7A7A",
    line="#EDEDED",
    shadow="#000000",
    accent="#6B80FF",
    accent_pressed="#4E66F0",
    accent_soft="#1A2352",
    on_accent="#FFFFFF",
    hot="#FF4D6D",
    hot_soft="#2A0810",
    sun="#FFDF33",
    lime="#D4FF33",
    success_bg="#0A2E1F",
    success_ink="#33FFB8",
    failed_bg="#2A0810",
)


def palette_for(theme: str, brightness: ft.Brightness | None) -> Palette:
    if theme == "dark":
        return DARK
    if theme == "light":
        return LIGHT
    return DARK if brightness == ft.Brightness.DARK else LIGHT


def material_theme(palette: Palette) -> ft.Theme:
    transitions = ft.PageTransitionsTheme(
        android=ft.PageTransitionTheme.PREDICTIVE,
        ios=ft.PageTransitionTheme.CUPERTINO,
        windows=ft.PageTransitionTheme.FADE_FORWARDS,
        linux=ft.PageTransitionTheme.FADE_FORWARDS,
        macos=ft.PageTransitionTheme.CUPERTINO,
    )
    return ft.Theme(
        font_family=BODY_FONT,
        use_material3=True,
        scaffold_bgcolor=palette.bg,
        canvas_color=palette.bg,
        splash_color=ft.Colors.TRANSPARENT,
        highlight_color=ft.Colors.TRANSPARENT,
        page_transitions=transitions,
        color_scheme=ft.ColorScheme(
            primary=palette.accent,
            on_primary=palette.on_accent,
            secondary=palette.ink,
            on_secondary=palette.bg,
            error=palette.hot,
            on_error=palette.on_accent,
            surface=palette.surface,
            on_surface=palette.ink,
            on_surface_variant=palette.ink_muted,
            outline=palette.line,
            outline_variant=palette.ink_subtle,
            shadow=palette.shadow,
            surface_tint=ft.Colors.TRANSPARENT,
        ),
    )


def hard_shadow(palette: Palette, depth: int = SHADOW) -> ft.BoxShadow:
    return ft.BoxShadow(
        spread_radius=0, blur_radius=0, color=palette.shadow, offset=ft.Offset(depth, depth)
    )


def mono(size: float, color: str, weight: ft.FontWeight = ft.FontWeight.W_700) -> ft.TextStyle:
    return ft.TextStyle(
        font_family=MONO_FONT, size=size, weight=weight, color=color, letter_spacing=0.8
    )
