import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, job_queue
from tests.test_metadata import make_epub

pytestmark = pytest.mark.usefixtures("clean_db")


def epub_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "src.epub"
    make_epub(path, "Zebrane Opowiadania", "Jan Kowalski")
    return path.read_bytes()


async def post_upload(client: AsyncClient, name: str, payload: bytes):
    return await client.post(
        "/upload",
        files={"files": (name, io.BytesIO(payload), "application/epub+zip")},
        data={"kindle_address": "k@kindle.com"},
    )


async def test_upload_renders_review_card_with_metadata(tmp_path: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await post_upload(client, "book.epub", epub_bytes(tmp_path))

    assert response.status_code == 200
    assert "Review" in response.text
    assert "Zebrane Opowiadania" in response.text
    assert "Jan Kowalski" in response.text


async def test_upload_persists_job_and_temp_file(tmp_path: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await post_upload(client, "book.epub", epub_bytes(tmp_path))

    jobs = await job_queue.with_status("review")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.kindle_address == "k@kindle.com"
    assert Path(job.tmp_path).exists()
    Path(job.tmp_path).unlink(missing_ok=True)


async def test_oversized_upload_is_rejected(tmp_path: Path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_attachment_mb", 1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await post_upload(client, "big.epub", b"x" * (2 * 1024 * 1024))

    assert "Failed" in response.text
    jobs = await job_queue.with_status("failed")
    assert "exceeds" in jobs[0].error


async def test_filename_with_traversal_stays_in_temp_dir(tmp_path: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await post_upload(client, "../../evil.epub", epub_bytes(tmp_path))

    job = (await job_queue.with_status("review"))[0]
    stored = Path(job.tmp_path)
    assert stored.name.endswith("evil.epub")
    assert ".." not in str(stored)
    stored.unlink(missing_ok=True)


async def test_search_endpoint_renders_results(monkeypatch):
    from app.services import library

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        result = library.SearchResult(
            source="wolnelektury",
            ref="quo-vadis",
            title="Quo vadis",
            author="Henryk Sienkiewicz",
            cover_url="https://wolnelektury.pl/cover.jpg",
            formats={"epub": "https://wolnelektury.pl/x.epub", "mobi": "https://x/y.mobi"},
            language="pl",
        )
        return library.SearchPage([result])

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/search", params={"q": "quo vadis"})

    assert "Quo vadis" in response.text
    assert "Wolne Lektury" in response.text
    assert 'value="epub"' in response.text
    assert 'value="mobi"' in response.text


async def test_search_endpoint_passes_language_and_rejects_bogus_codes(monkeypatch):
    from app.services import library

    seen: list[str] = []

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        seen.append(language)
        return library.SearchPage([])

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/search", params={"q": "x", "lang": "pl"})
        await client.get("/search", params={"q": "x", "lang": "klingon"})

    assert seen == ["pl", ""]


async def test_search_endpoint_passes_source_and_rejects_bogus_ones(monkeypatch):
    from app.services import library

    seen: list[str] = []

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        seen.append(source)
        return library.SearchPage([])

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/search", params={"q": "x", "source": "gutenberg"})
        await client.get("/search", params={"q": "x", "source": "libgen"})

    assert seen == ["gutenberg", ""]


async def test_index_renders_a_switch_option_per_source():
    from app.services import library

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    for code in library.SOURCES:
        assert f'name="source" value="{code}"' in response.text


async def test_search_endpoint_reports_empty_query(monkeypatch):
    from app.services import library

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        return library.SearchPage([])

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/search", params={"q": "zzzz"})

    assert "Nothing found" in response.text


async def test_library_download_creates_review_job(tmp_path: Path, monkeypatch):
    from app.services import library

    payload = epub_bytes(tmp_path)

    async def fake_resolve(source, ref, book_format):
        return (
            "https://wolnelektury.pl/media/book/epub/quo-vadis.epub",
            "Quo vadis",
            "Henryk Sienkiewicz",
        )

    async def fake_download(url, destination, max_bytes=None):
        destination.write_bytes(payload)

    monkeypatch.setattr(library, "resolve", fake_resolve)
    monkeypatch.setattr(library, "download", fake_download)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/library/download",
            data={
                "source": "wolnelektury",
                "ref": "quo-vadis",
                "book_format": "epub",
                "kindle_address": "k@kindle.com",
            },
        )

    assert "Review" in response.text
    job = (await job_queue.with_status("review"))[0]
    assert job.filename == "quo-vadis.epub"
    assert job.title == "Zebrane Opowiadania"
    assert (job.source, job.ref) == ("wolnelektury", "quo-vadis")
    assert Path(job.tmp_path).exists()
    Path(job.tmp_path).unlink(missing_ok=True)


async def test_download_of_format_without_metadata_uses_catalogue_title(monkeypatch):
    from app.services import library

    async def fake_resolve(source, ref, book_format):
        return (
            "https://wolnelektury.pl/media/book/txt/quo-vadis.txt",
            "Quo vadis",
            "Henryk Sienkiewicz",
        )

    async def fake_download(url, destination, max_bytes=None):
        destination.write_bytes(b"plain text without metadata")

    monkeypatch.setattr(library, "resolve", fake_resolve)
    monkeypatch.setattr(library, "download", fake_download)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/library/download",
            data={
                "source": "wolnelektury",
                "ref": "quo-vadis",
                "book_format": "txt",
                "kindle_address": "k@kindle.com",
            },
        )

    job = (await job_queue.with_status("review"))[0]
    assert job.title == "Quo vadis"
    assert job.author == "Henryk Sienkiewicz"
    assert job.id not in job.title
    Path(job.tmp_path).unlink(missing_ok=True)


async def test_uploaded_file_title_excludes_job_id_prefix(tmp_path: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/upload",
            files={"files": ("moja ksiazka.txt", io.BytesIO(b"tekst"), "text/plain")},
            data={"kindle_address": "k@kindle.com"},
        )

    job = (await job_queue.with_status("review"))[0]
    assert job.title == "moja ksiazka"
    assert job.id not in job.title
    Path(job.tmp_path).unlink(missing_ok=True)


async def test_library_download_surfaces_blocked_host(monkeypatch):
    from app.services import library

    async def fake_resolve(source, ref, book_format):
        return "http://127.0.0.1/secret", "Sneaky", "Nobody"

    monkeypatch.setattr(library, "resolve", fake_resolve)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/library/download",
            data={
                "source": "wolnelektury",
                "ref": "x",
                "book_format": "epub",
                "kindle_address": "k@kindle.com",
            },
        )

    assert "allowlist" in response.text
    assert (await job_queue.with_status("failed"))[0].error


async def test_job_status_endpoint_returns_card(tmp_path: Path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await post_upload(client, "book.epub", epub_bytes(tmp_path))
        job = (await job_queue.with_status("review"))[0]

        response = await client.get(f"/jobs/{job.id}")
        missing = await client.get("/jobs/nope")

    assert response.status_code == 200
    assert job.id in response.text
    assert "Job not found" in missing.text
    Path(job.tmp_path).unlink(missing_ok=True)


async def make_review_job(tmp_path: Path, status: str = "review") -> tuple[str, Path]:
    job = await job_queue.add("book.epub", status=status, kindle_address="k@kindle.com")
    stored = tmp_path / f"{job.id}_book.epub"
    make_epub(stored, "Dracula", "Bram Stoker")
    await job_queue.set(job.id, tmp_path=str(stored))
    return job.id, stored


async def test_queue_card_offers_download_and_delete(tmp_path: Path):
    job_id, _ = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}")

    assert f'href="/jobs/{job_id}/file"' in response.text
    assert f'hx-delete="/jobs/{job_id}"' in response.text


async def test_download_returns_the_stored_file(tmp_path: Path):
    job_id, stored = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}/file")

    assert response.status_code == 200
    assert response.content == stored.read_bytes()
    assert "book.epub" in response.headers["content-disposition"]


