import asyncio
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import Job, init_db
from app.queue import ACTIVE_STATUSES, JobQueue
from app.services.confirm import watch_for_problem
from app.services.convert import CalibreConverter
from app.services.covers import extract_cover
from app.services.detect import FileTypeDetector
from app.services.metadata import apply_metadata, probe_metadata
from app.services.sender import send_to_kindle

REVIEW_TTL = timedelta(hours=6)
CLEANUP_INTERVAL_SECONDS = 900

job_queue = JobQueue()


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
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


def render_job_card(request: Request, job: Job) -> str:
    return templates.get_template("partials/job_card.html").render({
        "request": request,
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "error": job.error,
        "title": job.title,
        "author": job.author,
        "cover": job.cover,
    })


async def process_file_job(job_id: str) -> None:
    job = await job_queue.get(job_id)
    if not job or not job.tmp_path:
        return

    tmp_path = Path(job.tmp_path)
    try:
        await job_queue.update(job_id, "processing")

        file_type, is_valid = FileTypeDetector.detect_file_type(tmp_path)
        if not is_valid:
            await job_queue.update(job_id, "failed", f"Invalid file type: {file_type}")
            return

        converted_path = None
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

        sent_at = datetime.now(UTC)
        await job_queue.update(job_id, "sending")
        await send_to_kindle(
            outgoing,
            job.kindle_address,
            convert=(outgoing_type == "pdf"),
            title=job.title,
            author=job.author,
        )
        if converted_path:
            converted_path.unlink(missing_ok=True)

        await job_queue.update(job_id, "checking")
        problem = await watch_for_problem(sent_at)
        if problem:
            await job_queue.update(job_id, "failed", f"Amazon: {problem}")
        else:
            await job_queue.update(job_id, "sent")

    except Exception as e:
        await job_queue.update(job_id, "failed", str(e))

    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "default_kindle": settings.kindle_address,
            "max_size": settings.max_attachment_mb,
        },
    )


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
            file_type, _ = FileTypeDetector.detect_file_type(tmp_path)
            title, author = probe_metadata(tmp_path, file_type)
            cover = extract_cover(tmp_path, file_type)
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


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_status(request: Request, job_id: str):
    job = await job_queue.get(job_id)
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found</div>")
    return render_job_card(request, job)
