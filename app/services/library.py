import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services import z_lib

GUTENDEX_URL = "https://gutendex.com/books"
WOLNELEKTURY_CATALOG_URL = "https://wolnelektury.pl/api/books/"
WOLNELEKTURY_BOOK_URL = "https://wolnelektury.pl/api/books/{slug}/"
WOLNELEKTURY_MEDIA_URL = "https://wolnelektury.pl/media/"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
STANDARDEBOOKS_DOWNLOAD_URL = "https://standardebooks.org/ebooks/{path}/downloads/"
STANDARDEBOOKS_DESCRIPTION_PREFIX = "Epub source for the Standard Ebooks edition of "
STANDARDEBOOKS_NON_BOOK_REPOS = frozenset({"tools", "web", "manual"})
STANDARDEBOOKS_SOURCE_PARAM = "?source=feed"

REJECTED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        "wolnelektury.pl",
        "www.wolnelektury.pl",
        "gutenberg.org",
        "www.gutenberg.org",
        "standardebooks.org",
        "www.standardebooks.org",
    }
)

LANGUAGES = {
    "": "All",
    "pl": "Polski",
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "ru": "Русский",
}

SOURCE_LANGUAGE = {"wolnelektury": "pl", "standardebooks": "en"}

SOURCES = {
    "": "All",
    "gutenberg": "Gutenberg",
    "wolnelektury": "Wolne Lektury",
    "standardebooks": "Standard Ebooks",
    "zlib": "Z-Lib",
}

GUTENBERG_FORMATS = {
    "application/epub+zip": "epub",
    "application/x-mobipocket-ebook": "mobi",
    "text/html": "html",
    "text/plain": "txt",
}

WOLNELEKTURY_FORMATS = ("epub", "mobi", "pdf", "fb2", "html", "txt")

HTTP_TIMEOUT = 20.0
MAX_REDIRECTS = 5
SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

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
    language: str = ""


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
        language=(book.get("languages") or [""])[0],
    )


async def _search_gutenberg(
    client: httpx.AsyncClient, query: str, limit: int, language: str = ""
) -> list[SearchResult]:
    params = {"search": query}
    if language:
        params["languages"] = language
    response = await client.get(GUTENDEX_URL, params=params)
    response.raise_for_status()
    books = response.json().get("results", [])[:limit]
    return [result for book in books if (result := _gutenberg_result(book))]


def _standardebooks_title_author(description: str, parts: list[str]) -> tuple[str, str]:
    text = description.removeprefix(STANDARDEBOOKS_DESCRIPTION_PREFIX)
    title, _, author = text.partition(", by ")
    author = author.split(" and translated by ")[0].strip()
    if not title:
        title = parts[1].replace("-", " ").title()
    if not author:
        author = parts[0].replace("-", " ").title()
    return title.strip(), author


def _standardebooks_formats(name: str, parts: list[str]) -> dict[str, str]:
    base = STANDARDEBOOKS_DOWNLOAD_URL.format(path="/".join(parts))
    suffix = STANDARDEBOOKS_SOURCE_PARAM
    return {
        "epub": f"{base}{name}.epub{suffix}",
        "azw3": f"{base}{name}.azw3{suffix}",
    }


def _standardebooks_result(repo: dict) -> SearchResult | None:
    name = repo.get("name", "")
    parts = name.split("_")
    if name in STANDARDEBOOKS_NON_BOOK_REPOS or len(parts) < 2:
        return None

    base = STANDARDEBOOKS_DOWNLOAD_URL.format(path="/".join(parts))
    title, author = _standardebooks_title_author(repo.get("description") or "", parts)
    return SearchResult(
        source="standardebooks",
        ref=name,
        title=title,
        author=author,
        cover_url=f"{base}cover.jpg",
        formats=_standardebooks_formats(name, parts),
        language="en",
    )


async def _search_standardebooks(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[SearchResult]:
    response = await client.get(
        GITHUB_SEARCH_URL,
        params={"q": f"{query} org:standardebooks", "per_page": limit},
        headers={"Accept": "application/vnd.github+json"},
    )
    response.raise_for_status()
    repos = response.json().get("items", [])
    return [result for repo in repos if (result := _standardebooks_result(repo))]


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
        language="pl",
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


async def _search_zlib(query: str, limit: int) -> list[SearchResult]:
    books = await z_lib.search(query, limit)
    return [
        SearchResult(
            source="zlib",
            ref=book["ref"],
            title=book["title"],
            author=book["author"],
            cover_url=book["cover_url"],
            formats={book["extension"]: book["extension"]},
            language=book["language"],
        )
        for book in books
    ]


def _is_enabled(name: str, source: str, language: str) -> bool:
    if source:
        return name == source
    return name != "zlib" and (not language or SOURCE_LANGUAGE.get(name, language) == language)


async def search(
    query: str, language: str = "", source: str = "", limit: int | None = None
) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []

    limit = limit or settings.search_limit
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        searches = []
        if _is_enabled("gutenberg", source, language):
            searches.append(_search_gutenberg(client, query, limit, language))
        if _is_enabled("wolnelektury", source, language):
            searches.append(_search_wolnelektury(client, query, limit))
        if _is_enabled("standardebooks", source, language):
            searches.append(_search_standardebooks(client, query, limit))
        if _is_enabled("zlib", source, language):
            searches.append(_search_zlib(query, limit))

        groups = await asyncio.gather(*searches, return_exceptions=True)

    if groups and source and isinstance(groups[0], Exception):
        raise groups[0]

    results: list[SearchResult] = []
    for group in groups:
        if isinstance(group, list):
            results.extend(group)
    return results


async def resolve(source: str, ref: str, book_format: str) -> tuple[str, str, str]:
    if not SAFE_REF.match(ref):
        raise ValueError("Invalid book reference.")

    if source == "zlib":
        return await z_lib.resolve(ref, book_format)

    if source == "standardebooks":
        parts = ref.split("_")
        if len(parts) < 2:
            raise ValueError("Invalid book reference.")
        formats = _standardebooks_formats(ref, parts)
        url = formats.get(book_format)
        if not url:
            raise ValueError(f"Format '{book_format}' is not available for this book.")
        title, author = _standardebooks_title_author("", parts)
        return url, title, author

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

                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type in REJECTED_CONTENT_TYPES:
                    raise ValueError("Download failed: the source returned a web page, not a file.")

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
                    await asyncio.to_thread(destination.unlink, missing_ok=True)
                    raise ValueError(f"Download exceeds the {settings.max_attachment_mb}MB limit.")
                return

    raise ValueError("Download blocked: too many redirects.")


async def download_zlib(url: str, destination: Path) -> None:
    await z_lib.download(url, destination, settings.max_attachment_bytes)
