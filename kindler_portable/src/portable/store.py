import asyncio
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import flet as ft
from slugify import slugify

from kindler import library, z_lib
from kindler.accounts import activate, get_account, list_accounts, remove_account, save_account
from kindler.covers import data_uri, image_media_type
from kindler.db import Job, ZLibAccount, init_db, utcnow
from kindler.detect import FileTypeDetector
from kindler.lookup import lookup, merge
from kindler.preferences import load_preferences, preferences, save_preferences
from kindler.queue import ACTIVE_STATUSES, JobQueue
from kindler.sender import send_to_kindle
from kindler.settings import settings
from kindler.workflow import (
    fetch_from_library,
    inspect_file,
    result_key,
    sent_marks,
    stamp_metadata,
)

KINDLE_FORMATS = ("epub", "pdf", "docx", "doc", "rtf", "txt", "html", "htm")
SEARCH_FORMATS = {"": "Any format", "epub": "EPUB", "pdf": "PDF"}
DEFAULT_MIRROR = "https://z-lib.sk"
SHARE_LIMIT_MB = 50
EMAIL_LIMIT_MB = 25
MAX_COVER_BYTES = 2 * 1024 * 1024
HISTORY_LIMIT = 100
SENT_FILES_TTL = timedelta(days=1)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
logger = logging.getLogger(__name__)


class StoreError(Exception):
    pass


class ShareTarget(Protocol):
    async def share_files(self, paths: list[Path], title: str) -> ft.ShareResultStatus: ...


@dataclass
class SearchState:
    query: str = ""
    source: str = ""
    language: str = ""
    book_format: str = ""
    results: list[library.SearchResult] = field(default_factory=list)
    marks: dict[str, datetime] = field(default_factory=dict)
    next_cursor: tuple[int, int] | None = None
    error: str | None = None
    searched: bool = False
    loading: bool = False


@dataclass
class SendReport:
    sent: int = 0
    failed: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def message(self) -> str:
        if self.cancelled:
            return "Sharing cancelled."
        if self.failed and not self.sent:
            return self.failed[0]
        noun = "book" if self.sent == 1 else "books"
        sent = f"Sent {self.sent} {noun} to Kindle."
        return f"{sent} {len(self.failed)} failed." if self.failed else sent


def kindle_formats(result: library.SearchResult) -> library.SearchResult:
    formats = {name: url for name, url in result.formats.items() if name in KINDLE_FORMATS}
    return replace(result, formats=formats)


def preferred_format(result: library.SearchResult) -> str:
    return "epub" if "epub" in result.formats else next(iter(result.formats), "")


def is_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.match(value))


