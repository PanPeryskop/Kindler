import base64

import httpx
import pytest

from app.services import lookup as lookup_service
from app.services.lookup import lookup

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def client_for(routes: dict[str, httpx.Response], calls: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                return response
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def image() -> httpx.Response:
    return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})


def open_library(*docs: dict) -> httpx.Response:
    return httpx.Response(200, json={"docs": list(docs)})


def google(*volumes: dict) -> httpx.Response:
    return httpx.Response(200, json={"items": [{"volumeInfo": v} for v in volumes]})


async def test_open_library_match_with_cover():
    calls: list[str] = []
    client = client_for(
        {
            "https://openlibrary.org/search.json": open_library(
                {"title": "Dune", "author_name": ["Frank Herbert"], "cover_i": 42}
            ),
            "https://covers.openlibrary.org/b/id/42-L.jpg": image(),
        },
        calls,
    )

    found = await lookup("dune", "herbert", client)

    assert (found.title, found.author) == ("Dune", "Frank Herbert")
    assert found.cover == "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    assert "q=dune+herbert" in calls[0]
    assert not any("googleapis" in url for url in calls)


async def test_google_supplies_the_cover_open_library_lacks():
    client = client_for(
        {
            "https://openlibrary.org/search.json": open_library(
                {"title": "Diuna", "author_name": ["Frank Herbert"]}
            ),
            "https://www.googleapis.com/books/v1/volumes": google(
                {
                    "title": "Diuna (wydanie)",
                    "authors": ["F. Herbert"],
                    "imageLinks": {"thumbnail": "http://books.google.com/c?id=1"},
                }
            ),
            "https://books.google.com/c": image(),
        }
    )

    found = await lookup("diuna", "", client)

    assert (found.title, found.author) == ("Diuna", "Frank Herbert")
    assert found.cover.startswith("data:image/jpeg;base64,")


async def test_google_is_the_fallback_when_open_library_finds_nothing():
    client = client_for(
        {
            "https://openlibrary.org/search.json": open_library(),
            "https://www.googleapis.com/books/v1/volumes": google(
                {"title": "Lalka", "authors": ["Bolesław Prus"]}
            ),
        }
    )

    found = await lookup("lalka", "prus", client)

    assert (found.title, found.author, found.cover) == ("Lalka", "Bolesław Prus", None)


async def test_service_errors_mean_no_match():
    client = client_for(
        {
            "https://openlibrary.org/search.json": httpx.Response(503),
            "https://www.googleapis.com/books/v1/volumes": httpx.Response(429),
        }
    )

    assert await lookup("dune", "", client) is None


async def test_empty_query_skips_the_network():
    calls: list[str] = []

    assert await lookup("  ", "", client_for({}, calls)) is None
    assert calls == []


async def test_cover_from_a_foreign_host_is_ignored():
    calls: list[str] = []
    client = client_for(
        {
            "https://openlibrary.org/search.json": open_library({"title": "Dune"}),
            "https://www.googleapis.com/books/v1/volumes": google(
                {"title": "Dune", "imageLinks": {"thumbnail": "https://evil.example/x.jpg"}}
            ),
        },
        calls,
    )

    found = await lookup("dune", "", client)

    assert found.cover is None
    assert not any("evil.example" in url for url in calls)


async def test_non_image_cover_is_ignored():
    client = client_for(
        {
            "https://openlibrary.org/search.json": open_library({"title": "Dune", "cover_i": 1}),
            "https://covers.openlibrary.org/": httpx.Response(
                200, content=b"<html>", headers={"content-type": "text/html"}
            ),
        }
    )

    assert (await lookup("dune", "", client)).cover is None


async def test_requests_off_the_allowlist_are_blocked():
    with pytest.raises(ValueError, match="blocked"):
        await lookup_service._check_request(httpx.Request("GET", "https://169.254.169.254/x"))

    await lookup_service._check_request(httpx.Request("GET", "https://ia800.us.archive.org/x"))
    await lookup_service._check_request(httpx.Request("GET", "https://openlibrary.org/search"))


def test_translated_title_is_kept_while_author_and_cover_are_filled():
    from app.services.lookup import BookInfo, merge

    merged = merge("Diuna", "", BookInfo("Dune", "Frank Herbert", "data:image/jpeg;base64,A"))

    assert merged == BookInfo("Diuna", "Frank Herbert", "data:image/jpeg;base64,A")


def test_similar_or_placeholder_titles_take_the_catalogue_spelling():
    from app.services.lookup import BookInfo, merge

    found = BookInfo("Dune", "Frank Herbert")

    assert merge("dune", "F. Herbert", found).title == "Dune"
    assert merge("dune", "F. Herbert", found).author == "Frank Herbert"
    assert merge("frank_herbert-dune", "Unknown Author", found).title == "Dune"
    assert merge("book.epub", "", found).title == "Dune"


def test_match_on_neither_title_nor_author_is_rejected():
    from app.services.lookup import BookInfo, merge

    assert merge("Lalka", "Bolesław Prus", BookInfo("Dune", "Frank Herbert")) is None


def test_polish_letters_match_their_ascii_spelling():
    from app.services.lookup import BookInfo, merge

    merged = merge("Lalka", "Boleslaw Prus", BookInfo("Lalka", "Bolesław Prus"))

    assert merged.author == "Bolesław Prus"