async def test_download_is_404_once_the_file_is_gone(tmp_path: Path):
    job_id, stored = await make_review_job(tmp_path)
    stored.unlink()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}/file")

    assert response.status_code == 404


async def test_card_hides_download_when_the_file_is_gone(tmp_path: Path):
    job_id, stored = await make_review_job(tmp_path)
    stored.unlink()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}")

    assert f"/jobs/{job_id}/file" not in response.text
    assert f'hx-delete="/jobs/{job_id}"' in response.text


async def test_delete_drops_the_job_and_its_file(tmp_path: Path):
    job_id, stored = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/jobs/{job_id}")

    assert response.text == ""
    assert await job_queue.get(job_id) is None
    assert not stored.exists()


async def test_delete_of_an_active_job_leaves_the_file_to_the_worker(tmp_path: Path):
    job_id, stored = await make_review_job(tmp_path, status="sending")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.delete(f"/jobs/{job_id}")

    assert await job_queue.get(job_id) is None
    assert stored.exists()


async def test_deleting_a_missing_job_is_a_no_op():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete("/jobs/doesnotexist")

    assert response.status_code == 200


async def make_pdf_job(tmp_path: Path) -> tuple[str, Path]:
    job = await job_queue.add("book.pdf", status="review", kindle_address="k@kindle.com")
    stored = tmp_path / f"{job.id}_book.pdf"
    stored.write_bytes(b"%PDF-1.4 fake")
    await job_queue.set(job.id, tmp_path=str(stored), title="Dracula", author="Bram Stoker")
    return job.id, stored


