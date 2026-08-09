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

    async def fake_search(query, limit=None):
        return [
            library.SearchResult(
                source="wolnelektury",
                ref="quo-vadis",
                title="Quo vadis",
                author="Henryk Sienkiewicz",
                cover_url="https://wolnelektury.pl/cover.jpg",
                formats={"epub": "https://wolnelektury.pl/x.epub", "mobi": "https://x/y.mobi"},
            )
        ]

    monkeypatch.setattr(library, "search", fake_search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/search", params={"q": "quo vadis"})

    assert "Quo vadis" in response.text
    assert "Wolne Lektury" in response.text
    assert 'value="epub"' in response.text
    assert 'value="mobi"' in response.text


async def test_search_endpoint_reports_empty_query(monkeypatch):
    from app.services import library

    async def fake_search(query, limit=None):
        return []

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
