import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from slugify import slugify

from app.services.covers import data_uri

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPEN_LIBRARY_COVER_URL = "https://covers.openlibrary.org/b/id/{id}-L.jpg?default=false"
GOOGLE_BOOKS_SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
COVER_HOSTS = (
    "covers.openlibrary.org",
    "archive.org",
    "books.google.com",
    "books.googleusercontent.com",
)
API_HOSTS = ("openlibrary.org", "www.googleapis.com")
USER_AGENT = "Kindler/0.1 (self-hosted Send-to-Kindle helper)"
HTTP_TIMEOUT = 15.0
MAX_COVER_BYTES = 2 * 1024 * 1024
RESULTS_TO_CHECK = 5
UNKNOWN_AUTHOR = "unknown author"
FILENAME_LIKE = re.compile(r"(_|\.(epub|pdf|mobi|azw3?|fb2|txt|docx?|rtf|html?)$)", re.IGNORECASE)


@dataclass
class BookInfo:
    title: str
    author: str
    cover: str | None = None


def _host_allowed(url: httpx.URL | str, hosts: tuple[str, ...]) -> bool:
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith(f".{allowed}") for allowed in hosts
    )


async def _check_request(request: httpx.Request) -> None:
    if not _host_allowed(request.url, API_HOSTS + COVER_HOSTS):
        raise ValueError(f"Request blocked: {request.url.host} is not allowed.")


def _query(title: str, author: str) -> str:
    return " ".join(part for part in (title.strip(), author.strip()) if part)


async def _open_library(client: httpx.AsyncClient, query: str) -> BookInfo | None:
    response = await client.get(
        OPEN_LIBRARY_SEARCH_URL,
        params={
            "q": query,
            "limit": RESULTS_TO_CHECK,
            "fields": "title,author_name,cover_i",
        },
    )
    response.raise_for_status()
    for doc in response.json().get("docs", []):
        if not doc.get("title"):
            continue
        cover_id = doc.get("cover_i")
        return BookInfo(
            title=doc["title"],
            author=", ".join(doc.get("author_name") or []),
            cover=OPEN_LIBRARY_COVER_URL.format(id=cover_id) if cover_id else None,
        )
    return None


def _google_cover(links: dict[str, str]) -> str | None:
    for size in ("extraLarge", "large", "medium", "small", "thumbnail"):
        if links.get(size):
            return links[size].replace("http://", "https://", 1)
    return None


async def _google_books(client: httpx.AsyncClient, query: str) -> BookInfo | None:
    response = await client.get(
        GOOGLE_BOOKS_SEARCH_URL,
        params={"q": query, "maxResults": RESULTS_TO_CHECK, "printType": "books"},
    )
    response.raise_for_status()
    for item in response.json().get("items", []):
        volume = item.get("volumeInfo", {})
        if not volume.get("title"):
            continue
        return BookInfo(
            title=volume["title"],
            author=", ".join(volume.get("authors") or []),
            cover=_google_cover(volume.get("imageLinks") or {}),
        )
    return None


async def _download_cover(client: httpx.AsyncClient, url: str) -> str | None:
    if not _host_allowed(url, COVER_HOSTS):
        return None
    try:
        async with client.stream("GET", url) as response:
            media_type = response.headers.get("content-type", "").split(";")[0].strip()
            if response.status_code != 200 or not media_type.startswith("image/"):
                return None
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_COVER_BYTES:
                    return None
    except (httpx.HTTPError, ValueError):
        return None
    return data_uri(bytes(data), media_type)


async def _attempt(
    source: Callable[[httpx.AsyncClient, str], Awaitable[BookInfo | None]],
    client: httpx.AsyncClient,
    query: str,
) -> BookInfo | None:
    try:
        return await source(client, query)
    except (httpx.HTTPError, ValueError):
        return None


async def _search(client: httpx.AsyncClient, query: str) -> BookInfo | None:
    found = await _attempt(_open_library, client, query)
    if found and found.cover:
        return found

    google = await _attempt(_google_books, client, query)
    if found is None:
        return google
    if google and google.cover:
        found.cover = google.cover
    return found


async def lookup(
    title: str, author: str, client: httpx.AsyncClient | None = None
) -> BookInfo | None:
    query = _query(title, author)
    if not query:
        return None

    owned = client is None
    client = client or httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_check_request]},
    )
    try:
        found = await _search(client, query)
        if found and found.cover:
            found.cover = await _download_cover(client, found.cover)
        return found
    finally:
        if owned:
            await client.aclose()


def _words(text: str) -> set[str]:
    return {word for word in slugify(text, separator=" ").split() if len(word) > 2}


def _same_text(typed: str, found: str) -> bool:
    typed_slug, found_slug = slugify(typed), slugify(found)
    return bool(typed_slug and found_slug) and (
        typed_slug in found_slug or found_slug in typed_slug
    )


def _placeholder_title(title: str) -> bool:
    title = title.strip()
    return not title or title.isdigit() or bool(FILENAME_LIKE.search(title))


def _placeholder_author(author: str) -> bool:
    return author.strip().casefold() in ("", UNKNOWN_AUTHOR)


def merge(title: str, author: str, found: BookInfo) -> BookInfo | None:
    title_matches = _placeholder_title(title) or _same_text(title, found.title)
    author_matches = _placeholder_author(author) or bool(_words(author) & _words(found.author))
    if not title_matches and not author_matches:
        return None
    return BookInfo(
        title=found.title if title_matches else title.strip(),
        author=found.author if author_matches and found.author else author.strip(),
        cover=found.cover,
    )
