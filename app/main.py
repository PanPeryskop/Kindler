import tempfile
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.queue import Job, JobQueue
from app.services.confirm import confirm_delivery
from app.services.convert import CalibreConverter
from app.services.covers import extract_cover
from app.services.detect import FileTypeDetector
from app.services.metadata import probe_metadata
from app.services.sender import send_to_kindle

job_queue = JobQueue()

app = FastAPI()

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
    job = job_queue.get(job_id)
    if not job or not job.tmp_path:
        return

    tmp_path = job.tmp_path
    try:
        job_queue.update(job_id, "processing")

        file_type, is_valid = FileTypeDetector.detect_file_type(tmp_path)
        if not is_valid:
            job_queue.update(job_id, "failed", f"Invalid file type: {file_type}")
            return

        if file_type in ["epub", "pdf"]:
            job_queue.update(job_id, "sending")
            await send_to_kindle(
                tmp_path,
                job.kindle_address,
                convert=(file_type == "pdf"),
                title=job.title,
                author=job.author,
            )
        else:
            job_queue.update(job_id, "converting")
            converted_path, error = await CalibreConverter.convert_to_epub(tmp_path)
            if error or not converted_path:
                job_queue.update(job_id, "failed", f"Calibre conversion error: {error}")
                return
            job_queue.update(job_id, "sending")
            await send_to_kindle(
                converted_path,
                job.kindle_address,
                title=job.title,
                author=job.author,
            )
            converted_path.unlink(missing_ok=True)

        job_queue.update(job_id, "confirming")
        result = await confirm_delivery(job.title)
        if result is True:
            job_queue.update(job_id, "success")
        elif result is False:
            job_queue.update(job_id, "failed", "Amazon reported a delivery problem.")
        else:
            job_queue.update(job_id, "unconfirmed")

    except Exception as e:
        job_queue.update(job_id, "failed", str(e))

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
        job = job_queue.add(filename, status="review", kindle_address=kindle_address)

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
            job_queue.update(job.id, "failed", f"File exceeds {settings.max_attachment_mb}MB limit")
        else:
            file_type, _ = FileTypeDetector.detect_file_type(tmp_path)
            title, author = probe_metadata(tmp_path, file_type)
            cover = extract_cover(tmp_path, file_type)
            job_queue.set(job.id, tmp_path=tmp_path, title=title, author=author, cover=cover)

        html_parts.append(render_job_card(request, job_queue.get(job.id)))

    return HTMLResponse("".join(html_parts))


@app.post("/jobs/{job_id}/send", response_class=HTMLResponse)
async def send_job(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
    title: str = Form(""),
    author: str = Form(""),
):
    job = job_queue.get(job_id)
    if not job or not job.tmp_path:
        return HTMLResponse("<div class='job-card__error'>Job not found (server rebooted)</div>")

    job_queue.set(job_id, title=title, author=author, status="pending")
    background_tasks.add_task(process_file_job, job_id)
    return render_job_card(request, job_queue.get(job_id))


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str):
    job = job_queue.get(job_id)
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found (server rebooted)</div>")
    return render_job_card(request, job)
