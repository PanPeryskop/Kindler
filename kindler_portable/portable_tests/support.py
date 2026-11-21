from pathlib import Path

import flet as ft

from kindler.db import Job


class FakeDevice:
    mobile = False

    def __init__(self) -> None:
        self.status = ft.ShareResultStatus.SUCCESS
        self.shared: list[list[str]] = []
        self.share_error: Exception | None = None

    async def share_files(self, paths: list[Path], title: str) -> ft.ShareResultStatus:
        if self.share_error:
            raise self.share_error
        self.shared.append([path.name for path in paths])
        return self.status

    async def tap(self) -> None:
        pass

    async def select(self) -> None:
        pass

    async def success(self) -> None:
        pass

    async def warn(self) -> None:
        pass


def book(directory: Path, name: str = "book.txt", text: str = "Call me Ishmael.") -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def stored_file(job: Job) -> Path:
    assert job.tmp_path is not None
    return Path(job.tmp_path)


def error_of(job: Job) -> str:
    assert job.error is not None
    return job.error
