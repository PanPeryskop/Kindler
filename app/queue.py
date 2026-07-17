from dataclasses import dataclass
from pathlib import Path
import threading
import uuid


@dataclass
class Job:
    id: str
    filename: str
    status: str = "pending"
    error: str | None = None
    kindle_address: str | None = None
    tmp_path: Path | None = None
    title: str | None = None
    author: str | None = None
    cover: str | None = None


class JobQueue:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def add(self, filename: str, **fields) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename, **fields)
        with self.lock:
            self.jobs[job.id] = job
            return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def set(self, job_id: str, **fields) -> None:
        with self.lock:
            if job := self.jobs.get(job_id):
                for key, value in fields.items():
                    setattr(job, key, value)

    def update(self, job_id: str, status: str, error: str | None = None) -> None:
        self.set(job_id, status=status, error=error)
