# Kindler

Send documents and books straight to your Kindle library, from a simple FastAPI web app.

Upload a file or pick a book from a catalogue, Kindler converts it if needed and emails it to your Send-to-Kindle address using your own Gmail account.

## Features

- EPUB and PDF are sent as-is (PDFs are converted by Amazon)
- Other formats (MOBI, AZW3, FB2, DOCX, HTML, TXT, images) are converted to EPUB via Calibre
- Search Project Gutenberg, Wolne Lektury, Standard Ebooks and Z-Library
- Edit title and author before sending, download the file or remove it from the queue
- Background job status (pending → converting → sending → sent)
- Simple HTMX-based UI, no JavaScript build step

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Calibre](https://calibre-ebook.com/) installed system-wide (provides `ebook-convert`)
- A Gmail account with 2-Step Verification enabled
- A Kindle device or app with Send-to-Kindle enabled

## Setup

### 1. Install dependencies

```bash
uv sync
uv run playwright install chromium
```

The second command downloads the headless Chromium that the Z-Lib source uses
to pass Z-Library's browser check (one-time, ~150 MB). Skip it if you don't use
Z-Lib.

### 2. Create a Gmail App Password

Kindler sends email over SMTP, so it needs a Gmail **App Password** (not your regular password).

1. Enable 2-Step Verification: `myaccount.google.com/security`
2. Generate an App Password: `myaccount.google.com/apppasswords`
3. Copy the 16-character code - you'll need it in the next step


### 3. Configure environment variables

Create a `.env` file in the project root:

```dotenv
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your_address@gmail.com
EMAIL_HOST_PASSWORD=your16charapppassword

KINDLE_ADDRESS=your_kindle_address@kindle.com
MAX_ATTACHMENT_MB=18

# Optional: Z-Lib source (a logged-in cookies.txt in the project root also works)
ZLIB_EMAIL=your_zlibrary_email
ZLIB_PASSWORD=your_zlibrary_password
ZLIB_MIRROR=https://z-lib.sk
# ZLIB_COOKIES_FILE=cookies.txt
# Route Z-Lib through Tor (onion site); 9150 = Tor Browser, 9050 = tor service
# ZLIB_TOR_PROXY=socks5://127.0.0.1:9150
```

### 4. Running

Run in terminal:

```
uv run uvicorn app.main:app --reload
```

### Z-Lib

The `Z-Lib` source searches and downloads from Z-Library with your account. It
reuses `remix_userid` / `remix_userkey` from `cookies.txt` (Netscape format,
exported from a logged-in browser) when present; otherwise it logs in with
`ZLIB_EMAIL` / `ZLIB_PASSWORD`. `cookies.txt` holds your session, so it is
git-ignored.

The clearnet mirror sits behind a DiamWall browser check. When it shows up, the
app opens the mirror in a headless Chromium (Playwright, no window), waits a few
seconds for the check to pass, and takes over the browser's cookies and user
agent; everything after that is plain HTTP through `curl_cffi` (`aiohttp` gets
rejected even with the right cookies). If the check comes back later - its
cookie only lasts a few minutes without traffic - the browser runs again
automatically. Install the browser once:

```
uv run playwright install chromium
```

If the check still cannot be passed, searches fail with a message saying so.
Try another mirror via `ZLIB_MIRROR`, or switch to Tor. The public catalogues
keep working either way.

#### Through Tor

Set `ZLIB_TOR_PROXY` (e.g. `socks5://127.0.0.1:9150`) and keep Tor running:
either Tor Browser open in the background (port 9150) or the `tor` service
(port 9050). Z-Lib then uses the onion site, which does not sit behind the
clearnet browser check. Only the account cookies (`remix_userid`,
`remix_userkey`) are taken from `cookies.txt` in this mode; without them the
app logs in with `ZLIB_EMAIL` / `ZLIB_PASSWORD`. The server opens the Tor
session in the background at startup and refreshes it every ~25 minutes, so
only a search made right after startup waits for the onion circuit. Covers are
hidden in this mode (your browser cannot load onion-hosted images), and
downloads are slower than on clearnet.