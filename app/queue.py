from dataclasses import dataclass
import threading
import uuid

@dataclass
class Job:
    id: str
    filename: str
    status: str = "pending"
    error: str | None = None
    

class JobQueue:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        
    def add(self, filename: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename)
        with self.lock:
            self.jobs[job.id] = job
            return job
        
    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)
        
    def update(self, job_id: str, status: str, error: str | None = None):
        with self.lock:
            if job := self.jobs.get(job_id):
                job.status = status
                job.error = error   