import asyncio
import html
import logging
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slugify import slugify

from app.config import settings
from app.db import Job, init_db
from app.queue import ACTIVE_STATUSES, JobQueue
from app.services import library, z_lib
from app.services.convert import CalibreConverter
from app.services.covers import extract_cover
from app.services.detect import FileTypeDetector
from app.services.metadata import apply_metadata, probe_metadata
from app.services.sender import send_to_kindle

REVIEW_TTL = timedelta(hours=6)
CLEANUP_INTERVAL_SECONDS = 900

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
    await fail_interrupted_jobs()
    await discard_abandoned_uploads()
    tasks = [asyncio.create_task(cleanup_loop()), asyncio.create_task(z_lib.keep_warm())]
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


def render_job_card(request: Request, job: Job) -> str:
    return templates.get_template("partials/job_card.html").render(
        {
            "request": request,
            "job_id": job.id,
            "filename": job.filename,
            "status": job.status,
            "error": job.error,
            "title": job.title,
            "author": job.author,
            "cover": job.cover,
            "downloadable": bool(job.tmp_path and Path(job.tmp_path).exists()),
        }
    )


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

        if file_type in ["epub", "pdf"]:
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

        await job_queue.update(job_id, "sending")
        await send_to_kindle(
            outgoing,
            job.kindle_address,
            convert=(outgoing_type == "pdf"),
            title=job.title,
            author=job.author,
        )
        await job_queue.update(job_id, "sent")

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
            "default_kindle": settings.kindle_address,
            "max_size": settings.max_attachment_mb,
            "languages": library.LANGUAGES,
            "sources": library.SOURCES,
        },
    )


@app.get("/search", response_class=HTMLResponse)
async def search_library(request: Request, q: str = "", lang: str = "", source: str = ""):
    if lang not in library.LANGUAGES:
        lang = ""
    if source not in library.SOURCES:
        source = ""

    try:
        results = await library.search(q, language=lang, source=source)
        error = None
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
    job = await job_queue.add(filename, status="review", kindle_address=kindle_address)
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
        job = await job_queue.add(filename, status="review", kindle_address=kindle_address)

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


@app.get("/jobs/{job_id}/file")
async def download_job_file(job_id: str):
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path or not Path(job.tmp_path).exists():
        raise HTTPException(status_code=404, detail="File is no longer on the server.")
    return FileResponse(job.tmp_path, filename=job.filename)


@app.delete("/jobs/{job_id}", response_class=HTMLResponse)
async def delete_job(job_id: str):
    job = await job_queue.get(job_id)
    if job:
        if job.tmp_path and job.status not in ACTIVE_STATUSES:
            Path(job.tmp_path).unlink(missing_ok=True)
        await job_queue.remove(job_id)
    return HTMLResponse("")


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_status(request: Request, job_id: str):
    job = await job_queue.get(job_id)
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")
    return render_job_card(request, job)
