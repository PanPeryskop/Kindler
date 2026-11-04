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
        return z_lib._ZSession(http=Http(), mirror="https://z-lib.sk", client=None)

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
