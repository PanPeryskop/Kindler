from pathlib import Path

from kindler.db import Job


def stored_file(job: Job) -> Path:
    assert job.tmp_path is not None
    return Path(job.tmp_path)


def error_of(job: Job) -> str:
    assert job.error is not None
    return job.error
