import asyncio
import hashlib
import html
import json
import logging
import re
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slugify import slugify

from app.accounts import activate, get_account, list_accounts, remove_account, save_account
from app.config import settings
from app.db import Job, init_db, utcnow
from app.preferences import load_preferences, preferences, save_preferences
from app.queue import ACTIVE_STATUSES, JobQueue
from app.services import library, z_lib
from app.services.convert import CalibreConverter, options_for, presets_for
from app.services.covers import data_uri, extract_cover, image_media_type
from app.services.detect import FileTypeDetector
from app.services.lookup import lookup, merge
from app.services.metadata import apply_cover, apply_metadata, probe_metadata
from app.services.sender import send_to_kindle

REVIEW_TTL = timedelta(hours=6)
NATIVE_TYPES = ("epub", "pdf")
CLEANUP_INTERVAL_SECONDS = 900
MAX_COVER_BYTES = 2 * 1024 * 1024
HISTORY_LIMIT = 100
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"

job_queue = JobQueue()
logger = logging.getLogger(__name__)


async def discard_abandoned_uploads() -> None:
    for job in await job_queue.older_than("review", REVIEW_TTL):
        if job.tmp_path:
            Path(job.tmp_path).unlink(missing_ok=True)
        await job_queue.update(job.id, "failed", "Expired before being sent.")


async def fail_interrupted_jobs() -> None:
    for job in await job_queue.with_status(*ACTIVE_STATUSES):
        if job.tmp_path:
            Path(job.tmp_path).unlink(missing_ok=True)
        await job_queue.update(job.id, "failed", "Interrupted by a server restart.")


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        with suppress(Exception):
            await discard_abandoned_uploads()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await load_preferences()
    await fail_interrupted_jobs()
    await discard_abandoned_uploads()
    tasks = [asyncio.create_task(cleanup_loop()), asyncio.create_task(z_lib.keep_warm())]
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=APP_DIR / "templates")


def static_url(path: str) -> str:
    version = int((STATIC_DIR / path).stat().st_mtime)
    return f"/static/{path}?v={version}"


templates.env.globals["static_url"] = static_url


def job_card_context(job: Job) -> dict:
    downloadable = bool(job.tmp_path and Path(job.tmp_path).exists())
    context = {
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "error": job.error,
        "title": job.title,
        "author": job.author,
        "cover": job.cover,
        "downloadable": downloadable,
        "presets": presets_for(Path(job.filename).suffix) if downloadable else {},
    }
    payload = json.dumps(context, sort_keys=True).encode()
    return {**context, "version": hashlib.blake2b(payload, digest_size=6).hexdigest()}


def render_card(request: Request, context: dict) -> str:
    return templates.get_template("partials/job_card.html").render({"request": request, **context})


def render_job_card(request: Request, job: Job) -> str:
    return render_card(request, job_card_context(job))


def title_key(title: str | None) -> str:
    return " ".join((title or "").casefold().split())


async def sent_marks(results: list[library.SearchResult]) -> dict[str, datetime]:
    if not results:
        return {}
    by_ref: dict[tuple[str | None, str | None], datetime] = {}
    by_title: dict[str, datetime] = {}
    for job in await job_queue.sent():
        sent_at = job.sent_at or job.created_at
        if job.ref:
            by_ref.setdefault((job.source, job.ref), sent_at)
        if job.title:
            by_title.setdefault(title_key(job.title), sent_at)

    marks = {}
    for result in results:
        sent_at = by_ref.get((result.source, result.ref)) or by_title.get(title_key(result.title))
        if sent_at:
            marks[f"{result.source}:{result.ref}"] = sent_at
    return marks


def inspect_file(path: Path, fallback_title: str) -> tuple[str, str, str | None]:
    file_type, _ = FileTypeDetector.detect_file_type(path)
    title, author = probe_metadata(path, file_type, fallback_title=fallback_title)
    return title, author, extract_cover(path, file_type)


