import asyncio
from datetime import datetime
from pathlib import Path

from slugify import slugify

from kindler import library
from kindler.covers import extract_cover
from kindler.db import Job
from kindler.detect import FileTypeDetector
from kindler.metadata import apply_cover, apply_metadata, probe_metadata
from kindler.queue import JobQueue


def title_key(title: str | None) -> str:
    return " ".join((title or "").casefold().split())


def result_key(result: library.SearchResult) -> str:
    return f"{result.source}:{result.ref}"


async def sent_marks(queue: JobQueue, results: list[library.SearchResult]) -> dict[str, datetime]:
    if not results:
        return {}
    by_ref: dict[tuple[str | None, str | None], datetime] = {}
    by_title: dict[str, datetime] = {}
    for job in await queue.sent():
        sent_at = job.sent_at or job.created_at
        if job.ref:
            by_ref.setdefault((job.source, job.ref), sent_at)
        if job.title:
            by_title.setdefault(title_key(job.title), sent_at)

    marks = {}
    for result in results:
        sent_at = by_ref.get((result.source, result.ref)) or by_title.get(title_key(result.title))
        if sent_at:
            marks[result_key(result)] = sent_at
    return marks


def inspect_file(path: Path, fallback_title: str) -> tuple[str, str, str | None]:
    file_type, _ = FileTypeDetector.detect_file_type(path)
    title, author = probe_metadata(path, file_type, fallback_title=fallback_title)
    return title, author, extract_cover(path, file_type)


async def fetch_from_library(
    queue: JobQueue,
    directory: Path,
    source: str,
    ref: str,
    book_format: str,
    kindle_address: str,
) -> Job:
    url, title, catalogue_author = await library.resolve(source, ref, book_format)
    filename = f"{slugify(title)}.{book_format}"
    job = await queue.add(
        filename, status="review", kindle_address=kindle_address, source=source, ref=ref
    )
    path = directory / f"{job.id}_{filename}"

    try:
        if source == "zlib":
            await library.download_zlib(url, path)
        else:
            await library.download(url, path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        await queue.update(job.id, "failed", str(exc))
        return await queue.require(job.id)

    probed_title, author, cover = await asyncio.to_thread(inspect_file, path, title)
    await queue.set(
        job.id,
        tmp_path=str(path),
        title=probed_title,
        author=author or catalogue_author,
        cover=cover,
    )
    return await queue.require(job.id)


async def stamp_metadata(path: Path, file_type: str, job: Job) -> None:
    await asyncio.to_thread(apply_metadata, path, file_type, job.title or "", job.author or "")
    await asyncio.to_thread(apply_cover, path, file_type, job.cover)
