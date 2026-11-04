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


def fake_converter(monkeypatch, tmp_path: Path, error: str | None = None):
    async def convert_to_epub(input_path: Path):
        if error:
            return None, error
        output = tmp_path / "calibre-output.epub"
        make_epub(output, "Dracula", "Bram Stoker")
        return output, None

    monkeypatch.setattr("app.main.CalibreConverter.convert_to_epub", convert_to_epub)


async def test_review_card_offers_conversion_only_for_non_epub(tmp_path: Path):
    pdf_id, _ = await make_pdf_job(tmp_path)
    epub_id, _ = await make_review_job(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pdf_card = await client.get(f"/jobs/{pdf_id}")
        epub_card = await client.get(f"/jobs/{epub_id}")

    assert f'hx-post="/jobs/{pdf_id}/convert"' in pdf_card.text
    assert "/convert" not in epub_card.text


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
