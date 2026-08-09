import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings

GUTENDEX_URL = "https://gutendex.com/books"
WOLNELEKTURY_CATALOG_URL = "https://wolnelektury.pl/api/books/"
WOLNELEKTURY_BOOK_URL = "https://wolnelektury.pl/api/books/{slug}/"
WOLNELEKTURY_MEDIA_URL = "https://wolnelektury.pl/media/"

ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "wolnelektury.pl",
    "www.wolnelektury.pl",
    "gutenberg.org",
    "www.gutenberg.org",
})

GUTENBERG_FORMATS = {
    "application/epub+zip": "epub",
    "application/x-mobipocket-ebook": "mobi",
    "text/html": "html",
    "text/plain": "txt",
}

WOLNELEKTURY_FORMATS = ("epub", "mobi", "pdf", "fb2", "html", "txt")

HTTP_TIMEOUT = 20.0
MAX_REDIRECTS = 5

_catalog: list[dict] = []
_catalog_fetched_at = 0.0


@dataclass
class SearchResult:
    source: str
    ref: str
    title: str
    author: str
    cover_url: str | None
    formats: dict[str, str]


def host_allowed(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ALLOWED_DOWNLOAD_HOSTS


def _absolute_media_url(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value
    return WOLNELEKTURY_MEDIA_URL + value.lstrip("/")


def _gutenberg_formats(raw: dict[str, str]) -> dict[str, str]:
    formats: dict[str, str] = {}
    for mime, url in raw.items():
        name = GUTENBERG_FORMATS.get(mime.split(";")[0].strip())
        if name and name not in formats and not url.endswith(".zip"):
            formats[name] = url
    return formats


def _gutenberg_result(book: dict) -> SearchResult | None:
    formats = _gutenberg_formats(book.get("formats", {}))
    if not formats:
        return None
    authors = ", ".join(a["name"] for a in book.get("authors", []))
    return SearchResult(
        source="gutenberg",
        ref=str(book["id"]),
        title=book.get("title", ""),
        author=authors or "Unknown Author",
        cover_url=book.get("formats", {}).get("image/jpeg"),
        formats=formats,
    )


async def _search_gutenberg(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[SearchResult]:
    response = await client.get(GUTENDEX_URL, params={"search": query})
    response.raise_for_status()
    books = response.json().get("results", [])[:limit]
    return [result for book in books if (result := _gutenberg_result(book))]


async def _wolnelektury_catalog(client: httpx.AsyncClient) -> list[dict]:
    global _catalog, _catalog_fetched_at

    if _catalog and time.monotonic() - _catalog_fetched_at < settings.catalog_ttl_minutes * 60:
        return _catalog

    response = await client.get(WOLNELEKTURY_CATALOG_URL)
    response.raise_for_status()
    _catalog = [
        {
            "slug": book["slug"],
            "title": book.get("title", ""),
            "author": book.get("author", ""),
            "cover": book.get("simple_thumb") or _absolute_media_url(book.get("cover_thumb")),
        }
        for book in response.json()
        if book.get("slug")
    ]
    _catalog_fetched_at = time.monotonic()
    return _catalog


async def _wolnelektury_detail(client: httpx.AsyncClient, book: dict) -> SearchResult | None:
    response = await client.get(WOLNELEKTURY_BOOK_URL.format(slug=book["slug"]))
    response.raise_for_status()
    data = response.json()
    formats = {name: data[name] for name in WOLNELEKTURY_FORMATS if data.get(name)}
    if not formats:
        return None
    return SearchResult(
        source="wolnelektury",
        ref=book["slug"],
        title=book.get("title") or data.get("title", ""),
        author=book.get("author") or "Unknown Author",
        cover_url=book.get("cover") or _absolute_media_url(data.get("cover_thumb")),
        formats=formats,
    )


async def _search_wolnelektury(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[SearchResult]:
    catalog = await _wolnelektury_catalog(client)
    needle = query.casefold()
    matches = [
        book
        for book in catalog
        if needle in book["title"].casefold() or needle in book["author"].casefold()
    ][:limit]

    details = await asyncio.gather(
        *(_wolnelektury_detail(client, book) for book in matches), return_exceptions=True
    )
    return [item for item in details if isinstance(item, SearchResult)]


async def search(query: str, limit: int | None = None) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []

    limit = limit or settings.search_limit
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        groups = await asyncio.gather(
            _search_wolnelektury(client, query, limit),
            _search_gutenberg(client, query, limit),
            return_exceptions=True,
        )

    results: list[SearchResult] = []
    for group in groups:
        if isinstance(group, list):
            results.extend(group)
    return results


async def resolve(source: str, ref: str, book_format: str) -> tuple[str, str, str]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        if source == "wolnelektury":
            response = await client.get(WOLNELEKTURY_BOOK_URL.format(slug=ref))
            response.raise_for_status()
            data = response.json()
            formats = {name: data[name] for name in WOLNELEKTURY_FORMATS if data.get(name)}
        elif source == "gutenberg":
            response = await client.get(f"{GUTENDEX_URL}/{ref}")
            response.raise_for_status()
            data = response.json()
            formats = _gutenberg_formats(data.get("formats", {}))
        else:
            raise ValueError("Unknown source.")

    url = formats.get(book_format)
    if not url:
        raise ValueError(f"Format '{book_format}' is not available for this book.")

    authors = ", ".join(a["name"] for a in data.get("authors", []) if a.get("name"))
    return url, data.get("title") or ref, authors


async def download(url: str, destination: Path, max_bytes: int | None = None) -> None:
    if not host_allowed(url):
        raise ValueError("Download blocked: host is not on the allowlist.")

    limit = max_bytes or settings.max_attachment_bytes
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS):
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.next_request.url)
                    if not host_allowed(url):
                        raise ValueError("Download blocked: redirect leaves the allowlist.")
                    continue

                response.raise_for_status()
                size = 0
                too_large = False
                with destination.open("wb") as out:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            too_large = True
                            break
                        out.write(chunk)

                if too_large:
                    destination.unlink(missing_ok=True)
                    raise ValueError(f"Download exceeds the {settings.max_attachment_mb}MB limit.")
                return

    raise ValueError("Download blocked: too many redirects.")