def fake_converter(
    monkeypatch, tmp_path: Path, error: str | None = None, calls: list | None = None
):
    async def convert_to_epub(input_path: Path, options=()):
        if calls is not None:
            calls.append((input_path.name, list(options)))
        if error:
            return None, error
        output = tmp_path / "calibre-output.epub"
        make_epub(output, "Dracula", "Bram Stoker")
        return output, None

    monkeypatch.setattr("app.main.CalibreConverter.convert_to_epub", convert_to_epub)


async def test_review_card_offers_presets_that_fit_the_file(tmp_path: Path):
    pdf_id, _ = await make_pdf_job(tmp_path)
    epub_id, _ = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pdf_card = await client.get(f"/jobs/{pdf_id}")
        epub_card = await client.get(f"/jobs/{epub_id}")

    assert f'hx-post="/jobs/{pdf_id}/convert"' in pdf_card.text
    for preset in ("standard", "cleanup", "text", "pdftohtml"):
        assert f'"preset": "{preset}"' in pdf_card.text
    assert '"preset": "cleanup"' in epub_card.text
    assert '"preset": "standard"' not in epub_card.text
    assert '"preset": "text"' not in epub_card.text


async def test_convert_replaces_the_file_with_an_epub(tmp_path: Path, monkeypatch):
    fake_converter(monkeypatch, tmp_path)
    job_id, stored = await make_pdf_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/jobs/{job_id}/convert", data={"title": "Drakula", "author": "B. Stoker"}
        )
        download = await client.get(f"/jobs/{job_id}/file")

    assert "Converting" in response.text
    job = await job_queue.get(job_id)
    assert job.status == "review"
    assert job.filename == "book.epub"
    assert (job.title, job.author) == ("Drakula", "B. Stoker")
    assert not stored.exists()
    assert Path(job.tmp_path).name == f"{job_id}_book.epub"
    assert "book.epub" in download.headers["content-disposition"]
    Path(job.tmp_path).unlink(missing_ok=True)


async def test_failed_conversion_keeps_the_original(tmp_path: Path, monkeypatch):
    fake_converter(monkeypatch, tmp_path, error="broken pdf")
    job_id, stored = await make_pdf_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(f"/jobs/{job_id}/convert")
        card = await client.get(f"/jobs/{job_id}")

    job = await job_queue.get(job_id)
    assert job.status == "review"
    assert job.filename == "book.pdf"
    assert stored.exists()
    assert "broken pdf" in card.text


async def test_convert_is_refused_outside_review(tmp_path: Path, monkeypatch):
    fake_converter(monkeypatch, tmp_path)
    job_id, stored = await make_pdf_job(tmp_path)
    await job_queue.update(job_id, "sending")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(f"/jobs/{job_id}/convert")

    job = await job_queue.get(job_id)
    assert job.status == "sending"
    assert job.filename == "book.pdf"
    assert stored.exists()


