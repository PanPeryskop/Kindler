import pytest

from app.services import z_lib

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".z-lib.sk\tTRUE\t/\tTRUE\t4102444800\tremix_userid\t42\n"
    ".z-lib.sk\tTRUE\t/\tTRUE\t4102444800\tremix_userkey\tsecret\n"
    ".other.org\tTRUE\t/\tTRUE\t4102444800\tforeign\tx\n"
)


def test_challenge_pages_are_detected():
    assert z_lib._is_challenge(513, "")
    assert z_lib._is_challenge(200, "<title>Verifying your browser | DiamWall</title>")
    assert z_lib._is_challenge(200, "<title>Checking your browser ...</title>")
    assert not z_lib._is_challenge(200, "<div class='z-bookcard'></div>")


def test_only_account_cookies_for_the_mirror_are_loaded(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text(COOKIES + ".z-lib.sk\tTRUE\t/\tTRUE\t4102444800\t__diamwall\t0x1\n")
    monkeypatch.setattr(z_lib.settings, "zlib_cookies_file", str(path))

    assert z_lib._load_cookies("https://z-lib.sk") == {
        "remix_userid": "42",
        "remix_userkey": "secret",
    }


def test_missing_cookie_file_yields_no_cookies(tmp_path, monkeypatch):
    monkeypatch.setattr(z_lib.settings, "zlib_cookies_file", str(tmp_path / "nope.txt"))

    assert z_lib._load_cookies("https://z-lib.sk") == {}


async def test_download_refuses_foreign_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(z_lib.settings, "zlib_mirror", "https://z-lib.sk")

    with pytest.raises(ValueError, match="mirror"):
        await z_lib.download("https://evil.example/dl/1", tmp_path / "x.epub", 1024)


def test_tor_mode_uses_onion_mirror_and_only_account_cookies(tmp_path, monkeypatch):
    path = tmp_path / "cookies.txt"
    path.write_text(COOKIES + ".z-lib.sk\tTRUE\t/\tTRUE\t4102444800\t__diamwall\t0x1\n")
    monkeypatch.setattr(z_lib.settings, "zlib_cookies_file", str(path))
    monkeypatch.setattr(z_lib.settings, "zlib_tor_proxy", "socks5://127.0.0.1:9150")

    assert z_lib._mirror().endswith(".onion")
    assert z_lib._load_cookies(z_lib._mirror()) == {
        "remix_userid": "42",
        "remix_userkey": "secret",
    }


async def test_tor_mode_reports_unreachable_proxy(monkeypatch):
    monkeypatch.setattr(z_lib.settings, "zlib_tor_proxy", "socks5://127.0.0.1:1")

    http = z_lib._new_http({}, z_lib.DEFAULT_USER_AGENT)
    try:
        with pytest.raises(RuntimeError, match="Is Tor running"):
            await z_lib._get_text(http, f"{z_lib._mirror()}/")
    finally:
        await http.close()


def test_onion_covers_are_dropped():
    assert z_lib._cover_url("http://example.onion", "/covers/1.jpg") is None
    assert z_lib._cover_url("https://z-lib.sk", "/covers/1.jpg") == (
        "https://z-lib.sk/covers/1.jpg"
    )
    assert z_lib._cover_url("http://example.onion", "https://cdn.example.com/1.jpg") == (
        "https://cdn.example.com/1.jpg"
    )


def test_book_code_comes_from_the_book_url():
    assert z_lib._book_code("https://z-lib.sk/book/Gz31nyAV5E/dune.html") == "Gz31nyAV5E"
    assert z_lib._book_code("https://z-lib.sk/s/dune") == ""


def test_book_page_ignores_hidden_decoy_links():
    html = """
    <h1 class="book-title">Dune</h1>
    <i class="authors"><a href="/author/x">Frank Herbert</a></i>
    <template><a href="/dl/decoy" hidden></a></template>
    <a class="btn dlButton addDownloadedBook" href="/dl/decoy2" hidden></a>
    <a class="btn btn-default dlButton addDownloadedBook" href="/dl/real">
      <span class="book-property__extension">EPUB</span>, 1.85 MB</a>
    """

    assert z_lib._parse_book_page(html) == ("/dl/real", "epub", "Dune", "Frank Herbert")


def test_book_page_without_download_button():
    href, extension, _, author = z_lib._parse_book_page("<h1 class='book-title'>X</h1>")

    assert (href, extension, author) == (None, "", "Unknown Author")


async def test_fetch_solves_the_check_again_when_it_returns(monkeypatch):
    pages = iter([(513, "<title>DiamWall</title>"), (200, "<div>ok</div>")])
    opened = []

    class Http:
        async def close(self):
            pass

    async def open_session():
        opened.append(1)
        return z_lib._ZSession(http=Http(), mirror="https://z-lib.sk")

    async def get_text(http, url):
        return next(pages)

    monkeypatch.setattr(z_lib, "_session", None)
    monkeypatch.setattr(z_lib, "_open_session", open_session)
    monkeypatch.setattr(z_lib, "_get_text", get_text)

    assert await z_lib._fetch("https://z-lib.sk/s/x") == "<div>ok</div>"
    assert len(opened) == 2


async def test_keep_warm_is_idle_without_tor(monkeypatch):
    monkeypatch.setattr(z_lib.settings, "zlib_tor_proxy", None)

    await z_lib.keep_warm()


def search_page(cards: int, pages_total: int, start: int = 0) -> str:
    items = "".join(
        f"""
        <div class="book-item"><z-bookcard href="/book/code{start + i}/slug"
          extension="epub" language="English">
          <img data-src="/covers/{start + i}.jpg">
          <div slot="title">Book {start + i}</div>
          <div slot="author">Ann Author;Bob Writer</div>
        </z-bookcard></div>"""
        for i in range(cards)
    )
    return (
        f'<div id="searchResultBox">{items}</div>'
        f"<script>var pagerOptions = {{pagesTotal: {pages_total}, x: 1}}</script>"
    )


def test_search_page_is_parsed():
    books, total = z_lib._parse_search_page(search_page(2, 7), "https://z-lib.sk")

    assert total == 7
    assert books[0] == {
        "ref": "code0",
        "title": "Book 0",
        "author": "Ann Author, Bob Writer",
        "cover_url": "https://z-lib.sk/covers/0.jpg",
        "extension": "epub",
        "language": "English",
    }


def test_empty_search_page_has_no_books():
    html = '<div id="searchResultBox"><div class="notFound">Nothing</div></div>'

    assert z_lib._parse_search_page(html, "https://z-lib.sk") == ([], 0)


def test_unexpected_search_page_is_an_error():
    with pytest.raises(RuntimeError, match="unexpected"):
        z_lib._parse_search_page("<html>login</html>", "https://z-lib.sk")


def test_search_url_carries_language_format_and_page(monkeypatch):
    monkeypatch.setattr(z_lib.settings, "zlib_tor_proxy", None)
    monkeypatch.setattr(z_lib.settings, "zlib_mirror", "https://z-lib.sk")

    url = z_lib._search_url("dune/messiah", "pl", "epub", 3)

    assert url == (
        "https://z-lib.sk/s/dune%2Fmessiah?languages%5B%5D=polish&extensions%5B%5D=EPUB&page=3"
    )


def fake_site(monkeypatch, pages: dict[int, str]) -> list[str]:
    fetched: list[str] = []

    async def fetch(url):
        fetched.append(url)
        return pages[int(url.rsplit("page=", 1)[1])]

    monkeypatch.setattr(z_lib.settings, "zlib_tor_proxy", None)
    monkeypatch.setattr(z_lib, "_fetch", fetch)
    monkeypatch.setattr(z_lib, "_pages", z_lib.OrderedDict())
    return fetched


async def test_search_pages_through_results_with_a_cursor(monkeypatch):
    fetched = fake_site(monkeypatch, {1: search_page(4, 2), 2: search_page(4, 2, start=4)})

    first, cursor = await z_lib.search("dune", 3)
    second, cursor = await z_lib.search("dune", 3, cursor=cursor)
    third, cursor = await z_lib.search("dune", 3, cursor=cursor)

    assert [b["ref"] for b in first] == ["code0", "code1", "code2"]
    assert [b["ref"] for b in second] == ["code3", "code4", "code5"]
    assert [b["ref"] for b in third] == ["code6", "code7"]
    assert cursor is None
    assert len(fetched) == 2


async def test_last_full_window_has_no_next_cursor(monkeypatch):
    fake_site(monkeypatch, {1: search_page(3, 1)})

    books, cursor = await z_lib.search("dune", 3)

    assert len(books) == 3
    assert cursor is None


async def test_search_drops_books_outside_the_filters(monkeypatch):
    html = search_page(2, 1).replace('language="English"', 'language="Polish"', 1)
    fake_site(monkeypatch, {1: html})

    books, _ = await z_lib.search("dune", 5, language="pl", extension="epub")

    assert [book["ref"] for book in books] == ["code0"]


async def test_search_skips_pages_emptied_by_the_filters(monkeypatch):
    pdf_only = search_page(3, 3).replace('extension="epub"', 'extension="pdf"')
    fake_site(monkeypatch, {1: pdf_only, 2: pdf_only, 3: search_page(2, 3, start=10)})

    books, cursor = await z_lib.search("dune", 5, extension="epub")

    assert [book["ref"] for book in books] == ["code10", "code11"]
    assert cursor is None


async def test_search_stops_after_a_few_empty_pages(monkeypatch):
    pdf_only = search_page(3, 9).replace('extension="epub"', 'extension="pdf"')
    fetched = fake_site(monkeypatch, dict.fromkeys(range(1, 10), pdf_only))

    books, cursor = await z_lib.search("dune", 5, extension="epub")

    assert books == []
    assert cursor == (4, 0)
    assert len(fetched) == z_lib.MAX_PAGES_PER_SEARCH