async def process_file_job(job_id: str) -> None:
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path:
        return

    tmp_path = Path(job.tmp_path)
    converted_path = None
    try:
        await job_queue.update(job_id, "processing")

        file_type, is_valid = FileTypeDetector.detect_file_type(tmp_path)
        if not is_valid:
            await job_queue.update(job_id, "failed", f"Invalid file type: {file_type}")
            return

        if file_type in NATIVE_TYPES:
            outgoing, outgoing_type = tmp_path, file_type
        else:
            await job_queue.update(job_id, "converting")
            converted_path, error = await CalibreConverter.convert_to_epub(tmp_path)
            if error or not converted_path:
                await job_queue.update(job_id, "failed", f"Calibre conversion error: {error}")
                return
            outgoing, outgoing_type = converted_path, "epub"

        await asyncio.to_thread(
            apply_metadata, outgoing, outgoing_type, job.title or "", job.author or ""
        )
        await asyncio.to_thread(apply_cover, outgoing, outgoing_type, job.cover)

        await job_queue.update(job_id, "sending")
        await send_to_kindle(
            outgoing,
            job.kindle_address,
            convert=(outgoing_type == "pdf"),
            title=job.title,
            author=job.author,
        )
        await job_queue.set(job_id, status="sent", error=None, sent_at=utcnow())

    except Exception as e:
        await job_queue.update(job_id, "failed", str(e))

    finally:
        tmp_path.unlink(missing_ok=True)
        if converted_path:
            converted_path.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "default_kindle": preferences.kindle_address,
            "show_notice": preferences.show_sender_notice,
            "max_size": settings.max_attachment_mb,
            "languages": library.LANGUAGES,
            "sources": library.SOURCES,
            "formats": library.FORMATS,
            **await zlib_context(),
        },
    )


async def zlib_context() -> dict[str, object]:
    return {"accounts": await list_accounts(), "active": preferences.zlib_active}


def settings_values() -> dict[str, object]:
    return {
        "kindle_address": preferences.kindle_address,
        "show_sender_notice": preferences.show_sender_notice,
    }


def render_settings(request: Request, name: str, **context: object) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "request": request,
            "values": settings_values(),
            "default_kindle": preferences.kindle_address,
            "show_notice": preferences.show_sender_notice,
            **context,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_panel(request: Request):
    return render_settings(request, "partials/settings_body.html", **await zlib_context())


@app.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    kindle_address: str = Form(""),
    show_sender_notice: bool = Form(False),
):
    kindle_address = kindle_address.strip()
    if kindle_address and not EMAIL_PATTERN.match(kindle_address):
        values = {"kindle_address": kindle_address, "show_sender_notice": show_sender_notice}
        return render_settings(
            request,
            "partials/settings_form.html",
            values=values,
            error="The Kindle address doesn't look like an e-mail address.",
        )

    await save_preferences(kindle_address=kindle_address, show_sender_notice=show_sender_notice)
    return render_settings(request, "partials/settings_saved.html", saved=True, oob=True)


def restart_zlib(background_tasks: BackgroundTasks) -> None:
    z_lib.reset()
    background_tasks.add_task(z_lib.warm_up)


async def switch_zlib_account(email: str, background_tasks: BackgroundTasks) -> None:
    if await activate(email):
        restart_zlib(background_tasks)


async def render_zlib(request: Request, **context: object) -> HTMLResponse:
    return render_settings(
        request, "partials/zlib_update.html", oob=True, **await zlib_context(), **context
    )


@app.post("/settings/zlib/accounts", response_class=HTMLResponse)
async def add_zlib_account(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(""),
    password: str = Form(""),
):
    email = email.strip()
    if not EMAIL_PATTERN.match(email):
        error = "The Z-Lib e-mail doesn't look like an e-mail address."
        return await render_zlib(request, error=error, draft_email=email)
    if not password:
        return await render_zlib(request, error="Enter the Z-Lib password.", draft_email=email)

    await save_account(email, password)
    if email == preferences.zlib_active:
        restart_zlib(background_tasks)
    elif not preferences.zlib_active:
        await switch_zlib_account(email, background_tasks)
    return await render_zlib(request)


@app.post("/settings/zlib/active", response_class=HTMLResponse)
async def choose_zlib_account(
    request: Request, background_tasks: BackgroundTasks, email: str = Form("")
):
    if await get_account(email):
        await switch_zlib_account(email, background_tasks)
    return await render_zlib(request)


@app.delete("/settings/zlib/accounts", response_class=HTMLResponse)
async def delete_zlib_account(request: Request, background_tasks: BackgroundTasks, email: str = ""):
    await remove_account(email)
    if email == preferences.zlib_active:
        remaining = await list_accounts()
        await switch_zlib_account(remaining[0].email if remaining else "", background_tasks)
    return await render_zlib(request)


