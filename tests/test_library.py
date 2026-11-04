from pathlib import Path

import httpx
import pytest

from app.services import library

GUTENDEX_PAYLOAD = {
    "results": [
        {
            "id": 8816,
            "title": "Quo Vadis, a Narrative of the Time of Nero",
            "authors": [{"name": "Sienkiewicz, Henryk"}],
            "formats": {
                "text/html": "https://www.gutenberg.org/ebooks/8816.html.images",
                "text/plain; charset=utf-8": "https://www.gutenberg.org/files/8816/8816-0.txt",
                "image/jpeg": "https://www.gutenberg.org/cache/epub/8816/pg8816.cover.medium.jpg",
                "application/rdf+xml": "https://www.gutenberg.org/ebooks/8816.rdf",
            },
        },
        {
            "id": 345,
            "title": "Dracula",
            "authors": [{"name": "Stoker, Bram"}],
            "formats": {
                "application/epub+zip": "https://www.gutenberg.org/ebooks/345.epub3.images",
                "application/x-mobipocket-ebook": "https://www.gutenberg.org/ebooks/345.kf8.images",
                "image/jpeg": "https://www.gutenberg.org/cache/epub/345/pg345.cover.medium.jpg",
            },
        },
    ]
}

WL_CATALOG = [
    {
        "slug": "quo-vadis",
        "title": "Quo vadis",
        "author": "Henryk Sienkiewicz",
        "cover_thumb": "book/cover_thumb/quo-vadis_vfcpo3v.jpg",
    },
    {
        "slug": "pan-tadeusz",
        "title": "Pan Tadeusz",
        "author": "Adam Mickiewicz",
        "cover_thumb": "book/cover_thumb/pan-tadeusz.jpg",
        "simple_thumb": "https://wolnelektury.pl/media/book/cover_api_thumb/pan-tadeusz.jpg",
    },
]

WL_DETAIL = {
    "title": "Quo vadis",
    "authors": [{"name": "Henryk Sienkiewicz", "slug": "henryk-sienkiewicz"}],
    "epub": "https://wolnelektury.pl/media/book/epub/quo-vadis.epub",
    "mobi": "https://wolnelektury.pl/media/book/mobi/quo-vadis.mobi",
    "txt": "https://wolnelektury.pl/media/book/txt/quo-vadis.txt",
}


GITHUB_PAYLOAD = {
    "items": [
        {
            "name": "bram-stoker_dracula",
            "description": (
                "Epub source for the Standard Ebooks edition of Dracula, by Bram Stoker"
            ),
        },
        {
            "name": "omar-khayyam_the-rubaiyat_edward-fitzgerald",
            "description": (
                "Epub source for the Standard Ebooks edition of The Rubaiyat, "
                "by Omar Khayyam and translated by Edward FitzGerald"
            ),
        },
        {"name": "tools", "description": "The Standard Ebooks toolset"},
    ]
}


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    library._catalog = []
    library._catalog_fetched_at = 0.0
    yield
    library._catalog = []
    library._catalog_fetched_at = 0.0