async def test_search_endpoint_passes_format_and_offers_more(monkeypatch):
    from app.services import library

    seen = []

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        seen.append((source, book_format, cursor))
        result = library.SearchResult(
            source="zlib",
            ref="abc",
            title="Dune",
            author="Frank Herbert",
            cover_url=None,
            formats={"epub": "epub"},
        )
        return library.SearchPage([result], next_cursor=(2, 4))

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/search", params={"q": "dune", "source": "zlib", "fmt": "epub"}
        )
        await client.get("/search", params={"q": "dune", "fmt": "exe", "page": 2, "offset": 4})

    assert seen == [("zlib", "epub", None), ("", "", (2, 4))]
    assert "Load more" in response.text
    assert "fmt=epub&amp;page=2&amp;offset=4" in response.text


async def test_appended_page_without_results_is_silent(monkeypatch):
    from app.services import library

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        return library.SearchPage([])

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/search", params={"q": "dune", "page": 3})

    assert "Nothing found" not in response.text
    assert "Load more" not in response.text


async def test_lookup_fills_the_review_card(tmp_path: Path, monkeypatch):
    from app.services.lookup import BookInfo

    seen = []

    async def fake_lookup(title, author):
        seen.append((title, author))
        return BookInfo("Dracula", "Bram Stoker", "data:image/jpeg;base64,AAAA")

    monkeypatch.setattr("app.main.lookup", fake_lookup)
    job_id, _ = await make_pdf_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/jobs/{job_id}/lookup", data={"title": "dracula", "author": ""}
        )

    assert seen == [("dracula", "")]
    assert 'value="Dracula"' in response.text
    assert 'value="Bram Stoker"' in response.text
    assert "data:image/jpeg;base64,AAAA" in response.text
    assert (await job_queue.get(job_id)).cover == "data:image/jpeg;base64,AAAA"


async def test_lookup_without_match_keeps_typed_values(tmp_path: Path, monkeypatch):
    async def fake_lookup(title, author):
        return None

    monkeypatch.setattr("app.main.lookup", fake_lookup)
    job_id, _ = await make_pdf_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/jobs/{job_id}/lookup", data={"title": "Moja ksiazka", "author": "Ja"}
        )

    assert 'value="Moja ksiazka"' in response.text
    assert "No match found" in response.text


async def test_review_card_always_offers_lookup(tmp_path: Path):
    job_id, _ = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}")

    assert f'hx-post="/jobs/{job_id}/lookup"' in response.text


async def test_convert_passes_the_preset_options(tmp_path: Path, monkeypatch):
    calls: list = []
    fake_converter(monkeypatch, tmp_path, calls=calls)
    job_id, _ = await make_pdf_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(f"/jobs/{job_id}/convert", data={"preset": "cleanup"})

    assert calls == [
        (
            f"{job_id}_book.pdf",
            ["--enable-heuristics", "--smarten-punctuation", "--unwrap-factor", "0.45"],
        )
    ]
    Path((await job_queue.get(job_id)).tmp_path).unlink(missing_ok=True)


async def test_unknown_or_unfit_preset_is_refused(tmp_path: Path, monkeypatch):
    calls: list = []
    fake_converter(monkeypatch, tmp_path, calls=calls)
    epub_id, _ = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(f"/jobs/{epub_id}/convert", data={"preset": "text"})
        await client.post(f"/jobs/{epub_id}/convert", data={"preset": "rm -rf"})

    assert calls == []
    assert (await job_queue.get(epub_id)).status == "review"


async def test_epub_can_be_cleaned_up_in_place(tmp_path: Path, monkeypatch):
    fake_converter(monkeypatch, tmp_path)
    job_id, stored = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(f"/jobs/{job_id}/convert", data={"preset": "cleanup"})

    job = await job_queue.get(job_id)
    assert job.status == "review"
    assert job.filename == "book.epub"
    assert Path(job.tmp_path) == stored
    assert stored.exists()


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