class Store:
    def __init__(self, device: ShareTarget, data_dir: Path) -> None:
        self.device = device
        self.data_dir = data_dir
        self.books_dir = data_dir / "books"
        self.outbox_dir = data_dir / "outbox"
        self.queue = JobQueue()
        self.jobs: list[Job] = []
        self.search = SearchState()
        self.fetching: set[str] = set()
        self.sending: set[str] = set()
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def reset_listeners(self) -> None:
        self._listeners.clear()

    def changed(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def start(self) -> None:
        settings.update(
            db_path=str(self.data_dir / "kindler.db"),
            zlib_state_file=str(self.data_dir / "zlib_state.json"),
            zlib_cookies_file=str(self.data_dir / "cookies.txt"),
            zlib_tor_proxy=None,
            zlib_email=None,
            zlib_password=None,
            test_email=None,
        )
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        await init_db()
        await load_preferences()
        self.apply_preferences()
        await self._recover()
        await self.refresh()

    def apply_preferences(self) -> None:
        by_email = preferences.send_method == "email"
        settings.update(
            email_host=preferences.smtp_host,
            email_port=preferences.smtp_port,
            email_host_user=preferences.smtp_user,
            email_host_password=preferences.smtp_password,
            zlib_mirror=preferences.zlib_mirror or DEFAULT_MIRROR,
            max_attachment_mb=EMAIL_LIMIT_MB if by_email else SHARE_LIMIT_MB,
        )

    async def _recover(self) -> None:
        for job in await self.queue.with_status(*ACTIVE_STATUSES):
            if job.tmp_path and Path(job.tmp_path).exists():
                await self.queue.update(job.id, "review", "Sending was interrupted. Try again.")
            else:
                await self.queue.update(job.id, "failed", "Interrupted before it was sent.")
        for job in await self.queue.older_than("sent", SENT_FILES_TTL):
            if job.tmp_path:
                Path(job.tmp_path).unlink(missing_ok=True)
                await self.queue.set(job.id, tmp_path=None)

    async def refresh(self) -> None:
        self.jobs = await self.queue.unsent()
        self.changed()

    @property
    def ready_jobs(self) -> list[Job]:
        return [job for job in self.jobs if job.status == "review" and job.id not in self.sending]

    async def run_search(self, append: bool = False) -> None:
        state = self.search
        state.loading = True
        state.error = None
        self.changed()
        try:
            page = await library.search(
                state.query,
                language=state.language,
                source=state.source,
                book_format=state.book_format,
                cursor=state.next_cursor if append else None,
            )
        except Exception as exc:
            logger.exception("Search failed (source=%r)", state.source)
            detail = str(exc) if state.source == "zlib" else ""
            state.error = detail or "Search is unavailable right now. Try again in a moment."
            state.loading = False
            self.changed()
            return

        results = [kindle_formats(result) for result in page.results]
        results = [result for result in results if result.formats]
        state.results = [*state.results, *results] if append else results
        if not append:
            state.marks = {}
        state.marks.update(await sent_marks(self.queue, results))
        state.next_cursor = page.next_cursor
        state.searched = True
        state.loading = False
        self.changed()

    async def fetch(self, result: library.SearchResult, book_format: str) -> Job:
        key = result_key(result)
        self.fetching.add(key)
        self.changed()
        try:
            return await fetch_from_library(
                self.queue,
                self.books_dir,
                result.source,
                result.ref,
                book_format,
                preferences.kindle_address,
            )
        finally:
            self.fetching.discard(key)
            await self.refresh()

    async def import_file(self, picked: ft.FilePickerFile) -> Job:
        try:
            return await self._import(picked)
        finally:
            await self.refresh()

    async def _import(self, picked: ft.FilePickerFile) -> Job:
        name = Path(picked.name).name
        job = await self.queue.add(
            name, status="review", kindle_address=preferences.kindle_address, source="upload"
        )
        path = self.books_dir / f"{job.id}_{name}"
        if picked.path:
            await asyncio.to_thread(shutil.copyfile, picked.path, path)
        elif picked.bytes is not None:
            await asyncio.to_thread(path.write_bytes, picked.bytes)
        else:
            await self.queue.update(job.id, "failed", "The file could not be read.")
            return await self.queue.require(job.id)

        if path.stat().st_size > settings.max_attachment_bytes:
            path.unlink(missing_ok=True)
            await self.queue.update(
                job.id, "failed", f"File exceeds the {settings.max_attachment_mb} MB limit."
            )
            return await self.queue.require(job.id)

        title, author, cover = await asyncio.to_thread(inspect_file, path, Path(name).stem)
        await self.queue.set(job.id, tmp_path=str(path), title=title, author=author, cover=cover)
        return await self.queue.require(job.id)

    async def save_details(self, job_id: str, title: str, author: str) -> None:
        await self.queue.set(job_id, title=title.strip(), author=author.strip(), error=None)
        await self.refresh()

    async def find_metadata(self, job_id: str, title: str, author: str) -> bool:
        job = await self.queue.require(job_id)
        try:
            found = await lookup(title, author)
        except Exception:
            logger.exception("Metadata lookup failed for job %s", job_id)
            found = None
        found = merge(title, author, found) if found else None
        if found is None:
            await self.queue.set(job_id, title=title, author=author)
            await self.refresh()
            return False
        await self.queue.set(
            job_id,
            title=found.title,
            author=found.author,
            cover=found.cover or job.cover,
            error=None,
        )
        await self.refresh()
        return True

    async def set_cover(self, job_id: str, data: bytes) -> None:
        if len(data) > MAX_COVER_BYTES:
            raise StoreError("The cover must be smaller than 2 MB.")
        media_type = image_media_type(data)
        if media_type is None:
            raise StoreError("The cover must be a JPEG, PNG or GIF image.")
        await self.queue.set(job_id, cover=data_uri(data, media_type))
        await self.refresh()

    async def retry(self, job: Job) -> None:
        if job.tmp_path and Path(job.tmp_path).exists():
            await self.queue.update(job.id, "review")
        else:
            await self.queue.update(job.id, "failed", "The file is gone. Add the book again.")
        await self.refresh()

    async def remove(self, job: Job) -> None:
        if job.tmp_path:
            Path(job.tmp_path).unlink(missing_ok=True)
        await self.queue.remove(job.id)
        await self.refresh()

    async def send(self, jobs: list[Job]) -> SendReport:
        report = SendReport()
        if preferences.send_method == "email":
            problem = self.email_problem()
            if problem:
                report.failed.append(problem)
                return report

        self.sending.update(job.id for job in jobs)
        self.changed()
        try:
            ready = []
            for job in jobs:
                prepared = await self._prepare(job, report)
                if prepared:
                    ready.append(prepared)
            if ready and preferences.send_method == "email":
                await self._email(ready, report)
            elif ready:
                await self._share(ready, report)
        finally:
            self.sending.difference_update(job.id for job in jobs)
            await self.refresh()
        return report

    def email_problem(self) -> str | None:
        if not is_email(preferences.kindle_address):
            return "Set your Kindle address in Settings first."
        if not preferences.smtp_user or not preferences.smtp_password:
            return "Add your e-mail account in Settings to send by e-mail."
        return None

    async def _prepare(self, job: Job, report: SendReport) -> tuple[Job, Path, str] | None:
        path = Path(job.tmp_path or "")
        if not job.tmp_path or not path.exists():
            await self._fail(job, "The file is no longer on this device.", report)
            return None
        file_type, _ = await asyncio.to_thread(FileTypeDetector.detect_file_type, path)
        if file_type not in KINDLE_FORMATS:
            await self._fail(job, f"Kindle can't open {file_type.upper()} files.", report)
            return None
        try:
            await stamp_metadata(path, file_type, job)
        except Exception as exc:
            logger.exception("Could not stamp metadata on job %s", job.id)
            await self._fail(job, f"Could not update the book details: {exc}", report)
            return None
        return job, path, file_type

    async def _fail(self, job: Job, error: str, report: SendReport) -> None:
        await self.queue.update(job.id, "failed", error)
        report.failed.append(error)

    async def _email(self, ready: list[tuple[Job, Path, str]], report: SendReport) -> None:
        for job, path, file_type in ready:
            await self.queue.update(job.id, "sending")
            try:
                await send_to_kindle(
                    path,
                    preferences.kindle_address,
                    convert=file_type == "pdf",
                    title=job.title,
                    author=job.author,
                )
            except Exception as exc:
                logger.exception("E-mail delivery failed for job %s", job.id)
                await self._fail(job, str(exc), report)
                continue
            await self._mark_sent(job)
            report.sent += 1

    async def _share(self, ready: list[tuple[Job, Path, str]], report: SendReport) -> None:
        await asyncio.to_thread(shutil.rmtree, self.outbox_dir, True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        copies = []
        for job, path, file_type in ready:
            name = slugify(job.title or Path(job.filename).stem) or job.id
            copy = self.outbox_dir / f"{name}.{file_type}"
            await asyncio.to_thread(shutil.copyfile, path, copy)
            copies.append(copy)

        try:
            status = await self.device.share_files(copies, "Send to Kindle")
        except Exception:
            logger.exception("Sharing failed")
            report.failed.append("Sharing isn't available here. Switch to e-mail in Settings.")
            return
        if status == ft.ShareResultStatus.DISMISSED:
            report.cancelled = True
            return
        for job, _, _ in ready:
            await self._mark_sent(job)
            report.sent += 1

    async def _mark_sent(self, job: Job) -> None:
        await self.queue.set(job.id, status="sent", error=None, sent_at=utcnow())

    async def history(self) -> list[Job]:
        return await self.queue.sent(HISTORY_LIMIT)

    async def forget(self, job: Job) -> None:
        if job.tmp_path:
            Path(job.tmp_path).unlink(missing_ok=True)
        await self.queue.remove(job.id)

    async def accounts(self) -> list[ZLibAccount]:
        return await list_accounts()

    async def add_account(self, email: str, password: str) -> None:
        email = email.strip()
        if not is_email(email):
            raise StoreError("That e-mail address doesn't look right.")
        if not password:
            raise StoreError("Enter the Z-Library password.")
        await save_account(email, password)
        if email == preferences.zlib_active:
            z_lib.reset()
        elif not preferences.zlib_active:
            await self.choose_account(email)

    async def choose_account(self, email: str) -> None:
        if await get_account(email) and await activate(email):
            z_lib.reset()

    async def remove_account(self, email: str) -> None:
        await remove_account(email)
        if email == preferences.zlib_active:
            remaining = await list_accounts()
            await activate(remaining[0].email if remaining else "")
            z_lib.reset()

    async def save(self, **changes: object) -> None:
        mirror_changed = (
            changes.get("zlib_mirror", preferences.zlib_mirror) != preferences.zlib_mirror
        )
        await save_preferences(**changes)
        self.apply_preferences()
        if mirror_changed:
            z_lib.reset()
