import tempfile
import uuid

from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.queue import JobQueue
from app.services.detect import FileTypeDetector
from app.services.convert import CalibreConverter
from app.services.sender import send_to_kindle

job_queue = JobQueue()


async def process_file_job(job_id: str, tmp_path: Path, kindle_address: str) -> None:
    job = job_queue.get(job_id)
    if not job:
        return
    
    try:
        job_queue.update(job_id, "processing")
        
        file_type, is_valid = FileTypeDetector.detect_file_type(tmp_path)
        if not is_valid:
            job_queue.update(job_id, "failed", f"Invalid file type: {file_type}")
            return
        
        if file_type in ["epub", "pdf"]:
            await send_to_kindle(tmp_path, kindle_address, convert=(file_type == "pdf"))
            
        else:
            converted_path, error = await CalibreConverter.convert_to_epub(tmp_path)
            
            if error or not converted_path:
                job_queue.update(job_id, "failed", f"Calibre conversion error: {error}")
                return
                
            await send_to_kindle(converted_path, kindle_address)
            converted_path.unlink(missing_ok=True)
            
        job_queue.update(job_id, status="success")

    except Exception as e:
        job_queue.update(job_id, status="failed", error=str(e))
        
    finally:
        tmp_path.unlink(missing_ok=True)
        


app = FastAPI()

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


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
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    kindle_address: str = Form(...),
    ):
    html_parts = []
    
    for file in files:
        content = await file.read()
        filename = file.filename or ""
        job = job_queue.add(filename)
        if len(content) > settings.max_attachment_bytes:
            job_queue.update(job.id, "failed", f"File exceeds {settings.max_attachment_mb}MB limit")
        
        else:
            tmp_path = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}_{file.filename}"
            tmp_path.write_bytes(content)

            background_tasks.add_task(
                process_file_job, job.id, tmp_path, kindle_address
            )
        

    
        html_parts.append(
            templates.get_template("partials/job_card.html").render({
                "request": request,
                "job_id": job.id,
                "filename": filename,
                "status": job.status,
                "error": job.error,
            })
        )
        
    return HTMLResponse("".join(html_parts))
    
    
@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_status(request: Request, job_id: str): 
    job = job_queue.get(job_id)
    
    if not job:
        return HTMLResponse("<div class='job-card__error'>Job not found (server rebooted)</div>")

    return templates.TemplateResponse(
        request=request,
        name="partials/job_card.html",
        context={
            "request": request,
            "job_id": job.id,
            "filename": job.filename,
            "status": job.status,
            "error": job.error,
        }
    )