async def test_uploaded_cover_replaces_the_card_cover(tmp_path: Path):
    job_id, _ = await make_pdf_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/jobs/{job_id}/cover",
            files={"cover": ("c.png", PNG, "image/png")},
            data={"title": "Drakula", "author": "Stoker"},
        )

    job = await job_queue.get(job_id)
    assert job.cover.startswith("data:image/png;base64,")
    assert (job.title, job.author) == ("Drakula", "Stoker")
    assert job.cover in response.text


async def test_cover_upload_rejects_non_images_and_large_files(tmp_path: Path, monkeypatch):
    job_id, _ = await make_pdf_job(tmp_path)
    monkeypatch.setattr("app.main.MAX_COVER_BYTES", 32)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        html_response = await client.post(
            f"/jobs/{job_id}/cover", files={"cover": ("c.png", b"<html>", "image/png")}
        )
        big_response = await client.post(
            f"/jobs/{job_id}/cover", files={"cover": ("c.png", PNG * 4, "image/png")}
        )

    assert "JPEG, PNG or GIF" in html_response.text
    assert "smaller than" in big_response.text
    assert (await job_queue.get(job_id)).cover is None


async def card_version(job_id: str) -> str:
    from app.main import job_card_context

    return job_card_context(await job_queue.get(job_id))["version"]


async def test_polling_skips_the_swap_while_the_card_is_unchanged(tmp_path: Path):
    job_id, _ = await make_review_job(tmp_path, status="sending")
    version = await card_version(job_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unchanged = await client.get(f"/jobs/{job_id}", params={"since": version})
        await job_queue.update(job_id, "sent")
        changed = await client.get(f"/jobs/{job_id}", params={"since": version})

    assert unchanged.status_code == 204
    assert unchanged.content == b""
    assert changed.status_code == 200
    assert "Sent" in changed.text


async def test_polling_swaps_when_the_card_changes_without_a_status_change(tmp_path: Path):
    job_id, _ = await make_review_job(tmp_path, status="converting")
    version = await card_version(job_id)
    await job_queue.set(job_id, error="Retrying conversion")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}", params={"since": version})

    assert response.status_code == 200
    assert "Retrying conversion" in response.text
    assert f"since={await card_version(job_id)}" in response.text


async def test_index_busts_the_stylesheet_cache():
    from app.main import static_url

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert static_url("css/style.css") in response.text
    assert "/static/css/style.css?v=" in response.text
    assert "lucide@latest" not in response.text


def catalogue_result(source: str, ref: str, title: str, formats: dict[str, str]):
    from app.services import library

    return library.SearchResult(
        source=source, ref=ref, title=title, author="A", cover_url=None, formats=formats
    )


async def search_with(monkeypatch, results):
    from app.services import library

    async def fake_search(query, language="", source="", book_format="", cursor=None):
        return library.SearchPage(results)

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/search", params={"q": "x"})


def result_block(html: str, title: str) -> str:
    start = html.index(f'search-result__title">{title}<')
    end = html.find('<div class="search-result">', start)
    return html[start : end if end != -1 else None]


async def test_search_marks_books_that_were_already_sent(monkeypatch):
    await job_queue.add("dune.epub", status="sent", source="zlib", ref="dune1", title="Diuna")
    await job_queue.add("quo.epub", status="sent", title="  Quo   VADIS ")

    response = await search_with(
        monkeypatch,
        [
            catalogue_result("zlib", "dune1", "Dune", {"epub": ""}),
            catalogue_result("wolnelektury", "quo-vadis", "Quo vadis", {"epub": ""}),
            catalogue_result("gutenberg", "1", "Dracula", {"epub": ""}),
        ],
    )

    assert "search-result__sent" in result_block(response.text, "Dune")
    assert "search-result__sent" in result_block(response.text, "Quo vadis")
    assert "search-result__sent" not in result_block(response.text, "Dracula")