@app.post("/settings/notice/dismiss", response_class=HTMLResponse)
async def dismiss_notice(request: Request):
    await save_preferences(show_sender_notice=False)
    return render_settings(request, "partials/sender_notice.html")


@app.get("/search", response_class=HTMLResponse)
async def search_library(
    request: Request,
    q: str = "",
    lang: str = "",
    source: str = "",
    fmt: str = "",
    page: int = 0,
    offset: int = 0,
):
    lang = lang if lang in library.LANGUAGES else ""
    source = source if source in library.SOURCES else ""
    fmt = fmt if fmt in library.FORMATS else ""
    cursor = (page, max(offset, 0)) if page >= 1 else None

    next_query = None
    try:
        found = await library.search(
            q, language=lang, source=source, book_format=fmt, cursor=cursor
        )
        results, error = found.results, None
        if found.next_cursor:
            next_page, next_offset = found.next_cursor
            next_query = urlencode(
                {
                    "q": q.strip(),
                    "lang": lang,
                    "source": source,
                    "fmt": fmt,
                    "page": next_page,
                    "offset": next_offset,
                }
            )
    except Exception as exc:
        logger.exception("Library search failed (source=%r)", source)
        detail = str(exc) if source == "zlib" and str(exc) else None
        results, error = [], detail or "Search is unavailable right now."

    return templates.TemplateResponse(
        request=request,
        name="partials/search_results.html",
        context={
            "request": request,
            "results": results,
            "query": q.strip(),
            "error": error,
            "sources": library.SOURCES,
            "sent": await sent_marks(results),
            "appending": cursor is not None,
            "next_query": next_query,
        },
    )


@app.post("/library/download", response_class=HTMLResponse)
async def download_from_library(
    request: Request,
    source: str = Form(...),
    ref: str = Form(...),
    book_format: str = Form(...),
    kindle_address: str = Form(...),
):
    try:
        url, title, catalogue_author = await library.resolve(source, ref, book_format)
    except Exception as exc:
        return HTMLResponse(f"<div class='job-card__error'>{html.escape(str(exc))}</div>")

    filename = f"{slugify(title)}.{book_format}"
    job = await job_queue.add(
        filename, status="review", kindle_address=kindle_address, source=source, ref=ref
    )
    tmp_path = Path(tempfile.gettempdir()) / f"{job.id}_{filename}"

    try:
        if source == "zlib":
            await library.download_zlib(url, tmp_path)
        else:
            await library.download(url, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        await job_queue.update(job.id, "failed", str(exc))
        return render_job_card(request, await job_queue.get(job.id))

    probed_title, author, cover = await asyncio.to_thread(inspect_file, tmp_path, title)
    await job_queue.set(
        job.id,
        tmp_path=str(tmp_path),
        title=probed_title,
        author=author or catalogue_author,
        cover=cover,
    )
    return render_job_card(request, await job_queue.get(job.id))


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    files: list[UploadFile] = File(...),
    kindle_address: str = Form(...),
):
    html_parts = []

    for file in files:
        filename = file.filename or ""
        job = await job_queue.add(
            filename, status="review", kindle_address=kindle_address, source="upload"
        )

        safe_name = Path(filename).name
        tmp_path = Path(tempfile.gettempdir()) / f"{job.id}_{safe_name}"

        size = 0
        too_large = False
        with tmp_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_attachment_bytes:
                    too_large = True
                    break
                out.write(chunk)

        if too_large:
            tmp_path.unlink(missing_ok=True)
            await job_queue.update(
                job.id, "failed", f"File exceeds {settings.max_attachment_mb}MB limit"
            )
        else:
            title, author, cover = await asyncio.to_thread(
                inspect_file, tmp_path, Path(safe_name).stem
            )
            await job_queue.set(
                job.id, tmp_path=str(tmp_path), title=title, author=author, cover=cover
            )

        html_parts.append(render_job_card(request, await job_queue.get(job.id)))

    return HTMLResponse("".join(html_parts))


@app.post("/jobs/{job_id}/send", response_class=HTMLResponse)
async def send_job(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
    title: str = Form(""),
    author: str = Form(""),
):
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path:
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")

    await job_queue.set(job_id, title=title, author=author, status="pending")
    background_tasks.add_task(process_file_job, job_id)
    return render_job_card(request, await job_queue.get(job_id))