def fake_api(calls: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        url = str(request.url)
        if "gutendex.com/books/345" in url:
            return httpx.Response(200, json=GUTENDEX_PAYLOAD["results"][1])
        if "api.github.com" in url:
            return httpx.Response(200, json=GITHUB_PAYLOAD)
        if "gutendex.com" in url:
            return httpx.Response(200, json=GUTENDEX_PAYLOAD)
        if url.rstrip("/").endswith("/api/books"):
            return httpx.Response(200, json=WL_CATALOG)
        if "quo-vadis" in url:
            return httpx.Response(200, json=WL_DETAIL)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def patch_client(monkeypatch, transport):
    original = httpx.AsyncClient

    def build(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(library.httpx, "AsyncClient", build)


async def test_search_merges_all_three_sources(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("quo vadis")).results

    sources = {r.source for r in results}
    assert sources == {"wolnelektury", "gutenberg", "standardebooks"}


async def test_standardebooks_builds_download_urls_from_repo_name(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("dracula")).results
    dracula = next(r for r in results if r.ref == "bram-stoker_dracula")

    base = "https://standardebooks.org/ebooks/bram-stoker/dracula/downloads/"
    assert dracula.title == "Dracula"
    assert dracula.author == "Bram Stoker"
    assert dracula.formats["epub"] == f"{base}bram-stoker_dracula.epub?source=feed"
    assert dracula.formats["azw3"] == f"{base}bram-stoker_dracula.azw3?source=feed"
    assert dracula.cover_url == f"{base}cover.jpg"
    assert dracula.language == "en"


async def test_standardebooks_handles_translator_in_repo_name(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("rubaiyat")).results
    book = next(r for r in results if "rubaiyat" in r.ref)

    assert book.title == "The Rubaiyat"
    assert book.author == "Omar Khayyam"
    assert "/omar-khayyam/the-rubaiyat/edward-fitzgerald/" in book.formats["epub"]


async def test_standardebooks_skips_non_book_repos(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("tools")).results

    assert all(r.ref != "tools" for r in results)


async def test_language_filter_keeps_only_matching_sources(monkeypatch):
    patch_client(monkeypatch, fake_api())

    polish = (await library.search("quo vadis", language="pl")).results
    english = (await library.search("dracula", language="en")).results

    assert {r.source for r in polish} == {"wolnelektury", "gutenberg"}
    assert "standardebooks" not in {r.source for r in polish}
    assert "wolnelektury" not in {r.source for r in english}


async def test_source_switch_queries_only_the_chosen_source(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    results = (await library.search("dracula", source="standardebooks")).results

    assert {r.source for r in results} == {"standardebooks"}
    assert all("gutendex" not in c and "wolnelektury" not in c for c in calls)


async def test_source_switch_overrides_the_language_filter(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("quo vadis", language="en", source="wolnelektury")).results

    assert {r.source for r in results} == {"wolnelektury"}


async def test_zlib_search_passes_filters_and_cursor(monkeypatch):
    async def fake_search(query, limit, language, extension, cursor):
        assert (query, limit, language, extension, cursor) == ("dracula", 10, "pl", "epub", (2, 5))
        book = {
            "ref": "123",
            "title": "Dracula",
            "author": "Bram Stoker",
            "cover_url": None,
            "extension": "epub",
            "language": "polish",
        }
        return [book], (3, 0)

    monkeypatch.setattr(library.z_lib, "search", fake_search)

    found = await library.search(
        "dracula", language="pl", source="zlib", book_format="epub", cursor=(2, 5)
    )

    assert [(book.source, book.ref, book.formats) for book in found.results] == [
        ("zlib", "123", {"epub": "epub"})
    ]
    assert found.next_cursor == (3, 0)


async def test_other_sources_have_no_further_pages(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    found = await library.search("dracula", cursor=(2, 0))

    assert found.results == []
    assert found.next_cursor is None
    assert calls == []


async def test_format_filter_keeps_books_offering_it(monkeypatch):
    patch_client(monkeypatch, fake_api())

    everything = (await library.search("quo vadis")).results
    only_epub = (await library.search("quo vadis", book_format="epub")).results

    assert 0 < len(only_epub) < len(everything)
    assert all("epub" in book.formats for book in only_epub)


async def test_zlib_is_not_queried_with_other_sources(monkeypatch):
    patch_client(monkeypatch, fake_api())

    async def unexpected_zlib_search(*args):
        raise AssertionError("z-lib search must not run unless selected")

    monkeypatch.setattr(library.z_lib, "search", unexpected_zlib_search)

    results = (await library.search("quo vadis", language="pl")).results

    assert {r.source for r in results} == {"wolnelektury", "gutenberg"}


async def test_unknown_source_yields_nothing(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    assert (await library.search("dracula", source="libgen")).results == []
    assert calls == []


async def test_language_is_passed_to_gutendex(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    await library.search("dracula", language="pl")

    gutendex = [c for c in calls if "gutendex" in c]
    assert any("languages=pl" in c for c in gutendex)


async def test_no_language_filter_queries_gutendex_without_param(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    await library.search("dracula")

    gutendex = [c for c in calls if "gutendex" in c]
    assert all("languages=" not in c for c in gutendex)


async def test_book_without_epub_still_offers_convertible_formats(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("quo vadis")).results
    nero = next(r for r in results if "Nero" in r.title)

    assert "epub" not in nero.formats
    assert set(nero.formats) == {"html", "txt"}


async def test_non_book_formats_are_dropped(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("quo vadis")).results
    dracula = next(r for r in results if r.title == "Dracula")

    assert set(dracula.formats) == {"epub", "mobi"}
    assert dracula.cover_url.endswith(".jpg")


async def test_relative_cover_paths_become_absolute(monkeypatch):
    patch_client(monkeypatch, fake_api())

    results = (await library.search("quo vadis")).results
    wolne = next(r for r in results if r.source == "wolnelektury")

    assert wolne.cover_url == (
        "https://wolnelektury.pl/media/book/cover_thumb/quo-vadis_vfcpo3v.jpg"
    )


def test_absolute_urls_are_left_alone():
    absolute = "https://wolnelektury.pl/media/book/cover_api_thumb/x.jpg"
    assert library._absolute_media_url(absolute) == absolute
    assert library._absolute_media_url(None) is None
    assert library._absolute_media_url("book/x.jpg") == "https://wolnelektury.pl/media/book/x.jpg"


async def test_catalog_is_cached_between_searches(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    await library.search("quo vadis")
    first = len([c for c in calls if c.rstrip("/").endswith("/api/books")])
    await library.search("pan tadeusz")
    second = len([c for c in calls if c.rstrip("/").endswith("/api/books")])

    assert first == 1
    assert second == 1


async def test_one_broken_source_does_not_kill_the_other(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "gutendex" in str(request.url):
            return httpx.Response(500, json={})
        if str(request.url).rstrip("/").endswith("/api/books"):
            return httpx.Response(200, json=WL_CATALOG)
        return httpx.Response(200, json=WL_DETAIL)

    patch_client(monkeypatch, httpx.MockTransport(handler))

    results = (await library.search("quo vadis")).results

    assert results
    assert {r.source for r in results} == {"wolnelektury"}


async def test_resolve_returns_requested_format(monkeypatch):
    patch_client(monkeypatch, fake_api())

    url, title, author = await library.resolve("wolnelektury", "quo-vadis", "mobi")

    assert url.endswith("quo-vadis.mobi")
    assert title == "Quo vadis"
    assert author == "Henryk Sienkiewicz"


async def test_resolve_joins_multiple_authors(monkeypatch):
    patch_client(monkeypatch, fake_api())

    _, _, author = await library.resolve("gutenberg", "345", "epub")

    assert author == "Stoker, Bram"


async def test_resolve_rejects_missing_format(monkeypatch):
    patch_client(monkeypatch, fake_api())

    with pytest.raises(ValueError, match="not available"):
        await library.resolve("wolnelektury", "quo-vadis", "fb2")


async def test_resolve_rejects_unknown_source():
    with pytest.raises(ValueError, match="Unknown source"):
        await library.resolve("libgen", "x", "epub")


async def test_resolve_standardebooks_needs_no_network():
    url, title, author = await library.resolve("standardebooks", "bram-stoker_dracula", "epub")

    assert url == (
        "https://standardebooks.org/ebooks/bram-stoker/dracula/downloads/"
        "bram-stoker_dracula.epub?source=feed"
    )
    assert (title, author) == ("Dracula", "Bram Stoker")
    assert library.host_allowed(url)


@pytest.mark.parametrize(
    "ref",
    ["../../etc/passwd", "bram-stoker/dracula", "a b", "", "-leading-dash"],
)
async def test_resolve_rejects_unsafe_refs(ref: str):
    with pytest.raises(ValueError, match="Invalid book reference"):
        await library.resolve("standardebooks", ref, "epub")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/secret",
        "http://localhost/admin",
        "https://evil.example.com/book.epub",
        "file:///etc/passwd",
    ],
)
async def test_download_blocks_hosts_outside_allowlist(tmp_path: Path, url: str):
    with pytest.raises(ValueError, match="not on the allowlist"):
        await library.download(url, tmp_path / "out.epub")


async def test_download_blocks_redirect_off_the_allowlist(tmp_path: Path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1:9000/pwn"})

    patch_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="redirect leaves the allowlist"):
        await library.download(
            "https://wolnelektury.pl/media/book/epub/quo-vadis.epub", tmp_path / "out.epub"
        )


async def test_download_follows_allowed_redirect(tmp_path: Path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "redirect" in str(request.url):
            return httpx.Response(302, headers={"location": "https://www.gutenberg.org/final.epub"})
        return httpx.Response(200, content=b"PK\x03\x04payload")

    patch_client(monkeypatch, httpx.MockTransport(handler))
    destination = tmp_path / "out.epub"

    await library.download("https://www.gutenberg.org/redirect.epub", destination)

    assert destination.read_bytes() == b"PK\x03\x04payload"


async def test_download_rejects_html_interstitial_page(tmp_path: Path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<?xml version='1.0'?><html>Your Download Has Started!</html>",
            headers={"content-type": "application/xhtml+xml; charset=utf-8"},
        )

    patch_client(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="not a file"):
        await library.download(
            "https://standardebooks.org/ebooks/a/b/downloads/a_b.epub", tmp_path / "out.epub"
        )


async def test_download_aborts_when_too_large(tmp_path: Path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 4096)

    patch_client(monkeypatch, httpx.MockTransport(handler))
    destination = tmp_path / "big.epub"

    with pytest.raises(ValueError, match="exceeds"):
        await library.download(
            "https://wolnelektury.pl/media/book/epub/big.epub", destination, max_bytes=100
        )

    assert not destination.exists()


async def test_empty_query_skips_network(monkeypatch):
    calls: list[str] = []
    patch_client(monkeypatch, fake_api(calls))

    assert (await library.search("   ")).results == []
    assert calls == []


def test_host_allowlist_accepts_known_sources():
    assert library.host_allowed("https://wolnelektury.pl/media/book/epub/x.epub")
    assert library.host_allowed("https://www.gutenberg.org/ebooks/1.epub3.images")
    assert not library.host_allowed("https://gutendex.com.evil.net/x")
    assert not library.host_allowed("https://wolnelektury.pl.evil.net/x")
