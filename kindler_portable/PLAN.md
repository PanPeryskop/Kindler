# Kindler Portable - plan

## Brief

Phone-first Flet app (Android first) that does what the web app does, running fully on the
device with no server: search catalogues and Z-Library (several accounts, quick switch),
review title, author and cover, queue, send to Kindle, sent history, settings. No Calibre.
Polished: animated, smooth transitions, haptics, neo-brutalist look of the web app but better.
Portrait only. English code, no comments, ruff clean, tests for the shared core.

## Decisions (verified on 2026-11-21)

- Flet 1.1.2 runs Python on the phone, so the existing service code is reused, not rewritten.
- pypi.flet.dev has Android wheels for every native dependency we use: pydantic-core,
  greenlet, lxml, pymupdf, curl-cffi, pillow. Playwright is the only one missing.
- Z-Library ships in v1. Measured against z-lib.sk:
  - DiamWall checks the TLS fingerprint: a curl_cffi session passes, httpx with the same
    cookies and headers is blocked. curl_cffi exists for Android.
  - The JS-visible cookies (`__diamwall`, `bsrv`, `c_token`, `siteLanguage`) are enough;
    the HttpOnly `__Host-dw_session_v2` is issued again by the server on the first request.
  - So on the phone the browser check is passed once in a Flet WebView shown in a sheet
    ("Verifying Z-Library"), cookies are read with `run_javascript` + `on_console_message`,
    the WebView user agent is read with `get_user_agent`, and from then on the existing
    curl_cffi code does everything. On desktop the existing Playwright solver stays.
  - Not testable here (Flet WebView runs only on Android, iOS and macOS). If the check
    never passes on a device, the sheet says so and the rest of the app keeps working.
- Sending: system share sheet to the Kindle app by default (no Gmail, no whitelist).
  SMTP stays optional in Settings.
- Formats: only what Send to Kindle accepts (EPUB, PDF, DOC, DOCX, TXT, RTF, HTML),
  EPUB preferred.
- Portrait lock: `page.set_allowed_device_orientations([ft.DeviceOrientation.PORTRAIT_UP])`
  on mobile platforms.

## Architecture

- `kindler/` at the repo root: framework-free core shared by both apps.
  `settings` (plain mutable dataclass configured at startup), `db` (engine opened at runtime),
  `queue`, `preferences`, `accounts`, `library`, `z_lib` (pluggable challenge solver),
  `metadata`, `covers`, `lookup`, `detect`, `sender`.
- `app/` (FastAPI web app) imports the core; Calibre `convert` stays in `app/`.
- `kindler_portable/src/` Flet app. `scripts/sync_core.py` copies `kindler/` into
  `kindler_portable/src/kindler` (git-ignored) before `flet build apk`; local runs use the
  repo root on `PYTHONPATH`.

## Screens

- Search: query, source chips, language and format filters, results with covers,
  "Sent" badges, multi-select, Z-Lib account switch, load more.
- Queue: cards with status, review sheet (title, author, cover lookup and upload), send one
  or send all, retry, remove.
- History: sent books, swipe to remove.
- Settings: default Kindle address, sending method (share or SMTP), Z-Lib accounts and
  mirror, reminder toggle, theme.

## Phases

1. Done: `kindler/` extracted from `app/`; the web app and its tests stay green.
2. Done: Flet shell with theme (Space Grotesk, JetBrains Mono, light and dark), navigation
   bar, transitions, haptics, portrait lock.
3. Done: search, results, "Sent" badges and multi-select.
4. Done: queue, review sheet, share and SMTP sending, file import, history.
5. Done: settings and Z-Lib accounts; WebView challenge sheet.
6. In progress: polish and `flet build apk`.

## Still to verify on a real phone

- The WebView passes the DiamWall check and the cookies work from `curl_cffi`.
- The share sheet hands EPUB files to the Kindle app and returns a status.
- Haptics and the portrait lock.