async def test_search_results_offer_a_picker_with_the_preferred_format(monkeypatch):
    response = await search_with(
        monkeypatch,
        [
            catalogue_result("gutenberg", "1", "Dracula", {"mobi": "", "epub": ""}),
            catalogue_result("gutenberg", "2", "Emma", {"pdf": "", "mobi": ""}),
            catalogue_result("gutenberg", "3", "Nothing", {}),
        ],
    )

    assert 'data-format="epub"' in result_block(response.text, "Dracula")
    assert 'data-format="pdf"' in result_block(response.text, "Emma")
    assert "search-result__select" not in result_block(response.text, "Nothing")


async def test_finished_job_records_when_it_was_sent(tmp_path: Path, monkeypatch):
    from app import main

    async def fake_send(*args, **kwargs):
        pass

    monkeypatch.setattr(main, "send_to_kindle", fake_send)
    job_id, _ = await make_review_job(tmp_path, status="pending")

    await main.process_file_job(job_id)

    job = await job_queue.get(job_id)
    assert job.status == "sent"
    assert job.sent_at is not None


async def test_removing_a_sent_card_keeps_it_in_history(tmp_path: Path):
    job_id, _ = await make_review_job(tmp_path, status="sent")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        removed = await client.delete(f"/jobs/{job_id}")
        history = await client.get("/history")

    assert removed.text == ""
    assert await job_queue.get(job_id) is not None
    assert f'id="history-{job_id}"' in history.text
    assert "Upload" not in history.text


async def test_history_labels_uploaded_files():
    await job_queue.add("own.epub", status="sent", source="upload", title="Own book")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        history = await client.get("/history")

    assert "Upload" in history.text


async def test_history_can_forget_a_sent_book_but_not_an_active_job(tmp_path: Path):
    sent_id, _ = await make_review_job(tmp_path, status="sent")
    active = await job_queue.add("x.epub", status="sending")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.delete(f"/history/{sent_id}")
        await client.delete(f"/history/{active.id}")
        history = await client.get("/history")

    assert await job_queue.get(sent_id) is None
    assert await job_queue.get(active.id) is not None
    assert "Nothing sent yet." in history.text


async def test_index_offers_batch_actions_and_history():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert 'id="send-all"' in response.text
    assert 'id="pick-bar"' in response.text
    assert 'hx-get="/history"' in response.text


async def test_index_takes_the_default_kindle_address_from_settings():
    from app.preferences import save_preferences

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.get("/")
        await save_preferences(kindle_address="me_1@kindle.com")
        saved = await client.get("/")

    assert 'id="kindle-addr" value=""' in empty.text
    assert 'id="kindle-addr" value="me_1@kindle.com"' in saved.text


async def post_settings(**data):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/settings", data=data)


async def test_saving_settings_updates_the_page(monkeypatch):
    from app.preferences import preferences

    response = await post_settings(kindle_address=" me_1@kindle.com ")

    assert preferences.kindle_address == "me_1@kindle.com"
    assert preferences.show_sender_notice is False
    assert "Saved" in response.text
    assert 'id="kindle-addr" value="me_1@kindle.com"' in response.text
    assert '<div id="sender-notice" hidden hx-swap-oob="true">' in response.text


async def test_invalid_settings_are_reported_and_not_saved():
    from app.preferences import preferences

    response = await post_settings(kindle_address="not-an-address", show_sender_notice="true")

    assert "look like an e-mail address" in response.text
    assert 'value="not-an-address"' in response.text
    assert preferences.kindle_address == ""
    assert "hx-swap-oob" not in response.text


async def test_dismissed_notice_stays_hidden():
    from app.preferences import preferences

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        shown = await client.get("/")
        dismissed = await client.post("/settings/notice/dismiss")
        page = await client.get("/")

    assert "Sender whitelist required" in shown.text
    assert preferences.show_sender_notice is False
    assert 'id="sender-notice" hidden' in dismissed.text
    assert "notice__content" not in dismissed.text
    assert "Sender whitelist required" not in page.text


async def test_settings_form_never_reveals_the_environment_account(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "zlib_email", "owner@example.com")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/settings")

    assert "owner@example.com" not in response.text
    assert ".env" not in response.text


