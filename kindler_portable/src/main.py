from pathlib import Path

import flet as ft

from portable.shell import Shell

ASSETS = Path(__file__).parent / "assets"


async def main(page: ft.Page) -> None:
    await Shell(page).start()


if __name__ == "__main__":
    ft.run(main, assets_dir=str(ASSETS))
