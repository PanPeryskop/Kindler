import logging
import os
from pathlib import Path

import flet as ft

MOBILE_PLATFORMS = (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
logger = logging.getLogger(__name__)


class Device:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.mobile = not page.web and page.platform in MOBILE_PLATFORMS
        self._haptics = ft.HapticFeedback()
        self._share = ft.Share()
        self._picker = ft.FilePicker()
        self._paths = ft.StoragePaths()

    async def lock_portrait(self) -> None:
        if self.mobile:
            await self.page.set_allowed_device_orientations([ft.DeviceOrientation.PORTRAIT_UP])

    async def data_directory(self) -> Path:
        override = os.getenv("KINDLER_DATA_DIR")
        if override:
            directory = Path(override)
        elif self.page.web:
            directory = Path.home() / ".kindler-portable"
        else:
            directory = Path(await self._paths.get_application_support_directory())
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    async def tap(self) -> None:
        await self._feel("light_impact")

    async def select(self) -> None:
        await self._feel("selection_click")

    async def success(self) -> None:
        await self._feel("medium_impact")

    async def warn(self) -> None:
        await self._feel("heavy_impact")

    async def _feel(self, kind: str) -> None:
        if not self.mobile:
            return
        try:
            await getattr(self._haptics, kind)()
        except Exception:
            logger.debug("Haptic feedback unavailable", exc_info=True)

    async def share_files(self, paths: list[Path], title: str) -> ft.ShareResultStatus:
        files = [ft.ShareFile.from_path(str(path), name=path.name) for path in paths]
        result = await self._share.share_files(files, title=title, subject=title)
        return result.status

    async def pick_books(self, extensions: list[str]) -> list[ft.FilePickerFile]:
        return await self._picker.pick_files(
            dialog_title="Add books",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=extensions,
            allow_multiple=True,
            with_data=self.page.web,
        )

    async def pick_image(self) -> ft.FilePickerFile | None:
        files = await self._picker.pick_files(
            dialog_title="Choose a cover",
            file_type=ft.FilePickerFileType.IMAGE,
            with_data=True,
        )
        return files[0] if files else None
