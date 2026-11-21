# Kindler Portable

Phone-first Flet app that finds books and sends them to a Kindle, running fully on the
device. It reuses the shared `kindler` core from the repository root, so search, Z-Library,
metadata, covers and history behave exactly like the web app. There is no Calibre: only
formats Kindle reads natively are offered (EPUB, PDF, DOCX, DOC, RTF, TXT, HTML).

## Features

- Search Gutenberg, Wolne Lektury, Standard Ebooks and Z-Library, with language and
  format filters, "Sent" badges and multi-select.
- Queue with covers, editable title, author and cover, online metadata lookup, send one or
  send all, retry and swipe to remove. Books can also be added from the phone's storage.
- Sending through the system share sheet to the Kindle app (no setup), or by e-mail to a
  Kindle address with your own SMTP account.
- Sent history, several Z-Library accounts with a quick switch, custom mirror, light and
  dark themes, haptics, portrait only.

## Z-Library on a phone

Z-Library sits behind a DiamWall browser check that also verifies the TLS fingerprint.
The app opens the mirror once in an in-app WebView sheet, reads the check cookies and the
WebView user agent, and then talks to Z-Library with `curl_cffi`, which ships for Android.
On desktop the check is passed in headless Chromium through Playwright.

## Run locally

```bash
uv sync --project kindler_portable
```

```bash
uv run --project kindler_portable python kindler_portable/dev.py
```

The app is served on <http://localhost:8550> as a web page. Data goes to
`~/.kindler-portable-dev`. Sharing to the Kindle app works only on a phone.

## Build an APK

```bash
uv run --project kindler_portable python kindler_portable/sync_core.py
```

```bash
cd kindler_portable && uv run flet build apk --arch arm64-v8a
```

`sync_core.py` copies `kindler/` into `src/kindler` (git-ignored) so it is packaged with the
app. The first build downloads Flutter and JDK 17. The APK lands in `build/apk`.

## Tests

```bash
cd kindler_portable && uv run pytest
```
