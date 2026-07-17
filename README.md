# Kindler

Send documents and articles straight to your Kindle library, from a simple FastAPI web app.

Upload a file (or paste a URL), Kindler converts it if needed and emails it to your Send-to-Kindle address using your own Gmail account.

## Features

- Upload PDF, EPUB, DOCX, HTML, TXT, or images - sent as-is to Kindle
- Paste an article URL - extracted and converted to a clean EPUB
- Automatic conversion for legacy formats (MOBI, AZW3, FB2) via Calibre
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
```

### 2. Create a Gmail App Password

Kindler sends email over SMTP, so it needs a Gmail **App Password** (not your regular password).

1. Enable 2-Step Verification: `myaccount.google.com/security`
2. Generate an App Password: `myaccount.google.com/apppasswords`
3. Copy the 16-character code — you'll need it in the next step


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
```

### 4. Running

Run in terminal:

```
uv run uvicorn app.main:app --reload
```