async def convert_job(job_id: str, preset: str = "standard") -> None:
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path:
        return

    source = Path(job.tmp_path)
    options = options_for(preset, Path(job.filename).suffix)
    converted, error = await CalibreConverter.convert_to_epub(source, options)
    job = await job_queue.get(job_id)
    if not job:
        source.unlink(missing_ok=True)
        if converted:
            converted.unlink(missing_ok=True)
        return
    if error or not converted:
        await job_queue.update(job_id, "review", f"Calibre conversion error: {error}")
        return

    filename = f"{Path(job.filename).stem}.epub"
    target = source.with_name(f"{job.id}_{filename}")
    try:
        await asyncio.to_thread(shutil.move, converted, target)
    except OSError as exc:
        converted.unlink(missing_ok=True)
        await job_queue.update(job_id, "review", f"Could not store the converted file: {exc}")
        return

    if target != source:
        source.unlink(missing_ok=True)
    await job_queue.set(
        job_id, tmp_path=str(target), filename=filename, status="review", error=None
    )


@app.post("/jobs/{job_id}/convert", response_class=HTMLResponse)
async def convert_job_file(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
    title: str = Form(""),
    author: str = Form(""),
    preset: str = Form("standard"),
):
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path or not Path(job.tmp_path).exists():
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")
    if job.status != "review" or preset not in presets_for(Path(job.filename).suffix):
        return render_job_card(request, job)

    await job_queue.set(job_id, title=title, author=author, status="converting", error=None)
    background_tasks.add_task(convert_job, job_id, preset)
    return render_job_card(request, await job_queue.get(job_id))


@app.post("/jobs/{job_id}/cover", response_class=HTMLResponse)
async def upload_job_cover(
    request: Request,
    job_id: str,
    cover: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
):
    job = await job_queue.get(job_id)
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")
    if job.status != "review":
        return render_job_card(request, job)

    data = await cover.read(MAX_COVER_BYTES + 1)
    media_type = image_media_type(data)
    if len(data) > MAX_COVER_BYTES:
        error = f"The cover must be smaller than {MAX_COVER_BYTES // 1_048_576} MB."
    elif media_type is None:
        error = "The cover must be a JPEG, PNG or GIF image."
    else:
        error = None

    fields = {"title": title, "author": author, "error": error}
    if error is None:
        fields["cover"] = data_uri(data, media_type)
    await job_queue.set(job_id, **fields)
    return render_job_card(request, await job_queue.get(job_id))


@app.post("/jobs/{job_id}/lookup", response_class=HTMLResponse)
async def lookup_job_metadata(
    request: Request,
    job_id: str,
    title: str = Form(""),
    author: str = Form(""),
):
    job = await job_queue.get(job_id)
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")
    if job.status != "review":
        return render_job_card(request, job)

    try:
        found = await lookup(title, author)
    except Exception:
        logger.exception("Metadata lookup failed for job %s", job_id)
        found = None
    if found is not None:
        found = merge(title, author, found)

    if found is None:
        await job_queue.set(
            job_id,
            title=title,
            author=author,
            error="No match found in Open Library or Google Books.",
        )
    else:
        await job_queue.set(
            job_id,
            title=found.title,
            author=found.author,
            cover=found.cover or job.cover,
            error=None,
        )
    return render_job_card(request, await job_queue.get(job_id))


@app.get("/jobs/{job_id}/file")
async def download_job_file(job_id: str):
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path or not Path(job.tmp_path).exists():
        raise HTTPException(status_code=404, detail="File is no longer on the server.")
    return FileResponse(job.tmp_path, filename=job.filename)


@app.delete("/jobs/{job_id}", response_class=HTMLResponse)
async def delete_job(job_id: str):
    job = await job_queue.get(job_id)
    if job and job.status != "sent":
        if job.tmp_path and job.status not in ACTIVE_STATUSES:
            Path(job.tmp_path).unlink(missing_ok=True)
        await job_queue.remove(job_id)
    return HTMLResponse("")


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="partials/history.html",
        context={
            "request": request,
            "jobs": await job_queue.sent(HISTORY_LIMIT),
            "sources": library.SOURCES,
        },
    )


@app.delete("/history/{job_id}", response_class=HTMLResponse)
async def forget_sent(job_id: str):
    job = await job_queue.get(job_id)
    if job and job.status == "sent":
        await job_queue.remove(job_id)
    return HTMLResponse("")


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_status(request: Request, job_id: str, since: str = ""):
    job = await job_queue.get(job_id)
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")
    context = job_card_context(job)
    if since == context["version"]:
        return Response(status_code=204)
    return render_card(request, context)
