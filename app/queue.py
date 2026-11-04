import uuid
from datetime import timedelta

from sqlmodel import select

from app.db import Job, session_factory, utcnow

ACTIVE_STATUSES = ("pending", "processing", "converting", "sending")


class JobQueue:
    async def add(self, filename: str, **fields) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename, **fields)
        async with session_factory() as session:
            session.add(job)
            await session.commit()
        return job

    async def get(self, job_id: str) -> Job | None:
        async with session_factory() as session:
            return await session.get(Job, job_id)

    async def set(self, job_id: str, **fields) -> None:
        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            session.add(job)
            await session.commit()

    async def update(self, job_id: str, status: str, error: str | None = None) -> None:
        await self.set(job_id, status=status, error=error)

    async def remove(self, job_id: str) -> None:
        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            await session.delete(job)
            await session.commit()

    async def with_status(self, *statuses: str) -> list[Job]:
        async with session_factory() as session:
            result = await session.exec(select(Job).where(Job.status.in_(statuses)))
            return list(result)

    async def older_than(self, status: str, age: timedelta) -> list[Job]:
        cutoff = utcnow() - age
        async with session_factory() as session:
            result = await session.exec(
                select(Job).where(Job.status == status, Job.created_at < cutoff)
            )
            return list(result)
