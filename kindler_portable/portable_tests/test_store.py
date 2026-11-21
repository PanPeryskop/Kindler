from pathlib import Path

import flet as ft

from kindler import library
from kindler.preferences import preferences
from kindler.settings import settings
from portable.store import Store, kindle_formats, preferred_format
from portable.zlib_check import parse_cookies
from portable_tests.support import FakeDevice, book, error_of, stored_file


def picked(path: Path) -> ft.FilePickerFile:
    return ft.FilePickerFile(id=1, name=path.name, size=path.stat().st_size, path=str(path))


async def ready_job(store: Store, tmp_path: Path, name: str = "moby.txt"):
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    job = await store.import_file(picked(book(inbox, name)))
    await store.save_details(job.id, "Moby Dick", "Herman Melville")
    return await store.queue.require(job.id)


def result(formats: dict[str, str]) -> library.SearchResult:
    return library.SearchResult(
        source="gutenberg", ref="1", title="T", author="A", cover_url=None, formats=formats
    )


def test_only_kindle_formats_are_offered():
    filtered = kindle_formats(result({"mobi": "m", "epub": "e", "fb2": "f", "pdf": "p"}))

    assert list(filtered.formats) == ["epub", "pdf"]
    assert preferred_format(filtered) == "epub"
    assert preferred_format(result({"txt": "t", "html": "h"})) == "txt"


def test_cookie_header_is_parsed():
    assert parse_cookies("__diamwall=ok; bsrv=1; broken; c_token=a=b") == {
        "__diamwall": "ok",
        "bsrv": "1",
        "c_token": "a=b",
    }


async def test_imported_file_waits_for_review(store: Store, tmp_path: Path):
    source = book(tmp_path, "notes.txt")

    job = await store.import_file(picked(source))

    assert job.status == "review"
    assert job.source == "upload"
    assert stored_file(job).parent == store.books_dir
    assert [queued.id for queued in store.jobs] == [job.id]


async def test_oversized_import_fails(store: Store, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_mb", 0)
    source = book(tmp_path, "big.txt")

    job = await store.import_file(picked(source))

    assert job.status == "failed"
    assert "limit" in error_of(job)


async def test_sharing_marks_books_sent_with_clean_names(
    store: Store, device: FakeDevice, tmp_path
):
    job = await ready_job(store, tmp_path)

    report = await store.send([job])

    assert report.sent == 1
    assert report.message == "Sent 1 book to Kindle."
    assert device.shared == [["moby-dick.txt"]]
    assert (await store.queue.require(job.id)).status == "sent"
    assert [sent.id for sent in await store.history()] == [job.id]
    assert store.jobs == []


async def test_dismissed_share_keeps_the_book_ready(store: Store, device: FakeDevice, tmp_path):
    device.status = ft.ShareResultStatus.DISMISSED
    job = await ready_job(store, tmp_path)

    report = await store.send([job])

    assert report.cancelled
    assert (await store.queue.require(job.id)).status == "review"


async def test_unavailable_share_keeps_the_book_ready(store: Store, device: FakeDevice, tmp_path):
    device.share_error = RuntimeError("no share sheet")
    job = await ready_job(store, tmp_path)

    report = await store.send([job])

    assert report.sent == 0
    assert "Sharing isn't available" in report.message
    assert (await store.queue.require(job.id)).status == "review"


async def test_email_needs_an_account(store: Store, tmp_path: Path):
    await store.save(send_method="email", kindle_address="me@kindle.com")
    job = await ready_job(store, tmp_path)

    report = await store.send([job])

    assert "Add your e-mail account" in report.message
    assert (await store.queue.require(job.id)).status == "review"


async def test_email_settings_reach_the_sender(store: Store, tmp_path: Path, monkeypatch):
    sent_to = []

    async def fake_send(path, kindle_address, **kwargs):
        sent_to.append((kindle_address, settings.email_host_user, settings.max_attachment_mb))

    monkeypatch.setattr("portable.store.send_to_kindle", fake_send)
    await store.save(
        send_method="email",
        kindle_address="me@kindle.com",
        smtp_user="me@example.com",
        smtp_password="secret",
    )
    job = await ready_job(store, tmp_path)

    report = await store.send([job])

    assert report.sent == 1
    assert sent_to == [("me@kindle.com", "me@example.com", 25)]


async def test_missing_file_fails_and_retry_explains(store: Store, tmp_path: Path):
    job = await ready_job(store, tmp_path)
    stored_file(job).unlink()

    report = await store.send([job])
    await store.retry(await store.queue.require(job.id))

    assert report.failed
    failed = await store.queue.require(job.id)
    assert failed.status == "failed"
    assert "Add the book again" in error_of(failed)


async def test_interrupted_sending_returns_to_review(store: Store, tmp_path: Path):
    job = await ready_job(store, tmp_path)
    await store.queue.update(job.id, "sending")

    await store.start()

    recovered = await store.queue.require(job.id)
    assert recovered.status == "review"
    assert "interrupted" in error_of(recovered)


async def test_mirror_preference_reaches_zlib(store: Store):
    await store.save(zlib_mirror="https://example.org")
    assert settings.zlib_mirror == "https://example.org"

    await store.save(zlib_mirror="")
    assert settings.zlib_mirror == "https://z-lib.sk"
    assert preferences.zlib_mirror == ""


def make_pdf(path: Path, title: str) -> Path:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": title, "/Author": "Somebody"})
    with path.open("wb") as output:
        writer.write(output)
    return path


async def test_pdf_details_work_without_pymupdf(store: Store, device: FakeDevice, tmp_path):
    from kindler.metadata import read_raw_metadata

    job = await store.import_file(picked(make_pdf(tmp_path / "scan.pdf", "Old title")))
    assert (job.title, job.author, job.cover) == ("Old title", "Somebody", None)

    await store.save_details(job.id, "New title", "Author")
    report = await store.send([await store.queue.require(job.id)])

    assert report.sent == 1
    assert read_raw_metadata(stored_file(job), "pdf") == ("New title", "Author")
    assert device.shared == [["new-title.pdf"]]