@pytest.fixture
def zlib_restarts(monkeypatch):
    from app.services import z_lib

    calls = []

    async def warm_up():
        calls.append("warm_up")

    monkeypatch.setattr(z_lib, "reset", lambda: calls.append("reset"))
    monkeypatch.setattr(z_lib, "warm_up", warm_up)
    return calls


async def zlib_request(method: str, url: str, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


async def add_account(email: str, password: str):
    return await zlib_request(
        "POST", "/settings/zlib/accounts", data={"email": email, "password": password}
    )


async def test_first_account_becomes_active(zlib_restarts):
    from app.accounts import list_accounts
    from app.preferences import preferences

    response = await add_account("one@example.com", "pass-1")

    assert [account.email for account in await list_accounts()] == ["one@example.com"]
    assert preferences.zlib_active == "one@example.com"
    assert zlib_restarts == ["reset", "warm_up"]
    assert 'id="zlib-accounts"' in response.text
    assert 'id="zlib-switch"' in response.text


async def test_more_accounts_keep_the_active_one_until_switched(zlib_restarts):
    from app.preferences import preferences

    await add_account("one@example.com", "pass-1")
    response = await add_account("two@example.com", "pass-1")

    assert preferences.zlib_active == "one@example.com"
    assert zlib_restarts == ["reset", "warm_up"]
    assert '<option value="two@example.com" >' in response.text
    assert 'id="zlib-account"' in response.text


async def test_switching_accounts_restarts_the_session(zlib_restarts):
    from app.preferences import preferences

    await add_account("one@example.com", "pass-1")
    await add_account("two@example.com", "pass-1")
    zlib_restarts.clear()

    response = await zlib_request(
        "POST", "/settings/zlib/active", data={"email": "two@example.com"}
    )
    await zlib_request("POST", "/settings/zlib/active", data={"email": "two@example.com"})
    await zlib_request("POST", "/settings/zlib/active", data={"email": "ghost@example.com"})

    assert preferences.zlib_active == "two@example.com"
    assert zlib_restarts == ["reset", "warm_up"]
    assert '<option value="two@example.com" selected>' in response.text


async def test_removing_the_active_account_falls_back_to_the_next(zlib_restarts):
    from app.accounts import list_accounts
    from app.preferences import preferences

    await add_account("one@example.com", "pass-1")
    await add_account("two@example.com", "pass-1")

    await zlib_request("DELETE", "/settings/zlib/accounts", params={"email": "one@example.com"})
    assert preferences.zlib_active == "two@example.com"

    response = await zlib_request(
        "DELETE", "/settings/zlib/accounts", params={"email": "two@example.com"}
    )
    assert preferences.zlib_active == ""
    assert await list_accounts() == []
    assert "No accounts yet" in response.text


async def test_readding_an_account_updates_its_password(zlib_restarts):
    from app.accounts import get_account

    await add_account("one@example.com", "old")
    zlib_restarts.clear()

    await add_account(" one@example.com ", "new")

    assert (await get_account("one@example.com")).password == "new"
    assert zlib_restarts == ["reset", "warm_up"]


async def test_invalid_accounts_are_refused(zlib_restarts):
    from app.accounts import list_accounts

    bad_email = await add_account("not-an-address", "pass-1")
    no_password = await add_account("one@example.com", "")

    assert "look like an e-mail address" in bad_email.text
    assert "Enter the Z-Lib password" in no_password.text
    assert 'value="one@example.com"' in no_password.text
    assert await list_accounts() == []
    assert zlib_restarts == []


async def test_settings_never_show_account_passwords(zlib_restarts):
    await add_account("one@example.com", "hunter2")

    response = await zlib_request("GET", "/settings")

    assert "one@example.com" in response.text
    assert "hunter2" not in response.text


async def test_account_switch_appears_on_the_page_with_two_accounts(zlib_restarts):
    one = await zlib_request("GET", "/")
    await add_account("one@example.com", "pass-1")
    await add_account("two@example.com", "pass-1")
    two = await zlib_request("GET", "/")

    assert 'id="zlib-account"' not in one.text
    assert 'id="zlib-account"' in two.text
