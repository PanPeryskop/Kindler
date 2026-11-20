from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.db import engine, init_db, utcnow
from app.main import discard_abandoned_uploads, fail_interrupted_jobs
from app.queue import JobQueue

pytestmark = pytest.mark.usefixtures("clean_db")

queue = JobQueue()


async def test_job_survives_and_round_trips():
    job = await queue.add("book.epub", status="review", kindle_address="k@kindle.com")

    stored = await queue.get(job.id)
    assert stored.filename == "book.epub"
    assert stored.status == "review"
    assert stored.kindle_address == "k@kindle.com"


async def test_set_updates_fields():
    job = await queue.add("book.epub")

    await queue.set(job.id, title="T", author="A", status="pending")

    stored = await queue.get(job.id)
    assert (stored.title, stored.author, stored.status) == ("T", "A", "pending")


async def test_update_records_error():
    job = await queue.add("book.epub")

    await queue.update(job.id, "failed", "boom")

    stored = await queue.get(job.id)
    assert stored.status == "failed"
    assert stored.error == "boom"


async def test_get_unknown_job_returns_none():
    assert await queue.get("does-not-exist") is None


async def test_older_than_only_matches_stale_rows():
    fresh = await queue.add("fresh.epub", status="review")
    stale = await queue.add("stale.epub", status="review")
    await queue.set(stale.id, created_at=utcnow() - timedelta(hours=10))

    found = await queue.older_than("review", timedelta(hours=6))

    assert [j.id for j in found] == [stale.id]
    assert fresh.id not in [j.id for j in found]


async def test_abandoned_upload_is_expired_and_file_removed(tmp_path: Path):
    leftover = tmp_path / "abandoned.epub"
    leftover.write_bytes(b"data")
    job = await queue.add("abandoned.epub", status="review", tmp_path=str(leftover))
    await queue.set(job.id, created_at=utcnow() - timedelta(hours=10))

    await discard_abandoned_uploads()

    assert not leftover.exists()
    stored = await queue.get(job.id)
    assert stored.status == "failed"
    assert "Expired" in stored.error


async def test_fresh_review_is_left_alone(tmp_path: Path):
    keep = tmp_path / "fresh.epub"
    keep.write_bytes(b"data")
    job = await queue.add("fresh.epub", status="review", tmp_path=str(keep))

    await discard_abandoned_uploads()

    assert keep.exists()
    assert (await queue.get(job.id)).status == "review"


async def test_interrupted_jobs_are_failed_on_restart(tmp_path: Path):
    leftover = tmp_path / "midflight.epub"
    leftover.write_bytes(b"data")
    active = await queue.add("midflight.epub", status="sending", tmp_path=str(leftover))
    done = await queue.add("done.epub", status="sent")

    await fail_interrupted_jobs()

    assert not leftover.exists()
    assert (await queue.get(active.id)).status == "failed"
    assert (await queue.get(done.id)).status == "sent"


async def test_init_db_adds_columns_missing_from_an_older_database():
    async with engine.begin() as connection:
        await connection.exec_driver_sql("DROP TABLE job")
        await connection.exec_driver_sql(
            "CREATE TABLE job (id VARCHAR PRIMARY KEY, filename VARCHAR NOT NULL, "
            "status VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
        )

    await init_db()

    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("job")}
        )
    assert {"source", "ref", "sent_at", "title", "cover"} <= columns
    job = await queue.add("book.epub", source="zlib", ref="abc")
    assert (await queue.get(job.id)).ref == "abc"


async def test_sent_lists_only_sent_jobs_newest_first():
    older = await queue.add("old.epub", status="sent", sent_at=utcnow() - timedelta(days=2))
    newer = await queue.add("new.epub", status="sent", sent_at=utcnow())
    await queue.add("draft.epub", status="review")

    assert [job.id for job in await queue.sent()] == [newer.id, older.id]
    assert [job.id for job in await queue.sent(limit=1)] == [newer.id]
