from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import zlibrary
import zlibrary.abs as zlib_abs
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException
from zlibrary.libasync import ZLIB_TOR_DOMAIN
from zlibrary.profile import ZlibProfile

from app.config import settings

ZLIBRARY_TIMEOUT = 60
TOR_TIMEOUT = 120
CHALLENGE_TIMEOUT_SECONDS = 45
KEEP_WARM_SECONDS = 1500
KEEP_WARM_RETRY_SECONDS = 60
SESSION_COOKIES = ("remix_userid", "remix_userkey")
CHALLENGE_STATUSES = {403, 503, 513}
CHALLENGE_MARKERS = ("diamwall", "checking your browser", "verifying your browser")
NAVIGATION_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}
XHR_HEADERS = {"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def _discard(*_args: Any, **_kwargs: Any) -> Any:
    return open(os.devnull, "w", encoding="utf-8")


zlib_abs.open = _discard  # type: ignore[attr-defined]

_session: _ZSession | None = None
_session_lock = asyncio.Lock()
_retiring: set[asyncio.Task] = set()
logger = logging.getLogger(__name__)


class ZLibraryBlocked(RuntimeError):
    pass


@dataclass
class _ZSession:
    http: AsyncSession
    mirror: str
    client: Any


def _use_tor() -> bool:
    return bool(settings.zlib_tor_proxy)


def _timeout() -> int:
    return TOR_TIMEOUT if _use_tor() else ZLIBRARY_TIMEOUT


def _mirror() -> str:
    return ZLIB_TOR_DOMAIN if _use_tor() else settings.zlib_mirror.rstrip("/")


def _proxy() -> str | None:
    proxy = settings.zlib_tor_proxy
    if proxy and proxy.startswith("socks5://"):
        proxy = "socks5h://" + proxy.removeprefix("socks5://")
    return proxy


def _load_cookies(mirror: str) -> dict[str, str]:
    path = Path(settings.zlib_cookies_file)
    if not path.is_file():
        return {}

    jar = MozillaCookieJar(str(path))
    try:
        jar.load(ignore_discard=True)
    except (LoadError, OSError) as exc:
        raise RuntimeError(f"Could not read {path.name}: {exc}") from exc

    host = urlparse(mirror).hostname or ""
    return {
        cookie.name: cookie.value or ""
        for cookie in jar
        if cookie.name in SESSION_COOKIES
        and (_use_tor() or host.endswith(cookie.domain.lstrip(".")))
    }


def _is_challenge(status: int, body: str) -> bool:
    head = body[:4000].lower()
    return status in CHALLENGE_STATUSES or any(marker in head for marker in CHALLENGE_MARKERS)


def _blocked() -> ZLibraryBlocked:
    if _use_tor():
        return ZLibraryBlocked(
            "Z-Library's onion site answered with a browser check instead of content."
        )
    return ZLibraryBlocked(
        "Z-Library's browser check could not be passed. "
        "Try another mirror via ZLIB_MIRROR, or set ZLIB_TOR_PROXY to go through Tor."
    )


def _solve_challenge_sync(mirror: str) -> tuple[dict[str, str], str]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import ProxySettings, sync_playwright

    proxy = ProxySettings(server=settings.zlib_tor_proxy) if settings.zlib_tor_proxy else None
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                proxy=proxy,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as exc:
            raise RuntimeError(
                "Z-Lib needs Playwright's Chromium to pass the browser check. "
                "Run: uv run playwright install chromium"
            ) from exc

        try:
            probe = browser.new_page()
            user_agent = probe.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
            probe.close()

            context = browser.new_context(user_agent=user_agent, locale="en-US")
            page = context.new_page()
            page.goto(f"{mirror}/", wait_until="domcontentloaded", timeout=_timeout() * 1000)

            deadline = time.monotonic() + CHALLENGE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                page.wait_for_timeout(1000)
                try:
                    body = page.content()
                except PlaywrightError:
                    continue
                if not _is_challenge(200, body):
                    cookies = {c.get("name", ""): c.get("value", "") for c in context.cookies()}
                    return cookies, user_agent
        finally:
            browser.close()

    raise _blocked()


async def _solve_challenge(mirror: str) -> tuple[dict[str, str], str]:
    logger.info("Z-Lib: solving the browser check in headless Chromium")
    return await asyncio.to_thread(_solve_challenge_sync, mirror)


def _new_http(cookies: dict[str, str], user_agent: str) -> AsyncSession:
    return AsyncSession(
        headers={"User-Agent": user_agent, **NAVIGATION_HEADERS},
        cookies=cookies,
        proxy=_proxy(),
        timeout=_timeout(),
        allow_redirects=True,
    )


async def _get_text(http: AsyncSession, url: str) -> tuple[int, str]:
    try:
        response = await http.get(url)
    except RequestException as exc:
        if not _use_tor():
            raise
        raise RuntimeError(
            f"Cannot reach Z-Library through Tor at {settings.zlib_tor_proxy}. Is Tor running?"
        ) from exc
    return response.status_code, response.text


async def _login(http: AsyncSession, mirror: str) -> None:
    if not settings.zlib_email or not settings.zlib_password:
        raise RuntimeError("Z-Lib needs a logged-in cookies.txt or ZLIB_EMAIL and ZLIB_PASSWORD.")

    response = await http.post(
        f"{mirror}/rpc.php",
        data={
            "isModal": "true",
            "email": settings.zlib_email,
            "password": settings.zlib_password,
            "site_mode": "books",
            "action": "login",
            "isSingleLogin": "1",
            "redirectUrl": "",
            "gg_json_mode": "1",
        },
        headers=XHR_HEADERS,
    )
    if _is_challenge(response.status_code, response.text):
        raise _blocked()
    try:
        result = json.loads(response.text).get("response", {})
    except json.JSONDecodeError as exc:
        raise RuntimeError("Z-Lib login returned an unexpected response.") from exc
    if result.get("validationError") or not all(http.cookies.get(name) for name in SESSION_COOKIES):
        raise RuntimeError("Z-Lib login failed: check ZLIB_EMAIL and ZLIB_PASSWORD.")


def _new_client(http: AsyncSession, mirror: str) -> Any:
    async def fetch(url: str) -> str:
        return await _fetch(url)

    client = zlibrary.AsyncZlib()
    client.mirror = mirror
    client.cookies = dict(http.cookies.items())
    client._r = fetch
    client.profile = ZlibProfile(fetch, client.cookies, mirror, mirror)
    return client


async def _open_session() -> _ZSession:
    mirror = _mirror()
    cookies = _load_cookies(mirror)
    http = _new_http(cookies, DEFAULT_USER_AGENT)
    try:
        status, body = await _get_text(http, f"{mirror}/")
        if _is_challenge(status, body):
            await http.close()
            solved, user_agent = await _solve_challenge(mirror)
            http = _new_http({**cookies, **solved}, user_agent)
            status, body = await _get_text(http, f"{mirror}/")
            if _is_challenge(status, body):
                raise _blocked()

        if not all(http.cookies.get(name) for name in SESSION_COOKIES):
            await _login(http, mirror)
    except BaseException:
        await http.close()
        raise

    return _ZSession(http=http, mirror=mirror, client=_new_client(http, mirror))


async def _get_session() -> _ZSession:
    global _session
    async with _session_lock:
        if _session is None:
            _session = await _open_session()
        return _session


async def _reset_session(stale: _ZSession) -> None:
    global _session
    async with _session_lock:
        if _session is stale:
            _session = None
            await stale.http.close()


async def _fetch(url: str) -> str:
    for attempt in range(2):
        session = await _get_session()
        status, body = await _get_text(session.http, url)
        if not _is_challenge(status, body):
            return body
        await _reset_session(session)
        if attempt:
            raise _blocked()
    raise AssertionError("unreachable")


def _retire(session: _ZSession) -> None:
    async def close_when_idle() -> None:
        await asyncio.sleep(_timeout() * 5)
        await session.http.close()

    task = asyncio.create_task(close_when_idle())
    _retiring.add(task)
    task.add_done_callback(_retiring.discard)


async def keep_warm() -> None:
    global _session
    if not _use_tor():
        return

    while True:
        try:
            session = await _open_session()
        except Exception as exc:
            logger.warning("Z-Lib Tor warm-up failed: %s", exc)
            delay = KEEP_WARM_RETRY_SECONDS
        else:
            async with _session_lock:
                old, _session = _session, session
            if old is not None:
                _retire(old)
            delay = KEEP_WARM_SECONDS
        await asyncio.sleep(delay)


def _author(book: dict[str, Any]) -> str:
    authors = book.get("authors", [])
    if isinstance(authors, list):
        names = [
            item.get("author", "") if isinstance(item, dict) else str(item) for item in authors
        ]
        return ", ".join(name for name in names if name) or "Unknown Author"
    return str(authors or "Unknown Author")


def _extension(book: dict[str, Any]) -> str:
    return str(book.get("extension", "")).strip().lower().lstrip(".")


def _book_code(url: str) -> str:
    parts = urlparse(url).path.strip("/").split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "book" else ""


def _cover_url(mirror: str, cover: str | None) -> str | None:
    if not cover:
        return None
    cover = urljoin(f"{mirror}/", cover)
    if (urlparse(cover).hostname or "").endswith(".onion"):
        return None
    return cover


async def search(query: str, limit: int) -> list[dict[str, Any]]:
    session = await _get_session()
    paginator = await asyncio.wait_for(
        session.client.search(q=query, count=min(max(limit, 1), 50)),
        timeout=_timeout(),
    )
    books = await asyncio.wait_for(paginator.next(), timeout=_timeout())

    results = []
    for book in books[:limit]:
        extension = _extension(book)
        code = _book_code(str(book.get("url", "")))
        if not code or not extension:
            continue
        results.append(
            {
                "ref": code,
                "title": str(book.get("name", "")).strip() or "Untitled",
                "author": _author(book),
                "cover_url": _cover_url(session.mirror, book.get("cover")),
                "extension": extension,
                "language": str(book.get("language", "")).strip(),
            }
        )
    return results


def _parse_book_page(html: str) -> tuple[str | None, str, str, str]:
    soup = BeautifulSoup(html, "lxml")
    button = soup.select_one("a.addDownloadedBook[href^='/dl/']:not([hidden])")
    href = str(button["href"]) if button else None
    extension_tag = button.select_one(".book-property__extension") if button else None
    extension = extension_tag.get_text(strip=True).lower() if extension_tag else ""

    title_tag = soup.select_one("h1.book-title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    authors = [a.get_text(strip=True) for a in soup.select("i.authors a")]
    return href, extension, title, ", ".join(authors) or "Unknown Author"


async def resolve(ref: str, book_format: str) -> tuple[str, str, str]:
    session = await _get_session()
    html = await asyncio.wait_for(_fetch(f"{session.mirror}/book/{ref}"), timeout=_timeout())
    href, extension, title, author = _parse_book_page(html)

    if not href:
        raise ValueError("This book is not currently available for download.")
    if book_format.lower().lstrip(".") != extension:
        raise ValueError(f"Format '{book_format}' is not available for this book.")
    return urljoin(f"{session.mirror}/", href), title or ref, author


async def download(url: str, destination: Path, max_bytes: int) -> None:
    if urlparse(url).hostname != urlparse(_mirror()).hostname:
        raise ValueError("Download blocked: URL does not point at the Z-Library mirror.")

    for attempt in range(2):
        session = await _get_session()
        async with session.http.stream("GET", url, timeout=_timeout() * 5) as response:
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "text/html":
                response.raise_for_status()
                await _save(response, destination, max_bytes)
                return

            body = (await response.acontent()).decode(errors="replace")
        if not _is_challenge(response.status_code, body):
            raise ValueError("Download failed: Z-Library returned a web page, not a file.")
        await _reset_session(session)
        if attempt:
            raise _blocked()


async def _save(response: Any, destination: Path, max_bytes: int) -> None:
    size = 0
    too_large = False
    with destination.open("wb") as output:
        async for chunk in response.aiter_content():
            size += len(chunk)
            if size > max_bytes:
                too_large = True
                break
            output.write(chunk)

    if too_large:
        await asyncio.to_thread(destination.unlink, missing_ok=True)
        raise ValueError(f"Download exceeds the {settings.max_attachment_mb}MB limit.")
